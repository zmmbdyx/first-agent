/**
 * Token 面板：本次运行的消耗、缓存命中率与上下文占用。
 *
 * 决策：
 * - 数据源有二：`metrics`（SSE 实时推送，每次 LLM 调用刷新）与 `stats`（run_done/final_answer 收尾快照）。
 *   运行中优先展示 metrics，结束后 metrics 可能缺失，此时回落到 stats，
 *   两者字段子集一致（prompt/completion/cached/tps/context…），因此统一映射成同一份视图模型。
 * - 缓存命中率用「竖向条形图」表达：**纯灰度**区分（命中深、未命中浅），
 *   用 `color-mix` 对令牌色做明度分层，深浅两套主题都能拿到有对比的两个灰阶，
 *   纯 CSS（flex + 百分比高度）实现，不引入图表库、也不写死任何颜色值。
 */
import { useMemo } from 'react'
import { cn, formatDuration, formatNumber, formatPercent } from '@/lib/utils'
import { useRunStore } from '@/store/useRunStore'

interface View {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  cachedTokens: number
  cacheHitRate: number
  tps: number
  llmMs: number
  contextTokens: number
  contextWindow: number
  contextRatio: number
  llmCalls: number
  toolCalls: number
}

/** 单行指标：标签左、值右（数字一律 mono） */
function Row({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-pf-border py-1 last:border-b-0">
      <span className="text-xs text-pf-muted">{label}</span>
      <span className={cn('font-mono text-xs', tone ?? 'text-pf-text')}>{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="pf-card px-2 py-1.5">
      <p className="pf-label mb-1">{title}</p>
      {children}
    </section>
  )
}

export default function TokenPanel() {
  const metrics = useRunStore((s) => s.metrics)
  const stats = useRunStore((s) => s.stats)

  const view = useMemo<View | null>(() => {
    if (metrics) {
      return {
        promptTokens: metrics.prompt_tokens,
        completionTokens: metrics.completion_tokens,
        totalTokens: metrics.total_tokens,
        cachedTokens: metrics.cached_tokens,
        cacheHitRate: metrics.cache_hit_rate,
        tps: metrics.tps,
        llmMs: metrics.llm_ms,
        contextTokens: metrics.context_tokens,
        contextWindow: metrics.context_window,
        contextRatio: metrics.context_ratio,
        llmCalls: metrics.llm_calls,
        // 实时计数与最终统计取较大值：运行中的 metric 事件是增量快照，
        // 收尾的 stats 才是权威值，二者不一致时不能让面板显示成 0
        toolCalls: Math.max(metrics.tool_calls ?? 0, stats?.tool_calls ?? 0),
      }
    }
    if (stats) {
      return {
        promptTokens: stats.prompt_tokens,
        completionTokens: stats.completion_tokens,
        totalTokens: stats.total_tokens,
        cachedTokens: stats.cached_tokens,
        cacheHitRate: stats.cache_hit_rate,
        tps: stats.tps,
        llmMs: 0,
        contextTokens: stats.context_tokens,
        contextWindow: stats.context_window,
        contextRatio: stats.context_ratio,
        llmCalls: stats.llm_calls,
        toolCalls: stats.tool_calls,
      }
    }
    return null
  }, [metrics, stats])

  // ---- 无数据占位 ----
  if (!view) {
    return (
      <div className="flex flex-col gap-2 p-2">
        <Section title="Token 消耗">
          <Row label="总 Token" value="—" />
          <Row label="输入 / 输出" value="— / —" />
          <Row label="缓存命中" value="—" />
        </Section>
        <p className="px-1 text-xs text-pf-faint">发送任务后这里会显示实时消耗</p>
      </div>
    )
  }

  const contextRatio = view.contextWindow > 0
    ? Math.min(1, Math.max(view.contextRatio || view.contextTokens / view.contextWindow, 0))
    : 0
  const cached = Math.min(view.cachedTokens, view.totalTokens)
  const uncached = Math.max(view.totalTokens - cached, 0)
  const cachedPct = view.totalTokens > 0 ? (cached / view.totalTokens) * 100 : 0
  // 灰度分层：命中段比未命中段更深，两段都从 text 令牌派生，故深浅主题各自成立
  const hitFill = 'color-mix(in srgb, var(--pf-text) 46%, var(--pf-surface))'
  const missFill = 'color-mix(in srgb, var(--pf-text) 12%, var(--pf-surface))'

  return (
    <div className="flex flex-col gap-2 p-2">
      <Section title="Token 消耗">
        <Row label="总 Token" value={formatNumber(view.totalTokens)} />
        <Row label="输入 Token" value={formatNumber(view.promptTokens)} />
        <Row label="输出 Token" value={formatNumber(view.completionTokens)} />
      </Section>

      <Section title="缓存">
        <div className="flex items-baseline justify-between gap-2 pb-1">
          <span className="text-xs text-pf-muted">缓存命中 Token</span>
          <span className="font-mono text-xs text-pf-text">
            {formatNumber(view.cachedTokens)}
            <span className="ml-1.5 text-pf-muted">{formatPercent(view.cacheHitRate)}</span>
          </span>
        </div>
        {/* 竖向条形图：缓存命中 / 未命中比例（纯 CSS 灰度分层） */}
        <div className="flex items-stretch gap-2">
          <div
            className="flex h-24 w-6 flex-col justify-end overflow-hidden rounded border border-pf-border"
            style={{ background: missFill }}
            title={`命中 ${formatNumber(cached)} / 未命中 ${formatNumber(uncached)}`}
          >
            <div className="w-full" style={{ height: `${cachedPct}%`, background: hitFill }} />
          </div>
          <div className="flex flex-1 flex-col justify-center gap-1 text-2xs text-pf-muted">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: hitFill }} />
              缓存命中 <span className="font-mono text-pf-text">{formatNumber(cached)}</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-sm border border-pf-border" style={{ background: missFill }} />
              未命中 <span className="font-mono text-pf-text">{formatNumber(uncached)}</span>
            </span>
            <span className="mt-0.5 font-mono text-pf-faint">
              命中率 {formatPercent(view.cacheHitRate)}
            </span>
          </div>
        </div>
      </Section>

      <Section title="上下文占用">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-xs text-pf-muted">已用 / 窗口</span>
          <span className="font-mono text-xs text-pf-text">
            {formatNumber(view.contextTokens)}
            <span className="text-pf-faint"> / {view.contextWindow > 0 ? formatNumber(view.contextWindow) : '—'}</span>
          </span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-sm bg-pf-surface">
          <div
            className={cn('h-full rounded-sm', contextRatio > 0.85 ? 'bg-pf-err' : 'bg-pf-accent')}
            style={{ width: `${contextRatio * 100}%` }}
          />
        </div>
        <p className="mt-1 font-mono text-2xs text-pf-faint">
          占比 {view.contextWindow > 0 ? formatPercent(contextRatio) : '—'}
        </p>
      </Section>

      <Section title="性能">
        <Row label="TPS" value={view.tps > 0 ? view.tps.toFixed(1) : '—'} />
        <Row label="LLM 累计耗时" value={view.llmMs > 0 ? formatDuration(view.llmMs) : '—'} />
        <Row label="LLM 调用" value={formatNumber(view.llmCalls)} />
        <Row label="工具调用" value={formatNumber(view.toolCalls)} />
      </Section>

      <Section title="运行">
        <Row label="本节点数" value={stats ? formatNumber(stats.nodes) : '—'} />
        <Row
          label="运行耗时"
          value={stats && stats.elapsed_s > 0 ? `${stats.elapsed_s.toFixed(1)}s` : '—'}
        />
        {stats && stats.tool_retries > 0 && (
          <Row label="工具重试" value={formatNumber(stats.tool_retries)} tone="text-pf-warn" />
        )}
        {stats && stats.failed_tasks.length > 0 && (
          <Row label="失败任务" value={formatNumber(stats.failed_tasks.length)} tone="text-pf-err" />
        )}
        {stats?.llm_degraded && <Row label="模型降级" value="已降级" tone="text-pf-warn" />}
      </Section>
    </div>
  )
}
