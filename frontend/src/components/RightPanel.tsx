/**
 * 右侧面板容器：360px 固定宽 + 四个 Tab（文件 / 工具 / Token / Git）。
 *
 * 决策：
 * - 四个面板**全部保持挂载**，只切换 `hidden` 显隐：
 *   react-query 缓存 + 本地展开状态（文件树展开项、工具详情展开项、Git 勾选）在切换 Tab 后不丢失，
 *   也不产生「切回来重新请求」的闪烁。
 * - 仅可见的 Tab 才 `enabled`（传给面板的 active 标志），避免隐藏面板在后台轮询。
 * - FileTreePanel / GitPanel 依赖 workspace，从全局 store 读取，App 只负责是否渲染本组件。
 */
import FileTreePanel from '@/components/FileTreePanel'
import GitPanel from '@/components/GitPanel'
import TokenPanel from '@/components/TokenPanel'
import ToolPanel from '@/components/ToolPanel'
import { IconFolder, IconGauge, IconGit, IconPanelRight, IconTool } from '@/components/icons'
import { cn } from '@/lib/utils'
import { useAppStore, type PanelTab } from '@/store/useAppStore'

const TABS: { id: PanelTab; label: string; Icon: typeof IconFolder }[] = [
  { id: 'files', label: '文件', Icon: IconFolder },
  { id: 'tools', label: '工具', Icon: IconTool },
  { id: 'tokens', label: 'Token', Icon: IconGauge },
  { id: 'git', label: 'Git', Icon: IconGit },
]

export default function RightPanel() {
  const panelTab = useAppStore((s) => s.panelTab)
  const setPanelTab = useAppStore((s) => s.setPanelTab)
  const togglePanel = useAppStore((s) => s.togglePanel)
  const workspace = useAppStore((s) => s.workspace)

  return (
    <aside className="flex h-full w-panel shrink-0 flex-col border-l border-pf-border bg-pf-bg">
      {/* Tab 栏 */}
      <div className="flex h-9 shrink-0 items-center gap-0.5 border-b border-pf-border px-1.5">
        {TABS.map(({ id, label, Icon }) => {
          const active = panelTab === id
          return (
            <button
              key={id}
              type="button"
              onClick={() => setPanelTab(id)}
              className={cn(
                'inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs transition-colors',
                active
                  ? 'bg-pf-surface text-pf-text'
                  : 'text-pf-muted hover:bg-pf-surface-hover hover:text-pf-text',
              )}
              title={label}
              aria-pressed={active}
            >
              <Icon size={13} />
              <span>{label}</span>
            </button>
          )
        })}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => togglePanel(false)}
          className="pf-btn-ghost"
          title="收起右侧面板"
          aria-label="收起右侧面板"
        >
          <IconPanelRight size={15} />
        </button>
      </div>

      {/* 内容区：四个面板常驻挂载，靠 hidden 切换，保证内部状态不丢 */}
      <div className="relative min-h-0 flex-1">
        <div className={cn('h-full', panelTab !== 'files' && 'hidden')}>
          <FileTreePanel workspace={workspace} active={panelTab === 'files'} />
        </div>
        <div className={cn('h-full', panelTab !== 'tools' && 'hidden')}>
          <ToolPanel />
        </div>
        <div className={cn('h-full overflow-y-auto', panelTab !== 'tokens' && 'hidden')}>
          <TokenPanel />
        </div>
        <div className={cn('h-full', panelTab !== 'git' && 'hidden')}>
          <GitPanel workspace={workspace} active={panelTab === 'git'} />
        </div>
      </div>
    </aside>
  )
}
