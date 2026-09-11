/**
 * 与后端 `backend/schemas/` 一一对应的类型定义。
 * 字段名严格遵循 docs/ARCHITECTURE.md（冻结契约），改动需先改契约。
 */

export type PresetId = 'standard' | 'minimal' | 'ptc' | 'creative'
export type PermissionMode = 'read_only' | 'workspace_write' | 'full_access'
export type ReasoningEffort = 'low' | 'medium' | 'high'
export type RunStatus =
  | 'idle' | 'queued' | 'running' | 'awaiting_input' | 'done' | 'failed' | 'interrupted'
export type NodeStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'waiting'
export type TaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'waiting' | 'skipped'
export type NodeName =
  | 'recall' | 'plan' | 'dispatch' | 'react' | 'check' | 'await_user' | 'synthesize' | 'finalize'

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
}

export interface Metrics {
  tps: number
  llm_ms: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  cache_hit_rate: number
  context_tokens: number
  context_window: number
  context_ratio: number
  llm_calls: number
  tool_calls: number
}

export interface RunStats extends TokenUsage {
  llm_calls: number
  tool_calls: number
  tool_retries: number
  failed_tasks: string[]
  elapsed_s: number
  high_cost_calls: number
  llm_degraded: boolean
  cache_disk_items: number
  cache_hit_rate: number
  tps: number
  context_tokens: number
  context_window: number
  context_ratio: number
  nodes: number
}

export interface Session {
  id: string
  title: string
  status: RunStatus
  workspace: string
  preset: PresetId
  permission_mode: PermissionMode
  model: string
  pinned: boolean
  created_at: string
  updated_at: string
  message_count: number
  token_stats: Partial<TokenUsage & { cache_hit_rate: number; tps: number }>
  facts: Record<string, unknown>
}

export interface ChatMessage {
  id?: number | string
  role: 'user' | 'assistant' | 'system'
  content: string
  ts: number
  run_id?: string
  tokens?: Partial<TokenUsage>
  chart?: string
}

export interface TaskInfo {
  id: string
  title: string
  detail?: string
  tool: string
  status: TaskStatus
  result?: string
  error?: string
  retries?: number
  elapsed_ms?: number
  depends_on?: string[]
  steps?: TaskStep[]
}

export interface TaskStep {
  thought: string
  tool: string
  ok: boolean
  args?: Record<string, string>
  observation?: string
}

export interface ToolCallRecord {
  call_id: string
  task_id: string
  tool: string
  args: Record<string, unknown>
  brief?: string
  error?: string
  ok: boolean
  elapsed_ms: number
  cached?: boolean
  tokens?: Partial<TokenUsage>
  status?: 'running' | 'ok' | 'error'
}

export interface TrajectoryNode {
  node_id: string
  node: NodeName | string
  label: string
  seq: number
  status: NodeStatus
  elapsed_ms: number
  tokens: Partial<TokenUsage>
  thoughts: { step: number; thought: string }[]
  tool_calls: ToolCallRecord[]
  error?: string
}

export interface ToolInfo {
  name: string
  description: string
  input_schema: Record<string, unknown>
  cost: string
  avg_seconds: number
  source: 'builtin' | 'custom'
  enabled: boolean
  kind?: 'http' | 'python' | 'mcp'
}

export interface Workspace {
  id: string
  name: string
  path: string
  description: string
  created_at: string
  updated_at: string
  exists: boolean
  file_count: number
}

export interface FileNode {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number
  mtime: number
  children?: FileNode[]
}

export interface FileContent {
  path: string
  type: 'text' | 'markdown' | 'image' | 'pdf' | 'binary'
  content: string
  language: string
  size: number
  truncated: boolean
}

export interface GitChange {
  path: string
  status: string
  staged: boolean
}

export interface GitStatus {
  is_repo: boolean
  branch: string
  changes: GitChange[]
}

export interface PresetInfo {
  id: PresetId
  name: string
  description: string
  params: Record<string, unknown>
}

/**
 * `/api/health` 的依赖状态：后端返回的是**结构化对象**（含 ok/available/enabled/mode 等），
 * 为兼容只报字符串的实现，这里用联合类型；渲染前必须经 `describeService()` 归一，
 * 直接把对象交给 React 会抛 "Objects are not valid as a React child" 并整页崩白。
 */
