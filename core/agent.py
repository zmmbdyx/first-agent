"""Agent 编排器：规划 → 逐任务 ReAct 执行（工具重试/降级） → 综合报告。
支持 ask_user 暂停-恢复、任务间产物自动接线（artifacts）、全程事件发射供前端可视化。"""
import json
import re
import time
from typing import Callable, List, Optional

from config import Config, load_config
from core import cache as disk_cache
from core import profile as profile_store
from core.memory import Memory, Session, Task
from core.prompts import (PERSONA_RULES, REACT_SYSTEM, SYNTHESIZE_SYSTEM,
                          FACT_EXTRACT_SYSTEM, ASK_USER_GUIDE,
                          CLASSIFY_SYSTEM, EMOTION_SUPPORT)
from tools.base import ToolRegistry, ToolResult
from tools.web_search import WebSearchTool
from tools.web_fetch import WebFetchTool
from tools.file_tools import (PdfExtractTool, FileReadTool, DocxExtractTool,
                              default_resume_path)
from tools.image_ocr import ImageOcrTool
from tools.jd_analyze import JdAnalyzeTool
from tools.resume_match import ResumeMatchTool
from tools.report import WriteReportTool
from core import planner as planner_mod

NON_TOOLS = {"", "none", "null", "finish", "final"}
READ_TOOLS = ("file_read", "pdf_extract", "docx_extract", "image_ocr")
CACHABLE_TOOLS = {"jd_analyze", "resume_match", "pdf_extract", "docx_extract", "image_ocr"}

# —— 求职智囊：敏感信息拦截（身份证/银行卡/密码验证码） ——
# 注意：中文与数字相邻时 \b 无效（汉字属于\w），用环视断言代替
SENSITIVE_RE = re.compile(
    r"(?<!\d)\d{17}[\dXx](?!\d)"            # 身份证号（18位）
    r"|(?<!\d)\d{16,19}(?!\d)"              # 银行卡号（16-19位）
    r"|(密码|验证码|口令|password)\s*[:：=]?\s*\S+", re.I)
SENSITIVE_REPLY = ("请勿在对话中透露身份证号、银行卡号、密码等敏感信息，我已自动忽略该内容。"
                   "去掉敏感信息后重新描述需求即可，不会影响分析。")
WELCOME_MSG = "你好，我是求职智囊。请告诉我你的姓名和目标职位，我马上开始为你服务。"
# 输出脱敏：防止密钥/内部端点随回复外泄（Prompt 注入防护的输出侧）
_SECRET_OUT_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{12,}"
    r"|(?:open\.generic-endpoint|api\.generic-llm|generic-endpoint|api\.openai|api\.generic)\.[a-z.]+/[a-zA-Z0-9./]*", re.I)
# 情绪信号（意图分类的本地快路径）
EMOTION_RE = re.compile(r"沮丧|焦虑|压力[大很]|不好找|迷茫|挂了|难过|emo|崩溃|没信心|怀疑自己", re.I)
MOCK_EMOTION_REPLY = (
    "求职受挫确实让人难受，这很正常——但方向比时长更重要。\n\n"
    "现在能做的三件小事：\n"
    "1. **复盘最近一次面试**：把被问住的问题记下来；\n"
    "2. **重写一段项目描述**：按「背景-做法-量化结果」结构；\n"
    "3. **把目标拆小**：先完成「本周改完简历」这一个动作。\n\n"
    "把你的简历或目标岗位发给我，我马上帮你做第一步。")


class EventBus:
    def __init__(self, agent=None):
        self._subs: List[Callable] = []
        self.agent = agent

    def subscribe(self, fn: Callable):
        self._subs.append(fn)
        return lambda: self._subs.remove(fn) if fn in self._subs else None

    def emit(self, etype: str, **data) -> dict:
        evt = {"type": etype, "ts": round(time.time(), 3), **data}
        if self.agent is not None and getattr(self.agent, "_session", None) is not None:
            evt["session_id"] = self.agent._session.id
        for fn in self._subs:
            try:
                fn(evt)
            except Exception:
                pass
        return evt


