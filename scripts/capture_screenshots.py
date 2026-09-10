"""用 Playwright 自动操作「求职智囊 Agent」Web 界面并截图（供 README 展示）。

前置：
    pip install playwright && playwright install chromium
    python server.py            # 默认 http://127.0.0.1:8000

用法：
    python scripts/capture_screenshots.py [--url http://127.0.0.1:8000]
                                          [--goal "帮我分析 data/jds/jd03_数据分析师.txt ..."]

产出（保存到项目根目录 screenshots/）：
    agent_main.png    主界面（欢迎语 + 空工作流面板）
    agent_running.png 运行过程（任务规划卡片 + 工具调用 + 思考流）
    agent_result.png  运行结果（最终报告 + 完成态工作流）
    agent_memory.png  Agent 记忆面板（画像与统计）
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "screenshots"
DEFAULT_GOAL = "帮我分析 data/jds/jd03_数据分析师.txt，匹配我的简历，给出面试题"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--goal", default=DEFAULT_GOAL)
    ap.add_argument("--timeout", type=int, default=420, help="等待任务完成的秒数上限")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1000},
                                device_scale_factor=1.5)
        print(f"→ 打开 {args.url}")
        page.goto(args.url, wait_until="networkidle", timeout=60_000)
        # 新会话会收到欢迎语，等它出现说明前后端已连通
        page.wait_for_selector("#messages .row.ai", timeout=60_000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT_DIR / "agent_main.png"))
        print("  ✅ agent_main.png")

        # ---- 派发目标：触发 规划 → 工具调用 → 报告 全流程 ----
        box = page.locator("#input")
        box.click()
        box.fill(args.goal)
        page.locator("#send").click()

        # 运行过程：等任务卡片出现后再多等一会儿，让工具调用与思考流铺满面板
        try:
            page.wait_for_selector("#tasks .task", timeout=90_000)
        except Exception:  # noqa: BLE001
            print("  ⚠️  未等到任务卡片，仍在运行中截图")
        page.wait_for_timeout(20_000)
        page.screenshot(path=str(OUT_DIR / "agent_running.png"))
        print("  ✅ agent_running.png")

        # 等最终报告（状态栏出现「任务完成」）
        deadline = time.time() + args.timeout
        done = False
        while time.time() < deadline:
            txt = page.locator("#statusText").inner_text()
            if "完成" in txt:
                done = True
                break
            if "失败" in txt or "中断" in txt:
                break
            page.wait_for_timeout(3000)
        print(f"  {'✅' if done else '⚠️ '} 工作流状态：{page.locator('#statusText').inner_text()}")
        page.wait_for_timeout(3000)

        # 滚动到对话底部，让最终报告完整入镜
        page.evaluate("() => { const m = document.getElementById('messages'); "
                      "if (m) m.scrollTop = m.scrollHeight; window.scrollTo(0, document.body.scrollHeight); }")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT_DIR / "agent_result.png"))
        print("  ✅ agent_result.png")

        # ---- Agent 记忆面板 ----
        page.locator('.sb-tab[data-tab="mem"]').click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT_DIR / "agent_memory.png"))
        print("  ✅ agent_memory.png")

        browser.close()
    print(f"\n🎉 截图已保存到 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
