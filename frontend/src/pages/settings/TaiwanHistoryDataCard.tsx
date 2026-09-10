import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  RefreshCw,
  Calendar,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Database,
  ShieldCheck,
  Layers,
} from 'lucide-react'
import {
  api,
  type TaiwanHistoryStatus,
  type TaiwanBootstrapJobState,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'

export function TaiwanHistoryDataCard() {
  const qc = useQueryClient()
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<TaiwanBootstrapJobState | null>(null)

  // 1. 查詢台股歷史日 K 本機存儲狀態
  const {
    data: historyStatus,
    isLoading: isStatusLoading,
    refetch: refetchStatus,
  } = useQuery<TaiwanHistoryStatus>({
    queryKey: QK.taiwanHistoryStatus,
    queryFn: api.taiwanHistoryStatus,
    staleTime: 30_000,
  })

  // 2. 觸發 Bootstrap 下載任務
  const bootstrapMutation = useMutation({
    mutationFn: api.taiwanBootstrapRun,
    onSuccess: (res) => {
      setActiveJobId(res.job_id)
      toast('台股歷史日 K 下載任務已啟動', 'success')
    },
    onError: (err: any) => {
      toast(`啟動下載失敗: ${err.message || err}`, 'error')
    },
  })

  // 3. 觸發「更新到最新」增量補齊
  const updateLatestMutation = useMutation({
    mutationFn: api.taiwanUpdateLatest,
    onSuccess: (res) => {
      toast(res.message || '更新完成', 'success')
      qc.invalidateQueries({ queryKey: QK.taiwanHistoryStatus })
      qc.invalidateQueries({ queryKey: QK.taiwanDataStatus })
    },
    onError: (err: any) => {
      toast(`更新至最新交易日失敗: ${err.message || err}`, 'error')
    },
  })

  // 4. 當有 activeJobId 時，輪詢任務進度直到完成
  useEffect(() => {
    if (!activeJobId) return

    let cancelled = false
    let timerId: any = null

    const checkProgress = async () => {
      try {
        const job = await api.taiwanBootstrapJob(activeJobId)
        if (cancelled) return
        setActiveJob(job)

        if (job.status === 'success') {
          toast(job.message || '歷史日 K 資料包匯入成功！', 'success')
          qc.invalidateQueries({ queryKey: QK.taiwanHistoryStatus })
          qc.invalidateQueries({ queryKey: QK.taiwanDataStatus })
          setActiveJobId(null)
          return
        }

        if (job.status === 'failed') {
          toast(job.error || '歷史資料包下載匯入失敗', 'error')
          setActiveJobId(null)
          return
        }

        timerId = setTimeout(checkProgress, 800)
      } catch (err: any) {
        if (!cancelled) {
          timerId = setTimeout(checkProgress, 2000)
        }
      }
    }

    checkProgress()

    return () => {
      cancelled = true
      if (timerId) clearTimeout(timerId)
    }
  }, [activeJobId, qc])

  const isJobRunning =
    activeJobId !== null ||
    (activeJob !== null &&
      activeJob.status !== 'success' &&
      activeJob.status !== 'failed')

  const assetInfo = historyStatus?.asset_info
  const approxSize = assetInfo?.approx_size_mb ?? 31
  const dateRange = assetInfo?.date_range ?? '2024-01-02 ~ 2026-09-10'

  return (
    <section className="rounded-card border border-border bg-surface p-6">
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
            <Database className="h-5 w-5 text-accent" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">
                台股歷史日 K 資料庫
              </h3>
              {historyStatus?.has_data ? (
                <span className="inline-flex items-center gap-1 text-[10px] text-emerald-500 bg-emerald-500/10 px-2 py-0.5 rounded font-medium">
                  <CheckCircle2 className="h-3 w-3" /> 資料已就緒
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] text-amber-500 bg-amber-500/10 px-2 py-0.5 rounded font-medium">
                  <AlertCircle className="h-3 w-3" /> 缺少歷史日 K
                </span>
              )}
            </div>
            <p className="text-xs text-secondary mt-1">
              本地 Parquet 高速列式存儲，供回測、選股指標預計算與個股歷史 K 線圖使用。
            </p>
          </div>
        </div>

        {/* 右上角操作按鈕 */}
        <div className="flex items-center gap-2">
          {historyStatus?.has_data && (
            <button
              type="button"
              disabled={updateLatestMutation.isPending || isJobRunning}
              onClick={() => updateLatestMutation.mutate()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn bg-accent text-white text-xs font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${
                  updateLatestMutation.isPending ? 'animate-spin' : ''
                }`}
              />
              {updateLatestMutation.isPending ? '正在更新行情...' : '更新到最新'}
            </button>
          )}

          <button
            type="button"
            disabled={isStatusLoading}
            onClick={() => refetchStatus()}
            title="重新檢查本地資料狀態"
            className="p-1.5 text-muted hover:text-foreground rounded hover:bg-elevated/60 transition-colors"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isStatusLoading ? 'animate-spin' : ''}`}
            />
          </button>
        </div>
      </div>

      {/* 狀態展示區 */}
      {isStatusLoading ? (
        <div className="py-6 flex items-center justify-center gap-2 text-xs text-muted">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          正在檢查本地台股歷史日 K 資料狀態...
        </div>
      ) : historyStatus?.has_data ? (
        /* 已有歷史資料狀態 */
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="rounded-lg border border-border/50 bg-elevated/30 p-3">
              <div className="flex items-center gap-1.5 text-muted text-xs mb-1">
                <Calendar className="h-3.5 w-3.5" />
                <span>最早交易日</span>
              </div>
              <div className="text-sm font-semibold text-foreground">
                {historyStatus.earliest_date || '無'}
              </div>
            </div>

            <div className="rounded-lg border border-border/50 bg-elevated/30 p-3">
              <div className="flex items-center gap-1.5 text-muted text-xs mb-1">
                <Calendar className="h-3.5 w-3.5" />
                <span>最新交易日</span>
              </div>
              <div className="text-sm font-semibold text-foreground">
                {historyStatus.latest_date || '無'}
              </div>
            </div>

            <div className="rounded-lg border border-border/50 bg-elevated/30 p-3">
              <div className="flex items-center gap-1.5 text-muted text-xs mb-1">
                <Layers className="h-3.5 w-3.5" />
                <span>涵蓋交易天數</span>
              </div>
              <div className="text-sm font-semibold text-foreground">
                {historyStatus.trading_days.toLocaleString()} 天
              </div>
            </div>
          </div>

          {/* 若正在執行背景 Job，顯示進度條 */}
          {isJobRunning && activeJob && (
            <div className="rounded-lg border border-accent/20 bg-accent/5 p-4 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-accent flex items-center gap-2">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {activeJob.stage}
                </span>
                <span className="font-mono text-muted">{activeJob.progress}%</span>
              </div>
              <div className="w-full bg-border rounded-full h-2 overflow-hidden">
                <div
                  className="bg-accent h-2 rounded-full transition-all duration-300 ease-out"
                  style={{ width: `${Math.max(5, activeJob.progress)}%` }}
                />
              </div>
              <p className="text-[11px] text-secondary">{activeJob.message}</p>
            </div>
          )}

          <div className="flex items-center justify-between text-xs text-muted pt-2 border-t border-border/40">
            <span>
              已有完整歷史資料。日常運作中，系統於收盤後自動向官方增量同步。
            </span>
            <button
              type="button"
              disabled={bootstrapMutation.isPending || isJobRunning}
              onClick={() => {
                if (
                  window.confirm(
                    '確認要重新下載官方 GitHub Release 歷史資料包嗎？既有同日期分區將被覆寫，新分區與用戶資料會妥善保留。'
                  )
                ) {
                  bootstrapMutation.mutate()
                }
              }}
              className="text-[11px] text-muted hover:text-accent underline transition-colors"
            >
              重新下載完整歷史資料包
            </button>
          </div>
        </div>
      ) : (
        /* 尚未下載歷史資料，引導一鍵下載 */
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4 space-y-4">
          <div className="flex items-start gap-3">
            <ShieldCheck className="h-5 w-5 text-accent shrink-0 mt-0.5" />
            <div className="space-y-1">
              <h4 className="text-xs font-semibold text-foreground">
                一鍵下載官方歷史日 K 資料包
              </h4>
              <p className="text-xs text-secondary leading-relaxed">
                本機目前尚未建立台股歷史日 K 資料。您可以一鍵從 GitHub Release
                下載官方已打包好的資料集（約 {approxSize} MiB，涵蓋 {dateRange}
                ），下載後系統會自動完成 SHA256
                安全校驗、解壓縮與本機資料庫匯入，並立即自動向 TWSE / TPEx
                補齊至最新交易日。
              </p>
            </div>
          </div>

          {/* 正在下載/解壓縮中的進度條 */}
          {isJobRunning && activeJob ? (
            <div className="rounded-lg border border-accent/20 bg-surface p-4 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-accent flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {activeJob.stage}
                </span>
                <span className="font-mono text-muted">{activeJob.progress}%</span>
              </div>
              <div className="w-full bg-border rounded-full h-2.5 overflow-hidden">
                <div
                  className="bg-accent h-2.5 rounded-full transition-all duration-300 ease-out"
                  style={{ width: `${Math.max(5, activeJob.progress)}%` }}
                />
              </div>
              <p className="text-xs text-secondary font-mono">
                {activeJob.message}
              </p>
            </div>
          ) : (
            <div className="flex items-center gap-3 pt-1">
              <button
                type="button"
                disabled={bootstrapMutation.isPending}
                onClick={() => bootstrapMutation.mutate()}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-btn bg-accent text-white text-xs font-medium hover:bg-accent/90 transition-colors shadow-sm disabled:opacity-50"
              >
                {bootstrapMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                <span>
                  {bootstrapMutation.isPending
                    ? '正在啟動下載...'
                    : `一鍵下載歷史資料包 (約 ${approxSize} MiB)`}
                </span>
              </button>

              <span className="text-[11px] text-muted">
                全程採原子式寫入，嚴格隔離 user_data，安全無風險。
              </span>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