class JobAgent:
    def __init__(self, cfg: Config = None):
        self.cfg = cfg or load_config()
        from core.llm import MockLLM, LLMClient
        self.llm = MockLLM(self.cfg) if self.cfg.provider == "mock" else LLMClient(self.cfg)
        self.registry = ToolRegistry(self.cfg.tool_max_retries)
        for t in (WebSearchTool(), WebFetchTool(), PdfExtractTool(), FileReadTool(),
                  DocxExtractTool(), ImageOcrTool(), JdAnalyzeTool(), ResumeMatchTool(),
                  WriteReportTool()):
            self.registry.register(t)
        self.memory = Memory(self.cfg.sessions_dir, self.llm,
                             privacy_mode=self.cfg.privacy_mode)
        self._session: Optional[Session] = None
        self._tool_cache: dict = {}   # (session_id, tool, args) -> ToolResult，会话内去重
        self.bus = EventBus(self)

    # ================= 入口 =================
    def handle_message(self, session: Session, text: str):
        """处理一条用户消息：新目标走 规划→执行→报告；awaiting_input 时走恢复流程。"""
        self._session = session
        try:
            self._handle(session, text)
        except Exception as e:  # 兜底：任何未预期异常都不许击穿会话
            session.status = "done"
            self.bus.emit("error", message=f"{type(e).__name__}: {e}")
            session.add_message("assistant", f"抱歉，执行中遇到错误：{e}")
        finally:
            self.memory.save(session)
            if not self.cfg.privacy_mode:
                profile_store.merge_from(session.facts)  # 跨会话长期记忆：增量合并

    def _handle(self, session: Session, text: str):
        self._run_started = time.time()
        self.bus.emit("user_message", content=text)
        # —— 求职智囊：敏感信息拦截（不落盘、不进记忆、不跑流水线） ——
        if SENSITIVE_RE.search(text):
            reply = SENSITIVE_REPLY
            session.add_message("assistant", reply)
            self.bus.emit("security_block", reason="sensitive_info",
                          message="检测到敏感信息已拦截，请勿发送身份证号/银行卡等隐私内容")
            self.bus.emit("final_answer", content=reply, chart="", stats=None)
            return
        # —— 数据控制指令（本地即时响应，不调用LLM） ——
        stripped = re.sub(r"\s", "", text)
        if stripped in ("查看我的数据", "我的数据", "查看记忆", "我的记忆"):
            reply = self._data_summary(session)
            session.add_message("assistant", reply)
            self.bus.emit("final_answer", content=reply, chart="", stats=None)
            return
        if stripped in ("删除我的所有记忆", "删除记忆", "重置记忆", "清空记忆"):
            session.facts = {}
            session.artifacts = {}
            session.summary = ""
            session.messages = []
            session.tasks = []
            profile_store.clear()
            self.memory.save(session)
            reply = ("✅ 已删除全部记忆（个人资料、求职偏好、对话上下文），存储文件同步清除。\n\n"
                     + WELCOME_MSG)
            self.bus.emit("final_answer", content=reply, chart="", stats=None)
            return

        # —— 求职智囊：意图分类前置（情绪/闲聊走支持路径，不跑流水线） ——
        if self._classify_and_route(session, text):
            return

        # —— 跨会话长期记忆：全局画像作为基线注入（会话内新信息可覆盖） ——
        if not session.facts:
            for k, v in profile_store.load().items():
                session.facts.setdefault(k, v)
        session.add_message("user", text)
        self._refresh_facts(session, text)

        if session.status == "awaiting_input" and session.waiting_task_id:
            self._resume(session, text)
            if session.status == "awaiting_input":
                return
            self._finalize(session)
            return

        goal, tasks = self._make_plan(session, text)
        session.artifacts = {}  # 新目标清空上一轮产物，避免旧JD/旧结果污染本轮上下文
        session.tasks = tasks
        if session.title in ("新会话", ""):
            session.title = goal[:30]
        self.bus.emit("plan_created", goal=goal, tasks=[t.to_dict() for t in tasks],
                      costs=self._tool_costs())
        session.status = "running"
        self._execute_plan(session)
        if session.status != "awaiting_input":
            self._finalize(session)

    # ================= 事实抽取（记忆更新） =================
    def _data_summary(self, session: Session) -> str:
        """「查看我的数据」：本地汇总用户画像与进度；30天未更新的事实标记「待确认」。"""
        f = {k: v for k, v in session.facts.items() if not str(k).startswith("_")}
        label = {"name": "姓名", "target_role": "目标岗位", "city": "城市",
                 "salary_range": "期望薪资", "education": "学历", "experience_years": "经验",
                 "jd_paths": "JD", "resume_paths": "简历", "preferences": "偏好"}
        ts_map = session.facts.get("_ts") or {}
        now = time.time()
        rows = []
        for k, v in f.items():
            suffix = ""
            ts = ts_map.get(k)
            if ts and now - ts > 30 * 86400:  # 自动遗忘机制：长期未使用 → 降权待确认
                suffix = "（⏳ 30天未更新，待确认）"
            rows.append(f"- {label.get(k, k)}：**{', '.join(v) if isinstance(v, list) else v}**{suffix}")
        rounds = len(session.messages) // 2
        return ("## 你的数据\n\n" + ("\n".join(rows) if rows else "- 暂无，聊两句我就记住了") +
                f"\n\n- 🧠 记忆{len(f)}项 · 已对话{rounds}轮"
                "\n\n- 🔒 已加密存储于本地 `data/sessions/`，不上传任何第三方；说「删除我的所有记忆」可立即清除")

    def _classify_and_route(self, session: Session, text: str) -> bool:
        """意图分类前置：返回 True 表示已处理（情绪/闲聊），False 走正常流水线。
        只对「短且无求职信号」的消息启用，避免误路由与多余 LLM 调用。"""
        if planner_mod.PATH_RE.search(text) or len(text) >= 60:
            return False  # 含材料/长文本 → 正常分析
        if any(k in text for k in planner_mod.ROLE_KEYWORDS) \
                or "JD" in text.upper() or "简历" in text or "匹配" in text:
            return False  # 明确求职意图 → 正常分析
        stripped = re.sub(r"\s", "", text)
        if self.cfg.provider == "mock":
            kind = "emotion" if EMOTION_RE.search(text) else (
                "chitchat" if re.search(r"^(你好+|嗨|哈喽|谢谢|再见)$|你是谁|你叫什么|能干什么|怎么用", stripped)
                else "job_task")
        else:
            try:
                data = self.llm.chat_json(
                    [{"role": "system", "content": CLASSIFY_SYSTEM},
                     {"role": "user", "content": text[:300]}], purpose="classify")
                kind = str((data or {}).get("type") or "job_task")
            except Exception:
                return False  # 分类失败按正常流水线（fail-open）
        if kind == "emotion":
            reply = MOCK_EMOTION_REPLY
            if self.cfg.provider != "mock":
                reply = self.llm.chat(
                    [{"role": "system", "content": EMOTION_SUPPORT},
                     {"role": "user", "content": text}], purpose="emotion_support")
        elif kind == "chitchat":
            reply = ("我是求职智囊，可以帮你：优化简历、分析JD匹配度、模拟面试、薪资谈判。"
                     "把目标岗位或JD发给我即可开始。")
        else:
            return False
        session.add_message("user", text)
        session.add_message("assistant", reply)
        self.bus.emit("final_answer", content=reply, chart="", stats=None)
        return True

    def _refresh_facts(self, session: Session, text: str):
        facts = planner_mod.extract_facts_regex(text)
        jd_markers = ("任职要求", "岗位职责", "职位描述", "岗位要求", "任职资格", "工作职责", "加分项")
        if len(text) >= 60 and any(m in text for m in jd_markers):
            facts["jd_text"] = text[:8000]  # 粘贴/截图OCR的JD（含职责或要求正文的都收录）
        if self.cfg.provider != "mock":
            try:
                data = self.llm.chat_json(
                    [{"role": "system", "content": FACT_EXTRACT_SYSTEM},
                     {"role": "user", "content": text[:2000]}], purpose="extract_facts")
                for k, v in (data or {}).items():
                    if v:
                        facts.setdefault(k, v)
            except Exception:
                pass  # 事实抽取失败不阻塞主流程，已有正则兜底
        changed = {}
        for k, v in facts.items():
            session.facts.setdefault("_ts", {})[k] = time.time()  # 事实时效：供自动降权/待确认
            if k == "jd_paths":
                old = set(session.facts.get("jd_paths") or [])
                merged = list(dict.fromkeys(list(session.facts.get("jd_paths") or []) + list(v)))
                if merged != list(old):
                    session.facts["jd_paths"] = merged
                    changed["jd_paths"] = merged
            elif k == "resume_paths":
                merged = list(dict.fromkeys(list(session.facts.get("resume_paths") or []) + list(v)))
                session.facts["resume_paths"] = merged
                changed["resume_paths"] = merged
            else:
                session.facts[k] = v
                changed[k] = v
        if changed:
            self.bus.emit("facts_updated", facts=changed)

    # ================= 规划 =================
    def _make_plan(self, session: Session, text: str):
        try:
            if self.cfg.provider == "mock":
                return planner_mod.build_fallback_plan(session.facts, self.cfg)
            return planner_mod.plan_with_llm(self.llm, self.registry, session.facts, text, self.cfg)
        except Exception as e:
            self.bus.emit("plan_fallback", reason=f"LLM 规划失败，使用兜底模板: {e}")
            return planner_mod.build_fallback_plan(session.facts, self.cfg)

    # ================= 计划执行 =================
    def _execute_plan(self, session: Session):
        for _ in range(len(session.tasks) + 3):  # 防死循环护栏
            # 依赖失败传播：前置失败的任务直接置败，避免卡死
            for t in session.tasks:
                if t.status == "pending":
                    dep_tasks = [session.task(d) for d in t.depends_on]
                    if any(d is None or d.status == "failed" for d in dep_tasks):
                        t.status = "failed"
                        t.error = f"前置任务失败: {t.depends_on}"
                        self.bus.emit("task_finish", task_id=t.id, status="failed", error=t.error)
                    elif dep_tasks and all(d and d.status in ("done", "skipped") for d in dep_tasks):
                        # 条件分支规划：condition 不满足则跳过（skipped 视同完成向下游放行）
                        if not self._condition_met(session, t.condition):
                            t.status = "skipped"
                            t.result = "条件不满足，已跳过"
                            self.bus.emit("task_finish", task_id=t.id, status="skipped",
                                          result_brief=t.result)
            runnable = [t for t in session.tasks if t.status == "pending"
                        and all((session.task(d) and session.task(d).status in ("done", "skipped"))
                                for d in t.depends_on)]
            if not runnable:
                break
            self._run_task(session, runnable[0])
            if session.status == "awaiting_input":
                return

    def _condition_met(self, session: Session, cond) -> bool:
        """条件分支求值：{"field": "match.score", "op": ">", "value": 80}。
        求值失败按执行处理（fail-open），绝不因条件表达式错误阻断流程。"""
        if not isinstance(cond, dict) or not cond.get("field"):
            return True
        try:
            roots = {"facts": session.facts,
                     "match": session.artifacts.get("match") or {},
                     "jd_analysis": session.artifacts.get("jd_analysis") or {},
                     "search": session.artifacts.get("search") or {}}
            cur = roots
            for part in str(cond.get("field")).split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    break
            op = str(cond.get("op") or "exists")
            val = cond.get("value")
            if op == "exists":
                return cur is not None and cur != ""
            if cur is None:
                return False
            if op == "contains":
                return str(val) in str(cur)
            if op in (">", ">=", "<", "<=", "==", "!="):
                try:
                    a, b = float(cur), float(val)
                except (TypeError, ValueError):
                    a, b = str(cur), str(val)
                return {"": True, ">": a > b, ">=": a >= b, "<": a < b,
                        "<=": a <= b, "==": a == b, "!=": a != b}.get(op, True)
        except Exception:
            pass
        return True

    def _sanitize_output(self, text: str) -> str:
        """输出脱敏：密钥/内部端点不随回复外泄（注入防护的输出侧）。"""
        return _SECRET_OUT_RE.sub("[已脱敏]", text or "")

    def _run_task(self, session: Session, task: Task, resume_obs: str = None):
        task.status = "running"
        self.bus.emit("task_start", task_id=task.id, title=task.title, tool=task.tool)
        consecutive_fails, last_obs, final_text = 0, "", None

        def on_retry(n: int, err: str):
            task.retries += 1
            self.bus.emit("retry", task_id=task.id, tool=tool, attempt=n, error=err[:200])

        for step in range(1, self.cfg.max_react_steps + 1):
            payload = self._react_payload(session, task, step, resume_obs)
            resume_obs = None
            try:
                decision = self.llm.chat_json(payload, purpose="react")
            except Exception as e:
                task.status, task.error = "failed", f"执行器决策失败: {e}"
                self.bus.emit("task_finish", task_id=task.id, status="failed", error=task.error)
                return
            action = decision.get("action") or {}
            tool = str(action.get("tool") or "none").strip().lower()
            args = dict(action.get("args") or {})
            thought = str(decision.get("thought") or "")[:300]
            self.bus.emit("thought", task_id=task.id, step=step, thought=thought)

            if tool in NON_TOOLS:
                final_text = str(decision.get("final") or "").strip()
                if not final_text:
                    final_text = self._task_answer(session, task, observation=last_obs)
                break

            if tool == "fail":  # 执行器判定任务无法完成（工具重试耗尽/材料缺失）
                task.status = "failed"
                task.error = str(action.get("reason") or decision.get("final") or "执行器判定失败")
                self.bus.emit("task_finish", task_id=task.id, status="failed", error=task.error[:300])
                return

            if tool == "ask_user":
                q = str(action.get("question") or "").strip() or ASK_USER_GUIDE
                task.status = "waiting"
                session.status = "awaiting_input"
                session.pending_question = q
                session.waiting_task_id = task.id
                session.facts["_ask_count"] = session.facts.get("_ask_count", 0) + 1
                self.bus.emit("ask_user", task_id=task.id, question=q)
                return

            if tool == "write_report" and not args.get("content"):
                args["content"] = self._task_answer(session, task, observation=last_obs)

            args = self._autofill_args(session, task, tool, args)
            cache_key = (session.id, tool,
                         json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)[:600])
            tool_cost = ""
            try:
                tool_cost = self.registry.get(tool).cost
            except Exception:
                pass
            hit_kind = None   # None=真实执行 / "mem"=内存缓存 / "disk"=磁盘缓存
            if cache_key in self._tool_cache:
                res: ToolResult = self._tool_cache[cache_key]
                hit_kind = "mem"
            else:
                self.bus.emit("tool_call", task_id=task.id, tool=tool, cost=tool_cost,
                              args={k: str(v)[:120] for k, v in args.items()})
                disk_data = disk_cache.load(tool, args) if tool in CACHABLE_TOOLS else None
                if disk_data is not None:  # 智能缓存复用：材料未变化直接复用上次分析
                    res = ToolResult(ok=True, data=disk_data)
                    hit_kind = "disk"
                else:
                    t0 = time.time()
                    res = self.registry.call(tool, args, on_retry=on_retry)
                    self._tool_elapsed = getattr(self, "_tool_elapsed", 0) + (time.time() - t0)
                    if res.ok:
                        self._tool_cache[cache_key] = res
                        if tool in CACHABLE_TOOLS:
                            disk_cache.store(tool, args, res.data)
            if len(self._tool_cache) > 200:  # 缓存上限护栏
                self._tool_cache.clear()

            if res.ok:
                cache_note = {None: "", "mem": "(内存缓存) ", "disk": "(磁盘缓存·材料未变化，0成本) "}[hit_kind]
                last_obs = cache_note + self._obs_brief(res.data)
                task.steps.append({"thought": thought, "tool": tool, "ok": True,
                                   "args": {k: str(v)[:100] for k, v in args.items()},
                                   "observation": last_obs})
                self.bus.emit("tool_result", task_id=task.id, tool=tool, brief=last_obs)
                self._update_artifacts(session, task, tool, res.data)
            else:
                last_obs = f"工具失败: {res.error}"
                task.steps.append({"thought": thought, "tool": tool, "ok": False,
                                   "args": {k: str(v)[:100] for k, v in args.items()},
                                   "observation": res.error})
                self.bus.emit("tool_error", task_id=task.id, tool=tool, error=res.error[:300])
                consecutive_fails += 1
                if consecutive_fails >= 2:  # 同一任务连续两次工具失败 → 判定失败，不无限烧 token
                    task.status, task.error = "failed", res.error
                    self.bus.emit("task_finish", task_id=task.id, status="failed", error=res.error[:300])
                    return

        if final_text is None:  # 步数用尽：用最后一次有效观察收尾
            ok_steps = [s for s in task.steps if s.get("ok")]
            if not ok_steps:
                task.status, task.error = "failed", "步骤数用尽且无有效产出"
                self.bus.emit("task_finish", task_id=task.id, status="failed", error=task.error)
                return
            final_text = self._task_answer(session, task, observation=ok_steps[-1]["observation"])

        final_text = self._sanitize_output(final_text)  # 输出脱敏（密钥/端点）
        task.status, task.result = "done", final_text
        self._update_artifacts(session, task, "answer", final_text)
        self.bus.emit("task_finish", task_id=task.id, status="done",
                      result_brief=final_text[:400])

    # ================= ask_user 恢复 =================
    def _resume(self, session: Session, answer: str):
        waiting = session.task(session.waiting_task_id)
        self.bus.emit("resumed", task_id=waiting.id if waiting else None, answer=answer[:200])
        self._refresh_facts(session, answer)
        has_material = bool(session.facts.get("jd_text") or session.facts.get("jd_paths")
                            or session.facts.get("resume_paths") or session.facts.get("resume_text"))
        if not has_material:
            if session.facts.get("_ask_count", 0) >= 2:
                if waiting:
                    waiting.status, waiting.error = "failed", "用户两轮未提供有效材料"
                    self.bus.emit("task_finish", task_id=waiting.id, status="failed",
                                  error=waiting.error)
                session.status = "running"
            else:
                self.bus.emit("ask_user", task_id=waiting.id if waiting else None,
                              question=ASK_USER_GUIDE)
            return
        # 材料到位：动态重新规划（体现 replan 能力），原等待任务作废
        if waiting:
            waiting.status = "done"
            waiting.result = "用户已补充材料，触发重新规划"
        goal, tasks = self._make_plan(session, answer)
        session.tasks = tasks
        session.status = "running"
        session.waiting_task_id = ""
        session.pending_question = ""
        self.bus.emit("plan_created", goal=f"[恢复] {goal}",
                      tasks=[t.to_dict() for t in tasks], replanned=True, costs=self._tool_costs())
        self._execute_plan(session)

    # ================= 收尾综合 =================
    def _tool_costs(self) -> dict:
        return {t.name: t.cost for t in self.registry.tools.values()}

    def _finalize(self, session: Session):
        material = {
            "facts": {k: v for k, v in session.facts.items() if not k.startswith("_")},
            "task_results": [{"title": t.title, "status": t.status,
                              "result": (t.result or t.error)[:500]} for t in session.tasks],
            "artifacts": self._compact_artifacts(session, limit=1200),
        }
        material_str = json.dumps(material, ensure_ascii=False)
        try:
            if self.cfg.provider == "mock":
                report = self.llm.chat([{"role": "user", "content": material_str}], purpose="synthesize")
            else:
                report = self.llm.chat(
                    [{"role": "system", "content": SYNTHESIZE_SYSTEM.format(material=material_str)},
                     {"role": "user", "content": "请生成本次求职分析的最终交付报告。"}],
                    purpose="synthesize")
        except Exception as e:
            report = f"## 报告生成失败\n\n子任务已完成但综合报告阶段出错（{e}）。各任务结果：\n" + \
                     "\n".join(f"- **{t.title}**: {(t.result or t.error)[:200]}" for t in session.tasks)
            self.bus.emit("error", message=f"综合报告生成失败: {e}")

        report = self._sanitize_output(report)  # 输出脱敏
        session.add_message("assistant", report)
        session.status = "done"
        chart = (session.artifacts.get("match") or {}).get("chart", "")
        failed = [t.title for t in session.tasks if t.status == "failed"]
        cost_map = self._tool_costs()
        high_cost_calls = sum(s["calls"] for n, s in self.registry.call_stats.items()
                              if cost_map.get(n) == "高")
        stats = {"llm_calls": getattr(self.llm, "calls", 0),
                 "tool_calls": sum(s["calls"] for s in self.registry.call_stats.values()),
                 "tool_retries": sum(s["retries"] for s in self.registry.call_stats.values()),
                 "failed_tasks": failed,
                 "elapsed_s": round(time.time() - getattr(self, "_run_started", time.time()), 1),
                 "high_cost_calls": high_cost_calls,
                 "llm_degraded": bool(getattr(self.llm, "degraded", False)),
                 "cache_disk_items": disk_cache.stats()["items"]}
        self.bus.emit("final_answer", content=report,
                      chart=f"/files/{chart}" if chart else "", stats=stats)
        self.bus.emit("run_done", stats=stats)

    # ================= 上下文构造 =================
    def _react_payload(self, session: Session, task: Task, step: int, resume_obs: str = None):
        transcript = "\n".join(
            f"[step{i + 1}] {s.get('tool')}: {'成功 → ' + s.get('observation', '')[:200] if s.get('ok') else '失败 → ' + s.get('observation', '')[:200]}"
            for i, s in enumerate(task.steps)) or "（尚无步骤）"
        if resume_obs:
            transcript += f"\n[用户补充] {resume_obs[:500]}"
        body = {
            "step": step, "task": {"id": task.id, "title": task.title, "detail": task.detail,
                                   "tool_hint": task.tool, "args_hint": task.args},
            "facts": {k: v for k, v in session.facts.items() if not str(k).startswith("_")},
            "artifacts_brief": self._compact_artifacts(session, limit=800),
            "transcript": transcript,
        }
        if self.cfg.provider == "mock":
            return [{"role": "user", "content": json.dumps(
                {**body, "tool_hint": task.tool, "args": task.args,
                 "title": task.title}, ensure_ascii=False)}]
        system = REACT_SYSTEM.format(tool_schema=self.registry.prompt_schema())
        return [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)}]

    def _compact_artifacts(self, session: Session, limit: int = 1000) -> dict:
        art = session.artifacts
        out = {}
        if art.get("jd_analysis"):
            jd = art["jd_analysis"]
            out["jd_analysis"] = {k: jd.get(k) for k in
                                  ("skills_hard", "skills_soft", "education",
                                   "experience_years", "salary")}
            if jd.get("requirements"):
                out["jd_analysis"]["requirements"] = jd["requirements"][:5]
        if art.get("match"):
            m = art["match"]
            out["match"] = {"score": m.get("score"), "grade": m.get("grade"),
                            "matched_skills": list((m.get("matched_skills") or {}).keys()),
                            "missing_skills": list((m.get("missing_skills") or {}).keys()),
                            "chart": m.get("chart"), "html": m.get("html")}
        if art.get("jd_text"):
            out["jd_text_brief"] = art["jd_text"][:limit]
        if art.get("resume_text"):
            out["resume_text_brief"] = art["resume_text"][:600]
        if art.get("search"):
            out["search"] = [{"title": r.get("title"), "url": r.get("url")}
                             for r in art["search"].get("results", [])[:5]]
        if art.get("interview_questions"):
            out["interview_questions"] = art["interview_questions"][:500]
        if art.get("suggestions"):
            out["suggestions"] = art["suggestions"][:500]
        return out

    # ================= 产物接线 / 参数自动注入 =================
    def _update_artifacts(self, session: Session, task: Task, tool: str, data):
        art = session.artifacts
        ctx = (task.title + task.detail).lower()
        if tool in READ_TOOLS:
            text = (data or {}).get("text", "")
            path = (data or {}).get("path", "").lower()
            if "简历" in ctx or "resume" in path or "cv" in path:
                art["resume_text"] = text
            else:
                art["jd_text"] = text  # 计划中读取的材料默认视作 JD（含截图OCR）
        elif tool == "jd_analyze":
            art["jd_analysis"] = data
        elif tool == "resume_match":
            art["match"] = data
        elif tool == "write_report":
            art["report"] = data
        elif tool == "web_search":
            art["search"] = data
        elif tool == "answer":
            if "面试" in task.title:
                art["interview_questions"] = data
            elif "建议" in task.title or "优化" in task.title or "报告" in task.title:
                art["suggestions"] = data

    def _autofill_args(self, session: Session, task: Task, tool: str, args: dict) -> dict:
        """执行器与工具之间的产物自动接线：缺失参数从 artifacts / facts 注入。"""
        art = session.artifacts
        args = {k: v for k, v in args.items() if v not in ("", None, "上一步结果", "自动注入")}
        if tool == "jd_analyze" and not args.get("jd_text") and not args.get("jd_path"):
            # 注入优先级：上一步读取的原文 > 会话记忆中的JD文本 > 已有结构化结果
            if art.get("jd_text"):
                args["jd_text"] = art["jd_text"]
            elif session.facts.get("jd_text"):
                args["jd_text"] = session.facts["jd_text"]
            elif art.get("jd_analysis"):
                args["jd_text"] = json.dumps(art["jd_analysis"], ensure_ascii=False)
        if tool == "resume_match":
            if not args.get("jd_text"):
                if art.get("jd_text"):
                    args["jd_text"] = art["jd_text"]
                elif session.facts.get("jd_text"):  # 纯文本/OCR的JD没有读取步骤，从会话记忆注入
                    args["jd_text"] = session.facts["jd_text"]
            if not args.get("resume_text") and not args.get("resume_path"):
                if art.get("resume_text"):
                    args["resume_text"] = art["resume_text"]
                else:
                    args["resume_path"] = ((session.facts.get("resume_paths") or [None])[0]
                                           or default_resume_path())
            args.setdefault("role", session.facts.get("target_role") or task.title)
        if tool in ("file_read", "pdf_extract", "docx_extract", "image_ocr") and not args.get("path"):
            paths = session.facts.get("jd_paths") or []
            if paths:
                args["path"] = paths[0]
        return args

    # ================= 辅助 =================
    def _task_answer(self, session: Session, task: Task, observation: str = "") -> str:
        """tool=none 时由模型（或 mock 模板）产出任务结论。"""
        body = json.dumps({"title": task.title, "detail": task.detail, "tool_hint": task.tool,
                           "observation": observation[:800],
                           "facts": {k: v for k, v in session.facts.items() if not str(k).startswith("_")},
                           "artifacts": session.artifacts},  # mock 模板需要全量数据；真实LLM走下方compact分支
                          ensure_ascii=False, default=str)
        if self.cfg.provider != "mock":
            body = json.dumps({"title": task.title, "detail": task.detail,
                               "observation": observation[:800],
                               "artifacts": self._compact_artifacts(session, limit=1500)},
                              ensure_ascii=False, default=str)
        if self.cfg.provider == "mock":
            return self.llm.chat([{"role": "user", "content": body}], purpose="task_answer")
        return self.llm.chat(
            [{"role": "system",
              "content": PERSONA_RULES + "\n\n你是AI求职助手的执行器。基于材料完成当前子任务，"
                         "输出具体、可执行的中文 markdown 内容，直接给结论。材料：" + body},
             {"role": "user", "content": f"请完成任务「{task.title}」：{task.detail}"}],
            purpose="task_answer")

    @staticmethod
    def _obs_brief(data) -> str:
        if isinstance(data, dict):
            if "text" in data:
                return f"读取成功({len(data['text'])}字): " + data["text"][:150].replace("\n", " ")
            if "results" in data:
                return f"搜索到{len(data['results'])}条: " + "; ".join(
                    r.get("title", "")[:30] for r in data["results"][:3])
            if "score" in data:
                return f"匹配分 {data.get('score')}({data.get('grade')}级)，缺失技能: " + \
                       ",".join(list((data.get("missing_skills") or {}).keys())[:6])
            if "skills_hard" in data:
                return "JD分析完成，Top技能: " + ",".join(list(data["skills_hard"].keys())[:8])
            if "path" in data:
                return f"已写入 {data['path']}"
        return str(data)[:200]
