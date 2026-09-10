# -*- coding: utf-8 -*-
"""Pydantic 结构化校验测试：验证 LLM 输出的边界校验与降级行为。

不依赖任何外部 API。运行：
    python tests/test_schemas.py
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LLM_PROVIDER"] = "mock"

from config import load_config  # noqa: E402
from core.planner import validate_plan  # noqa: E402
from core.schemas import (PlanSchema, ReactDecisionSchema,  # noqa: E402
                          validate_plan_dict, validate_react_decision)

PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  ({detail})" if detail else ""))


TOOLS = {"file_read", "jd_analyze", "resume_match", "write_report", "web_search", "image_ocr"}


def main():
    cfg = load_config()

    print("== 1. 合法计划 ==")
    good = {"goal": "分析JD", "tasks": [
        {"id": "t1", "title": "读取JD", "tool": "file_read", "args": {"path": "a.txt"}},
        {"id": "t2", "title": "结构化分析", "tool": "jd_analyze", "depends_on": ["t1"]},
        {"id": "t3", "title": "匹配简历", "tool": "resume_match", "depends_on": ["t2"]},
    ]}
    tasks = validate_plan(good, cfg, known_tools=TOOLS)
    check("合法计划通过", len(tasks) == 3, f"{len(tasks)} tasks")
    check("字段正确映射", tasks[1].title == "结构化分析" and tasks[1].depends_on == ["t1"])
    check("goal 正确", PlanSchema.model_validate(good).goal == "分析JD")

    print("== 2. 结构非法 → 拒绝（触发模板计划降级） ==")
    check("非 dict", validate_plan_dict("not a dict") is None)
    check("缺 tasks", validate_plan_dict({"goal": "x"}) is None)
    check("tasks 为空", validate_plan_dict({"tasks": []}) is None)
    check("tasks 非列表", validate_plan_dict({"tasks": {"a": 1}}) is None)
    check("任务缺 title",
          validate_plan_dict({"tasks": [{"id": "t1", "detail": "无标题"}]}) is None)

    print("== 3. 依赖合法性 ==")
    check("依赖不存在",
          validate_plan_dict({"tasks": [{"id": "t1", "title": "a", "depends_on": ["t9"]}]}) is None)
    check("依赖自身",
          validate_plan_dict({"tasks": [{"id": "t1", "title": "a", "depends_on": ["t1"]}]}) is None)
    cycle = {"tasks": [{"id": "t1", "title": "a", "depends_on": ["t2"]},
                       {"id": "t2", "title": "b", "depends_on": ["t1"]}]}
    check("双任务互依赖成环 → 拒绝（否则会留下僵尸任务）", validate_plan_dict(cycle) is None)
    three_cycle = {"tasks": [{"id": "t1", "title": "a", "depends_on": ["t3"]},
                             {"id": "t2", "title": "b", "depends_on": ["t1"]},
                             {"id": "t3", "title": "c", "depends_on": ["t2"]}]}
    check("三任务环 → 拒绝", validate_plan_dict(three_cycle) is None)

    print("== 4. id 处理 ==")
    dup = {"tasks": [{"id": "t1", "title": "a"}, {"id": "t1", "title": "b"}]}
    check("id 重复 → 拒绝", validate_plan_dict(dup) is None)
    noid = {"tasks": [{"title": "a"}, {"title": "b"}]}
    p = validate_plan_dict(noid)
    check("缺 id 自动补 t1/t2", p is not None and [t.id for t in p.tasks] == ["t1", "t2"])
    numid = {"tasks": [{"id": 1, "title": "a"}, {"id": 2, "title": "b", "depends_on": [1]}]}
    p = validate_plan_dict(numid)
    check("数字 id 归一为字符串且依赖可解析",
          p is not None and [t.id for t in p.tasks] == ["1", "2"], str(p and [t.id for t in p.tasks]))

    print("== 5. 工具名校验（降级而非作废） ==")
    unk = {"tasks": [{"id": "t1", "title": "a", "tool": "read_pdf_file"}]}
    p = validate_plan_dict(unk, known_tools=TOOLS)
    check("未知工具降级为 none（不整份作废）", p is not None and p.tasks[0].tool == "none")
    p2 = validate_plan_dict({"tasks": [{"id": "t1", "title": "a", "tool": "file_read"}]},
                            known_tools=TOOLS)
    check("合法工具保留", p2.tasks[0].tool == "file_read")
    p3 = validate_plan_dict({"tasks": [{"id": "t1", "title": "a", "tool": "ask_user"}]},
                            known_tools=TOOLS)
    check("保留字 ask_user 不算违规", p3.tasks[0].tool == "ask_user")

    print("== 6. 字段清洗与截断 ==")
    long = {"tasks": [{"id": "t1", "title": "标" * 200}]}
    p = validate_plan_dict(long)
    check("超长 title 被拒绝或截断", p is None or len(p.tasks[0].title) <= 60)
    dirty = {"tasks": [{"id": "t1", "title": "  a  ", "args": "not-a-dict",
                        "depends_on": ["", None, "t2", "t2"]},
                       {"id": "t2", "title": "b"}]}
    p = validate_plan_dict(dirty)
    check("args 非字典 → 归一为空字典", p is not None and p.tasks[0].args == {})
    check("依赖去重并去空值", p is not None and p.tasks[0].depends_on == ["t2"],
          str(p and p.tasks[0].depends_on))

    print("== 7. ReAct 决策归一化（旧实现会击穿会话的场景） ==")
    d = validate_react_decision({"thought": "t", "action": "write_report"})
    check("action 是字符串 → 归一为对象（不再抛 AttributeError）",
          isinstance(d, ReactDecisionSchema) and d.action.tool == "write_report")
    d = validate_react_decision({"action": {"tool": "x", "args": "path=y"}})
    check("args 是字符串 → 归一为空字典", d.action.args == {})
    d = validate_react_decision({"action": {"tool": "  FILE_READ  "}})
    check("工具名去空格并小写", d.action.tool == "file_read")
    d = validate_react_decision(None)
    check("None → 无动作", d.action.tool == "none")
    d = validate_react_decision([1, 2, 3])
    check("列表 → 无动作", d.action.tool == "none")
    d = validate_react_decision({"action": {"tool": "fail", "reason": "材料缺失"}})
    check("fail 保留 reason", d.action.tool == "fail" and d.action.reason == "材料缺失")
    d = validate_react_decision({"action": {"tool": "ask_user", "question": "请给简历"}})
    check("ask_user 保留 question", d.action.question == "请给简历")
    d = validate_react_decision({"final": "结论内容"})
    check("final 保留", d.final == "结论内容")
    d = validate_react_decision({"thought": "x" * 1000})
    check("超长 thought 截断到 300", len(d.thought) <= 300, str(len(d.thought)))

    print("== 8. 端到端：mock 模式下 Agent 仍正常跑通 ==")
    from core.agent import JobAgent
    from core.memory import Memory
    agent = JobAgent(cfg)
    mem = Memory(cfg.sessions_dir, agent.llm, privacy_mode=True)
    s = mem.new_session()
    agent.handle_message(s, "帮我分析 data/jds/jd03_数据分析师.txt，匹配简历并给面试题")
    check("规划产出任务", len(s.tasks) >= 2, f"{len(s.tasks)} tasks")
    check("任务都到达终态", all(t.status in ("done", "failed", "skipped") for t in s.tasks),
          str([(t.id, t.status) for t in s.tasks]))
    check("会话状态 done", s.status == "done", s.status)

    total = len(PASS)
    ok = sum(1 for _, v in PASS if v)
    print("\n" + "=" * 46)
    print(f"结果: {ok}/{total} 通过" + (" 🎉" if ok == total else "  ⚠️"))
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
