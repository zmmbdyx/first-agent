"""轻量可观测层：结构化日志 + Prometheus 指标（对应评审 P2-4 的"部分"）。

为什么不直接上 OpenTelemetry：
- 本项目的部署形态是单进程（`uvicorn main:app`），OTel 的 collector/SDK 依赖与运维成本
  远超当前收益；先把**指标与结构化日志**这两个最能回答"出了什么事、花了多少钱"的部分做扎实，
  需要链路追踪时再在 `run_id` 这条主线之上接入 OTel 即可（run_id 已贯穿日志与数据表）。

设计：无第三方依赖、线程安全、进程内单例；指标名与标签遵守 Prometheus 文本格式约定。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import defaultdict
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_LOCK = threading.Lock()

# 计数器：name -> {(label_key, label_value): value}
_COUNTERS: dict[str, dict[tuple, float]] = defaultdict(dict)
# 仪表：name -> value
_GAUGES: dict[str, float] = {}
# 直方图：name -> {"buckets": [...], "counts": [...], "sum": float, "count": int}
_HISTOGRAMS: dict[str, dict] = {}

RUN_DURATION_BUCKETS = (1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600)


def inc(name: str, value: float = 1.0, **labels: Any) -> None:
    """计数器自增。标签值会转成字符串，None 归一为 ""。"""
    key = tuple(sorted((k, "" if v is None else str(v)) for k, v in labels.items()))
    with _LOCK:
        bucket = _COUNTERS[name]
        bucket[key] = bucket.get(key, 0.0) + value


def gauge(name: str, value: float) -> None:
    with _LOCK:
        _GAUGES[name] = float(value)


def observe(name: str, value: float, buckets: tuple = RUN_DURATION_BUCKETS) -> None:
    """直方图观测（桶为秒；默认用于运行耗时）。"""
    with _LOCK:
        h = _HISTOGRAMS.get(name)
        if h is None:
            h = {"buckets": list(buckets), "counts": [0] * len(buckets), "sum": 0.0, "count": 0}
            _HISTOGRAMS[name] = h
        h["sum"] += float(value)
        h["count"] += 1
        for i, edge in enumerate(h["buckets"]):
            if value <= edge:
                h["counts"][i] += 1


def snapshot() -> dict:
    with _LOCK:
        return {
            "counters": {k: {("|".join(f"{a}={b}" for a, b in key) or "-"): v
                             for key, v in bucket.items()}
                         for k, bucket in _COUNTERS.items()},
            "gauges": dict(_GAUGES),
            "histograms": {k: {"count": v["count"], "sum": round(v["sum"], 3),
                               "buckets": v["buckets"], "counts": v["counts"]}
                           for k, v in _HISTOGRAMS.items()},
        }


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_prometheus() -> str:
    """渲染成 Prometheus 文本格式（无需额外依赖即可被 scrape）。"""
    lines: list[str] = []
    snap = snapshot()
    for name, bucket in sorted(snap["counters"].items()):
        lines.append(f"# TYPE {name} counter")
        for label_str, value in sorted(bucket.items()):
            labels = ""
            if label_str and label_str != "-":
                parts = [p.split("=", 1) for p in label_str.split("|") if "=" in p]
                labels = "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in parts) + "}"
            lines.append(f"{name}{labels} {value:g}")
    for name, value in sorted(snap["gauges"].items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value:g}")
    for name, h in sorted(snap["histograms"].items()):
        lines.append(f"# TYPE {name} histogram")
        cumulative = 0
        for edge, count in zip(h["buckets"], h["counts"]):
            cumulative = max(cumulative, count)
            lines.append(f'{name}_bucket{{le="{edge}"}} {cumulative}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {h["count"]}')
        lines.append(f"{name}_sum {h['sum']:g}")
        lines.append(f"{name}_count {h['count']}")
    return "\n".join(lines) + "\n"


def bootstrap() -> None:
    """初始化"始终存在"的指标。

    为什么需要：Prometheus 抓取端在指标首次出现前看不到任何序列，
    "服务刚起来、还没跑过任务"与"服务挂了"在监控上无法区分。
    这里把关键指标先置 0，抓取端能立刻确认服务存活。
    """
    for name in ("pathforge_runs_started_total", "pathforge_runs_failed_total",
                 "pathforge_runs_interrupted_total", "pathforge_runs_timeout_total",
                 "pathforge_queue_rejected_total", "pathforge_auth_failures_total"):
        inc(name, 0.0)
    for name in ("pathforge_runs_active", "pathforge_queue_waiting", "pathforge_run_tokens",
                 "pathforge_context_ratio", "pathforge_cache_hit_rate"):
        gauge(name, 0.0)
    observe("pathforge_run_duration_seconds", 0.0)


def log_event(event: str, level: str = "info", **fields: Any) -> None:
    """结构化日志：默认人类可读单行；`LOG_JSON=true` 时输出 JSON（便于采集）。

    刻意不引入 logging 配置体系：本项目日志点少而集中，
    自己拼一行比配置 handler/formatter 更可控，也避免与 uvicorn 的日志配置互相打架。
    """
    payload = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
    try:
        from config import load_config
        as_json = bool(getattr(load_config(), "log_json", False))
    except Exception:  # noqa: BLE001 — 配置不可用时退回纯文本
        as_json = False
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
        return
    parts = " ".join(f"{k}={v}" for k, v in fields.items() if v not in (None, ""))
    print(f"[{level}] {event}" + (f" {parts}" if parts else ""), flush=True)


__all__ = ["inc", "gauge", "observe", "snapshot", "render_prometheus", "log_event",
           "bootstrap", "RUN_DURATION_BUCKETS"]
