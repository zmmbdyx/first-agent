# 拓径 PATHFORGE — 架构契约（冻结版）

> ## ⚠️ 本轮加固增量（优先于下方正文，详见 `docs/HARDENING.md`）
>
> 1. **鉴权**：新增全局中间件（fail-closed）。`AUTH_ENABLED=true` 时 `/api/*`、`/metrics` 需凭据；
>    `/api/health` 公开。凭据来源：`Authorization: Bearer <token>` 或 `X-API-Key: <token>`；
>    **只读 GET 额外接受 `?token=`**（供 `<img src>`/直链预览），WebSocket 用 `?token=`。
>    失败：401（未带凭据）/ 403（凭据无效）。
> 2. **owner（多租户）**：`sessions.owner`、`runs.owner`、`feedback.owner` 三处新增；
>    会话域读操作把"非本 owner"视为**不存在**（404）。`owner` 由令牌映射（`API_KEYS=token=owner`）决定。
> 3. **新增端点**：
>    - `POST /api/sessions/{id}/feedback` → `{ok, id, session_id, run_id, rating, comment, message_ts, created_at}`
>    - `GET /api/sessions/{id}/feedback` → `{items:[...], summary:{count, average, distribution}}`
>    - `GET /metrics` → Prometheus 文本格式
> 4. **运行预算/超时**：`MAX_TOKENS_PER_RUN` 超限或 `TASK_TIMEOUT` 超时 → 事件序列
>    `error` → `interrupted`（reason 说明原因），run 归档为 `interrupted`；**`TASK_TIMEOUT` 语义
>    由"沙箱单命令超时"修正为"单次运行墙钟超时"**（沙箱改用 `SANDBOX_TIMEOUT`）。
> 5. **队列上限**：`MAX_QUEUE_SIZE` 满时 `POST /api/agent/run` 在建流前返回 `429` + `Retry-After`。
> 6. **不可信内容边界**：所有外部文本必须经 `core/prompts.py::wrap_untrusted(text, source)`
>    包裹为 `<user_data source="…" trust="untrusted">…</user_data>` 后方可进入 LLM 调用。
> 7. **新增 run 字段**：`owner`、`prompt_version`；**新增表** `feedback`。
>
> ---

> 本文件是前后端并行开发的**唯一接口基线**。任何实现都必须严格遵循此处的字段名、事件名、路径与方法签名；如需变更，先改本文件。

- 项目名：**拓径 / PATHFORGE**
- 定位：求职业务域智能体平台（任务规划 → 工具调用 → 多步执行 → 状态管理），前后端分离
- 后端：Python 3.10+ / FastAPI / LangGraph / SQLAlchemy / Redis / ChromaDB
- 前端：React 18 + TypeScript + Vite + Tailwind CSS + zustand + @tanstack/react-query
- 品牌约束：界面、Logo、文案、配置占位符中**不得出现任何第三方品牌名称**（模型厂商名一律用中性占位）

---

## 0. 目录结构（冻结）

