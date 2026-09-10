/**
 * 消息流：用户右侧浅灰气泡、Agent 左侧无背景 Markdown、图表预览、流式光标、错误告警条。
 *
 * 布局决策：
 * - 用户气泡 `max-w-[78%]` 右对齐，Agent 回答占满整行宽度（报告里常有表格/代码，
 *   限宽会让 Markdown 提前折行），二者的差异靠对齐方式与底色表达，不靠边框；
 * - 自动吸底：只有当滚动位置贴近底部时才在消息更新后吸底，
 *   用户上滑查看历史时保持视口不动（否则流式输出会把用户"拽"回底部）。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import MarkdownView from '@/components/MarkdownView'
import { IconAlert, IconX } from '@/components/icons'
import type { ChatMessage } from '@/types'

export interface MessageListProps {
  messages: ChatMessage[]
  /** 流式执行中：最后一条 Agent 消息末尾显示闪烁光标 */
  streaming?: boolean
  /** 运行期错误（useRunStore.error），非空时展示可关闭告警条 */
  error?: string
  onDismissError?: () => void
}

/** 距底部多少像素以内算"贴底" */
const STICK_THRESHOLD = 48

export default function MessageList({
  messages, streaming = false, error = '', onDismissError,
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  // 用户手动上滑后给出"回到底部"的入口
  const [atBottom, setAtBottom] = useState(true)
  // 关闭告警条只影响本组件：记住"已关闭的那条错误"，错误内容变化后会自动重新出现
  const [dismissed, setDismissed] = useState('')

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    const near = distance <= STICK_THRESHOLD
    stickRef.current = near
    setAtBottom(near)
  }

  // 消息或告警条变化后吸底（useLayoutEffect：在浏览器绘制前完成，避免闪动）
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el || !stickRef.current) return
    el.scrollTop = el.scrollHeight
  }, [messages, streaming, error])

  // 切换会话/清空消息时重置为吸底
  useEffect(() => {
    if (messages.length === 0) {
      stickRef.current = true
      setAtBottom(true)
    }
  }, [messages.length])

  const lastAssistantIndex = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role !== 'user') return i
    }
    return -1
  })()

  const visibleError = error && error !== dismissed ? error : ''

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto px-3 py-3"
        role="log"
        aria-live="polite"
      >
        {visibleError && (
          <div
            className="mb-2 flex items-start gap-2 rounded border border-pf-err bg-pf-surface
                       px-2 py-1 text-sm text-pf-text"
            role="alert"
          >
            <span className="mt-0.5 shrink-0 text-pf-err">
              <IconAlert size={14} />
            </span>
            <span className="min-w-0 flex-1 break-words">{visibleError}</span>
            <button
              type="button"
              className="pf-btn-ghost shrink-0"
              onClick={() => {
                setDismissed(error)
                onDismissError?.()
              }}
              title="关闭提示"
              aria-label="关闭提示"
            >
              <IconX size={13} />
            </button>
          </div>
        )}

        <div className="space-y-3">
          {messages.map((msg, i) => (
            <MessageRow
              key={msg.id ?? `${msg.role}-${i}`}
              message={msg}
              // 只有最后一条非用户消息在流式期间显示光标
              showCursor={streaming && i === lastAssistantIndex}
            />
          ))}
        </div>
      </div>

      {!atBottom && messages.length > 0 && (
        <button
          type="button"
          className="pf-btn absolute bottom-2 left-1/2 -translate-x-1/2 bg-pf-elevated shadow"
          onClick={() => {
            const el = scrollRef.current
            if (!el) return
            stickRef.current = true
            setAtBottom(true)
            el.scrollTop = el.scrollHeight
          }}
        >
          回到底部
        </button>
      )}
    </div>
  )
}

function MessageRow({ message, showCursor }: { message: ChatMessage; showCursor: boolean }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] whitespace-pre-wrap break-words rounded bg-pf-surface px-2.5 py-1.5 text-base text-pf-text">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="min-w-0">
      <MarkdownView content={message.content} />
      {message.chart && (
        // chart 为后端返回的图片地址，直接预览；加载失败时不撑破布局
        <img
          src={message.chart}
          alt="结果图表"
          loading="lazy"
          className="mt-2 max-h-[420px] w-auto max-w-full rounded border border-pf-border"
        />
      )}
      {showCursor && (
        <span
          className="ml-0.5 inline-block h-3.5 w-[6px] translate-y-[1px] animate-pf-pulse rounded-sm bg-pf-text align-baseline"
          aria-hidden="true"
        />
      )}
    </div>
  )
}

/** 供外部（ChatArea）复用的空态判断，避免在两处写同样的长度判断 */
export const hasMessages = (messages: ChatMessage[]) =>
  messages.some((m) => m.content.trim().length > 0)
