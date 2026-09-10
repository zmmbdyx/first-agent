/**
 * 输入区：全宽 textarea + @ 引用工作区文件 + 上传 + 工作区/预设/权限/模型/推理强度 + 发送⇄停止。
 *
 * 布局决策：
 * - 输入区整体固定在三栏布局的下沿（由 ChatArea 的 flex 列持有，本组件不脱离文档流），
 *   上方一行"参数栏"承载环境信息与运行参数，下方两行以内是输入框与动作按钮，
 *   保证无论窗口多高，输入框永远贴底且不遮挡消息阅读区；
 * - textarea 自动增高 2–10 行：低于 2 行太窄不利于写长任务，超过 10 行开始在框内滚动，
 *   否则输入框会把消息区挤没；
 * - @ 引用浮层用"候选列表 + 键盘导航"（↑↓ / Enter / Esc），
 *   因为鼠标点击会让用户离开键盘流，写长任务时体验很差。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import { api, useFileTree, useModels, useWorkspaces } from '@/api/client'
import {
  IconAlert, IconChevronDown, IconFile, IconFolder, IconRotate, IconSend, IconSpinner,
  IconStop, IconUpload, IconX,
} from '@/components/icons'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/useAppStore'
import { useRunStore } from '@/store/useRunStore'
import type { FileNode, PermissionMode, PresetId, ReasoningEffort } from '@/types'

export interface InputBoxProps {
  running: boolean
  onSend: (task: string, opts?: { resume?: boolean }) => void
  onStop: () => void
  onRollback: () => void
  /** 外部（空状态示例任务）填入文本的入口，由 ChatArea 通过 ref 调用 */
  registerInsert?: (insert: (text: string) => void) => void
}

const PRESETS: { id: PresetId; label: string }[] = [
  { id: 'standard', label: '标准' },
  { id: 'minimal', label: '极简' },
  // PTC = 规划–工具–校验
  { id: 'ptc', label: 'PTC' },
  { id: 'creative', label: '创造' },
]

const PERMISSIONS: { id: PermissionMode; label: string }[] = [
  { id: 'read_only', label: '只读' },
  { id: 'workspace_write', label: '工作区可写' },
  { id: 'full_access', label: '完全访问' },
]

const EFFORTS: { id: ReasoningEffort; label: string }[] = [
  { id: 'low', label: '低' },
  { id: 'medium', label: '中' },
  { id: 'high', label: '高' },
]

/** 自动增高区间（行）：2 行起步，10 行封顶 */
const MIN_ROWS = 2
const MAX_ROWS = 10
const LINE_HEIGHT = 20
const BOX_PADDING = 16

