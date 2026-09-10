/**
 * 空状态：Logo + 项目名 + 欢迎语 + 示例任务。
 *
 * 布局决策：
 * - 整体垂直居中（父容器给定高度），但内容块自身限宽 `max-w-xl`，
 *   避免思路被超宽行拉到难以扫读；
 * - 示例任务按 1 列（窄屏）/ 2 列（宽屏）排布，卡片左对齐文字，
 *   点击只"填入输入框"不直接发送，让用户在发送前仍可补充细节。
 */
import Logo from '@/components/Logo'

export interface EmptyStateProps {
  examples: string[]
  onPickExample: (text: string) => void
}

export default function EmptyState({ examples, onPickExample }: EmptyStateProps) {
  return (
    <div className="flex h-full items-center justify-center px-4 py-8">
      <div className="w-full max-w-xl animate-pf-fade-in text-center">
        <div className="flex justify-center text-pf-text">
          <Logo size={40} />
        </div>

        <h1 className="mt-3 text-md font-medium tracking-tight text-pf-text">拓径</h1>
        <p className="mt-1 font-mono text-2xs uppercase tracking-wide text-pf-faint">
          PATHFORGE · 智能任务执行台
        </p>

        <h2 className="mt-6 text-base font-medium text-pf-text">开始你的智能任务</h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-pf-muted">
          描述你的目标，我会自主规划步骤、调用工具并交付结果。
        </p>

        {examples.length > 0 && (
          <div className="mt-5 grid gap-1.5 text-left sm:grid-cols-2">
            {examples.map((text) => (
              <button
                key={text}
                type="button"
                onClick={() => onPickExample(text)}
                title="填入输入框"
                className="pf-card px-2.5 py-2 text-sm text-pf-muted transition-colors
                           hover:bg-pf-surface-hover hover:text-pf-text"
              >
                <span className="leading-snug">{text}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
