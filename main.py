"""CLI 入口：
  python main.py "帮我分析 data/jds/jd01.txt，匹配简历并给面试题"   # 单轮分析
  python main.py                                                    # 交互多轮对话
  python main.py --serve                                            # 启动Web服务
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from config import load_config
from core.agent import JobAgent

COLOR = {"task_start": "\033[36m", "done": "\033[32m", "failed": "\033[31m",
         "retry": "\033[33m", "ask_user": "\033[33m"}


def print_event(evt: dict):
    t = evt.get("type")
    if t == "plan_created":
        print(f"\n\033[35m📋 任务规划（{len(evt['tasks'])}个子任务）:\033[0m {evt['goal']}")
        for i, task in enumerate(evt["tasks"], 1):
            tool = task.get("tool", "none")
            print(f"   {i}. [{tool}] {task['title']}")
    elif t == "task_start":
        print(f"\n▶ {evt['title']}")
    elif t == "thought":
        print(f"   💭 {evt['thought']}")
    elif t == "tool_call":
        print(f"   🔧 {evt['tool']}({str(evt.get('args'))[:100]})")
    elif t == "tool_result":
        print(f"   ↳ {evt['brief'][:150]}")
    elif t == "tool_error":
        print(f"   {COLOR['failed']}↳ 工具失败: {evt['error'][:150]}\033[0m")
    elif t == "retry":
        print(f"   {COLOR['retry']}🔁 第{evt['attempt']}次重试: {evt['error'][:100]}\033[0m")
    elif t == "task_finish":
        mark = "✅" if evt["status"] == "done" else "❌"
        print(f"{mark} 子任务完成" if evt["status"] == "done"
              else f"{mark} 子任务失败: {evt.get('error', '')[:120]}")
    elif t == "ask_user":
        print(f"\n{COLOR['ask_user']}❓ Agent 需要补充信息: {evt['question']}\033[0m")
    elif t == "final_answer":
        print("\n" + "=" * 60 + "\n" + evt.get("content", "") + "\n" + "=" * 60)
        if evt.get("stats"):
            print(f"统计: {evt['stats']}")


def cli():
    cfg = load_config()
    agent = JobAgent(cfg)
    agent.bus.subscribe(print_event)
    memory = agent.memory
    session = memory.new_session()
    print(f"AI求职助手（provider={cfg.provider}/{cfg.model}）输入 quit 退出。")

    if len(sys.argv) > 1:  # 单轮模式：直接执行命令行目标
        agent.handle_message(session, sys.argv[1])
        return

    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in ("quit", "exit", "q"):
            break
        if not text:
            continue
        agent.handle_message(session, text)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        import uvicorn
        from server import app
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    else:
        cli()
