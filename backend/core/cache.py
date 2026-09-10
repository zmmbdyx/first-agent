"""智能缓存复用：对 jd_analyze / resume_match / pdf_extract / image_ocr 等重结果工具，
按内容哈希落盘缓存（data/cache/）。材料未变化时直接复用上次分析，省时省钱。
改动：缓存文件同样经 core.secure_store 加密落盘——pdf_extract/image_ocr 的缓存里
是简历、JD 的完整原文，原先是明文 json，与本项目「落盘即加密」的承诺不一致。"""
import hashlib
import json
import time
from pathlib import Path

from config import ROOT

CACHE_DIR = ROOT / "data" / "cache"
MAX_ITEMS = 300


def _digest(obj) -> bytes:
    """把参数值转成参与哈希的字节串（路径参数按文件内容哈希）。
    改动：原函数返回类型标注为 str，实际返回 bytes（或 str），标注与实现不符。"""
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
        from core import secure_store
        f = CACHE_DIR / f"{tool}_{content_key(tool, args)}.json"
        if f.exists():
            # 改动：经 secure_store 读取，兼容历史明文缓存（无 ENC1: 前缀时原样返回）
            return json.loads(secure_store.read_bytes(f).decode("utf-8"))
    except Exception:
        pass
    return None


def store(tool: str, args: dict, data):
    try:
        from core import secure_store
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        f = CACHE_DIR / f"{tool}_{content_key(tool, args)}.json"
        # 改动：加密落盘（缓存内容含简历/JD 原文，属个人敏感信息）
        secure_store.write_bytes(f, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        files = sorted(CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:-MAX_ITEMS]:  # 容量护栏
            old.unlink(missing_ok=True)
    except Exception:
        pass


def stats() -> dict:
    if not CACHE_DIR.exists():
        return {"items": 0}
    files = list(CACHE_DIR.glob("*.json"))
    # 改动：原实现取 files[0] 当作最旧条目，但 glob 返回顺序不保证按时间排序，
    # 「oldest」显示的可能是任意文件；改为显式取 mtime 最小值。
    return {"items": len(files),
            "oldest": time.strftime("%Y-%m-%d", time.localtime(
                min(p.stat().st_mtime for p in files))) if files else None}
