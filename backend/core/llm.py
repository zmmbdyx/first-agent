"""LLM 客户端：OpenAI 兼容接口 + 指数退避重试 + JSON 修复重试 + 离线 Mock 模式。

所有 chat() 调用带 purpose 参数（plan/react/task_answer/synthesize/extract_facts/summarize），
真实 LLM 仅将其用于日志，Mock LLM 依据 purpose 走不同的启发式分支。
"""
import json
import re
import time
from typing import Callable, List, Dict, Optional

from config import Config


class LLMError(Exception):
    pass


def extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中稳健地提取第一个 JSON 对象（容忍 markdown 代码块）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    text = text[start:i + 1]
                    break
        else:
            return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", text))  # 容忍尾逗号
        except json.JSONDecodeError:
            return None


class LLMClient:
    """真实 LLM：OpenAI 兼容协议，带指数退避重试与 JSON 输出自动修复。

    用量观测（新增）：每次成功调用后记录 token 用量与耗时，并通过 on_usage 回调
    外抛；这样上层（指标面板 / LangGraph 节点）无需改动调用点即可拿到
    TPS、缓存命中率、上下文占用等实时指标。观测代码不参与任何控制流。
    """

    name = "llm"

    def __init__(self, cfg: Config):
        from openai import OpenAI
        self.cfg = cfg
        self.model = cfg.model
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=60, max_retries=0)
        self.calls = 0
        self.on_usage: Optional[Callable[[dict], None]] = None  # 用量回调（可赋值）
        self._usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "total_tokens": 0, "cached_tokens": 0, "llm_ms": 0.0}

    def set_model(self, model: str):
        """运行时切换模型（多模型支持）。"""
        if not model:
            raise LLMError("模型名不能为空")
        self.model = model

    def usage_stats(self) -> dict:
        """累计用量快照：供最终 stats 与 Token 面板使用。"""
        u = dict(self._usage)
        u["avg_tps"] = round(u["completion_tokens"] / (u["llm_ms"] / 1000), 1) if u["llm_ms"] else 0.0
        u["llm_ms"] = round(u["llm_ms"], 1)
        return u

    def _record_usage(self, resp, purpose: str, latency_ms: float):
        """提取 OpenAI 兼容响应里的 usage（不同端点字段可能缺失，一律容错）。"""
        usage = getattr(resp, "usage", None)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or (prompt + completion))
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        rec = {"purpose": purpose, "model": self.model, "prompt_tokens": prompt,
               "completion_tokens": completion, "total_tokens": total,
               "cached_tokens": cached, "latency_ms": round(latency_ms, 1),
               "tps": round(completion / (latency_ms / 1000), 1) if latency_ms > 0 else 0.0}
        self._usage["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
            self._usage[k] += rec[k]
        self._usage["llm_ms"] += latency_ms
        if self.on_usage is not None:
            try:
                self.on_usage(rec)
            except Exception:
                pass  # 观测失败不得影响主流程
        return rec

    def chat(self, messages: List[Dict], purpose: str = "chat", json_mode: bool = False,
             max_retries: Optional[int] = None) -> str:
        try:
            return self._chat_retry(messages, purpose, json_mode, max_retries)
        except LLMError:
            fb = getattr(self.cfg, "fallback_model", "")
            if fb and fb != self.model:  # 模型降级：主模型连续失败自动切换
                print(f"[llm] 主模型 {self.model} 连续失败，自动降级到备用模型 {fb}")
                self.model, self.degraded = fb, True
                return self._chat_retry(messages, purpose, json_mode, max_retries)
            raise

    def _chat_retry(self, messages, purpose, json_mode, max_retries) -> str:
        # 改动：原实现若 max_retries/配置为 0，for 循环一次都不执行，直接抛出
        # "LLM 调用失败（已重试0次）: None"（last_err 为 None），报错信息无参考价值。
        # 这里保证至少尝试一次（配置侧也做了 >=1 的下限护栏）。
        retries = max(1, max_retries if max_retries is not None else self.cfg.llm_max_retries)
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                self.calls += 1
                kwargs = dict(model=self.model, messages=messages,
                              temperature=self.cfg.temperature)
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                t0 = time.time()
                resp = self.client.chat.completions.create(**kwargs)
                latency_ms = (time.time() - t0) * 1000
                content = (resp.choices[0].message.content or "").strip()
                if not content:
                    raise LLMError("模型返回空内容")
                self._record_usage(resp, purpose, latency_ms)
                return content
            except Exception as e:  # 网络/限流/超时统一走退避重试
                last_err = e
                wait = min(2 ** attempt * 1.5, 20)
                print(f"[llm] {purpose} 第{attempt}次调用失败: {type(e).__name__}: {e}，{wait:.0f}s 后重试")
                time.sleep(wait)
        raise LLMError(f"LLM 调用失败（已重试{retries}次）: {last_err}")

    def chat_json(self, messages: List[Dict], purpose: str, max_retries: Optional[int] = None) -> dict:
        """结构化输出：解析失败时把错误信息回传给模型修复，最多修复 2 次。"""
        convo = list(messages)
        for repair in range(3):
            raw = self.chat(convo, purpose=purpose, json_mode=True, max_retries=max_retries)
            data = extract_json(raw)
            if data is not None:
                return data
            convo = convo + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "你的上一条输出不是合法 JSON，无法解析。请重新输出，只输出一个合法 JSON 对象，不要任何解释或代码块。"},
            ]
        raise LLMError("LLM 无法产出合法 JSON（已修复重试2次）")


