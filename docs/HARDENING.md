# 加固变更契约（本轮，冻结）

> 依据 `docs/REVIEW.md` 的评审结论实施。**字段名、环境变量名、接口路径在此冻结**，并行开发一律以此为准。

## 完成情况（实施后回填）

| 编号 | 项 | 状态 | 落地位置 / 证据 |
|---|---|---|---|
| P0-1 | 认证与鉴权 | ✅ 完成 | `backend/security.py` + `main.py` 全局中间件；`scripts/smoke_auth.py` 覆盖 401/403/公开路径/WS 鉴权 |
| P0-2 | 租户隔离（owner） | ✅ 完成（会话域 + 反馈；工作区/长期记忆仍全局，已在 README「已知取舍」标注） | `sessions.owner`、`runs.owner`、`feedback.owner`；`session_service` 读操作按"非本 owner = 不存在"处理 |
| P1-1 | 运行级超时与 token 预算 | ✅ 完成 | `run_service.stream()` 的 `asyncio.timeout(TASK_TIMEOUT)` + `_budget_error`；smoke_auth 第 4 节 |
| P1-2 | 准入队列上限 | ✅ 完成 | `_Admission.max_queue` + `precheck()` + 路由 429/Retry-After；smoke_auth 第 5 节 |
| P1-3 | 不可信内容结构化边界 | ✅ 完成 | `core/prompts.py::wrap_untrusted`，接入 ReAct 观察值 / 产物摘要 / 综合素材；含定界符转义 |
| P1-4 | 反馈闭环 | ✅ 完成 | `feedback` 表 + 两个端点 + 前端 `FeedbackBar`；`tests/golden_eval.py` 质量回归 20 用例 |
| P2-1 | 契约措辞纠正 | ✅ 完成 | 见下方 §9 说明（`DelegateTool` 是"转发到已注册工具"，不是子 Agent） |
| P2-2 | `context_window` 配置化 | ✅ 完成 | `Settings.context_window`（此前 `_context_window()` 读的字段根本不存在，恒回退硬编码值） |
| P2-4 | 结构化日志 + `/metrics` | ✅ 完成（完整 OTel 仍延后） | `backend/observability.py`、`main.py` 的 `/metrics` |
| P2-6 | 沙箱 exec 能力一致化 | ✅ 完成 | `SANDBOX_TIMEOUT` 独立生效；文档明确"不做 shell 工具" |
| P2-8 | prompt 版本随 run 落库 | ✅ 完成 | `runs.prompt_version`（由 `PROMPT_VERSION` 注入） |
| P2-3 | 高危工具调用前确认 | ⏸ 延后 | 需先定 UX 与授权记忆策略 |
| P2-5 | 无依赖工具并行执行 | ⏸ 延后 | 收益需先由真实端点的延迟数据证明 |
| P2-7 | 向量记忆 TTL/去重 | ⏸ 延后 | 依赖 owner 下沉到向量集合后再做更合适 |

**验收结果（全部实跑）**：`test_smoke 77/77` · `test_schemas 33/33` · `golden_eval 20/20（4 项指标全 1.000）` ·
`smoke_api 32/32` · `smoke_auth 31/31` · `ui_check 33/33` · `check_postgres_path 8/8` ·
`audit_secrets（含全历史）通过` · `audit_brand（工作树 + 全历史 + 规则自检）通过`。

## 0. 范围

| 编号 | 项 | 本轮 |
|---|---|---|
| P0-1 | 认证与鉴权 | ✅ 做 |
| P0-2 | 租户/用户隔离（owner） | ✅ 做（会话域 + 记忆/向量；工作区保持共享） |
| P1-1 | 运行级超时与 token 预算 | ✅ 做 |
| P1-2 | 准入队列上限 | ✅ 做 |
| P1-3 | 不可信内容结构化边界 | ✅ 做 |
| P1-4 | 反馈闭环（API + UI + 黄金集） | ✅ 做 |
| P2-1 | 契约「子 Agent 委派」措辞纠正 | ✅ 做（文档） |
| P2-2 | `context_window` 配置化（修正恒 65536） | ✅ 做 |
| P2-3 | 高危工具调用前确认 | ⏸ 延后（需先定 UX 与授权记忆策略） |
| P2-4 | 完整 OpenTelemetry 链路追踪 | ⏸ 部分：本轮做结构化日志 + `/metrics` |
| P2-5 | 无依赖工具并行执行 | ⏸ 延后 |
| P2-6 | 沙箱 exec 能力与配置一致化 | ✅ 做（文档 + 配置显式化） |
| P2-7 | 向量记忆 TTL/去重 | ⏸ 延后 |
| P2-8 | prompt/模型版本随 run 落库 | ✅ 做 |

## 1. 环境变量（新增）

