import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'

/**
 * 全站頂部「資料過期」橫幅。
 *
 * 台股日線不是最新交易日時 (程式沒在 16:30 執行、剛開機等) 顯示一行提醒與
 * 「更新到最新」按鈕；更新走既有 bootstrap/update-latest 端點，不另建流程。
 * 資料正常或狀態未知時完全不渲染。
 */
export function StaleDataBanner() {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: QK.taiwanDataStatus,
    queryFn: api.taiwanDataStatus,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  })
  const update = useMutation({
    mutationFn: api.taiwanUpdateLatest,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: QK.taiwanDataStatus })
      qc.invalidateQueries({ queryKey: ['beginner-selection'] })
      qc.invalidateQueries({ queryKey: ['taiwanAbnormalDiagnostics'] })
      qc.invalidateQueries({ queryKey: ['taiwanMarketIntelligence'] })
      // 端點在上游部分失敗時仍回 200: 要看 ok / failed_dates / 實際抓到幾天, 不能一律報成功
      const failed = Array.isArray(res?.stats?.failed_dates) ? res.stats.failed_dates.length : 0
      if (!res?.ok || failed > 0 || (!res.already_current && res.dates_fetched === 0)) {
        toast.error(`更新未完成：${res?.message || '上游資料來源暫時無法取得'}${failed ? `（${failed} 個交易日失敗）` : ''}`)
        return
      }
      toast.success(res.already_current ? '台股資料已是最新' : '台股資料已更新到最近交易日')
    },
    onError: (err: Error) => toast.error('更新失敗：' + (err.message || '請稍後再試')),
  })

  const data = status.data
  if (!data || data.daily_status === 'current') return null

  const unavailable = data.daily_status === 'unavailable'
  const behind = data.daily_days_behind ?? 0
  const text = unavailable
    ? '目前沒有台股日線資料，看板與選股暫時無法計算。'
    : `日線資料停在 ${data.daily_as_of ?? '未知日期'}，已落後 ${behind} 個交易日（最近交易日 ${data.target_latest_trading_date}）。`

  return (
    <div
      role="status"
      aria-live="polite"
      className="sticky top-0 z-30 flex flex-wrap items-center gap-2 border-b border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning backdrop-blur-sm"
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{text}</span>
      <button
        type="button"
        onClick={() => !update.isPending && update.mutate()}
        disabled={update.isPending}
        className="inline-flex min-h-7 items-center gap-1 rounded-btn border border-warning/40 bg-warning/15 px-2 font-medium text-warning hover:bg-warning/25 disabled:opacity-60"
      >
        {update.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
        {update.isPending ? '更新中…' : '更新到最新'}
      </button>
    </div>
  )
}