```
backend/
├── main.py                  # FastAPI 入口（lifespan：建表 / 队列工作线程 / 事件总线）
├── config.py                # pydantic-settings 全局配置（含兼容别名 load_config/Config）
├── db.py                    # 引擎 / SessionLocal / Base / init_db
├── api/
│   ├── __init__.py          # APIRouter 汇总
│   ├── agent.py             # /api/agent/run（SSE）、中断、回滚
│   ├── sessions.py          # 会话 CRUD + 历史
│   ├── tools.py             # 工具注册/列表/卸载（MCP 兼容）
│   ├── workspaces.py        # 工作区 CRUD
│   ├── files.py             # 工作区文件树/内容（沙箱内）
│   ├── git.py               # 工作区 Git 变更 / stage / unstage
│   ├── assets.py            # 简历库 / 材料上传 / 截图 OCR / 模型列表
│   └── ws.py                # WebSocket /ws/agent/{session_id}
├── core/
│   ├── agent.py             # ★ 既有 JobAgent 编排器（业务核心，逻辑保持不变）
│   ├── planner.py llm.py memory.py prompts.py schemas.py cache.py profile.py secure_store.py  # ★ 既有
│   ├── tools/               # ★ 既有 9 个内置工具
│   ├── graph.py             # ▲ 新增：LangGraph StateGraph 编排（ReAct / 子 Agent / 中断恢复）
│   ├── state.py             # ▲ 新增：AgentState TypedDict + reducer
│   ├── checkpointer.py      # ▲ 新增：检查点（内存 / SQLAlchemy 快照）
│   ├── presets.py           # ▲ 新增：预设 standard/minimal/ptc/creative
│   ├── permissions.py       # ▲ 新增：权限模式 read_only/workspace_write/full_access 解析
│   ├── sandbox.py           # ▲ 新增：沙箱执行环境（路径限定 + 超时 + 命令白名单）
│   ├── queue.py             # ▲ 新增：优先级任务队列
│   ├── metrics.py           # ▲ 新增：Token / TPS / 缓存命中率 / 上下文占用采集
│   ├── longterm.py          # ▲ 新增：ChromaDB 长期记忆（不可用时降级为本地文件）
│   ├── redis_memory.py      # ▲ 新增：Redis 短期记忆（不可用时降级为内存 dict）
│   ├── events.py            # ▲ 新增：事件类型常量 + EventBus 桥接（SSE/WS 共用）
│   └── legacy/              # 旧单体实现（server.py / cli.py / static/），仅供追溯
├── models/                  # SQLAlchemy 模型
├── schemas/                 # Pydantic 请求/响应模型
├── services/                # 业务逻辑层
├── .env.example
├── requirements.txt
└── README.md
frontend/
├── index.html  package.json  vite.config.ts  tsconfig.json  tailwind.config.js  postcss.config.js
└── src/{main.tsx,App.tsx,index.css,components/,store/,api/,types/,lib/}
```

---

## 1. 环境变量（.env.example 的唯一真源）

变量名固定如下，取值全部为中性占位符（**不得出现厂商品牌名**）：

```
# ---- 应用 ----
APP_NAME=PATHFORGE
APP_ENV=development
API_HOST=127.0.0.1
API_PORT=8000
CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173

# ---- LLM 配置（OpenAI 兼容协议，任选端点）----
LLM_PROVIDER=openai-compatible
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://your-llm-endpoint/v1
LLM_MODEL=your-model-name
LLM_MODELS=your-model-name,your-backup-model
LLM_FALLBACK_MODEL=
LLM_TEMPERATURE=0.3
MAX_REACT_STEPS=5
LLM_MAX_RETRIES=3
TOOL_MAX_RETRIES=2
MAX_TASKS=6
TASK_TIMEOUT=300
PRIVACY_MODE=false

# ---- 数据库 ----
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/pathforge
SQLITE_FALLBACK_URL=sqlite:///./data/pathforge.db

# ---- Redis（短期记忆 / 任务队列；留空自动降级进程内实现）----
REDIS_URL=redis://localhost:6379/0

# ---- 向量库（长期记忆；留空自动降级本地文件）----
VECTOR_STORE_URL=http://localhost:8000
VECTOR_STORE_PATH=./data/vectors
EMBEDDING_MODEL=your-embedding-model

# ---- 沙箱 ----
SANDBOX_ENABLED=true
SANDBOX_TIMEOUT=300
SANDBOX_MAX_OUTPUT=65536
WORKSPACE_ROOT=./workspaces
ALLOW_FULL_ACCESS=false

# ---- 联网检索（可选）----
SEARCH_API_KEY=

# ---- 数据加密 ----
DATA_KEY=
```

约定：
- `DATABASE_URL` 为空 → 回落 `SQLITE_FALLBACK_URL`（开箱即跑）；以 `postgresql+psycopg://` 开头时按 PostgreSQL 建 JSONB/UUID 索引。
- `REDIS_URL` 连接失败 → 自动降级为进程内字典，**不得抛异常阻断启动**。
- `VECTOR_STORE_URL` 不可达 → 降级 `VECTOR_STORE_PATH` 本地 JSON 向量文件。
- 所有敏感项**只从环境变量读取**，代码中零硬编码。

