import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  History,
  TrendingUp,
  Clock,
  Trash2,
  ArrowUpRight,
  BarChart3,
  Calendar,
  AlertCircle,
  Layers,
  ChevronRight,
  ArrowLeft,
} from 'lucide-react'
import {
  api,
  type SnapshotListItem,
  type SnapshotReviewDetail,
  type ForwardBatchStats,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import { CopyButton } from '@/components/CopyButton'
import { formatSelectionReviewCopy, formatSelectionReviewPrompt } from '@/lib/copy-formatters'
import { SelectionForwardPanel, type SelectionForwardBatchReviewView } from '@/components/selection/SelectionForwardPanel'

type ReviewTab = 'snapshots' | 'forward' | 'strategies' | 'conditions'

function toForwardBatchReview(detail: SnapshotReviewDetail): SelectionForwardBatchReviewView {
  return {
    statistics: {
      '1D': { referenceCloseReturnPct: detail.h1d_reference_close_avg_return_pct ?? null, referenceCloseEvaluableCount: detail.h1d_reference_close_evaluated_count ?? 0, paperReturnPct: detail.h1d_avg_return_pct ?? null, benchmark0050ReturnPct: detail.h1d_bm_avg_return_pct ?? null, benchmarkEvaluableCount: detail.h1d_bm_evaluated_count ?? 0, excessReturnPct: detail.h1d_avg_excess_pct ?? null, excessEvaluableCount: detail.h1d_excess_evaluated_count ?? 0,
        evaluableCount: detail.h1d_evaluated_count ?? 0, trackingCount: detail.h1d_pending_count ?? 0, missingCount: detail.h1d_unavailable_count ?? 0 },
      '5D': { referenceCloseReturnPct: detail.h5d_reference_close_avg_return_pct ?? null, referenceCloseEvaluableCount: detail.h5d_reference_close_evaluated_count ?? 0, paperReturnPct: detail.h5d_avg_return_pct, benchmark0050ReturnPct: detail.h5d_bm_avg_return_pct, benchmarkEvaluableCount: detail.h5d_bm_evaluated_count ?? 0, excessReturnPct: detail.h5d_avg_excess_pct, excessEvaluableCount: detail.h5d_excess_evaluated_count ?? 0,
        evaluableCount: detail.h5d_evaluated_count, trackingCount: detail.h5d_pending_count ?? 0, missingCount: detail.h5d_unavailable_count ?? 0 },
      '20D': { referenceCloseReturnPct: detail.h20d_reference_close_avg_return_pct ?? null, referenceCloseEvaluableCount: detail.h20d_reference_close_evaluated_count ?? 0, paperReturnPct: detail.h20d_avg_return_pct, benchmark0050ReturnPct: detail.h20d_bm_avg_return_pct, benchmarkEvaluableCount: detail.h20d_bm_evaluated_count ?? 0, excessReturnPct: detail.h20d_avg_excess_pct, excessEvaluableCount: detail.h20d_excess_evaluated_count ?? 0,
        evaluableCount: detail.h20d_evaluated_count, trackingCount: detail.h20d_pending_count ?? 0, missingCount: detail.h20d_unavailable_count ?? 0 },
    },
    items: detail.evaluated_items.map(item => ({
      symbol: item.symbol, name: item.name,
      referencePrice: item.entry_price,
      paperEntryPrice: item.paper_entry_price ?? null,
      paperEntryPriceStatus: reviewStatus(item.entry_status ?? 'pending'),
      entryReason: item.status_reasons?.entry,
      referenceCloseReturns: {
        '1D': { status: reviewStatus(item.h1d_reference_close_status ?? 'pending'), returnPct: item.h1d_reference_close_return_pct ?? null, reason: item.status_reasons?.h1d_reference_close },
        '5D': { status: reviewStatus(item.h5d_reference_close_status ?? 'pending'), returnPct: item.h5d_reference_close_return_pct ?? null, reason: item.status_reasons?.h5d_reference_close },
        '20D': { status: reviewStatus(item.h20d_reference_close_status ?? 'pending'), returnPct: item.h20d_reference_close_return_pct ?? null, reason: item.status_reasons?.h20d_reference_close },
      },
      paperEntryReturns: {
        '1D': { status: reviewStatus(item.h1d_status), returnPct: item.h1d_return_pct, reason: item.status_reasons?.h1d },
        '5D': { status: reviewStatus(item.h5d_status), returnPct: item.h5d_return_pct, reason: item.status_reasons?.h5d },
        '20D': { status: reviewStatus(item.h20d_status), returnPct: item.h20d_return_pct, reason: item.status_reasons?.h20d },
      },
      benchmarkReturns: {
        '1D': { status: metricStatus(item.h1d_bm_status, item.h1d_bm_return_pct), returnPct: item.h1d_bm_return_pct, reason: item.status_reasons?.h1d_benchmark },
        '5D': { status: metricStatus(item.h5d_bm_status, item.h5d_bm_return_pct), returnPct: item.h5d_bm_return_pct, reason: item.status_reasons?.h5d_benchmark },
        '20D': { status: metricStatus(item.h20d_bm_status, item.h20d_bm_return_pct), returnPct: item.h20d_bm_return_pct, reason: item.status_reasons?.h20d_benchmark },
      },
      excessReturns: {
        '1D': { status: excessStatus(item.h1d_status, item.h1d_bm_status, item.h1d_excess_pct), returnPct: item.h1d_excess_pct, reason: item.status_reasons?.h1d_benchmark },
        '5D': { status: excessStatus(item.h5d_status, item.h5d_bm_status, item.h5d_excess_pct), returnPct: item.h5d_excess_pct, reason: item.status_reasons?.h5d_benchmark },
        '20D': { status: excessStatus(item.h20d_status, item.h20d_bm_status, item.h20d_excess_pct), returnPct: item.h20d_excess_pct, reason: item.status_reasons?.h20d_benchmark },
      },
    })),
    metadata: {
      lockedAt: detail.snapshot.locked_at ?? null,
      targetTradeDate: detail.snapshot.target_trade_date ?? null,
      targetTradeDateStatus: detail.snapshot.target_trade_date_status ?? null,
      ruleVersion: detail.snapshot.rule_version ?? null,
      selectedCount: detail.snapshot.items.length,
      priceAdjustment: detail.snapshot.price_adjustment ?? null,
      costAssumption: detail.snapshot.cost_assumption ?? null,
    },
  }
}

function reviewStatus(status: 'completed' | 'pending' | 'unavailable') {
  return status === 'completed' ? 'completed' : status === 'pending' ? 'tracking' : 'missing'
}

function metricStatus(status: 'completed' | 'pending' | 'unavailable' | undefined, value: number | null) {
  if (value === null) return status === 'pending' ? 'tracking' : 'missing'
  return 'completed'
}

function excessStatus(stock: 'completed' | 'pending' | 'unavailable', benchmark: 'completed' | 'pending' | 'unavailable', value: number | null) {
  if (value !== null) return 'completed'
  return stock === 'pending' || benchmark === 'pending' ? 'tracking' : 'missing'
}

function ForwardStats({ stats, formatPct }: { stats: ForwardBatchStats; formatPct: (value: number | null | undefined) => string }) {
  const horizons = [
    { label: '1D', evaluated: stats.h1d_evaluated_count, pending: stats.h1d_pending_count, missing: stats.h1d_unavailable_count, hitRate: stats.h1d_hit_rate_pct, paper: stats.h1d_avg_return_pct, bm: stats.h1d_bm_avg_return_pct, bmCount: stats.h1d_bm_evaluated_count, excess: stats.h1d_avg_excess_pct, excessCount: stats.h1d_excess_evaluated_count, reference: stats.h1d_reference_close_avg_return_pct, referenceCount: stats.h1d_reference_close_evaluated_count },
    { label: '5D', evaluated: stats.h5d_evaluated_count, pending: stats.h5d_pending_count, missing: stats.h5d_unavailable_count, hitRate: stats.h5d_hit_rate_pct, paper: stats.h5d_avg_return_pct, bm: stats.h5d_bm_avg_return_pct, bmCount: stats.h5d_bm_evaluated_count, excess: stats.h5d_avg_excess_pct, excessCount: stats.h5d_excess_evaluated_count, reference: stats.h5d_reference_close_avg_return_pct, referenceCount: stats.h5d_reference_close_evaluated_count },
    { label: '20D', evaluated: stats.h20d_evaluated_count, pending: stats.h20d_pending_count, missing: stats.h20d_unavailable_count, hitRate: stats.h20d_hit_rate_pct, paper: stats.h20d_avg_return_pct, bm: stats.h20d_bm_avg_return_pct, bmCount: stats.h20d_bm_evaluated_count, excess: stats.h20d_avg_excess_pct, excessCount: stats.h20d_excess_evaluated_count, reference: stats.h20d_reference_close_avg_return_pct, referenceCount: stats.h20d_reference_close_evaluated_count },
  ]
  return <section aria-label="全部正式批次統計" className="space-y-2">
    <p className="text-xs font-medium">全部正式批次：{stats.batches_count} 批、{stats.picks_count} 檔，以下採後端整批統計</p>
    <div className="grid gap-2 md:grid-cols-3">{horizons.map(h => <section key={h.label} className="rounded-lg border border-border/60 bg-card p-3 text-xs"><h3 className="font-semibold">{h.label}</h3><dl className="mt-2 grid grid-cols-2 gap-y-1"><dt className="text-muted-foreground">參考收盤漲跌</dt><dd className="text-right">{formatPct(h.reference)}（{h.referenceCount} 檔）</dd><dt className="text-muted-foreground">紙上進場報酬</dt><dd className="text-right">{formatPct(h.paper)}</dd><dt className="text-muted-foreground">0050 同期報酬</dt><dd className="text-right">{formatPct(h.bm)}（{h.bmCount} 檔）</dd><dt className="text-muted-foreground">超額報酬</dt><dd className="text-right">{formatPct(h.excess)}（{h.excessCount} 檔）</dd><dt className="text-muted-foreground">可評估／追蹤／缺資料</dt><dd className="text-right">{h.evaluated}／{h.pending}／{h.missing}</dd><dt className="text-muted-foreground">後端命中率</dt><dd className="text-right">{h.hitRate == null ? (h.evaluated === 0 && h.pending > 0 ? '尚未到期' : h.evaluated === 0 && h.missing === 0 ? '尚無樣本' : '資料不足') : formatPct(h.hitRate)}</dd></dl></section>)}</div>
  </section>
}

export function SelectionReview() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const activeTab: ReviewTab = requestedTab === 'forward' || requestedTab === 'strategies' || requestedTab === 'conditions'
    ? requestedTab
    : 'snapshots'
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null)
  const selectedForwardBatchId = searchParams.get('batch_id')
  const [strategyFilter, setStrategyFilter] = useState<string>('all')

  // 1. 快照列表
  const snapshotsQuery = useQuery({
    queryKey: QK.selectionSnapshots(strategyFilter === 'all' ? undefined : strategyFilter),
    queryFn: () =>
      api.selectionReview.listSnapshots(
        strategyFilter === 'all' ? undefined : { strategy_id: strategyFilter },
      ),
  })

  // 2. 單一快照詳情
  const snapshotDetailQuery = useQuery({
    queryKey: QK.selectionSnapshotDetail(selectedSnapshotId || ''),
    queryFn: () =>
      selectedSnapshotId ? api.selectionReview.getSnapshotDetail(selectedSnapshotId) : null,
    enabled: !!selectedSnapshotId,
  })

  const forwardBatchesQuery = useQuery({
    queryKey: QK.selectionForwardBatches,
    queryFn: () => api.selectionReview.listForwardBatches(),
    enabled: activeTab === 'forward',
  })
  const forwardStatsQuery = useQuery({
    queryKey: QK.selectionForwardStats,
    queryFn: () => api.selectionReview.getForwardBatchStats(),
    enabled: activeTab === 'forward',
  })
  const forwardBatchDetailQuery = useQuery({
    queryKey: QK.selectionForwardBatchDetail(selectedForwardBatchId || ''),
    queryFn: () => selectedForwardBatchId ? api.selectionReview.getForwardBatchDetail(selectedForwardBatchId) : null,
    enabled: activeTab === 'forward' && !!selectedForwardBatchId,
  })

  // 3. 策略統計
  const strategyStatsQuery = useQuery({
    queryKey: QK.selectionStrategyStats,
    queryFn: () => api.selectionReview.getStrategyStats(),
    enabled: activeTab === 'strategies',
  })

  // 4. 條件統計
  const conditionStatsQuery = useQuery({
    queryKey: QK.selectionConditionStats,
    queryFn: () => api.selectionReview.getConditionStats(),
    enabled: activeTab === 'conditions',
  })

  // 刪除快照 mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.selectionReview.deleteSnapshot(id),
    onSuccess: (_, deletedId) => {
      toast.success('已刪除選股快照')
      qc.invalidateQueries({ queryKey: ['selection-snapshots'] })
      qc.invalidateQueries({ queryKey: QK.selectionStrategyStats })
      qc.invalidateQueries({ queryKey: QK.selectionConditionStats })
      if (selectedSnapshotId === deletedId) {
        setSelectedSnapshotId(null)
      }
    },
    onError: (err: any) => {
      toast.error('刪除失敗: ' + (err.message || '未知錯誤'))
    },
  })

  // 所有不重複的策略選項
  const strategyOptions = Array.from(
    new Set((snapshotsQuery.data || []).map((s) => s.strategy_name)),
  )

  const formatPct = (val: number | null | undefined) => {
    if (val === null || val === undefined) return '-'
    const prefix = val > 0 ? '+' : ''
    return `${prefix}${val.toFixed(2)}%`
  }

  const getReturnColor = (val: number | null | undefined) => {
    if (val === null || val === undefined) return 'text-muted-foreground'
    if (val > 0) return 'text-rose-500 font-medium'
    if (val < 0) return 'text-emerald-500 font-medium'
    return 'text-muted-foreground'
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto px-4 py-6">
      {/* 頂部標題 */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border/40 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-primary/10 text-primary">
              <History className="h-6 w-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight">選股復盤與策略追蹤</h1>
              <p className="text-sm text-muted-foreground mt-0.5">
                追蹤選股當下事實與後續 1D / 5D / 20D 實際交易日表現，評估策略與入選原因勝率
              </p>
            </div>
          </div>
        </div>

        {/* 頁籤切換 */}
        <div className="flex items-center bg-muted/60 p-1 rounded-lg border border-border/40 text-sm">
          <button
            onClick={() => {
              setSearchParams({})
              setSelectedSnapshotId(null)
            }}
            className={cn(
              'px-3.5 py-1.5 rounded-md font-medium transition-all flex items-center gap-1.5',
              activeTab === 'snapshots'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Layers className="h-4 w-4" />
            研究快照
          </button>
          <button
            onClick={() => {
              setSearchParams({ tab: 'forward' })
              setSelectedSnapshotId(null)
            }}
            className={cn(
              'px-3.5 py-1.5 rounded-md font-medium transition-all flex items-center gap-1.5',
              activeTab === 'forward'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Clock className="h-4 w-4" />
            正式前瞻批次
          </button>
          <button
            onClick={() => setSearchParams({ tab: 'strategies' })}
            className={cn(
              'px-3.5 py-1.5 rounded-md font-medium transition-all flex items-center gap-1.5',
              activeTab === 'strategies'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <TrendingUp className="h-4 w-4" />
            策略統計
          </button>
          <button
            onClick={() => setSearchParams({ tab: 'conditions' })}
            className={cn(
              'px-3.5 py-1.5 rounded-md font-medium transition-all flex items-center gap-1.5',
              activeTab === 'conditions'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <BarChart3 className="h-4 w-4" />
            條件成效
          </button>
        </div>
      </div>

      {activeTab === 'forward' && (
        <section aria-label="正式前瞻批次" className="space-y-4">
          {forwardStatsQuery.data && <ForwardStats stats={forwardStatsQuery.data} formatPct={formatPct} />}
          {selectedForwardBatchId ? (
            <div className="space-y-3">
              <button type="button" onClick={() => setSearchParams({ tab: 'forward' })} className="text-xs text-primary hover:underline">返回正式批次清單</button>
              {forwardBatchDetailQuery.isLoading ? <p className="py-8 text-center text-sm text-muted-foreground">載入正式批次復盤…</p> : forwardBatchDetailQuery.isError ? (
                <div role="alert" className="space-y-2 rounded-lg border border-destructive/30 p-4 text-sm"><p>正式批次復盤載入失敗。</p><button type="button" onClick={() => forwardBatchDetailQuery.refetch()} className="text-primary underline">重新載入</button></div>
              ) : forwardBatchDetailQuery.data ? (
                <SelectionForwardPanel batchReview={toForwardBatchReview(forwardBatchDetailQuery.data)} />
              ) : <p role="status" className="py-8 text-center text-sm text-muted-foreground">找不到正式批次或目前無法載入。</p>}
            </div>
          ) : forwardBatchesQuery.isLoading ? <p className="py-8 text-center text-sm text-muted-foreground">載入正式批次…</p> : forwardBatchesQuery.isError ? (
            <div role="alert" className="space-y-2 rounded-lg border border-destructive/30 p-4 text-sm"><p>正式批次列表載入失敗，尚無法確認是否有批次。</p><button type="button" onClick={() => forwardBatchesQuery.refetch()} className="text-primary underline">重新載入</button></div>
          ) :
            forwardBatchesQuery.data?.length ? <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">{forwardBatchesQuery.data.filter(batch => batch.record_type === 'forward_batch').map(batch => (
              <button key={batch.snapshot_id} type="button" onClick={() => setSearchParams({ tab: 'forward', batch_id: batch.snapshot_id })} className="rounded-xl border border-border/60 bg-card p-4 text-left transition-colors hover:border-primary/50">
                <span className="text-xs font-semibold text-primary">{batch.rule_version ?? '正式前瞻批次'}</span>
                <span className="mt-2 block font-semibold">來源日期 {batch.source_data_date ?? batch.as_of_date}</span>
                <span className="mt-1 block text-xs text-muted-foreground">預定進場 {batch.target_trade_date ?? '資料不足'} · {batch.selected_count} 檔</span>
                <span className="mt-2 inline-flex items-center gap-1 text-xs text-primary">查看批次復盤 <ChevronRight className="h-3 w-3" /></span>
              </button>
            ))}</div> : <div className="rounded-xl border border-dashed border-border/70 bg-card/40 p-8 text-center"><Clock className="mx-auto mb-3 h-9 w-9 text-muted-foreground/50" /><h2 className="font-medium">尚無正式前瞻批次</h2><p className="mt-2 text-xs text-muted-foreground">台股選股頁可在合適時段預覽並明確鎖定正式名單。</p><Link to="/taiwan-screener" className="mt-4 inline-flex items-center gap-1 text-xs text-primary">前往台股選股 <ChevronRight className="h-3 w-3" /></Link></div>}
        </section>
      )}

      {/* TAB 1: 快照復盤 (列表 or 詳情) */}
      {activeTab === 'snapshots' && (
        <div className="space-y-4">
          {selectedSnapshotId ? (
            /* 快照詳情 View */
            <SnapshotDetailView
              detail={snapshotDetailQuery.data}
              isLoading={snapshotDetailQuery.isLoading}
              onBack={() => setSelectedSnapshotId(null)}
              onDelete={(id) => {
                if (window.confirm('確定要刪除這筆選股快照嗎？此操作無法還原。')) {
                  deleteMutation.mutate(id)
                }
              }}
              formatPct={formatPct}
              getReturnColor={getReturnColor}
            />
          ) : (
            /* 快照清單 View */
            <div className="space-y-4">
              {/* 工具列與篩選 */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">策略篩選：</span>
                  <select
                    value={strategyFilter}
                    onChange={(e) => setStrategyFilter(e.target.value)}
                    className="text-xs bg-background border border-border/60 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    <option value="all">全部策略 ({snapshotsQuery.data?.length || 0})</option>
                    {strategyOptions.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3.5 w-3.5" />
                  <span>以真實交易日推進 Horizons，未到期標記追蹤中，缺資料不補 0</span>
                </div>
              </div>

              {/* 列表內容 */}
              {snapshotsQuery.isLoading ? (
                <div className="text-center py-12 text-sm text-muted-foreground">載入快照中...</div>
              ) : !snapshotsQuery.data || snapshotsQuery.data.length === 0 ? (
                <div className="text-center py-16 border border-dashed rounded-xl p-8 bg-card/40">
                  <History className="h-10 w-10 text-muted-foreground/40 mx-auto mb-3" />
                  <h3 className="font-medium text-base">目前尚無已保存的選股快照</h3>
                  <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">
                    您可以在「台股選股」或「自訂策略」執行篩選後，點擊「保存本次選股」，即可在此追蹤後續真實交易日表現。
                  </p>
                  <Link
                    to="/taiwan-screener"
                    className="inline-flex items-center gap-1.5 mt-4 text-xs font-medium bg-primary text-primary-foreground px-3.5 py-2 rounded-lg hover:bg-primary/90 transition-colors"
                  >
                    前往台股選股
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Link>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {snapshotsQuery.data.map((item) => (
                    <SnapshotCard
                      key={item.snapshot_id}
                      item={item}
                      onClick={() => setSelectedSnapshotId(item.snapshot_id)}
                      onDelete={(e) => {
                        e.stopPropagation()
                        if (window.confirm(`確定要刪除「${item.strategy_name}」快照嗎？`)) {
                          deleteMutation.mutate(item.snapshot_id)
                        }
                      }}
                      formatPct={formatPct}
                      getReturnColor={getReturnColor}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: 策略統計 */}
      {activeTab === 'strategies' && (
        <div className="space-y-4">
          <div className="bg-card border border-border/60 rounded-xl p-4 text-xs text-muted-foreground flex items-start gap-2.5">
            <AlertCircle className="h-4 w-4 text-primary shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-foreground">策略復盤指標說明</p>
              <p className="mt-0.5">
                • <strong>勝率 (Hit Rate)</strong>：嚴格定義為個股期間報酬率大於 0% 之比率。
                <br />• <strong>超額報酬 (Benchmark Excess)</strong>：以台灣50指數 ETF (0050.TWSE) 為基準。超額報酬不等於 Alpha，過去表現不代表未來獲利保證。
              </p>
            </div>
          </div>

          {strategyStatsQuery.isLoading ? (
            <div className="text-center py-12 text-sm text-muted-foreground">計算策略統計中...</div>
          ) : !strategyStatsQuery.data || strategyStatsQuery.data.length === 0 ? (
            <div className="text-center py-16 border border-dashed rounded-xl p-8 bg-card/40">
              <TrendingUp className="h-10 w-10 text-muted-foreground/40 mx-auto mb-3" />
              <h3 className="font-medium text-base">暫無策略評估數據</h3>
              <p className="text-xs text-muted-foreground mt-1">
                保存快照並累積至 5 個以上交易日後，系統將自動計算策略報酬與勝率統計。
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto border border-border/60 rounded-xl bg-card">
              <table className="w-full text-left text-xs">
                <thead className="bg-muted/40 border-b border-border/60 text-muted-foreground font-medium">
                  <tr>
                    <th className="py-3 px-4">策略名稱</th>
                    <th className="py-3 px-4 text-center">快照次數</th>
                    <th className="py-3 px-4 text-center">5D 樣本</th>
                    <th className="py-3 px-4 text-right">5D 平均報酬</th>
                    <th className="py-3 px-4 text-right">5D 勝率</th>
                    <th className="py-3 px-4 text-right">5D 超額 (vs 0050)</th>
                    <th className="py-3 px-4 text-center">20D 樣本</th>
                    <th className="py-3 px-4 text-right">20D 平均報酬</th>
                    <th className="py-3 px-4 text-right">20D 勝率</th>
                    <th className="py-3 px-4 text-right">20D 超額 (vs 0050)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {strategyStatsQuery.data.map((strat) => (
                    <tr key={strat.strategy_id} className="hover:bg-muted/20">
                      <td className="py-3 px-4 font-medium text-foreground">{strat.strategy_name}</td>
                      <td className="py-3 px-4 text-center text-muted-foreground">{strat.snapshots_count}</td>
                      <td className="py-3 px-4 text-center text-muted-foreground">{strat.evaluated_picks_5d}</td>
                      <td className={cn('py-3 px-4 text-right', getReturnColor(strat.avg_return_5d))}>
                        {formatPct(strat.avg_return_5d)}
                      </td>
                      <td className="py-3 px-4 text-right font-medium">
                        {strat.hit_rate_5d !== null ? `${strat.hit_rate_5d.toFixed(1)}%` : '-'}
                      </td>
                      <td className={cn('py-3 px-4 text-right', getReturnColor(strat.bm_excess_5d))}>
                        {formatPct(strat.bm_excess_5d)}
                      </td>
                      <td className="py-3 px-4 text-center text-muted-foreground">{strat.evaluated_picks_20d}</td>
                      <td className={cn('py-3 px-4 text-right', getReturnColor(strat.avg_return_20d))}>
                        {formatPct(strat.avg_return_20d)}
                      </td>
                      <td className="py-3 px-4 text-right font-medium">
                        {strat.hit_rate_20d !== null ? `${strat.hit_rate_20d.toFixed(1)}%` : '-'}
                      </td>
                      <td className={cn('py-3 px-4 text-right', getReturnColor(strat.bm_excess_20d))}>
                        {formatPct(strat.bm_excess_20d)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: 條件成效 */}
      {activeTab === 'conditions' && (
        <div className="space-y-4">
          <div className="bg-amber-500/10 border border-amber-500/20 text-amber-700 dark:text-amber-400 rounded-xl p-4 text-xs flex items-start gap-2.5">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">嚴格客觀提示</p>
              <p className="mt-0.5">
                此處僅呈現歷史入選條件之<strong>敘述性統計 (Descriptive Statistics)</strong>，不代表任何因果關係。系統不會自動微調或過度擬合條件權重。當樣本數小於 5 筆時，將明確提示「樣本不足」。
              </p>
            </div>
          </div>

          {conditionStatsQuery.isLoading ? (
            <div className="text-center py-12 text-sm text-muted-foreground">計算條件成效中...</div>
          ) : !conditionStatsQuery.data || conditionStatsQuery.data.length === 0 ? (
            <div className="text-center py-16 border border-dashed rounded-xl p-8 bg-card/40">
              <BarChart3 className="h-10 w-10 text-muted-foreground/40 mx-auto mb-3" />
              <h3 className="font-medium text-base">暫無條件成效統計</h3>
              <p className="text-xs text-muted-foreground mt-1">
                建立快照時將自動解析入選原因與條件標籤，累積成熟交易日後即行彙整。
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto border border-border/60 rounded-xl bg-card">
              <table className="w-full text-left text-xs">
                <thead className="bg-muted/40 border-b border-border/60 text-muted-foreground font-medium">
                  <tr>
                    <th className="py-3 px-4">入選條件 / 原因標籤</th>
                    <th className="py-3 px-4 text-center">樣本狀態</th>
                    <th className="py-3 px-4 text-center">5D 樣本數</th>
                    <th className="py-3 px-4 text-right">5D 平均報酬</th>
                    <th className="py-3 px-4 text-right">5D 勝率</th>
                    <th className="py-3 px-4 text-center">20D 樣本數</th>
                    <th className="py-3 px-4 text-right">20D 平均報酬</th>
                    <th className="py-3 px-4 text-right">20D 勝率</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {conditionStatsQuery.data.map((cond, idx) => (
                    <tr key={idx} className="hover:bg-muted/20">
                      <td className="py-3 px-4 font-medium text-foreground">
                        <span className="inline-block bg-muted px-2 py-0.5 rounded text-xs">
                          {cond.condition_label}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        {cond.is_sample_sufficient ? (
                          <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium">
                            樣本充分
                          </span>
                        ) : (
                          <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium">
                            樣本不足 (&lt;5)
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-center text-muted-foreground">{cond.sample_count_5d}</td>
                      <td className={cn('py-3 px-4 text-right', getReturnColor(cond.avg_return_5d))}>
                        {formatPct(cond.avg_return_5d)}
                      </td>
                      <td className="py-3 px-4 text-right font-medium">
                        {cond.hit_rate_5d !== null ? `${cond.hit_rate_5d.toFixed(1)}%` : '-'}
                      </td>
                      <td className="py-3 px-4 text-center text-muted-foreground">{cond.sample_count_20d}</td>
                      <td className={cn('py-3 px-4 text-right', getReturnColor(cond.avg_return_20d))}>
                        {formatPct(cond.avg_return_20d)}
                      </td>
                      <td className="py-3 px-4 text-right font-medium">
                        {cond.hit_rate_20d !== null ? `${cond.hit_rate_20d.toFixed(1)}%` : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// 快照卡片元件
function SnapshotCard({
  item,
  onClick,
  onDelete,
  formatPct,
  getReturnColor,
}: {
  item: SnapshotListItem
  onClick: () => void
  onDelete: (e: React.MouseEvent) => void
  formatPct: (val: number | null | undefined) => string
  getReturnColor: (val: number | null | undefined) => string
}) {
  return (
    <div
      onClick={onClick}
      className="bg-card border border-border/60 hover:border-primary/50 transition-all rounded-xl p-4 cursor-pointer hover:shadow-sm flex flex-col justify-between group"
    >
      <div>
        <div className="flex items-start justify-between gap-2">
          <div>
            <h4 className="font-semibold text-sm text-foreground group-hover:text-primary transition-colors">
              {item.strategy_name}
            </h4>
            <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                {item.as_of_date}
              </span>
              <span>•</span>
              <span>{item.selected_count} 檔標的</span>
            </div>
          </div>
          <button
            onClick={onDelete}
            title="刪除快照"
            className="text-muted-foreground/60 hover:text-rose-500 p-1 rounded transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>

        {/* 評估摘要指標 */}
        <div className="grid grid-cols-2 gap-2 mt-4 pt-3 border-t border-border/40 text-xs">
          <div className="bg-muted/30 p-2 rounded-lg">
            <span className="text-muted-foreground block text-[11px]">5D 交易日表現</span>
            {item.h5d_evaluated_count > 0 ? (
              <div className="mt-0.5">
                <span className={cn('font-semibold', getReturnColor(item.h5d_avg_return_pct))}>
                  {formatPct(item.h5d_avg_return_pct)}
                </span>
                <span className="text-[10px] text-muted-foreground block">
                  超額 {formatPct(item.h5d_excess_pct)}
                </span>
              </div>
            ) : (
              <span className="text-[11px] text-amber-500 flex items-center gap-1 mt-0.5">
                <Clock className="h-3 w-3" /> 追蹤中
              </span>
            )}
          </div>

          <div className="bg-muted/30 p-2 rounded-lg">
            <span className="text-muted-foreground block text-[11px]">20D 交易日表現</span>
            {item.h20d_evaluated_count > 0 ? (
              <div className="mt-0.5">
                <span className={cn('font-semibold', getReturnColor(item.h20d_avg_return_pct))}>
                  {formatPct(item.h20d_avg_return_pct)}
                </span>
                <span className="text-[10px] text-muted-foreground block">
                  超額 {formatPct(item.h20d_excess_pct)}
                </span>
              </div>
            ) : (
              <span className="text-[11px] text-amber-500 flex items-center gap-1 mt-0.5">
                <Clock className="h-3 w-3" /> 追蹤中
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center justify-end text-[11px] text-primary font-medium">
        <span>查看評估明細</span>
        <ChevronRight className="h-3.5 w-3.5 ml-0.5" />
      </div>
    </div>
  )
}

// 快照詳情元件
function SnapshotDetailView({
  detail,
  isLoading,
  onBack,
  onDelete,
  formatPct,
  getReturnColor,
}: {
  detail: SnapshotReviewDetail | null | undefined
  isLoading: boolean
  onBack: () => void
  onDelete: (id: string) => void
  formatPct: (val: number | null | undefined) => string
  getReturnColor: (val: number | null | undefined) => string
}) {
  if (isLoading || !detail) {
    return <div className="text-center py-16 text-sm text-muted-foreground">載入快照詳情中...</div>
  }

  const { snapshot, evaluated_items } = detail

  return (
    <div className="space-y-6">
      {/* 頂部導航與標頭 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-card border border-border/60 rounded-xl p-4">
        <div>
          <button
            onClick={onBack}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-2 transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            返回快照清單
          </button>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-foreground">{snapshot.strategy_name} 快照詳情</h2>
            <span className="text-xs px-2 py-0.5 rounded bg-muted text-muted-foreground font-mono">
              基準日 {snapshot.as_of_date}
            </span>
          </div>
          {snapshot.market_context_summary && (
            <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
              選股時環境：{snapshot.market_context_summary}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          <CopyButton
            size="xs"
            label="複製回顧資料"
            getText={() => formatSelectionReviewCopy({
              strategy_id: snapshot.strategy_id,
              strategy_name: snapshot.strategy_name,
              snapshot_date: snapshot.as_of_date,
              market_context: snapshot.market_context_summary,
              strategy_stats: {
                avg_return_5d: detail.h5d_avg_return_pct,
                benchmark_5d: detail.h5d_bm_avg_return_pct,
                excess_return_5d: detail.h5d_avg_excess_pct,
                avg_return_20d: detail.h20d_avg_return_pct,
                benchmark_20d: detail.h20d_bm_avg_return_pct,
                excess_return_20d: detail.h20d_avg_excess_pct,
              },
              picks: evaluated_items.map(i => ({
                symbol: i.symbol,
                name: i.name,
                rank: i.rank,
                quant_score: i.quant_score,
                initial_price: i.entry_price,
                h5d_status: i.h5d_status,
                h5d_return_pct: i.h5d_return_pct,
                h20d_status: i.h20d_status,
                h20d_return_pct: i.h20d_return_pct,
                match_reasons: i.match_reasons,
              })),
            })}
          />
          <CopyButton
            size="xs"
            label="複製 AI 提示詞"
            successLabel="已複製"
            getText={() => formatSelectionReviewPrompt({
              strategy_id: snapshot.strategy_id,
              strategy_name: snapshot.strategy_name,
              snapshot_date: snapshot.as_of_date,
              market_context: snapshot.market_context_summary,
              strategy_stats: {
                avg_return_5d: detail.h5d_avg_return_pct,
                benchmark_5d: detail.h5d_bm_avg_return_pct,
                excess_return_5d: detail.h5d_avg_excess_pct,
                avg_return_20d: detail.h20d_avg_return_pct,
                benchmark_20d: detail.h20d_bm_avg_return_pct,
                excess_return_20d: detail.h20d_avg_excess_pct,
              },
              picks: evaluated_items.map(i => ({
                symbol: i.symbol,
                name: i.name,
                rank: i.rank,
                quant_score: i.quant_score,
                initial_price: i.entry_price,
                h5d_status: i.h5d_status,
                h5d_return_pct: i.h5d_return_pct,
                h20d_status: i.h20d_status,
                h20d_return_pct: i.h20d_return_pct,
                match_reasons: i.match_reasons,
              })),
            })}
          />
          <button
            onClick={() => onDelete(snapshot.snapshot_id)}
            className="inline-flex items-center gap-1.5 text-xs text-rose-500 hover:text-rose-600 border border-rose-500/20 hover:border-rose-500/40 px-3 py-1.5 rounded-lg transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
            刪除快照
          </button>
        </div>
      </div>

      {/* 成果聚合卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-card border border-border/60 rounded-xl p-3">
          <span className="text-xs text-muted-foreground">5D 平均報酬</span>
          <div className={cn('text-lg font-bold mt-1', getReturnColor(detail.h5d_avg_return_pct))}>
            {formatPct(detail.h5d_avg_return_pct)}
          </div>
          <span className="text-[11px] text-muted-foreground">
            基準(0050): {formatPct(detail.h5d_bm_avg_return_pct)}
          </span>
        </div>

        <div className="bg-card border border-border/60 rounded-xl p-3">
          <span className="text-xs text-muted-foreground">5D 平均超額</span>
          <div className={cn('text-lg font-bold mt-1', getReturnColor(detail.h5d_avg_excess_pct))}>
            {formatPct(detail.h5d_avg_excess_pct)}
          </div>
          <span className="text-[11px] text-muted-foreground">已評估 {detail.h5d_evaluated_count} 檔</span>
        </div>

        <div className="bg-card border border-border/60 rounded-xl p-3">
          <span className="text-xs text-muted-foreground">20D 平均報酬</span>
          <div className={cn('text-lg font-bold mt-1', getReturnColor(detail.h20d_avg_return_pct))}>
            {formatPct(detail.h20d_avg_return_pct)}
          </div>
          <span className="text-[11px] text-muted-foreground">
            基準(0050): {formatPct(detail.h20d_bm_avg_return_pct)}
          </span>
        </div>

        <div className="bg-card border border-border/60 rounded-xl p-3">
          <span className="text-xs text-muted-foreground">20D 平均超額</span>
          <div className={cn('text-lg font-bold mt-1', getReturnColor(detail.h20d_avg_excess_pct))}>
            {formatPct(detail.h20d_avg_excess_pct)}
          </div>
          <span className="text-[11px] text-muted-foreground">已評估 {detail.h20d_evaluated_count} 檔</span>
        </div>
      </div>

      {/* 標的明細表格 */}
      <div className="border border-border/60 rounded-xl bg-card overflow-hidden">
        <div className="p-3 bg-muted/30 border-b border-border/60 flex items-center justify-between text-xs">
          <span className="font-semibold text-foreground">
            當時選股標的明細 ({evaluated_items.length} 檔)
          </span>
          <span className="text-muted-foreground">
            基準代號：0050.TWSE (台灣50)
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-muted/40 border-b border-border/60 text-muted-foreground font-medium">
              <tr>
                <th className="py-2.5 px-3 text-center">排序</th>
                <th className="py-2.5 px-3">標的代碼/名稱</th>
                <th className="py-2.5 px-3">入選原因 / Quant</th>
                <th className="py-2.5 px-3 text-right">當時收盤</th>
                <th className="py-2.5 px-3 text-right">1D 報酬 (超額)</th>
                <th className="py-2.5 px-3 text-right">5D 報酬 (超額)</th>
                <th className="py-2.5 px-3 text-right">20D 報酬 (超額)</th>
                <th className="py-2.5 px-3">當時基本面/籌碼/事件摘要</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {evaluated_items.map((item) => (
                <tr key={item.symbol} className="hover:bg-muted/20">
                  <td className="py-3 px-3 text-center font-mono text-muted-foreground">{item.rank}</td>
                  <td className="py-3 px-3">
                    <Link
                      to={`/stocks/${item.symbol}`}
                      className="font-medium text-foreground hover:text-primary transition-colors flex items-center gap-1"
                    >
                      <span>{item.name}</span>
                      <span className="text-[11px] text-muted-foreground font-mono">({item.symbol})</span>
                      <ArrowUpRight className="h-3 w-3 opacity-60" />
                    </Link>
                  </td>
                  <td className="py-3 px-3">
                    <div className="flex flex-wrap gap-1">
                      {item.quant_score !== null && (
                        <span className="bg-primary/10 text-primary text-[10px] px-1.5 py-0.5 rounded font-mono">
                          QS: {item.quant_score.toFixed(1)}
                        </span>
                      )}
                      {item.match_reasons.map((r, i) => (
                        <span key={i} className="bg-muted text-muted-foreground text-[10px] px-1.5 py-0.5 rounded">
                          {r}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-3 px-3 text-right font-mono font-medium">
                    {item.entry_price.toFixed(2)}
                  </td>
                  {/* 1D Horizon */}
                  <td className="py-3 px-3 text-right">
                    <HorizonCell
                      status={item.h1d_status}
                      returnPct={item.h1d_return_pct}
                      excessPct={item.h1d_excess_pct}
                      formatPct={formatPct}
                      getReturnColor={getReturnColor}
                    />
                  </td>
                  {/* 5D Horizon */}
                  <td className="py-3 px-3 text-right">
                    <HorizonCell
                      status={item.h5d_status}
                      returnPct={item.h5d_return_pct}
                      excessPct={item.h5d_excess_pct}
                      formatPct={formatPct}
                      getReturnColor={getReturnColor}
                    />
                  </td>
                  {/* 20D Horizon */}
                  <td className="py-3 px-3 text-right">
                    <HorizonCell
                      status={item.h20d_status}
                      returnPct={item.h20d_return_pct}
                      excessPct={item.h20d_excess_pct}
                      formatPct={formatPct}
                      getReturnColor={getReturnColor}
                    />
                  </td>
                  {/* 快照當時事實摘要 */}
                  <td className="py-3 px-3 text-muted-foreground text-[11px] max-w-xs">
                    <div className="space-y-0.5">
                      {item.fundamental_summary && <div>• {item.fundamental_summary}</div>}
                      {item.chips_summary && <div>• {item.chips_summary}</div>}
                      {item.event_risk_summary && (
                        <div className="text-amber-500">• {item.event_risk_summary}</div>
                      )}
                      {!item.fundamental_summary && !item.chips_summary && !item.event_risk_summary && (
                        <span className="text-muted-foreground/50">-</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function HorizonCell({
  status,
  returnPct,
  excessPct,
  formatPct,
  getReturnColor,
}: {
  status: string
  returnPct: number | null | undefined
  excessPct: number | null | undefined
  formatPct: (val: number | null | undefined) => string
  getReturnColor: (val: number | null | undefined) => string
}) {
  if (status === 'pending') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-amber-500/90 font-normal">
        <Clock className="h-3 w-3" /> 追蹤中
      </span>
    )
  }
  if (status === 'unavailable') {
    return <span className="text-[11px] text-muted-foreground/70">缺資料</span>
  }
  return (
    <div>
      <span className={cn('font-semibold', getReturnColor(returnPct))}>{formatPct(returnPct)}</span>
      {excessPct !== null && excessPct !== undefined && (
        <span className="block text-[10px] text-muted-foreground">
          超額 {formatPct(excessPct)}
        </span>
      )}
    </div>
  )
}
