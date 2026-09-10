/**
 * 文件面板：工作区文件树 + 预览（Markdown / 代码 / 图片 / PDF）。
 *
 * 决策：
 * - 树形结构「扁平化」后渲染：展开集合用 `Set<string>` 记录，
 *   递归只发生在 flatten 阶段，渲染层是纯列表，展开/折叠不重建组件树。
 * - 文件内容走 react-query（key 含 workspace + path），同一文件来回点击命中缓存无需重取；
 *   隐藏 Tab 时不请求（借用 RightPanel 传入的 active）。
 * - 预览按后端判定的 `type` 分流：markdown → react-markdown，text → 语法高亮，
 *   image/pdf → 原始字节 URL，binary 直接给「不支持预览」提示。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import CodeBlock from '@/components/CodeBlock'
import { api, useFileTree } from '@/api/client'
import { IconChevronDown, IconChevronRight, IconFile, IconFolder, IconRefresh } from '@/components/icons'
import { cn, formatBytes, languageOf } from '@/lib/utils'
import type { FileNode } from '@/types'

export interface FileTreePanelProps {
  workspace: string
  /** 所属 Tab 是否可见：不可见时不发请求、不轮询 */
  active: boolean
  className?: string
}

interface FlatRow {
  node: FileNode
  depth: number
}

/** 深度优先展开可见节点（目录在前、文件在后的顺序由后端保证，这里保持原序） */
function flatten(nodes: FileNode[], openDirs: Set<string>, depth = 0, out: FlatRow[] = []): FlatRow[] {
  for (const node of nodes) {
    out.push({ node, depth })
    if (node.type === 'dir' && openDirs.has(node.path) && node.children?.length) {
      flatten(node.children, openDirs, depth + 1, out)
    }
  }
  return out
}

export default function FileTreePanel({ workspace, active, className }: FileTreePanelProps) {
  const [openDirs, setOpenDirs] = useState<Set<string>>(() => new Set())
  const [selected, setSelected] = useState<string | null>(null)

  const { data, isLoading, isError, error, refetch, isFetching } = useFileTree(workspace, '', active)

  const content = useQuery({
    queryKey: ['file-content', workspace, selected],
    queryFn: () => api.fileContent(workspace, selected as string),
    enabled: Boolean(workspace && selected) && active,
    retry: false,
  })

  const rows = useMemo(
    () => flatten(data?.nodes ?? [], openDirs),
    [data, openDirs],
  )

  const toggleDir = (path: string) =>
    setOpenDirs((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const file = content.data
  const breadcrumb = selected ? selected.split(/[\\/]/).filter(Boolean) : []

  return (
    <div className={cn('flex h-full flex-col', className)}>
      {/* 顶部：工作区根 + 刷新 */}
      <div className="flex h-8 shrink-0 items-center gap-1.5 border-b border-pf-border px-2">
        <span className="truncate font-mono text-2xs text-pf-faint" title={data?.root || workspace}>
          {data?.root || workspace}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => void refetch()}
          className="pf-btn-ghost p-0.5"
          title="刷新文件树"
          aria-label="刷新文件树"
        >
          <IconRefresh size={13} className={isFetching ? 'animate-spin' : undefined} />
        </button>
      </div>

      {/* 文件树：唯一滚动区，预览展开时收缩为上半区 */}
      <div className={cn('min-h-0 overflow-y-auto px-1 py-1', selected ? 'h-2/5 shrink-0' : 'flex-1')}>
        {isLoading && <p className="px-2 py-2 text-xs text-pf-faint">加载中…</p>}
        {isError && (
          <p className="px-2 py-2 text-xs text-pf-err">
            文件树加载失败：{(error as Error)?.message || '未知错误'}
          </p>
        )}
        {!isLoading && !isError && rows.length === 0 && (
          <p className="px-2 py-2 text-xs text-pf-faint">工作区为空</p>
        )}
        {rows.map(({ node, depth }) => {
          const isDir = node.type === 'dir'
          const open = openDirs.has(node.path)
          const isSelected = selected === node.path
          return (
            <button
              key={node.path}
              type="button"
              onClick={() => (isDir ? toggleDir(node.path) : setSelected(node.path))}
              style={{ paddingLeft: `${depth * 12 + 4}px` }}
              className={cn(
                'flex w-full items-center gap-1 rounded py-0.5 pr-1 text-left transition-colors',
                isSelected ? 'bg-pf-surface text-pf-text' : 'text-pf-muted hover:bg-pf-surface-hover hover:text-pf-text',
              )}
              title={node.path}
            >
              {isDir
                ? (open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />)
                : <span className="w-[11px] shrink-0" />}
              {isDir ? <IconFolder size={12} /> : <IconFile size={12} />}
              <span className="min-w-0 flex-1 truncate text-xs">{node.name}</span>
              {!isDir && node.size > 0 && (
                <span className="shrink-0 font-mono text-2xs text-pf-faint">{formatBytes(node.size)}</span>
              )}
            </button>
          )
        })}
      </div>

      {/* 预览区：仅有选中文件时占据下半区 */}
      {selected && (
        <div className="flex min-h-0 flex-1 flex-col border-t border-pf-border">
          <div className="flex h-7 shrink-0 items-center gap-1 border-b border-pf-border bg-pf-elevated px-2">
            {/* 路径面包屑 */}
            <span className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden font-mono text-2xs text-pf-faint">
              {breadcrumb.map((seg, i) => (
                <span key={`${seg}-${i}`} className="flex min-w-0 items-center gap-0.5">
                  {i > 0 && <span>/</span>}
                  <span className={cn('truncate', i === breadcrumb.length - 1 && 'text-pf-muted')}>{seg}</span>
                </span>
              ))}
            </span>
            {file && (
              <span className="pf-tag shrink-0">
                {formatBytes(file.size)}
                {file.truncated ? ' · 已截断' : ''}
              </span>
            )}
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="pf-btn-ghost shrink-0 p-0.5 text-2xs"
              title="关闭预览"
              aria-label="关闭预览"
            >
              关闭
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {content.isLoading && <p className="px-2 py-2 text-xs text-pf-faint">读取中…</p>}
            {content.isError && (
              <p className="px-2 py-2 text-xs text-pf-err">
                读取失败：{(content.error as Error)?.message || '未知错误'}
              </p>
            )}

            {file && file.type === 'markdown' && (
              <div className="pf-md px-2.5 py-2">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{file.content}</ReactMarkdown>
              </div>
            )}

            {file && file.type === 'text' && (
              // 复用 CodeBlock（按需注册语言的轻量高亮）：原先这里直接 import 全量
              // react-syntax-highlighter，会把所有语言再打进包一次，等于让首屏多背 500KB+
              <div className="px-2 py-2">
                <CodeBlock code={file.content} language={file.language || languageOf(file.path)} />
              </div>
            )}

            {file && file.type === 'image' && (
              <div className="flex items-start justify-center p-2">
                <img
                  src={api.fileRawUrl(workspace, file.path)}
                  alt={file.path}
                  className="max-w-full rounded border border-pf-border"
                />
              </div>
            )}

            {file && file.type === 'pdf' && (
              <iframe
                title={file.path}
                src={api.fileRawUrl(workspace, file.path)}
                className="h-full w-full border-0"
              />
            )}

            {file && file.type === 'binary' && (
              <p className="px-2 py-3 text-xs text-pf-faint">该文件类型不支持预览</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
