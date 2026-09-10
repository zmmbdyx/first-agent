/**
 * 代码块：语法高亮 + 语言标签 + 复制按钮。
 *
 * 布局决策：
 * - 头部单独一行承载「语言标签 / 复制按钮」，代码体在下方独立滚动，
 *   这样横向滚动只发生在代码内部，不会把消息气泡撑宽（消息区是纵向流）。
 * - 主题跟随 useAppStore.theme（深色用 oneDark、浅色用 oneLight），
 *   与 index.css 的 .dark 类保持同源，避免出现"深色正文 + 浅色代码块"。
 */
import { useEffect, useRef, useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { IconCheck, IconCopy } from '@/components/icons'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/useAppStore'

export interface CodeBlockProps {
  code: string
  /** 语言标识（来自 Markdown 的 language-xxx）；缺省按纯文本渲染 */
  language?: string
  /** 行内代码（`code`）：不渲染头部，避免破坏段落行高 */
  inline?: boolean
  className?: string
}

export default function CodeBlock({ code, language, inline, className }: CodeBlockProps) {
  const theme = useAppStore((s) => s.theme)
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  // 组件卸载时清掉复位定时器，避免在已卸载组件上 setState
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  if (inline) {
    // 行内代码沿用 .pf-md code 的样式，这里只补一个等宽字体兜底
    return (
      <code className={cn('font-mono', className)}>
        {code}
      </code>
    )
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1200)
    } catch {
      /* 非安全上下文下剪贴板不可用：静默失败，不打断阅读 */
    }
  }

  return (
    <div className="my-0 overflow-hidden rounded-md border border-pf-border">
      <div className="flex items-center justify-between gap-2 border-b border-pf-border bg-pf-surface px-2 py-1">
        <span className="font-mono text-2xs uppercase text-pf-faint">{language || 'text'}</span>
        <button
          type="button"
          className="pf-btn-ghost gap-1 px-1 py-0.5 text-2xs"
          onClick={copy}
          title="复制代码"
          aria-label="复制代码"
        >
          {copied ? <IconCheck size={12} /> : <IconCopy size={12} />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || 'text'}
        style={theme === 'dark' ? oneDark : oneLight}
        customStyle={{
          margin: 0,
          background: 'transparent',
          padding: '10px 12px',
          fontSize: 12,
          lineHeight: 1.6,
        }}
        codeTagProps={{ style: { fontFamily: 'inherit' } }}
        wrapLongLines={false}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}
