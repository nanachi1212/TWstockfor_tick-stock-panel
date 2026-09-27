import { useState } from 'react'
import { Clock, LockKeyhole, Play, ShieldAlert } from 'lucide-react'

/** UI view model only. Bind this to the backend contract when the core PR lands. */
export interface SelectionForwardPreviewView {
  dataDate: string | null
  ruleVersion: string | null
  rankingBasis: string | null
  status: 'pre_open' | 'market_open' | 'unavailable'
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
    paperReturnPct: number | null
    benchmark0050ReturnPct: number | null
    excessReturnPct: number | null
    evaluableCount: number
    trackingCount: number
    missingCount: number
  }>
  items: Array<{
    symbol: string
    name: string
    referencePrice: number | null
    paperEntryPrice: number | null
    paperEntryReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }>
    benchmarkReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }>
    excessReturns: Record<'1D' | '5D' | '20D', { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }>
  }>
}

interface Props {
  preview?: SelectionForwardPreviewView | null
  onDryRun?: () => void
  onLockOfficialBatch?: () => void
  pending?: boolean
  batchReview?: SelectionForwardBatchReviewView | null
}

export function SelectionForwardPanel({ preview = null, onDryRun, onLockOfficialBatch, pending = false, batchReview = null }: Props) {
  const [showTop20, setShowTop20] = useState(false)
  const candidates = preview?.candidates.slice(0, showTop20 ? 20 : 10) ?? []
  const preOpen = preview?.status === 'pre_open'

  return (
    <section aria-labelledby="selection-forward-title" className="rounded-xl border border-primary/25 bg-card p-4 space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="selection-forward-title" className="text-base font-semibold">每日候選股前瞻測試</h2>
          <p className="mt-1 text-xs text-muted-foreground">趨勢流動性 v1，先預覽候選與資料狀態，再鎖定正式測試名單。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={onDryRun} disabled={!onDryRun || pending} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50">
            <Play className="h-3.5 w-3.5" />乾跑預覽
          </button>
          <button type="button" onClick={onLockOfficialBatch} disabled={!preview || !onLockOfficialBatch || !preOpen || !preview.lockAllowed || pending} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-50">
            <LockKeyhole className="h-3.5 w-3.5" />鎖定正式測試名單
          </button>
        </div>
      </div>

      {!preview ? (
        <div className="rounded-lg border border-dashed border-border/70 bg-muted/20 p-4 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">尚未載入候選資料</p>
          <p className="mt-1 text-xs">使用乾跑預覽讀取後端候選與資料覆蓋，確認後才可鎖定正式批次。</p>
        </div>
      ) : (
        <>
          {preview.status === 'market_open' && (
            <p role="status" className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-300">
              <Clock className="mt-0.5 h-4 w-4 shrink-0" />已開盤，這份預覽不能建立或冒充當日盤前正式批次。
            </p>
          )}
          {preview.status === 'unavailable' && (
            <p role="status" className="rounded-lg border border-border p-3 text-xs text-muted-foreground">目前無法提供正式候選資料。</p>
          )}
          {preview.candidates.length === 0 && <p role="status" className="rounded-lg border border-border p-3 text-xs text-muted-foreground">目前沒有可展示的候選標的，無法鎖定名單。</p>}
          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
            <div><dt className="text-muted-foreground">資料日期</dt><dd className="mt-0.5 font-medium">{preview.dataDate ?? '資料不足'}</dd></div>
            <div><dt className="text-muted-foreground">規則版本</dt><dd className="mt-0.5 font-medium">{preview.ruleVersion ?? '資料不足'}</dd></div>
            <div><dt className="text-muted-foreground">排名依據</dt><dd className="mt-0.5 font-medium">{preview.rankingBasis ?? '資料不足'}</dd></div>
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
          <p className="text-[11px] text-muted-foreground">候選取後端回傳的前 20 名；規則版本與排序依據以本次篩選契約標示。覆蓋不足時以資料狀態呈現，正式鎖定仍由後端驗證。</p>
        </>
      )}
      {batchReview && (
        <div className="space-y-3 border-t border-border/60 pt-4">
          <h3 className="text-sm font-semibold">正式批次績效，採用後端評估結果</h3>
          <div className="grid gap-2 md:grid-cols-3">
            {(['1D', '5D', '20D'] as const).map(horizon => {
              const result = batchReview.statistics[horizon]
              return (
                <section key={horizon} className="rounded-lg border border-border/60 bg-muted/20 p-3 text-xs">
                  <h4 className="font-semibold">{horizon}</h4>
                  <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                    <dt className="text-muted-foreground">紙上進場報酬</dt><dd className="text-right">{formatPct(result.paperReturnPct)}</dd>
                    <dt className="text-muted-foreground">0050 同期報酬</dt><dd className="text-right">{formatPct(result.benchmark0050ReturnPct)}</dd>
                    <dt className="text-muted-foreground">超額報酬</dt><dd className="text-right">{formatPct(result.excessReturnPct)}</dd>
                    <dt className="text-muted-foreground">可評估筆數</dt><dd className="text-right">{result.evaluableCount}</dd>
                    <dt className="text-muted-foreground">追蹤中</dt><dd className="text-right">{result.trackingCount}</dd>
                    <dt className="text-muted-foreground">缺資料</dt><dd className="text-right">{result.missingCount}</dd>
                  </dl>
                </section>
              )
            })}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border/60">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="bg-muted/40 text-muted-foreground"><tr><th rowSpan={2} className="px-3 py-2">標的</th><th rowSpan={2} className="px-3 py-2 text-right">鎖定參考價</th><th rowSpan={2} className="px-3 py-2 text-right">紙上進場價</th>{(['1D', '5D', '20D'] as const).map(h => <th key={h} colSpan={3} className="px-3 py-2 text-center">{h}</th>)}</tr><tr>{(['1D', '5D', '20D'] as const).flatMap(h => [<th key={`${h}-return`} className="px-3 py-1 text-right">紙上</th>, <th key={`${h}-benchmark`} className="px-3 py-1 text-right">0050</th>, <th key={`${h}-excess`} className="px-3 py-1 text-right">超額</th>])}</tr></thead>
              <tbody className="divide-y divide-border/40">{batchReview.items.map(item => <tr key={item.symbol}><td className="px-3 py-2">{item.name} ({item.symbol})</td><td className="px-3 py-2 text-right">{item.referencePrice ?? '資料不足'}</td><td className="px-3 py-2 text-right">{item.paperEntryPrice ?? '追蹤中'}</td>{(['1D', '5D', '20D'] as const).flatMap(horizon => [<td key={`${horizon}-return`} className="px-3 py-2 text-right">{formatEvaluation(item.paperEntryReturns[horizon])}</td>, <td key={`${horizon}-benchmark`} className="px-3 py-2 text-right">{formatEvaluation(item.benchmarkReturns[horizon])}</td>, <td key={`${horizon}-excess`} className="px-3 py-2 text-right">{formatEvaluation(item.excessReturns[horizon])}</td>])}</tr>)}</tbody>
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

function formatEvaluation(value: { status: 'completed' | 'tracking' | 'missing'; returnPct: number | null }) {
  if (value.status === 'tracking') return '追蹤中'
  if (value.status === 'missing' || value.returnPct === null) return '缺資料'
  return formatPct(value.returnPct)
}
