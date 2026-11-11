import { useCallback, useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Bell, TrendingUp, TrendingDown, X } from 'lucide-react'
import type { AlertEvent } from '@/lib/api'
import { fmtPct, fmtPrice, priceColorClass } from '@/lib/format'
import { cnSignal } from '@/lib/signals'
import { cn } from '@/lib/cn'
import { playNotificationSound } from '@/lib/notificationSound'
import { speakAlerts } from '@/lib/voiceBroadcast'
import { strategyEventMeta, strategyName } from '@/lib/strategyMonitorEvents'

/** 通知渠道分發 — 所有副作用渠道在此匯合, 新增渠道只改這裡 */
function dispatchSideEffects(alerts: AlertEvent[]) {
  playNotificationSound()        // 提示音 (Web Audio 合成)
  speakAlerts(alerts)            // 語音播報 (speechSynthesis, 各自獨立開關)
}

// ===== 全局狀態 (模組級, 仿 Toast.tsx 模式) =====
type Item = { id: number; alert: AlertEvent }
let _id = 0
let _queue: Item[] = []
const AUTO_DISMISS = 5000      // 5 秒自動消失
const _listeners: Set<(items: Item[]) => void> = new Set()

/** 從 localStorage 讀取設定 */
function getEnabled(): boolean {
  try {
    const v = localStorage.getItem('alert_toast_enabled')
    return v === null ? true : v === '1'   // 預設開啟
  } catch { return true }
}

function getMaxVisible(): number {
  try {
    const v = parseInt(localStorage.getItem('alert_toast_max') || '', 10)
    return v >= 1 && v <= 10 ? v : 3       // 預設 3, 範圍 1-10
  } catch { return 3 }
}

/** 通知外部設定變更後重新整理 (設定頁改了設定後呼叫) */
export function refreshAlertToastConfig() {
  _emit()
}

function _emit() { _listeners.forEach(fn => fn([..._queue])) }

/**
 * 批量推入監控告警通知 (一輪 SSE 多隻新命中時呼叫)。
 * - 每條都彈 Toast (受 maxVisible 上限, 超出丟最舊)
 * - 整批只播放一聲通知音, 避免短時連續響多聲刷屏
 */
export function pushAlertToasts(alerts: AlertEvent[]) {
  if (alerts.length === 0) return
  if (!getEnabled()) return                  // 開關關閉: 不彈
  const maxVisible = getMaxVisible()
  const newItems = alerts.map(alert => ({ id: ++_id, alert }))
  _queue = [..._queue, ...newItems]
  // 超出上限: 丟棄最舊的
  if (_queue.length > maxVisible) {
    _queue = _queue.slice(-maxVisible)
  }
  _emit()
  for (const item of newItems) {
    setTimeout(() => dismiss(item.id), AUTO_DISMISS)
  }
  dispatchSideEffects(alerts)                  // 副作用分發: 提示音 + 語音 (整批各一次)
}

/** 手動關閉 */
export function dismiss(id: number) {
  _queue = _queue.filter(t => t.id !== id)
  _emit()
}

