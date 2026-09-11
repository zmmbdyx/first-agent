/**
 * 设置弹窗：通用设置 / 模型配置 / 插件管理 / Agent 预设。
 *
 * 决策：
 * - 显隐完全由 `useAppStore.settingsOpen` 驱动（App 不传 props），
 *   关闭路径统一收敛到 setSettingsOpen(false)：遮罩点击 / Esc / 右上角关闭。
 * - 挂载即卸载：`settingsOpen` 为 false 时直接 return null，
 *   这样每次打开都从服务端最新数据渲染（react-query 缓存命中则瞬时返回），表单草稿也随之复位。
 * - 主题、布局偏好、权限模式默认值**直接读写全局 store**（store 内部负责落 localStorage），
 *   弹窗不维护影子状态，避免两处状态不一致。
 */
import { useEffect, useRef, useState } from 'react'
import { ApiError, api, useHealth, useModels, usePresets, useRegisterTool, useTools, useUnregisterTool, useWorkspaces } from '@/api/client'
import { IconCheck, IconPlus, IconTrash, IconX } from '@/components/icons'
import { getToken, setToken } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/useAppStore'
import type { HealthServiceState, PermissionMode, PresetId, ToolInfo } from '@/types'

type TabId = 'general' | 'model' | 'plugins' | 'presets'

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: 'general', label: '通用设置', hint: '主题 / 布局 / 权限 / 连接' },
  { id: 'model', label: '模型配置', hint: '模型选择与只读参数' },
  { id: 'plugins', label: '插件管理', hint: '内置与自定义工具' },
  { id: 'presets', label: 'Agent 预设', hint: '执行风格与参数' },
]

const PERMISSIONS: { id: PermissionMode; label: string }[] = [
  { id: 'read_only', label: '只读' },
  { id: 'workspace_write', label: '工作区可写' },
  { id: 'full_access', label: '完全访问' },
]

/** 统一的错误文案：优先展示后端 detail */
function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '未知错误'
}

/**
 * 把 `/api/health` 的依赖状态归一成「一行可读文本 + 是否健康」。
 *
 * 后端返回的是结构化对象（如 `{dialect:'sqlite', ok:true}`、`{mode:'in-memory', available:false}`），
 * 早期实现按字符串渲染，直接把对象交给 React 会抛
 * "Objects are not valid as a React child"，**整页崩成空白**（实测打开设置即复现）。
 * 这里对 对象 / 字符串 / 布尔 三种形态一律兜住。
 */
function describeService(
  raw: HealthServiceState | string | boolean | undefined,
  onText = 'ok',
  offText = '不可用',
): { value: string; ok: boolean } {
  if (raw === undefined || raw === null) return { value: '—', ok: false }
  if (typeof raw === 'string') return { value: raw, ok: raw === 'ok' }
  if (typeof raw === 'boolean') return { value: raw ? onText : offText, ok: raw }
  const ok = Boolean(raw.ok ?? raw.available ?? raw.enabled)
  const bits = [raw.mode, raw.dialect, raw.path].filter(Boolean) as string[]
  if (bits.length) return { value: bits.join(' · '), ok }
  return { value: ok ? onText : offText, ok }
}

