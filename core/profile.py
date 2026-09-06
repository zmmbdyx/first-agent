"""跨会话长期记忆：全局用户画像（加密存储 data/profile.json）。
- 会话结束时把持久化字段（姓名/岗位/城市/薪资/学历/经验/方式/偏好）合并进全局画像；
- 新会话启动时注入作为基线（会话内新信息可覆盖）；
- 「删除我的所有记忆」同步清除。"""
from pathlib import Path

from config import ROOT
from core import secure_store

PROFILE_FILE = ROOT / "data" / "profile.json"

# 允许进入长期记忆的字段（材料类如 jd_text/jd_paths 属会话级，不入库）
PERSIST_KEYS = ("name", "target_role", "city", "salary_range",
                "education", "experience_years", "work_mode", "preferences")


def _load() -> dict:
    p = Path(PROFILE_FILE)
    if not p.exists():
        return {"facts": {}, "updated_at": {}}
    try:
        import json
        return json.loads(secure_store.read_bytes(p).decode("utf-8"))
    except Exception:
        return {"facts": {}, "updated_at": {}}


def load() -> dict:
    """返回全局画像 facts（只含白名单字段）。"""
    import json
    try:
        data = _load()
        return {k: v for k, v in (data.get("facts") or {}).items()
                if k in PERSIST_KEYS and v}
    except Exception:
        return {}


def merge_from(facts: dict):
    """把会话事实合并进全局画像（仅白名单字段、非空值覆盖）。"""
    import json
    profile = _load()
    now = __import__("time").time()
    changed = False
    for k in PERSIST_KEYS:
        v = facts.get(k)
        if v:
            if profile.get("facts", {}).get(k) != v:
                changed = True
            profile.setdefault("facts", {})[k] = v
            profile.setdefault("updated_at", {})[k] = now
    if changed or profile.get("facts"):
        try:
            secure_store.write_bytes(PROFILE_FILE, json.dumps(profile, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass
    return profile.get("facts", {})


def clear():
    try:
        Path(PROFILE_FILE).unlink(missing_ok=True)
    except Exception:
        pass
