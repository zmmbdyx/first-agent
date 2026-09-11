# 本机部署流程（Windows / 已实测）

> 全部命令在**本机实测通过**：Python 3.14.3、Node v24.21.0、npm 11.19.0。
> 仓库路径以 `E:\ai\ai job\agent` 为例，路径含空格不影响下面的命令（已按此环境验证）。

---

## 0. 三种运行模式（先选一个）

| 模式 | 用途 | 需要 LLM 凭据 | 访问地址 |
|---|---|---|---|
| **A. 离线演示（当前默认）** | 无需任何密钥，跑通全流程与全部界面 | 否（自动 mock） | `http://127.0.0.1:8000` |
| **B. 开发双端口** | 改前端代码即时热更新 | 可选 | 前端 `:5173` → 代理后端 `:8000` |
| **C. 单端口生产** | 前端产物由后端托管，只暴露一个端口 | 可选 | `http://127.0.0.1:8000` |

> **当前仓库状态**：`.env` 里 `LLM_MODEL` 为空 → 运行在**模式 A**（mock）。
> 想接真实模型只需补一个 `LLM_MODEL`，见第 3 节。

---

## 1. 前置条件

```powershell
python --version      # 需 3.10+，本机 3.14.3
node --version        # 需 18+，本机 24.21
```

依赖已在本机装好（`backend` 的 Python 包 + `frontend/node_modules` + `frontend/dist`）。
若在**新机器**上从零开始：

```powershell
cd "E:\ai\ai job\agent"
python -m pip install -r backend\requirements.txt        # 生产切 PostgreSQL 再加 backend\requirements-postgres.txt
cd frontend; npm install; cd ..
```

---

## 2. 启动后端（模式 A/C）

```powershell
cd "E:\ai\ai job\agent"
python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

- **`--app-dir backend` 已足够**，不必再设 `PYTHONPATH`（本机已验证）。
- 若你更喜欢前台热重载，加 `--reload`；但注意 `--reload` 会在改文件时重启，跑长任务时会被打断。
- 启动日志会出现三行关键信息：

```
[config] 生效的配置文件（后者覆盖前者）: .env     ← 哪个 .env 真正生效
[startup] 数据库就绪: 连接正常                     ← 无 PostgreSQL 时会显示 SQLite
[startup] 鉴权未启用（AUTH_ENABLED=false）：仅适合本机单用户使用…
```

**放在后台跑（不占终端）：**

```powershell
Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","main:app","--app-dir","backend","--host","127.0.0.1","--port","8000","--log-level","warning" -WindowStyle Hidden
Start-Sleep -Seconds 8
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing | Select-Object -ExpandProperty StatusCode
```

---

## 3. 配置 `.env`（**这里有个坑**）

### 优先级（已实测）

```
backend/.env   <   <仓库根>/.env   <   <仓库根>/.env.local
     低优先级 ──────────────────────────────► 高优先级（覆盖前者）
```

也就是说**仓库根的 `.env` 会覆盖 `backend/.env`**。同时存在两份时，"我改了 `backend/.env` 却没生效"是必然结果。
启动日志里的 `[config] 生效的配置文件（后者覆盖前者）: .env` 就是用来一眼确认这件事的。

**建议：只保留一份 `.env`，放在仓库根。**

```powershell
cd "E:\ai\ai job\agent"
Copy-Item backend\.env.example .env      # 首次：用模板生成
notepad .env                             # 或 code .env
```

### 切换到真实模型（模式 A → 真实）

`.env` 里这四个变量**必须同时有值**，否则会自动退回 mock：

```ini
LLM_PROVIDER=openai-compatible
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://你的端点/v1
LLM_MODEL=你的模型名          # ← 当前为空，这是仍处于 mock 的原因
```

改完重启后端，用一条命令自查：

```powershell
python -c "from config import load_config as l; c=l(); print(c.provider, c.model)"   # 需先 $env:PYTHONPATH='backend'
```

`provider=mock` 说明端点或模型名还没填全。

### 常用可调项

```ini
AUTH_ENABLED=false          # 对外暴露时必须改 true（见第 6 节）
MAX_CONCURRENT_RUNS=2       # 同时执行的运行数，超出排队
MAX_QUEUE_SIZE=32           # 队列上限，超出立即 429
MAX_TOKENS_PER_RUN=0        # 单次运行 token 上限，0=不限
TASK_TIMEOUT=300            # 单次运行墙钟超时（秒）
CONTEXT_WINDOW=65536        # 真实模型的上下文窗口，用于占用率显示
```

---

## 4. 模式 C：单端口部署（推荐日常使用）

```powershell
cd "E:\ai\ai job\agent\frontend"
npm run build                      # 产出 frontend\dist（约 4 秒）
cd ..
# 重启后端（它检测到 dist 存在就会托管前端）
python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000` 即为完整应用（前端 + API 同端口）。

> 前端产物**不入库**（`.gitignore` 排除 `frontend/dist`），新克隆后需要先 `npm run build` 或跑模式 B。

---

## 5. 模式 B：开发双端口（改前端热更新）

```powershell
# 终端 1：后端
cd "E:\ai\ai job\agent"; python -m uvicorn main:app --app-dir backend --port 8000

