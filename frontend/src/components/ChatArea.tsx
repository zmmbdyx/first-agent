/**
 * 中央对话/任务区：空状态 / 消息流 / 执行轨迹 / 实时指标 / 输入区。
 *
 * 布局决策：
 * - 整列 `flex-col + min-h-0`：消息区是可伸缩的滚动容器（flex-1 + overflow-y-auto），
 *   输入区 `shrink-0` 固定在最下沿，因此无论轨迹块多高，输入框都不会被挤出视口；
 * - 执行轨迹放在消息区上方的独立横条里（不随消息滚动），
 *   因为轨迹回答的是"现在跑到哪一步了"，跟历史消息一起滚走就失去意义；
 * - 实时指标贴在输入区上方：它和输入框同属"当前运行态"，视觉上成组。
 */
import { useCallback, useRef, useState } from 'react'
import EmptyState from '@/components/EmptyState'
import InputBox from '@/components/InputBox'
import MessageList, { hasMessages } from '@/components/MessageList'
import StreamMetrics from '@/components/StreamMetrics'
import TrajectoryView from '@/components/TrajectoryView'
import { IconX } from '@/components/icons'
import { useRunStore } from '@/store/useRunStore'

export interface ChatAreaProps {
  running: boolean
  onSend: (task: string, opts?: { resume?: boolean }) => void
  onStop: () => void
  onRollback: () => void
}

/** 空状态示例任务：均为求职域内、且现有工具链可独立完成的任务 */
const EXAMPLES = [
  '分析这份 JD 并匹配我的简历，指出差距与补救建议',
  '生成 10 道高级后端岗位面试题，并附参考回答要点',
  '做一次薪资谈判准备：给出目标区间、话术与让步底线',
  '读取工作区里的简历与项目笔记，整理一份面试自我介绍',
]

export default function ChatArea({ running, onSend, onStop, onRollback }: ChatAreaProps) {
  const messages = useRunStore((s) => s.messages)
  const status = useRunStore((s) => s.status)
  const metrics = useRunStore((s) => s.metrics)
  const error = useRunStore((s) => s.error)

  // 输入框把"填入文本"的能力注册上来，供空状态示例任务调用
  const insertRef = useRef<((text: string) => void) | null>(null)
  const registerInsert = useCallback((insert: (text: string) => void) => {
    insertRef.current = insert
  }, [])

  // 关闭告警条只在本地生效：错误内容变化（新的报错）后自动重新展示
  const [dismissed, setDismissed] = useState('')
  const visibleError = error && error !== dismissed ? error : ''

  const showMetrics = running || status === 'awaiting_input'
  const hasContent = hasMessages(messages)

  return (
    <div className="flex h-full min-h-0 flex-col">
      {hasContent ? (
        <>
          <TrajectoryView />
          <MessageList messages={messages} streaming={running} error={error} />
        </>
      ) : (
        // 空状态占满剩余高度；顶部保留告警条位置（例如后端未启动时的报错）
        <div className="relative min-h-0 flex-1 overflow-y-auto">
          {visibleError && (
            <div
              className="m-3 flex items-start gap-2 rounded border border-pf-err bg-pf-surface
                         px-2 py-1 text-sm"
              role="alert"
            >
              <span className="min-w-0 flex-1 break-words">{visibleError}</span>
              <button
                type="button"
                className="pf-btn-ghost shrink-0"
                onClick={() => setDismissed(error)}
                title="关闭提示"
                aria-label="关闭提示"
              >
                <IconX size={13} />
              </button>
            </div>
          )}
          <EmptyState
            examples={EXAMPLES}
            onPickExample={(text) => insertRef.current?.(text)}
          />
        </div>
      )}

      {showMetrics && <StreamMetrics metrics={metrics} />}

      <InputBox
        running={running}
        onSend={onSend}
        onStop={onStop}
        onRollback={onRollback}
        registerInsert={registerInsert}
      />
    </div>
  )
}
