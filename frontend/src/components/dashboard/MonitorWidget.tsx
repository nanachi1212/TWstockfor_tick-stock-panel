import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Trash2 } from 'lucide-react'
import { api, type AlertEvent } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { fmtPct, fmtPrice, priceColorClass, taipeiDateTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import { cnSignal } from '@/lib/signals'
import { strategyEventMeta, strategyName } from '@/lib/strategyMonitorEvents'
import { boardTag } from '@/components/stock-table/primitives'

const _SOURCE_BADGE: Record<string, string> = {
  strategy: 'bg-amber-400/10 text-amber-400',
  signal: 'bg-accent/10 text-accent',
  price: 'bg-emerald-400/10 text-emerald-400',
  market: 'bg-purple-500/10 text-purple-400',
  sector: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
  quant: 'bg-violet-500/10 text-violet-500',
}

const _SOURCE_LABEL: Record<string, string> = {
  strategy: '策略', signal: '訊號', price: '價格', market: '異動', sector: '類股', quant: 'Quant',
}

export function MonitorWidget({ onStockClick }: {
  onStockClick: (symbol: string, name?: string, alert?: AlertEvent) => void
}) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const alerts = useQuery({
    // 只抓 10 筆, key 要和監控頁/個股頁 (同 key 抓 500 筆) 分開; invalidate 用前綴 ['alerts',''] 仍會命中
    queryKey: [...QK.alerts(undefined), 'dashboard-10'],
    queryFn: () => api.alertsList({ days: 7, limit: 10 }),
    refetchInterval: 10000,
  })
  const events: AlertEvent[] = alerts.data?.alerts ?? []
  const refreshAlerts = () => { void qc.invalidateQueries({ queryKey: QK.alerts(undefined) }) }
  const markRead = useMutation({ mutationFn: api.alertsMarkRead, onSuccess: refreshAlerts })
  const deleteAlert = useMutation({ mutationFn: api.alertDeleteById, onSuccess: refreshAlerts })
  const markAllRead = useMutation({ mutationFn: api.alertsMarkAllRead, onSuccess: refreshAlerts })

  if (events.length === 0) {
    return (
      <div className="py-6 text-center text-xs text-muted">
        {alerts.isLoading ? '載入中…' : alerts.isError ? '提醒暫時無法讀取' : '目前沒有提醒'}
      </div>
    )
  }

  return (
    <>
      <div className="mb-1 flex justify-end">
        <button type="button" onClick={() => markAllRead.mutate()} disabled={markAllRead.isPending} className="interactive-target -my-2 inline-flex items-center rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-accent disabled:opacity-40">全部標記已讀</button>
      </div>
      <div className="mt-1 space-y-1.5">
        {events.map((ev, i) => {
          const isStrategy = ev.source === 'strategy'
          const isSector = ev.source === 'sector'
          const isTaiwanSymbol = !!ev.symbol && /\.(TWSE|TPEX)$/i.test(ev.symbol)
          const sname = isStrategy ? strategyName(ev.message ?? '') : ''
          const eventMeta = strategyEventMeta(ev.type)
          return (
            <div
              key={`${ev.ts}-${i}`}
              className={`relative overflow-hidden rounded-md border ${ev.is_read ? 'border-border/40 bg-surface/60' : 'border-accent/40 bg-accent/5'} pl-2.5 pr-2 py-1.5 hover:border-border hover:bg-surface transition-colors`}
            >
              {/* 第一行: 代碼 + 名稱 + 價格 + 漲跌幅 (點擊代碼/名稱彈日K) */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => {
                    if (isSector) navigate('/monitor')
                    else if (ev.symbol && isTaiwanSymbol) navigate(`/stocks/${encodeURIComponent(ev.symbol)}`)
                    else if (ev.symbol) onStockClick(ev.symbol, ev.name ?? undefined, ev)
                  }}
                  title={isSector ? '在監控中心查看類股警示' : ev.symbol && isTaiwanSymbol ? `查看 ${ev.symbol} 個股詳情` : ev.symbol ? `預覽 ${ev.symbol} 個股資料` : undefined}
                  className={`inline-flex items-center gap-1 min-w-0 shrink-0 rounded hover:bg-elevated/60 transition-colors -mx-0.5 px-0.5 ${isSector || ev.symbol ? 'cursor-pointer' : 'cursor-default'}`}
                >
                  <span className="font-mono text-xs font-medium text-foreground/80 hover:text-accent">{ev.symbol?.replace(/\.(SH|SZ|BJ)$/, '')}</span>
                  {ev.symbol && (() => {
                    const board = boardTag(ev.symbol)
                    return board && (
                      <span className={`inline-flex items-center justify-center h-3 w-3 rounded text-xs font-bold leading-none border ${board.color}`}>
                        {board.label}
                      </span>
                    )
                  })()}
                  {ev.name && <span className="text-xs text-secondary truncate max-w-[5rem] hover:text-foreground">{ev.name}</span>}
                </button>
                <span className="flex-1" />
                {ev.price != null && (
                  <span className="text-xs font-mono text-foreground/60 shrink-0">{fmtPrice(ev.price)}</span>
                )}
                {ev.change_pct != null && (
                  <span className={cn('text-xs font-mono font-medium shrink-0 w-12 text-right', priceColorClass(ev.change_pct))}>
                    {fmtPct(ev.change_pct)}
                  </span>
                )}
              </div>
              <div className="mt-1 flex justify-end gap-1">
                <span className={`mr-auto rounded px-1 py-0.5 text-xs ${ev.is_read ? 'bg-elevated text-muted' : 'bg-accent/10 text-accent'}`}>{ev.is_read ? '已讀' : '未讀'}</span>
                {!ev.is_read && ev.alert_id && <button type="button" aria-label={`標記 ${ev.symbol ?? '提醒'} 已讀`} onClick={() => markRead.mutate(ev.alert_id!)} className="interactive-target inline-flex items-center gap-1 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-accent"><Check className="h-3 w-3" />標記已讀</button>}
                {ev.alert_id && <button type="button" aria-label={`刪除 ${ev.symbol ?? '提醒'}`} onClick={() => deleteAlert.mutate(ev.alert_id!)} className="interactive-target inline-flex items-center justify-center rounded-btn text-muted hover:bg-danger/10 hover:text-danger"><Trash2 className="h-3.5 w-3.5" /></button>}
              </div>
              {/* 第二行: 策略類型走新格式, 其他走舊格式 */}
              {isStrategy ? (
                <>
                  {ev.symbol ? (
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                      <span className={cn('shrink-0 text-xs font-medium', eventMeta.className)}>
                        {eventMeta.action}
                      </span>
                      {sname
                        ? <span className="truncate text-xs font-medium text-amber-400">「{sname}」</span>
                        : ev.message && <span className="truncate text-xs text-muted">{ev.message}</span>}
                      <span className="flex-1" />
                      <span className="text-xs text-muted/50 shrink-0 font-mono">
                        {taipeiDateTime(ev.ts)?.slice(5) ?? ''}
                      </span>
                    </div>
                  ) : (
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                      <span className="truncate text-xs text-muted">{ev.message}</span>
                      <span className="flex-1" />
                      <span className="text-xs text-muted/50 shrink-0 font-mono">
                        {taipeiDateTime(ev.ts)?.slice(5) ?? ''}
                      </span>
                    </div>
                  )}
                  {ev.signals && ev.signals.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {ev.signals.map(signal => (
                        <span key={signal} className="rounded bg-accent/8 px-1 py-px text-xs text-accent/80">{cnSignal(signal)}</span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <span className={cn('shrink-0 rounded px-1 py-px text-xs font-medium', _SOURCE_BADGE[ev.source] ?? 'bg-elevated text-muted')}>
                      {_SOURCE_LABEL[ev.source] ?? ev.source}
                    </span>
                    {ev.message && (
                      <span className="text-xs text-muted truncate flex-1">{ev.message}</span>
                    )}
                    <span className="text-xs text-muted/50 shrink-0 font-mono">
                      {taipeiDateTime(ev.ts)?.slice(5) ?? ''}
                    </span>
                  </div>
                  {ev.signals && ev.signals.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {ev.signals.map((s, j) => (
                        <span key={j} className="rounded bg-accent/8 px-1 py-px text-xs text-accent/80">{cnSignal(s)}</span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </>
  )
}
