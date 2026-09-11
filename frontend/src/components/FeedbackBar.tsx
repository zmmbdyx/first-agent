/**
 * 反馈条：Agent 消息下方的 1–5 星评分 + 可选备注（契约 HARDENING §4/§7）。
 *
 * 设计决策：
 * - 星星是手写 SVG（与 components/icons.tsx 同一套 stroke 视觉语言），
 *   不引入任何图标库，避免多一个第三方依赖；
 * - 点击星即提交（不设「提交」按钮），反馈成本越低越容易被填写；
 *   备注走「添加备注」展开的单行输入，回车提交，空备注合法；
 * - 初始分数从 `useSessionFeedback` 里回填：先按 message_ts 精确匹配，
 *   退而按 run_id 匹配（历史消息可能没有 ts），这样刷新页面后星星仍是亮的。
 */
import { useEffect, useState } from 'react'
import { ApiError, useSessionFeedback, useSubmitFeedback } from '@/api/client'
import { cn } from '@/lib/utils'
import type { FeedbackItem } from '@/types'

export interface FeedbackBarProps {
  sessionId: string
  /** 该条消息所属运行，历史消息可能缺失 */
  runId?: string
  /** 消息时间戳，作为「同一条消息」的主匹配键 */
  messageTs?: number
}

/** 与 icons.tsx 同源的手写星形（实心/描边由 fill 决定，不引入图标库） */
function IconStar({ size = 14, filled = false, className }: {
  size?: number
  filled?: boolean
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8z" />
    </svg>
  )
}

/** 在已有反馈里找这条消息的评分：message_ts 优先，run_id 兜底 */
function findExisting(
  items: FeedbackItem[] | undefined,
  runId?: string,
  messageTs?: number,
): FeedbackItem | undefined {
  if (!items?.length) return undefined
  // 0/空值一律视为「未填写」：否则缺 ts 的旧反馈会和缺 ts 的消息互相误配，
  // 把别人的评分显示到这条回答下面。
  if (typeof messageTs === 'number' && messageTs > 0) {
    const byTs = items.find((it) => Number(it.message_ts) === messageTs)
    if (byTs) return byTs
  }
  if (runId) {
    const byRun = items.find((it) => it.run_id && it.run_id === runId)
    if (byRun) return byRun
  }
  return undefined
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '未知错误'
}

export default function FeedbackBar({ sessionId, runId, messageTs }: FeedbackBarProps) {
  const { data } = useSessionFeedback(sessionId)
  const submit = useSubmitFeedback(sessionId)

  const [rating, setRating] = useState(0)
  const [comment, setComment] = useState('')
  const [showComment, setShowComment] = useState(false)
  /** 提交成功后的轻量提示，几秒后自行消失（不用 alert，不打断阅读） */
  const [toast, setToast] = useState('')

  const existing = findExisting(data?.items, runId, messageTs)
  const existingRating = existing?.rating ?? 0

  // 后端反馈到位后回填初始分数；本地已点过则不被覆盖（避免闪烁回退）
  useEffect(() => {
    if (existingRating > 0) setRating((prev) => (prev > 0 ? prev : existingRating))
  }, [existingRating])

  useEffect(() => {
    if (!toast) return undefined
    const timer = window.setTimeout(() => setToast(''), 2600)
    return () => window.clearTimeout(timer)
  }, [toast])

  const send = (value: number, note: string) => {
    setRating(value)
    setToast('')
    submit.mutate(
      {
        rating: value,
        ...(note.trim() ? { comment: note.trim() } : {}),
        ...(runId ? { run_id: runId } : {}),
        ...(typeof messageTs === 'number' ? { message_ts: messageTs } : {}),
      },
      {
        onSuccess: () => {
          setToast(note.trim() ? '备注已记录' : '已记录')
          setComment('')
          setShowComment(false)
        },
      },
    )
  }

  const display = rating || existingRating
  const pending = submit.isPending

  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-2xs text-pf-faint">这条回答有用吗</span>
        <div className="flex items-center gap-0.5" role="group" aria-label="评分 1 到 5 星">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              disabled={pending}
              onClick={() => send(n, comment)}
              className={cn(
                'rounded p-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40',
                n <= display ? 'text-pf-text' : 'text-pf-faint hover:text-pf-muted',
              )}
              title={`${n} 分`}
              aria-label={`评 ${n} 分`}
              aria-pressed={display === n}
            >
              <IconStar size={14} filled={n <= display} />
            </button>
          ))}
        </div>

        {display > 0 && <span className="text-2xs text-pf-muted">{display} 分</span>}

        <button
          type="button"
          disabled={pending}
          onClick={() => setShowComment((v) => !v)}
          className="pf-btn-ghost text-2xs text-pf-faint hover:text-pf-text disabled:cursor-not-allowed disabled:opacity-40"
          aria-expanded={showComment}
        >
          {showComment ? '收起备注' : '添加备注'}
        </button>

        {pending && <span className="text-2xs text-pf-faint">提交中…</span>}
        {!pending && toast && <span className="text-2xs text-pf-ok">{toast}</span>}
      </div>

      {showComment && (
        <input
          className="pf-input"
          value={comment}
          disabled={pending}
          autoFocus
          placeholder="补充说明（可选），回车提交"
          aria-label="反馈备注"
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return
            e.preventDefault()
            // 未点星时回车按 5 分处理没有依据，这里默认取当前分数，没有分数则给中间值 3
            send(display > 0 ? display : 3, comment)
          }}
        />
      )}

      {/* 失败必须给出可读原因：401/403 的文案由后端 detail 透出，提示用户去设置令牌 */}
      {submit.isError && <p className="text-2xs text-pf-err">提交失败：{errorText(submit.error)}</p>}
    </div>
  )
}
