/**
 * REST 客户端与 react-query 数据钩子。
 * 所有请求走同源 `/api`（开发期由 Vite 代理到后端），无需在代码中硬编码后端地址。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authHeaders } from '@/lib/auth'
import type {
  FeedbackBody, FeedbackItem, FileContent, FileNode, GitChange, GitStatus, HealthInfo, ModelInfo,
  PresetInfo, Session, ToolInfo, Workspace,
} from '@/types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }

  /**
   * 凭据缺失/无效（契约 §2：无凭据 401、无效凭据 403）。
   * 这里只做**标记**，不在本文件里跳转或弹窗——顶栏的连接状态与设置入口是 App 的职责，
   * 数据层一旦做全局副作用，测试与非浏览器环境都会跟着遭殃。
   */
  get unauthorized(): boolean {
    return this.status === 401 || this.status === 403
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData 的 Content-Type（含 boundary）必须交给浏览器自己生成，因此不设置；
  // 但 Authorization 头与 body 形态无关，任何请求都要带上。
  const headers = init?.body instanceof FormData
    ? authHeaders()
    : { 'Content-Type': 'application/json', ...authHeaders() }
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      detail = data?.detail || data?.error || detail
    } catch {
      /* 响应体非 JSON 时保留状态行 */
    }
    throw new ApiError(detail, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const json = (body: unknown) => JSON.stringify(body)

/** 原始 REST 方法（组件里的大多数读操作请优先用下面的 react-query 钩子） */
export const api = {
  health: () => request<HealthInfo>('/health'),
  models: () => request<ModelInfo>('/models'),
  selectModel: (model: string) =>
    request<{ ok: boolean; current: string }>('/model/select', {
      method: 'POST', body: json({ model }),
    }),
  presets: () => request<{ items: PresetInfo[] }>('/agent/presets'),

  sessions: (workspace?: string, q?: string) => {
    const p = new URLSearchParams()
    if (workspace) p.set('workspace', workspace)
    if (q) p.set('q', q)
    const qs = p.toString()
    return request<{ items: Session[]; total: number }>(`/sessions${qs ? `?${qs}` : ''}`)
  },
  createSession: (body: { title?: string; workspace?: string; preset?: string }) =>
    request<Session>('/sessions', { method: 'POST', body: json(body) }),
  sessionHistory: (id: string) => request<SessionHistory>(`/sessions/${id}/history`),
  renameSession: (id: string, title: string) =>
    request<Session>(`/sessions/${id}/title`, { method: 'PUT', body: json({ title }) }),
  pinSession: (id: string, pinned: boolean) =>
    request<Session>(`/sessions/${id}/pin`, { method: 'PUT', body: json({ pinned }) }),
  deleteSession: (id: string) =>
    request<{ ok: boolean }>(`/sessions/${id}`, { method: 'DELETE' }),
  rollback: (id: string, checkpoint_id = '') =>
    request<{ ok: boolean; checkpoint_id: string }>(`/sessions/${id}/rollback`, {
      method: 'POST', body: json({ checkpoint_id }),
    }),

  tools: () => request<{ items: ToolInfo[]; mcp: Record<string, string> }>('/tools'),
  registerTool: (body: Record<string, unknown>) =>
    request<ToolInfo>('/tools', { method: 'POST', body: json(body) }),
  unregisterTool: (name: string) =>
    request<{ ok: boolean }>(`/tools/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  testTool: (name: string, args: Record<string, unknown>) =>
    request<{ ok: boolean; data: unknown }>(`/tools/${encodeURIComponent(name)}/test`, {
      method: 'POST', body: json({ args }),
    }),

  workspaces: () => request<{ items: Workspace[] }>('/workspaces'),
  createWorkspace: (body: { name: string; path: string; description?: string }) =>
    request<Workspace>('/workspaces', { method: 'POST', body: json(body) }),
  deleteWorkspace: (id: string) =>
    request<{ ok: boolean }>(`/workspaces/${id}`, { method: 'DELETE' }),

  fileTree: (workspace: string, path = '', depth = 3) =>
    request<{ root: string; nodes: FileNode[] }>(
      `/files/tree?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(path)}&depth=${depth}`),
  fileContent: (workspace: string, path: string) =>
    request<FileContent>(
      `/files/content?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(path)}`),
  fileRawUrl: (workspace: string, path: string) =>
    `${BASE}/files/raw?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(path)}`,

  gitStatus: (workspace: string) =>
    request<GitStatus>(`/git/status?workspace=${encodeURIComponent(workspace)}`),
  gitStage: (workspace: string, paths: string[]) =>
    request<{ ok: boolean; changes: GitChange[] }>('/git/stage', {
      method: 'POST', body: json({ workspace, paths }),
    }),
  gitUnstage: (workspace: string, paths: string[]) =>
    request<{ ok: boolean; changes: GitChange[] }>('/git/unstage', {
      method: 'POST', body: json({ workspace, paths }),
    }),

  upload: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ ok: boolean; path: string; name: string }>('/upload', {
      method: 'POST', body: fd,
    })
  },
  interrupt: (runId: string) =>
    request<{ ok: boolean }>(`/agent/runs/${runId}/interrupt`, { method: 'POST' }),

  // ---- 反馈闭环（契约 HARDENING §4）----
  submitFeedback: (sessionId: string, body: FeedbackBody) =>
    request<{ ok: boolean; id: string }>(`/sessions/${sessionId}/feedback`, {
      method: 'POST', body: json(body),
    }),
  sessionFeedback: (sessionId: string) =>
    request<{ items: FeedbackItem[] }>(`/sessions/${sessionId}/feedback`),
}

export interface SessionHistory {
  session: Session
  messages: import('@/types').ChatMessage[]
  tasks: import('@/types').TaskInfo[]
  tool_calls: import('@/types').ToolCallRecord[]
  trajectory: import('@/types').TrajectoryNode[]
  stats: Record<string, number>
}

const KEY = {
  sessions: (ws?: string) => ['sessions', ws ?? 'all'] as const,
  history: (id: string) => ['session-history', id] as const,
  tools: ['tools'] as const,
  workspaces: ['workspaces'] as const,
  health: ['health'] as const,
  models: ['models'] as const,
  presets: ['presets'] as const,
  git: (ws: string) => ['git', ws] as const,
  tree: (ws: string, path: string) => ['tree', ws, path] as const,
  feedback: (id: string) => ['session-feedback', id] as const,
}

export const useSessions = (workspace?: string) =>
  useQuery({ queryKey: KEY.sessions(workspace), queryFn: () => api.sessions(workspace) })

export const useSessionHistory = (id: string | null) =>
  useQuery({
    queryKey: KEY.history(id ?? ''),
    queryFn: () => api.sessionHistory(id as string),
    enabled: Boolean(id),
  })

export const useTools = () =>
  useQuery({ queryKey: KEY.tools, queryFn: api.tools, staleTime: 15_000 })

export const useWorkspaces = () =>
  useQuery({ queryKey: KEY.workspaces, queryFn: api.workspaces, staleTime: 15_000 })

export const useHealth = () =>
  useQuery({ queryKey: KEY.health, queryFn: api.health, refetchInterval: 30_000 })

export const useModels = () => useQuery({ queryKey: KEY.models, queryFn: api.models })

export const usePresets = () =>
  useQuery({ queryKey: KEY.presets, queryFn: api.presets, staleTime: 300_000 })

export const useGitStatus = (workspace: string, enabled = true) =>
  useQuery({
    queryKey: KEY.git(workspace),
    queryFn: () => api.gitStatus(workspace),
    enabled: Boolean(workspace) && enabled,
  })

export const useFileTree = (workspace: string, path = '', enabled = true) =>
  useQuery({
    queryKey: KEY.tree(workspace, path),
    queryFn: () => api.fileTree(workspace, path),
    enabled: Boolean(workspace) && enabled,
  })

export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.createSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

export function useDeleteSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.deleteSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

export function useRenameSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.renameSession(id, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

export function usePinSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => api.pinSession(id, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

export function useCreateWorkspace() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.createWorkspace,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workspaces'] }),
  })
}

export function useRegisterTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.registerTool,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

export function useUnregisterTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.unregisterTool,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

/** 会话反馈列表：已评过的消息据此回填星级（契约 §7） */
export const useSessionFeedback = (sessionId: string) =>
  useQuery({
    queryKey: KEY.feedback(sessionId),
    queryFn: () => api.sessionFeedback(sessionId),
    enabled: Boolean(sessionId),
    staleTime: 30_000,
    // 反馈是锦上添花：404（后端未部署该端点）或 401/403（令牌无效）都不该反复重试刷屏，
    // 星级按钮本身不依赖这个查询，失败时照常可点。
    retry: false,
  })

/** 提交反馈：成功后失效该会话的反馈查询，让星星立刻反映最新状态 */
export function useSubmitFeedback(sessionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: FeedbackBody) => api.submitFeedback(sessionId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY.feedback(sessionId) }),
  })
}

export function useGitMutation() {
  const qc = useQueryClient()
  const invalidate = (ws: string) => {
    qc.invalidateQueries({ queryKey: ['git', ws] })
    qc.invalidateQueries({ queryKey: ['tree', ws] })
  }
  return {
    stage: useMutation({
      mutationFn: ({ workspace, paths }: { workspace: string; paths: string[] }) =>
        api.gitStage(workspace, paths),
      onSuccess: (_d, v) => invalidate(v.workspace),
    }),
    unstage: useMutation({
      mutationFn: ({ workspace, paths }: { workspace: string; paths: string[] }) =>
        api.gitUnstage(workspace, paths),
      onSuccess: (_d, v) => invalidate(v.workspace),
    }),
  }
}