# 终端 2：前端
cd "E:\ai\ai job\agent\frontend"; npm run dev      # http://127.0.0.1:5173
```

`vite.config.ts` 已把 `/api` 与 `/ws` 代理到 `127.0.0.1:8000`，无需额外配置。

---

## 6. 局域网 / 对外暴露（**必须先开鉴权**）

默认 `AUTH_ENABLED=false`，此时**任何能访问端口的人都能读写你的会话与工作区文件**。
要让别的机器访问，先做这三步：

```ini
# .env
AUTH_ENABLED=true
API_KEYS=自己生成的长随机串=alice
```

```powershell
python -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
```

然后在浏览器界面：**设置 → 通用设置 → 访问令牌**，填入 `自己生成的长随机串`（存在本机 `localStorage`，随请求发送）。

- 生成随机串：`python -c "import secrets;print(secrets.token_urlsafe(32))"`
- **坑**：打开了 `AUTH_ENABLED` 却没配 `API_KEYS`/`AUTH_TOKEN` 时，按安全设计**所有请求都会被拒绝**（fail-closed），启动日志会打印严重告警，这是有意为之而不是故障。
- 公网还应在前面加反向代理做 TLS；`ALLOW_FULL_ACCESS` 保持 `false`。

---

## 7. 可选依赖（不装也能跑）

| 组件 | 不装时的行为 | 装上后 |
|---|---|---|
| PostgreSQL | 回落 SQLite（`data/pathforge.db`） | 设 `DATABASE_URL=postgresql+psycopg://...`，需 `pip install -r backend/requirements-postgres.txt` |
| Redis | 短期记忆/队列走进程内实现 | 设 `REDIS_URL=redis://localhost:6379/0` |
| ChromaDB | 长期记忆降级为本地文件 + 关键词检索 | 设 `VECTOR_STORE_PATH`（Chroma 已随依赖安装） |

`GET /api/health` 会如实回报每一项的实际状态与降级原因。

**Docker Compose（含 PostgreSQL + Redis）：**

```powershell
cd "E:\ai\ai job\agent"
Copy-Item backend\.env.example .env      # compose 会读取其中的 LLM_* 与 DATA_KEY
docker compose up -d --build
```

---

## 8. 验收：确认部署正确

```powershell
cd "E:\ai\ai job\agent"
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing | Select-Object -ExpandProperty Content
```

完整自动化验收（全部离线可跑）：

```powershell
$env:PYTHONPATH="E:\ai\ai job\agent\backend"
python tests\test_smoke.py            # 77 项 业务核心
python tests\test_schemas.py          # 33 项 结构化校验
python tests\golden_eval.py           # 20 条黄金集 + 4 项质量指标
python scripts\smoke_api.py           # 32 项 接口与 SSE 全链路
python scripts\smoke_auth.py          # 31 项 鉴权/隔离/预算/队列
python scripts\check_postgres_path.py # 8 项 PostgreSQL 方言路径
python scripts\audit_secrets.py --repo .   # 密钥审计（含全历史）
python scripts\audit_brand.py --repo .     # 品牌合规审计
```

界面交互验收（需后端已启动 + 前端已构建）：

```powershell
python scripts\ui_check.py            # 33 项：真实点击侧栏/设置/面板/输入区/令牌/反馈
python scripts\capture_screenshots.py # 顺带生成 README 截图
```

> **PowerShell 注意**：不要把原生命令的输出用管道接给 `Select-String` 这类 cmdlet
> （本机沙箱/编码下会报 `Access is denied`）；需要筛选请先重定向到文件再读。

---

## 9. 停止与重置

```powershell
# 停止所有本项目的 python 进程（含后台启动的 uvicorn）
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

| 想重置什么 | 做法 |
|---|---|
| 会话与运行记录 | 删 `data\pathforge.db`（SQLite 模式）；PostgreSQL 模式则清库 |
| 长期记忆画像 | 删 `data\profile.json` |
| 工具结果缓存（重复分析 0 成本的那份） | 删 `data\cache\` |
| 工作区文件 | 清 `workspaces\`（**这是 Agent 产出物，删前确认**） |
| 落盘加密密钥 | 删 `data\.key`（**删了旧会话将无法解密**） |

---

## 10. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 界面提示「后端未连接」 | 后端未起或端口不同；确认 `:8000` 有 `api/health` 返回 200 |
| 一直是 mock 模式 | `.env` 里 `LLM_MODEL` 为空，或四项凭据没配全 |
| 改了 `.env` 没生效 | 存在多份 `.env`；看启动日志的「生效的配置文件」行，根目录那份优先级更高 |
| 所有接口 401 | `AUTH_ENABLED=true` 但没配令牌（fail-closed）；或前端未在设置里填访问令牌 |
| 图片/PDF 预览 401 | 已支持只读 GET 的 `?token=`；若仍失败，检查令牌是否正确 |
| 运行途中被中断 | 命中 `TASK_TIMEOUT` 或 `MAX_TOKENS_PER_RUN`；事件流会给出 `interrupted` 原因 |
| 请求返回 429 | 队列已满（`MAX_QUEUE_SIZE`）或并发已达 `MAX_CONCURRENT_RUNS`；稍后重试 |
| 端口被占用 | `Get-NetTCPConnection -LocalPort 8000` 找到进程后停掉，或换 `--port` |
| 控制台中文/emoji 乱码 | 已内置 UTF-8 兜底；若是旧终端，`chcp 65001` |
| 右侧文件树为空 | 工作区是本机目录（默认 `workspaces\default`），把文件放进去或改用上传按钮 |

---

## 11. 数据落在哪里（便于备份与清理）

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `data/pathforge.db` | 会话/任务/工具调用/轨迹/运行/反馈（SQLite 模式） | 否 |
| `data/profile.json` | 跨会话长期记忆画像（加密） | 否 |
| `data/cache/`、`data/vectors/`、`data/checkpoints/` | 工具结果缓存、向量、回滚快照 | 否 |
| `data/reports/` | 生成的报告与图表 | 否 |
| `workspaces/` | Agent 的工作区文件 | 否 |
| `.env`、`data/.key` | 凭据与加密密钥 | 否 |
| `frontend/dist/` | 前端构建产物 | 否 |

上面这些都在 `.gitignore` 里；仓库只包含源码、配置模板与截图。
