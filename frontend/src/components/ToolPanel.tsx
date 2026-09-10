/**
 * 工具面板：本次运行的工具调用清单（输入 / 输出 / 耗时 / 状态）。
 *
 * 决策：
 * - 数据源只有 `useRunStore.toolCalls`（SSE 与 WS 两条通道已在 store 内幂等合并），
 *   面板本身不持有副本，保证与轨迹视图看到的是同一份事实。
 * - 展开状态是「渲染层关注点」而非业务状态，放在本组件的 Set 里，
 *   不污染全局 store；切换 Tab 时组件常驻挂载，展开状态得以保留。
 */
import { useMemo, useState } from 'react'
import { IconChevronDown, IconChevronRight } from '@/components/icons'
import { argsPreview, cn, formatDuration } from '@/lib/utils'
import { useRunStore } from '@/store/useRunStore'
import type { ToolCallRecord } from '@/types'

/** 状态点：运行中脉冲 / 成功 / 失败 */
function StatusDot({ record }: { record: ToolCallRecord }) {
  const state = record.status ?? (record.ok ? 'ok' : 'error')
  const tone = state === 'running' ? 'bg-pf-warn animate-pf-pulse'
    : state === 'error' ? 'bg-pf-err' : 'bg-pf-ok'
  const label = state === 'running' ? '运行中' : state === 'error' ? '失败' : '成功'
  return <span className={cn('inline-block h-1.5 w-1.5 shrink-0 rounded-full', tone)} title={label} />
}

export default function ToolPanel() {
  const toolCalls = useRunStore((s) => s.toolCalls)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  const failed = useMemo(() => toolCalls.filter((c) => c.status === 'error' || !c.ok).length, [toolCalls])

  const toggle = (callId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(callId)) next.delete(callId)
      else next.add(callId)
      return next
    })

  return (
    <div className="flex h-full flex-col">
      {/* 顶部统计 */}
      <div className="flex h-8 shrink-0 items-center gap-1.5 border-b border-pf-border px-2">
        <span className="pf-label">工具调用</span>
        <span className="pf-tag font-mono">{toolCalls.length}</span>
        {failed > 0 && (
          <span className="pf-tag font-mono text-pf-err" title="失败次数">失败 {failed}</span>
        )}
        <span className="flex-1" />
        {toolCalls.length > 0 && (
          <span className="font-mono text-2xs text-pf-faint">
            {toolCalls.filter((c) => c.status === 'running').length} 运行中
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {toolCalls.length === 0 ? (
          <p className="px-1 py-3 text-xs text-pf-faint">本次运行还没有工具调用</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {toolCalls.map((record) => {
              const open = expanded.has(record.call_id)
              const state = record.status ?? (record.ok ? 'ok' : 'error')
              const hasDetail = Object.keys(record.args ?? {}).length > 0 || Boolean(record.brief || record.error)
              return (
                <li key={record.call_id} className="pf-card overflow-hidden">
                  <button
                    type="button"
                    onClick={() => toggle(record.call_id)}
                    className="flex w-full items-center gap-1.5 px-2 py-1 text-left transition-colors hover:bg-pf-surface-hover"
                    title={open ? '折叠详情' : '展开详情'}
                  >
                    {open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />}
                    <StatusDot record={record} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-pf-text">{record.tool}</span>
                    {record.cached && <span className="pf-tag">缓存</span>}
                    <span className="shrink-0 font-mono text-2xs text-pf-faint">
                      {formatDuration(record.elapsed_ms)}
                    </span>
                  </button>

                  {/* 入参预览：单行截断，完整内容在展开区 */}
                  {!open && Object.keys(record.args ?? {}).length > 0 && (
                    <p className="truncate px-2 pb-1 font-mono text-2xs text-pf-faint">
                      {argsPreview(record.args)}
                    </p>
                  )}
                  {!open && state !== 'running' && (record.brief || record.error) && (
                    <p
                      className={cn(
                        'truncate px-2 pb-1 text-2xs',
                        state === 'error' ? 'text-pf-err' : 'text-pf-muted',
                      )}
                      title={record.error || record.brief}
                    >
                      {record.error || record.brief}
                    </p>
                  )}

                  {open && (
                    <div className="flex flex-col gap-1.5 border-t border-pf-border px-2 py-1.5">
                      <div>
                        <p className="pf-label mb-0.5">入参</p>
                        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-pf-surface px-1.5 py-1 font-mono text-2xs text-pf-muted">
                          {Object.keys(record.args ?? {}).length > 0
                            ? JSON.stringify(record.args, null, 2)
                            : '（无入参）'}
                        </pre>
                      </div>
                      <div>
                        <p className="pf-label mb-0.5">{record.error ? '错误' : '输出摘要'}</p>
                        <pre
                          className={cn(
                            'max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-pf-surface px-1.5 py-1 font-mono text-2xs',
                            record.error ? 'text-pf-err' : 'text-pf-muted',
                          )}
                        >
                          {record.error || record.brief || '（无输出）'}
                        </pre>
                      </div>
                      <p className="font-mono text-2xs text-pf-faint">
                        call_id {record.call_id}
                        {record.task_id ? ` · task ${record.task_id}` : ''}
                      </p>
                      {!hasDetail && <p className="text-2xs text-pf-faint">该次调用没有更多细节</p>}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
