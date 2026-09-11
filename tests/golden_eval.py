# -*- coding: utf-8 -*-
"""黄金集质量回归（评审 P1-4：无在线/离线质量评估）。

对 tests/golden_set.json 的 20 条黄金用例逐条跑完整 Agent 流水线，统计
「子任务完成数 / 工具调用数 / 命中词 / 违规词 / 断言结果」，汇总四个质量指标
（任务成功率、步骤效率、内容合规率、追问正确率）并与阈值比较，给出退出码。

运行（在仓库根目录）：
    $env:PYTHONPATH="E:\ai\ai job\agent\backend"
    python tests/golden_eval.py                 # 默认离线 mock，无需任何密钥
    python tests/golden_eval.py --real          # 走真实端点（需凭据；消耗额度）
    python tests/golden_eval.py --only g01,g15  # 只跑指定用例
    python tests/golden_eval.py --limit 3       # 只跑前 N 条（--real 试跑省钱）
退出码：0 = 全部断言通过且四项指标均达标；1 = 有指标低于阈值或断言失败。

———————————————— 局限（必读，不要把它当成真实质量评测） ————————————————
1. **默认 mock 模式下这是「确定性回归」，不是「质量评测」。** mock 的文案由模板生成、
   分数来自确定性算法（jd_analyze / resume_match），因此它只能回答「报告结构/流程/
   安全边界有没有坏」，**不能**回答「回答有没有变好」。真实质量需要 `--real` +
   人工评分（脚本末尾会打印人工评分表模板）。
2. `--real` 下模型规划形态可能与 mock 不同（子任务数、工具序列、话术都会变），
   本脚本的阈值与断言是按离线基线定的，真实模式下失败不一定代表质量退化，
   必须人工判读；报告落盘在 data/reports/ 供人工打分。
3. 复跑时工具结果命中磁盘缓存（data/cache），所以本脚本覆盖的是「编排 + 报告结构 +
   安全边界」，不覆盖工具算法本身的回归（那由 test_smoke / test_schemas 覆盖）
   与真实模型的生成质量。
4. 在线质量闭环（用户 1–5 分反馈回写 `POST /api/sessions/{id}/feedback`）属于接口层，
   由 scripts/smoke_api.py 覆盖；本脚本只做离线黄金集回归。
5. 会话落在临时目录（SESSIONS_DIR 指向 temp），data/profile.json（跨会话长期记忆）
   运行前清空、运行后还原，保证用例之间不互相串味、也不破坏本机数据。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 被重定向的流没有 reconfigure
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))  # 新布局：应用代码在 backend/

# —— 环境隔离（必须在 load_config() 之前）：会话落临时目录，默认强制离线 ——
_TMP_SESSIONS = Path(tempfile.mkdtemp(prefix="pf_golden_sessions_"))
os.environ["SESSIONS_DIR"] = str(_TMP_SESSIONS)
if "--real" not in sys.argv:
    os.environ["LLM_PROVIDER"] = "mock"

from config import load_config  # noqa: E402
from core import profile as profile_store  # noqa: E402
from core.agent import JobAgent  # noqa: E402
from core.memory import Memory  # noqa: E402

# —— 用例里的长文本占位符：写进 JSON 会让用例表不可读，统一在这里展开 ——
_JD_PASTE = ("岗位职责：负责大模型应用研发，搭建RAG检索增强问答系统，参与Agent工具链建设与Prompt工程。\n"
             "任职要求：本科及以上学历，2年以上NLP或大模型应用经验，精通Python，熟悉LangChain框架与向量数据库。")
_JD_NOISY = ("岗位职责：负责数據分析和报表。另：公司附近有健身房和咖啡厅，团队氛围轻松，"
             "老板人很好经常组织聚餐，提供下午茶和节日礼物。\n"
             "任职要求：熟炼使用SQL和Python；逻辑思维清晰，本科及以上学历。")
_JD_LONG = ("岗位职责：负责数据分析与指标体系搭建，输出经营分析报告。\n"
            "任职要求：精通SQL、Python、Excel、Tableau，熟悉A/B测试与用户增长，本科及以上学历。\n") * 120
PLACEHOLDERS = {"{JD_PASTE}": _JD_PASTE, "{JD_NOISY}": _JD_NOISY, "{JD_LONG}": _JD_LONG}

# —— 阈值（任务成功率来自 REVIEW 第 5 节 E1 的 ≥85%；其余为本轮 P1-4 验收口径） ——
THRESHOLDS = {"任务成功率": 0.85, "步骤效率": 0.95, "内容合规率": 0.90, "追问正确率": 1.00}
HIT_COVERAGE_MIN = 0.8    # 单条用例 must_contain 覆盖率下限（低于此值判「内容不合规」）
TOOL_CALLS_MAX = 12       # E2：每任务工具调用 ≤ 12
TASKS_MAX = 6             # E2：每任务子任务数 ≤ 6


def load_cases() -> list[dict]:
    """读黄金集（顶层可以是 {"cases": [...]} 或直接是数组）。"""
    raw = json.loads((ROOT / "tests" / "golden_set.json").read_text(encoding="utf-8"))
    cases = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(cases, list) or not cases:
        raise ValueError("golden_set.json 未提供用例（应为 {\"cases\": [...]} 或顶层数组）")
    return cases


def expand(text: str) -> str:
    """展开 {JD_PASTE} / {JD_NOISY} / {JD_LONG} 占位符。"""
    for key, val in PLACEHOLDERS.items():
        text = text.replace(key, val)
    return text


def run_case(agent: JobAgent, memory: Memory, case: dict) -> dict:
    """跑一条用例（followup 存在时在同一会话里跑第二轮），收集断言素材。"""
    session = memory.new_session()
    events: list[dict] = []
    unsubscribe = agent.bus.subscribe(events.append)  # 用完必须退订，避免订阅者随用例累积
    t0 = time.time()
    try:
        turns = [case.get("task", "")]
        if case.get("followup"):
            turns.append(case["followup"])
        for text in turns:
            agent.handle_message(session, expand(text))
    finally:
        unsubscribe()
    elapsed = time.time() - t0

    exp = case.get("expect") or {}
    types = [e["type"] for e in events]
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    tool_names = [e.get("tool", "") for e in tool_calls]
    ask_events = [e for e in events if e["type"] == "ask_user"]
    answers = [e for e in events if e["type"] == "final_answer"]
    # 「最终回复」= 最后一次 final_answer；没有 final_answer（追问/拦截路径）时取追问话术
    content = answers[-1]["content"] if answers else (session.pending_question or "")
    # 违规词扫描面：最终回复 + 追问话术 + 全部「助手」消息 + 子任务结果/错误——
    # 中途静默报错、被污染的任务结论都算违规，不能只看最后一段；
    # 注意只扫助手侧：用户自己粘贴的原文里出现违规词（如 JD 里的「健身房」）不算助手违规，
    # 否则「干扰信息未污染报告」这条断言会因用户输入本身而假失败。
    pool = [content, session.pending_question or ""]
    pool += [str(m.get("content", "")) for m in session.messages if m.get("role") == "assistant"]
    pool += [(t.result or "") + (t.error or "") for t in session.tasks]
    all_text = "\n".join(pool)

    tasks_done = [t for t in session.tasks if t.status == "done"]
    match = session.artifacts.get("match") or {}
    report = session.artifacts.get("report") or {}
    must = exp.get("must_contain") or []
    hits = [x for x in must if x in content]
    viol = [x for x in (exp.get("must_not_contain") or []) if x in all_text]

    checks: list[tuple] = []

    def chk(name: str, cond, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    chk("无违规词", not viol, ("命中: " + "、".join(viol)) if viol else "")
    chk(f"命中词全覆盖({len(hits)}/{len(must)})", not [x for x in must if x not in content],
        "缺: " + "、".join(x for x in must if x not in content))
    chk(f"子任务完成≥{exp.get('min_tasks_done', 0)}",
        len(tasks_done) >= exp.get("min_tasks_done", 0), f"{len(tasks_done)}/{len(session.tasks)}")
    chk(f"工具调用≥{exp.get('tool_calls_min', 0)}",
        len(tool_calls) >= exp.get("tool_calls_min", 0), str(len(tool_calls)))
    chk(f"工具调用≤{exp.get('tool_calls_max', TOOL_CALLS_MAX)}",
        len(tool_calls) <= exp.get("tool_calls_max", TOOL_CALLS_MAX), str(len(tool_calls)))
    chk(f"子任务数≤{exp.get('max_tasks', TASKS_MAX)}",
        len(session.tasks) <= exp.get("max_tasks", TASKS_MAX), str(len(session.tasks)))
    if "score_range" in exp:
        lo, hi = exp["score_range"]
        score = match.get("score")
        chk(f"匹配分∈[{lo},{hi}]", score is not None and lo <= score <= hi, f"score={score}")
    if exp.get("expect_status"):
        chk(f"终态={exp['expect_status']}", session.status == exp["expect_status"], session.status)
    if exp.get("expect_ask_user"):
        chk("产生 ask_user", bool(ask_events))
    if exp.get("expect_event"):
        chk(f"事件 {exp['expect_event']}", exp["expect_event"] in types)
    if exp.get("expect_no_plan"):
        chk("未生成计划", "plan_created" not in types)
    if exp.get("expect_report"):
        chk("报告已落盘", bool(report.get("path")), str(report.get("path", "")))
    if exp.get("forbid_tool_calls"):
        chk("零工具调用", not tool_calls, "、".join(tool_names))
    for bad in exp.get("forbid_tools") or []:
        chk(f"未调用 {bad}", bad not in tool_names)

    return {
        "id": case["id"], "category": case.get("category", "-"), "difficulty": case.get("difficulty", "-"),
        "expect": exp, "checks": checks, "ok": all(c[1] for c in checks),
        "tasks_total": len(session.tasks), "tasks_done": len(tasks_done),
        "tool_calls": len(tool_calls), "tool_names": tool_names,
        "ask_count": len(ask_events),
        "hits": len(hits), "must_total": len(must), "viol": viol,
        "coverage": (len(hits) / len(must)) if must else 1.0,
        "score": match.get("score"), "report": report.get("path", ""), "elapsed": elapsed,
    }


def summarize(results: list[dict]) -> list[dict]:
    """汇总四个质量指标：每项给出实测值、阈值、是否达标与口径说明。"""
    # 1) 任务成功率：期望跑完（expect_status=done）的用例里，done 子任务占全部子任务的比例
    eligible = [r for r in results if r["expect"].get("expect_status") == "done"]
    total_t = sum(r["tasks_total"] for r in eligible)
    done_t = sum(r["tasks_done"] for r in eligible)
    task_success = (done_t / total_t) if total_t else 1.0
    # 2) 步骤效率：子任务 ≤6 且工具调用 ≤12 的用例占比
    eff_ok = [r for r in results if r["tasks_total"] <= TASKS_MAX and r["tool_calls"] <= TOOL_CALLS_MAX]
    # 3) 内容合规率：无违规词，且 must_contain 覆盖率 ≥ HIT_COVERAGE_MIN
    compliant = [r for r in results if not r["viol"] and r["coverage"] >= HIT_COVERAGE_MIN]
    # 4) 追问正确率：追问类必须有 ask_user；闲聊/拦截类必须零工具调用
    ask_cases = [r for r in results if r["expect"].get("expect_ask_user") or r["expect"].get("forbid_tool_calls")]
    ask_ok = []
    for r in ask_cases:
        good = True
        if r["expect"].get("expect_ask_user"):
            good = good and r["ask_count"] > 0
        if r["expect"].get("forbid_tool_calls"):
            good = good and r["tool_calls"] == 0
        ask_ok.append(good)

    def row(name, value, detail):
        return {"name": name, "value": value, "threshold": THRESHOLDS[name],
                "ok": value >= THRESHOLDS[name], "detail": detail}

    return [
        row("任务成功率", round(task_success, 3),
            f"{len(eligible)} 条应跑完用例的 done 子任务占比（{done_t}/{total_t}）"),
        row("步骤效率", round(len(eff_ok) / len(results), 3),
            f"子任务≤{TASKS_MAX} 且工具调用≤{TOOL_CALLS_MAX} 的用例（{len(eff_ok)}/{len(results)}）"),
        row("内容合规率", round(len(compliant) / len(results), 3),
            f"无违规词且命中词覆盖≥{HIT_COVERAGE_MIN} 的用例（{len(compliant)}/{len(results)}）"),
        row("追问正确率", round(sum(ask_ok) / len(ask_cases), 3) if ask_cases else 1.0,
            f"追问类必出 ask_user、闲聊/拦截类零工具调用（{sum(ask_ok)}/{len(ask_cases)}）"),
    ]


def _width(s) -> int:
    """显示宽度：中日韩全角字符按 2 列计，保证表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, n: int) -> str:
    s = str(s)
    return s + " " * max(0, n - _width(s))


