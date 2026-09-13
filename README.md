<div align="center">

# 拓径 PATHFORGE

**求职业务域自主智能体平台**

输入目标 → Agent 自主规划 → 调用工具 → 交付结构化报告，全过程可观测

[![CI](https://github.com/zmmbdyx/first-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zmmbdyx/first-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-1C3C3C?logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-学习与个人使用-lightgrey)](#许可)

</div>

---

## 项目简介

**拓径** 是一个面向求职场景的自主智能体平台。你给出目标——分析某份 JD、匹配简历、准备面试、
评估薪资谈判空间——它自行拆解任务、按依赖顺序调度、调用合适的工具、把中间产物在任务间自动接线，
最后产出结构化交付报告。

与传统「问答式」AI 应用的关键差异：

- **不是一次生成，而是多步执行** —— 任务被拆成带依赖关系的子任务图，逐步推进、失败可重试可熔断；
- **全过程可观测** —— 每个编排节点的输入/输出、耗时、Token 消耗、错误完整落库，前端可视化；
- **开箱即跑** —— 未配置 LLM 凭据时自动进入**离线 mock 模式**，无需任何密钥即可跑通全部接口与界面；
- **外部依赖可选** —— PostgreSQL / Redis / ChromaDB 全部可缺省，缺失时自动降级而非启动失败。

---

## 核心特性

- 🧠 **智能体编排（LangGraph 状态机）**
  执行流水线为 `recall → plan → dispatch → react → synthesize → finalize`，
  支持条件边、检查点快照、`interrupt` 中断与 `Command(resume=...)` 续跑。

- 🔍 **RAG 长期记忆与检索增强**
  基于 ChromaDB 的跨会话画像向量召回：`recall` 节点自动召回历史偏好并注入提示词，
  支撑「记得你上次说想投大模型岗」这类跨会话上下文。向量库不可用时降级为本地关键词匹配。

- 🖥️ **前后端分离**
  FastAPI 提供 REST / SSE / WebSocket 三套协议；React 18 + TypeScript 独立构建。
  前端产物由后端托管时可实现**单端口部署**，也可完全独立部署。

- 📊 **执行轨迹可视化**
  每个编排节点对应一条读条（宽度按耗时占比、明度按执行顺序编码），
  点击可查看该节点的完整输入输出；TPS、LLM 耗时、上下文占用、缓存命中率实时刷新。

- 🧰 **9 个内置工具 + 可扩展工具系统**
  `web_search` / `web_fetch` / `pdf_extract` / `docx_extract` / `image_ocr` / `file_read` /
  `jd_analyze` / `resume_match` / `write_report`；支持注册自定义工具（HTTP / MCP 兼容描述）并动态装卸。

- 🎛️ **预设与权限双维度控制**
  四档预设（标准 / 极简 / PTC 规划–工具–校验 / 创造）× 三档权限
  （只读 / 工作区可写 / 完全访问），工具执行被沙箱限定在 `WORKSPACE_ROOT` 内。

- 🔐 **安全与多租户**
  fail-closed 全局鉴权中间件、按 `owner` 的会话级租户隔离（越权一律 404 不泄漏存在性）、
  运行 Token 预算护栏、准入队列上限、提示词注入边界（不可信数据显式包裹并转义定界符）。

- 💾 **中断 · 恢复 · 回滚**
  信息不足时 `ask_user` 挂起等待补充，补充后从检查点续跑；任意时刻可中断；
  可回滚会话态到运行起点快照。

- 🔒 **落盘加密与隐私模式**
  会话与上传材料经 Fernet 加密落盘（密钥自动生成于 `data/.key`）；
  `PRIVACY_MODE=true` 时会话完全不落盘。

---

## 技术栈

### 后端

| 组件 | 选型 | 用途 |
|---|---|---|
| Web 框架 | FastAPI + Uvicorn | REST / SSE / WebSocket |
| 编排引擎 | LangGraph（StateGraph） | 节点化执行、条件边、检查点、中断恢复 |
| 流式输出 | FastAPI `StreamingResponse` | 手写 `text/event-stream`，无额外 SSE 依赖 |
| 数据校验 | Pydantic 2 + pydantic-settings | 请求/响应模型与环境变量集中管理 |
| 存储 | SQLAlchemy 2.0 | 会话/任务/工具调用/轨迹/工作区（PostgreSQL 或 SQLite） |
| 短期记忆 | Redis（可选） | 优先级任务队列与上下文（不可用时进程内降级） |
| 长期记忆 | ChromaDB（可选） | 跨会话画像向量召回（不可用时本地关键词降级） |
| LLM 客户端 | OpenAI 兼容协议 | 任何兼容端点均可，未配置时进入离线 mock 模式 |
| 文档解析 | pdfplumber / python-docx / rapidocr-onnxruntime | PDF / Word 简历解析与截图 OCR |
| 数据分析 | jieba / numpy / matplotlib | 技能词频、加权评分、匹配度雷达图 |

### 前端

| 组件 | 选型 | 用途 |
|---|---|---|
| 框架 | React 18 + TypeScript 5.6 | 组件化与类型安全 |
| 构建 | Vite 5 | 开发服务器与生产构建 |
| 样式 | Tailwind CSS 3 | 设计令牌与紧凑布局（`darkMode: 'class'`） |
| 状态管理 | zustand | 事件流归约（消息/任务/轨迹/指标） |
| 数据请求 | @tanstack/react-query | 会话、工具、工作区、文件、Git 的请求缓存 |
| Markdown | react-markdown + remark-gfm | 报告与文档渲染 |
| 代码高亮 | react-syntax-highlighter | 按需注册 17 种语言的 `prism-light` |

---

## 系统架构

```mermaid
flowchart TB
    subgraph FE["前端 · React 18 + TypeScript + Vite"]
        direction LR
        SB["Sidebar<br/>会话 / 工作区<br/>置顶·重命名·删除"]
        CA["ChatArea<br/>MessageList · TrajectoryView<br/>StreamMetrics · InputBox"]
        RP["RightPanel<br/>文件树 · 工具调用<br/>Token 统计 · Git 变更"]
    end

    subgraph API["FastAPI · backend/main.py"]
        direction LR
        MW["鉴权中间件<br/>fail-closed"]
        RT["api/ 路由层<br/>agent · sessions · tools<br/>workspaces · files · git · assets"]
        SV["services/ 服务层<br/>RunService 事件双通道转发"]
    end

    subgraph CORE["core/ · LangGraph 编排"]
        direction LR
        G["graph.py<br/>StateGraph"]
        AG["agent.py<br/>JobAgent 业务核心"]
        TL["tools/<br/>9 个内置工具"]
        SDX["sandbox.py<br/>路径限定执行"]
        LTM["longterm.py<br/>RAG 向量召回"]
    end

    subgraph DATA["存储层"]
        direction LR
        SQLDB[("SQLAlchemy<br/>PostgreSQL / SQLite")]
        RDS[("Redis<br/>队列 · 短期记忆<br/>可选")]
        VDB[("ChromaDB<br/>长期记忆向量<br/>可选")]
    end

    FE -- "POST /api/agent/run · SSE" --> MW
    FE -- "REST /api/*" --> MW
    FE -. "WS /ws/agent 推送<br/>工具状态 · 指标" .-> MW
    MW --> RT --> SV --> G
    G --> AG
    AG --> TL
    AG --> SDX
    G --> LTM
    LTM --> VDB
    SV --> SQLDB
    SV --> RDS
    TL --> SQLDB
```

### 一次执行的完整链路

1. 前端把 `{task, session_id, preset, workspace, permission_mode, model, reasoning_effort}`
   POST 到 `/api/agent/run`；
2. `RunService` 解析/新建会话、登记 run、进入优先级准入队列，返回 `run_started`；
3. `astream_run` 驱动 LangGraph 状态机逐节点推进；
4. 每个节点前后发 `node_start` / `node_end`，事件经 `EventBridge` 归一化后同时进入
   SSE 主链路与 WebSocket 补充通道；
5. 工具调用、Token 用量、轨迹节点实时落库；`final_answer` / `run_done` 携带完整统计；
6. 前端用 zustand 归约事件流，渲染消息、轨迹读条与实时指标。

### LangGraph 编排节点

| 节点 | 作用 |
|---|---|
| `recall` | 敏感信息拦截、数据控制指令、意图分类、画像刷新、**长期记忆召回（RAG）** |
| `plan` | 目标 → 带依赖关系的子任务清单（Pydantic 校验，非法回退模板） |
| `dispatch` | 依赖失败传播、条件分支跳过、挑选下一个就绪任务 |
| `react` | 单任务 ReAct 循环：思考 → 工具调用（含重试/熔断）→ 观察 |
| `check` | PTC 预设的校验节点：产物不满足契约则触发一次补救（仅 `ptc` 预设启用） |
| `await_user` | 信息不足时挂起（LangGraph `interrupt`），用户补充后从检查点续跑 |
| `synthesize` | 汇总子任务结果生成交付报告 |
| `finalize` | 统计、快照与收尾事件 |

> **设计取舍**：业务规则（怎么规划、怎么重试、怎么接线产物）集中在 `core/agent.py`，
> 编排层只决定「按什么顺序调用、怎么观测」。图节点是对这些方法的调用与轨迹记录，
> 不重写任何业务判定——否则两套规则必然随时间漂移。

**四档预设**

| 预设 | 行为 |
|---|---|
| `standard` 标准 | 完整流水线，步数与温度取配置默认值 |
| `minimal` 极简 | 收敛 ReAct 步数、输出精简，适合快速问答 |
| `ptc` | 规划–工具–校验：启用 `check` 节点对产物做契约校验与补救 |
| `creative` 创造 | 提高采样温度、放宽步数，适合发散型任务 |

---

## 界面预览

> 截图由 Playwright 操作**真实运行中的页面**生成（非设计稿）。
> 复现：`python scripts/capture_screenshots.py`

<table>
<tr>
<td width="50%"><b>空状态与输入区</b><br/>居中标识；底部输入区聚合预设、权限模式、模型与推理强度选择<br/><img src="./screenshots/01-empty-state.png" alt="空状态"/></td>
<td width="50%"><b>执行中：轨迹读条与实时指标</b><br/>读条宽度按耗时占比、明度按顺序递增；实时显示 TPS / 上下文占用 / Token<br/><img src="./screenshots/02-running.png" alt="执行中"/></td>
</tr>
<tr>
<td width="50%"><b>交付结果与右侧面板</b><br/>报告以 Markdown 渲染；右侧可切换文件树 / 工具调用 / Token / Git<br/><img src="./screenshots/03-result-panel.png" alt="结果与面板"/></td>
<td width="50%"><b>浅色模式</b><br/>同一套设计令牌，深浅色仅切换 CSS 变量<br/><img src="./screenshots/04-light.png" alt="浅色模式"/></td>
</tr>
</table>

---

## 快速开始

### 前置条件

- **Python 3.10+**（CI 与开发环境使用 3.12 / 3.14）
- **Node.js 18+**（开发环境使用 24）
- 可选：PostgreSQL、Redis——**不装也能跑**，缺失时自动降级

---

### 方案一：本地开发（推荐首次体验）

**1. 启动后端**

```bash
git clone https://github.com/zmmbdyx/first-agent.git
cd first-agent/backend

pip install -r requirements.txt
cp .env.example .env          # 填入 LLM 凭据；不填则进入离线 mock 模式

uvicorn main:app --host 127.0.0.1 --port 8000
```

或用 CLI 入口（等价，自动设置 `PYTHONPATH`）：

```bash
python backend/cli.py --serve
```

后端就绪：`http://127.0.0.1:8000/api/health` · 接口文档 `http://127.0.0.1:8000/docs`

> **离线 mock 模式**：未配置 LLM 凭据时不调用任何外部服务，用启发式模板驱动完整的
> 「规划 → 工具 → 报告」链路；工具产物（技能词频、匹配分、图表）仍来自真实计算。
> 这让 clone 后无需任何密钥即可跑通全部接口与界面。

**2. 启动前端**（另开一个终端）

```bash
cd frontend
npm install
npm run dev                   # http://127.0.0.1:5173（Vite 已代理 /api 与 /ws）
```

**3. 生产构建（单端口部署）**

```bash
cd frontend
npm run build                 # 产物输出到 frontend/dist/
```

后端检测到 `frontend/dist/` 存在时会自动挂载为静态站点并做 SPA 回退，
因此构建后直接访问 `http://127.0.0.1:8000` 即可，无需再跑 Vite。

**4. 命令行方式（可选）**

```bash
python backend/cli.py "帮我分析 data/jds/jd03_数据分析师.txt，匹配简历并给面试题"   # 单轮
python backend/cli.py                                                            # 交互多轮
```

---

### 方案二：Docker 部署

```bash
# 1. 准备环境变量（compose 会读取其中的 LLM_* 与 DATA_KEY）
cp backend/.env.example .env

# 2. 一键起（后端 + PostgreSQL + Redis）
docker compose up -d --build

# 3. 打开界面
#    http://127.0.0.1:8000
```

镜像为**多阶段构建**：阶段一用 Node 构建前端静态产物，阶段二用 Python 运行时
托管后端与前端产物，单端口对外（`8000`）。数据库与 Redis 端口仅绑定 `127.0.0.1`。

**不使用 compose，直接跑单容器：**

```bash
docker build -t pathforge .
docker run -d -p 8000:8000 \
  --env-file backend/.env \
  -v ./data:/app/data \
  -v ./workspaces:/app/workspaces \
  pathforge
```

> **安全提醒**：`.env` 已被 `.gitignore` 与 `.dockerignore` **双重排除**，不会进入仓库或镜像。
> 镜像内不包含任何密钥，凭据一律在运行时注入。

---

### 方案三：生产部署要点

```bash
cd backend && pip install -r requirements.txt -r requirements-postgres.txt
uvicorn main:app --host 0.0.0.0 --port 8000     # 建议用 systemd / supervisor 守护

cd ../frontend && npm ci && npm run build       # 后端自动托管 frontend/dist
```

生产环境建议：

- 前置反向代理做 TLS 与静态资源缓存；
- `DATABASE_URL` 指向 PostgreSQL，并保持 `DB_FALLBACK_POLICY=deny`
  （否则连接串写错会把数据静默写进容器内的 SQLite）；
- `REDIS_URL` 指向 Redis；
- **`AUTH_ENABLED=true` 并配置 `API_KEYS`**（对外暴露时必须）；
- `ALLOW_FULL_ACCESS=false`；
- 密钥通过密钥管理服务注入，而非明文 `.env`。

---

## 环境变量配置

全部配置集中在 [`backend/.env.example`](backend/.env.example)，复制为 `.env` 后填写
（`.env` 已被 `.gitignore` 排除）。**仓库内不含任何真实凭据，全部为中性占位符。**

### 必填项（不填则进入离线 mock 模式）

| 变量 | 示例值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | `your_api_key_here` | LLM 接口密钥 |
| `LLM_BASE_URL` | `https://your-llm-endpoint/v1` | OpenAI 兼容协议端点 |
| `LLM_MODEL` | `your-model-name` | 模型名称 |

> 三者**必须同时提供**才启用真实模型；任一缺失都会自动降级为离线 mock 模式
> 并在启动日志中说明原因。

### 应用与 LLM

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` / `APP_ENV` | `PATHFORGE` / `development` | 应用标识与运行环境 |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `CORS_ORIGINS` | `http://127.0.0.1:5173,…` | 允许的前端来源（逗号分隔） |
| `LLM_PROVIDER` | `openai-compatible` | 端点标签（仅作展示，无厂商耦合） |
| `LLM_MODELS` | `your-model-name,…` | 界面顶部可切换的模型列表 |
| `LLM_FALLBACK_MODEL` | 空 | 主模型连续失败时降级到的备用模型 |
| `LLM_TEMPERATURE` | `0.3` | 采样温度 |
| `MAX_REACT_STEPS` | `5` | 单任务 ReAct 最大循环次数 |
| `LLM_MAX_RETRIES` / `TOOL_MAX_RETRIES` | `3` / `2` | LLM 与工具重试次数 |
| `MAX_TASKS` | `6` | 计划最多子任务数 |

### 存储与记忆

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user:password@localhost:5432/pathforge` | 生产数据库 |
| `SQLITE_FALLBACK_URL` | `sqlite:///./data/pathforge.db` | `DATABASE_URL` 为空时的回落 |
| `DB_FALLBACK_POLICY` | 空（按环境判定） | `allow` 回落 SQLite / `deny` 直接失败；留空时 `APP_ENV=production` 视为 `deny` |
| `REDIS_URL` | `redis://localhost:6379/0` | 队列与短期记忆（连不上自动降级） |
| `VECTOR_STORE_URL` | `http://localhost:8000` | 向量库服务地址（长期记忆） |
| `VECTOR_STORE_PATH` | `./data/vectors` | 向量库不可用时的本地降级路径 |
| `EMBEDDING_MODEL` | `your-embedding-model` | 向量化模型 |

### 沙箱与权限

| 变量 | 默认 | 说明 |
|---|---|---|
| `SANDBOX_ENABLED` | `true` | 工具执行沙箱开关 |
| `SANDBOX_TIMEOUT` / `SANDBOX_MAX_OUTPUT` | `300` / `65536` | 沙箱单命令超时与输出截断 |
| `WORKSPACE_ROOT` | `./workspaces` | 工作区根目录（Agent 文件读写被严格限定在此） |
| `ALLOW_FULL_ACCESS` | `false` | 是否允许「完全访问」权限模式 |
| `PRIVACY_MODE` | `false` | 隐私模式：会话不落盘 |

### 安全与多租户

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_ENABLED` | `false` | **对外暴露时必须开启**：`true` 时 `/api/*`、`/ws/*`、`/metrics` 均需凭据 |
| `API_KEYS` | 空 | 令牌→归属者映射，如 `token1=alice,token2=bob`；owner 决定可见范围 |
| `AUTH_TOKEN` | 空 | 单令牌快捷方式（owner 固定 `default`），仅 `API_KEYS` 为空时生效 |
| `AUTH_HEADER` | `Authorization` | 令牌请求头名（`Authorization: Bearer` 与 `X-API-Key` 都接受） |
| `DATA_KEY` | 空 | 落盘加密密钥；留空自动生成 `data/.key`（该文件不入库） |
| `SEARCH_API_KEY` | 空 | 可选检索端点密钥；留空走免密钥公开检索通道 |
| `SEARCH_API_URL` | 空 | 可选检索端点地址；需与 `SEARCH_API_KEY` **同时配置**才启用 |

### 运行预算与可观测

| 变量 | 默认 | 说明 |
|---|---|---|
| `TASK_TIMEOUT` | `300` | **单次运行的墙钟超时**（秒） |
| `MAX_CONCURRENT_RUNS` | `2` | 同时执行的 run 上限（超出进入队列） |
| `MAX_TOKENS_PER_RUN` | `0` | 单次运行 Token 上限（0 = 不限）；超限中止并置为 `interrupted` |
| `MAX_QUEUE_SIZE` | `32` | 准入队列上限，超出立即返回 `429`（不再排队堆积） |
| `CONTEXT_WINDOW` | `65536` | 真实模型上下文窗口（用于占用率与提示词预算） |
| `PROMPT_VERSION` | `v1` | 提示词版本号，随 run 落库便于回归归因 |
| `LOG_JSON` | `false` | `true` 时输出 JSON 结构化日志 |

---

## API 一览

统一前缀 `/api`，完整契约见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

### Agent 执行

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/agent/run` | **SSE 流式执行**；请求体 `{task, session_id, preset, workspace, permission_mode, model, reasoning_effort, priority, resume}`，响应 `text/event-stream` |
| `POST` | `/api/agent/runs/{run_id}/interrupt` | 中断执行 |
| `GET` | `/api/agent/runs/{run_id}` | 运行状态与统计 |
| `GET` | `/api/agent/presets` | 预设列表 |

### 会话

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` `POST` | `/api/sessions` | 列表（置顶优先）/ 创建 |
| `GET` | `/api/sessions/{id}` | 单个会话 |
| `GET` | `/api/sessions/{id}/history` | 完整历史（消息 / 任务 / 工具调用 / 轨迹 / 统计） |
| `PUT` | `/api/sessions/{id}/title` · `/pin` | 重命名 / 置顶 |
| `DELETE` | `/api/sessions/{id}` | 删除（连带消息/任务/轨迹/运行/反馈） |
| `POST` | `/api/sessions/{id}/rollback` | 回滚到检查点快照 |
| `GET` `POST` | `/api/sessions/{id}/feedback` | 用户评分与备注（1–5 星） |

### 工具 / 工作区 / 文件 / Git

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` `POST` | `/api/tools` | 工具列表 / 注册自定义工具（MCP 兼容描述） |
| `DELETE` | `/api/tools/{name}` | 卸载工具（内置工具返回 403） |
| `POST` | `/api/tools/{name}/test` | 试运行工具 |
| `GET` `POST` | `/api/workspaces` | 工作区列表 / 创建 |
| `GET` `PUT` `DELETE` | `/api/workspaces/{ident}` | 读取 / 更新 / 删除 |
| `GET` | `/api/files/tree` · `/content` · `/raw` | 文件树 / 内容预览 / 原始字节 |
| `GET` | `/api/files/report` · `/report-exists` | 报告产物读取与存在性检查 |
| `GET` | `/api/git/status` | 工作区变更 |
| `POST` | `/api/git/stage` · `/unstage` | 暂存 / 取消暂存 |

### 资产、健康检查与实时通道

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` `POST` `DELETE` | `/api/resumes` · `/resumes/upload` · `/resumes/{name}` | 简历库管理 |
| `POST` | `/api/upload` · `/api/ocr` | 材料上传 / 截图文字识别 |
| `GET` `POST` | `/api/models` · `/api/model/select` | 模型列表 / 切换 |
| `GET` | `/api/health` | 依赖健康状态（数据库 / Redis / 向量库 / 沙箱）**公开**（供探针） |
| `GET` | `/api/config` | 前端设置面板所需的**非敏感**配置快照（绝不含 api_key） |
| `GET` | `/metrics` | Prometheus 文本指标（运行数 / 失败数 / 队列长度 / Token / 缓存命中率） |
| `WS` | `/ws/agent/{session_id}` | 工具状态与指标实时推送 |

> **鉴权说明**：`AUTH_ENABLED=true` 时，上表除 `/api/health` 外全部需要凭据
> （`Authorization: Bearer <token>` 或 `X-API-Key: <token>`；WebSocket 用 `?token=`，
> 只读 GET 也允许查询参数以便 `<img src>` 直链预览）。失败返回
> `401`（未带凭据）或 `403`（凭据无效）。

### SSE 事件类型

```
run_started · session_info · queue_position · user_message · node_start · node_end
plan_created · task_start · thought · tool_call · tool_result · tool_error · retry
task_finish · ask_user · security_block · facts_updated · metric · trajectory
final_answer · run_done · interrupted · rolled_back · error · heartbeat
```

帧格式：`event: <type>\ndata: <单行 JSON>\n\n`（`ensure_ascii=false`）。

---

## 项目结构

```
.
├── backend/                        # 后端：FastAPI + LangGraph
│   ├── main.py                     #   入口：CORS、lifespan、路由挂载、静态托管
│   ├── config.py                   #   pydantic-settings 配置
│   ├── db.py                       #   引擎 / SessionLocal / Base / init_db
│   ├── security.py                 #   鉴权与 owner 解析
│   ├── observability.py            #   Prometheus 指标与结构化日志
│   ├── cli.py                      #   命令行入口（单轮 / 交互 / --serve）
│   ├── api/                        #   路由层：agent / sessions / tools / workspaces /
│   │                               #            files / git / assets / ws
│   ├── core/
│   │   ├── graph.py                #   ★ LangGraph StateGraph 编排
│   │   ├── agent.py                #   ★ 业务核心：JobAgent（规划/ReAct/重试/产物接线）
│   │   ├── state.py                #     AgentState 与 reducer
│   │   ├── checkpointer.py         #     检查点与快照（回滚）
│   │   ├── events.py               #     事件常量 + EventBridge（线程安全桥接）
│   │   ├── metrics.py              #     Token / TPS / 缓存命中率采集
│   │   ├── presets.py              #     预设：标准 / 极简 / PTC / 创造
│   │   ├── permissions.py          #     权限模式解析
│   │   ├── sandbox.py              #     沙箱：路径限定 + 超时 + 输出截断
│   │   ├── queue.py                #     优先级队列（Redis 可选）
│   │   ├── longterm.py             #     ★ ChromaDB 长期记忆 / RAG 召回（可降级）
│   │   ├── planner.py llm.py memory.py prompts.py schemas.py
│   │   ├── cache.py profile.py secure_store.py
│   │   └── tools/                  #   ★ 9 个内置工具 + 带重试的注册表
│   ├── models/                     #   SQLAlchemy 模型
│   ├── schemas/                    #   Pydantic 请求/响应模型
│   ├── services/                   #   业务服务层（含 run_service 事件流数据源）
│   ├── .env.example                #   环境变量模板（占位符，无真实值）
│   ├── requirements.txt            #   生产依赖
│   ├── requirements-dev.txt        #   开发/测试依赖（pandas / reportlab / playwright）
│   └── requirements-postgres.txt   #   PostgreSQL 驱动（按需）
├── frontend/                       # 前端：React 18 + TypeScript + Vite + Tailwind
│   ├── src/
│   │   ├── App.tsx                 #   三栏外壳 + 顶栏 + 运行编排
│   │   ├── main.tsx                #   挂载入口 + QueryClientProvider
│   │   ├── components/             #   Sidebar / ChatArea / MessageList / TrajectoryView /
│   │   │                           #   StreamMetrics / RightPanel / FileTreePanel / ToolPanel /
│   │   │                           #   TokenPanel / GitPanel / SettingsModal / InputBox /
│   │   │                           #   MarkdownView / CodeBlock / EmptyState / FeedbackBar /
│   │   │                           #   SessionList / Logo / icons
│   │   ├── store/                  #   zustand：useAppStore + useRunStore
│   │   ├── api/                    #   client.ts（REST）/ stream.ts（SSE）/ ws.ts
│   │   ├── hooks/useAgentRun.ts    #   一次运行的生命周期编排
│   │   ├── types/index.ts          #   与后端 schemas 一一对应的类型
│   │   ├── lib/                    #   utils.ts（样式工具）/ auth.ts（令牌）
│   │   └── index.css               #   ★ 设计令牌（深 / 浅色）
│   ├── tailwind.config.js  vite.config.ts  package.json
├── docs/
│   ├── ARCHITECTURE.md             #   前后端接口契约（冻结）
│   ├── HARDENING.md                #   加固变更契约（环境变量/字段名冻结）
│   ├── DEPLOY_LOCAL.md             #   本机部署与运行数据落盘说明
│   ├── REVIEW.md                   #   架构评审记录
│   ├── EVALUATION.md               #   评测记录
│   └── EVALUATION_v2.md            #   评测记录（最新）
├── scripts/
│   ├── smoke_api.py                #   端到端接口冒烟（32 项）
│   ├── smoke_auth.py               #   安全加固验收（31 项）
│   ├── ui_check.py                 #   交互层实机校验（Playwright 真实点击）
│   ├── check_postgres_path.py      #   PostgreSQL 方言级校验
│   ├── capture_screenshots.py      #   Playwright 截图
│   ├── audit_secrets.py            #   密钥 / 隐私审计（含 git 全历史）
│   └── audit_brand.py              #   第三方品牌字样审计
├── tests/
│   ├── test_smoke.py               #   业务核心冒烟（77 项，离线）
│   ├── test_schemas.py             #   结构化输出校验（33 项）
│   ├── golden_eval.py              #   黄金集质量回归（20 条用例）
│   └── batch_eval.py               #   批量 JD 评测
├── data/                           # 运行时数据（已 gitignore）
├── workspaces/                     # Agent 工作区（已 gitignore）
├── screenshots/                    # 文档截图
├── Dockerfile  docker-compose.yml  .dockerignore
└── .github/workflows/ci.yml
```

---

## 测试与验证

全部测试**无需任何密钥**即可运行（离线 mock 模式），可直接从仓库根目录执行：

```bash
# ---- 业务核心 ----
python tests/test_smoke.py        # 77 项：规划/工具/记忆/加密/缓存/故障注入/对抗样本
python tests/test_schemas.py      # 33 项：LLM 输出作为不可信输入的边界与降级
python tests/golden_eval.py       # 20 条用例 + 4 项质量指标（成功率/步骤效率/合规率/追问正确率）
python tests/batch_eval.py        # 批量 JD 全链路评测

# ---- 端到端接口（含真实 SSE 流与 WebSocket）----
python scripts/smoke_api.py       # 32 项：契约端点 + 事件序列 + 路径穿越防护
python scripts/smoke_auth.py      # 31 项：401/403、跨 owner 隔离、超预算中止、队列 429、WS 鉴权

# ---- 交互层实机校验（需后端已启动且前端已构建）----
python scripts/ui_check.py        # Playwright 真实点击侧栏 / 设置 / 面板 / 输入区

# ---- PostgreSQL 代码路径（无实例也可做方言级校验）----
python scripts/check_postgres_path.py

# ---- 脱敏与合规审计 ----
python scripts/audit_secrets.py --repo .    # 密钥 / 私有端点 / 敏感信息（含全历史）
python scripts/audit_brand.py --repo .      # 第三方品牌字样

# ---- 前端 ----
cd frontend && npx tsc --noEmit && npm run build
```

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）分五个作业：
后端测试（含黄金集质量回归与安全加固验收）、前端构建与类型检查、交互层实机校验、
PostgreSQL 路径（带真实服务容器）、脱敏审计。

> **关于 `--real`**：`golden_eval.py` 默认在离线 mock 下运行，属于**确定性回归**
> （防报告结构、流程与安全边界退化），**不等价于真实模型的质量评测**。
> 接入真实端点后加 `--real` 可跑同一套用例。

---

## 安全与合规

| 能力 | 实现 |
|---|---|
| **认证** | 全局 HTTP 中间件（fail-closed）：`/api/*` 与 `/metrics` 默认需凭据；`/api/health` 公开供探针 |
| **租户隔离** | 会话域贯穿 `owner` 维度；越权一律按「不存在」处理（**404 而非 403**，避免探测存在性） |
| **运行预算** | `MAX_TOKENS_PER_RUN` 超限即中止（发 `error` + `interrupted`），已产出内容照常落库 |
| **运行超时** | `TASK_TIMEOUT` 为单次运行的墙钟超时，超时同样中止并归档为 `interrupted` |
| **队列上限** | `MAX_QUEUE_SIZE` 满时在**建立 SSE 之前**返回 `429` + `Retry-After` |
| **注入边界** | `core/prompts.py::wrap_untrusted()` 把工具输出、抓取内容、用户材料包进带 `trust="untrusted"` 的定界块，并转义伪造闭合标签 |
| **路径防护** | 沙箱限定工作区；`/files` 静态路由白名单前缀校验 + 路径归一化，剥离 `..` 与反斜杠绕过；`web_fetch` 拒绝内网/环回地址（SSRF 防护） |
| **落盘加密** | 会话与上传材料经 Fernet 加密（`data/.key` 自动生成且不入库） |
| **脱敏审计** | `audit_secrets.py` 覆盖工作树与 **git 全历史**，带白名单避免误报测试夹具中的假密钥 |

**已知取舍（有意保留）**

- **工作区与长期记忆当前仍是全局共享**：多用户部署前需把 `owner` 继续下沉到画像与向量集合；
  数据模型与路由层已预留 owner 维度，改动是加法而非重构。
- **只读 GET 允许 `?token=`**：`<img src>` / 直链下载无法携带自定义请求头，
  若一律拒绝则图片与 PDF 预览在开启鉴权后必然 401。代价是令牌可能进入访问日志，
  因此写操作严格要求请求头。需要更强隔离时应改为短期签名 URL。
- **未做逐工具调用确认**：权限模式是 run 级，高危工具调用前的人工确认需先定义
  UX 与授权记忆策略。

---

## 界面设计语言

- **三栏式**：左侧栏（收起 `56px` / 展开 `280px`）＋ 中央对话/任务区（自适应）
  ＋ 右侧详情面板（`360px`），两侧均可折叠；
- **响应式**：视口 < 1024px 时右侧面板自动关闭、左侧栏强制紧凑 Rail；
- **极简黑白灰**：主色为零彩色灰阶；语义色（成功/警告/失败/信息）仅用于状态指示，低饱和；
- **小圆角**：`4–8px`；间距紧凑，信息密度贴近开发者工具；
- **字体**：`-apple-system, Inter, "Segoe UI", system-ui, sans-serif`，数字与路径一律等宽；
- **主题**：默认深色，浅色一键切换；仅切换 CSS 变量（`--pf-*`），组件层无硬编码颜色；
- **动效**：仅用于状态表达（执行中呼吸、流式光标、淡入），不做装饰性动画。

---

## 路线图

| 方向 | 项 |
|---|---|
| 执行 | 断点续跑的可视化控制（当前 `ask_user` 已支持挂起与恢复） |
| 执行 | 多 Agent 并行分工与结果合并 |
| 记忆 | 多版本画像对比、行为图谱、跨会话行动项跟踪 |
| 报告 | 模板切换（简洁/详细/HR 视角）、多岗位横向对比 |
| 交互 | 语音输入、快捷键体系、命令面板 |
| 运维 | 用户与配额、多租户隔离下沉、OpenTelemetry 链路追踪 |

---

## 贡献

欢迎提交 Issue 与 PR。提交前建议先跑一遍离线测试套件：

```bash
python tests/test_smoke.py && python tests/test_schemas.py && python scripts/audit_secrets.py --repo .
```

---

## 许可

仅供学习与个人使用。示例简历与 JD 均为虚构数据。