---

## 2. 后端 API（冻结）

统一前缀 `/api`，CORS 允许 `CORS_ORIGINS`。错误统一返回 `{"detail": "..."}` + 恰当状态码。

### 2.1 Agent 执行

**POST `/api/agent/run`** → `text/event-stream`

请求体：
```json
{
  "task": "帮我分析这份 JD 并匹配简历",
  "session_id": "a1b2c3d4e5f6",
  "preset": "standard",
  "workspace": "default",
  "permission_mode": "workspace_write",
  "model": "",
  "reasoning_effort": "medium",
  "priority": 5,
  "resume": false
}
```
- `preset` ∈ `standard | minimal | ptc | creative`（缺省 `standard`）
- `permission_mode` ∈ `read_only | workspace_write | full_access`（缺省 `workspace_write`，且受 `ALLOW_FULL_ACCESS` 约束）
- `reasoning_effort` ∈ `low | medium | high`
- `session_id` 为空 → 服务端新建会话，并在首个事件回传

响应：`text/event-stream`，每条事件形如
```
event: tool_call
data: {"type":"tool_call", ...}

```
（`event:` 名与 `data.type` 必须一致；`data` 为单行 JSON，`ensure_ascii=false`）

**POST `/api/agent/runs/{run_id}/interrupt`** → `{"ok": true, "run_id": "..."}`
**POST `/api/sessions/{session_id}/rollback`** body `{"checkpoint_id": ""}` → 回滚到指定检查点（空则回滚到本 run 起点）
**GET `/api/agent/runs/{run_id}`** → run 状态与统计

### 2.2 SSE 事件字典（冻结，前端按此实现）

| event | 载荷字段 | 说明 |
|---|---|---|
| `run_started` | `run_id, session_id, model, preset, permission_mode, workspace` | 首个事件 |
| `session_info` | `session_id, status, title` | 会话标识（新建会话时前端据此切换） |
| `queue_position` | `run_id, position, priority` | 排队中 |
| `user_message` | `content` | 回显 |
| `node_start` | `node_id, node, label, seq` | 执行节点开始（轨迹读条起点） |
| `node_end` | `node_id, node, status, elapsed_ms, tokens{...}, error` | 执行节点结束 |
| `plan_created` | `goal, tasks[], costs{}` | 任务计划 |
| `task_start` | `task_id, title, tool` | 子任务开始 |
| `thought` | `task_id, step, thought` | 思考内容 |
| `tool_call` | `call_id, task_id, tool, args{}, cost` | 工具调用开始 |
| `tool_result` | `call_id, task_id, tool, brief, elapsed_ms, ok=true` | 工具成功 |
| `tool_error` | `call_id, task_id, tool, error, elapsed_ms, ok=false` | 工具失败 |
| `retry` | `task_id, tool, attempt, error` | 重试 |
| `task_finish` | `task_id, status(done/failed/skipped), result_brief, error, elapsed_ms` | 子任务结束 |
| `ask_user` | `task_id, question` | 需要用户补充信息（图中断） |
| `security_block` | `reason, message` | 敏感信息拦截 |
| `facts_updated` | `facts{}` | 画像记忆更新 |
| `metric` | `tps, llm_ms, prompt_tokens, completion_tokens, total_tokens, cached_tokens, cache_hit_rate, context_tokens, context_window, context_ratio, llm_calls, tool_calls` | **实时指标**（每次 LLM 调用后推送） |
| `trajectory` | `nodes[{node_id,node,label,seq,status,elapsed_ms,tokens,thoughts[],tool_calls[],error}]` | 轨迹快照（增量推送整份） |
| `final_answer` | `content, chart, stats{...}` | 最终答复（Markdown） |
| `run_done` | `stats{...}` | 收尾 |
| `interrupted` | `run_id, reason` | 已中断 |
| `rolled_back` | `checkpoint_id` | 已回滚 |
| `error` | `message` | 错误 |
| `heartbeat` | `ts` | 每 15s 保活 |

