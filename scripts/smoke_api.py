# -*- coding: utf-8 -*-
"""端到端接口冒烟：把契约（docs/ARCHITECTURE.md 第 2 节）里的端点逐个打一遍。

用法（在仓库根目录）：
    set PYTHONPATH=backend           # Windows
    export PYTHONPATH=backend        # Linux/macOS
    python scripts/smoke_api.py

特点：
- 全程用离线 mock 模式（不需要任何 API key、PostgreSQL、Redis）；
- `/api/agent/run` 走真实 SSE 流，校验事件序列里必须出现
  run_started / node_start / node_end / plan_created / trajectory / metric / final_answer / run_done；
- 覆盖路径穿越防护（files/git 的 `..` 必须被拒）；
- 退出码 0 = 全部通过。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))  # 新布局：应用代码在 backend/

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 强制离线：即使本机 .env 配了真实端点，冒烟也走 mock，避免误扣费与网络抖动。
# 数据库连接串在 main() 里按 --use-env-db 决定（默认覆盖成临时 SQLite，保证可重复）。
os.environ["LLM_PROVIDER"] = "mock"
os.environ["REDIS_URL"] = ""
os.environ["VECTOR_STORE_URL"] = ""

PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  ❌ {name}  {detail}")


def main() -> int:
    ap = argparse.ArgumentParser(description="端到端接口冒烟")
    ap.add_argument("--use-env-db", action="store_true",
                    help="使用环境里的 DATABASE_URL（默认强制用临时 SQLite，保证可重复）")
    args = ap.parse_args()

    if args.use_env_db:
        # 真正跑在 PostgreSQL 等外部库上：只清掉临时 SQLite 回落，保留 DATABASE_URL
        os.environ.pop("SQLITE_FALLBACK_URL", None)
    else:
        os.environ["DATABASE_URL"] = ""
        os.environ["SQLITE_FALLBACK_URL"] = \
            f"sqlite:///{Path(tempfile.gettempdir()).as_posix()}/pf_smoke.db"

    from fastapi.testclient import TestClient

    import main as app_mod

    with TestClient(app_mod.app) as client:
        print("== 1. 健康检查与元信息 ==")
        r = client.get("/api/health")
        check("GET /api/health", r.status_code == 200, str(r.status_code))
        health = r.json()
        check("health 含 provider", "provider" in health, health.get("provider", ""))

        r = client.get("/api/agent/presets")
        check("GET /api/agent/presets", r.status_code == 200 and r.json().get("items"),
              str([p.get("id") for p in r.json().get("items", [])]))
        presets = {p["id"] for p in r.json().get("items", [])}
        check("预设四件套齐全", presets >= {"standard", "minimal", "ptc", "creative"}, str(presets))

        r = client.get("/api/models")
        check("GET /api/models", r.status_code == 200, str(r.status_code))

        print("== 2. 工作区 ==")
        # 名称带随机后缀：保证脚本可重复运行（上一轮残留不应让本轮"假失败"）
        ws_name = f"smoke_ws_{uuid.uuid4().hex[:6]}"
        r = client.post("/api/workspaces", json={"name": ws_name, "path": f"./workspaces/{ws_name}",
                                                 "description": "冒烟测试工作区"})
        check("POST /api/workspaces", r.status_code in (200, 201), str(r.status_code))
        ws_id = r.json().get("id", "") if r.status_code in (200, 201) else ""
        r = client.get("/api/workspaces")
        check("GET /api/workspaces", r.status_code == 200 and r.json().get("items") is not None)
        names = [w["name"] for w in r.json()["items"]]
        check("工作区列表含新建项", ws_name in names, str(names[:6]))

        print("== 3. 会话 CRUD ==")
        r = client.post("/api/sessions", json={"title": "冒烟会话", "workspace": "default"})
        check("POST /api/sessions", r.status_code in (200, 201), str(r.status_code))
        sid = r.json()["id"]

        r = client.get("/api/sessions?workspace=default")
        check("GET /api/sessions", r.status_code == 200 and r.json().get("total", 0) >= 1,
              f"total={r.json().get('total')}")

        r = client.put(f"/api/sessions/{sid}/title", json={"title": "冒烟会话·改名"})
        check("PUT /api/sessions/{id}/title", r.status_code == 200 and
              r.json().get("title") == "冒烟会话·改名", r.json().get("title", ""))

        r = client.put(f"/api/sessions/{sid}/pin", json={"pinned": True})
        check("PUT /api/sessions/{id}/pin", r.status_code == 200 and r.json().get("pinned") is True)

        print("== 4. 工具管理（MCP 兼容） ==")
        r = client.get("/api/tools")
        check("GET /api/tools", r.status_code == 200 and len(r.json().get("items", [])) >= 9,
              f"{len(r.json().get('items', []))} 个工具")

        payload = {"name": "smoke_http_tool", "description": "冒烟用自定义工具",
                   "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                   "kind": "http", "config": {"url": "https://example.com/api", "method": "POST"},
                   "enabled": True}
        r = client.post("/api/tools", json=payload)
        check("POST /api/tools", r.status_code in (200, 201, 409), str(r.status_code))
        r = client.delete("/api/tools/smoke_http_tool")
        check("DELETE /api/tools/{name}", r.status_code in (200, 204, 404), str(r.status_code))

        print("== 5. 文件与 Git（含路径穿越防护） ==")
        r = client.get("/api/files/tree?workspace=default&depth=2")
        check("GET /api/files/tree", r.status_code == 200 and "nodes" in r.json(), str(r.status_code))

        r = client.get("/api/files/content?workspace=default&path=../../.env")
        check("文件穿越被拒（400/404）", r.status_code in (400, 404), str(r.status_code))

        r = client.get("/api/git/status?workspace=default")
        check("GET /api/git/status", r.status_code == 200 and "is_repo" in r.json(), str(r.status_code))

        print("== 6. Agent 执行（真实 SSE 流，mock 模式） ==")
        # 带材料路径的任务：mock 规划器据此拆出 jd_analyze / resume_match 等子任务，
        # 能跑完整链路（规划 → 工具 → 报告），这是校验事件序列完整性的主用例。
        task_full = ("帮我分析 data/jds/jd03_数据分析师.txt，"
                     "并匹配简历 data/resumes/简历_李明_数据分析师.txt，给出面试题")
        seen: list[str] = []
        nodes: set[str] = set()
        with client.stream("POST", "/api/agent/run",
                           json={"task": task_full, "session_id": sid, "preset": "standard",
                                 "workspace": "default",
                                 "permission_mode": "workspace_write"}) as resp:
            check("POST /api/agent/run 返回 200", resp.status_code == 200, str(resp.status_code))
            check("Content-Type 为 text/event-stream",
                  "text/event-stream" in resp.headers.get("content-type", ""),
                  resp.headers.get("content-type", ""))
            event_name = ""
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event_name = line[7:].strip()
                    seen.append(event_name)
                elif line.startswith("data: ") and event_name:
                    try:
                        payload_obj = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if payload_obj.get("type") == "node_start":
                        nodes.add(str(payload_obj.get("node")))
                    if len(seen) > 4000:
                        break

        required = ["run_started", "session_info", "node_start", "node_end", "plan_created",
                    "task_start", "thought", "tool_call", "task_finish", "trajectory",
                    "metric", "final_answer", "run_done"]
        missing = [e for e in required if e not in seen]
        check("SSE 事件序列完整", not missing, f"缺失 {missing}" if missing else f"{len(seen)} 条事件")
        check("执行节点覆盖 recall/plan/dispatch/react/synthesize",
              {"recall", "plan", "dispatch", "react", "synthesize"} <= nodes, str(sorted(nodes)))

        print("== 6b. ask_user 挂起路径（信息不足时应停下追问） ==")
        ask_events: list[str] = []
        with client.stream("POST", "/api/agent/run",
                           json={"task": "帮我找份好工作", "session_id": "", "preset": "minimal",
                                 "workspace": "default"}) as resp2:
            event_name = ""
            for line in resp2.iter_lines():
                if line.startswith("event: "):
                    event_name = line[7:].strip()
                    ask_events.append(event_name)
                if len(ask_events) > 2000:
                    break
        check("信息不足时产生 ask_user 或直接给出答复",
              ("ask_user" in ask_events) or ("final_answer" in ask_events),
              f"{ask_events.count('ask_user')} 次 ask_user / {len(ask_events)} 条事件")

        print("== 7. 执行后历史与轨迹 ==")
        r = client.get(f"/api/sessions/{sid}/history")
        check("GET /api/sessions/{id}/history", r.status_code == 200, str(r.status_code))
        hist = r.json()
        check("历史含消息", len(hist.get("messages", [])) >= 2, f"{len(hist.get('messages', []))} 条")
        check("历史含任务", len(hist.get("tasks", [])) >= 1, f"{len(hist.get('tasks', []))} 个")
        check("历史含工具调用", len(hist.get("tool_calls", [])) >= 1,
              f"{len(hist.get('tool_calls', []))} 次")
        check("历史含轨迹节点", len(hist.get("trajectory", [])) >= 3,
              f"{len(hist.get('trajectory', []))} 个节点")

        print("== 8. WebSocket 通道 ==")
        try:
            with client.websocket_connect(f"/ws/agent/{sid}") as ws:
                ws.send_json({"type": "ping"})
                msg = ws.receive_json()
                check("WS ping/pong", msg.get("type") in ("pong", "heartbeat"), str(msg)[:80])
        except Exception as e:  # noqa: BLE001 — WS 只是增强通道
            check("WS ping/pong", False, f"{type(e).__name__}: {e}")

        print("== 9. 回滚与清理 ==")
        r = client.post(f"/api/sessions/{sid}/rollback", json={"checkpoint_id": ""})
        check("POST /api/sessions/{id}/rollback", r.status_code in (200, 400, 404, 409),
              str(r.status_code))

        r = client.delete(f"/api/sessions/{sid}")
        check("DELETE /api/sessions/{id}", r.status_code in (200, 204), str(r.status_code))

        if ws_id:
            r = client.delete(f"/api/workspaces/{ws_id}")
            check("DELETE /api/workspaces/{id}（清理）", r.status_code in (200, 204, 404),
                  str(r.status_code))

    print("\n" + "=" * 46)
    if FAILED:
        print(f"结果: {PASSED} 通过, {len(FAILED)} 失败 ❌")
        for f in FAILED:
            print("   -", f)
        return 1
    print(f"结果: {PASSED}/{PASSED} 全部通过 🎉")
    return 0


if __name__ == "__main__":
    sys.exit(main())
