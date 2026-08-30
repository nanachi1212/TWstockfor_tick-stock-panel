import { motion } from 'framer-motion'
import {
  AlertTriangle,
  Bell,
  Clock,
  Flame,
} from 'lucide-react'
import type { AlertEvent, TaiwanAlertEvent } from '@/lib/api'

import { cn } from '@/lib/cn'

interface TaiwanAlertsListProps {
  alerts: (AlertEvent | TaiwanAlertEvent)[]
  onSelectSymbol?: (symbol: string) => void
}

const SEVERITY_CONFIG: Record<string, { bar: string; icon: any; iconCls: string; badgeCls: string; label: string }> = {
  info: {
    bar: 'bg-accent/40',
    icon: Bell,
    iconCls: 'text-accent',
    badgeCls: 'bg-accent/10 text-accent border-accent/20',
    label: '一般 (INFO)',
  },
  warning: {
    bar: 'bg-warning',
    icon: AlertTriangle,
    iconCls: 'text-warning',
    badgeCls: 'bg-warning/10 text-warning border-warning/20',
    label: '提醒 (WARN)',
  },
  warn: {
    bar: 'bg-warning',
    icon: AlertTriangle,
    iconCls: 'text-warning',
    badgeCls: 'bg-warning/10 text-warning border-warning/20',
    label: '提醒 (WARN)',
  },
  critical: {
    bar: 'bg-danger',
    icon: Flame,
    iconCls: 'text-danger',
    badgeCls: 'bg-danger/10 text-danger border-danger/20',
    label: '緊急 (CRITICAL)',
  },
}

function formatAlertTime(ts: number | string | undefined): string {
  if (!ts) return '--'
  try {
    const d = typeof ts === 'number' ? new Date(ts) : new Date(ts)
    if (isNaN(d.getTime())) return String(ts)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    return `${hh}:${mm}:${ss}`
  } catch {
    return String(ts)
  }
}

export function TaiwanAlertsList({ alerts, onSelectSymbol }: TaiwanAlertsListProps) {
  if (!alerts || alerts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-center text-muted">
        <div className="rounded-full bg-elevated/40 p-3 mb-2">
          <Bell className="h-6 w-6 text-muted/60" />
        </div>
        <p className="text-xs font-medium">尚無即時觸發告警紀錄</p>
        <p className="text-[10px] text-muted mt-0.5">盤中監控規則符合條件時，將第一時間在此處推播</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert, idx) => {
        const id = (alert as any).alert_id || `${alert.rule_id || 'rule'}_${alert.ts || idx}`
        const sev = (alert.severity || 'warning').toLowerCase()
        const sevCfg = SEVERITY_CONFIG[sev] || SEVERITY_CONFIG.warning
        const SevIcon = sevCfg.icon
        const isTaiwan = alert.symbol?.includes('.TWSE') || alert.symbol?.includes('.TPEX') || alert.source === 'twse:mis'

        return (
          <motion.div
            key={id}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            className="group relative flex overflow-hidden rounded-xl border border-border/70 bg-surface/80 shadow-sm hover:border-accent/40 transition-all p-3 text-xs"
          >
            {/* 左側彩色指示條 */}
            <div className={cn('absolute left-0 top-0 bottom-0 w-1', sevCfg.bar)} />

            <div className="ml-1.5 flex flex-1 flex-col gap-1">
              {/* 頂部: 時間、標的、嚴重等級、來源 */}
              <div className="flex flex-wrap items-center justify-between gap-1.5">
                <div className="flex items-center gap-2">
                  <span className="flex items-center gap-1 font-mono text-[11px] text-muted">
                    <Clock className="h-3 w-3" />
                    {formatAlertTime((alert as any).triggered_at || alert.ts)}
                  </span>
                  {alert.symbol && (
                    <button
                      onClick={() => onSelectSymbol?.(alert.symbol!)}
                      className="font-mono font-bold text-accent hover:underline flex items-center gap-0.5 cursor-pointer"
                    >
                      {alert.symbol}
                      {alert.name && <span className="text-foreground ml-1 font-sans font-medium">({alert.name})</span>}
                    </button>
                  )}
                  {isTaiwan && (
                    <span className="rounded bg-sky-500/10 px-1 py-0.2 text-[9px] font-medium text-sky-600 dark:text-sky-400 border border-sky-500/20">
                      台股即時
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1.5">
                  <span className={cn('inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium', sevCfg.badgeCls)}>
                    <SevIcon className="h-3 w-3" />
                    {sevCfg.label}
                  </span>
                  {alert.source && (
                    <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">
                      {alert.source}
                    </span>
                  )}
                </div>
              </div>

              {/* 中間: 訊息內文 */}
              <div className="text-foreground/90 font-medium leading-relaxed my-0.5">
                {alert.message}
              </div>

              {/* 底部數值標籤 (若有) */}
              {((alert as any).trigger_value != null || alert.price != null || alert.change_pct != null) && (
                <div className="flex flex-wrap items-center gap-2 pt-1 text-[11px] text-muted border-t border-border/40 font-mono">
                  {(alert as any).trigger_value != null && (
                    <span>觸發值: <strong className="text-foreground font-semibold">{(alert as any).trigger_value}</strong></span>
                  )}
                  {(alert as any).threshold != null && (
                    <span>閾值門檻: <strong className="text-foreground font-semibold">{(alert as any).threshold}</strong></span>
                  )}
                  {alert.price != null && (
                    <span>價格: <strong className="text-foreground font-semibold">{alert.price.toFixed(2)}</strong></span>
                  )}
                  {alert.change_pct != null && (
                    <span className={alert.change_pct > 0 ? 'text-rose-500 font-semibold' : 'text-emerald-500 font-semibold'}>
                      {alert.change_pct > 0 ? `+${alert.change_pct.toFixed(2)}%` : `${alert.change_pct.toFixed(2)}%`}
                    </span>
                  )}
                </div>
              )}
            </div>
          </motion.div>
        )
      })}
    </div>
  )
}