/** 开关行：左侧文案 + 右侧方形勾选框（无第三方开关组件） */
function ToggleRow({ label, hint, checked, onChange }: {
  label: string
  hint?: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex w-full items-center gap-2 rounded border border-pf-border px-2 py-1.5 text-left transition-colors hover:bg-pf-surface-hover"
    >
      <span
        className={cn(
          'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border',
          checked ? 'border-pf-accent bg-pf-accent text-pf-accent-text' : 'border-pf-border-strong',
        )}
      >
        {checked && <IconCheck size={10} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs text-pf-text">{label}</span>
        {hint && <span className="block text-2xs text-pf-faint">{hint}</span>}
      </span>
    </button>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="pf-label">{label}</span>
      {children}
    </label>
  )
}

/**
 * 访问令牌（契约 HARDENING §7）：密码框 + 失焦即存 + 「保存」按钮给出轻量回执。
 *
 * 为什么不复用组件外的全局状态：令牌的唯一真相是 localStorage['pf.token']，
 * lib/auth 是它的唯一读写口；这里用局部 state 只是「输入框草稿」，
 * 落盘后不再有第二份副本，避免和 lib/auth 的取值分叉。
 * 提示一律走按钮文案（短暂变「已保存」），不用 alert，不打断设置流程。
 */
function TokenRow() {
  const [value, setValue] = useState(() => getToken())
  const [saved, setSaved] = useState(false)
  const savedTimer = useRef<number | null>(null)

  // 卸载时清定时器：设置弹窗关闭即卸载，避免对已卸载组件 setState
  useEffect(() => () => {
    if (savedTimer.current !== null) window.clearTimeout(savedTimer.current)
  }, [])

  const persist = () => {
    setToken(value)
    setValue(getToken()) // 回读一次：trim/清除的最终结果以存储为准
    setSaved(true)
    if (savedTimer.current !== null) window.clearTimeout(savedTimer.current)
    savedTimer.current = window.setTimeout(() => setSaved(false), 1600)
  }

  const filled = value.trim().length > 0

  return (
    <section className="flex flex-col gap-1.5">
      <p className="pf-label">访问令牌</p>
      <div className="flex items-center gap-1.5">
        <input
          type="password"
          className="pf-input min-w-0 flex-1 font-mono"
          value={value}
          placeholder="留空表示不携带凭据"
          autoComplete="off"
          spellCheck={false}
          aria-label="访问令牌"
          onChange={(e) => setValue(e.target.value)}
          onBlur={persist}
        />
        <button
          type="button"
          className={cn('pf-btn shrink-0', saved && 'border-pf-border-strong bg-pf-surface text-pf-text')}
          onClick={persist}
        >
          {saved ? '已保存' : '保存'}
        </button>
      </div>
      <p className="text-2xs text-pf-faint">
        后端开启 AUTH_ENABLED 时必填；仅存于本机 localStorage，不会上传。
      </p>
      {filled && <p className="text-2xs text-pf-faint">当前已配置令牌，所有请求自动携带 Bearer 凭据。</p>}
    </section>
  )
}

export default function SettingsModal() {
  const open = useAppStore((s) => s.settingsOpen)
  const setSettingsOpen = useAppStore((s) => s.setSettingsOpen)
  const [tab, setTab] = useState<TabId>('general')

  // Esc 关闭：仅在打开时挂监听，关闭即卸载，不留全局副作用
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSettingsOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setSettingsOpen])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center pf-scrim p-4"
      role="presentation"
      onClick={() => setSettingsOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        onClick={(e) => e.stopPropagation()}
        className="pf-card flex h-[420px] w-full max-w-[640px] animate-pf-fade-in overflow-hidden bg-pf-elevated"
      >
        {/* 左侧竖排 Tab */}
        <nav className="flex w-[168px] shrink-0 flex-col border-r border-pf-border bg-pf-bg px-1.5 py-2">
          <p className="px-1.5 pb-1.5 text-xs font-medium text-pf-text">设置</p>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                'flex flex-col items-start gap-0.5 rounded px-1.5 py-1 text-left transition-colors',
                tab === t.id
                  ? 'bg-pf-surface text-pf-text'
                  : 'text-pf-muted hover:bg-pf-surface-hover hover:text-pf-text',
              )}
              aria-current={tab === t.id}
            >
              <span className="text-xs">{t.label}</span>
              <span className="text-2xs text-pf-faint">{t.hint}</span>
            </button>
          ))}
        </nav>

        {/* 右侧内容区 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-pf-border px-3">
            <span className="text-xs font-medium">{TABS.find((t) => t.id === tab)?.label}</span>
            <span className="flex-1" />
            <button
              type="button"
              onClick={() => setSettingsOpen(false)}
              className="pf-btn-ghost"
              title="关闭（Esc）"
              aria-label="关闭设置"
            >
              <IconX size={14} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {tab === 'general' && <GeneralTab />}
            {tab === 'model' && <ModelTab />}
            {tab === 'plugins' && <PluginTab />}
            {tab === 'presets' && <PresetTab />}
          </div>
        </div>
      </div>
    </div>
  )
}

/** 通用设置：主题 / 布局偏好 / 权限默认值 / 工作区根目录 / 后端连接状态 */
function GeneralTab() {
  const theme = useAppStore((s) => s.theme)
  const toggleTheme = useAppStore((s) => s.toggleTheme)
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed)
  const setSidebarCollapsed = useAppStore((s) => s.setSidebarCollapsed)
  const panelOpen = useAppStore((s) => s.panelOpen)
  const togglePanel = useAppStore((s) => s.togglePanel)
  const permissionMode = useAppStore((s) => s.permissionMode)
  const setPermissionMode = useAppStore((s) => s.setPermissionMode)
  const workspace = useAppStore((s) => s.workspace)

  const { data: health, isError } = useHealth()
  const { data: workspaces } = useWorkspaces()
  const current = workspaces?.items.find((w) => w.name === workspace || w.id === workspace)

  const services: { label: string; value: string; ok: boolean }[] = health
    ? [
        { label: 'provider', value: health.provider || '—', ok: Boolean(health.provider) },
        { label: 'model', value: health.model || '—', ok: Boolean(health.model) },
        { label: 'db', ...describeService(health.db) },
        { label: 'redis', ...describeService(health.redis) },
        { label: 'vector_store', ...describeService(health.vector_store) },
        { label: 'sandbox', ...describeService(health.sandbox, '启用', '停用') },
      ]
    : []

  return (
    <div className="flex flex-col gap-3">
      <section className="flex flex-col gap-1.5">
        <p className="pf-label">主题</p>
        <div className="flex items-center gap-1.5">
          {(['dark', 'light'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => { if (theme !== t) toggleTheme() }}
              className={cn('pf-btn', theme === t && 'border-pf-border-strong bg-pf-surface text-pf-text')}
            >
              {t === 'dark' ? '深色' : '浅色'}
            </button>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-1.5">
        <p className="pf-label">布局偏好</p>
        <ToggleRow
          label="侧栏默认折叠"
          hint="启动时左侧栏收为 56px 图标轨道"
          checked={sidebarCollapsed}
          onChange={setSidebarCollapsed}
        />
        <ToggleRow
          label="右侧面板默认展开"
          hint="启动时显示文件 / 工具 / Token / Git 面板"
          checked={panelOpen}
          onChange={(v) => togglePanel(v)}
        />
      </section>

      <section className="flex flex-col gap-1.5">
        <p className="pf-label">权限模式默认值</p>
        <div className="flex items-center gap-1.5">
          {PERMISSIONS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setPermissionMode(p.id)}
              className={cn('pf-btn', permissionMode === p.id && 'border-pf-border-strong bg-pf-surface text-pf-text')}
            >
              {p.label}
            </button>
          ))}
        </div>
        <p className="text-2xs text-pf-faint">
          完全访问还受后端 ALLOW_FULL_ACCESS 开关约束，可能被降级为工作区可写。
        </p>
      </section>

      <section className="flex flex-col gap-1">
        <p className="pf-label">工作区根目录</p>
        <p className="break-all rounded border border-pf-border bg-pf-bg px-2 py-1 font-mono text-2xs text-pf-muted">
          {current?.path || workspace}
        </p>
        <p className="font-mono text-2xs text-pf-faint">
          名称 {current?.name || workspace}
          {current ? ` · 文件 ${current.file_count} · ${current.exists ? '存在' : '缺失'}` : ''}
        </p>
      </section>

      <TokenRow />

      <section className="flex flex-col gap-1.5">
        <p className="pf-label">后端连接状态</p>
        {isError && <p className="text-xs text-pf-err">后端未连接</p>}
        {!isError && services.length === 0 && <p className="text-xs text-pf-faint">查询中…</p>}
        {services.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {services.map((s) => (
              <span
                key={s.label}
                className={cn('pf-tag', s.ok ? 'text-pf-muted' : 'text-pf-err')}
                title={`${s.label}: ${s.value}`}
              >
                <span className={cn('inline-block h-1.5 w-1.5 rounded-full', s.ok ? 'bg-pf-ok' : 'bg-pf-err')} />
                {s.label} {s.value}
              </span>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

/** 模型配置：模型列表（可切换） + 由后端环境变量控制的只读参数 */
function ModelTab() {
  const model = useAppStore((s) => s.model)
  const setModel = useAppStore((s) => s.setModel)
  const { data, isLoading, refetch } = useModels()
  const [error, setError] = useState('')

  const currentName = data?.current ?? model

  const pick = (name: string) => {
    setError('')
    api.selectModel(name)
      .then((res) => {
        setModel(res.current || name)
        void refetch()
      })
      .catch((err: unknown) => setError(errorText(err)))
  }

  return (
    <div className="flex flex-col gap-3">
      <section className="flex flex-col gap-1.5">
        <p className="pf-label">当前模型</p>
        <div className="flex items-center gap-1.5">
          <span className="pf-tag">{currentName || '未指定（使用后端默认）'}</span>
          {data?.provider && <span className="pf-tag">provider {data.provider}</span>}
        </div>
        {error && <p className="text-xs text-pf-err">{error}</p>}
      </section>

      <section className="flex flex-col gap-1">
        <p className="pf-label">可选模型</p>
        {isLoading && <p className="text-xs text-pf-faint">加载中…</p>}
        {!isLoading && (data?.models.length ?? 0) === 0 && (
          <p className="text-xs text-pf-faint">后端未返回模型列表，请检查环境变量配置。</p>
        )}
        <div className="flex flex-col gap-1">
          {(data?.models ?? []).map((name) => {
            const active = name === currentName
            return (
              <button
                key={name}
                type="button"
                onClick={() => pick(name)}
                className={cn(
                  'flex items-center gap-2 rounded border px-2 py-1 text-left transition-colors',
                  active
                    ? 'border-pf-border-strong bg-pf-surface'
                    : 'border-pf-border hover:bg-pf-surface-hover',
                )}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{name}</span>
                {active && <IconCheck size={12} className="shrink-0 text-pf-ok" />}
              </button>
            )
          })}
        </div>
      </section>

      <section className="flex flex-col gap-1">
        <p className="pf-label">只读参数（由后端环境变量控制）</p>
        <div className="flex flex-wrap gap-1">
          <span className="pf-tag">LLM_TEMPERATURE</span>
          <span className="pf-tag">MAX_REACT_STEPS</span>
          <span className="pf-tag">LLM_MAX_RETRIES</span>
          <span className="pf-tag">TOOL_MAX_RETRIES</span>
          <span className="pf-tag">MAX_TASKS</span>
          <span className="pf-tag">TASK_TIMEOUT</span>
        </div>
        <p className="text-2xs text-pf-faint">
          温度、最大 ReAct 步数等运行参数不在界面修改，统一由后端 .env 决定，避免前后端取值分叉。
        </p>
      </section>
    </div>
  )
}

/** 插件管理：内置/自定义工具列表 + 注册表单 */
function PluginTab() {
  const { data, isLoading } = useTools()
  const unregister = useUnregisterTool()
  const register = useRegisterTool()

  const [form, setForm] = useState({ name: '', description: '', url: '', method: 'POST' })
  const [formError, setFormError] = useState('')
  const [okText, setOkText] = useState('')

  const tools = data?.items ?? []
  const builtin = tools.filter((t) => t.source === 'builtin')
  const custom = tools.filter((t) => t.source !== 'builtin')

  const submit = () => {
    setFormError('')
    setOkText('')
    if (!form.name.trim()) {
      setFormError('工具名不能为空')
      return
    }
    register.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim(),
        input_schema: { type: 'object', properties: {} },
        kind: 'http',
        config: { url: form.url.trim(), method: form.method },
        enabled: true,
      },
      {
        onSuccess: () => {
          setOkText(`已注册 ${form.name.trim()}`)
          setForm({ name: '', description: '', url: '', method: 'POST' })
        },
        onError: (err) => setFormError(errorText(err)),
      },
    )
  }

  const renderTool = (tool: ToolInfo) => {
    const isBuiltin = tool.source === 'builtin'
    return (
      <li key={tool.name} className="pf-card flex flex-col gap-1 px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-pf-text">{tool.name}</span>
          <span className="pf-tag">{isBuiltin ? '内置' : '自定义'}</span>
          {tool.kind && <span className="pf-tag">{tool.kind}</span>}
          <button
            type="button"
            disabled={isBuiltin || unregister.isPending}
            onClick={() => unregister.mutate(tool.name)}
            className="pf-btn-ghost p-0.5 hover:text-pf-err"
            title={isBuiltin ? '内置工具不可卸载' : '卸载该工具'}
            aria-label={isBuiltin ? '内置工具不可卸载' : '卸载工具'}
          >
            <IconTrash size={12} />
          </button>
        </div>
        <p className="line-clamp-2 text-2xs text-pf-muted">{tool.description || '（无描述）'}</p>
        <p className="font-mono text-2xs text-pf-faint">
          成本 {tool.cost || '—'} · 预估 {tool.avg_seconds ? `${tool.avg_seconds}s` : '—'}
          {isBuiltin ? ' · 内置工具不可卸载' : ''}
        </p>
      </li>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {isLoading && <p className="text-xs text-pf-faint">加载中…</p>}
      {unregister.isError && <p className="text-xs text-pf-err">卸载失败：{errorText(unregister.error)}</p>}

      <section className="flex flex-col gap-1">
        <p className="pf-label">内置工具 {builtin.length}</p>
        <ul className="flex flex-col gap-1">{builtin.map(renderTool)}</ul>
      </section>

      <section className="flex flex-col gap-1">
        <p className="pf-label">自定义工具 {custom.length}</p>
        {custom.length === 0
          ? <p className="text-2xs text-pf-faint">还没有自定义工具</p>
          : <ul className="flex flex-col gap-1">{custom.map(renderTool)}</ul>}
      </section>

      <section className="flex flex-col gap-1.5 border-t border-pf-border pt-2">
        <p className="pf-label">注册自定义工具</p>
        <div className="grid grid-cols-2 gap-1.5">
          <Field label="名称">
            <input
              className="pf-input font-mono"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="my_tool"
            />
          </Field>
          <Field label="请求方法">
            <select
              className="pf-input"
              value={form.method}
              onChange={(e) => setForm({ ...form, method: e.target.value })}
            >
              {['GET', 'POST', 'PUT', 'PATCH'].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>
        </div>
        <Field label="描述">
          <input
            className="pf-input"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder="这个工具做什么"
          />
        </Field>
        <Field label="地址（kind=http）">
          <input
            className="pf-input font-mono"
            value={form.url}
            onChange={(e) => setForm({ ...form, url: e.target.value })}
            placeholder="https://your-endpoint/path"
          />
        </Field>
        {formError && <p className="text-xs text-pf-err">{formError}</p>}
        {okText && <p className="text-xs text-pf-ok">{okText}</p>}
        <div className="flex items-center gap-1.5">
          <button type="button" className="pf-btn-primary" disabled={register.isPending} onClick={submit}>
            <IconPlus size={12} />
            注册工具
          </button>
          <span className="text-2xs text-pf-faint">名称冲突时后端返回 409，会显示在上方。</span>
        </div>
      </section>
    </div>
  )
}

/** Agent 预设：四个预设卡片，点击即设为当前预设 */
function PresetTab() {
  const preset = useAppStore((s) => s.preset)
  const setPreset = useAppStore((s) => s.setPreset)
  const { data, isLoading } = usePresets()

  // 后端不可用时给出契约 5.4 的四个固定预设，保证界面仍有可选项
  const fallback: { id: PresetId; name: string; description: string; params: Record<string, unknown> }[] = [
    { id: 'standard', name: '标准', description: '规划 → 执行 → 综合，均衡的默认执行风格。', params: {} },
    { id: 'minimal', name: '极简', description: '减少步骤与工具调用，快速给出结论。', params: {} },
    { id: 'ptc', name: 'PTC', description: '规划–工具–校验，每步产出都经过校验。', params: {} },
    { id: 'creative', name: '创造', description: '放开探索空间，允许多方案并行尝试。', params: {} },
  ]
  const items = data?.items?.length ? data.items : fallback

  return (
    <div className="flex flex-col gap-2">
      <p className="pf-label">当前预设：{items.find((p) => p.id === preset)?.name ?? preset}</p>
      {isLoading && <p className="text-xs text-pf-faint">加载中…</p>}
      <div className="grid grid-cols-2 gap-1.5">
        {items.map((p) => {
          const active = p.id === preset
          const params = Object.entries(p.params ?? {})
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setPreset(p.id)}
              className={cn(
                'flex flex-col gap-1 rounded border px-2 py-1.5 text-left transition-colors',
                active
                  ? 'border-pf-border-strong bg-pf-surface'
                  : 'border-pf-border hover:bg-pf-surface-hover',
              )}
              aria-pressed={active}
            >
              <span className="flex items-center gap-1.5">
                <span className="text-xs text-pf-text">{p.name}</span>
                <span className="font-mono text-2xs text-pf-faint">{p.id}</span>
                {active && <IconCheck size={12} className="ml-auto shrink-0 text-pf-ok" />}
              </span>
              <span className="text-2xs text-pf-muted">{p.description}</span>
              <span className="flex flex-wrap gap-1">
                {params.length === 0
                  ? <span className="font-mono text-2xs text-pf-faint">params —</span>
                  : params.map(([k, v]) => (
                      <span key={k} className="pf-tag">{k}={String(v)}</span>
                    ))}
              </span>
            </button>
          )
        })}
      </div>
      <p className="text-2xs text-pf-faint">预设决定规划深度与工具策略，切换后对下一次发送生效。</p>
    </div>
  )
}
