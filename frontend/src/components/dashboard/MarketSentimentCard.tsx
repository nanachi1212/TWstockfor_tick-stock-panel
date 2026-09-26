import { useQuery } from '@tanstack/react-query'
import {
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  MinusCircle,
  CheckCircle2,
  XCircle,
  Minus,
  Loader2,
  Sparkles,
} from 'lucide-react'
import { api, type TaiwanMarketSentimentResponse, type MarketEvidenceItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

const SENTIMENT_STYLE = {
  bullish: {
    label: '偏多',
    badgeCls: 'bg-bull/15 text-bull border-bull/30',
    icon: TrendingUp,
    borderCls: 'border-bull/20',
  },
  bearish: {
    label: '偏空',
    badgeCls: 'bg-bear/15 text-bear border-bear/30',
    icon: TrendingDown,
    borderCls: 'border-bear/20',
  },
  mixed: {
    label: '多空分歧',
    badgeCls: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
    icon: AlertTriangle,
    borderCls: 'border-amber-500/20',
  },
  neutral: {
    label: '中性震盪',
    badgeCls: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
    icon: MinusCircle,
    borderCls: 'border-border',
  },
}

export function MarketSentimentCard({ targetDate }: { targetDate?: string | null }) {
  const sentimentQuery = useQuery({
    queryKey: QK.taiwanMarketSentiment(targetDate ?? undefined),
    queryFn: () => api.taiwanMarketSentiment(targetDate ?? undefined),
    staleTime: 5 * 60 * 1000,
  })

  const data: TaiwanMarketSentimentResponse | undefined = sentimentQuery.data
  const currentStyle = data
    ? SENTIMENT_STYLE[data.sentiment] || SENTIMENT_STYLE.neutral
    : SENTIMENT_STYLE.neutral
  const SentimentIcon = currentStyle.icon

  return (
    <section className="mb-2.5 rounded-card border border-border bg-surface/85 p-3 shadow-sm backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border/60 pb-2 mb-2">
        <div className="flex items-center gap-1.5">
          <span className="h-3.5 w-1 rounded-full bg-gradient-to-b from-accent to-accent/40" />
          <Sparkles className="h-4 w-4 text-accent" />
          <h2 className="text-xs font-bold text-foreground">市場整體情緒與多空依據</h2>
          <span className="rounded bg-accent/10 px-1.5 py-0.2 text-[10px] font-semibold text-accent">
            現貨 + 衍生品綜合判定
          </span>
        </div>
        {data?.as_of_date && (
          <span className="font-mono text-[10px] text-muted">
            基準日: {data.as_of_date}
          </span>
        )}
      </div>

      {sentimentQuery.isLoading ? (
        <div className="flex items-center justify-center py-6 text-muted text-xs gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          正在綜合理性分析市場現貨與衍生品籌碼…
        </div>
      ) : sentimentQuery.isError ? (
        <div className="py-4 text-center text-xs text-muted">
          市場情緒資料暫時無法取得，不影響其他功能。
        </div>
      ) : !data ? null : (
        <div className="space-y-2.5">
          {/* Main Sentiment Badge & Summary */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded-lg bg-elevated/40 p-2.5 border border-border/50">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-bold shadow-sm',
                  currentStyle.badgeCls,
                )}
              >
                <SentimentIcon className="h-3.5 w-3.5" />
                {currentStyle.label}
              </span>
              <p className="text-xs text-foreground font-medium leading-relaxed">
                {data.summary}
              </p>
            </div>

            {/* Counts */}
            <div className="flex items-center gap-2 text-[11px] font-mono shrink-0">
              <span className="rounded bg-bull/10 px-1.5 py-0.5 text-bull font-semibold">
                多方 {data.bullish_count}
              </span>
              <span className="rounded bg-slate-500/10 px-1.5 py-0.5 text-muted">
                中性 {data.neutral_count}
              </span>
              <span className="rounded bg-bear/10 px-1.5 py-0.5 text-bear font-semibold">
                空方 {data.bearish_count}
              </span>
            </div>
          </div>

          {/* Evidence Checklist Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {data.evidence.map((item: MarketEvidenceItem) => {
              const isBull = item.direction === 'bullish'
              const isBear = item.direction === 'bearish'
              const DirIcon = isBull ? CheckCircle2 : isBear ? XCircle : Minus
              const dirColor = isBull
                ? 'text-bull'
                : isBear
                ? 'text-bear'
                : 'text-muted'

              return (
                <div
                  key={item.id}
                  className="flex items-start gap-2 rounded-md border border-border/50 bg-base/50 p-2 text-xs"
                >
                  <DirIcon className={cn('h-3.5 w-3.5 shrink-0 mt-0.5', dirColor)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-1 mb-0.5">
                      <span className="font-semibold text-foreground text-[11px]">
                        {item.label}
                      </span>
                      <span
                        className={cn(
                          'rounded px-1 py-0.2 text-[9px] font-medium font-mono',
                          isBull
                            ? 'bg-bull/10 text-bull'
                            : isBear
                            ? 'bg-bear/10 text-bear'
                            : 'bg-elevated text-muted',
                        )}
                      >
                        {isBull ? '偏多' : isBear ? '偏空' : '中性'}
                      </span>
                    </div>
                    <p className="text-[11px] text-secondary leading-snug">
                      {item.description}
                    </p>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Derivatives Degradation Notice */}
          {data.derivatives_status !== 'available' && (
            <div className="flex items-center gap-1.5 rounded bg-amber-500/5 px-2.5 py-1.5 text-[10px] text-amber-500 border border-amber-500/20">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              <span>
                {data.derivatives_status_message ||
                  '期權籌碼資料未配置或受限，已純依現貨指標客觀計算，未造假數據。'}
              </span>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
