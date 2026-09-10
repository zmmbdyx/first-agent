# -*- coding: utf-8 -*-
"""提交/历史脱敏审计：扫描密钥、私有端点与个人敏感信息。

用法：
    python scripts/audit_secrets.py              # 扫描全部历史（默认，CI 用）
    python scripts/audit_secrets.py --head       # 只扫工作树（快，提交前用）
    python scripts/audit_secrets.py --staged     # 只扫暂存内容（pre-commit 用）

退出码：0 = 通过；1 = 发现疑似泄露（CI 会因此失败）。

为什么默认扫全部历史：
    把敏感值从最新代码里删掉，并不能让它从 git 历史里消失——clone 之后
    `git log -p` 依然能翻出旧提交里的明文。另一个仓库就曾把私有推理端点
    写进早期提交，因此审计必须覆盖历史。

与旧版 grep 扫描的区别：
    旧 CI 只用 `grep "sk-[a-zA-Z0-9]{20,}"` 扫工作树，既漏历史、又会误报
    ——本仓库 tests/test_smoke.py 里就有用于验证"密钥脱敏"功能的假密钥字面量，
    导致 CI 恒失败。本脚本带白名单与更全的特征集，并覆盖全历史。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict

# Windows 控制台默认 GBK，直接 print emoji/特殊符号会抛 UnicodeEncodeError。
# 统一切到 UTF-8 并对无法编码的字符降级替换，保证脚本在任何终端都能跑完。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 老版本解释器无 reconfigure
    pass

# 高置信度特征。宁可多报，由 ALLOW 白名单收敛误报。
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("API Key (sk-)", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.I)),
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("私钥文件内容", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("URL 内嵌密码", re.compile(r"://[^/\s:@]{3,}:[^/\s:@]{3,}@")),
    ("私有推理端点", re.compile(r"\\bws-[a-z0-9]{10,}\\.[a-z0-9.\-]+\\.[a-z]{2,}\\b")),
    ("内网 IP", re.compile(
        r"(?<![\d.])(?:10\.\d{1,3}|192\.168\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3})"
        r"\.\d{1,3}(?![\d.])")),
    ("内网域名", re.compile(
        r"(?<![.\w])(?!threading|locale|gettext)[a-z0-9\-]{3,}\.(?:internal|corp|intranet)(?![.\w])",
        re.I)),
    ("手机号", re.compile(r"(?<![\d.])1[3-9]\d{9}(?![\d.])")),
    ("身份证号", re.compile(r"(?<![\d.])\d{17}[\dXx](?![\d.])")),
    ("邮箱", re.compile(r"(?<![.\w])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![.\w])")),
]

# 占位符 / 测试夹具 / 文档示例值：命中这些不算泄露
ALLOW = re.compile(
    r"(你的密钥|你的key|sk-xxx|sk-yyy|sk-your|placeholder|example\.com|"
    # 验证"密钥脱敏"功能的假密钥（tests/test_smoke.py）
    r"sk-abcdefghijklmnop1234|"
    # 验证"敏感信息拦截"的假身份证号（tests/test_smoke.py、eval_suite.json）
    r"110101199003077777|"
    # 示例简历里的假联系方式（data/generate_data.py、data/resumes/*）
    r"liming@email\.com|138\*{4}5678|@email\.com)",
    re.I,
)

# 二进制/大文件不扫描
SKIP_EXT = re.compile(
    r"\.(png|jpe?g|gif|webp|bmp|ico|pdf|docx?|xlsx?|pptx?|zip|gz|7z|rar|pkl|bin|exe|dll|"
    r"woff2?|ttf|otf|mp4|mp3|onnx|pt|safetensors|db|sqlite3?)$", re.I)


def _git(repo: str, args: list[str]) -> str:
    return subprocess.run(["git", "-C", repo] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def _scan_text(text: str, label: str, findings: dict) -> None:
    for line in text.splitlines():
        if SKIP_EXT.search(line[:200]):
            continue
        for name, pat in PATTERNS:
            for m in pat.finditer(line):
                val = m.group(0)
                if ALLOW.search(val) or ALLOW.search(line):
                    continue
                findings[name].append((label, val))


def audit_history(repo: str) -> dict:
    findings: dict = defaultdict(list)
    log = _git(repo, ["log", "--all", "--no-color", "--format=@@@%h|%s", "-p"])
    commit, path = "?", "?"
    for line in log.splitlines():
        if line.startswith("@@@"):
            commit = line[3:].strip()
        elif line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("+"):
            _scan_text(line[1:], f"{commit}  {path}", findings)
    return findings


def audit_worktree(repo: str) -> dict:
    findings: dict = defaultdict(list)
    for f in [x for x in _git(repo, ["ls-files"]).splitlines() if x.strip()]:
        if SKIP_EXT.search(f):
            continue
        try:
            with open(f"{repo}/{f}", "r", encoding="utf-8", errors="ignore") as fh:
                _scan_text(fh.read(), f"工作树  {f}", findings)
        except OSError:
            continue
    return findings


def audit_staged(repo: str) -> dict:
    findings: dict = defaultdict(list)
    path = "?"
    for line in _git(repo, ["diff", "--cached", "--no-color", "-U0"]).splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("+") and not line.startswith("+++"):
            _scan_text(line[1:], f"暂存区  {path}", findings)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", help="仓库路径（默认当前目录）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--head", action="store_true", help="只扫工作树")
    mode.add_argument("--staged", action="store_true", help="只扫暂存区")
    args = ap.parse_args()

    if args.staged:
        findings, scope = audit_staged(args.repo), "暂存区"
    elif args.head:
        findings, scope = audit_worktree(args.repo), "工作树"
    else:
        findings, scope = audit_history(args.repo), "全部历史"

    print(f"[审计] 范围={scope}  仓库={args.repo}")
    if not findings:
        print("[通过] 未发现密钥 / 私有端点 / 个人敏感信息")
        return 0

    total = sum(len(v) for v in findings.values())
    print(f"[发现] 共 {total} 处疑似泄露：\n")
    for name, items in sorted(findings.items()):
        uniq = sorted(set(items))
        print(f"■ {name}（{len(uniq)} 处）")
        for label, val in uniq[:5]:
            masked = val if len(val) <= 16 else val[:8] + "…" + val[-4:]
            print(f"    {label}\n        {masked}")
        if len(uniq) > 5:
            print(f"    …… 另有 {len(uniq) - 5} 处")
        print()
    print("处理建议：把真实值移入 .env（已 gitignore），代码里只保留占位符；")
    print("若已进入历史，需用 git filter-repo 重写历史后再强制推送。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
