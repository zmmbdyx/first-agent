/**
 * 运行期状态：把 SSE/WS 事件流归约成「消息 + 任务 + 执行轨迹 + 实时指标」。
 * 所有事件处理集中在这里，组件只负责渲染，便于单点排查事件语义问题。
 */
import { create } from 'zustand'
import type {
  ChatMessage, Metrics, RunEvent, RunStats, RunStatus, TaskInfo, ToolCallRecord,
  TrajectoryNode,
} from '@/types'

interface RunState {
  sessionId: string | null
  runId: string | null
  status: RunStatus
  goal: string
  messages: ChatMessage[]
  tasks: Record<string, TaskInfo>
  taskOrder: string[]
  trajectory: TrajectoryNode[]
  toolCalls: ToolCallRecord[]
  metrics: Metrics | null
  stats: RunStats | null
  pendingQuestion: string
  error: string
  selectedNodeId: string | null
  /** 事件流里最近一次 metric 的时间戳，用于判断指标是否仍在刷新 */
  metricsAt: number

  applyEvent: (evt: RunEvent) => void
  reset: (sessionId?: string | null) => void
  loadHistory: (payload: {
    messages?: ChatMessage[]
    tasks?: TaskInfo[]
    trajectory?: TrajectoryNode[]
    tool_calls?: ToolCallRecord[]
    sessionId?: string
    status?: RunStatus
  }) => void
  appendUserMessage: (content: string) => void
  setSelectedNode: (nodeId: string | null) => void
  setStatus: (status: RunStatus) => void
}

const emptyMetrics: Metrics = {
  tps: 0, llm_ms: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
  cached_tokens: 0, cache_hit_rate: 0, context_tokens: 0, context_window: 0,
  context_ratio: 0, llm_calls: 0, tool_calls: 0,
}

let seq = 0

/** 找到当前正在运行的节点：thought / tool_call 等事件需要挂到它下面 */
function activeNode(nodes: TrajectoryNode[]): TrajectoryNode | undefined {
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    if (nodes[i].status === 'running') return nodes[i]
  }
  return nodes[nodes.length - 1]
}