`stats` 结构（`final_answer` / `run_done` 共用）：
```json
{"llm_calls":9,"tool_calls":6,"tool_retries":0,"failed_tasks":[],
 "elapsed_s":42.1,"high_cost_calls":1,"llm_degraded":false,"cache_disk_items":12,
 "prompt_tokens":8123,"completion_tokens":2310,"total_tokens":10433,
 "cached_tokens":5120,"cache_hit_rate":0.63,"tps":38.4,"context_tokens":9400,
 "context_window":65536,"context_ratio":0.143,"nodes":7}
```

### 2.3 会话

- **POST `/api/sessions`** body `{"title?":"", "workspace?":"default", "preset?":"standard"}` → `SessionOut`
- **GET `/api/sessions?workspace=&limit=50&offset=0&q=`** → `{"items":[SessionOut...],"total":n}`，按 `pinned desc, updated_at desc`
- **GET `/api/sessions/{id}/history`** → `{"session":SessionOut,"messages":[...],"tasks":[...],"tool_calls":[...],"trajectory":[...],"stats":{...}}`
- **DELETE `/api/sessions/{id}`** → `{"ok":true}`
- **PUT `/api/sessions/{id}/title`** body `{"title":"..."}` → `SessionOut`
- **PUT `/api/sessions/{id}/pin`** body `{"pinned":true}` → `SessionOut`

`SessionOut`：`{id,title,status,workspace,preset,permission_mode,model,pinned,created_at,updated_at,message_count,token_stats{},facts{}}`

### 2.4 工具（MCP 兼容）

- **GET `/api/tools`** → `{"items":[ToolOut...],"mcp":{"protocol":"mcp/1.0","tools_endpoint":"/api/tools"}}`
- **POST `/api/tools`** body（MCP 工具描述 + 绑定方式）：
```json
{"name":"my_tool","description":"...","input_schema":{"type":"object","properties":{}},
 "kind":"http","config":{"url":"https://...","method":"POST","headers":{}},"enabled":true}
```
  `kind` ∈ `http | python | mcp`；`name` 冲突返回 409
- **DELETE `/api/tools/{name}`** → 卸载（内置工具不可卸载，返回 403）
- **POST `/api/tools/{name}/test`** body `{"args":{}}` → `{"ok":true,"data":{}}`

`ToolOut`：`{name,description,input_schema,cost,avg_seconds,source:"builtin"|"custom",enabled,kind}`

### 2.5 工作区

- **POST `/api/workspaces`** `{"name":"默认工作区","path":"./workspaces/default","description":""}` → `WorkspaceOut`
- **GET `/api/workspaces`** → `{"items":[WorkspaceOut...]}`
- **DELETE `/api/workspaces/{id}`**、**PUT `/api/workspaces/{id}`**
- `WorkspaceOut`：`{id,name,path,description,created_at,updated_at,exists,file_count}`

### 2.6 文件 / Git（严格限定在工作区内）

- **GET `/api/files/tree?workspace=&path=&depth=3`** → `{"root":"...","nodes":[{"name","path","type":"file|dir","size","mtime","children":[]}]}`
- **GET `/api/files/content?workspace=&path=`** → `{"path","type":"text|markdown|image|pdf|binary","content","language","size","truncated"}`
- **GET `/api/files/raw?workspace=&path=`** → 原始字节（图片/PDF 预览）
- **GET `/api/git/status?workspace=`** → `{"is_repo":true,"branch":"main","changes":[{"path","status":"M|A|D|??","staged":false}]}`
- **POST `/api/git/stage`** `{"workspace":"","paths":["a.md"]}` → `{"ok":true,"changes":[...]}`
- **POST `/api/git/unstage`** 同上
- **安全**：所有 `path` 必须 `resolve()` 后位于工作区根内，否则 400；禁止 `..`、绝对路径、符号链接逃逸（参考 legacy/server.py 的 `ReportStaticFiles` 白名单做法）。

