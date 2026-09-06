"""智能缓存复用：对 jd_analyze / resume_match / pdf_extract / image_ocr 等重结果工具，
按内容哈希落盘缓存（data/cache/）。材料未变化时直接复用上次分析，省时省钱。"""
import hashlib
import json
import time
from pathlib import Path

from config import ROOT

CACHE_DIR = ROOT / "data" / "cache"
MAX_ITEMS = 300


def _digest(obj) -> str:
    if obj is None:
        return b""
    if isinstance(obj, (int, float, bool)):
        obj = str(obj)
    p = Path(str(obj))
    try:
        if p.is_file():  # 路径参数 → 按文件内容哈希（材料变更即失效）
            return p.read_bytes()
    except OSError:
        pass
    return str(obj).encode("utf-8", "ignore")


def content_key(tool: str, args: dict) -> str:
    h = hashlib.sha256()
    h.update(tool.encode())
    for k in sorted(args):
        h.update(k.encode())
        h.update(_digest(args[k]))
    return h.hexdigest()[:24]


def load(tool: str, args: dict):
    """命中返回缓存数据，未命中返回 None。"""
    try:
        f = CACHE_DIR / f"{tool}_{content_key(tool, args)}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def store(tool: str, args: dict, data):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        f = CACHE_DIR / f"{tool}_{content_key(tool, args)}.json"
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        files = sorted(CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:-MAX_ITEMS]:  # 容量护栏
            old.unlink(missing_ok=True)
    except Exception:
        pass


def stats() -> dict:
    if not CACHE_DIR.exists():
        return {"items": 0}
    files = list(CACHE_DIR.glob("*.json"))
    return {"items": len(files),
            "oldest": time.strftime("%Y-%m-%d", time.localtime(files[0].stat().st_mtime)) if files else None}
