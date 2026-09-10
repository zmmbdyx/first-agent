/**
 * 流式实时指标条：单行紧凑展示 TPS / LLM 耗时 / 上下文占用 / 缓存命中率 / 输入输出 Token。
 *
 * 布局决策：
 * - 用一条横向 flex + 细分隔线承载全部指标，高度控制在 22px 左右，
 *   贴在输入区上方，不占用消息阅读区的高度；
 * - 只在 running / awaiting_input 时挂载（调用方控制），历史会话不显示"僵尸指标"；
 * - 缺失值统一显示 `—`：0 与"没有数据"语义不同，直接显示 0 会误导;
 * - 数字一律 font-mono 且用 tabular 等宽，逐帧刷新时不会左右跳动。
 */
import { formatDuration, formatNumber } from '@/lib/utils'
import type { Metrics } from '@/types'

export interface StreamMetricsProps {
  metrics: Metrics | null
}

export default function StreamMetrics({ metrics }: StreamMetricsProps) {
  // 命中率可能真的是 0（冷启动全部未命中），但"没有缓存数据"必须显示 —，
  // 因此以 cached_tokens / cache_hit_rate 是否有值为准，而不是看命中率是否大于 0
  const hitRate = metrics && (metrics.cached_tokens > 0 || metrics.cache_hit_rate > 0)
    ? `${(metrics.cache_hit_rate * 100).toFixed(0)}%`
    : '—'
  const tokens = metrics && (metrics.prompt_tokens > 0 || metrics.completion_tokens > 0)
    ? `${formatNumber(metrics.prompt_tokens)} / ${formatNumber(metrics.completion_tokens)}`
    : '—'

  return (
    <div className="flex items-center gap-2.5 overflow-x-auto border-t border-pf-border bg-pf-elevated px-3 py-1">
      <Metric label="TPS" value={metrics?.tps ? metrics.tps.toFixed(1) : '—'} />

      <Divider />
      <Metric label="LLM 耗时" value={metrics?.llm_ms ? formatDuration(metrics.llm_ms) : '—'} />

      <Divider />
      <ContextUsage metrics={metrics} />

      <Divider />
      <Metric label="缓存命中率" value={hitRate} />

      <Divider />
      <Metric label="输入/输出 Token" value={tokens} />

      <Divider />
      <Metric
        label="调用"
        value={metrics ? `LLM ${metrics.llm_calls} · 工具 ${metrics.tool_calls}` : '—'}
      />
    </div>
  )
}

/** 单指标：标签在上、数值在下，整体高度极小，保持单行观感 */
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex shrink-0 items-baseline gap-1.5">
      <span className="pf-label whitespace-nowrap">{label}</span>
      <span className="whitespace-nowrap font-mono text-xs tabular-nums text-pf-text">{value}</span>
    </span>
  )
}

function Divider() {
  return <span className="h-3 w-px shrink-0 bg-pf-border" aria-hidden="true" />
}

/** 上下文占用：数值 + 4px 细进度条，超过 85% 用告警色提示即将溢出 */
function ContextUsage({ metrics }: { metrics: Metrics | null }) {
  const used = metrics?.context_tokens ?? 0
  const window = metrics?.context_window ?? 0
  const ratio = window > 0 ? (metrics?.context_ratio || used / window) : 0
  const percent = Math.min(100, Math.max(0, ratio * 100))
  const nearLimit = percent >= 85

  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <span className="pf-label whitespace-nowrap">上下文占用</span>
      <span className="whitespace-nowrap font-mono text-xs tabular-nums text-pf-text">
        {window > 0 ? `${formatNumber(used)} / ${formatNumber(window)}` : '—'}
      </span>
      {window > 0 && (
        <span className="flex items-center gap-1">
          <span className="h-1 w-14 overflow-hidden rounded-sm bg-pf-surface" aria-hidden="true">
            <span
              className="block h-full rounded-sm transition-all"
              style={{
                width: `${percent}%`,
                background: nearLimit ? 'var(--pf-warn)' : 'var(--pf-border-strong)',
              }}
            />
          </span>
          <span
            className={`font-mono text-2xs tabular-nums ${nearLimit ? 'text-pf-warn' : 'text-pf-faint'}`}
          >
            {percent.toFixed(0)}%
          </span>
        </span>
      )}
    </span>
  )
}
