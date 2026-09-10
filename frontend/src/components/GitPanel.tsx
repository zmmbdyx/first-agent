/**
 * Git 面板：工作区变更列表 + 勾选暂存 / 取消暂存。
 *
 * 决策：
 * - 变更按 `staged` 分成两组展示（后端每条 change 自带 staged 标志），
 *   勾选集合用 `path::staged` 组合键，因为同一文件可能同时存在「已暂存」与「未暂存」两条记录，
 *   只用 path 会让两条记录互相串选。
 * - 暂存 / 取消暂存各自走 useGitMutation，钩子内部已在成功后失效 git 与文件树缓存，
 *   因此这里不做本地乐观更新，操作完成即自动刷新（避免与服务端实际状态不一致）。
 * - 非仓库（is_repo=false）只给提示，不渲染操作条。
 */
import { useMemo, useState } from 'react'
import { useGitMutation, useGitStatus } from '@/api/client'
import { IconBranch, IconRefresh } from '@/components/icons'
import { cn } from '@/lib/utils'
import type { GitChange } from '@/types'

export interface GitPanelProps {
  workspace: string
  /** 所属 Tab 是否可见：不可见时不请求 */
  active: boolean
  className?: string
}

/** 与 status/index.ts 类型一致的组合键，确保两条同路径记录互不干扰 */
const keyOf = (c: GitChange) => `${c.path}::${c.staged ? 's' : 'w'}`

/** 状态缩写着色：新增/修改为中性，删除与未跟踪用语义色区分 */
function statusTone(status: string): string {
  if (status.includes('D')) return 'text-pf-err'
  if (status.includes('?')) return 'text-pf-faint'
  if (status.includes('A')) return 'text-pf-ok'
  return 'text-pf-warn'
}

export default function GitPanel({ workspace, active, className }: GitPanelProps) {
  const { data, isLoading, isError, error, refetch, isFetching } = useGitStatus(workspace, active)
  const { stage, unstage } = useGitMutation()
  const [selected, setSelected] = useState<Set<string>>(() => new Set())

  const { staged, unstaged } = useMemo(() => {
    const list = data?.changes ?? []
    return {
      staged: list.filter((c) => c.staged),
      unstaged: list.filter((c) => !c.staged),
    }
  }, [data])

  const toggle = (change: GitChange) =>
    setSelected((prev) => {
      const next = new Set(prev)
      const k = keyOf(change)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })

  const toggleAll = (list: GitChange[]) =>
    setSelected((prev) => {
      const next = new Set(prev)
      const keys = list.map(keyOf)
      const allOn = keys.every((k) => next.has(k))
      keys.forEach((k) => (allOn ? next.delete(k) : next.add(k)))
      return next
    })

  const runStage = (withStaged: boolean) => {
    const source = withStaged ? staged : unstaged
    const paths = source.filter((c) => selected.has(keyOf(c))).map((c) => c.path)
    if (paths.length === 0) return
    const done = () => setSelected(new Set())
    if (withStaged) unstage.mutate({ workspace, paths }, { onSuccess: done })
    else stage.mutate({ workspace, paths }, { onSuccess: done })
  }

  const busy = stage.isPending || unstage.isPending
  const stagedPicked = staged.filter((c) => selected.has(keyOf(c))).length
  const unstagedPicked = unstaged.filter((c) => selected.has(keyOf(c))).length

  const renderGroup = (title: string, list: GitChange[]) => {
    if (list.length === 0) return null
    const allOn = list.every((c) => selected.has(keyOf(c)))
    return (
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-1.5 px-1 py-0.5">
          <input
            type="checkbox"
            checked={allOn}
            onChange={() => toggleAll(list)}
            className="h-3 w-3 accent-pf-accent"
            aria-label={`全选${title}`}
          />
          <span className="pf-label">{title}</span>
          <span className="pf-label font-mono">{list.length}</span>
        </div>
        {list.map((change) => {
          const k = keyOf(change)
          return (
            <label
              key={k}
              className={cn(
                'flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-0.5 transition-colors',
                selected.has(k) ? 'bg-pf-surface' : 'hover:bg-pf-surface-hover',
              )}
              title={change.path}
            >
              <input
                type="checkbox"
                checked={selected.has(k)}
                onChange={() => toggle(change)}
                className="h-3 w-3 shrink-0 accent-pf-accent"
              />
              <span className={cn('w-4 shrink-0 font-mono text-2xs', statusTone(change.status))}>
                {change.status || '?'}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-2xs text-pf-muted">{change.path}</span>
            </label>
          )
        })}
      </div>
    )
  }

  return (
    <div className={cn('flex h-full flex-col', className)}>
      {/* 顶部：分支 + 刷新 */}
      <div className="flex h-8 shrink-0 items-center gap-1.5 border-b border-pf-border px-2">
        <IconBranch size={13} className="shrink-0 text-pf-muted" />
        <span className="min-w-0 flex-1 truncate font-mono text-2xs text-pf-muted" title={data?.branch || workspace}>
          {data?.branch || '—'}
        </span>
        <span className="pf-tag font-mono">{(staged.length + unstaged.length)} 项变更</span>
        <button
          type="button"
          onClick={() => void refetch()}
          className="pf-btn-ghost p-0.5"
          title="刷新变更"
          aria-label="刷新变更"
        >
          <IconRefresh size={13} className={isFetching ? 'animate-spin' : undefined} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {isLoading && <p className="px-1 py-2 text-xs text-pf-faint">加载中…</p>}
        {isError && (
          <p className="px-1 py-2 text-xs text-pf-err">
            读取失败：{(error as Error)?.message || '未知错误'}
          </p>
        )}
        {data && !data.is_repo && (
          <p className="px-1 py-3 text-xs text-pf-faint">当前工作区不是 Git 仓库</p>
        )}
        {data?.is_repo && staged.length + unstaged.length === 0 && (
          <p className="px-1 py-3 text-xs text-pf-faint">工作区干净，没有待提交的变更</p>
        )}
        {data?.is_repo && (
          <div className="flex flex-col gap-2">
            {renderGroup('已暂存', staged)}
            {renderGroup('未暂存', unstaged)}
          </div>
        )}
      </div>

      {/* 底部操作条：仅仓库且有变更时出现 */}
      {data?.is_repo && staged.length + unstaged.length > 0 && (
        <div className="flex shrink-0 items-center gap-1.5 border-t border-pf-border px-2 py-1.5">
          <span className="flex-1 truncate font-mono text-2xs text-pf-faint">
            已选 {stagedPicked + unstagedPicked}
          </span>
          <button
            type="button"
            disabled={busy || stagedPicked === 0}
            onClick={() => runStage(true)}
            className="pf-btn"
            title={stagedPicked === 0 ? '先勾选已暂存的变更' : '取消暂存所选变更'}
          >
            取消暂存
          </button>
          <button
            type="button"
            disabled={busy || unstagedPicked === 0}
            onClick={() => runStage(false)}
            className="pf-btn-primary"
            title={unstagedPicked === 0 ? '先勾选未暂存的变更' : '暂存所选变更'}
          >
            暂存
          </button>
        </div>
      )}
    </div>
  )
}
