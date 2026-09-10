"""长期记忆：向量库优先，不可用时降级本地 JSON + 关键词重叠打分（离线必须可用）。

设计权衡
--------
1. **两级后端，接口一致**：优先尝试远端向量库（`VECTOR_STORE_URL`），失败再尝试本地
   持久化向量集合（`VECTOR_STORE_PATH`），两者都不可用则落到本地 JSON + 关键词打分。
   任何一步失败都只降级、不抛异常——离线环境必须能跑通全流程。
2. **不引入 embedding 依赖也算分**：向量后端一律挂自带的轻量嵌入函数
   （词哈希 + 二元组，见 `_HashingEmbedding`），**绝不触发模型下载**——
   多数向量库的默认嵌入函数会在首次使用时联网拉取模型，离线环境会卡住或报错，
   这与"离线必须可用"直接冲突。远端向量库若不可达，则降级为本地 JSON +
   关键词重叠打分：完全离线、零依赖、可解释。
3. 远端向量库的集合可能与旧的默认嵌入函数绑定，此时删除重建（数据来自可重建的记忆条目，
   代价可接受），并把原因记录在 `degraded_reason`。
4. 写入本地文件时用原子替换（临时文件 + `replace`），避免进程中断留下半截 JSON。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

from config import Settings

logger = logging.getLogger(__name__)

_COLLECTION = "pathforge_longterm"
_DEFAULT_K = 5
_EMBED_DIM = 256

# 关键词切分：连续中文与英文数字词
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9+#._-]*|\d+(?:\.\d+)?")


def _embedding_terms(text: str) -> list[str]:
    """嵌入用词元：英文/数字整词 + 中文单字与二元组（中文无空格，二元组能保留词序信息）。"""
    terms: list[str] = []
    for chunk in _TOKEN_RE.findall(text or ""):
        if re.match(r"^[\u4e00-\u9fff]+$", chunk):
            terms.extend(chunk)
            terms.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            terms.append(chunk.lower())
    return terms or ["\u2205"]


def _hashed_embedding(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """哈希袋向量 + L2 归一化：确定性、零依赖、零网络，跨进程可复现。

    这是"词袋"的等价物：语义能力弱于神经嵌入，但足以把同类记忆聚到一起
    （同一岗位/同一结论措辞不同也能召回），且完全离线。
    """
    vec = [0.0] * dim
    for term in _embedding_terms(text):
        digest = hashlib.blake2b(term.encode("utf-8", "ignore"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _embed_request(value: Any) -> list[str]:
    """把嵌入函数的入参统一成字符串列表。

    调用方可能传字符串、字符串列表，或已经带 `input=`/`query=` 包装的 dict，
    这里一次性归一化，保证下面的签名能严格匹配向量库的校验要求。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        inner = value.get("input", value.get("query", value.get("texts", "")))
        return _embed_request(inner)
    try:
        return [str(v) for v in value]
    except TypeError:
        return [str(value)]


class _HashingEmbedding:
    """向量库的嵌入函数适配器（零依赖、零网络，绝不触发模型下载）。

    注意签名：向量库会**校验**嵌入函数的形参名，`__call__` 必须是 `(self, input)`，
    `embed_query` / `embed_documents` 同样只接受 `input`。多一个 `**kwargs`
    都会被判为不符合接口（表现为入库/检索时抛 ValueError 并被迫降级），
    因此这里不写可变参数，改用 `_embed_request` 兼容各种入参包装。
    """

    def __init__(self, dim: int = _EMBED_DIM):
        self.dim = dim

    def __call__(self, input: Any) -> list[list[float]]:  # noqa: A002 - 参数名由协议约定
        return [_hashed_embedding(text, self.dim) for text in _embed_request(input)]

    def embed_documents(self, input: Any) -> list[list[float]]:  # noqa: A002
        """入库路径（有的版本按位置传参，有的用 `input=`）。"""
        return self(input)

    def embed_query(self, input: Any) -> list[float]:  # noqa: A002
        """检索路径。"""
        texts = _embed_request(input)
        return _hashed_embedding(texts[0] if texts else "", self.dim)

    def name(self) -> str:
        return "pathforge-hashing-embedding"


def _keywords(text: str) -> list[str]:
    """把文本切成可比较的关键词：中文取单字 + 二元组，英文/数字取整词（小写）。"""
    tokens: list[str] = []
    for chunk in _TOKEN_RE.findall(text or ""):
        if re.match(r"^[\u4e00-\u9fff]+$", chunk):
            tokens.extend(chunk)
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk.lower())
    return [t for t in tokens if t]