### 2.7 业务资产（求职域必需，保留旧能力）

- **GET `/api/resumes`**、**POST `/api/resumes/upload`**（multipart）、**DELETE `/api/resumes/{name}`**
- **POST `/api/upload`**（通用材料）、**POST `/api/ocr`**（截图识别）
- **GET `/api/models`**、**POST `/api/model/select`**
- **GET `/api/health`** → `{status,provider,model,tools,db,redis,vector_store,sandbox,encrypted_storage,cache}`
- **GET `/api/agent/presets`** → `{"items":[{"id":"standard","name":"标准","description":"...","params":{}}]}`
- **GET `/api/files/report?path=`** → 报告产物（限定 `data/reports/` 白名单）

### 2.8 WebSocket

**`/ws/agent/{session_id}`**
- 服务端推送：`{"type":"tool_status","call_id","tool","status":"running|ok|error","elapsed_ms","tokens"}`、`{"type":"metric", ...}`（同 SSE `metric`）、`{"type":"node", ...}`、`{"type":"pong"}`
- 客户端可发：`{"type":"ping"}`、`{"type":"subscribe","run_id":"..."}`
- 与 SSE 并行，用于工具调用状态的实时推送；连接不上不得影响 SSE 主链路。

---

## 3. LangGraph 编排契约（`core/graph.py`）

对外唯一入口：
```python
def build_agent_graph(agent, checkpointer=None) -> CompiledStateGraph
async def astream_run(agent, session, task_text, config) -> AsyncIterator[dict]  # 产出 SSE 事件 dict
```

- `StateGraph(AgentState)`，节点（`node` 名固定，前端轨迹读条按此显示）：
  1. `recall` 记忆召回（会话事实 + 长期记忆）
  2. `plan` 任务规划（调用既有 `agent._make_plan`）
  3. `dispatch` 依赖调度（既有 `_execute_plan` 的依赖/条件逻辑）
  4. `react` 单任务 ReAct（调用既有 `agent._run_task`）
  5. `await_user` 图中断（`langgraph.types.interrupt`）承接 `ask_user`
  6. `synthesize` 综合报告（既有 `agent._finalize`）
  7. `finalize` 收尾统计
- 条件边：`react → dispatch`（还有就绪任务）、`react → synthesize`（全部结束）、`dispatch → await_user`（挂起）、`await_user → react`（resume）
- **必须保留既有业务逻辑**：规划、ReAct 步进、工具重试/熔断、产物接线（artifacts）、PTC 校验等一律复用 `core/agent.py` 的现有方法，图节点只做编排与轨迹记录，不得重写业务规则。
- 每个节点执行前后发射 `node_start` / `node_end`，并写入 `trajectory` 表。
- 子 Agent 委派：`react` 节点内若决策为 `delegate`（或预设 `ptc` 的校验节点不通过），以子图（`graph.add_node("subagent", ...)` / 递归 `react`）方式执行，轨迹上作为子节点呈现。
- 中断/恢复：`run_service.interrupt(run_id)` → 取消 asyncio 任务 + 图状态落检查点；`resume=True` 时用 `Command(resume=answer)` 续跑。
- 回滚：基于检查点快照恢复 `Session`（消息/任务/artifacts）到 run 起点。

### 服务层签名（冻结）

