/**
 * 会话分组列表与行内操作（置顶 / 重命名 / 删除）。
 *
 * 状态管理决策：
 * - 数据与操作钩子放在 Sidebar（数据源唯一），本组件只接收 `sessions` 与回调，
 *   避免「展示组件」直接依赖 react-query，方便折叠态与展开态复用同一份数据。
 * - 行内重命名 / 删除确认属于**纯局部 UI 状态**，用 useState 记录目标 id 即可，
 *   不进入全局 store（全局 store 只承载跨组件的布局与运行参数）。
 * - 分组按后端返回顺序保序归并：后端已按 `pinned desc, updated_at desc` 排序，
 *   前端不再二次排序，只在组内保持原顺序，保证置顶项始终靠前。
 */
import { useState } from 'react'
import { IconChevronDown, IconChevronRight, IconPencil, IconPin, IconTrash } from '@/components/icons'
import { cn, relativeTime } from '@/lib/utils'
import type { Session } from '@/types'

export interface SessionListProps {
  sessions: Session[]
  activeSessionId: string | null
  /** 折叠态（Rail 56px）：只渲染首字圆牌，hover 用 title 给出完整标题 */
  collapsed: boolean
  busy?: boolean
  onSelect: (session: Session) => void
  onPin: (session: Session) => void
  onRename: (session: Session, title: string) => void
  onDelete: (session: Session) => void
}

/** 按工作区分组：保持后端返回顺序，未知工作区归入「default」 */
function groupByWorkspace(sessions: Session[]): { workspace: string; items: Session[] }[] {
  const groups: { workspace: string; items: Session[] }[] = []
  const index = new Map<string, number>()
  for (const s of sessions) {
    const key = s.workspace || 'default'
    const at = index.get(key)
    if (at === undefined) {
      index.set(key, groups.length)
      groups.push({ workspace: key, items: [s] })
    } else {
      groups[at].items.push(s)
    }
  }
  return groups
}

export default function SessionList({
  sessions, activeSessionId, collapsed, busy = false,
  onSelect, onPin, onRename, onDelete,
}: SessionListProps) {
  /** 已折叠的工作区分组名 */
  const [closedGroups, setClosedGroups] = useState<string[]>([])
  /** 正在行内重命名的会话 id 与草稿标题 */
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  /** 已点击一次删除、等待二次确认的会话 id */
  const [confirmId, setConfirmId] = useState<string | null>(null)

  if (sessions.length === 0) {
    return (
      <div className={cn('px-2 py-3 text-pf-faint', collapsed ? 'text-center text-2xs' : 'text-xs')}>
        {collapsed ? '—' : '还没有会话，点击上方新建'}
      </div>
    )
  }

  const toggleGroup = (ws: string) =>
    setClosedGroups((prev) => (prev.includes(ws) ? prev.filter((x) => x !== ws) : [...prev, ws]))

  const submitRename = (session: Session) => {
    const title = draft.trim()
    setEditingId(null)
    if (title && title !== session.title) onRename(session, title)
  }

  const groups = groupByWorkspace(sessions)

  // ---- 折叠态：极简单列首字圆牌 ----
  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1 px-1 py-1.5">
        {sessions.map((s) => {
          const active = s.id === activeSessionId
          const initial = (s.title || '新').trim().charAt(0) || '新'
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onSelect(s)}
              title={`${s.title || '未命名会话'} · ${relativeTime(s.updated_at)}`}
              aria-label={s.title || '未命名会话'}
              className={cn(
                'relative flex h-8 w-8 items-center justify-center rounded text-xs transition-colors',
                active
                  ? 'bg-pf-surface text-pf-text'
                  : 'text-pf-muted hover:bg-pf-surface-hover hover:text-pf-text',
              )}
            >
              {active && <span className="absolute left-0 top-1.5 h-5 w-[2px] rounded-sm bg-pf-border-strong" />}
              {s.pinned ? <IconPin size={13} /> : <span>{initial}</span>}
            </button>
          )
        })}
      </div>
    )
  }

  // ---- 展开态：按工作区分组 ----
  return (
    <div className="flex flex-col gap-1.5 px-1.5 py-1.5">
      {groups.map((group) => {
        const open = !closedGroups.includes(group.workspace)
        return (
          <div key={group.workspace} className="flex flex-col gap-0.5">
            {/* 组标题：工作区名 + 数量，整行可点击折叠 */}
            <button
              type="button"
              onClick={() => toggleGroup(group.workspace)}
              className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-pf-faint transition-colors hover:text-pf-muted"
              title={open ? '折叠该工作区' : '展开该工作区'}
            >
              {open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />}
              <span className="pf-label truncate">{group.workspace}</span>
              <span className="pf-label font-mono">{group.items.length}</span>
            </button>

            {open && group.items.map((s) => {
              const active = s.id === activeSessionId
              const editing = editingId === s.id
              const confirming = confirmId === s.id
              return (
                <div
                  key={s.id}
                  className={cn(
                    'group relative flex items-center gap-1 rounded px-1.5 py-1 transition-colors',
                    active ? 'bg-pf-surface' : 'hover:bg-pf-surface-hover',
                  )}
                >
                  {/* 选中态：左侧 2px 竖条 */}
                  {active && (
                    <span className="absolute left-0 top-1.5 h-[calc(100%-12px)] w-[2px] rounded-sm bg-pf-border-strong" />
                  )}

                  {editing ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => setEditingId(null)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') submitRename(s)
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      className="pf-input px-1 py-0.5 text-xs"
                      aria-label="重命名会话"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => onSelect(s)}
                      className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left"
                      title={s.title || '未命名会话'}
                    >
                      <span className="flex w-full items-center gap-1">
                        {s.pinned && <IconPin size={11} className="shrink-0 text-pf-muted" />}
                        <span className={cn('truncate text-xs', active ? 'text-pf-text' : 'text-pf-muted')}>
                          {s.title || '未命名会话'}
                        </span>
                      </span>
                      <span className="font-mono text-2xs text-pf-faint">{relativeTime(s.updated_at)}</span>
                    </button>
                  )}

                  {/* 行内操作：hover / 选中 / 待确认时显示，避免列表被图标噪声填满 */}
                  {!editing && (
                    <span
                      className={cn(
                        'flex shrink-0 items-center gap-0.5',
                        confirming || active ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
                      )}
                    >
                      {confirming ? (
                        <>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => { setConfirmId(null); onDelete(s) }}
                            className="rounded px-1 text-2xs text-pf-err transition-colors hover:bg-pf-surface-hover"
                            title="确认删除"
                          >
                            删除
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmId(null)}
                            className="rounded px-1 text-2xs text-pf-faint transition-colors hover:bg-pf-surface-hover hover:text-pf-text"
                            title="取消"
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => onPin(s)}
                            className="pf-btn-ghost p-0.5"
                            title={s.pinned ? '取消置顶' : '置顶'}
                            aria-label={s.pinned ? '取消置顶' : '置顶'}
                          >
                            <IconPin size={12} />
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => { setEditingId(s.id); setDraft(s.title || '') }}
                            className="pf-btn-ghost p-0.5"
                            title="重命名"
                            aria-label="重命名"
                          >
                            <IconPencil size={12} />
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setConfirmId(s.id)}
                            className="pf-btn-ghost p-0.5 hover:text-pf-err"
                            title="删除"
                            aria-label="删除"
                          >
                            <IconTrash size={12} />
                          </button>
                        </>
                      )}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
