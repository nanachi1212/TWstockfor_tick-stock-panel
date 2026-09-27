import { useState } from 'react'
import { LockKeyhole, Play, RefreshCw, ShieldAlert } from 'lucide-react'
import type { ForwardCohortStats } from '@/lib/api'

/** UI view model for the backend's immutable forward-batch contract. */
export interface SelectionForwardPreviewView {
  dataDate: string | null
  strategyName?: string | null
  strategyReadiness?: string | null
  ruleVersion: string | null
  rankingBasis: string | null
  status: 'available' | 'unavailable'
  targetTradeDate: string | null
  targetTradeDateStatus: 'confirmed' | 'scheduled_unverified' | null
  lockAllowed?: boolean
  candidates: Array<{
    symbol: string
    name: string
    referencePrice: number | null
    knownRisks: string[]
    missingData: string[]
  }>
  knownRisks: string[]
  missingData: string[]
}

export interface SelectionForwardBatchReviewView {
  statistics: Record<'1D' | '5D' | '20D', {
    referenceCloseReturnPct: number | null
    referenceCloseEvaluableCount: number
    paperReturnPct: number | null
    benchmark0050ReturnPct: number | null
    benchmarkEvaluableCount: number
    excessReturnPct: number | null
    excessEvaluableCount: number
    evaluableCount: number
    trackingCount: number
    missingCount: number
  }>
  cohorts?: Record<'1D' | '5D' | '20D', {
    top10: ForwardCohortStats
    full_batch: ForwardCohortStats
  }>
  items: Array<{
    symbol: string
    name: string
    referencePrice: number | null
    paperEntryPrice: number | null
    paperEntryPriceStatus: 'completed' | 'tracking' | 'missing'
    entryReason?: string
    referenceCloseReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null; reason?: string }>
    paperEntryReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }>
    benchmarkReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }>
    excessReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }>
  }>
  metadata: {
    lockedAt: string | null
    targetTradeDate: string | null
    targetTradeDateStatus: 'confirmed' | 'scheduled_unverified' | null
    ruleVersion: string | null
    selectedCount: number
    priceAdjustment: string | null
    costAssumption: string | null
  }
}

interface Props {
  preview?: SelectionForwardPreviewView | null
  onDryRun?: () => void
  onRefreshData?: () => void
  onLockOfficialBatch?: () => void
  pending?: boolean
  batchReview?: SelectionForwardBatchReviewView | null
  lockError?: string | null
  dryRunError?: string | null
}