export interface HealthServiceState {
  ok?: boolean
  available?: boolean
  enabled?: boolean
  configured?: boolean
  mode?: string
  dialect?: string
  fallback?: boolean
  path?: string
  timeout?: number
  max_output?: number
  permission_modes?: string[]
}

export interface HealthInfo {
  status: string
  provider: string
  model: string
  tools: string[]
  tool_count?: number
  tool_costs?: Record<string, string>
  db: HealthServiceState | string
  redis: HealthServiceState | string
  vector_store: HealthServiceState | string
  sandbox: HealthServiceState | boolean
  encrypted_storage: boolean
  ocr_ready?: boolean
  llm_ready?: boolean
  cache: Record<string, unknown>
}

export interface ModelInfo {
  models: string[]
  current: string
  provider: string
}

/** `GET /api/sessions/{id}/feedback` 的列表项（契约 HARDENING §4） */
export interface FeedbackItem {
  id: string
  session_id: string
  run_id: string
  rating: number
  comment: string
  created_at: string
  /** 契约 §3 的 feedback 表含 message_ts；用于把评分回填到具体某条消息 */
  message_ts?: number
}

/** `POST /api/sessions/{id}/feedback` 的请求体（rating 1–5，其余可省） */
export interface FeedbackBody {
  rating: number
  comment?: string
  run_id?: string
  message_ts?: number
}

export interface RunRequest {
  task: string
  session_id: string
  preset: PresetId
  workspace: string
  permission_mode: PermissionMode
  model?: string
  reasoning_effort?: ReasoningEffort
  priority?: number
  resume?: boolean
}

/** SSE / WebSocket 事件的判别联合（`type` 为判别字段，与契约 2.2 节一致） */
export type RunEvent =
  | { type: 'run_started'; run_id: string; session_id: string; model: string;
      preset: PresetId; permission_mode: PermissionMode; workspace: string }
  | { type: 'session_info'; session_id: string; status: RunStatus; title: string }
  | { type: 'queue_position'; run_id: string; position: number; priority: number }
  | { type: 'user_message'; content: string }
  | { type: 'node_start'; node_id: string; node: NodeName; label: string; seq: number }
  | { type: 'node_end'; node_id: string; node: NodeName; status: NodeStatus;
      elapsed_ms: number; tokens?: Partial<TokenUsage>; error?: string }
  | { type: 'plan_created'; goal: string; tasks: TaskInfo[]; costs: Record<string, string> }
  | { type: 'task_start'; task_id: string; title: string; tool: string }
  | { type: 'thought'; task_id: string; step: number; thought: string }
  | { type: 'tool_call'; call_id: string; task_id: string; tool: string;
      args: Record<string, unknown>; cost: string }
  | { type: 'tool_result'; call_id: string; task_id: string; tool: string; brief: string;
      elapsed_ms: number; ok: true }
  | { type: 'tool_error'; call_id: string; task_id: string; tool: string; error: string;
      elapsed_ms: number; ok: false }
  | { type: 'retry'; task_id: string; tool: string; attempt: number; error: string }
  | { type: 'task_finish'; task_id: string; status: TaskStatus; result_brief?: string;
      error?: string; elapsed_ms?: number }
  | { type: 'ask_user'; task_id: string; question: string }
  | { type: 'security_block'; reason: string; message: string }
  | { type: 'facts_updated'; facts: Record<string, unknown> }
  | ({ type: 'metric' } & Metrics)
  | { type: 'trajectory'; nodes: TrajectoryNode[] }
  | { type: 'final_answer'; content: string; chart: string; stats: RunStats | null }
  | { type: 'run_done'; stats: RunStats }
  | { type: 'interrupted'; run_id: string; reason: string }
  | { type: 'rolled_back'; checkpoint_id: string }
  | { type: 'error'; message: string }
  | { type: 'heartbeat'; ts: number }
  | { type: 'tool_status'; call_id: string; tool: string;
      status: 'running' | 'ok' | 'error'; elapsed_ms: number; tokens?: Partial<TokenUsage> }
