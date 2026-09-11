# -*- coding: utf-8 -*-
"""安全加固验收：鉴权、租户隔离、运行预算、队列上限（评审 P0-1/P0-2/P1-1/P1-2）。

为什么单独一个脚本、而不是塞进 smoke_api.py：
    配置在进程启动时读取（`load_config()` 带缓存），鉴权开关必须在 **import main 之前**
    就设好；在同一进程里既跑"关闭鉴权"又跑"开启鉴权"会得出自相矛盾的结论。
    因此这里独立成脚本，用子进程环境变量构建一个"开启鉴权"的实例。

用法（仓库根目录）：
    set PYTHONPATH=backend
    python scripts/smoke_auth.py

退出码：0 = 全部通过。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# ---- 必须在 import main 之前设置：鉴权开启 + 两个 owner + 极小预算便于触发护栏 ----
os.environ["LLM_PROVIDER"] = "mock"
os.environ["AUTH_ENABLED"] = "true"
os.environ["API_KEYS"] = "tok-alice=alice,tok-bob=bob"
os.environ["DATABASE_URL"] = ""
os.environ["SQLITE_FALLBACK_URL"] = \
    f"sqlite:///{Path(tempfile.gettempdir()).as_posix()}/pf_auth_smoke.db"
os.environ["REDIS_URL"] = ""
os.environ["VECTOR_STORE_URL"] = ""
os.environ["MAX_CONCURRENT_RUNS"] = "1"
os.environ["MAX_QUEUE_SIZE"] = "1"
os.environ["MAX_TOKENS_PER_RUN"] = "1"     # 极小预算：任何一次真实调用都会超限

ALICE = {"Authorization": "Bearer tok-alice"}
BOB = {"X-API-Key": "tok-bob"}

PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    from fastapi.testclient import TestClient

    import main as app_mod

    with TestClient(app_mod.app) as client:
        print("== 1. 鉴权拒绝路径 ==")
        r = client.get("/api/sessions")
        check("无令牌访问受保护端点 → 401", r.status_code == 401, str(r.status_code))
        r = client.get("/api/sessions", headers={"Authorization": "Bearer wrong-token"})
        check("错误令牌 → 403", r.status_code == 403, str(r.status_code))
        r = client.get("/api/health")
        check("健康检查公开（探针可用）", r.status_code == 200, str(r.status_code))
        r = client.get("/api/sessions?token=tok-alice")
        check("GET 允许查询参数令牌（供 <img src> 直链预览）",
              r.status_code == 200, str(r.status_code))
        r = client.post("/api/sessions?token=tok-alice", json={"title": "经查询参数建会话"})
        check("POST 拒绝查询参数令牌 → 401（写操作必须走请求头）",
              r.status_code == 401, str(r.status_code))
        r = client.get("/metrics")
        check("/metrics 需要令牌 → 401", r.status_code == 401, str(r.status_code))
        r = client.get("/metrics", headers=ALICE)
        check("/metrics 带令牌 → 200", r.status_code == 200, str(r.status_code))
        check("metrics 含预置 0 值指标", "pathforge_runs_active" in r.text,
              r.text.splitlines()[0] if r.text else "(空)")

        print("== 2. 正常访问与租户隔离 ==")
        r = client.post("/api/sessions", json={"title": "alice-会话"}, headers=ALICE)
        check("alice 建会话 → 201", r.status_code == 201, str(r.status_code))
        sid = r.json()["id"]

        r = client.get("/api/sessions", headers=ALICE)
        alice_titles = [s["title"] for s in r.json()["items"]]
        check("alice 看得到自己的会话", "alice-会话" in alice_titles, f"{len(alice_titles)} 条")

        r = client.get("/api/sessions", headers=BOB)
        bob_titles = [s["title"] for s in r.json()["items"]]
        check("bob 看不到 alice 的会话", "alice-会话" not in bob_titles, f"{len(bob_titles)} 条")

        r = client.get(f"/api/sessions/{sid}", headers=BOB)
        check("bob 直接按 ID 取 alice 会话 → 404（不泄漏存在性）",
              r.status_code == 404, str(r.status_code))
        r = client.get(f"/api/sessions/{sid}/history", headers=BOB)
        check("bob 取 alice 历史 → 404", r.status_code == 404, str(r.status_code))
        r = client.delete(f"/api/sessions/{sid}", headers=BOB)
        check("bob 删 alice 会话 → 404", r.status_code == 404, str(r.status_code))
        r = client.put(f"/api/sessions/{sid}/title", json={"title": "hacked"}, headers=BOB)
        check("bob 改 alice 标题 → 404", r.status_code == 404, str(r.status_code))
        r = client.get(f"/api/sessions/{sid}", headers=ALICE)
        check("alice 自己仍可读", r.status_code == 200, str(r.status_code))

        print("== 3. 反馈接口（含隔离） ==")
        r = client.post(f"/api/sessions/{sid}/feedback",
                        json={"rating": 5, "comment": "很有帮助", "message_ts": 1.5}, headers=ALICE)
        check("alice 提交反馈 → 201", r.status_code == 201, str(r.status_code))
        r = client.get(f"/api/sessions/{sid}/feedback", headers=ALICE)
        body = r.json()
        check("alice 读到自己的反馈", r.status_code == 200 and len(body["items"]) == 1,
              f"{len(body.get('items', []))} 条")
        check("反馈概览含平均分", body.get("summary", {}).get("average") == 5.0,
              str(body.get("summary")))
        r = client.get(f"/api/sessions/{sid}/feedback", headers=BOB)
        check("bob 读 alice 反馈 → 404", r.status_code == 404, str(r.status_code))
        r = client.post(f"/api/sessions/{sid}/feedback", json={"rating": 1}, headers=BOB)
        check("bob 给 alice 会话写反馈 → 404", r.status_code == 404, str(r.status_code))

        print("== 4. 运行预算护栏（MAX_TOKENS_PER_RUN=1） ==")
        # 用会真正调用工具/模型的任务：只有真的消耗 token，预算才有可判定的意义
        events: list[str] = []
        with client.stream("POST", "/api/agent/run",
                           json={"task": "帮我分析 data/jds/jd03_数据分析师.txt 并匹配简历",
                                 "session_id": sid, "preset": "minimal",
                                 "workspace": "default"}, headers=ALICE) as resp:
            check("带令牌可发起运行", resp.status_code == 200, str(resp.status_code))
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    events.append(line[7:].strip())
                if len(events) > 800:
                    break
        check("超出 token 预算被中止",
              "interrupted" in events, f"{len(events)} 条事件: {events[-4:]}")
        check("中止前有 error 事件说明原因", "error" in events, str(events[-4:]))
        from api.agent import resolve_run_service as _rrs
        _svc, _ = _rrs()
        if _svc is not None:
            metas = [m for m in _svc._runs.values() if m.get("session_id") == sid]
            check("run 状态归档为 interrupted",
                  any(m.get("status") == "interrupted" for m in metas),
                  str([m.get("status") for m in metas]))

        print("== 5. 队列上限（MAX_QUEUE_SIZE=1） ==")
        from api.agent import resolve_run_service
        service, err = resolve_run_service()
        check("RunService 可用", service is not None, err or "ok")
        if service is not None:
            adm = service._admission
            adm._active["fake-run"] = 5                      # 占满并发
            adm._waiting.append((5, 0, "queued-run", _dummy_future()))
            r = client.post("/api/agent/run",
                            json={"task": "再跑一个", "preset": "minimal"}, headers=ALICE)
            check("队列满 → 429（且不建立 SSE）", r.status_code == 429, str(r.status_code))
            check("429 带 Retry-After", r.headers.get("retry-after") == "5",
                  str(r.headers.get("retry-after")))
            adm._active.clear()
            adm._waiting.clear()

        print("== 6. WebSocket 鉴权 ==")
        try:
            with client.websocket_connect(f"/ws/agent/{sid}") as ws:
                ws.send_json({"type": "ping"})
                msg = ws.receive_json()
                check("无令牌 WS 应被拒（不应收到 pong）", msg.get("type") != "pong", str(msg)[:60])
        except Exception as e:  # noqa: BLE001 — 握手被拒会以异常形式抛出
            check("无令牌 WS 拒绝握手", True, type(e).__name__)
        try:
            with client.websocket_connect(f"/ws/agent/{sid}?token=tok-alice") as ws:
                ws.send_json({"type": "ping"})
                msg = ws.receive_json()
                check("带令牌 WS ping→pong", msg.get("type") == "pong", str(msg)[:60])
        except Exception as e:  # noqa: BLE001
            check("带令牌 WS 可连接", False, f"{type(e).__name__}: {e}")

        print("== 7. 清理 ==")
        r = client.delete(f"/api/sessions/{sid}", headers=ALICE)
        check("alice 删除自己的会话 → 200", r.status_code == 200, str(r.status_code))

    print("\n" + "=" * 52)
    if FAILED:
        print(f"结果: {PASSED} 通过, {len(FAILED)} 失败")
        for f in FAILED:
            print("   -", f)
        return 1
    print(f"结果: {PASSED}/{PASSED} 全部通过 🎉")
    return 0


def _dummy_future():
    """构造一个不会被 await 的 Future，仅用于占位队列（测试内同步调用，不涉及事件循环）。"""
    import asyncio
    return asyncio.new_event_loop().create_future()


if __name__ == "__main__":
    sys.exit(main())