def main() -> int:
    ap = argparse.ArgumentParser(description="黄金集质量回归（默认离线 mock）")
    ap.add_argument("--real", action="store_true",
                    help="走真实 LLM 端点（需 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL；消耗额度，默认不跑）")
    ap.add_argument("--only", default="", help="只跑指定 id，逗号分隔（如 g01,g09,g15）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部；--real 试跑建议 3）")
    args = ap.parse_args()

    cases = load_cases()
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in want]
    if args.limit > 0:
        cases = cases[:args.limit]
    if not cases:
        print("❌ 没有匹配的用例，请检查 --only / --limit")
        shutil.rmtree(_TMP_SESSIONS, ignore_errors=True)
        return 1

    cfg = load_config()
    if args.real:
        if cfg.provider == "mock":
            print("❌ --real 需要完整凭据：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL"
                  "（当前凭据不完整，配置已降级为 mock，无法走真实端点）")
            shutil.rmtree(_TMP_SESSIONS, ignore_errors=True)
            return 1
        print(f"⚠️ 真实模型模式 {cfg.provider}/{cfg.model}：会消耗真实额度；"
              "离线阈值仅供参考，真实质量结论须人工评分（见本文件头「局限」）。")
    elif cfg.provider != "mock":
        # config.model_post_init 在「凭据齐全」时会把 provider 归一化为真实端点（即使环境变量写了 mock），
        # 这里显式切回 mock：离线回归绝不扣费、绝不依赖网络。
        print("⚠️ 检测到本机 .env 配置了真实 LLM 凭据，已强制切回离线 mock（避免误扣费）")
        cfg.provider = "mock"

    print(f"黄金集：tests/golden_set.json ｜ 用例 {len(cases)} 条 ｜ 会话隔离目录 {cfg.sessions_dir}")
    print(f"模式：{'离线 mock（确定性回归）' if cfg.provider == 'mock' else '真实端点（需人工评分）'}"
          f" ｜ 阈值：任务成功率≥{THRESHOLDS['任务成功率']} 步骤效率≥{THRESHOLDS['步骤效率']}"
          f" 内容合规率≥{THRESHOLDS['内容合规率']} 追问正确率≥{THRESHOLDS['追问正确率']}\n")

    prof_file = ROOT / "data" / "profile.json"
    prof_backup = prof_file.read_bytes() if prof_file.exists() else None
    profile_store.clear()  # 从「无长期记忆」开始，保证用例之间不串味（跑完还原本机画像）
    results: list[dict] = []
    t_start = time.time()
    try:
        agent = JobAgent(cfg)
        memory = Memory(cfg.sessions_dir)
        print("== 一、逐条用例 ==")
        print(_pad("id", 5) + _pad("分类", 11) + _pad("子任务完成", 12) + _pad("工具调用", 10)
              + _pad("命中词", 8) + _pad("违规词", 8) + _pad("匹配分", 9) + "断言结果")
        for case in cases:
            r = run_case(agent, memory, case)
            results.append(r)
            row = (_pad(r["id"], 5) + _pad(r["category"], 11)
                   + _pad(f"{r['tasks_done']}/{r['tasks_total']}", 12)
                   + _pad(f"{r['tool_calls']} ({','.join(r['tool_names'][:3]) or '-'})"
                          if r["tool_calls"] else "0", 10)
                   + _pad(f"{r['hits']}/{r['must_total']}", 8) + _pad(len(r["viol"]), 8)
                   + _pad(r["score"] if r["score"] is not None else "-", 9))
            n_ok = sum(1 for _, ok, _ in r["checks"] if ok)
            row += ("✅ PASS" if r["ok"] else "❌ FAIL") + \
                f"  ({n_ok}/{len(r['checks'])} 断言, {r['elapsed']:.2f}s)"
            print(row)
    finally:
        shutil.rmtree(_TMP_SESSIONS, ignore_errors=True)
        if prof_backup is None:
            prof_file.unlink(missing_ok=True)
        else:
            prof_file.write_bytes(prof_backup)

    total_elapsed = time.time() - t_start
    metrics = summarize(results)

    print("\n== 二、质量指标（阈值见文件头与 docs/REVIEW.md 第 5 节 E1/E2） ==")
    print(_pad("指标", 14) + _pad("实测", 10) + _pad("阈值", 9) + _pad("判定", 8) + "口径")
    for m in metrics:
        print(_pad(m["name"], 14) + _pad(f"{m['value']:.3f}", 10) + _pad(f"≥{m['threshold']:.2f}", 9)
              + _pad("✅ 达标" if m["ok"] else "⚠️ 低于阈值", 8) + m["detail"])
    viol_total = sum(len(r["viol"]) for r in results)
    print(f"违规词总数：{viol_total}（硬门槛：0）｜ 用例总耗时 {total_elapsed:.1f}s"
          f"｜ 平均 {(total_elapsed / len(results)):.2f}s/条")

    fails = [r for r in results if not r["ok"]]
    if fails:
        print("\n== 三、失败明细 ==")
        for r in fails:
            bad = [f"{n}" + (f"（{d}）" if d else "") for n, ok, d in r["checks"] if not ok]
            print(f"- {r['id']} [{r['category']}/{r['difficulty']}]: " + "；".join(bad))
            print(f"  期望：{json.dumps(r['expect'], ensure_ascii=False)}")

    all_ok = (not fails) and all(m["ok"] for m in metrics) and viol_total == 0
    print("\n" + "=" * 60)
    if all_ok:
        print(f"结果：✅ 全部通过（{len(results)}/{len(results)} 用例断言通过，4 项指标全部达标）")
    else:
        print(f"结果：⚠️ 未达标（用例断言 {len(results) - len(fails)}/{len(results)} 通过，"
              f"指标 {sum(1 for m in metrics if m['ok'])}/4 达标，违规词 {viol_total}）")
    if cfg.provider == "mock":
        print("提醒：以上为 mock 确定性回归（防报告结构/流程/安全边界退化），"
              "不等于真实模型下的质量评测；真实质量请用 --real + 人工评分。")
    else:
        print("人工评分表：对本次生成的报告按 1–5 分打分（要点覆盖/可执行性/无编造/结构清晰/语气得体），"
              "均分 ≥4.0 且不低于基线 −5%。本次报告：")
        for r in results:
            if r["report"]:
                print(f"  - {r['id']} → {r['report']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
