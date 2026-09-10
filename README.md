# 拓径 PATHFORGE

> **求职业务域智能体平台** —— 输入目标，Agent 自主规划步骤、调用工具、交付结果，全过程可观测。
> 前后端分离 · FastAPI + LangGraph 编排 · React 执行轨迹可视化 · 原创命名与标识

[![CI](https://github.com/zmmbdyx/first-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zmmbdyx/first-agent/actions/workflows/ci.yml)

---

## 目录

- [界面预览](#界面预览)
- [项目介绍](#项目介绍)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [LangGraph 编排](#langgraph-编排)
- [快速开始](#快速开始)
- [环境变量配置](#环境变量配置)
- [API 一览](#api-一览)
- [项目结构](#项目结构)
- [测试与验证](#测试与验证)
- [脱敏与合规](#脱敏与合规)
- [界面设计语言](#界面设计语言)
- [部署](#部署)
- [已知取舍与路线图](#已知取舍与路线图)

---

## 界面预览

> 截图由 Playwright 自动操作**真实运行中的页面**生成（非设计稿）：
> 启动后端 → 打开 `http://127.0.0.1:8000` → 发送任务 → 等待工作流跑完并截图。
> 复现：`python scripts/capture_screenshots.py`（需 `pip install playwright && playwright install chromium`）

### 1. 空状态与输入区

居中标识与欢迎语；底部输入区聚合预设、权限模式、模型与推理强度选择。

![空状态](./screenshots/01-empty-state.png)

### 2. 执行中：轨迹读条与实时指标

每个执行节点对应一条读条，宽度按耗时占比分配、明度按顺序递增；运行中显示 TPS、
LLM 耗时、上下文占用、缓存命中率与输入/输出 Token。

![执行中](./screenshots/02-running.png)

### 3. 交付结果与右侧面板

报告以 Markdown 渲染；右侧面板可切换文件树 / 工具调用 / Token 统计 / Git 变更。

![结果与面板](./screenshots/03-result-panel.png)

### 4. 浅色模式

同一套设计令牌，深浅色仅切换 CSS 变量。

![浅色模式](./screenshots/04-light.png)

### 5. 右侧详情面板：Git 变更与暂存

由 `scripts/ui_check.py` 在真实点击「勾选 → 暂存」后截图，同时断言了 `/api/git/stage` 的调用与返回。

![Git 面板](./screenshots/05-ui-check.png)

---

## 项目介绍

**拓径** 是一个面向求职场景的自主智能体平台：你给出目标（分析某份 JD、匹配简历、准备面试、
评估薪资谈判空间），它自行拆解任务、按依赖顺序调度、调用合适的工具、把中间产物在任务间
自动接线，最后产出结构化交付报告。

它由两部分组成，可独立部署、独立演进：

| | 职责 | 关键点 |
|---|---|---|
| **后端** | 任务规划、工具调用、多步执行、状态管理 | FastAPI + LangGraph 状态机；SSE 流式输出；WebSocket 推送工具状态；执行轨迹完整落库 |
| **前端** | 用户交互、过程可视化 | React 18 + TypeScript；三栏式开发者工具界面；轨迹读条、实时 Token 统计、工作区文件树与 Git 面板 |

平台能力（与业务无关的通用部分）：

- **执行轨迹可视化**：每个编排节点记录输入/输出、耗时、Token 消耗、错误信息；读条宽度按时耗占比、明度按顺序编码，点击读条查看该节点完整过程；
- **实时指标**：TPS、LLM 耗时、上下文占用、缓存命中率、输入/输出 Token 逐次刷新；
- **预设与权限**：标准 / 极简 / PTC（规划–工具–校验）/ 创造 四档预设；只读 / 工作区可写 / 完全访问 三档权限，工具执行被沙箱限定在工作区内；
- **中断、恢复与回滚**：`ask_user` 使图挂起等待补充信息，`Command(resume=...)` 续跑；任意时刻可中断，可回滚到运行起点快照；
- **任务队列**：带优先级的准入队列（Redis 可用时用 Redis，否则进程内实现），同一会话串行、跨会话并发；
- **工具可扩展**：内置 9 个求职域工具；支持注册自定义工具（HTTP / MCP 兼容描述），可动态装卸。

业务域能力（内置工具）：`web_search` 联网检索、`web_fetch` 网页抓取、`pdf_extract` /
`docx_extract` 简历解析、`image_ocr` JD 截图识别、`file_read`、`jd_analyze`（技能词频、学历、
薪资、章节结构化）、`resume_match`（加权评分 + 匹配度图表）、`write_report`。

---

## 技术栈

### 前端

| 组件 | 选型 | 用途 |
|---|---|---|
| 框架 | React 18 + TypeScript 5.6 | 组件化与类型安全 |
| 构建 | Vite 5 | 开发服务器与生产构建 |
| 样式 | Tailwind CSS 3（`darkMode: 'class'`） | 设计令牌与紧凑布局 |
| 状态 | zustand | 事件流归约（消息/任务/轨迹/指标） |
| 数据 | @tanstack/react-query | 会话、工具、工作区、文件、Git 的请求缓存 |
| 渲染 | react-markdown + remark-gfm | 报告与文档渲染 |
| 高亮 | react-syntax-highlighter | 代码块高亮 |
| 终端 | @xterm/xterm（可选） | 终端组件按需接入 |

### 后端

| 组件 | 选型 | 用途 |
|---|---|---|
| Web | FastAPI + Uvicorn | REST / SSE / WebSocket |
| 编排 | LangGraph（StateGraph） | 节点化执行、条件边、检查点、中断恢复 |
| 流式 | sse-starlette | `text/event-stream` 响应 |
| 存储 | SQLAlchemy 2.0 | 会话 / 任务 / 工具调用 / 轨迹 / 工作区（PostgreSQL 或 SQLite） |
| 短期记忆 | Redis | 会话上下文与队列（不可用时进程内降级） |
| 长期记忆 | ChromaDB | 跨会话画像向量召回（不可用时本地关键词降级） |
| 配置 | pydantic-settings | 环境变量集中管理 |
| LLM | OpenAI 兼容协议客户端 | 任何兼容端点均可，未配置时进入离线 mock 模式 |

---

## 系统架构

```
┌────────────────────────── 前端 (React 18 + Vite) ──────────────────────────┐
│  Sidebar(会话/工作区分组)  │  ChatArea(消息·轨迹·指标·输入)  │  RightPanel  │
│  新建·置顶·重命名·删除     │  TrajectoryView / StreamMetrics │  文件·工具·  │
│  设置(通用/模型/插件/预设) │  MessageList(Markdown 渲染)     │  Token·Git   │
└───────┬────────────────────────────────────────────┬──────────────────────┘
        │ POST /api/agent/run  (SSE 事件流)           │ WS /ws/agent/{sid}
        │ REST: sessions / tools / workspaces / …     │ (工具状态·指标推送)
┌───────▼────────────────────────────────────────────▼──────────────────────┐
│                        FastAPI (backend/main.py)                          │
│  api/  路由与协议转换   services/  业务服务   models/  schemas/  (数据层)  │
├───────────────────────────────────────────────────────────────────────────┤
│  RunService：会话解析 → 优先级准入 → 事件双通道转发 → 收尾落库            │
├───────────────────────────────────────────────────────────────────────────┤
│  core/graph.py ── LangGraph StateGraph                                    │
│   recall → plan → dispatch → react → (check) → synthesize → finalize      │
│                    ↑____________|        └─ await_user (interrupt/resume) │
├───────────────────────────────────────────────────────────────────────────┤
│  core/agent.py ── JobAgent（既有业务核心，逻辑未改）                      │
│   任务规划 · ReAct 步进 · 工具重试/熔断 · 产物接线 · 条件分支 · 报告综合  │
├───────────────────────────────────────────────────────────────────────────┤
│  core/tools/ ── 9 个内置工具      core/sandbox.py ── 工作区路径限定执行   │
└───────┬───────────────────────────┬──────────────────────┬────────────────┘
        │                           │                      │
   SQLAlchemy                  Redis(可选)            ChromaDB(可选)
   PostgreSQL / SQLite         短期记忆·队列          长期记忆向量召回
```

**一次执行的完整链路**

1. 前端把 `{task, session_id, preset, workspace, permission_mode, model, reasoning_effort}` POST 到 `/api/agent/run`；
2. `RunService` 解析/新建会话、登记 run、进入优先级准入队列，返回 `run_started`；
3. `astream_run` 驱动 LangGraph：`recall`（前置路由 + 画像刷新 + 长期记忆召回）→ `plan`（LLM 拆解为带依赖的子任务）→ `dispatch`（依赖满足性调度）→ `react`（单任务 ReAct：思考 → 工具 → 观察）→ `synthesize`（综合报告）→ `finalize`（统计收尾）；
4. 每个节点前后发 `node_start` / `node_end`，事件经 `EventBridge` 归一化后同时进入 SSE 主链路与 WebSocket 补充通道；
5. 工具调用、Token 用量、轨迹节点实时落库；`final_answer` / `run_done` 携带完整统计；
6. 前端用 zustand 归约事件流，渲染消息、轨迹读条与实时指标。

---

## LangGraph 编排

| 节点 | 作用 | 复用的既有逻辑 |
|---|---|---|
| `recall` | 敏感信息拦截、数据控制指令、意图分类、画像刷新、长期记忆召回 | `JobAgent._preflight` / `_refresh_facts` |
| `plan` | 目标 → 带依赖关系的子任务清单（Pydantic 校验，非法回退模板） | `JobAgent._make_plan` |
| `dispatch` | 依赖失败传播、条件分支跳过、挑选下一个就绪任务 | `JobAgent._propagate_dependencies` / `_next_runnable` |
| `react` | 单任务 ReAct 循环：思考 → 工具调用（含重试/熔断）→ 观察 | `JobAgent._run_task` |
| `check` | PTC 预设的校验节点：产物不满足契约则触发一次补救 | 新增（仅 `ptc` 预设启用） |
| `await_user` | 信息不足时挂起（LangGraph `interrupt`），用户补充后续跑 | `JobAgent._resume` |
| `synthesize` | 汇总子任务结果生成交付报告 | `JobAgent._finalize` |
| `finalize` | 统计、快照与收尾事件 | 新增 |

> **设计取舍**：业务规则（怎么规划、怎么重试、怎么接线产物）已完整存在于 `core/agent.py`，
> 编排层只决定"按什么顺序调用、怎么观测"。因此图节点全部是对既有方法的调用与轨迹记录，
> 不重写任何业务判定——否则两套规则必然随时间漂移。

**预设**

| 预设 | 行为 |
|---|---|
| 标准 `standard` | 完整流水线，步数与温度取配置默认值 |
| 极简 `minimal` | 收敛 ReAct 步数、输出精简，适合快速问答 |
| PTC `ptc` | 规划–工具–校验：启用 `check` 节点对产物做一次契约校验与补救 |
| 创造 `creative` | 提高采样温度、放宽步数，适合发散型任务 |

**权限模式**：`read_only`（只读工作区）/ `workspace_write`（可写工作区）/ `full_access`
（越出工作区，需 `ALLOW_FULL_ACCESS=true` 才生效，否则自动降级并告警）。

**中断 · 恢复 · 回滚**

- 中断：`POST /api/agent/runs/{run_id}/interrupt` 取消运行任务，已产出部分照常落库，状态置 `interrupted`；
- 恢复：`ask_user` 触发图挂起，前端把补充信息作为下一条消息发送（`resume` 语义），图从检查点续跑；
- 回滚：`POST /api/sessions/{id}/rollback` 把会话态（消息/任务/产物）恢复到运行起点快照。
  注意**只回滚会话态，不删除工作区里已落盘的文件**——静默删除用户产物比不回滚更危险。

---

## 快速开始

### 0. 前置条件

- Python 3.10+（开发环境使用 3.14）
- Node.js 18+（开发环境使用 24）
- 可选：PostgreSQL、Redis（不装也能跑，会自动降级）

### 1. 启动后端

```bash
cd backend
pip install -r requirements.txt          # 生产切 PostgreSQL：再装 requirements-postgres.txt
cp .env.example .env                     # 填入你自己的 LLM 凭据（不填则进入离线 mock 模式）
uvicorn main:app --host 127.0.0.1 --port 8000
```

后端就绪：`http://127.0.0.1:8000/api/health`，接口文档 `http://127.0.0.1:8000/docs`。

> 未配置 LLM 凭据时自动进入**离线 mock 模式**：不调用任何外部服务，用启发式模板驱动完整
> 「规划 → 工具 → 报告」链路，工具产物（技能词频、匹配分、图表）仍来自真实计算。
> 这让 `git clone` 后无需任何密钥即可跑通全部接口与界面。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev                              # http://127.0.0.1:5173（Vite 已代理 /api 与 /ws）
```

生产构建：`npm run build`（产物 `frontend/dist/`，后端检测到该目录会自动托管为静态站点，
实现单端口部署）。

### 3. 一键起（Docker Compose）

```bash
cp backend/.env.example .env             # compose 会读取其中的 LLM_* 变量
docker compose up -d --build             # 后端 + PostgreSQL + Redis
# 打开 http://127.0.0.1:8000
```

### 4. 命令行方式（可选）

```bash
python backend/cli.py "帮我分析 data/jds/jd03_数据分析师.txt，匹配简历并给面试题"   # 单轮
python backend/cli.py                                                            # 交互
```

---

## 环境变量配置

全部配置集中在 `backend/.env.example`（复制为 `.env` 后填写；`.env` 已被 `.gitignore` 排除）。
**所有取值均为中性占位符，仓库内不含任何真实凭据。**

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` / `APP_ENV` | `PATHFORGE` / `development` | 应用标识与运行环境 |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `CORS_ORIGINS` | `http://127.0.0.1:5173,…` | 允许的前端来源（逗号分隔） |
| `LLM_PROVIDER` | `openai-compatible` | 端点标识（仅作展示用标签，无厂商耦合） |
| `LLM_API_KEY` | `your_api_key_here` | 接口密钥 |
| `LLM_BASE_URL` | `https://your-llm-endpoint/v1` | 兼容协议端点 |
| `LLM_MODEL` / `LLM_MODELS` | `your-model-name` | 当前模型 / 可切换模型列表 |
| `LLM_FALLBACK_MODEL` | 空 | 主模型连续失败时的降级模型 |
| `LLM_TEMPERATURE` | `0.3` | 采样温度 |
| `MAX_REACT_STEPS` | `5` | 单任务 ReAct 最大循环次数 |
| `LLM_MAX_RETRIES` / `TOOL_MAX_RETRIES` | `3` / `2` | LLM 与工具的重试次数 |
| `MAX_TASKS` | `6` | 计划最多子任务数 |
| `TASK_TIMEOUT` | `300` | 单次运行超时（秒） |
| `MAX_CONCURRENT_RUNS` | `2` | 同时执行的 run 上限（超出排队） |
| `PRIVACY_MODE` | `false` | 隐私模式：会话不落盘 |
| `DATABASE_URL` | `postgresql+psycopg://user:password@localhost:5432/pathforge` | 生产数据库 |
| `SQLITE_FALLBACK_URL` | `sqlite:///./data/pathforge.db` | `DATABASE_URL` 为空时的回落 |
| `REDIS_URL` | `redis://localhost:6379/0` | 短期记忆与队列（连不上自动降级） |
| `VECTOR_STORE_URL` / `VECTOR_STORE_PATH` | `http://localhost:8000` / `./data/vectors` | 长期记忆向量库 |
| `EMBEDDING_MODEL` | `your-embedding-model` | 向量化模型 |
| `SANDBOX_ENABLED` / `SANDBOX_TIMEOUT` / `SANDBOX_MAX_OUTPUT` | `true` / `300` / `65536` | 沙箱开关、超时、输出截断 |
| `WORKSPACE_ROOT` | `./workspaces` | 工作区根目录（Agent 文件读写被限定在此） |
| `ALLOW_FULL_ACCESS` | `false` | 是否允许「完全访问」权限模式 |
| `SEARCH_API_KEY` / `SEARCH_API_URL` | 空 | 可选检索端点（留空走免密钥公开检索通道） |
| `DATA_KEY` | 空 | 落盘加密密钥（留空自动生成 `data/.key`，该文件不入库） |

---

## API 一览

完整契约见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。统一前缀 `/api`。

### Agent 执行

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/agent/run` | **SSE 流式执行**。请求体 `{task, session_id, preset, workspace, permission_mode, model, reasoning_effort, priority, resume}`；响应 `text/event-stream` |
| `POST` | `/api/agent/runs/{run_id}/interrupt` | 中断执行 |
| `GET` | `/api/agent/runs/{run_id}` | 运行状态与统计 |
| `GET` | `/api/agent/presets` | 预设列表 |

### 会话

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/sessions` | 创建会话 |
| `GET` | `/api/sessions?workspace=&limit=&offset=&q=` | 会话列表（置顶优先，按更新时间倒序） |
| `GET` | `/api/sessions/{id}/history` | 完整历史（消息 / 任务 / 工具调用 / 轨迹 / 统计） |
| `PUT` | `/api/sessions/{id}/title` | 重命名 |
| `PUT` | `/api/sessions/{id}/pin` | 置顶 / 取消置顶 |
| `DELETE` | `/api/sessions/{id}` | 删除 |
| `POST` | `/api/sessions/{id}/rollback` | 回滚到检查点 |

### 工具 / 工作区 / 文件 / Git

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` `POST` | `/api/tools` | 工具列表 / 注册自定义工具（MCP 兼容描述） |
| `DELETE` | `/api/tools/{name}` | 卸载工具（内置工具返回 403） |
| `POST` | `/api/tools/{name}/test` | 试运行工具 |
| `GET` `POST` | `/api/workspaces` | 工作区列表 / 创建 |
| `PUT` `DELETE` | `/api/workspaces/{id}` | 更新 / 删除 |
| `GET` | `/api/files/tree` / `/api/files/content` / `/api/files/raw` | 文件树 / 内容预览 / 原始字节 |
| `GET` | `/api/git/status` | 工作区变更 |
| `POST` | `/api/git/stage` / `/api/git/unstage` | 暂存 / 取消暂存 |

### 资产与健康检查

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` `POST` `DELETE` | `/api/resumes` … | 简历库管理 |
| `POST` | `/api/upload` / `/api/ocr` | 材料上传 / 截图文字识别 |
| `GET` `POST` | `/api/models` / `/api/model/select` | 模型列表 / 切换 |
| `GET` | `/api/health` | 依赖健康状态（数据库 / Redis / 向量库 / 沙箱） |
| `WS` | `/ws/agent/{session_id}` | 工具状态与指标实时推送 |

### SSE 事件

`run_started` · `session_info` · `queue_position` · `user_message` · `node_start` · `node_end` ·
`plan_created` · `task_start` · `thought` · `tool_call` · `tool_result` · `tool_error` · `retry` ·
`task_finish` · `ask_user` · `security_block` · `facts_updated` · `metric` · `trajectory` ·
`final_answer` · `run_done` · `interrupted` · `rolled_back` · `error` · `heartbeat`

帧格式：`event: <type>\ndata: <单行 JSON>\n\n`（`ensure_ascii=false`）。

---

## 项目结构

```
.
├── backend/                        # 后端：FastAPI + LangGraph
│   ├── main.py                     #   入口：CORS、lifespan、路由挂载、静态托管
│   ├── config.py                   #   pydantic-settings 配置（含旧字段名兼容）
│   ├── db.py                       #   引擎 / SessionLocal / Base / init_db
│   ├── cli.py                      #   命令行入口（单轮 / 交互）
│   ├── api/                        #   路由层：agent / sessions / tools / workspaces /
│   │                               #            files / git / assets / ws
│   ├── core/
│   │   ├── graph.py                #   ★ LangGraph StateGraph 编排
│   │   ├── state.py                #     AgentState 与 reducer
│   │   ├── checkpointer.py         #     检查点与快照（回滚）
│   │   ├── events.py               #     事件常量 + EventBridge（线程安全桥接）
│   │   ├── metrics.py              #     Token / TPS / 缓存命中率采集
│   │   ├── presets.py              #     预设：标准 / 极简 / PTC / 创造
│   │   ├── permissions.py          #     权限模式解析
│   │   ├── sandbox.py              #     沙箱：路径限定 + 超时 + 输出截断
│   │   ├── queue.py                #     优先级队列（Redis 可选）
│   │   ├── longterm.py             #     ChromaDB 长期记忆（可降级）
│   │   ├── redis_memory.py         #     Redis 短期记忆（可降级）
│   │   ├── agent.py                #   ★ 既有业务核心：JobAgent 编排器
│   │   ├── planner.py llm.py memory.py prompts.py schemas.py
│   │   ├── cache.py profile.py secure_store.py
│   │   └── tools/                  #   ★ 9 个内置工具 + 带重试的注册表
│   ├── models/                     #   SQLAlchemy 模型（8 张表）
│   ├── schemas/                    #   Pydantic 请求/响应模型
│   ├── services/                   #   业务服务层（含 run_service 事件流数据源）
│   ├── .env.example                #   环境变量模板（占位符，无真实值）
│   ├── requirements.txt
│   └── requirements-postgres.txt
├── frontend/                       # 前端：React 18 + TypeScript + Vite + Tailwind
│   ├── src/
│   │   ├── App.tsx                 #   三栏外壳 + 顶栏 + 运行编排
│   │   ├── components/             #   Sidebar / ChatArea / TrajectoryView / RightPanel /
│   │   │                           #   InputBox / ToolPanel / TokenPanel / GitPanel /
│   │   │                           #   SettingsModal / Logo / icons …
│   │   ├── store/                  #   zustand：UI 状态 + 运行事件归约
│   │   ├── api/                    #   REST 客户端 / SSE 解析 / WebSocket
│   │   ├── hooks/useAgentRun.ts    #   一次运行的生命周期编排
│   │   ├── types/                  #   与后端 schemas 一一对应的类型
│   │   ├── lib/utils.ts            #   格式化与样式工具
│   │   └── index.css               #   ★ 设计令牌（深/浅色）
│   ├── tailwind.config.js  vite.config.ts  package.json
├── docs/
│   ├── ARCHITECTURE.md             # ★ 前后端接口契约（冻结）
│   ├── EVALUATION.md               #   评测记录
│   └── EVALUATION_v2.md
├── scripts/
│   ├── smoke_api.py                # 端到端接口冒烟（32 项）
│   ├── ui_check.py                 # 交互层实机校验（27 项，Playwright 真实点击）
│   ├── capture_screenshots.py      # Playwright 截图
│   ├── audit_secrets.py            # 密钥/隐私审计（含 git 历史）
│   └── audit_brand.py              # 第三方品牌字样审计
├── tests/
│   ├── test_smoke.py               # 业务核心冒烟（77 项，离线）
│   ├── test_schemas.py             # 结构化输出校验（33 项）
│   └── batch_eval.py               # 10 份真实风格 JD 批量评测
├── data/                           # 运行时数据（会话/上传/缓存/报告，已 gitignore）
├── workspaces/                     # Agent 工作区（已 gitignore）
├── Dockerfile  docker-compose.yml  .dockerignore
└── .github/workflows/ci.yml
```

---

## 测试与验证

```bash
# 业务核心（离线 mock，无需任何密钥）
python tests/test_smoke.py          # 77 项：规划/工具/记忆/加密/缓存/故障注入/对抗样本
python tests/test_schemas.py        # 33 项：LLM 输出作为不可信输入的边界与降级
python tests/batch_eval.py          # 10 份 JD 全链路批量评测

# 端到端接口（含真实 SSE 流与 WebSocket）
set PYTHONPATH=backend              # Windows；Linux/macOS 用 export
python scripts/smoke_api.py         # 32 项：契约端点 + 事件序列 + 路径穿越防护

# 交互层实机校验（需后端已启动且前端已构建）
python scripts/ui_check.py          # 27 项：真实点击侧栏/设置四 Tab/右侧四面板/输入区

# 脱敏与合规审计
python scripts/audit_secrets.py --repo .    # 密钥/私有端点/个人敏感信息（含全历史）
python scripts/audit_brand.py --repo .      # 第三方品牌字样（含全历史）

# 前端
cd frontend && npx tsc --noEmit && npm run build
```

CI（`.github/workflows/ci.yml`）分四个作业：后端测试、前端构建与类型检查、交互层实机校验、脱敏审计。

> **为什么需要 `ui_check.py`**：类型检查与构建通过并不代表交互接通——
> 实测中"打开设置弹窗整页崩成空白"（后端 `/api/health` 返回结构化对象，前端按字符串渲染）
> 这类缺陷只有在真实点击时才会暴露。该脚本用 DOM 状态 + 网络请求双重断言，
> 覆盖会话置顶/重命名/删除、设置四 Tab、文件树展开与预览、Git 勾选暂存、
> `@` 引用浮层、参数下拉、布局折叠与深/浅色切换。

---

## 脱敏与合规

提交前完成的处理，以及可复现的验证方式：

1. **环境变量提取**：API Key、数据库/Redis/向量库连接串、沙箱参数、加密密钥全部改为从环境变量读取；
   代码内零硬编码。`backend/.env.example` 列出全部变量名与中性占位值。
2. **`.gitignore`**：`.env`、`__pycache__/`、`node_modules/`、`.venv/`、`*.db`、`uploads/`、
   `workspaces/`、`data/sessions*`、`data/reports`、`*.log`、`dist/`、`.idea/`、`.vscode/` 等均在排除之列。
3. **密钥审计**：`scripts/audit_secrets.py` 覆盖工作树与 **git 全历史**（把密钥从最新提交删掉
   并不能让它从历史里消失），带白名单以避免误报测试夹具中的假密钥。
4. **品牌审计**：`scripts/audit_brand.py` 扫描项目命名、界面文案、注释、文档叙述与配置取值中的
   第三方品牌字样。判定策略显式、可审计：
   - **禁止**：模型厂商、Agent 产品、检索服务等品牌名出现在命名/文案/配置取值中；
   - **允许**（白名单）：依赖清单里的**包名**（技术依赖标识）、`import` 语句、
     以及 "OpenAI 兼容协议 / `openai-compatible`" 这类**协议描述**——它是行业通用的接口兼容性说法，
     本项目的实际取值一律为中性占位符。
   - **范围**：CI 只扫**工作树**。重构前的历史提交（v1 单体版本）里存在早期配置示例中的厂商名，
     那是既成历史、不影响当前发布内容；而密钥审计（上面第 3 条）**必须**覆盖全历史，
     因为"从最新提交删掉密钥"并不能让它从 `git log -p` 里消失。
     如需连历史一并中和，可在获得仓库协作者同意后执行
     `git filter-repo --replace-text <规则文件>` 并强制推送（会改写全部提交 SHA）。
5. **样例数据**：示例简历与 JD 中的模型产品名已替换为中性表述（"大模型 / LLM"）；
   输出侧脱敏不再枚举任何厂商域名，改为「通用密钥特征 + 运行期实际端点（由 `LLM_BASE_URL` 动态生成）」。
6. **原创性**：项目名称「拓径 / PATHFORGE」、折线路径标识、欢迎语与全部界面文案均为原创，
   界面中不含任何第三方品牌名称或图形。

---

## 界面设计语言

- **三栏式**：左侧栏（收起 `56px` / 展开 `280px`，可折叠）＋ 中央对话/任务区（自适应）＋
  右侧详情面板（`360px`，可折叠）；
- **响应式**：视口 < 1024px 时右侧面板自动关闭、左侧栏强制紧凑 Rail；
- **极简黑白灰**：主色为零彩色灰阶；语义色（成功/警告/失败/信息）仅用于状态指示，低饱和；
- **小圆角**：`4–8px`；间距紧凑，信息密度贴近开发者工具；
- **字体**：`-apple-system, Inter, "Segoe UI", system-ui, sans-serif`；数字与路径一律等宽；
- **主题**：默认深色，浅色一键切换；仅切换 CSS 变量（`--pf-*`），组件层无硬编码颜色；
- **动效**：仅用于状态表达（执行中呼吸、流式光标、淡入），不做装饰性动画。

---

## 部署

### 裸机 / 云服务器

```bash
cd backend && pip install -r requirements.txt -r requirements-postgres.txt
uvicorn main:app --host 0.0.0.0 --port 8000      # 建议用 systemd/supervisor 守护
cd ../frontend && npm ci && npm run build        # 后端会自动托管 frontend/dist
```

生产建议：前置反向代理做 TLS 与静态缓存；`DATABASE_URL` 指向 PostgreSQL；`REDIS_URL` 指向 Redis；
`ALLOW_FULL_ACCESS=false`；密钥通过密钥管理服务注入而非明文 `.env`。

### Docker

```bash
docker build -t pathforge .
docker run -d -p 8000:8000 --env-file backend/.env -v ./data:/app/data -v ./workspaces:/app/workspaces pathforge
```

镜像为多阶段构建（Node 构建前端 → Python 运行时托管后端与前端产物），单端口对外。

> 安全提醒：`.env` 已被 `.gitignore` 与 `.dockerignore` 双重排除，不会进入仓库与镜像。

---

## 已知取舍与路线图

**已知取舍**

- 前端代码高亮分块约 1.5 MB（gzip 521 KB）：`react-syntax-highlighter` 默认打包全量语言；
  已通过 `manualChunks` 独立分块，后续可改为按需注册语言进一步瘦身；
- 本地开发默认 SQLite：模型与查询按 PostgreSQL 设计（JSONB、时区感知时间列、索引），
  但本机无 PostgreSQL 实例时无法验证 PG 专有路径，CI 亦只覆盖 SQLite；
- 回滚只恢复会话态，不删除工作区产物（见"中断·恢复·回滚"）；
- `data/` 下的旧版会话存档会在首次启动时导入数据库（`SessionService.import_legacy`）。

**路线图**

| 方向 | 项 |
|---|---|
| 执行 | 断点续跑的可视化控制（当前 `ask_user` 已支持挂起与恢复） |
| 执行 | 多 Agent 并行分工与结果合并 |
| 记忆 | 多版本画像对比、行为图谱、跨会话行动项跟踪 |
| 报告 | 模板切换（简洁/详细/HR 视角）、多岗位横向对比 |
| 交互 | 语音输入、快捷键体系、命令面板 |
| 运维 | 用户与配额、多租户隔离、OpenTelemetry 链路追踪 |

---

## 许可

仅供学习与个人使用。示例简历与 JD 均为虚构数据。
