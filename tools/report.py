"""报告落盘：把最终分析结果写成 markdown 文件。含 PII 脱敏（手机号/邮箱）。"""
import re
import time
from pathlib import Path

from config import ROOT, to_root_relative
from tools.base import Tool, ToolError

_PII_RE = re.compile(r"(1[3-9]\d)[\s:-]?(\d{4})[\s:-]?(\d{4})"
                     r"|([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+)")


def mask_pii(text: str) -> str:
    def phone(m):
        return f"{m.group(1)}****{m.group(3)}"

    def mail(m):
        return f"{m.group(1)[0]}***@{m.group(2)}"

    return _PII_RE.sub(lambda m: phone(m) if m.group(1) else mail(m), text)


class WriteReportTool(Tool):
    name = "write_report"
    description = "将 markdown 内容保存为报告文件（自动脱敏手机号/邮箱），返回可访问路径。"
    args_desc = {"filename": "文件名（不含扩展名，自动加 .md）", "content": "markdown 正文"}
    cost = "低"
    avg_seconds = 0.1

    def run(self, filename: str = "", content: str = "") -> dict:
        if not content or len(content.strip()) < 20:
            raise ToolError("报告内容为空或过短")
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", filename or f"report_{time.strftime('%Y%m%d_%H%M%S')}")
        # 改动：报告原先写相对 CWD 的 "data/reports/"，服务从其他工作目录启动会落到别处
        # 且 /files 链接 404；改为以项目根定位，返回值仍为相对项目根的路径。
        path = Path(ROOT) / "data" / "reports" / f"{safe[:60]}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mask_pii(content), encoding="utf-8")
        rel = to_root_relative(path)
        return {"path": rel,
                "url": f"/files/{rel}",
                "chars": len(content)}
