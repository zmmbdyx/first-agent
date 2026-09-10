/**
 * 应用外壳：三栏布局 + 顶栏 + 弹窗 + 运行编排。
 * 布局契约：左侧栏（56/280px，可折叠）/ 中央对话区（自适应）/ 右侧面板（360px，可折叠）；
 * 视口 < 1024px 时右侧面板自动关闭、左侧栏强制紧凑 Rail。
 */
import { useEffect } from 'react'
import ChatArea from '@/components/ChatArea'
import RightPanel from '@/components/RightPanel'
import SettingsModal from '@/components/SettingsModal'
import Sidebar from '@/components/Sidebar'
import Logo from '@/components/Logo'
import { IconGauge, IconPanelLeft, IconPanelRight, IconSun, IconMoon } from '@/components/icons'
import { useHealth } from '@/api/client'
import { api } from '@/api/client'
import { useAgentRun } from '@/hooks/useAgentRun'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/useAppStore'
import { useRunStore } from '@/store/useRunStore'

export default function App() {
  const {
    theme, toggleTheme, sidebarCollapsed, toggleSidebar, panelOpen, togglePanel,
    setViewportCompact, activeSessionId, setPanelTab,
  } = useAppStore()

  const { running, start, stop, rollback } = useAgentRun()
  const loadHistory = useRunStore((s) => s.loadHistory)
  const resetRun = useRunStore((s) => s.reset)
  const status = useRunStore((s) => s.status)

  const { data: health } = useHealth()

  // 响应式：<1024px 收起右栏与侧栏
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1023px)')
    const apply = () => setViewportCompact(mq.matches)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [setViewportCompact])

  // 切换会话时拉一次历史。
  // 关键：仅当"界面上的运行状态不属于该会话"时才覆盖本地状态——否则一次运行刚结束、
  // react-query 里仍是运行前拉到的空历史，会立刻把消息与轨迹清空（曾实测复现）。
  useEffect(() => {
    if (!activeSessionId) return
    const current = useRunStore.getState()
    if (current.sessionId === activeSessionId && current.messages.length > 0) return

    let cancelled = false
    api.sessionHistory(activeSessionId)
      .then((h) => {
        if (cancelled) return
        loadHistory({
          messages: h.messages,
          tasks: h.tasks,
          trajectory: h.trajectory,
          tool_calls: h.tool_calls,
          sessionId: activeSessionId,
          status: h.session.status,
        })
      })
      .catch(() => {
        if (!cancelled) resetRun(activeSessionId)
      })
    return () => {
      cancelled = true
    }
  }, [activeSessionId, loadHistory, resetRun])

  const connection = health ? 'ok' : 'down'

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-pf-bg text-pf-text">
      {/* 顶栏：极简细条，仅承载身份与全局动作 */}
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-pf-border px-2.5">
        <button
          type="button"
          className="pf-btn-ghost"
          onClick={toggleSidebar}
          title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
          aria-label="切换侧边栏"
        >
          <IconPanelLeft size={15} />
        </button>

        <div className="flex items-center gap-1.5">
          <Logo size={15} />
          <span className="text-xs font-medium tracking-tight">拓径</span>
          <span className="font-mono text-2xs text-pf-faint">PATHFORGE</span>
        </div>

        <div className="flex-1" />

        <span className="hidden items-center gap-1.5 sm:flex">
          <span
            className={cn(
              'inline-block h-1.5 w-1.5 rounded-full',
              connection === 'ok' ? 'bg-pf-ok' : 'bg-pf-err',
            )}
          />
          <span className="font-mono text-2xs text-pf-faint">
            {health ? `${health.provider} · ${health.model || 'n/a'}` : '后端未连接'}
          </span>
        </span>

        <span className="pf-tag" title="当前运行状态">
          {statusLabel(status)}
        </span>

        <button
          type="button"
          className="pf-btn-ghost"
          onClick={() => setPanelTab('tokens')}
          title="Token 统计"
          aria-label="Token 统计"
        >
          <IconGauge size={15} />
        </button>
        <button
          type="button"
          className="pf-btn-ghost"
          onClick={toggleTheme}
          title={theme === 'dark' ? '切换到浅色' : '切换到深色'}
          aria-label="切换主题"
        >
          {theme === 'dark' ? <IconSun size={15} /> : <IconMoon size={15} />}
        </button>
        <button
          type="button"
          className="pf-btn-ghost"
          onClick={() => togglePanel()}
          title={panelOpen ? '收起右侧面板' : '展开右侧面板'}
          aria-label="切换右侧面板"
        >
          <IconPanelRight size={15} />
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <ChatArea running={running} onSend={start} onStop={stop} onRollback={rollback} />
        </main>
        {panelOpen && <RightPanel />}
      </div>

      <SettingsModal />
    </div>
  )
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    idle: '待命', queued: '排队中', running: '正在执行', awaiting_input: '等待补充',
    done: '已完成', failed: '失败', interrupted: '已中断',
  }
  return map[status] ?? status
}
