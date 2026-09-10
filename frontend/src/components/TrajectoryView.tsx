/**
 * 执行轨迹可视化：一条"读条带"表达节点耗时占比，下方是可折叠的节点详情面板。
 *
 * 设计决策（三条核心规则）：
 * 1. 读条宽度 = 节点耗时占比（flex-grow 分配），并给极短节点兜底 `min-w-[6px]`：
 *    否则一次 3ms 的 recall 会在视觉上消失，用户会以为轨迹漏了一环；
 * 2. 读条底色用灰度明度梯度 hsl(0 0% X%)（32%→62%），表达"这是第几段"；
 *    状态另用语义色描边/填充表达（运行 warn / 完成 ok / 失败 err / 跳过 faint），
 *    两套编码互不干扰：灰度回答"在哪"，语义色回答"怎么样了"；
 * 3. 悬停 = 预览（不改变选中），点击 = 锁定选中，再点一次取消；
 *    这样既能快速扫过所有节点，又能把某个节点"钉"在详情面板里细看。
 */
import { useEffect, useMemo, useState } from 'react'
import { IconAlert, IconCheck, IconChevronDown, IconChevronRight, IconTool } from '@/components/icons'
import { argsPreview, cn, formatDuration, formatNumber } from '@/lib/utils'
import { useRunStore } from '@/store/useRunStore'
import type { NodeStatus, ToolCallRecord, TrajectoryNode } from '@/types'

export interface TrajectoryViewProps {
  /** 整个轨迹块是否可折叠，默认 true */
  collapsible?: boolean
}

/** 节点状态 → 中文标签 + 语义色（颜色一律取设计令牌，不写死色值） */
const STATUS_META: Record<NodeStatus, { text: string; color: string }> = {
  pending: { text: '等待', color: 'var(--pf-text-faint)' },
  waiting: { text: '等待', color: 'var(--pf-text-faint)' },
  skipped: { text: '跳过', color: 'var(--pf-text-faint)' },
  running: { text: '运行中', color: 'var(--pf-warn)' },
  done: { text: '完成', color: 'var(--pf-ok)' },
  failed: { text: '失败', color: 'var(--pf-err)' },
}

const statusMeta = (status: NodeStatus) =>
  STATUS_META[status] ?? { text: status, color: 'var(--pf-text-faint)' }

/**
 * 灰度明度梯度：第 i 个节点取 hsl(0 0% X%)，X 从 32% 线性升到 62%。
 * 用内联样式而非 Tailwind 类，是因为档位数量随节点数变化（契约允许内联计算）。
 */
function barFill(index: number, total: number): string {
  const ratio = total <= 1 ? 1 : index / (total - 1)
  const lightness = Math.round(32 + ratio * 30)
  return `hsl(0 0% ${lightness}%)`
}

/** 运行中节点追加一层半透明语义色，配合呼吸动画表达"正在跑" */
function barStyle(node: TrajectoryNode, index: number, total: number) {
  const fill = barFill(index, total)
  const meta = statusMeta(node.status)
  switch (node.status) {
    case 'done':
      return { background: fill, boxShadow: `inset 0 -2px 0 0 ${meta.color}` }
    case 'running':
      return { background: `linear-gradient(180deg, ${fill}, ${meta.color})` }
    case 'failed':
      return { background: fill, boxShadow: `inset 0 0 0 1.5px ${meta.color}` }
    default:
      // 跳过/等待：只保留极淡的描边，视觉上"未参与"
      return { background: 'var(--pf-surface)', boxShadow: `inset 0 0 0 1px ${meta.color}` }
  }
}

/** 读条悬浮提示：节点名 + 耗时 + token */
function barTooltip(node: TrajectoryNode): string {
  const meta = statusMeta(node.status)
  const tokens = node.tokens?.total_tokens
  const parts = [
    `${node.label || node.node}（${node.node}）`,
    meta.text,
    formatDuration(node.elapsed_ms),
  ]
  if (tokens) parts.push(`${formatNumber(tokens)} tokens`)
  if (node.error) parts.push(`错误：${node.error}`)
  return parts.join(' · ')
}

