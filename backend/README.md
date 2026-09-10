# 拓径 PATHFORGE — 后端

求职业务域智能体平台后端：会话 / 工具 / 工作区 / 文件 / Git / Agent SSE / WebSocket。
接口基线见仓库根 `docs/ARCHITECTURE.md`（**冻结契约**，字段名与路径不得改动）。

---

## 1. 目录结构

```
backend/
├── main.py                  # FastAPI 入口：CORS、lifespan（建表/默认工作区/归档残留运行）、静态站点
├── config.py                # pydantic-settings 全局配置（load_config()）
├── db.py                    # 引擎 / SessionLocal / Base / init_db / get_db 依赖
├── models/                  # SQLAlchemy 模型（表名见契约第 4 节）
│   ├── base.py              # Base、JSONType（PG→JSONB / SQLite→JSON）、utcnow、gen_id、iso
│   └── session.py message.py run.py task.py tool_call.py trajectory.py workspace.py custom_tool.py
├── schemas/                 # Pydantic v2 请求/响应模型（与前端 types/index.ts 一一对应）
│   └── common.py session.py agent.py tool.py workspace.py file.py
├── services/                # 业务逻辑层（路由只做协议转换）
│   ├── session_service.py   # 会话 CRUD / 历史聚合 / JobAgent 运行态 ↔ 数据库搬运
│   ├── workspace_service.py # 工作区 CRUD + 路径沙箱（safe_join / resolve_path）
│   ├── file_service.py      # 文件树 / 内容预览 / 报告白名单
│   ├── git_service.py       # 变更列表 / stage / unstage
│   ├── tool_service.py      # 工具注册表 + 自定义工具（MCP 兼容）
│   ├── mcp_service.py       # MCP 工具发现与调用元信息
│   ├── broadcast.py         # 进程内事件广播中心（WS 推送与 SSE 主链路解耦）
│   └── run_service.py       # 图编排执行（LangGraph，见契约第 3 节）
├── api/                     # 路由层
│   ├── __init__.py          # 汇总 APIRouter（前缀 /api）+ ws 路由
│   └── sessions.py agent.py tools.py workspaces.py files.py git.py assets.py ws.py
├── core/                    # 既有业务核心（Agent / 工具 / LLM / 记忆 / 图编排）
└── legacy/                  # 旧单体实现，仅供追溯
```

---

## 2. 环境变量

全部配置从环境变量或 `.env` 读取（查找顺序：`backend/.env` → 根 `.env` → 根 `.env.local`），
**代码中零硬编码连接串与密钥**。变量清单与默认值见 `backend/.env.example`，常用项：

| 变量 | 作用 | 缺省行为 |
|---|---|---|
| `APP_NAME` / `APP_ENV` / `API_HOST` / `API_PORT` | 应用与监听 | `PATHFORGE` / `development` / `127.0.0.1` / `8000` |
| `CORS_ORIGINS` | 允许的前端来源（逗号分隔） | `http://127.0.0.1:5173,http://localhost:5173` |
| `LLM_PROVIDER` `LLM_API_KEY` `LLM_BASE_URL` `LLM_MODEL` `LLM_MODELS` | 模型端点（OpenAI 兼容协议，中性占位符） | 凭据不全 → 自动降级离线 `mock` |
| `MAX_REACT_STEPS` `MAX_TASKS` `TASK_TIMEOUT` `TOOL_MAX_RETRIES` | 执行护栏 | 5 / 6 / 300 / 2 |
| `DATABASE_URL` | 主库（PostgreSQL） | 为空 → 回落 `SQLITE_FALLBACK_URL` |
| `SQLITE_FALLBACK_URL` | 开箱即跑用的 SQLite | `sqlite:///./data/pathforge.db` |
| `REDIS_URL` | 短期记忆 / 队列 | 不可达 → 进程内实现，**不阻断启动** |
| `VECTOR_STORE_URL` / `VECTOR_STORE_PATH` | 长期记忆 | 远端不可达 → 本地文件 |
| `WORKSPACE_ROOT` | 工作区根目录（路径沙箱边界） | `./workspaces` |
| `SANDBOX_ENABLED` `SANDBOX_TIMEOUT` `SANDBOX_MAX_OUTPUT` `ALLOW_FULL_ACCESS` | 沙箱与权限上限 | true / 300 / 65536 / false |
| `DATA_KEY` | 静态数据加密口令（**留空则读 `data/.key` 或自动生成**） | 自动生成，不入库 |