export const useRunStore = create<RunState>((set) => ({
  sessionId: null,
  runId: null,
  status: 'idle',
  goal: '',
  messages: [],
  tasks: {},
  taskOrder: [],
  trajectory: [],
  toolCalls: [],
  metrics: null,
  stats: null,
  pendingQuestion: '',
  error: '',
  selectedNodeId: null,
  metricsAt: 0,

  reset: (sessionId = null) =>
    set({
      sessionId, runId: null, status: 'idle', goal: '', messages: [], tasks: {},
      taskOrder: [], trajectory: [], toolCalls: [], metrics: null, stats: null,
      pendingQuestion: '', error: '', selectedNodeId: null, metricsAt: 0,
    }),

  appendUserMessage: (content) =>
    set((s) => ({
      messages: [...s.messages, { role: 'user', content, ts: Date.now() / 1000 }],
      error: '',
    })),

  setSelectedNode: (selectedNodeId) => set({ selectedNodeId }),
  setStatus: (status) => set({ status }),

  loadHistory: ({ messages = [], tasks = [], trajectory = [], tool_calls = [],
                  sessionId, status }) =>
    set(() => ({
      sessionId: sessionId ?? null,
      messages,
      tasks: Object.fromEntries(tasks.map((t) => [t.id, t])),
      taskOrder: tasks.map((t) => t.id),
      trajectory,
      toolCalls: tool_calls,
      status: status ?? 'idle',
      selectedNodeId: null,
    })),

  applyEvent: (evt) =>
    set((s) => {
      switch (evt.type) {
        case 'run_started':
          // 一并认领会话：runStore.sessionId 表示"当前界面上的运行状态属于哪个会话"，
          // App 用它判断是否需要从后端拉历史（避免用陈旧历史覆盖正在进行的运行）
          return { runId: evt.run_id, sessionId: evt.session_id || s.sessionId,
                   status: 'running' as RunStatus, error: '' }

        case 'session_info':
          return { sessionId: evt.session_id || s.sessionId, status: evt.status }

        case 'queue_position':
          return { status: 'queued' as RunStatus }

        case 'user_message': {
          // 后端会回显用户消息；已在本地乐观插入时避免重复
          const last = s.messages[s.messages.length - 1]
          if (last && last.role === 'user' && last.content === evt.content) return {}
          return { messages: [...s.messages, { role: 'user', content: evt.content, ts: Date.now() / 1000 }] }
        }

        case 'node_start': {
          const node: TrajectoryNode = {
            node_id: evt.node_id, node: evt.node, label: evt.label,
            seq: evt.seq ?? (seq += 1),
            status: 'running', elapsed_ms: 0, tokens: {}, thoughts: [], tool_calls: [],
          }
          return { trajectory: [...s.trajectory, node] }
        }

        case 'node_end': {
          const trajectory = s.trajectory.map((n) =>
            n.node_id === evt.node_id
              ? { ...n, status: evt.status, elapsed_ms: evt.elapsed_ms,
                  tokens: evt.tokens ?? n.tokens, error: evt.error }
              : n)
          return { trajectory }
        }

        case 'plan_created':
          return {
            goal: evt.goal,
            tasks: Object.fromEntries(evt.tasks.map((t) => [t.id, t])),
            taskOrder: evt.tasks.map((t) => t.id),
          }

        case 'task_start':
          return {
            tasks: {
              ...s.tasks,
              [evt.task_id]: {
                ...(s.tasks[evt.task_id] ?? { id: evt.task_id, title: evt.title, tool: evt.tool }),
                title: evt.title, tool: evt.tool, status: 'running' as const,
              },
            },
            taskOrder: s.taskOrder.includes(evt.task_id)
              ? s.taskOrder : [...s.taskOrder, evt.task_id],
          }

        case 'thought': {
          const node = activeNode(s.trajectory)
          if (!node) return {}
          const trajectory = s.trajectory.map((n) =>
            n.node_id === node.node_id
              ? { ...n, thoughts: [...n.thoughts, { step: evt.step, thought: evt.thought }] }
              : n)
          return { trajectory }
        }

        case 'tool_call': {
          const node = activeNode(s.trajectory)
          const record: ToolCallRecord = {
            call_id: evt.call_id, task_id: evt.task_id, tool: evt.tool, args: evt.args ?? {},
            ok: true, elapsed_ms: 0, status: 'running',
          }
          return {
            toolCalls: [...s.toolCalls, record],
            trajectory: node
              ? s.trajectory.map((n) =>
                  n.node_id === node.node_id ? { ...n, tool_calls: [...n.tool_calls, record] } : n)
              : s.trajectory,
          }
        }

        case 'tool_result':
        case 'tool_error': {
          const ok = evt.type === 'tool_result'
          const patch = (c: ToolCallRecord): ToolCallRecord =>
            c.call_id === evt.call_id
              ? { ...c, ok, status: ok ? 'ok' : 'error', elapsed_ms: evt.elapsed_ms,
                  brief: ok ? evt.brief : undefined, error: ok ? undefined : evt.error }
              : c
          return {
            toolCalls: s.toolCalls.map(patch),
            trajectory: s.trajectory.map((n) => ({ ...n, tool_calls: n.tool_calls.map(patch) })),
          }
        }

        case 'tool_status': {
          // WebSocket 通道的补充推送：与 SSE 的 tool_result 幂等合并
          const patch = (c: ToolCallRecord): ToolCallRecord =>
            c.call_id === evt.call_id
              ? { ...c, status: evt.status, elapsed_ms: evt.elapsed_ms || c.elapsed_ms,
                  tokens: evt.tokens ?? c.tokens }
              : c
          return {
            toolCalls: s.toolCalls.map(patch),
            trajectory: s.trajectory.map((n) => ({ ...n, tool_calls: n.tool_calls.map(patch) })),
          }
        }

        case 'retry': {
          const t = s.tasks[evt.task_id]
          if (!t) return {}
          return { tasks: { ...s.tasks, [evt.task_id]: { ...t, retries: (t.retries ?? 0) + 1 } } }
        }

        case 'task_finish': {
          const t = s.tasks[evt.task_id]
          if (!t) return {}
          return {
            tasks: {
              ...s.tasks,
              [evt.task_id]: {
                ...t, status: evt.status, result: evt.result_brief ?? t.result,
                error: evt.error, elapsed_ms: evt.elapsed_ms ?? t.elapsed_ms,
              },
            },
          }
        }

        case 'ask_user':
          return { status: 'awaiting_input' as RunStatus, pendingQuestion: evt.question }

        case 'security_block':
          return { error: evt.message }

        case 'facts_updated':
          return {}

        case 'metric': {
          const { type: _t, ...m } = evt
          return { metrics: m as Metrics, metricsAt: Date.now() }
        }

        case 'trajectory':
          return { trajectory: evt.nodes }

        case 'final_answer':
          return {
            messages: [...s.messages, {
              role: 'assistant' as const, content: evt.content, ts: Date.now() / 1000,
              chart: evt.chart || undefined,
            }],
            stats: evt.stats ?? s.stats,
            status: (evt.stats ? 'done' : s.status) as RunStatus,
            pendingQuestion: '',
          }

        case 'run_done':
          return { stats: evt.stats, status: 'done' as RunStatus }

        case 'interrupted':
          return { status: 'interrupted' as RunStatus }

        case 'rolled_back':
          return { error: '' }

        case 'error':
          return { error: evt.message }

        default:
          return {}
      }
    }),
}))

/** 指标为空时的占位，避免组件里到处判空 */
export const fallbackMetrics = emptyMetrics