export default function TrajectoryView({ collapsible = true }: TrajectoryViewProps) {
  const trajectory = useRunStore((s) => s.trajectory)
  const selectedNodeId = useRunStore((s) => s.selectedNodeId)
  const setSelectedNode = useRunStore((s) => s.setSelectedNode)

  const [collapsed, setCollapsed] = useState(false)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  // 轨迹整体收起时保留选中，仅在节点消失时清理
  const [detailOpen, setDetailOpen] = useState(true)

  // 选中的节点可能因会话切换/回滚而不复存在，做一次温和的清理
  useEffect(() => {
    if (selectedNodeId && !trajectory.some((n) => n.node_id === selectedNodeId)) {
      setSelectedNode(null)
    }
  }, [trajectory, selectedNodeId, setSelectedNode])

  // 悬停优先、其次选中：hover 只影响详情展示，不写回 store
  const activeId = hoveredId ?? selectedNodeId
  const activeNode = useMemo(
    () => trajectory.find((n) => n.node_id === activeId) ?? null,
    [trajectory, activeId],
  )

  const totalMs = trajectory.reduce((sum, n) => sum + (n.elapsed_ms || 0), 0)
  const doneCount = trajectory.filter((n) => n.status === 'done').length

  if (trajectory.length === 0) return null

  const toggleSelect = (nodeId: string) => {
    setSelectedNode(selectedNodeId === nodeId ? null : nodeId)
    setDetailOpen(true)
  }

  return (
    <section className="border-b border-pf-border bg-pf-elevated" aria-label="执行轨迹">
      {/* 头部：节点进度概览 + 折叠开关 */}
      <div className="flex items-center gap-2 px-3 pt-2">
        <button
          type="button"
          className={cn('pf-btn-ghost gap-1 px-1 py-0.5', !collapsible && 'pointer-events-none')}
          onClick={() => collapsible && setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          title={collapsed ? '展开执行轨迹' : '折叠执行轨迹'}
        >
          {collapsed ? <IconChevronRight size={12} /> : <IconChevronDown size={12} />}
          <span className="pf-label">执行轨迹</span>
        </button>
        <span className="font-mono text-2xs tabular-nums text-pf-faint">
          {doneCount}/{trajectory.length} 节点 · 累计 {formatDuration(totalMs)}
        </span>
        <div className="flex-1" />
        {activeNode && (
          <span className="hidden font-mono text-2xs text-pf-faint sm:inline">
            当前查看：{activeNode.label || activeNode.node}
          </span>
        )}
      </div>

      {!collapsed && (
        <>
          {/* 读条带：flex 容器按 flex-grow 分配宽度，等价于按耗时占比分配 */}
          <div className="flex items-stretch gap-1 px-3 pb-2 pt-1.5">
            {trajectory.map((node, i) => {
              // 宽度权重：以耗时为比例，最小 1ms 兜底，保证零耗时节点仍占位可见
              const weight = Math.max(node.elapsed_ms || 0, 1)
              const isActive = node.node_id === activeId
              return (
                <button
                  key={node.node_id}
                  type="button"
                  className={cn(
                    'pf-bar group min-w-[6px] text-left',
                    node.status === 'running' && 'animate-pf-pulse',
                    isActive && 'ring-1 ring-pf-border-strong',
                  )}
                  style={{ ...barStyle(node, i, trajectory.length), flexGrow: weight, flexBasis: 0 }}
                  title={barTooltip(node)}
                  aria-pressed={node.node_id === selectedNodeId}
                  onMouseEnter={() => setHoveredId(node.node_id)}
                  onMouseLeave={() => setHoveredId(null)}
                  onFocus={() => setHoveredId(node.node_id)}
                  onBlur={() => setHoveredId(null)}
                  onClick={() => toggleSelect(node.node_id)}
                >
                  {/* 标签浮在读条上：只在读条足够宽时显示，避免文字互相压叠 */}
                  <span
                    className="pointer-events-none absolute inset-x-0 top-0 truncate px-1 text-2xs
                               leading-none text-pf-accent-text mix-blend-difference"
                  >
                    {node.label}
                  </span>
                </button>
              )
            })}
          </div>

          {/* 详情面板：悬停同步预览，点击锁定 */}
          {activeNode ? (
            <NodeDetail
              node={activeNode}
              seq={trajectory.indexOf(activeNode) + 1}
              total={trajectory.length}
              open={detailOpen}
              onToggle={() => setDetailOpen((v) => !v)}
              pinned={selectedNodeId === activeNode.node_id}
            />
          ) : (
            <p className="px-3 pb-2 text-2xs text-pf-faint">
              悬停读条预览节点详情，点击可锁定查看思考与工具调用。
            </p>
          )}
        </>
      )}
    </section>
  )
}

interface NodeDetailProps {
  node: TrajectoryNode
  seq: number
  total: number
  open: boolean
  onToggle: () => void
  /** 是否已锁定（点击选中）；未锁定时为悬停预览 */
  pinned: boolean
}

function NodeDetail({ node, seq, total, open, onToggle, pinned }: NodeDetailProps) {
  const meta = statusMeta(node.status)

  return (
    <div className="mx-3 mb-2 rounded-md border border-pf-border bg-pf-elevated">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-pf-surface-hover"
        onClick={onToggle}
        aria-expanded={open}
      >
        {open ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
        <span className="font-mono text-2xs tabular-nums text-pf-faint">
          {String(seq).padStart(2, '0')}/{total}
        </span>
        <span className="truncate text-sm text-pf-text">{node.label || node.node}</span>
        <span
          className="shrink-0 rounded border px-1 font-mono text-2xs"
          style={{ color: meta.color, borderColor: meta.color }}
        >
          {meta.text}
        </span>
        {!pinned && <span className="pf-label shrink-0">预览</span>}
        <div className="flex-1" />
        <span className="shrink-0 font-mono text-2xs tabular-nums text-pf-muted">
          {formatDuration(node.elapsed_ms)}
        </span>
      </button>

      {open && (
        <div className="space-y-2 border-t border-pf-border px-2 py-2">
          {/* 概览：节点标识 / 耗时 / Token */}
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-4">
            <Field label="节点" value={node.node} mono />
            <Field label="耗时" value={formatDuration(node.elapsed_ms)} mono />
            <Field label="Token" value={tokenText(node)} mono />
            <Field
              label="工具调用"
              value={node.tool_calls.length ? `${node.tool_calls.length} 次` : '—'}
              mono
            />
          </div>

          {node.error && (
            <div className="flex items-start gap-1.5 rounded border border-pf-err px-2 py-1 text-xs text-pf-text">
              <span className="mt-0.5 shrink-0 text-pf-err">
                <IconAlert size={12} />
              </span>
              <span className="min-w-0 flex-1 break-words">{node.error}</span>
            </div>
          )}

          {/* 思考内容 */}
          <div>
            <p className="pf-label mb-1">思考内容</p>
            {node.thoughts.length === 0 ? (
              <p className="text-xs text-pf-faint">该节点未记录思考内容。</p>
            ) : (
              <ol className="space-y-1">
                {node.thoughts.map((t) => (
                  <li key={`${t.step}-${t.thought.slice(0, 12)}`} className="flex gap-2">
                    <span className="mt-0.5 shrink-0 font-mono text-2xs text-pf-faint">
                      {String(t.step).padStart(2, '0')}
                    </span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-xs text-pf-muted">
                      {t.thought}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          {/* 工具调用明细 */}
          <div>
            <p className="pf-label mb-1">工具调用</p>
            {node.tool_calls.length === 0 ? (
              <p className="text-xs text-pf-faint">该节点未调用工具。</p>
            ) : (
              <ul className="space-y-1.5">
                {node.tool_calls.map((call) => (
                  <ToolCallItem key={call.call_id} call={call} />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** Token 汇总：优先总计，缺省时回落到 输入+输出 */
function tokenText(node: TrajectoryNode): string {
  const total = node.tokens?.total_tokens
    ?? ((node.tokens?.prompt_tokens ?? 0) + (node.tokens?.completion_tokens ?? 0))
  if (!total) return '—'
  const cached = node.tokens?.cached_tokens ?? 0
  return cached > 0 ? `${formatNumber(total)}（缓存 ${formatNumber(cached)}）` : formatNumber(total)
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="pf-label">{label}</p>
      <p className={cn('truncate text-xs text-pf-text', mono && 'font-mono')} title={value}>
        {value}
      </p>
    </div>
  )
}

/** 单次工具调用：工具名 / 状态 / 缓存 / 耗时 / 入参 / 输出摘要或错误 */
function ToolCallItem({ call }: { call: ToolCallRecord }) {
  const status = call.status ?? (call.error ? 'error' : call.ok ? 'ok' : 'running')
  const color = status === 'ok'
    ? 'var(--pf-ok)'
    : status === 'error' ? 'var(--pf-err)' : 'var(--pf-warn)'
  const statusText = status === 'ok' ? '成功' : status === 'error' ? '失败' : '执行中'
  const args = argsPreview(call.args, 80)

  return (
    <li className="rounded border border-pf-border bg-pf-elevated px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <span className="shrink-0 text-pf-faint">
          <IconTool size={12} />
        </span>
        <span className="truncate font-mono text-xs text-pf-text">{call.tool}</span>
        <span className="shrink-0 font-mono text-2xs" style={{ color }}>{statusText}</span>
        {call.cached && (
          <span className="shrink-0 rounded border border-pf-border px-1 font-mono text-2xs text-pf-info">
            缓存命中
          </span>
        )}
        <div className="flex-1" />
        <span className="shrink-0 font-mono text-2xs tabular-nums text-pf-faint">
          {formatDuration(call.elapsed_ms)}
        </span>
      </div>

      {args && (
        <p className="mt-1 break-all font-mono text-2xs text-pf-muted" title={args}>
          {args}
        </p>
      )}

      {call.error ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-2xs text-pf-err">{call.error}</p>
      ) : call.brief ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-2xs text-pf-muted">{call.brief}</p>
      ) : (
        <p className="mt-1 flex items-center gap-1 text-2xs text-pf-faint">
          {status === 'ok' && <IconCheck size={11} />}
          无输出摘要
        </p>
      )}
    </li>
  )
}
