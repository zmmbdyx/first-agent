# -*- coding: utf-8 -*-
"""交互层实机校验：用 Playwright 真实点击左侧栏 / 设置弹窗 / 右侧四面板 / 输入区。

与 `capture_screenshots.py` 的分工：
- `capture_screenshots.py` 负责产出 README 截图（走一次完整任务）；
- 本脚本负责**断言交互是否真的接通**——此前这些功能只做过类型与构建层面验证，
  没有实机点击过（文件树展开与预览、Git 勾选暂存、会话行内重命名/置顶/删除、
  设置四个 Tab、@ 引用浮层、参数下拉、侧栏与面板折叠、深浅色切换）。

判定方式：DOM 状态 + **网络请求断言**（点击是否真的发出了对应的 API 调用、返回是否成功）。
退出码 0 = 全部通过。

前置：后端已启动，且已构建前端产物（`cd frontend && npm run build`）。
    python -m uvicorn main:app --app-dir backend --port 8000
运行：
    python scripts/ui_check.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = ROOT / "workspaces" / "default"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

PASSED = 0
FAILED: list[str] = []
CONSOLE_ERRORS: list[str] = []
PAGE_ERRORS: list[str] = []
API_CALLS: list[tuple[str, str, int]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name}  {detail}")


def prepare_workspace() -> None:
    """准备一个带文件、且是 Git 仓库的工作区，用于文件树与 Git 面板的真实交互。"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    (WORKSPACE_DIR / "notes.md").write_text(
        "# 工作区示例\n\n用于校验文件树预览与 Git 变更面板的示例文件。\n", encoding="utf-8")
    (WORKSPACE_DIR / "sample.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n", encoding="utf-8")
    (WORKSPACE_DIR / "jds").mkdir(exist_ok=True)
    (WORKSPACE_DIR / "jds" / "demo.txt").write_text("岗位职责：数据分析\n任职要求：SQL\n",
                                                    encoding="utf-8")

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(WORKSPACE_DIR), *args],
                              capture_output=True, text=True)

    if not (WORKSPACE_DIR / ".git").exists():
        git("init", "-b", "main")
    git("config", "user.name", "ui-check")
    # 用 example.com 域：既满足 git 对邮箱格式的要求，也落在脱敏审计的白名单内
    git("config", "user.email", "ui-check@example.com")
    git("add", "-A")
    git("commit", "-m", "baseline", "--quiet")
    # 造出一个「未暂存」的修改，供 Git 面板勾选暂存。
    # 每次运行都追加唯一标记：上一轮可能已经把改动暂存掉了，否则本轮就没有变更可勾选。
    marker = f"（校验改动 {uuid.uuid4().hex[:6]}）"
    notes = WORKSPACE_DIR / "notes.md"
    base = ("# 工作区示例\n\n用于校验文件树预览与 Git 变更面板的示例文件。\n")
    notes.write_text(base, encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "baseline", "--quiet")
    notes.write_text(base + f"\n{marker}\n", encoding="utf-8")
    print(f"[ui] 工作区已准备: {WORKSPACE_DIR.relative_to(ROOT).as_posix()}（含 1 处未暂存改动）")