export function SelectionForwardPanel({ preview = null, onDryRun, onRefreshData, onLockOfficialBatch, pending = false, batchReview = null, lockError = null, dryRunError = null }: Props) {
  const [showTop20, setShowTop20] = useState(false)
  const [showBatchTop20, setShowBatchTop20] = useState(false)
  const candidates = preview?.candidates.slice(0, showTop20 ? 20 : 10) ?? []
  const batchItems = batchReview?.items.slice(0, showBatchTop20 ? 20 : 10) ?? []

  return (
    <section aria-labelledby="selection-forward-title" className="rounded-xl border border-primary/25 bg-card p-4 space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="selection-forward-title" className="text-base font-semibold">每日候選股前瞻測試</h2>
          <p className="mt-1 text-xs text-muted-foreground">{preview?.strategyName ?? '趨勢流動性 v1'}，先預覽候選與資料狀態，再鎖定正式測試名單。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={onDryRun} disabled={!onDryRun || pending} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50">
            <Play className="h-3.5 w-3.5" />乾跑預覽
          </button>
          <button type="button" onClick={onRefreshData} disabled={!onRefreshData || pending} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50">
            <RefreshCw className="h-3.5 w-3.5" />更新資料
          </button>
          <button type="button" onClick={onLockOfficialBatch} disabled={!preview || !onLockOfficialBatch || preview.status !== 'available' || !preview.lockAllowed || pending} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-50">
            <LockKeyhole className="h-3.5 w-3.5" />鎖定正式測試名單
          </button>
        </div>
      </div>

      {dryRunError && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">乾跑預覽失敗：{dryRunError}，可重新嘗試。</p>}

      {!preview ? (
        <div className="rounded-lg border border-dashed border-border/70 bg-muted/20 p-4 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">尚未載入候選資料</p>
          <p className="mt-1 text-xs">使用乾跑預覽讀取後端候選與資料覆蓋，確認後才可鎖定正式批次。</p>
        </div>
      ) : (
        <>
          {preview.status === 'unavailable' && (
            <p role="status" className="rounded-lg border border-border p-3 text-xs text-muted-foreground">目前無法提供正式候選資料。</p>
          )}
          {preview.candidates.length === 0 && <p role="status" className="rounded-lg border border-border p-3 text-xs text-muted-foreground">{preview.strategyReadiness === 'ready' ? '目前條件下沒有候選標的。' : '目前沒有可展示的候選標的，資料尚未準備完成。'}</p>}
          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-4">
            <div><dt className="text-muted-foreground">策略 readiness</dt><dd className="mt-0.5 font-medium">{preview.strategyReadiness ?? '資料不足'}</dd></div>
            <div><dt className="text-muted-foreground">資料日期</dt><dd className="mt-0.5 font-medium">{preview.dataDate ?? '資料不足'}</dd></div>
            <div><dt className="text-muted-foreground">規則版本</dt><dd className="mt-0.5 font-medium">{preview.ruleVersion ?? '資料不足'}</dd></div>
            <div><dt className="text-muted-foreground">排名依據</dt><dd className="mt-0.5 font-medium">{preview.rankingBasis ?? '資料不足'}</dd></div>
            <div><dt className="text-muted-foreground">預定進場日</dt><dd className="mt-0.5 font-medium">{preview.targetTradeDate ?? '待後端確認'}{preview.targetTradeDateStatus === 'scheduled_unverified' ? '（未確認）' : ''}</dd></div>
          </dl>
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium">候選 Top {candidates.length}</span>
            {preview.candidates.length > 10 && <button type="button" onClick={() => setShowTop20(value => !value)} className="text-primary hover:underline">{showTop20 ? '收合至 Top10' : '展開至 Top20'}</button>}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border/60">
            <table className="w-full min-w-[580px] text-left text-xs">
              <thead className="bg-muted/40 text-muted-foreground"><tr><th className="px-3 py-2">排名</th><th className="px-3 py-2">標的</th><th className="px-3 py-2 text-right">參考價</th><th className="px-3 py-2">已知風險</th><th className="px-3 py-2">資料不足</th></tr></thead>
              <tbody className="divide-y divide-border/40">{candidates.map((item, index) => <tr key={item.symbol}><td className="px-3 py-2 font-mono">{index + 1}</td><td className="px-3 py-2">{item.name} <span className="text-muted-foreground">({item.symbol})</span></td><td className="px-3 py-2 text-right">{item.referencePrice ?? '資料不足'}</td><td className="px-3 py-2">{item.knownRisks.length ? item.knownRisks.join('、') : '無已知風險標記'}</td><td className="px-3 py-2">{item.missingData.length ? item.missingData.join('、') : '無'}</td></tr>)}</tbody>
            </table>
          </div>
          {(preview.knownRisks.length > 0 || preview.missingData.length > 0) && (
            <div className="grid gap-2 text-xs sm:grid-cols-2">
              <p className="flex gap-2 rounded-lg bg-amber-500/5 p-3"><ShieldAlert className="h-4 w-4 shrink-0 text-amber-500" /><span><strong>已知風險：</strong>{preview.knownRisks.join('、') || '無'}</span></p>
              <p className="flex gap-2 rounded-lg bg-muted/40 p-3"><ShieldAlert className="h-4 w-4 shrink-0 text-muted-foreground" /><span><strong>資料不足：</strong>{preview.missingData.join('、') || '無'}</span></p>
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">正式鎖定時點與進場日期以後端核驗為準，乾跑預覽不保證可以鎖定。候選依後端排序回傳，前端不重排。</p>
          {lockError && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">正式鎖定遭後端拒絕：{lockError}</p>}
        </>
      )}
      {batchReview && (
        <div className="space-y-3 border-t border-border/60 pt-4">
          <h3 className="text-sm font-semibold">正式批次績效，採用後端評估結果</h3>
          <p className="text-xs text-muted-foreground">整批統計共 {batchReview.metadata.selectedCount} 檔，顯示 Top {batchItems.length} 不會改變統計範圍。{batchReview.metadata.lockedAt ? `鎖定時間 ${batchReview.metadata.lockedAt}。` : ''}預定進場 {batchReview.metadata.targetTradeDate ?? '資料不足'}{batchReview.metadata.targetTradeDateStatus === 'scheduled_unverified' ? '（交易日未確認）' : ''}。</p>
          <p className="text-xs text-muted-foreground">實驗性選股，紙上開盤價不保證成交，未扣成本與滑價。報酬調整口徑：{batchReview.metadata.priceAdjustment ?? '後端未提供'}，不代表股利總報酬。{batchReview.metadata.costAssumption ? ` ${batchReview.metadata.costAssumption}` : ''}</p>
          <div className="grid gap-2 md:grid-cols-3">
            {(['1D', '5D', '20D'] as const).map(horizon => {
              const result = batchReview.statistics[horizon]
              return (
                <section key={horizon} className="rounded-lg border border-border/60 bg-muted/20 p-3 text-xs">
                  <h4 className="font-semibold">{horizon}</h4>
                  <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                    <dt className="text-muted-foreground">參考收盤漲跌</dt><dd className="text-right">{formatPct(result.referenceCloseReturnPct)}（{result.referenceCloseEvaluableCount} 檔）</dd>
                    <dt className="text-muted-foreground">紙上進場報酬</dt><dd className="text-right">{formatPct(result.paperReturnPct)}</dd>
                    <dt className="text-muted-foreground">0050 同期報酬</dt><dd className="text-right">{formatPct(result.benchmark0050ReturnPct)}（{result.benchmarkEvaluableCount} 檔）</dd>
                    <dt className="text-muted-foreground">超額報酬</dt><dd className="text-right">{formatPct(result.excessReturnPct)}（{result.excessEvaluableCount} 檔）</dd>
                    <dt className="text-muted-foreground">可評估筆數</dt><dd className="text-right">{result.evaluableCount}</dd>
                    <dt className="text-muted-foreground">追蹤中</dt><dd className="text-right">{result.trackingCount}</dd>
                    <dt className="text-muted-foreground">缺資料</dt><dd className="text-right">{result.missingCount}</dd>
                  </dl>
                </section>
              )
            })}
          </div>
          {batchReview.cohorts && (
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">本批次 cohort 統計，依 immutable rank 計算</h4>
              <div className="grid gap-2 lg:grid-cols-3">
                {(['1D', '5D', '20D'] as const).map(horizon => (
                  <section key={horizon} className="rounded-lg border border-border/60 p-3 text-xs">
                    <h5 className="font-semibold">{horizon}</h5>
                    {(['top10', 'full_batch'] as const).map(cohort => {
                      const summary = batchReview.cohorts?.[horizon]?.[cohort]
                      return summary ? <div key={cohort} className="mt-2 border-t border-border/40 pt-2 first:border-t-0 first:pt-0">
                        <p className="font-medium">{cohort === 'top10' ? 'Top10' : 'Top20／整批'}</p>
                        <dl className="mt-1 grid grid-cols-2 gap-y-1">
                          <dt className="text-muted-foreground">命中率／N</dt><dd className="text-right">{formatRate(summary.hit_rate)}／{summary.evaluable_count}</dd>
                          <dt className="text-muted-foreground">平均／中位數報酬</dt><dd className="text-right">{formatPct(summary.average_return_pct)}／{formatPct(summary.median_return_pct)}</dd>
                          <dt className="text-muted-foreground">平均 0050／超額</dt><dd className="text-right">{formatPct(summary.average_benchmark_return_pct)}／{formatPct(summary.average_excess_return_pct)}</dd>
                          <dt className="text-muted-foreground">Beat 0050</dt><dd className="text-right">{formatRate(summary.beat_benchmark_rate)}（{summary.beat_benchmark_count}/{summary.excess_evaluable_count}）</dd>
                          <dt className="text-muted-foreground">待追蹤／不可評估</dt><dd className="text-right">{summary.pending_count}／{summary.unavailable_count}</dd>
                        </dl>
                      </div> : null
                    })}
                  </section>
                ))}
              </div>
            </div>
          )}
          {batchReview.items.length > 10 && <button type="button" onClick={() => setShowBatchTop20(value => !value)} className="text-xs text-primary hover:underline">{showBatchTop20 ? '收合至 Top10' : '展開至 Top20'}，只改變明細顯示</button>}
          <div className="overflow-x-auto rounded-lg border border-border/60">
            <table className="w-full min-w-[980px] text-left text-xs">
              <thead className="bg-muted/40 text-muted-foreground"><tr><th rowSpan={2} className="px-3 py-2">標的</th><th rowSpan={2} className="px-3 py-2 text-right">鎖定參考收盤</th><th rowSpan={2} className="px-3 py-2 text-right">紙上進場價</th>{(['1D', '5D', '20D'] as const).map(h => <th key={h} colSpan={4} className="px-3 py-2 text-center">{h}</th>)}</tr><tr>{(['1D', '5D', '20D'] as const).flatMap(h => [<th key={`${h}-reference`} className="px-3 py-1 text-right">參考收盤漲跌</th>, <th key={`${h}-return`} className="px-3 py-1 text-right">紙上報酬</th>, <th key={`${h}-benchmark`} className="px-3 py-1 text-right">0050</th>, <th key={`${h}-excess`} className="px-3 py-1 text-right">超額</th>])}</tr></thead>
              <tbody className="divide-y divide-border/40">{batchItems.map(item => <tr key={item.symbol}><td className="px-3 py-2">{item.name} ({item.symbol})</td><td className="px-3 py-2 text-right">{item.referencePrice ?? '資料不足'}</td><td className="px-3 py-2 text-right">{formatPrice(item.paperEntryPrice, item.paperEntryPriceStatus, item.entryReason)}</td>{(['1D', '5D', '20D'] as const).flatMap(horizon => [<td key={`${horizon}-reference`} className="px-3 py-2 text-right">{formatEvaluation(item.referenceCloseReturns[horizon])}</td>, <td key={`${horizon}-return`} className="px-3 py-2 text-right">{formatEvaluation(item.paperEntryReturns[horizon])}</td>, <td key={`${horizon}-benchmark`} className="px-3 py-2 text-right">{formatEvaluation(item.benchmarkReturns[horizon])}</td>, <td key={`${horizon}-excess`} className="px-3 py-2 text-right">{formatEvaluation(item.excessReturns[horizon])}</td>])}</tr>)}</tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}

function formatPct(value: number | null) {
  if (value === null) return '尚無可評估結果'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatRate(value: number | null) {
  return value === null ? '尚無樣本' : `${value.toFixed(1)}%`
}

function formatEvaluation(value: { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null; reason?: string }) {
  const text = value.status === 'tracking' ? '追蹤中' : value.status === 'missing' || value.returnPct === null ? '不可評估' : formatPct(value.returnPct)
  return value.reason ? <span title={value.reason}>{text} · {value.reason}</span> : text
}

function formatPrice(value: number | null, status: 'completed' | 'tracking' | 'missing', reason?: string) {
  const text = status === 'completed' && value !== null ? value.toFixed(2) : status === 'tracking' ? '追蹤中' : '不可評估'
  return reason ? <span title={reason}>{text} · {reason}</span> : text
}