// ===== 配色 =====
const SOURCE_BADGE: Record<string, { label: string; cls: string }> = {
  strategy:  { label: '策略',   cls: 'bg-amber-400/15 text-amber-400' },
  signal:    { label: '訊號',   cls: 'bg-accent/15 text-accent' },
  price:     { label: '價格',   cls: 'bg-emerald-400/15 text-emerald-400' },
  market:    { label: '異動',   cls: 'bg-purple-500/15 text-purple-400' },
  sector:    { label: '類股',   cls: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-300' },
  pool_entry: { label: '進入', cls: 'bg-danger/15 text-danger' },
  pool_exit:   { label: '移出', cls: 'bg-bear/15 text-bear' },
  buy_signal: { label: '買進', cls: 'bg-danger/15 text-danger' },
  sell_signal: { label: '賣出', cls: 'bg-bear/15 text-bear' },
  new_entry: { label: '進入', cls: 'bg-danger/15 text-danger' },
  dropped:   { label: '移出', cls: 'bg-bear/15 text-bear' },
}

// ===== 容器 — 掛在 Layout =====
export function AlertToastContainer() {
  const [items, setItems] = useState<Item[]>([])
  const navigate = useNavigate()

  const sub = useCallback(() => {
    _listeners.add(setItems)
    return () => { _listeners.delete(setItems) }
  }, [])
  useEffect(sub, [sub])

  // 點擊通知 → 跳轉監控中心 + 關閉當前通知
  const handleClick = (id: number) => {
    dismiss(id)
    navigate('/monitor')
  }

  if (!items.length) return null

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 w-[320px] pointer-events-none"
    >
      <AnimatePresence>
        {items.map(item => {
          const ev = item.alert
          const badgeKey = (ev.source === 'strategy' && ev.type) ? ev.type : ev.source
          const badge = SOURCE_BADGE[badgeKey] ?? { label: badgeKey, cls: 'bg-elevated text-muted' }
          const isStrategy = ev.source === 'strategy'
          const sname = isStrategy ? strategyName(ev.message ?? '') : ''
          const eventMeta = strategyEventMeta(ev.type)
          return (
            <motion.div
              key={item.id}
              layout
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 24 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              onClick={() => handleClick(item.id)}
              role="button"
              tabIndex={0}
              aria-label={`查看監控通知${ev.name ? ` ${ev.name}` : ''}${ev.symbol ? ` ${ev.symbol}` : ''}`}
              onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleClick(item.id)
                }
              }}
              className="pointer-events-auto relative overflow-hidden rounded-card border border-border bg-surface pl-3 pr-2 py-2.5 cursor-pointer hover:border-border-interactive transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
            >
              {/* 頂行: 分類標籤 + 代碼/名稱 + 漲跌幅 + 關閉 */}
              <div className="flex items-center gap-2">
                <span className={cn('shrink-0 rounded px-1 py-px text-xs font-medium', badge.cls)}>
                  {badge.label}
                </span>
                {ev.symbol && <span className="font-mono text-xs font-medium text-foreground shrink-0">{ev.symbol}</span>}
                {ev.name && <span className="text-xs text-secondary truncate flex-1">{ev.name}</span>}
                {ev.change_pct != null && (
                  <span className={cn('inline-flex items-center gap-0.5 text-xs font-mono font-medium shrink-0', priceColorClass(ev.change_pct))}>
                    {ev.change_pct > 0 ? <TrendingUp className="h-2.5 w-2.5" /> : ev.change_pct < 0 ? <TrendingDown className="h-2.5 w-2.5" /> : null}
                    {fmtPct(ev.change_pct)}
                  </span>
                )}
                <button aria-label="關閉通知" onClick={(e: MouseEvent<HTMLButtonElement>) => { e.stopPropagation(); dismiss(item.id) }} className="shrink-0 p-0.5 rounded text-muted/50 hover:text-foreground hover:bg-elevated transition-colors cursor-pointer">
                  <X className="h-3 w-3" />
                </button>
              </div>

              {/* 底行: 策略類型走新格式, 其他走舊格式 */}
              {isStrategy ? (
                <>
                  {ev.symbol ? (
                    <div className="mt-1 flex min-w-0 items-center gap-1.5 pl-0.5">
                      <Bell className={cn('h-3 w-3 shrink-0', eventMeta.className)} />
                      <span className={cn('shrink-0 text-xs font-medium', eventMeta.className)}>
                        {eventMeta.action}
                      </span>
                      {sname
                        ? <span className="truncate text-xs font-medium text-amber-400">「{sname}」</span>
                        : ev.message && <span className="truncate text-xs text-muted">{ev.message}</span>}
                      <span className="flex-1" />
                      {ev.price != null && <span className="text-xs font-mono text-muted shrink-0">{fmtPrice(ev.price)}</span>}
                    </div>
                  ) : (
                    <div className="mt-1 flex min-w-0 items-center gap-1.5 pl-0.5">
                      <Bell className={cn('h-3 w-3 shrink-0', eventMeta.className)} />
                      <span className="truncate text-xs text-foreground/70">{ev.message}</span>
                    </div>
                  )}
                  {ev.signals && ev.signals.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1 pl-0.5">
                      {ev.signals.map(signal => (
                        <span key={signal} className="rounded bg-accent/8 px-1 py-px text-xs text-accent/80">{cnSignal(signal)}</span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="mt-1 flex items-center gap-1.5 pl-0.5">
                  <Bell className="h-3 w-3 shrink-0 text-muted" />
                  {/* message 已含「條件摘要，現價，漲跌幅」(後端生成), 直接展示避免重複 */}
                  {ev.message && <span className="text-xs text-foreground/70 truncate flex-1">{ev.message}</span>}
                </div>
              )}
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
