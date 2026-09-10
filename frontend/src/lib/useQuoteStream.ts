import { useEffect, useRef, useCallback, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { SSE_INVALIDATE_PREFIXES } from './queryKeys'
import { getQueryConfig } from './useQueryConfig'
import { toast } from '@/components/Toast'
import { pushAlertToasts } from '@/components/AlertToast'
import type { StrategyAlertEvent } from './api'

// ===== 全局 SSE 連接狀態 (模塊級 store, 仿 AlertToast.tsx 模式) =====
// 實時行情 SSE 斷開時 UI 無感知 → 會漏掉策略告警。這裡暴露連接狀態,
// 供 Layout 頂部渲染徽標、連續失敗 N 次後彈一次 toast。
export type QuoteStreamStatus = 'connected' | 'reconnecting' | 'disconnected'

let _streamStatus: QuoteStreamStatus = 'disconnected'
const _statusListeners = new Set<() => void>()

// 連續失敗到達該閾值後彈一次 toast (只彈一次, 恢復後重置)
const FAILS_BEFORE_TOAST = 3
// 指數退避上限
const BACKOFF_CAP_MS = 60_000

function _emitStatus() {
  _statusListeners.forEach((fn) => fn())
}

function _setStatus(s: QuoteStreamStatus) {
  if (_streamStatus === s) return
  _streamStatus = s
  _emitStatus()
}

function _subscribeStatus(fn: () => void) {
  _statusListeners.add(fn)
  return () => {
    _statusListeners.delete(fn)
  }
}

function _getStatus() {
  return _streamStatus
}

/** React hook: 讀取全局實時行情 SSE 連接狀態 (供 Layout 徽標使用) */
export function useQuoteStreamStatus(): QuoteStreamStatus {
  return useSyncExternalStore(_subscribeStatus, _getStatus, () => 'disconnected' as const)
}

// ===== 焦點股票註冊表 (個股對話框用) =====
// 個股對話框打開時註冊當前 symbol, SSE quotes_updated 推送時精準 invalidate
// 該 symbol 的日K查詢 (['kline', symbol]), 讓日K最後一根蠟燭隨實時價變化。
// 不加進 SSE_INVALIDATE_PREFIXES 全局列表 —— 避免回測彈窗等也每秒重拉。
let _focusSymbol: string | null = null

/** 註冊當前焦點股票 (個股對話框打開時調用)。 */
export function setFocusSymbol(symbol: string): void {
  _focusSymbol = symbol
}

/** 清除焦點股票 (個股對話框關閉時調用)。 */
export function clearFocusSymbol(): void {
  _focusSymbol = null
}

/**
 * 全局 SSE hook: 監聽後端行情更新推送 + 策略監控通知。
 *
 * - 行情更新 (quotes_updated): 根據 sseRefreshPages 配置過濾 invalidation
 * - 策略監控通知 (strategy_alert): 通過 onAlert 回調彈 toast
 *
 * 應在頂層 Layout 中調用一次。
 */
export function useQuoteStream(
  enabled: boolean,
  sseRefreshPages: Record<string, boolean> | undefined,
  onAlert?: (alerts: StrategyAlertEvent[]) => void,
) {
  const qc = useQueryClient()
  const esRef = useRef<EventSource | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout>>()
  const pagesRef = useRef(sseRefreshPages)
  pagesRef.current = sseRefreshPages

  const handleAlerts = useCallback((alerts: StrategyAlertEvent[]) => {
    // depth 系統接管通知: 單獨處理, 不走 strategy 回調
    const depthAlerts = alerts.filter(a => a.source === 'depth')
    const strategyAlerts = alerts.filter(a => a.source !== 'depth')

    // depth 通知直接 toast(防刷屏: 後端已在狀態切換時才推)
    for (const a of depthAlerts.slice(0, 1)) {
      toast(a.message, 'success')
    }

    // 監控告警: 用專用 AlertToast (整批只響一聲, 每條都彈, 受 maxVisible 上限保護)
    if (strategyAlerts.length > 0) {
      // 有 onAlert 回調時走回調, 否則彈 AlertToast
      if (onAlert) {
        onAlert(strategyAlerts)
      }
      // 批量彈通知 (去掉了 slice(0,2) 截斷, 讓每隻新命中都彈 toast; 聲音整批只響一次)
      pushAlertToasts(strategyAlerts as any)
    }
  }, [onAlert])

  const enabledRef = useRef(enabled)
  enabledRef.current = enabled

  useEffect(() => {
    // SSE 始終連接 — 監控告警不依賴實時行情開關
    // (quotes_updated 行情刷新受 enabled 控制, strategy_alert 始終處理)

    // 連續失敗計數 (用於指數退避 + 到閾值彈一次 toast)
    let failCount = 0
    let toastFired = false

    const connect = () => {
      _setStatus(failCount > 0 ? 'reconnecting' : _streamStatus)
      const es = new EventSource('/api/intraday/stream')
      esRef.current = es

      es.onopen = () => {
        // 連接成功: 重置退避與 toast 標記
        failCount = 0
        toastFired = false
        _setStatus('connected')
      }

      // sse-starlette ping 心跳走 SSE comment，不會到達這裡

      es.addEventListener('quotes_updated', () => {
        // 實時行情未開啟時不處理行情刷新
        if (!enabledRef.current) return
        // 根據用戶配置過濾 invalidation
        const pages = pagesRef.current
        if (pages) {
          // 只 invalidate 開啟的頁面對應的 prefix
          const activePrefixes = SSE_INVALIDATE_PREFIXES.filter((p) => {
            // 'quote-status' 始終刷新 (全局狀態)
            if (p === 'quote-status') return true
            // 兼容舊配置: 'watchlist' 拆成兩個精確前綴後, 未單獨設置時沿用舊 'watchlist' 開關
            if (
              (p === 'watchlist-quotes' || p === 'watchlist-enriched') &&
              pages[p] === undefined
            ) {
              return pages['watchlist'] !== false
            }
            return pages[p] !== false
          })
          qc.invalidateQueries({
            predicate: (query) =>
              activePrefixes.some(
                (prefix) => String(query.queryKey[0]).startsWith(prefix),
              ),
          })
        } else {
          // 無配置時全部刷新 (向後兼容)
          qc.invalidateQueries({
            predicate: (query) =>
              SSE_INVALIDATE_PREFIXES.some(
                (prefix) => String(query.queryKey[0]).startsWith(prefix),
              ),
          })
        }
        // 焦點股票日K精準刷新: 個股對話框打開時, 日K最後一根蠟燭隨實時價變化。
        // 後端 _maybe_inject_live_candle 只讀內存緩存, 不調 TickFlow, 秒級重拉零額外成本。
        if (_focusSymbol) {
          qc.invalidateQueries({ queryKey: ['kline', _focusSymbol] })
        }
      })

      es.addEventListener('strategy_results_updated', () => {
        // 策略監控完成後只刷新策略結果緩存，不擴散到其他行情頁面。
        qc.invalidateQueries({ queryKey: ['screener-cached'] })
      })

      es.addEventListener('depth_updated', () => {
        // 五檔修正完成後刷新看板封單數據。
        // 不受實時行情開關限制 — 修正輪詢獨立於行情輪詢, 用戶開了修正就想看實時封單。
        qc.invalidateQueries({ queryKey: ['overview-market'] })
      })

      es.addEventListener('strategy_alert', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data)
            const alerts: StrategyAlertEvent[] = data.alerts || []
            if (alerts.length > 0) {
              handleAlerts(alerts)
              // 實時刷新觸發記錄列表 + 監控中心徽標
              qc.invalidateQueries({ queryKey: ['alerts'] })
              qc.invalidateQueries({ queryKey: ['alerts-total'] })
            }
        } catch {
          // 忽略解析錯誤
        }
      })

      es.onerror = () => {
        es.close()
        esRef.current = null
        failCount += 1
        _setStatus('reconnecting')
        // 連續失敗到閾值 → 彈一次 toast (漏行情=可能漏策略告警, 需明確告知)
        if (failCount >= FAILS_BEFORE_TOAST && !toastFired) {
          toastFired = true
          toast('即時連線已中斷，正在重連…', 'error')
        }
        // 指數退避 (base * 2^(n-1), 上限 60s), 替代原來固定 5s
        const base = getQueryConfig().sse.reconnectDelay
        const delay = Math.min(base * 2 ** (failCount - 1), BACKOFF_CAP_MS)
        retryRef.current = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      clearTimeout(retryRef.current)
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      _setStatus('disconnected')
    }
  }, [qc, handleAlerts])
}