export default function InputBox({
  running, onSend, onStop, onRollback, registerInsert,
}: InputBoxProps) {
  const workspace = useAppStore((s) => s.workspace)
  const setWorkspace = useAppStore((s) => s.setWorkspace)
  const preset = useAppStore((s) => s.preset)
  const setPreset = useAppStore((s) => s.setPreset)
  const permissionMode = useAppStore((s) => s.permissionMode)
  const setPermissionMode = useAppStore((s) => s.setPermissionMode)
  const model = useAppStore((s) => s.model)
  const setModel = useAppStore((s) => s.setModel)
  const reasoningEffort = useAppStore((s) => s.reasoningEffort)
  const setReasoningEffort = useAppStore((s) => s.setReasoningEffort)

  const status = useRunStore((s) => s.status)
  const pendingQuestion = useRunStore((s) => s.pendingQuestion)

  const [text, setText] = useState('')
  const [composing, setComposing] = useState(false)
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null)
  const [mentionIndex, setMentionIndex] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')

  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: workspaces } = useWorkspaces()
  const { data: models } = useModels()
  // 只在浮层打开时拉取文件树，避免每次进入页面都打一次 /files/tree
  const { data: tree } = useFileTree(workspace, '', mention !== null)

  const candidates = useMemo(() => {
    if (!mention) return []
    const files = flattenFiles(tree?.nodes ?? [])
    const q = mention.query.toLowerCase()
    if (!q) return files.slice(0, 50)
    // 前缀命中优先于包含命中：输入 @re 时 report.md 应排在 appendix/re.md 之前
    const starts: string[] = []
    const contains: string[] = []
    for (const f of files) {
      const lower = f.toLowerCase()
      if (lower.startsWith(q)) starts.push(f)
      else if (lower.includes(q)) contains.push(f)
    }
    return [...starts, ...contains].slice(0, 50)
  }, [mention, tree])

  // 自动增高：先复位再按内容量取，避免删除文本后高度不回落
  useLayoutEffect(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    const max = MAX_ROWS * LINE_HEIGHT + BOX_PADDING
    const min = MIN_ROWS * LINE_HEIGHT + BOX_PADDING
    el.style.height = `${Math.min(Math.max(el.scrollHeight, min), max)}px`
  }, [text])

  // 外部（空状态示例任务）填入文本：写入后聚焦并把光标放到末尾
  useEffect(() => {
    registerInsert?.((value) => {
      setText(value)
      setMention(null)
      requestAnimationFrame(() => {
        const el = taRef.current
        if (!el) return
        el.focus()
        el.setSelectionRange(value.length, value.length)
      })
    })
  }, [registerInsert])

  /** 重新解析光标前的 @ 片段：决定浮层开合与候选过滤词 */
  const syncMention = (value: string, caret: number) => {
    const before = value.slice(0, caret)
    const at = before.lastIndexOf('@')
    if (at === -1) {
      setMention(null)
      return
    }
    // @ 必须位于行首或空白之后，邮件/装饰性字符里的 @ 不触发引用
    const prev = at === 0 ? ' ' : before[at - 1]
    if (!/\s/.test(prev)) {
      setMention(null)
      return
    }
    const query = before.slice(at + 1)
    if (/[\s\n]/.test(query)) {
      setMention(null)
      return
    }
    setMention({ start: at, query })
    setMentionIndex(0)
  }

  const applyText = (value: string, caret?: number) => {
    setText(value)
    const pos = caret ?? value.length
    syncMention(value, pos)
  }

  /** 确认引用：把 @query 整段替换为 @路径 + 空格 */
  const confirmMention = (path?: string) => {
    const picked = path ?? candidates[mentionIndex]
    if (!mention || !picked) return
    const el = taRef.current
    const caret = el?.selectionStart ?? text.length
    const next = `${text.slice(0, mention.start)}@${picked} ${text.slice(caret)}`
    setText(next)
    setMention(null)
    const pos = mention.start + picked.length + 2
    requestAnimationFrame(() => {
      const node = taRef.current
      if (!node) return
      node.focus()
      node.setSelectionRange(pos, pos)
    })
  }

  const canSend = text.trim().length > 0 && !running

  const send = () => {
    if (!canSend) return
    // ask_user 挂起时，本次发送是对图中断的补充信息 → resume=true
    onSend(text, { resume: status === 'awaiting_input' })
    setText('')
    setMention(null)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 打开候选浮层时，方向键/Enter/Esc 归浮层使用
    if (mention && candidates.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setMentionIndex((i) => (i + 1) % candidates.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setMentionIndex((i) => (i - 1 + candidates.length) % candidates.length)
        return
      }
      if (e.key === 'Enter' && !composing && !e.shiftKey) {
        e.preventDefault()
        confirmMention()
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setMention(null)
        return
      }
    }
    // 中文输入法组合中的 Enter 是"选词确认"，不能当作发送
    if (e.key === 'Enter' && !e.shiftKey && !composing && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send()
    }
  }

  const onUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = '' // 允许连续上传同一个文件
    if (!file) return
    setUploading(true)
    setUploadError('')
    try {
      const res = await api.upload(file)
      // 上传成功 → 以 @路径 形式插入，让后端明确知道要处理哪个文件
      applyText(text ? `${text.replace(/\s*$/, '')} @${res.path} ` : `@${res.path} `)
    } catch (err) {
      setUploadError((err as Error).message || '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const modelOptions = models?.models ?? []
  const workspaceOptions = workspaces?.items ?? []

  return (
    <div className="relative shrink-0 border-t border-pf-border bg-pf-elevated">
      {/* @ 引用候选浮层：向上弹出，避免遮挡输入框本身 */}
      {mention && (
        <div className="absolute bottom-full left-3 z-20 mb-1 max-h-64 w-80 overflow-y-auto rounded-md border border-pf-border bg-pf-elevated shadow-lg">
          <div className="sticky top-0 flex items-center gap-1.5 border-b border-pf-border bg-pf-elevated px-2 py-1">
            <IconFolder size={12} />
            <span className="pf-label">引用工作区文件</span>
            <div className="flex-1" />
            <span className="font-mono text-2xs text-pf-faint">↑↓ 选择 · Enter 确认 · Esc 关闭</span>
          </div>
          {candidates.length === 0 ? (
            <p className="px-2 py-2 text-xs text-pf-faint">没有匹配的文件。</p>
          ) : (
            <ul>
              {candidates.map((path, i) => (
                <li key={path}>
                  <button
                    type="button"
                    className={cn(
                      'flex w-full items-center gap-1.5 px-2 py-1 text-left',
                      i === mentionIndex ? 'bg-pf-surface text-pf-text' : 'text-pf-muted hover:bg-pf-surface-hover',
                    )}
                    onMouseEnter={() => setMentionIndex(i)}
                    // 用 onMouseDown 抢在 textarea 失焦前确认，避免浮层先关闭
                    onMouseDown={(e) => {
                      e.preventDefault()
                      confirmMention(path)
                    }}
                  >
                    <IconFile size={12} />
                    <span className="truncate font-mono text-xs">{path}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 参数栏：左环境信息（工作区路径 + 权限），右运行参数（预设/权限/模型/推理强度） */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 pt-2">
        <span className="flex min-w-0 items-center gap-1 text-pf-faint">
          <IconFolder size={12} />
          <span className="truncate font-mono text-2xs text-pf-muted" title={workspace}>
            {workspacePath(workspaceOptions, workspace)}
          </span>
        </span>
        <span className="pf-tag shrink-0">{permissionLabel(permissionMode)}</span>

        <div className="flex-1" />

        <ParamSelect
          label="预设"
          value={preset}
          options={PRESETS.map((p) => ({ value: p.id, label: p.label }))}
          onChange={(v) => setPreset(v as PresetId)}
        />
        <ParamSelect
          label="权限"
          value={permissionMode}
          options={PERMISSIONS.map((p) => ({ value: p.id, label: p.label }))}
          onChange={(v) => setPermissionMode(v as PermissionMode)}
        />
        <ParamSelect
          label="模型"
          value={model}
          options={[
            { value: '', label: models?.current ? `默认 · ${models.current}` : '默认' },
            ...modelOptions.map((m) => ({ value: m, label: m })),
          ]}
          onChange={setModel}
        />
        <ParamSelect
          label="推理"
          value={reasoningEffort}
          options={EFFORTS.map((p) => ({ value: p.id, label: p.label }))}
          onChange={(v) => setReasoningEffort(v as ReasoningEffort)}
        />
      </div>

      {/* ask_user 挂起提示：模型在等补充信息，发送即恢复执行 */}
      {status === 'awaiting_input' && pendingQuestion && (
        <div className="mx-3 mt-1.5 flex items-start gap-1.5 rounded border border-pf-warn bg-pf-surface px-2 py-1">
          <span className="mt-0.5 shrink-0 text-pf-warn">
            <IconAlert size={12} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-pf-text">
              <span className="pf-label mr-1">待补充</span>
              {pendingQuestion}
            </p>
            <p className="text-2xs text-pf-faint">补充信息后继续发送</p>
          </div>
        </div>
      )}

      {uploadError && (
        <p className="mx-3 mt-1.5 text-2xs text-pf-err">上传失败：{uploadError}</p>
      )}
      {/* 输入行：左动作按钮、中 textarea、右发送⇄停止 */}
      <div className="flex items-end gap-1.5 px-3 pb-2 pt-1.5">
        <div className="flex shrink-0 items-center gap-1 pb-0.5">
          <input ref={fileRef} type="file" hidden onChange={onUpload} />
          <button
            type="button"
            className="pf-btn-ghost"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            title="上传材料并引用"
            aria-label="上传材料"
          >
            {uploading ? <IconSpinner size={14} /> : <IconUpload size={14} />}
          </button>
          <WorkspacePicker
            value={workspace}
            // 运行请求里的 workspace 是"标识或名称"（契约 2.1），这里统一送工作区名称，
            // 与默认值 `default` 的语义保持一致，避免把 UUID 传给后端
            options={workspaceOptions.map((w) => w.name)}
            onChange={setWorkspace}
          />
        </div>

        <textarea
          ref={taRef}
          className="pf-input flex-1 resize-none py-1.5 leading-5"
          style={{ height: MIN_ROWS * LINE_HEIGHT + BOX_PADDING }}
          value={text}
          placeholder="描述任务，或输入 @ 引用工作区文件…"
          spellCheck={false}
          onChange={(e) => applyText(e.target.value, e.target.selectionStart)}
          onClick={(e) => syncMention(text, (e.target as HTMLTextAreaElement).selectionStart)}
          onKeyDown={onKeyDown}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={(e) => {
            setComposing(false)
            applyText(e.currentTarget.value, e.currentTarget.selectionStart)
          }}
          onBlur={() => {
            // 延迟关闭，给候选行的 onMouseDown 留出确认时机
            window.setTimeout(() => setMention(null), 120)
          }}
        />

        <div className="flex shrink-0 items-center gap-1 pb-0.5">
          {running && (
            <>
              <button type="button" className="pf-btn gap-1" onClick={onStop} title="中断本次执行">
                <IconX size={12} />
                中断
              </button>
              <button
                type="button"
                className="pf-btn gap-1"
                onClick={onRollback}
                title="回滚到本次执行起点"
              >
                <IconRotate size={12} />
                回滚
              </button>
            </>
          )}
          {running ? (
            <button
              type="button"
              className="pf-btn shrink-0 gap-1"
              onClick={onStop}
              title="停止执行"
              aria-label="停止执行"
            >
              <IconStop size={13} />
            </button>
          ) : (
            <button
              type="button"
              className="pf-btn-primary shrink-0 gap-1"
              onClick={send}
              disabled={!canSend}
              title="发送（Enter）"
              aria-label="发送"
            >
              <IconSend size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/** 参数选择器：统一「标签 + 原生 select」的紧凑样式，避免为下拉自绘一套浮层 */
function ParamSelect({ label, value, options, onChange }: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
}) {
  return (
    <label className="flex shrink-0 items-center gap-1" title={label}>
      <span className="pf-label">{label}</span>
      <span className="relative">
        <select
          className="max-w-[9.5rem] appearance-none truncate rounded border border-pf-border
                     bg-pf-surface py-0 pl-1.5 pr-5 font-mono text-2xs text-pf-text
                     focus:border-pf-border-strong focus:outline-none"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-1 top-1/2 -translate-y-1/2 text-pf-faint">
          <IconChevronDown size={10} />
        </span>
      </span>
    </label>
  )
}

/** 工作区选择器：图标按钮 + 原生 select 叠放，保持与上传按钮同一视觉重量 */
function WorkspacePicker({ value, options, onChange }: {
  value: string
  options: string[]
  onChange: (workspace: string) => void
}) {
  return (
    <span className="relative inline-flex items-center" title="选择工作区">
      <span className="pointer-events-none absolute left-1 text-pf-muted">
        <IconFolder size={13} />
      </span>
      <select
        className="max-w-[8rem] appearance-none truncate rounded border border-pf-border
                   bg-pf-surface py-0.5 pl-6 pr-4 font-mono text-2xs text-pf-muted focus:outline-none"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="选择工作区"
      >
        {options.length === 0 && <option value={value}>{value}</option>}
        {options.map((name) => (
          <option key={name} value={name}>{name}</option>
        ))}
      </select>
      <span className="pointer-events-none absolute right-1 text-pf-faint">
        <IconChevronDown size={10} />
      </span>
    </span>
  )
}

/** 文件树 → 相对路径列表（只取文件，目录由路径前缀表达） */
function flattenFiles(nodes: FileNode[]): string[] {
  const out: string[] = []
  const walk = (list: FileNode[]) => {
    for (const node of list) {
      if (node.type === 'file') out.push(node.path)
      else if (node.children?.length) walk(node.children)
    }
  }
  walk(nodes)
  return out
}

function permissionLabel(mode: PermissionMode): string {
  return PERMISSIONS.find((p) => p.id === mode)?.label ?? mode
}

/** 参数栏展示的是"当前工作区路径"：优先取后端返回的真实路径，回落到工作区标识 */
function workspacePath(
  items: { id: string; name: string; path: string }[],
  ident: string,
): string {
  const hit = items.find((w) => w.id === ident || w.name === ident)
  return hit ? hit.path : ident
}
