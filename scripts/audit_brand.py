# -*- coding: utf-8 -*-
"""原创性审计：扫描仓库中的第三方品牌字样（命名 / Logo / 文案 / 配置）。

用法：
    python scripts/audit_brand.py                 # 扫描工作树（默认，提交前用）
    python scripts/audit_brand.py --history       # 连同 git 全历史一起扫（CI 用）
    python scripts/audit_brand.py --repo .        # 指定仓库根

退出码：0 = 通过；1 = 发现品牌字样。

判定策略（重要，避免"误杀"与技术栈说明自相矛盾）：
    1. 硬性禁止：AI 模型厂商、Agent 产品、检索服务等**品牌名**出现在
       界面文案、项目命名、配置取值、注释与文档叙述中；
    2. 允许的上下文（可审计的白名单，命中即跳过）：
       - 依赖清单里的**包名**（requirements*.txt / package.json / lock 文件）——
         包名是技术依赖标识，不是产品命名；
       - 代码中的 import 语句与安装命令（`import openai`、`pip install openai`）；
       - "OpenAI 兼容协议 / openai-compatible" 这类**协议描述**——它是行业通用的
         接口兼容性说法，本项目所有端点取值均为中性占位符（见 backend/.env.example）。

CI 使用 --history：把品牌字样从最新代码里删掉并不会让它从历史提交中消失，
而仓库一旦公开，`git log -p` 就能翻出来，因此历史同样要卡。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 老解释器没有 reconfigure
    pass

# ---- 品牌特征（大小写不敏感，按词边界匹配以避免误伤普通单词） ----
#
# 为什么用「片段拼接」而不是直接写字面量：
#   1) 审计脚本自身若含完整品牌字面量，就不得不把自己加进跳过名单——等于审计有盲区；
#   2) 历史脱敏（git filter-repo --replace-text）会改写所有提交里的 blob，
#      脚本里的检测模式同样会被改掉，导致审计能力被"脱敏"破坏。
#   拼接后源码中不存在完整品牌词，规则表依然可读、可审计。
def _t(*parts: str) -> str:
    """把片段拼成完整词（源码里不出现完整品牌字面量）。"""
    return "".join(parts)


_VENDORS = [
    _t("deep", "seek"), _t("深度", "求索"), _t("zhi", "pu"), _t("智", "谱"),
    _t("big", "model"), _t("moon", "shot"), _t("ki", "mi"), _t("月之", "暗面"),
    _t("q", "wen"), _t("通", "义", "千问"), _t("通", "义"), _t("ali", "yun"),
    _t("ali", "yun", "cs"), _t("阿里", "云"), _t("百", "炼"), _t("anthro", "pic"),
    _t("clau", "de"), _t("gem", "ini"), _t("mist", "ral"), _t("lla", "ma"),
    _t("x", "ai"), _t("gr", "ok"),
]
_PRODUCTS = [
    _t("chat", "gpt"), _t("gpt-", "[3-9]"), _t("gl", "m-", r"\d"), _t("er", "nie"),
    _t("文心", "一言"), _t("hun", "yuan"), _t("混", "元"), _t("dou", "bao"), _t("豆", "包"),
]
_AGENTS = [
    _t("har", "ness"), _t("co", "pilot"), _t("cur", "sor"), _t("wind", "surf"),
    _t("de", "vin"), _t("auto", "gpt"), _t("baby", "agi"), _t("open", "hands"),
    _t("clau", "de", "-code"),
]
_SEARCH = [
    _t("ta", "vily"), _t("duck", "duckgo"), _t("per", "plexity"), _t("serp", "api"),
    r"brave\s*search",
]


def _alternation(words: list[str]) -> re.Pattern:
    """词边界断言 + 片段拼接的候选词 → 一个匹配模式。"""
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(words) + r")(?![a-z0-9])", re.I)


BRANDS: list[tuple[str, re.Pattern]] = [
    ("模型厂商", _alternation(_VENDORS)),
    ("模型产品", _alternation(_PRODUCTS)),
    ("Agent 产品", _alternation(_AGENTS)),
    ("检索服务", _alternation(_SEARCH)),
]

# ---- 允许的上下文：命中则不算违规（白名单必须显式、可审计） ----
# CSS 的光标属性与某个 Agent 产品同名，这里同样用片段拼接，避免规则表自伤
_CURSOR = _t("cur", "sor")
ALLOW_LINE = re.compile(
    r"(?:"
    r"openai[\s\-_]?compatible"          # 协议取值：LLM_PROVIDER=openai-compatible
    r"|openai\s*兼容"                     # 协议描述：OpenAI 兼容协议
    r"|兼容协议|通用兼容"                  # 中性化的协议叙述
    r"|^\s*(?:from|import)\s+\w+"        # import 语句（包名）
    r"|pip\s+install|npm\s+(?:i|install)"  # 安装命令（包名）
    r"|" + _CURSOR + r"\s*:"             # CSS 光标属性（与某 Agent 产品同名，属误报）
    r"|" + _CURSOR + r"-(?:pointer|default|text|not-allowed|move|grab|wait|help)"
    r"|^\s*[\"']?(?:openai|ddgs|langchain[\w-]*|chromadb|redis|sqlalchemy)[\"']?\s*[><=~]"  # 依赖清单行
    r")", re.I)

# ---- 目录 / 文件排除 ----
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
             ".pytest_cache", ".mypy_cache", "screenshots", ".idea", ".vscode",
             "data/sessions", "data/reports", "data/uploads", "data/cache",
             "data/vectors", "workspaces"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2",
               ".ttf", ".zip", ".gz", ".onnx", ".pyc", ".so", ".dll", ".lock", ".db"}
SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "audit_brand.py",
              # 本地 .env 已被 .gitignore 排除、不会进入仓库，且开发者有权在其中
              # 填写自己实际使用的端点；审计只针对"会被提交的内容"。
              ".env",
              # SQLite 运行时库（契约的 SQLITE_FALLBACK_URL 目标，已被 .gitignore 的
              # *.db 排除）：内容是 data/sessions 等旧对话的**投影**，本身不是文案来源，
              # 且 SQLite 加密页会让随机字节偶然命中品牌词，扫描它只会制造假阳性。
              "pathforge.db", "pathforge.db-journal", "pathforge.db-wal", "pathforge.db-shm"}

HISTORY_SUFFIX = (".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".yml", ".yaml",
                  ".txt", ".css", ".html", ".example", ".toml", ".cfg", ".ini")


def scan_text(text: str, label: str) -> list[tuple[str, int, str, str]]:
    """返回 [(文件, 行号, 品牌类别, 命中文本)]。"""
    hits: list[tuple[str, int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_LINE.search(line):
            continue
        for category, pattern in BRANDS:
            m = pattern.search(line)
            if m:
                hits.append((label, lineno, category, m.group(0)))
                break
    return hits


def iter_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        parts = set(rel.split("/"))
        if parts & SKIP_DIRS or any(rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if p.name in SKIP_FILES or p.suffix.lower() in SKIP_SUFFIX:
            continue
        yield p, rel


def scan_tree(root: Path) -> list[tuple[str, int, str, str]]:
    hits = []
    for p, rel in iter_files(root):
        try:
            text = p.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        hits.extend(scan_text(text, rel))
    return hits


def scan_history(root: Path) -> list[tuple[str, int, str, str]]:
    """扫描 git 历史中每个提交的每行新增内容（--diff-filter 全量 + -U0 减少噪音）。"""
    try:
        log = subprocess.run(["git", "-C", str(root), "log", "--all", "--pretty=format:%H"],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[audit_brand] 跳过历史扫描（git 不可用：{e}）")
        return []
    if log.returncode != 0:
        print("[audit_brand] 跳过历史扫描（不是 git 仓库或无可读历史）")
        return []
    hits: list[tuple[str, int, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for sha in [s for s in log.stdout.split() if s]:
        proc = subprocess.run(
            ["git", "-C", str(root), "show", "--format=", "--unified=0", "--no-color", sha],
            capture_output=True, text=True, timeout=180, errors="ignore")
        if proc.returncode != 0:
            continue
        current = ""
        for raw in proc.stdout.splitlines():
            if raw.startswith("+++ b/"):
                current = raw[6:]
                continue
            if not raw.startswith("+") or raw.startswith("+++"):
                continue
            if current and not current.endswith(HISTORY_SUFFIX):
                continue
            # 与工作树扫描保持同一套跳过规则：审计脚本自身的规则表不是品牌引用
            if current and Path(current).name in SKIP_FILES:
                continue
            line = raw[1:]
            for _, lineno, category, matched in scan_text(line, current or "<history>"):
                key = (category, matched.lower())
                if key in seen:
                    continue
                seen.add(key)
                hits.append((f"{current or '<history>'} @{sha[:8]}", 0, category, matched))
    return hits


def selftest() -> int:
    """自检：用规则表自身拼出的词逐一验证「每条规则确实能命中」。

    为什么需要：规则表是"片段拼接"的，而历史脱敏替换曾**跨界命中片段**
    （把 `_t("通义", "千问")` 中的片段改掉），整条模式因此失效、审计却仍然"通过"。
    本自检不读仓库、不依赖外部文件，纯靠规则表自证，适合作为常驻护栏。
    """
    problems: list[str] = []
    for (category, pattern), words in zip(BRANDS, [_VENDORS, _PRODUCTS, _AGENTS, _SEARCH]):
        for w in words:
            if re.search(r"[\\\[\]()*+?{}.|^$]", w):   # 含正则元字符的片段无法还原为字面样本
                continue
            if not pattern.search(w):
                problems.append(f"{category}: {w}")
    if problems:
        print(f"❌ 规则自检失败 {len(problems)} 条（模式被削弱或改写）：")
        for p in problems:
            print("   -", p)
        return 1
    total = sum(len(w) for w in [_VENDORS, _PRODUCTS, _AGENTS, _SEARCH])
    print(f"✅ 规则自检通过：{total} 个品牌词全部能被对应规则命中")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="第三方品牌字样审计")
    ap.add_argument("--repo", default=".", help="仓库根目录")
    ap.add_argument("--history", action="store_true", help="同时扫描 git 全历史")
    ap.add_argument("--selftest", action="store_true", help="只做规则表自检（不扫描仓库）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    root = Path(args.repo).resolve()
    hits = scan_tree(root)
    if args.history:
        hits += scan_history(root)

    if not hits:
        print("✅ 品牌审计通过：未发现第三方品牌字样")
        return 0

    print(f"❌ 发现 {len(hits)} 处第三方品牌字样：\n")
    for path, lineno, category, matched in hits:
        loc = f"{path}:{lineno}" if lineno else path
        print(f"  [{category}] {loc}  →  {matched}")
    print("\n处理方式：界面/命名/文案/配置取值中的品牌字样必须改为中性表述；"
          "依赖包名与兼容协议描述已在白名单内（见本脚本 ALLOW_LINE）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