```
# ---- 认证 ----
AUTH_ENABLED=false            # true 时 /api/* 与 /ws/* 均需凭据
API_KEYS=                     # 形如 token1=alice,token2=bob；owner 缺省为 default
AUTH_HEADER=Authorization     # 也可用 X-API-Key；两种都接受

# ---- 运行预算 ----
MAX_TOKENS_PER_RUN=0          # 0=不限；超限则中止本次运行
MAX_QUEUE_SIZE=32             # 准入队列上限，超出返回 429
CONTEXT_WINDOW=65536          # 真实模型上下文窗口（用于占用率计算与提示词预算）
PROMPT_VERSION=v1             # 提示词版本号，随 run 落库便于回归归因
```

- `TASK_TIMEOUT` **语义修正**：本轮起真正作为「单次运行的墙钟超时」，沙箱单命令超时改用已有的 `SANDBOX_TIMEOUT`。
- 认证关闭时（默认）行为与现状完全一致，保证本地开箱即跑。

## 2. 认证与 owner 约定

- 请求头：`Authorization: Bearer <token>` 或 `X-API-Key: <token>`（大小写不敏感）。
- token → owner 映射由 `API_KEYS` 决定；未配置 `API_KEYS` 且 `AUTH_ENABLED=true` 时，`AUTH_TOKEN` 单 token 可用，owner 固定 `default`。
- 失败响应：`401 {"detail": "..."}`；若提供了无效 token 但非空，返回 `403`。
- 公开端点（无需鉴权）：`/api/health`、`/docs`、`/openapi.json`、`/`（前端静态资源）。
- owner 透传：`POST /api/agent/run` 增加可选字段 `owner`（仅当 `AUTH_ENABLED=false` 时生效，用于本地多身份调试）。

## 3. 数据模型变更

| 表 | 变更 |
|---|---|
| `sessions` | 新增 `owner VARCHAR(64) NOT NULL DEFAULT 'default'`，建索引 `ix_sessions_owner` |
| `feedback` | **新表**：`id, session_id, run_id, message_ts, rating(int 1-5), comment(Text), owner, created_at` |
| `runs` | 新增 `owner VARCHAR(64) DEFAULT ''`、`prompt_version VARCHAR(32) DEFAULT ''` |

兼容策略：`db.init_db()` 后执行 `ensure_columns()`，对已存在的库做 `ALTER TABLE ... ADD COLUMN`（SQLite/PG 通用），失败只告警不阻断。

## 4. 接口变更

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/sessions/{id}/feedback` | body `{rating: 1-5, comment?: string, run_id?: string, message_ts?: number}` → `{ok, id}` |
| `GET` | `/api/sessions/{id}/feedback` | 返回该会话反馈列表 `{items: [...]}` |
| `GET` | `/metrics` | Prometheus 文本格式：运行计数/失败数/耗时直方图近似、token 累计、队列长度、活跃 run |

其余端点路径与语义不变；所有会话域端点在 `AUTH_ENABLED=true` 时**只返回/只允许操作本 owner 的资源**。

## 5. 预算与超时行为

- 超时/超预算 → 事件序列：`error`（原因）→ `interrupted`（reason=`运行超时` / `超出 token 预算`），run 状态置 `interrupted`，已产出内容照常落库。
- 队列超限 → `POST /api/agent/run` 返回 `429 {"detail": "..."}`（不建立 SSE）。
- 预算统计口径：以 `metric` 事件的 `total_tokens` 累计为准（跨 LLM 调用累加）。

## 6. 不可信内容边界

新增 `core/prompts.py::wrap_untrusted(text, source)`：

```
<user_data source="web_fetch" trust="untrusted">
...原文（截断后）...
</user_data>
```

接入点：① ReAct 观察值（工具输出）② 综合报告素材中的外部文本 ③ 用户粘贴的 JD/简历正文。
要求：包裹后的文本才允许进入任何 LLM 调用；系统提示词中的边界声明与本结构一致。

## 7. 前端变更

- 设置弹窗「通用设置」新增 **访问令牌** 输入框：写 `localStorage['pf.token']`，所有 `fetch`/SSE/WS 自动带 `Authorization: Bearer`；留空则不带头。
- Agent 消息下方新增**反馈条**：1–5 星 + 可选备注，`POST /api/sessions/{id}/feedback`；已评过则显示当前评分。
- 顶部连接状态：`401/403` 时显示「未授权」而非「后端未连接」。

## 8. 验收（本轮结束时必须全绿）

```
python tests/test_smoke.py          77/77      业务核心未被破坏
python tests/test_schemas.py        33/33
python scripts/smoke_api.py         32+ 新增鉴权/预算/队列/反馈/隔离用例
python scripts/check_postgres_path.py 8/8
python scripts/ui_check.py          27+ 新增令牌与反馈交互用例
python tests/golden_eval.py         新增：黄金集质量回归
python scripts/audit_secrets.py --repo .   通过（含全历史）
python scripts/audit_brand.py --repo . --history  通过
```