class LongTermMemory:
    """跨会话长期记忆：`add` 写入，`search` 语义/关键词检索。"""

    def __init__(self, cfg: Settings, path: str | Path | None = None,
                 collection: str = _COLLECTION, dim: int = _EMBED_DIM):
        self.cfg = cfg
        self.collection_name = collection
        self._dim = max(16, int(dim))
        self.degraded_reason = ""
        self._lock = threading.RLock()
        self._client: Any = None
        self._collection: Any = None
        self._backend = "file"
        base = Path(path or getattr(cfg, "vector_store_path", "./data/vectors") or "./data/vectors")
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.info("向量目录不可用（%s），改用项目数据目录", e)
            base = Path(getattr(cfg, "data_path", Path.cwd())) / "vectors"
            base.mkdir(parents=True, exist_ok=True)
        self.path = base
        self.file_path = self.path / "longterm_memory.json"
        self._connect()

    # ---------------- 连接 ----------------
    def _connect(self) -> None:
        url = str(getattr(self.cfg, "vector_store_url", "") or "").strip()
        reasons: list[str] = []
        if url:
            if self._try_client("http", url, reasons):
                return
        if self._try_client("file", str(self.path), reasons):
            return
        self.degraded_reason = "；".join(reasons) or "未启用向量库"
        logger.info("长期记忆降级为本地关键词检索：%s", self.degraded_reason)
        self._backend = "file"

    def _try_client(self, kind: str, target: str, reasons: list[str]) -> bool:
        """尝试建立向量后端；失败时把原因写进 reasons 并返回 False。"""
        client = None
        try:
            import chromadb  # 延迟导入：未安装也必须能跑
            if kind == "http":
                client = chromadb.HttpClient(host=self._host(target), port=self._port(target),
                                             ssl=target.startswith("https"))
                client.heartbeat()  # 显式探活，避免"连上了但没服务"
            else:
                client = chromadb.PersistentClient(path=target)
        except Exception as e:  # 导入失败 / 连接失败 / 版本差异
            reasons.append(f"向量后端({kind})不可用（{type(e).__name__}: {e}）")
            return False
        embedder = _HashingEmbedding()
        try:
            collection = client.get_or_create_collection(self.collection_name,
                                                         embedding_function=embedder)
        except Exception as e:
            # 集合可能已绑定"默认嵌入函数"（会触发模型下载）：删除重建，改挂本地嵌入
            try:
                client.delete_collection(self.collection_name)
                collection = client.create_collection(self.collection_name,
                                                      embedding_function=embedder)
            except Exception as e2:
                reasons.append(f"向量集合创建失败（{type(e).__name__}: {e} / {e2}）")
                return False
        self._collection = collection
        self._client = client
        self._backend = "vector"
        return True

    @staticmethod
    def _host(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).hostname or "localhost"

    @staticmethod
    def _port(url: str) -> int:
        from urllib.parse import urlparse
        return urlparse(url).port or (443 if url.startswith("https") else 8000)

    # ---------------- 写入 ----------------
    def add(self, text: str, metadata: dict | None = None) -> dict:
        """写入一条长期记忆（**同时**写向量库与本地 JSON 镜像，返回落库记录）。

        双写是刻意的：本地镜像保证"向量库中途不可用"时记忆不会丢，
        也让离线环境永远有一条可用的关键词检索通道。
        """
        content = (text or "").strip()
        if not content:
            return {"id": "", "backend": self.backend_name, "skipped": True}
        meta = {str(k): (v if isinstance(v, (str, int, float, bool)) else str(v))
                for k, v in dict(metadata or {}).items()}
        meta.setdefault("ts", time.time())
        entry_id = hashlib.sha256(
            (content + json.dumps(meta, sort_keys=True, ensure_ascii=False, default=str))
            .encode("utf-8", "ignore")).hexdigest()[:24]

        mirror_written = True
        with self._lock:
            rows = self._read_file()
            rows = [r for r in rows if r.get("id") != entry_id]
            rows.append({"id": entry_id, "text": content, "metadata": meta})
            mirror_written = self._write_file(rows)

        if self._collection is not None:
            try:
                # 显式传向量：不让向量库回调嵌入函数（版本间回调约定不一致，
                # 且显式传参可以确保"入库"和"检索"用的是同一套向量）。
                self._collection.upsert(ids=[entry_id], documents=[content], metadatas=[meta],
                                        embeddings=[_hashed_embedding(content, self._dim)])
                return {"id": entry_id, "backend": "vector", "ts": meta["ts"]}
            except Exception as e:
                self._degrade(e)
        return {"id": entry_id, "backend": "file", "mirror": mirror_written, "ts": meta["ts"]}

    # ---------------- 检索 ----------------
    def search(self, query: str, k: int = _DEFAULT_K) -> list[dict]:
        """检索最相关的 k 条（向量检索与本地关键词检索**取并集**后按分数排序）。

        取并集而不是"向量优先、失败才降级"：本地关键词通道完全离线且零依赖，
        让它始终参与，既能在向量库召回不足时兜底，也保证"向量库中途挂掉"时
        检索结果不会突然空掉（本地镜像每次 add 都会写）。
        """
        text = (query or "").strip()
        size = max(1, int(k or _DEFAULT_K))
        if not text:
            return []
        merged: dict[str, dict] = {}
        if self._collection is not None:
            for hit in self._search_vector(text, size) or []:
                key = str(hit.get("id") or hit.get("text") or "")
                merged[key] = hit
        for hit in self._search_local(text, size):
            key = str(hit.get("id") or hit.get("text") or "")
            if key in merged:
                # 两路都命中：分数取较大者，并标注来源（可解释性）
                merged[key]["score"] = max(merged[key].get("score", 0.0), hit["score"])
                merged[key]["backend"] = "vector+file"
            else:
                merged[key] = hit
        rows = sorted(merged.values(), key=lambda r: -float(r.get("score") or 0.0))
        return rows[:size]

    def _search_vector(self, text: str, k: int) -> list[dict] | None:
        try:
            res = self._collection.query(query_embeddings=[_hashed_embedding(text, self._dim)],
                                         n_results=k,
                                         include=["documents", "metadatas", "distances"])
        except Exception as e:
            self._degrade(e)
            return None
        docs = (res or {}).get("documents") or [[]]
        metas = (res or {}).get("metadatas") or [[]]
        dists = (res or {}).get("distances") or [[]]
        ids = (res or {}).get("ids") or [[]]
        out: list[dict] = []
        for i, doc in enumerate(docs[0] if docs else []):
            distance = float((dists[0][i] if dists and dists[0] and i < len(dists[0]) else 0.0) or 0.0)
            out.append({"id": (ids[0][i] if ids and ids[0] and i < len(ids[0]) else ""),
                        "text": doc,
                        "metadata": (metas[0][i] if metas and metas[0] and i < len(metas[0]) else {}) or {},
                        "score": round(1.0 / (1.0 + max(0.0, distance)), 4),
                        "distance": round(distance, 4),
                        "backend": "vector"})
        return out

    def _search_local(self, text: str, k: int) -> list[dict]:
        q_tokens = _keywords(text)
        q_set = set(q_tokens)
        with self._lock:
            rows = self._read_file()
        scored: list[dict] = []
        for row in rows:
            doc = str(row.get("text") or "")
            d_set = set(_keywords(doc))
            if not d_set or not q_set:
                continue
            inter = len(q_set & d_set)
            if not inter:
                continue
            # 重叠度：用查询侧覆盖率为主、Jaccard 为辅，避免长文档被长度稀释
            coverage = inter / len(q_set)
            jaccard = inter / len(q_set | d_set)
            score = 0.7 * coverage + 0.3 * jaccard
            if text[:24] and text[:24] in doc:
                score = min(1.0, score + 0.15)   # 整串命中加成
            scored.append({"id": row.get("id", ""), "text": doc,
                           "metadata": row.get("metadata") or {},
                           "score": round(score, 4), "backend": "file"})
        scored.sort(key=lambda r: (-r["score"], str(r["id"])))
        return scored[:k]

    # ---------------- 本地文件读写 ----------------
    def _read_file(self) -> list[dict]:
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            logger.info("长期记忆文件损坏（%s），按空库处理", e)
            return []
        if isinstance(raw, dict):
            raw = raw.get("items") or []
        return [r for r in raw if isinstance(r, dict)]

    def _write_file(self, rows: list[dict]) -> bool:
        """原子写本地镜像；返回是否写成功。"""
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            tmp = self.file_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"items": rows}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.file_path)   # 原子替换
            return True
        except OSError as e:
            logger.warning("长期记忆写盘失败：%s", e)
            return False

    def _degrade(self, exc: Exception) -> None:
        self.degraded_reason = f"向量后端运行期异常（{type(exc).__name__}: {exc}），已降级本地关键词检索"
        logger.warning(self.degraded_reason)
        self._collection = None
        self._client = None
        self._backend = "file"

    # ---------------- 元信息 ----------------
    @property
    def backend_name(self) -> str:
        return "vector" if self._collection is not None else "file"

    def stats(self) -> dict:
        """供 `/api/health` 展示。向量后端统计失败时静默用本地镜像计数。"""
        count = -1
        if self._collection is not None:
            try:
                count = int(self._collection.count())
            except Exception as e:
                self._degrade(e)
        with self._lock:
            mirror = len(self._read_file())
        if count < 0:
            count = mirror
        return {"backend": self.backend_name, "items": count, "mirror_items": mirror,
                "path": str(self.path), "collection": self.collection_name,
                "degraded_reason": self.degraded_reason}

    def clear(self) -> None:
        """清空长期记忆（向量后端不可用时清本地文件）。

        集合不存在时 `delete_collection` 会抛错——那是正常状态而非故障，
        不能因此把后端判死（否则清一次记忆就永久降级了）。
        """
        if self._collection is not None:
            try:
                self._client.delete_collection(self.collection_name)
            except Exception as e:
                logger.debug("删除向量集合未成功（可能本就不存在）：%s", e)
            try:
                self._collection = self._client.create_collection(
                    self.collection_name, embedding_function=_HashingEmbedding())
            except Exception as e:
                self._degrade(e)
        with self._lock:
            self._write_file([])


def keyword_overlap(query: str, document: str) -> float:
    """纯函数形式的关键词重叠打分（便于单测与复用）。"""
    q, d = set(_keywords(query)), set(_keywords(document))
    if not q or not d:
        return 0.0
    inter = len(q & d)
    return 0.0 if not inter else round(0.7 * inter / len(q) + 0.3 * inter / len(q | d), 4)


__all__ = ["LongTermMemory", "keyword_overlap"]
