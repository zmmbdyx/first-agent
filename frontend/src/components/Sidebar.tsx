/**
 * 左侧栏：折叠 Rail(56px) ⇄ 展开(280px)。
 *
 * 决策：
 * - 宽度变化用 `transition-[width] duration-150`，只动宽度不做位移，避免中央对话区抖动。
 * - 会话数据钩子只在这里调用一次，折叠态与展开态共用同一份 `sessions`，
 *   由子组件 SessionList 决定渲染形态（圆牌 / 分组列表），避免重复请求。
 * - 新建会话流程固定为：创建 → 切换 activeSession → 重置 run store，
 *   顺序不可颠倒（reset 需要新会话 id 作为归属，否则事件会挂到旧会话上）。
 */
import { useMemo } from 'react'
import SessionList from '@/components/SessionList'
import Logo from '@/components/Logo'
import { IconPanelLeft, IconPlus, IconSettings } from '@/components/icons'
import {
  useCreateSession, useDeleteSession, usePinSession, useRenameSession, useSessions,
} from '@/api/client'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/useAppStore'
import { useRunStore } from '@/store/useRunStore'
import type { Session } from '@/types'

export default function Sidebar() {
  const collapsed = useAppStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)
  const setSettingsOpen = useAppStore((s) => s.setSettingsOpen)
  const activeSessionId = useAppStore((s) => s.activeSessionId)
  const setActiveSession = useAppStore((s) => s.setActiveSession)
  const workspace = useAppStore((s) => s.workspace)
  const preset = useAppStore((s) => s.preset)

  const { data, isLoading } = useSessions(workspace)
  const createSession = useCreateSession()
  const pinSession = usePinSession()
  const renameSession = useRenameSession()
  const deleteSession = useDeleteSession()

  const sessions = useMemo(() => data?.items ?? [], [data])
  const busy = createSession.isPending || pinSession.isPending
    || renameSession.isPending || deleteSession.isPending

  const handleCreate = () => {
    createSession.mutate(
      { workspace, preset },
      {
        onSuccess: (session) => {
          setActiveSession(session.id)
          // 新会话没有任何运行数据，清空运行态避免残留上一条轨迹
          useRunStore.getState().reset(session.id)
        },
      },
    )
  }

  const handleSelect = (session: Session) => {
    if (session.id === activeSessionId) return
    setActiveSession(session.id)
  }

  const handlePin = (session: Session) =>
    pinSession.mutate({ id: session.id, pinned: !session.pinned })

  const handleRename = (session: Session, title: string) =>
    renameSession.mutate({ id: session.id, title })

  const handleDelete = (session: Session) => {
    deleteSession.mutate(session.id, {
      onSuccess: () => {
        // 删除的是当前会话：清空选中，避免中央区停留在一个已不存在的会话
        if (session.id === activeSessionId) {
          setActiveSession(null)
          useRunStore.getState().reset(null)
        }
      },
    })
  }

  return (
    <aside
      className={cn(
        'flex h-full shrink-0 flex-col border-r border-pf-border bg-pf-elevated',
        'transition-[width] duration-150',
        collapsed ? 'w-rail' : 'w-sidebar',
      )}
    >
      {/* 顶部：品牌 + 折叠开关（折叠态只留图标按钮） */}
      <div className={cn('flex h-9 shrink-0 items-center border-b border-pf-border', collapsed ? 'justify-center px-1' : 'gap-1.5 px-2')}>
        {!collapsed && (
          <span className="flex min-w-0 flex-1 items-center gap-1.5">
            <Logo size={16} className="text-pf-text" />
            <span className="truncate text-xs font-medium tracking-tight">拓径</span>
            <span className="truncate font-mono text-2xs text-pf-faint">PATHFORGE</span>
          </span>
        )}
        <button
          type="button"
          onClick={toggleSidebar}
          className="pf-btn-ghost"
          title={collapsed ? '展开侧边栏' : '折叠侧边栏'}
          aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'}
        >
          <IconPanelLeft size={15} />
        </button>
      </div>

      {/* 新建会话：深色高亮主操作，全宽 */}
      <div className={cn('shrink-0', collapsed ? 'px-1 py-1.5' : 'px-2 py-2')}>
        <button
          type="button"
          onClick={handleCreate}
          disabled={createSession.isPending}
          className={cn('pf-btn-primary', collapsed ? 'w-9 px-0 py-1.5' : 'w-full py-1.5')}
          title="新建会话"
          aria-label="新建会话"
        >
          <IconPlus size={14} />
          {!collapsed && <span>新建会话</span>}
        </button>
      </div>

      {/* 会话列表：唯一滚动区 */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        {isLoading && sessions.length === 0 ? (
          <div className={cn('px-2 py-3 text-pf-faint', collapsed ? 'text-center text-2xs' : 'text-xs')}>
            {collapsed ? '…' : '加载中…'}
          </div>
        ) : (
          <SessionList
            sessions={sessions}
            activeSessionId={activeSessionId}
            collapsed={collapsed}
            busy={busy}
            onSelect={handleSelect}
            onPin={handlePin}
            onRename={handleRename}
            onDelete={handleDelete}
          />
        )}
      </div>

      {/* 底部：设置入口 */}
      <div className={cn('shrink-0 border-t border-pf-border', collapsed ? 'p-1' : 'p-1.5')}>
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className={cn(
            'pf-btn-ghost w-full text-xs',
            collapsed ? 'justify-center p-1.5' : 'justify-start gap-1.5 px-2 py-1.5',
          )}
          title="设置"
          aria-label="设置"
        >
          <IconSettings size={14} />
          {!collapsed && <span>设置</span>}
        </button>
      </div>
    </aside>
  )
}
