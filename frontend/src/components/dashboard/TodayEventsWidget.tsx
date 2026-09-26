import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  CalendarDays,
  ShieldAlert,
  AlertTriangle,
  Info,
  ArrowUpRight,
  CheckCircle2,
  Loader2,
} from 'lucide-react'
import { api, type MarketEvent } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

export function TodayEventsWidget() {
  const eventsQuery = useQuery({
    queryKey: QK.taiwanEvents('today', undefined, undefined),
    queryFn: () => api.taiwanEvents({ scope: 'today', limit: 10 }),
    staleTime: 5 * 60 * 1000,
  })

  const events: MarketEvent[] = eventsQuery.data?.events ?? []
  const hasEvents = events.length > 0

  return (
    <section className="mb-2.5 rounded-card border border-border bg-surface/85 p-3 shadow-sm backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-border/60 pb-2 mb-2">
        <div className="flex items-center gap-1.5">
          <span className="h-3.5 w-1 rounded-full bg-gradient-to-b from-accent to-accent/40" />
          <CalendarDays className="h-4 w-4 text-accent" />
          <h2 className="text-xs font-bold text-foreground">今日重要市場事件</h2>
          <span className="rounded bg-accent/10 px-1.5 py-0.2 text-[10px] font-semibold text-accent">
            處置 · 注意 · 除權息
          </span>
        </div>
        <Link
          to="/events"
          className="inline-flex items-center gap-1 text-[11px] font-medium text-accent hover:underline"
        >
          <span>完整事件中心</span>
          <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>

      {eventsQuery.isLoading ? (
        <div className="flex items-center justify-center py-4 text-xs text-muted gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
          正在整理今日市場重大事件…
        </div>
      ) : eventsQuery.isError ? (
        <div className="py-3 text-center text-xs text-muted">
          今日事件暫時無法讀取。
        </div>
      ) : !hasEvents ? (
        <div className="flex items-center gap-2 py-3 px-2 text-xs text-muted rounded bg-elevated/30">
          <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
          <span>今日市場平穩，無新增處置管制限額股票。</span>
        </div>
      ) : (
        <div className="space-y-1.5">
          {events.slice(0, 5).map(ev => {
            const isRisk = ev.severity === 'risk'
            const isAttention = ev.severity === 'attention'
            const Icon = isRisk ? ShieldAlert : isAttention ? AlertTriangle : Info
            const badgeCls = isRisk
              ? 'bg-rose-500/10 text-rose-500 border-rose-500/30'
              : isAttention
              ? 'bg-amber-500/10 text-amber-500 border-amber-500/30'
              : 'bg-sky-500/10 text-sky-500 border-sky-500/30'

            return (
              <div
                key={ev.id}
                className="flex items-center justify-between gap-2 rounded-md border border-border/50 bg-elevated/25 p-2 text-xs hover:border-accent/40 hover:bg-elevated/50 transition-colors"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={cn(
                      'inline-flex items-center gap-1 rounded border px-1.5 py-0.2 text-[10px] font-semibold shrink-0',
                      badgeCls,
                    )}
                  >
                    <Icon className="h-2.5 w-2.5" />
                    {ev.event_type_label}
                  </span>
                  <Link
                    to={`/stocks/${encodeURIComponent(ev.symbol)}`}
                    className="font-bold text-foreground hover:text-accent shrink-0"
                  >
                    {ev.name} ({ev.code})
                  </Link>
                  <span className="text-secondary truncate text-[11px]">
                    {ev.summary}
                  </span>
                </div>

                <Link
                  to={`/stocks/${encodeURIComponent(ev.symbol)}`}
                  className="text-muted hover:text-accent shrink-0 p-1"
                  title="查看個股"
                >
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </Link>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
