/**
 * Markdown 渲染：react-markdown + remark-gfm，代码块交给 CodeBlock 做高亮。
 *
 * 排版全部交给全局 `.pf-md` 类（见 index.css），这样列表/表格/引用的间距只在一处维护；
 * 组件内部只做"节点 → 组件"的映射，不引入额外样式，保证深浅色主题跟随令牌。
 */
import type { ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import CodeBlock from '@/components/CodeBlock'

export interface MarkdownViewProps {
  content: string
  className?: string
}

/** react-markdown v9 的 code 渲染器会传入 `inline`（类型定义里没写），这里用结构化断言安全取出 */
type CodeRendererProps = {
  className?: string
  children?: ReactNode
  inline?: boolean
  node?: unknown
}

function CodeRenderer({ className, children, inline }: CodeRendererProps) {
  const text = String(children ?? '').replace(/\n$/, '')
  const language = /language-([\w+#-]+)/.exec(className ?? '')?.[1]
  // 无语言标记且内容不含换行 → 行内代码（Markdown 语法决定）
  const isInline = inline ?? (!language && !text.includes('\n'))
  return <CodeBlock code={text} language={language} inline={isInline} />
}

/** 只放行同源/相对地址与 http(s)，避免后端意外返回可执行协议 */
function SafeImage({ src, alt }: { src?: string | Blob; alt?: string }) {
  const url = typeof src === 'string' ? src : ''
  if (!/^(https?:|data:image\/|\/|\.\/)/i.test(url)) {
    return <span className="text-xs text-pf-faint">[图片不可用]</span>
  }
  return <img src={url} alt={alt ?? ''} loading="lazy" />
}

const components = {
  code: CodeRenderer,
  // 代码块的边框与滚动由 CodeBlock 自己管，pre 只做透传，避免双层边框
  pre: ({ children }: { children?: ReactNode }) => <>{children}</>,
  img: SafeImage,
} as unknown as Components

export default function MarkdownView({ content, className }: MarkdownViewProps) {
  return (
    <div className={`pf-md ${className ?? ''}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