> 未配置任何模型凭据时 `provider=mock`：全链路（规划 → 工具 → 报告）仍可离线跑通，
> 便于本机验证与冒烟测试。

---

## 3. 启动

```powershell
# 依赖（仓库根）
pip install -r requirements.txt

# 从仓库根启动（推荐：相对路径配置与 data/ 目录均以项目根为基准）
$env:PYTHONPATH="E:\ai\ai job\agent\backend"
python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000

# 或直接
python backend\main.py
```

- 接口文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/health`
- 若 `frontend/dist` 存在，根路径自动作为静态站点并做 SPA 回退；不存在则返回 JSON 提示。

**降级说明**：无 PostgreSQL → 自动用 SQLite；无 Redis → 队列/短期记忆走进程内实现；
无向量库 → 长期记忆落本地文件。任一依赖缺失都只影响对应能力，服务照常启动，
实际状态在 `/api/health` 的 `db` / `redis` / `vector_store` 字段中如实上报。

---

## 4. API 一览

统一前缀 `/api`，错误统一 `{"detail": "..."}` + 恰当状态码
（路径越界 400、资源不存在 404、名称冲突 409、内置资源不可删 403、可选依赖未就绪 503）。

### 会话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sessions` | 新建会话 → `SessionOut` |
| GET | `/api/sessions?workspace=&limit=&offset=&q=` | 列表（`pinned desc, updated_at desc`） |
| GET | `/api/sessions/{id}` | 单个会话 |
| GET | `/api/sessions/{id}/history` | `{session,messages,tasks,tool_calls,trajectory,stats}` |
| PUT | `/api/sessions/{id}/title` · `/pin` | 重命名 / 置顶 |
| DELETE | `/api/sessions/{id}` | 删除会话及其消息、任务、轨迹 |
| POST | `/api/sessions/{id}/rollback` | 回滚到检查点（`checkpoint_id` 空 = 本 run 起点） |

### Agent 执行

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/run` | `text/event-stream`；事件格式 `event: <type>\ndata: <单行JSON>\n\n`，15s 心跳 |
| POST | `/api/agent/runs/{run_id}/interrupt` | 中断运行 |
| GET | `/api/agent/runs/{run_id}` | 运行状态与统计 |
| GET | `/api/agent/presets` | 预设 `standard/minimal/ptc/creative` |

SSE 事件名以契约 2.2 节为准（`run_started` / `session_info` / `queue_position` /
`node_start` / `node_end` / `plan_created` / `thought` / `tool_call` / `tool_result` /
`tool_error` / `task_finish` / `metric` / `trajectory` / `final_answer` / `run_done` / `heartbeat` …）。
流经的事件会被同步落库：`tool_call*` 写 `tool_calls`，`node_*` 写 `trajectory_nodes`，
`metric`/`run_done` 归档到 `runs.stats`。

### 工具（MCP 兼容）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tools` | `{items:[ToolOut], mcp:{protocol:"mcp/1.0", tools_endpoint:"/api/tools"}}` |
| POST | `/api/tools` | 注册自定义工具（`kind ∈ http\|python\|mcp`），重名 409 |
| DELETE | `/api/tools/{name}` | 卸载自定义工具；内置工具 403 |
| POST | `/api/tools/{name}/test` | 试调用 → `{ok, data, error}` |

`kind=python` 采用**声明式委派**（`config.delegate` 指向已注册工具名），
不接受任意源码——开放任意代码执行等于把沙箱边界让掉。