```python
# services/run_service.py
class RunService:
    def __init__(self, cfg, session_service, tool_service, queue=None): ...
    async def start(self, req: RunRequest) -> RunHandle            # 入队并返回 run_id
    async def stream(self, req: RunRequest) -> AsyncIterator[dict] # SSE 事件流
    def interrupt(self, run_id: str) -> bool
    def rollback(self, session_id: str, run_id: str, checkpoint_id: str = "") -> dict
    def status(self, run_id: str) -> dict

# services/session_service.py
class SessionService:
    def create(self, title="", workspace="default", preset="standard") -> dict
    def list(self, workspace=None, limit=50, offset=0, q=None) -> tuple[list[dict], int]
    def get(self, session_id) -> dict | None
    def history(self, session_id) -> dict | None
    def rename(self, session_id, title) -> dict | None
    def set_pinned(self, session_id, pinned) -> dict | None
    def delete(self, session_id) -> bool
    def save_runtime(self, session) -> None      # JobAgent 的 Session → 落库（消息/任务/token）
    def load_runtime(self, session_id): ...      # 落库数据 → core.memory.Session

# services/tool_service.py
class ToolService:
    def list(self) -> list[dict]
    def register(self, payload: dict) -> dict
    def unregister(self, name: str) -> bool
    def test(self, name: str, args: dict) -> dict
    def registry(self): ...                      # 返回 core.tools.base.ToolRegistry

# services/workspace_service.py
class WorkspaceService:
    def create(self, name, path, description="") -> dict
    def list(self) -> list[dict]
    def get(self, ident: str) -> dict | None     # id 或 name
    def update(self, ident, **fields) -> dict | None
    def delete(self, ident) -> bool
    def resolve(self, ident: str) -> Path        # 归一化并确保目录存在且在工作区根内
```

---

## 4. 数据库模型（`models/`，表名冻结）

| 表 | 关键列 |
|---|---|
| `sessions` | id(str,PK) title status workspace preset permission_mode model pinned(bool) facts(JSON) summary(Text) token_stats(JSON) message_count(int) created_at updated_at |
| `messages` | id(int,PK) session_id(FK,idx) run_id role content(Text) ts(float) tokens(JSON) |
| `runs` | id(str,PK) session_id(idx) task(Text) preset workspace permission_mode model status started_at finished_at stats(JSON) error(Text) |
| `tasks` | id(str,PK) run_id(idx) session_id(idx) seq title detail tool status result(Text) error(Text) retries elapsed_ms tokens(JSON) |
| `tool_calls` | id(str,PK) run_id(idx) session_id(idx) task_id tool args(JSON) brief(Text) ok(bool) error(Text) elapsed_ms cached(bool) tokens(JSON) created_at |
| `trajectory_nodes` | id(str,PK) run_id(idx) session_id(idx) seq node label status elapsed_ms input(JSON) output(JSON) tokens(JSON) error(Text) created_at |
| `workspaces` | id(str,PK) name(uniq) path description created_at updated_at |
| `custom_tools` | id(str,PK) name(uniq) description input_schema(JSON) kind config(JSON) enabled(bool) created_at |

- JSON 列：PostgreSQL 用 `JSONB`，SQLite 用 `JSON`（`models/base.py` 提供 `JSONType` 变体与 `utcnow()`）。
- 所有时间列 `DateTime(timezone=True)`；`updated_at` 由服务层显式更新（会话列表按其倒序）。

---

## 5. 前端设计契约

### 5.1 设计令牌（`src/index.css` + `tailwind.config.js`）

- 主色：**纯灰阶**（无彩色点缀）；语义色仅用于状态指示，低饱和。
- 深色为默认（`<html class="dark">`），支持浅色切换（Tailwind `darkMode:'class'`）。
- 圆角 4–8px；间距紧凑；字体 `-apple-system, "Inter", "Segoe UI", system-ui, sans-serif`。

CSS 变量（两套主题同名）：
```
--pf-bg --pf-bg-elevated --pf-surface --pf-surface-hover --pf-border --pf-border-strong
--pf-text --pf-text-muted --pf-text-faint --pf-accent --pf-accent-text
--pf-ok --pf-warn --pf-err --pf-info
```
Tailwind 映射：`bg-pf-surface`、`text-pf-muted`、`border-pf-border`、`bg-pf-accent` …（`pf-*` 前缀）

### 5.2 布局（`App.tsx`）

- 三栏：左侧栏（收起 `56px` / 展开 `280px`，可折叠）+ 中央对话/任务区（自适应）+ 右侧面板（首选 `360px`，可折叠）
- `<1024px`：右侧面板自动关闭、左侧栏强制紧凑 Rail（`56px`）
- 顶部细条：项目名 + 模型 + 连接状态；底部：输入区固定

