import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, GitCompareArrows, Loader2, Search } from 'lucide-react'
import { api, type TaiwanAIResearchHistoryRecord } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function recordsFrom(value: unknown): TaiwanAIResearchHistoryRecord[] {
  const raw = Array.isArray(value)
    ? value
    : value && typeof value === 'object'
      ? ((value as { items?: unknown }).items ?? (value as { records?: unknown }).records)
      : []
  return Array.isArray(raw)
    ? raw.filter((item): item is TaiwanAIResearchHistoryRecord => Boolean(item && typeof item === 'object' && typeof (item as { id?: unknown }).id === 'string'))
    : []
}

function printable(value: unknown) {
  if (value == null) return '未提供'
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) } catch { return '資料格式無法顯示' }
}

export function Research() {
  const [params, setParams] = useSearchParams()
  const symbol = params.get('symbol') ?? ''
  const from = params.get('from') ?? ''
  const to = params.get('to') ?? ''
  const selectedId = params.get('id') ?? ''
  const firstCompareId = params.get('compare') ?? ''
  const secondCompareId = params.get('other') ?? ''
  const filters = useMemo(() => ({ symbol: symbol || undefined, from: from || undefined, to: to || undefined, purpose: 'stock', limit: 100 }), [symbol, from, to])
  const historyQuery = useQuery({
    queryKey: QK.taiwanAIResearchHistory(filters),
    queryFn: () => api.taiwanAIResearchHistory(filters),
    staleTime: 30_000,
  })
  const records = useMemo(() => recordsFrom(historyQuery.data), [historyQuery.data])
  const selected = records.find(item => item.id === selectedId) ?? (selectedId ? undefined : records[0])
  const detailQuery = useQuery({
    queryKey: QK.taiwanAIResearchHistoryDetail(selected?.id ?? ''),
    queryFn: () => api.taiwanAIResearchHistoryDetail(selected!.id),
    enabled: Boolean(selected?.id),
  })
  const selectedDetail = detailQuery.data ?? selected
  const compareQuery = useQuery({
    queryKey: QK.taiwanAIResearchHistoryCompare(firstCompareId, secondCompareId),
    queryFn: () => api.taiwanAIResearchHistoryCompare(firstCompareId, secondCompareId),
    enabled: Boolean(firstCompareId && secondCompareId && firstCompareId !== secondCompareId),
  })
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  const setFilters = (updates: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(updates).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
    setParams(next)
  }
  const selectRecord = (id: string) => setFilter('id', id)

  return (
    <main className="mx-auto w-full max-w-7xl space-y-4 p-4 text-foreground">
      <header>
        <h1 className="text-lg font-bold">研究歷史</h1>
        <p className="mt-1 text-xs text-muted">跨股票回看已保存的 AI 研究；比較只讀取 API 結果，不會重新呼叫 AI。</p>
      </header>
      <section className="flex flex-wrap items-end gap-2 rounded-xl border border-border bg-surface p-3" aria-label="研究歷史篩選">
        <label className="text-xs text-muted">股票代號<input aria-label="股票代號" value={symbol} onChange={e => setFilter('symbol', e.target.value.toUpperCase())} placeholder="例如 2330.TWSE" className="mt-1 block rounded-lg border border-border bg-base px-2 py-1.5 font-mono text-xs text-foreground" /></label>
        <label className="text-xs text-muted">起始日期<input type="date" aria-label="起始日期" value={from} onChange={e => setFilter('from', e.target.value)} className="mt-1 block rounded-lg border border-border bg-base px-2 py-1.5 text-xs text-foreground" /></label>
        <label className="text-xs text-muted">結束日期<input type="date" aria-label="結束日期" value={to} onChange={e => setFilter('to', e.target.value)} className="mt-1 block rounded-lg border border-border bg-base px-2 py-1.5 text-xs text-foreground" /></label>
        <Search className="mb-2 h-4 w-4 text-muted" aria-hidden="true" />
      </section>
      {historyQuery.isLoading && <div role="status" className="flex items-center gap-2 rounded-xl border border-border bg-surface p-6 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin" />正在載入研究歷史…</div>}
      {historyQuery.isError && <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-xs text-rose-300"><AlertTriangle className="mr-2 inline h-4 w-4" />研究歷史目前不可用，請稍後重試。</div>}
      {!historyQuery.isLoading && !historyQuery.isError && records.length === 0 && <div className="rounded-xl border border-dashed border-border bg-surface p-8 text-center text-xs text-muted">沒有符合篩選條件的研究紀錄。</div>}
      {records.length > 0 && <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <section className="rounded-xl border border-border bg-surface p-3" aria-label="研究歷史清單">
          <h2 className="text-sm font-semibold">紀錄（{records.length}）</h2>
          <div className="mt-2 space-y-1.5">
            {records.map(record => (
              <div key={record.id} className={`rounded-lg border p-2 text-xs ${selected?.id === record.id ? 'border-accent bg-accent/5' : 'border-border/60'}`}>
                <button type="button" onClick={() => selectRecord(record.id)} className="w-full text-left">
                  <div className="flex items-center justify-between gap-2"><span className="font-mono font-semibold">{record.symbol ?? '標的未提供'}</span><span className="text-[10px] text-muted">{record.saved_at ?? record.generated_at ?? '時間未提供'}</span></div>
                  <div className="mt-1 text-[10px] text-muted">{record.provider ?? 'provider 未提供'} · {record.model ?? 'model 未提供'} · {record.prompt_version ?? '提示詞版本未提供'}</div>
                </button>
                <div className="mt-1 flex flex-wrap gap-1">
                  <button type="button" onClick={() => setFilters({ compare: record.id, ...(firstCompareId === record.id ? { other: '' } : {}) })} className="inline-flex items-center gap-1 text-[10px] text-purple-300 hover:underline"><GitCompareArrows className="h-3 w-3" />選為比較 A</button>
                  <button type="button" onClick={() => setFilters({ other: record.id, ...(secondCompareId === record.id ? { compare: '' } : {}) })} className="inline-flex items-center gap-1 text-[10px] text-purple-300 hover:underline"><GitCompareArrows className="h-3 w-3" />選為比較 B</button>
                </div>
              </div>
            ))}
          </div>
        </section>
        <section className="space-y-4" aria-label="研究詳情與比較">
          {selectedDetail && <article className="rounded-xl border border-border bg-surface p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold">研究詳情：{selectedDetail.symbol ?? '未提供標的'}</h2><span className="font-mono text-[10px] text-muted">{selectedDetail.id}</span></div>
            {detailQuery.isFetching && <p role="status" className="mt-1 text-[10px] text-muted">正在讀取完整紀錄…</p>}
            <dl className="mt-3 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4"><div><dt className="text-muted">Provider</dt><dd>{selectedDetail.provider ?? '未提供'}</dd></div><div><dt className="text-muted">Model</dt><dd>{selectedDetail.model ?? '未提供'}</dd></div><div><dt className="text-muted">提示詞版本</dt><dd>{selectedDetail.prompt_version ?? printable(selectedDetail.prompt_versions)}</dd></div><div><dt className="text-muted">保存時間</dt><dd>{selectedDetail.saved_at ?? selectedDetail.generated_at ?? '未提供'}</dd></div></dl>
            <div className="mt-3 rounded-lg border border-border/50 bg-base/30 p-3"><h3 className="text-xs font-semibold">報告</h3><pre className="mt-2 max-h-[28rem] overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-secondary">{printable(selectedDetail.report ?? selectedDetail.response ?? selectedDetail)}</pre></div>
          </article>}
          {(firstCompareId || secondCompareId) && <article className="rounded-xl border border-purple-500/30 bg-surface p-4"><h2 className="flex items-center gap-2 text-sm font-semibold"><GitCompareArrows className="h-4 w-4 text-purple-300" />研究比較</h2>{!firstCompareId || !secondCompareId ? <p className="mt-2 text-xs text-muted">請在左側各選一筆比較 A 與比較 B。</p> : compareQuery.isLoading ? <p role="status" className="mt-2 text-xs text-muted">正在取得確定性比較…</p> : compareQuery.isError ? <p role="alert" className="mt-2 text-xs text-muted">比較資料目前不可用。</p> : compareQuery.data ? <div className="mt-3 grid gap-3 md:grid-cols-3"><div className="rounded-lg border border-border/50 p-3"><h3 className="text-xs font-semibold">資料變化</h3><pre className="mt-2 whitespace-pre-wrap text-[10px] text-secondary">{printable(compareQuery.data.data_changes ?? compareQuery.data.current)}</pre></div><div className="rounded-lg border border-border/50 p-3"><h3 className="text-xs font-semibold">模型／提示詞變化</h3><pre className="mt-2 whitespace-pre-wrap text-[10px] text-secondary">{printable(compareQuery.data.model_prompt_changes)}</pre></div><div className="rounded-lg border border-border/50 p-3"><h3 className="text-xs font-semibold">解讀變化</h3><pre className="mt-2 whitespace-pre-wrap text-[10px] text-secondary">{printable(compareQuery.data.interpretation_changes ?? compareQuery.data.other)}</pre></div></div> : null}</article>}
        </section>
      </div>}
    </main>
  )
}

export default Research