class MockLLM:
    """离线 Mock：不依赖任何 API，用启发式模板驱动整条流水线，保证开箱即跑。
    工具产生的数据（关键词/匹配分/差距）全部来自真实计算，Mock 只负责编排与组织文案。

    用量观测：无真实服务端 usage 可读，这里用「字符数 / 4」粗估 token 并按固定
    合成速率折算耗时，使离线演示时 Token 面板、TPS、缓存命中率等指标同样可见。
    """

    name = "mock-llm"
    model = "mock"
    degraded = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.calls = 0
        self.on_usage: Optional[Callable[[dict], None]] = None
        self._usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "total_tokens": 0, "cached_tokens": 0, "llm_ms": 0.0}

    def set_model(self, model: str):
        self.model = model  # mock 模式仅作标签展示

    def usage_stats(self) -> dict:
        u = dict(self._usage)
        u["avg_tps"] = round(u["completion_tokens"] / (u["llm_ms"] / 1000), 1) if u["llm_ms"] else 0.0
        u["llm_ms"] = round(u["llm_ms"], 1)
        return u

    @staticmethod
    def _estimate_tokens(text) -> int:
        return max(1, len(str(text)) // 4)

    def _record_usage(self, messages, output, purpose: str) -> dict:
        prompt = sum(self._estimate_tokens(m.get("content", "")) for m in messages
                     if isinstance(m, dict)) if isinstance(messages, list) \
            else self._estimate_tokens(messages)
        completion = self._estimate_tokens(output)
        # 合成速率：模拟本地小模型的 ~45 token/s，便于演示 TPS 与耗时
        latency_ms = completion / 45 * 1000
        rec = {"purpose": purpose, "model": self.model, "prompt_tokens": prompt,
               "completion_tokens": completion, "total_tokens": prompt + completion,
               "cached_tokens": 0, "latency_ms": round(latency_ms, 1),
               "tps": 45.0}
        self._usage["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
            self._usage[k] += rec[k]
        self._usage["llm_ms"] += latency_ms
        if self.on_usage is not None:
            try:
                self.on_usage(rec)
            except Exception:
                pass
        return rec

    def chat(self, messages: List[Dict], purpose: str = "chat", json_mode: bool = False,
             max_retries: Optional[int] = None) -> str:
        self.calls += 1
        result = self._dispatch(messages, purpose)
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        self._record_usage(messages, text, purpose)
        return text

    def chat_json(self, messages: List[Dict], purpose: str, max_retries: Optional[int] = None) -> dict:
        self.calls += 1
        result = self._dispatch(messages, purpose)
        self._record_usage(messages, json.dumps(result, ensure_ascii=False), purpose)
        return result

    # ------ 各 purpose 的启发式实现 ------
    def _dispatch(self, messages, purpose):
        return getattr(self, f"_mock_{purpose}", self._mock_chat)(messages)

    @staticmethod
    def _flatten(messages) -> str:
        if isinstance(messages, str):
            return messages
        return "\n".join(m.get("content", "") for m in messages if isinstance(m, dict))

    def _mock_plan(self, messages):
        # 真实规划在 agent/planner 的 fallback 中完成，这里仅兜底
        return extract_json(self._flatten(messages)) or {"tasks": []}

    def _mock_react(self, messages):
        payload = extract_json(self._flatten(messages)) or {}
        tool = payload.get("tool_hint") or "none"
        if payload.get("step", 1) >= 2:  # mock 下每任务一次工具调用即收尾
            lines = [l for l in str(payload.get("transcript", "")).splitlines() if l.strip()]
            last = lines[-1] if lines else ""
            if last.startswith("[step") and "失败" in last:  # 工具重试后仍失败 → 判定任务失败
                return {"thought": "工具自动重试后仍然失败，判定该子任务无法完成。",
                        "action": {"tool": "fail", "args": {"reason": last[:150]}}}
            return {"thought": "材料与工具结果已就绪，汇总本任务结论。",
                    "action": {"tool": "none"}, "final": ""}
        return {"thought": f"根据当前上下文完成「{payload.get('title', '')}」，"
                           f"使用 {tool} 处理已有材料。",
                "action": {"tool": tool, "args": payload.get("args", {}) or {}}}

    # ------ mock 的内容生成模板（数据均来自工具真实计算结果） ------
    def _mock_task_answer(self, messages):
        payload = extract_json(self._flatten(messages)) or {}
        title = payload.get("title", "")
        art = payload.get("artifacts") or {}
        if payload.get("tool_hint") == "write_report" or "报告" in title:
            return self._report_md(art, payload.get("facts") or {})
        if "面试" in title:
            return self._questions_md(art)
        if "建议" in title or "优化" in title:
            return self._suggestions_md(art)
        return f"已完成「{title}」。\n\n当前掌握的关键信息：\n" + \
            "\n".join(f"- {k}" for k in art.keys() or ["（暂无中间产物）"])

    def _mock_synthesize(self, messages):
        payload = extract_json(self._flatten(messages)) or {}
        return self._report_md(payload.get("artifacts") or {}, payload.get("facts") or {})

    @staticmethod
    def _as_count_dict(v) -> dict:
        if isinstance(v, dict):
            return v
        return {k: 1 for k in (v or [])}

    @staticmethod
    def _questions_md(art) -> str:
        jd = art.get("jd_analysis") or {}
        skills = list((jd.get("skills_hard") or {}).keys())
        reqs = jd.get("requirements") or []
        tech = [f"请结合具体项目，说明你使用 **{s}** 解决过的最有挑战的问题，以及你的技术取舍。" for s in skills[:3]]
        req_q = [f"JD 明确要求「{r[:40]}…」，请举一个能证明该能力的实例。" for r in reqs[:2]]
        scen = ["如果入职后发现需求评估与开发实际排期严重冲突，你会如何推动解决？",
                "给你一个全新的业务模块从0到1负责，你的前两周会怎么规划？"]
        back = ["这个岗位团队目前的规模和分工是怎样的？",
                "团队目前最大的技术/业务挑战是什么？"]
        lines = ["## 面试问题清单", "", "### 技术/专业题", *[f"{i}. {q}" for i, q in enumerate(tech, 1)],
                 "", "### 项目经历题", *[f"{i}. {q}" for i, q in enumerate(req_q, len(tech) + 1)],
                 "", "### 情景题", *[f"{i}. {q}" for i, q in enumerate(scen, len(tech) + len(req_q) + 1)],
                 "", "### 反问面试官", *[f"{i}. {q}" for i, q in enumerate(back, len(tech) + len(req_q) + len(scen) + 1)]]
        return "\n".join(lines)

    def _suggestions_md(self, art) -> str:
        m = art.get("match") or {}
        missing = self._as_count_dict(m.get("missing_skills"))
        matched = list(self._as_count_dict(m.get("matched_skills")).keys())
        lines = ["## 简历优化建议", ""]
        if matched:
            lines += ["**放大已有优势**（简历中前置突出）：", ""]
            lines += [f"- **{k}**：已命中JD要求，建议在项目描述中量化成果（如「用{k}将XX效率提升N%」）。" for k in matched[:4]]
        if missing:
            lines += ["", "**补齐差距**（按JD出现频次排优先级）：", ""]
            lines += [f"- **{k}**（JD出现{v}次，优先级{'高' if v >= 2 else '中'}）："
                      f"建议通过一个小型实战项目补齐，并写入简历项目栏。" for k, v in list(missing.items())[:6]]
        lines += ["", "**表述改写公式**：将「负责XX」改为「使用[技能]实现[结果]，带来[量化收益]」。"]
        return "\n".join(lines)

    def _report_md(self, art, facts) -> str:
        m = dict(art.get("match") or {})
        m["matched_skills"] = self._as_count_dict(m.get("matched_skills"))
        m["missing_skills"] = self._as_count_dict(m.get("missing_skills"))
        jd = art.get("jd_analysis") or {}
        role = facts.get("target_role") or "目标岗位"
        if not m and not jd and art.get("search"):
            results = art["search"].get("results", [])
            return "## 搜索结果摘要\n\n" + "\n".join(
                f"- [{r.get('title')}]({r.get('url')}): {r.get('snippet', '')[:80]}" for r in results[:5])
        if not m and not jd:
            return "## 待补充材料\n\n尚未获得JD或简历数据，请提供后重新分析。"
        matched, missing = m.get("matched_skills") or {}, m.get("missing_skills") or {}
        parts = [f"# {role} · 求职分析报告", "",
                 "## 一、结论摘要", "",
                 f"- 简历-JD 匹配分 **{m.get('score', 'N/A')}/100（{m.get('grade', '-')}级）**，"
                 f"硬技能覆盖 {m.get('hard_coverage', '-')}%。",
                 f"- JD 关键要求：{m.get('jd_education', '-')} / 经验 {m.get('jd_experience', '-')} / 薪资 {m.get('jd_salary', '-')}。",
                 f"- 最大机会点：{('、'.join(list(matched.keys())[:3]) or '待挖掘')}；最需补齐：{('、'.join(list(missing.keys())[:3]) or '无')}.", ""]
        if jd:
            parts += ["## 二、JD要求解读", "",
                      f"**硬技能频次**：{'、'.join(f'{k}({v})' for k, v in list(jd.get('skills_hard', {}).items())[:8])}",
                      f"**软技能**：{'、'.join(list((jd.get('skills_soft') or {}).keys())[:6]) or '未明确'}", ""]
        if m:
            chart = m.get("chart") or ""
            html_r = m.get("html") or ""
            parts += ["## 三、简历匹配度分析", "",
                      # 改动：原为 f"![匹配度图表](/{chart})"，多出的前导斜杠使前端
                      # utils.js 的 "data/ 前缀 → /files/ 前缀" 规则不生效，图片链接
                      # 指向不存在的 /data/... 路由（404）；去掉斜杠后与真实 LLM 分支
                      # 输出的 data/reports/... 写法一致。
                      f"![匹配度图表]({chart})" if chart else "",
                      f"[🔍 打开交互式报告（悬停查看技能差距）](/files/{html_r})" if html_r else "",
                      f"- **已具备**：{'、'.join(list(matched.keys())[:10]) or '无'}",
                      f"- **缺失**：{'、'.join(list(missing.keys())[:10]) or '无'}", ""]
            parts += [self._suggestions_md(art), ""]
        parts += [self._questions_md(art), "", "## 六、行动清单", "",
                  "1. 按优化建议改写简历中 3 条核心项目描述；",
                  "2. 优先补齐最高频缺失技能并产出可展示的项目；",
                  "3. 用本报告面试题清单完成 2 轮模拟面试；",
                  "4. 针对 JD 公司做背景调研，准备 3 个反问问题。"]
        return "\n".join(parts)

    def _mock_chat(self, messages):
        return "（mock 模式）已收到消息。配置 LLM_API_KEY 后可获得真实大模型回复。"

    def _mock_classify(self, messages):
        text = self._flatten(messages)
        if re.search(r"沮丧|焦虑|压力|不好找|迷茫|挂了|难过|emo|崩溃|怀疑自己", text, re.I):
            return {"type": "emotion"}
        if re.search(r"^(你好+|嗨|哈喽|谢谢|再见)$|你是谁|你叫什么|能干什么|怎么用", re.sub(r"\s", "", text)):
            return {"type": "chitchat"}
        return {"type": "job_task"}

    def _mock_emotion_support(self, messages):
        return ("求职受挫确实让人难受——但这说明你在认真对待这件事。\n\n"
                "今天就能做的三件小事：\n"
                "1. **复盘最近一次面试**，记下被问住的问题；\n"
                "2. **用 STAR 结构重写一段项目描述**；\n"
                "3. **把目标拆小**：先只完成「本周改完简历」。\n\n"
                "把简历或目标岗位发给我，我马上帮你做第一步。")

    def _mock_summarize(self, messages):
        text = self._flatten(messages)
        return text[:600] + ("…" if len(text) > 600 else "")