def main() -> int:
    ap = argparse.ArgumentParser(description="交互层实机校验")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--keep", action="store_true", help="保留校验用会话不删除")
    args = ap.parse_args()

    prepare_workspace()

    from playwright.sync_api import sync_playwright

    tag = uuid.uuid4().hex[:6]
    title = f"UI校验-{tag}"
    renamed = f"UI校验改名-{tag}"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: CONSOLE_ERRORS.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)))

        def record(resp):
            if "/api/" in resp.url:
                API_CALLS.append((resp.request.method, resp.url.split("/api/")[-1].split("?")[0],
                                  resp.status))

        page.on("response", record)

        # 用 API 造一条标题唯一的会话，便于在长长的列表里精确定位（避免误点到历史会话）
        api_ctx = page.request
        r = api_ctx.post(f"{args.url}/api/sessions",
                         data=json.dumps({"title": title, "workspace": "default"}),
                         headers={"Content-Type": "application/json"})
        check("准备校验会话", r.ok, f"{r.status}")
        sid = (r.json() or {}).get("id", "")

        page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("textarea", timeout=20_000)
        page.wait_for_timeout(1200)

        # ---------------- 1. 布局折叠 ----------------
        print("== 1. 布局：侧栏 / 右栏 / 主题 ==")
        sidebar = page.locator("aside").first
        width_expanded = sidebar.bounding_box()["width"] if sidebar.count() else 0
        page.click('[aria-label="切换侧边栏"]')
        page.wait_for_timeout(500)
        width_collapsed = sidebar.bounding_box()["width"] if sidebar.count() else 0
        check("侧栏可折叠为 Rail(56px)",
              width_expanded > 200 and 50 <= width_collapsed <= 60,
              f"{width_expanded:.0f}px → {width_collapsed:.0f}px")
        page.click('[aria-label="切换侧边栏"]')
        page.wait_for_timeout(400)

        page.click('[aria-label="切换右侧面板"]')
        page.wait_for_timeout(400)
        panel_off = page.locator('button:has-text("Token")').count()
        page.click('[aria-label="切换右侧面板"]')
        page.wait_for_timeout(500)
        panel_on = page.locator('button:has-text("Token")').count()
        check("右侧面板可折叠/展开", panel_off == 0 and panel_on > 0,
              f"折叠时按钮数={panel_off}，展开时={panel_on}")

        dark_before = page.evaluate("document.documentElement.classList.contains('dark')")
        page.click('[title*="浅色"]')
        page.wait_for_timeout(400)
        light = page.evaluate("document.documentElement.classList.contains('dark')")
        page.click('[title*="深色"]')
        page.wait_for_timeout(400)
        dark_after = page.evaluate("document.documentElement.classList.contains('dark')")
        check("深/浅色切换生效", dark_before and not light and dark_after,
              f"dark={dark_before} → light={not light} → dark={dark_after}")

        # ---------------- 2. 会话行内操作 ----------------
        print("== 2. 会话：置顶 / 重命名 / 删除 ==")
        row_text = page.get_by_text(title, exact=True).first
        check("会话列表出现新会话", row_text.count() > 0, title)
        row = row_text.locator('xpath=ancestor::div[contains(@class,"group")][1]')

        row.hover()
        page.wait_for_timeout(300)
        with page.expect_response(lambda r: f"/api/sessions/{sid}/title" in r.url) as info:
            row.locator('[aria-label="重命名"]').click()
            page.wait_for_timeout(250)
            # 进入编辑态后标题文本会被输入框替换，原先按文本定位的行会失效；
            # 同一时刻只可能有一行处于编辑态，因此直接按 aria-label 定位输入框
            box = page.locator('[aria-label="重命名会话"]')
            box.fill(renamed)
            box.press("Enter")
        check("行内重命名发出 PUT 并成功", info.value.ok, f"{info.value.status}")
        page.wait_for_timeout(900)
        check("重命名结果渲染到列表",
              page.get_by_text(renamed, exact=True).count() > 0, renamed)

        row = page.get_by_text(renamed, exact=True).first.locator(
            'xpath=ancestor::div[contains(@class,"group")][1]')
        row.hover()
        page.wait_for_timeout(300)
        with page.expect_response(lambda r: f"/api/sessions/{sid}/pin" in r.url) as info:
            row.locator('[aria-label="置顶"]').click()
        check("置顶发出 PUT 并成功", info.value.ok, f"{info.value.status}")
        page.wait_for_timeout(900)

        # ---------------- 3. 设置弹窗四 Tab ----------------
        print("== 3. 设置弹窗：通用 / 模型 / 插件 / 预设 ==")
        page.click('aside [aria-label="设置"]')
        page.wait_for_timeout(700)
        modal = page.locator('[role="dialog"]')
        check("设置弹窗打开", modal.count() > 0)
        for tab, marker in (("通用设置", "主题"), ("模型配置", "模型"),
                            ("插件管理", "工具"), ("Agent 预设", "预设")):
            page.get_by_role("button", name=tab).first.click()
            page.wait_for_timeout(500)
            body = modal.inner_text()
            check(f"Tab「{tab}」渲染内容", len(body) > 40 and marker in body, f"{len(body)} 字")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        check("Esc 关闭设置弹窗", modal.count() == 0)

        # ---------------- 4. 右侧四面板 ----------------
        print("== 4. 右侧面板：文件 / 工具 / Token / Git ==")
        page.get_by_role("button", name="文件").first.click()
        page.wait_for_timeout(1200)
        check("文件树加载出工作区条目", page.get_by_text("notes.md").count() > 0, "notes.md")

        # 展开子目录并预览 Markdown
        jds = page.get_by_text("jds", exact=True).first
        if jds.count():
            jds.click()
            page.wait_for_timeout(700)
            check("目录可展开", page.get_by_text("demo.txt").count() > 0, "jds/demo.txt")
        with page.expect_response(lambda r: "/api/files/content" in r.url) as info:
            page.get_by_text("notes.md").first.click()
        check("点击文件发出预览请求", info.value.ok, f"{info.value.status}")
        page.wait_for_timeout(900)
        check("Markdown 预览渲染", page.get_by_text("用于校验文件树预览").count() > 0)

        page.get_by_role("button", name="工具").first.click()
        page.wait_for_timeout(500)
        check("工具面板渲染", page.locator("text=工具调用").count() > 0
              or page.locator("text=还没有工具调用").count() > 0)

        page.get_by_role("button", name="Token").first.click()
        page.wait_for_timeout(500)
        check("Token 面板渲染", page.get_by_text("总 Token").count() > 0)

        page.get_by_role("button", name="Git").first.click()
        page.wait_for_timeout(1200)
        git_text = page.locator('[aria-label="刷新变更"]').first
        check("Git 面板渲染", git_text.count() > 0)
        if page.get_by_text("未暂存").count():
            # 勾选「未暂存」分组并暂存：断言真的发出了 /api/git/stage
            page.locator('[aria-label="全选未暂存"]').check()
            page.wait_for_timeout(300)
            with page.expect_response(lambda r: "/api/git/stage" in r.url) as info:
                # 必须 exact：否则会命中相邻的「取消暂存」按钮（禁用态，点击会一直等待）
                page.get_by_role("button", name="暂存", exact=True).first.click()
            check("Git 暂存发出请求并成功", info.value.ok, f"{info.value.status}")
            page.wait_for_timeout(1200)
            check("暂存后进入「已暂存」分组",
                  page.get_by_text("已暂存").count() > 0 and page.get_by_text("未暂存").count() == 0)
        else:
            check("Git 面板显示变更", False, "未看到「未暂存」分组")

        # ---------------- 5. 输入区 ----------------
        print("== 5. 输入区：@ 引用 / 参数下拉 ==")
        textarea = page.locator("textarea").first
        textarea.click()
        textarea.type("@", delay=120)
        page.wait_for_timeout(800)
        check("@ 引用浮层弹出", page.locator("text=notes.md").count() > 0, "候选含工作区文件")
        textarea.press("Enter")
        page.wait_for_timeout(400)
        value = textarea.input_value()
        check("@ 引用插入路径", value.startswith("@"), repr(value[:40]))

        # 参数下拉：按 store 中的值是否随之变化来判定（避免依赖下拉的具体 DOM 结构）
        before = page.evaluate("localStorage.getItem('pf.preset')")
        selects = page.locator("select")
        changed = False
        for i in range(selects.count()):
            opts = selects.nth(i).locator("option")
            texts = [opts.nth(j).inner_text() for j in range(min(opts.count(), 8))]
            if any("PTC" in t for t in texts):
                selects.nth(i).select_option(label=[t for t in texts if "PTC" in t][0])
                changed = True
                break
        page.wait_for_timeout(400)
        after = page.evaluate("localStorage.getItem('pf.preset')")
        check("预设下拉可切换并持久化", changed and after != before, f"{before} → {after}")

        # ---------------- 6. 收尾 ----------------
        print("== 6. 交互后清理 ==")
        if not args.keep:
            row = page.get_by_text(renamed, exact=True).first.locator(
                'xpath=ancestor::div[contains(@class,"group")][1]')
            row.hover()
            page.wait_for_timeout(300)
            row.locator('[aria-label="删除"]').click()
            page.wait_for_timeout(300)
            with page.expect_response(lambda r: f"/api/sessions/{sid}" in r.url) as info:
                page.locator('[title="确认删除"]').first.click()
            check("删除会话（二次确认）发出 DELETE", info.value.ok, f"{info.value.status}")

        page.screenshot(path=str(ROOT / "screenshots" / "05-ui-check.png"))
        browser.close()

    print("\n" + "=" * 52)
    if PAGE_ERRORS:
        print(f"页面异常 {len(PAGE_ERRORS)} 条：")
        for e in PAGE_ERRORS[:6]:
            print("   -", e[:180])
    if CONSOLE_ERRORS:
        print(f"控制台错误 {len(CONSOLE_ERRORS)} 条：")
        for e in CONSOLE_ERRORS[:6]:
            print("   -", e[:180])
    api_fail = [c for c in API_CALLS if c[2] >= 400]
    if api_fail:
        print(f"非 2xx 的 API 调用 {len(api_fail)} 条：")
        for c in api_fail[:8]:
            print(f"   - {c[0]} /api/{c[1]} → {c[2]}")

    print(f"结果: {PASSED} 通过, {len(FAILED)} 失败")
    for f in FAILED:
        print("   -", f)
    return 1 if (FAILED or PAGE_ERRORS) else 0


if __name__ == "__main__":
    sys.exit(main())