### 5.3 组件清单（文件名冻结）

| 文件 | 职责 |
|---|---|
| `components/Logo.tsx` | **原创抽象折线路径几何 Logo**（`currentColor`，`size` 属性），无任何第三方图形 |
| `components/Sidebar.tsx` | 折叠侧栏：新建会话、按工作区分组的会话列表、置顶/重命名/删除、底部设置入口 |
| `components/SessionList.tsx` | 会话分组列表与行内操作 |
| `components/SettingsModal.tsx` | 四个 Tab：通用设置 / 模型配置 / 插件管理 / Agent 预设 |
| `components/ChatArea.tsx` | 中央区：空状态 / 消息流 / 轨迹 / 流式指标 / 输入区 |
| `components/MessageList.tsx` | 用户右对齐浅灰气泡、Agent 左对齐无背景 + Markdown |
| `components/MarkdownView.tsx` | react-markdown + remark-gfm + 代码高亮（react-syntax-highlighter） |
| `components/TrajectoryView.tsx` | **执行轨迹可视化**：节点彩色读条 + 点击查看节点详情（思考/工具输入输出/耗时/Token）+ 折叠展开 |
| `components/StreamMetrics.tsx` | TPS / LLM 耗时 / 上下文占用 / 缓存命中率 / 输入输出 Token |
| `components/InputBox.tsx` | 全宽输入框、@ 引用工作区文件、上传按钮、工作区选择、预设/权限/模型/推理强度、发送⇄停止 |
| `components/RightPanel.tsx` | 右侧面板容器 + 四个 Tab：文件 / 工具 / Token / Git |
| `components/FileTreePanel.tsx` | 工作区文件树 + 预览（Markdown/代码/PDF/图片） |
| `components/ToolPanel.tsx` | 工具调用列表（输入/输出/耗时） |
| `components/TokenPanel.tsx` | 会话 Token 消耗与缓存命中率 |
| `components/GitPanel.tsx` | 工作区变更列表 + stage/unstage |
| `store/useAppStore.ts` | zustand：布局/主题/会话/运行态 |
| `store/useRunStore.ts` | zustand：SSE 事件归约（消息、任务、轨迹、指标） |
| `api/client.ts` | fetch 封装 + react-query hooks（会话/工具/工作区/文件/Git） |
| `api/stream.ts` | SSE 客户端（fetch + ReadableStream，支持 POST、中断） |
| `api/ws.ts` | WebSocket 客户端（工具状态推送，断线重连） |
| `types/index.ts` | 与后端 `schemas/` 一一对应的 TS 类型 |

### 5.4 原创文案（冻结，不得改动为品牌词）

- 空状态标题：**拓径**；副标题：`PATHFORGE · 智能任务执行台`
- 欢迎语：**开始你的智能任务** — 「描述你的目标，我会自主规划步骤、调用工具并交付结果。」
- 输入框占位：`描述任务，或输入 @ 引用工作区文件…`
- 预设：标准 / 极简 / PTC / 创造（PTC = 规划–工具–校验）
- 权限：只读 / 工作区可写 / 完全访问
- 侧栏按钮：新建会话；设置项：通用设置 / 模型配置 / 插件管理 / Agent 预设
- 执行中状态：`正在执行`、`已中断`、`已完成`、`排队中`

---

## 6. 验收标准

1. `python -m pytest`/既有 `tests/*.py` 全绿（业务核心未被破坏）
2. `uvicorn main:app` 可启动；`/api/health` 200；`/api/agent/run` 在 mock 模式下可产出完整 SSE 事件流（含 `node_start/node_end/trajectory/metric/final_answer`）
3. `npm run build`（tsc + vite）零错误
4. 全仓扫描：无 `sk-` 类密钥、无 `.env` 入库、无第三方品牌名、无硬编码连接串
5. `.env.example` 覆盖全部所需变量；`.gitignore` 覆盖 `.env`/`node_modules`/`data/` 等