### 工作区 / 文件 / Git

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/workspaces[/{id}]` | 工作区 CRUD（`WorkspaceOut` 含 `exists`/`file_count`） |
| GET | `/api/files/tree?workspace=&path=&depth=` | 文件树 |
| GET | `/api/files/content?workspace=&path=` | 文本预览（`text/markdown/image/pdf/binary`） |
| GET | `/api/files/raw?workspace=&path=` | 原始字节（图片/PDF 内联预览） |
| GET | `/api/files/report?path=` | 报告产物，**仅放行 `data/reports/` 白名单** |
| GET | `/api/git/status?workspace=` | 变更列表 |
| POST | `/api/git/stage` · `/unstage` | 暂存 / 取消暂存 |

### 业务资产与系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/DELETE | `/api/resumes[/upload/{name}]` | 简历库（落盘即加密） |
| POST | `/api/upload` · `/api/ocr` | 通用材料上传 / 截图识别 |
| GET | `/api/models` · POST `/api/model/select` | 模型清单与切换 |
| GET | `/api/health` | 各子系统真实状态 |
| GET | `/api/config` | 非敏感配置快照（绝不回传密钥/连接串） |

### WebSocket

`/ws/agent/{session_id}`：客户端发 `{"type":"ping"}` → 收 `{"type":"pong"}`；
服务端通过 `services/broadcast.py` 推送 `tool_status` / `metric` / `node` 等事件。
**该通道只是 SSE 的旁路加速**，连接失败或断开都不影响主链路执行。

---

## 5. 数据层要点

- **表结构**：`sessions` / `messages` / `runs` / `tasks` / `tool_calls` / `trajectory_nodes` /
  `workspaces` / `custom_tools`，列名见契约第 4 节；JSON 列在 PostgreSQL 上为 `JSONB`，
  SQLite 上为 `JSON`（`models.base.JSONType`）。
- **时间**：统一 `DateTime(timezone=True)` + UTC；出参经 `models.base.iso()` 补时区后转 ISO 字符串。
- **运行态搬运**：`SessionService.save_runtime()` 幂等地整表重写消息与任务；
  artifacts / pending_question 等附加态存在 `sessions.facts` 的 `_runtime` 保留键下
  （`core.agent` 已过滤所有 `_` 前缀键，不会污染用户画像）。
- **任务主键**：业务侧任务 ID 形如 `t1`（跨会话重复），落库主键用 `会话ID:序号`，
  原始 ID 存在 `tasks.tokens.plan.orig_id`，读回时还原。
- **旧数据迁移**：首次访问会话列表时把 `data/sessions/*.json` 旧存档惰性导入数据库
  （加密存档经 `core.secure_store` 解密），导入失败的单条会被跳过而不影响整体。
- **残留运行归档**：启动时把超过 5 分钟仍处于 `queued/running` 的运行标记为 `interrupted`。

## 6. 安全边界

1. `/api/files/*` 与 `/api/git/*` 的 `path` 一律 `resolve()` 后校验位于工作区根内：
   `..`、绝对路径、符号链接逃逸全部 400；目标不存在时向上寻找最近存在祖先同样校验。
2. 文件树不跟随符号链接，并跳过 `.git`/`node_modules`/`__pycache__` 等目录。
3. `/api/files/report` 做 `data/reports/` 白名单 + 二次 resolve 校验，
   杜绝 `.env`、`data/.key`、`data/sessions/*.json` 被下载。
4. 简历/材料/截图落盘前经 `core.secure_store` 加密（Fernet），预览时按 `ENC1:` 魔数解密。
5. 上传仅放行白名单扩展名与体积上限；文件名经 `Path(...).name` + 字符替换，无路径穿越。
6. 沙箱内 `git` 调用使用参数数组（`shell=False`），路径先过沙箱再交给 git。

## 7. 本机验证

```powershell
$env:PYTHONPATH="E:\ai\ai job\agent\backend"
python -m pyflakes backend\db.py backend\models backend\schemas backend\services backend\api backend\main.py
python tests\test_smoke.py          # 既有业务核心冒烟（77 项）
python scripts\smoke_api.py         # 接口级冒烟
python scripts\audit_brand.py       # 品牌字样审计
python scripts\audit_secrets.py     # 密钥/隐私审计
```
