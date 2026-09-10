"""检查点保存器与 `Session` 快照存储。

设计权衡
--------
1. 优先使用编排框架自带的内存检查点保存器（同进程、零依赖，满足"中断/恢复/回滚"）。
   但**框架版本差异会导致导入失败**，因此本模块自带一份接口等价的最小实现
   （`_MinimalMemorySaver`）：同样按 `thread_id / checkpoint_ns / checkpoint_id`
   分层存储，写入前用框架的序列化器转成字节，避免原地引用被后续节点改写。
2. `SnapshotStore` 是**业务态**的检查点：框架检查点存的是图状态，而回滚需要把
   `Session`（消息/任务/产物）恢复到某个 run 的起点。两者按同一个 `checkpoint_id`
   关联，落盘 JSON（沿用 `core/secure_store`，与其它会话文件一致地"落盘即加密"）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from config import Settings

logger = logging.getLogger(__name__)

# 每个会话最多保留的快照数量（防止回滚目录无限增长）
MAX_SNAPSHOTS_PER_SESSION = 20


# ============================================================================
# 框架检查点保存器
# ============================================================================
def build_checkpointer(cfg: Settings | None = None, snapshot_dir: str | Path | None = None,
                       **kwargs: Any) -> Any:
    """返回一个 LangGraph 检查点保存器。

    优先 `langgraph.checkpoint.memory.InMemorySaver`；导入失败时返回本模块自带的
    最小等价实现。`snapshot_dir` 仅供调用方记录快照落盘位置（框架保存器本身在内存中）。
    """
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver(**kwargs)
    except Exception as e:  # 导入/构造失败：绝不因此让整个 run 起不来
        logger.info("内置内存检查点保存器不可用（%s），使用最小实现", e)
        return _MinimalMemorySaver(**kwargs)


def has_framework_checkpointer() -> bool:
    """探测框架保存器是否可用（供 `/api/health` 与测试断言）。"""
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        return isinstance(InMemorySaver, type)
    except Exception:
        return False


def _build_minimal_base():
    """尝试继承框架基类以保持接口兼容；失败则退化为普通 object。"""
    try:
        from langgraph.checkpoint.base import BaseCheckpointSaver
        return BaseCheckpointSaver
    except Exception:  # pragma: no cover - 仅在框架缺失时触发
        return object


_MinimalBase = _build_minimal_base()


class _MinimalMemorySaver(_MinimalBase):  # type: ignore[misc,valid-type]
    """最小可用的内存检查点保存器（与框架内存实现接口一致）。"""

    def __init__(self, serde: Any = None, **_: Any) -> None:
        try:
            super().__init__()
        except Exception:
            pass
        if serde is None:
            try:
                from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
                serde = JsonPlusSerializer()
            except Exception:
                serde = None
        self.serde = serde
        self._lock = threading.RLock()
        # thread_id -> checkpoint_ns -> checkpoint_id -> (checkpoint, metadata, parent_id)
        self.storage: dict[str, dict[str, dict[str, tuple[Any, Any, str | None]]]] = {}
        self.blobs: dict[tuple[str, str, str, Any], tuple[str, bytes]] = {}
        self.writes: dict[tuple[str, str, str], dict[tuple[str, str], tuple[str, str, Any]]] = {}

    # ---------------- 内部 ----------------
    def _dump(self, obj: Any) -> Any:
        if self.serde is None:
            # 极端兜底：连序列化器都不可用时用 JSON 文本，语义等价（够检查点用）
            return ("json", json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))
        return self.serde.dumps_typed(obj)

    def _load(self, data: Any) -> Any:
        if self.serde is None:
            if isinstance(data, tuple) and len(data) == 2 and isinstance(data[1], (bytes, bytearray)):
                return json.loads(bytes(data[1]).decode("utf-8"))
            return data
        return self.serde.loads_typed(data)

    @staticmethod
    def _config_id(config: dict) -> str | None:
        return (config.get("configurable") or {}).get("checkpoint_id")

    def _load_blobs(self, thread_id: str, ns: str, versions: dict) -> dict:
        out: dict[str, Any] = {}
        for key, version in (versions or {}).items():
            blob = self.blobs.get((thread_id, ns, key, version))
            if blob is None:
                continue
            if blob[0] == "empty" and blob[1] == b"":
                continue
            try:
                out[key] = self._load(blob)
            except Exception:  # 单个通道反序列化失败不影响整体恢复
                continue
        return out

    # ---------------- 必须实现的方法 ----------------
    def get_tuple(self, config: dict):
        from langgraph.checkpoint.base import CheckpointTuple
        cfg = config.get("configurable") or {}
        thread_id = str(cfg.get("thread_id") or "")
        ns = str(cfg.get("checkpoint_ns") or "")
        with self._lock:
            bucket = self.storage.get(thread_id, {}).get(ns, {})
            if not bucket:
                return None
            checkpoint_id = cfg.get("checkpoint_id")
            if not checkpoint_id:
                checkpoint_id = max(bucket.keys())
            saved = bucket.get(checkpoint_id)
            if saved is None:
                return None
            packed_checkpoint, packed_meta, parent_id = saved
            checkpoint = self._load(packed_checkpoint)
            checkpoint = {**checkpoint,
                          "channel_values": self._load_blobs(thread_id, ns,
                                                             checkpoint.get("channel_versions") or {})}
            writes = list(self.writes.get((thread_id, ns, checkpoint_id), {}).values())
            pending = []
            for _task_id, channel, value in writes:
                try:
                    pending.append((_task_id, channel, self._load(value)))
                except Exception:
                    continue
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                     "checkpoint_id": checkpoint_id}},
            checkpoint=checkpoint,
            metadata=self._load(packed_meta),
            parent_config=({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                             "checkpoint_id": parent_id}} if parent_id else None),
            pending_writes=pending,
        )

    def list(self, config: dict | None, *, filter: dict | None = None,
             before: dict | None = None, limit: int | None = None) -> Iterator[Any]:
        thread_id = str(((config or {}).get("configurable") or {}).get("thread_id") or "")
        ns = str(((config or {}).get("configurable") or {}).get("checkpoint_ns") or "")
        before_id = self._config_id(before or {})
        with self._lock:
            ids = sorted(self.storage.get(thread_id, {}).get(ns, {}).keys(), reverse=True)
        count = 0
        for checkpoint_id in ids:
            if before_id and checkpoint_id >= before_id:
                continue
            if limit is not None and count >= limit:
                return
            item = self.get_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                                    "checkpoint_id": checkpoint_id}})
            if item is None:
                continue
            if filter:
                meta = item.metadata or {}
                if any(meta.get(k) != v for k, v in filter.items()):
                    continue
            count += 1
            yield item

    def put(self, config: dict, checkpoint: dict, metadata: dict, new_versions: dict) -> dict:
        cfg = config.get("configurable") or {}
        thread_id = str(cfg.get("thread_id") or "")
        ns = str(cfg.get("checkpoint_ns") or "")
        body = dict(checkpoint)
        values = body.pop("channel_values", {}) or {}
        with self._lock:
            for key, version in (new_versions or {}).items():
                self.blobs[(thread_id, ns, key, version)] = (
                    self._dump(values[key]) if key in values else ("empty", b""))
            try:
                from langgraph.checkpoint.base import get_checkpoint_metadata
                meta = get_checkpoint_metadata(config, metadata)
            except Exception:
                meta = metadata
            self.storage.setdefault(thread_id, {}).setdefault(ns, {})[str(checkpoint.get("id"))] = (
                self._dump(body), self._dump(meta), cfg.get("checkpoint_id"))
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                 "checkpoint_id": str(checkpoint.get("id"))}}

    def put_writes(self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        cfg = config.get("configurable") or {}
        thread_id = str(cfg.get("thread_id") or "")
        ns = str(cfg.get("checkpoint_ns") or "")
        checkpoint_id = str(cfg.get("checkpoint_id") or "")
        with self._lock:
            bucket = self.writes.setdefault((thread_id, ns, checkpoint_id), {})
            for idx, (channel, value) in enumerate(writes):
                bucket[(task_id, f"{channel}-{idx}")] = (task_id, channel, self._dump(value))

    def get_next_version(self, current: Any, channel: Any = None) -> str:
        if current is None:
            return "1"
        try:
            return str(int(str(current).split(".")[0]) + 1)
        except (TypeError, ValueError):
            return f"{current}.1"

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            self.storage.pop(thread_id, None)
            for key in [k for k in self.blobs if k[0] == thread_id]:
                self.blobs.pop(key, None)
            for key in [k for k in self.writes if k[0] == thread_id]:
                self.writes.pop(key, None)

    # ---- 异步包装（框架可能调用 a* 变体） ----
    async def aget_tuple(self, config: dict):
        return self.get_tuple(config)

    async def alist(self, config: dict | None, *, filter: dict | None = None,
                    before: dict | None = None, limit: int | None = None):
        return [item for item in self.list(config, filter=filter, before=before, limit=limit)]

    async def aput(self, config: dict, checkpoint: dict, metadata: dict, new_versions: dict) -> dict:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str,
                          task_path: str = "") -> None:
        return self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        return self.delete_thread(thread_id)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<MinimalMemorySaver threads={len(self.storage)}>"


def checkpointer_kind(saver: Any) -> str:
    """返回保存器类型标识：`framework` | `minimal` | `none`（供 health/日志）。"""
    if saver is None:
        return "none"
    if isinstance(saver, _MinimalMemorySaver):
        return "minimal"
    return "framework"


# ============================================================================
# 会话快照（回滚）
# ============================================================================
class SnapshotStore:
    """按 `checkpoint_id` 保存/恢复 `Session` 快照（JSON 落盘，供回滚）。"""

    def __init__(self, snapshot_dir: str | Path | None = None):
        base = Path(snapshot_dir) if snapshot_dir else (Path.cwd() / "data" / "checkpoints")
        self.dir = Path(base)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.info("快照目录不可用（%s），快照功能降级为内存", e)
            self.dir = Path.cwd() / "data" / "checkpoints"
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self._mem: dict[str, dict] = {}
        self._lock = threading.RLock()

    # ---------------- 路径 ----------------
    def _session_dir(self, session_id: str) -> Path:
        safe = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "-_") or "unknown"
        return self.dir / safe

    def _path(self, session_id: str, checkpoint_id: str) -> Path:
        safe = "".join(ch for ch in str(checkpoint_id) if ch.isalnum() or ch in "-_") or "latest"
        return self._session_dir(session_id) / f"{safe}.json"

    # ---------------- 写 ----------------
    def save(self, session_id: str, checkpoint_id: str, session: Any,
             meta: dict | None = None) -> dict:
        """保存快照。`session` 可以是 `Session` 对象或已序列化的 dict。"""
        cid = str(checkpoint_id or "").strip() or f"cp-{int(time.time() * 1000)}"
        snapshot = {
            "checkpoint_id": cid,
            "session_id": str(session_id),
            "ts": time.time(),
            "meta": dict(meta or {}),
            "session": self._serialize(session),
        }
        with self._lock:
            self._mem.setdefault(str(session_id), {})[cid] = snapshot
        try:
            path = self._path(session_id, cid)
            path.parent.mkdir(parents=True, exist_ok=True)
            from core import secure_store
            secure_store.write_bytes(
                path, json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8"))
            self._prune(session_id)
        except Exception as e:  # 落盘失败不影响主流程（内存快照仍可用）
            logger.info("快照落盘失败（%s），仅保留内存快照", e)
        return {"checkpoint_id": cid, "session_id": str(session_id),
                "path": str(self._path(session_id, cid))}

    @staticmethod
    def _serialize(session: Any) -> dict:
        if isinstance(session, dict):
            return session
        if hasattr(session, "to_dict"):
            return session.to_dict()
        return json.loads(json.dumps(session, default=str))

    # ---------------- 读 ----------------
    def load(self, session_id: str, checkpoint_id: str = "") -> dict | None:
        """读取快照 dict；`checkpoint_id` 为空时取该会话最近一条。"""
        sid = str(session_id)
        with self._lock:
            bucket = dict(self._mem.get(sid, {}))
        if not bucket:
            bucket = self._load_from_disk(sid)
        if not bucket:
            return None
        if checkpoint_id:
            return bucket.get(str(checkpoint_id))
        latest = sorted(bucket.values(), key=lambda s: float(s.get("ts") or 0))[-1:]
        return latest[0] if latest else None

    def _load_from_disk(self, session_id: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        try:
            from core import secure_store
            for path in self._session_dir(session_id).glob("*.json"):
                try:
                    data = json.loads(secure_store.read_bytes(path).decode("utf-8"))
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("checkpoint_id"):
                    out[str(data["checkpoint_id"])] = data
        except OSError:
            return {}
        with self._lock:
            self._mem.setdefault(session_id, {}).update(out)
        return out

    def restore_session(self, session_id: str, checkpoint_id: str = "", session_cls: Any = None):
        """恢复 `Session` 对象（默认用 `core.memory.Session`）。找不到快照返回 None。"""
        data = self.load(session_id, checkpoint_id)
        if not data:
            return None
        payload = data.get("session") or {}
        if session_cls is None:
            from core.memory import Session as session_cls  # noqa: N813
        try:
            return session_cls.from_dict(payload)
        except Exception as e:
            logger.warning("快照恢复失败：%s", e)
            return None

    def list(self, session_id: str) -> list[dict]:
        """列出某会话的快照摘要（新的在前），供回滚接口选择。"""
        with self._lock:
            bucket = dict(self._mem.get(str(session_id), {}))
        if not bucket:
            bucket = self._load_from_disk(str(session_id))
        rows = [{"checkpoint_id": s.get("checkpoint_id"), "ts": s.get("ts"),
                 "meta": s.get("meta") or {}} for s in bucket.values()]
        rows.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
        return rows

    def _prune(self, session_id: str) -> None:
        """按 mtime 裁掉过老快照，保证回滚目录不会无限增长。"""
        directory = self._session_dir(session_id)
        try:
            files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for old in files[MAX_SNAPSHOTS_PER_SESSION:]:
            try:
                old.unlink()
            except OSError:
                continue


__all__ = ["build_checkpointer", "has_framework_checkpointer", "checkpointer_kind",
           "SnapshotStore", "MAX_SNAPSHOTS_PER_SESSION"]
