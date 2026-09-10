# -*- coding: utf-8 -*-
"""用 Playwright 操作**真实运行中的页面**并截图（README 图片来源，非设计稿）。

前置：
    pip install playwright && playwright install chromium
    python -m uvicorn main:app --app-dir backend --port 8000        # 另开一个终端

运行：
    python scripts/capture_screenshots.py
    python scripts/capture_screenshots.py --url http://127.0.0.1:5173   # 打 Vite 开发服务器

产出（screenshots/）：
    01-empty-state.png     空状态与输入区
    02-running.png         执行中：轨迹读条 + 实时指标
    03-result-panel.png    交付结果 + 右侧面板
    04-light.png           浅色模式

同时把浏览器控制台错误与页面异常打印出来——截图脚本顺带充当一次实机冒烟。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots"

# Windows 控制台默认 GBK，打印 ✅/⚠️ 这类符号会抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 老版本解释器没有 reconfigure
    pass

TASK = ("帮我分析 data/jds/jd03_数据分析师.txt，"
        "并匹配简历 data/resumes/简历_李明_数据分析师.txt，给出面试题")

CONSOLE_ERRORS: list[str] = []
PAGE_ERRORS: list[str] = []
NOT_FOUND: list[str] = []


def main() -> int:
    ap = argparse.ArgumentParser(description="页面截图")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="站点地址")
    ap.add_argument("--task", default=TASK, help="要发送的任务文本")
    ap.add_argument("--timeout", type=float, default=240.0, help="等待执行完成的秒数")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    shots: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900},
                                device_scale_factor=1.5)
        page.on("console", lambda m: CONSOLE_ERRORS.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)))
        # 记录 404 的具体 URL：控制台只报 "Failed to load resource"，不足以定位
        page.on("response", lambda r: NOT_FOUND.append(f"{r.status} {r.url}")
                if r.status >= 400 else None)

        print(f"[shot] 打开 {args.url}")
        page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)

        # 新建会话，保证从干净的空状态开始（否则会恢复上一次会话的历史）
        try:
            page.click('[aria-label="新建会话"]', timeout=8_000)
            page.wait_for_timeout(700)
        except Exception as e:  # noqa: BLE001
            print(f"[shot] 新建会话跳过：{type(e).__name__}")

        page.wait_for_selector("textarea", timeout=20_000)
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / "01-empty-state.png"))
        shots.append("01-empty-state.png")
        print("[shot] 01 空状态完成")

        # 发送任务
        page.fill("textarea", args.task)
        page.wait_for_timeout(200)
        page.click('[aria-label="发送"]')
        print("[shot] 已发送任务，等待轨迹出现…")

        # 等轨迹读条渲染出来（执行中的关键画面）
        try:
            page.wait_for_selector('section[aria-label="执行轨迹"]', timeout=60_000)
            page.wait_for_timeout(3_500)   # 留一点时间让读条与实时指标刷新
            page.screenshot(path=str(OUT / "02-running.png"))
            shots.append("02-running.png")
            print("[shot] 02 执行中完成")
        except Exception as e:  # noqa: BLE001
            print(f"[shot] 轨迹未出现：{type(e).__name__}: {e}")
            page.screenshot(path=str(OUT / "02-running.png"))
            shots.append("02-running.png")

        # 等执行结束：发送按钮重新可用（执行中它是停止按钮）
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                if page.query_selector('[aria-label="发送"]') and not page.query_selector('[aria-label="停止执行"]'):
                    break
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(1_000)
        page.wait_for_timeout(1_500)

        # 结果图：切到 Token 面板，展示统计
        try:
            page.click('text=Token', timeout=5_000)
            page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            pass
        page.screenshot(path=str(OUT / "03-result-panel.png"))
        shots.append("03-result-panel.png")
        print("[shot] 03 结果与面板完成")

        # 浅色模式
        try:
            page.click('[title*="浅色"]', timeout=5_000)
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / "04-light.png"))
            shots.append("04-light.png")
            print("[shot] 04 浅色模式完成")
        except Exception as e:  # noqa: BLE001
            print(f"[shot] 浅色切换失败：{type(e).__name__}")

        browser.close()

    print("\n产物：")
    for s in shots:
        print("  -", (OUT / s).relative_to(ROOT).as_posix())

    if PAGE_ERRORS:
        print(f"\n❌ 页面异常 {len(PAGE_ERRORS)} 条：")
        for e in PAGE_ERRORS[:10]:
            print("   -", e[:200])
    if CONSOLE_ERRORS:
        print(f"\n⚠️ 控制台错误 {len(CONSOLE_ERRORS)} 条：")
        for e in CONSOLE_ERRORS[:10]:
            print("   -", e[:200])
    if NOT_FOUND:
        print(f"\n⚠️ 失败请求 {len(NOT_FOUND)} 条：")
        for e in NOT_FOUND[:10]:
            print("   -", e[:200])
    if not PAGE_ERRORS and not CONSOLE_ERRORS:
        print("\n✅ 无页面异常、无控制台错误")

    return 1 if PAGE_ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
