import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Loader2, RefreshCw, Star, TrendingUp } from 'lucide-react'
import { api, type TaiwanLiveQuantSignal } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatPct(value: number | null, digits = 1) {
  return value == null ? '—' : `${(value * 100).toFixed(digits)}%`
}

function reasons(signal: TaiwanLiveQuantSignal): string[] {
  const labels = [
    ['momentum_5d', '短期動能排名前段'],
    ['momentum_20d', '中期動能排名前段'],
    ['momentum_60d', '長期動能排名前段'],
  ] as const
  return labels
    .filter(([feature]) => finite(signal.feature_percentiles[feature]) != null && signal.feature_percentiles[feature] >= 0.7)
    .slice(0, 3)
    .map(([, label]) => label)
}

export function TodaySelection({ symbol }: { symbol?: string }) {
  const qc = useQueryClient()
  const models = useQuery({
    queryKey: QK.taiwanQuantLiveModels,
    queryFn: api.taiwanQuantLiveModels,
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
  const runs = useQuery({
    queryKey: QK.taiwanQuantLiveRuns,
    queryFn: () => api.taiwanQuantLiveRuns(30),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
  const modelKey = models.data?.configured_model.model_key
  const latest = runs.data?.runs.find(run => run.model_key === modelKey)
  const expectedRun = Boolean(models.data?.current_run_valid && latest?.session === models.data.expected_session)
  const run = useQuery({
    queryKey: QK.taiwanQuantLiveRun(modelKey ?? '', latest?.session ?? ''),
    queryFn: () => api.taiwanQuantLiveRun(modelKey!, latest!.session),
    enabled: Boolean(modelKey && latest && expectedRun),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
  const runData = run.data
  const validRun = expectedRun && runData && runData.session === models.data?.expected_session && runData.audit_status === 'ok' ? runData : null
  const signals = validRun?.snapshot.signals ?? []
  const symbols = signals.map(item => item.symbol)
  const quotes = useQuery({
    queryKey: QK.taiwanQuotes(symbols.join(',')),
    queryFn: () => api.taiwanQuotes(symbols),
    enabled: !symbol && symbols.length > 0,
    staleTime: 15_000,
    refetchInterval: 60_000,
  })
  const quoteMap = useMemo(() => new Map((quotes.data?.quotes ?? []).map(item => [item.symbol, item])), [quotes.data])
  const featureMap = useMemo(() => new Map((run.data?.snapshot.features ?? []).map(item => [item.symbol, item])), [run.data])
  const watchlist = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    enabled: !symbol,
    staleTime: 60_000,
  })
  const watchlistSymbols = new Set((watchlist.data?.symbols ?? []).map(item => item.symbol))
  const add = useMutation({
    mutationFn: (target: string) => api.watchlistAdd(target),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: QK.watchlistEnriched() })
    },
  })
  const selected = symbol ? signals.find(item => item.symbol === symbol) : null
  const loading = models.isLoading || runs.isLoading || (!models.isError && !runs.isError && expectedRun && run.isLoading)
  const error = models.isError || runs.isError || run.isError

  if (symbol) {
    if (loading) return <div className="text-xs text-muted" role="status">正在載入 live Quant 排名…</div>
    if (error) return <div className="text-xs text-muted" role="alert">Live Quant 摘要目前無法讀取。</div>
    if (!expectedRun || !validRun) return <div className="text-xs text-muted" role="status">目前沒有通過交易日新鮮度與稽核檢查的 Live Quant 排名。</div>
    if (!selected) return null
    const features = featureMap.get(symbol)
    return (
      <section aria-label="Live Quant 摘要" className="rounded-xl border border-accent/20 bg-accent/5 p-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <strong className="text-sm">Live Quant 摘要</strong>
          <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">即時排名，非 Primary OOS</span>
          <span className="ml-auto text-[10px] text-muted">最近完成排名 {validRun.snapshot.signal_session}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span>排名 #{selected.rank}</span><span>分數 {formatPct(selected.score, 1)}</span>
          {Object.entries(selected.feature_percentiles).map(([key, value]) => (
            <span key={key} className="text-secondary">{key.replace('momentum_', '')}D 動能 {formatPct(value)}</span>
          ))}
          <span className="text-secondary">20D 波動 {formatPct(finite(features?.volatility_20d))}</span>
          <span className="text-secondary">20D 日均成交額 {finite(features?.adv20_twd) == null ? '—' : `${(finite(features?.adv20_twd)! / 1_000_000).toFixed(0)} 百萬`}</span>
          <span className="text-secondary">相對量 {finite(features?.relative_volume)?.toFixed(2) ?? '—'}</span>
        </div>
        <p className="mt-1.5 text-[11px] text-secondary">{reasons(selected).join('、') || '入選條件符合既有動能規則'}。說明取自凍結快照因子百分位，未重新計分。</p>
      </section>
    )
  }

  return (
    <section aria-label="今日 Quant 選股" className="mb-1.5 rounded-card border border-border bg-surface/90 p-3 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <TrendingUp className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-semibold">今日 Quant 選股</h2>
        <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{validRun ? 'Live current' : 'Live 排名待驗證'}</span>
        <span className="text-[10px] text-muted">與 Primary OOS 歷史驗證分開</span>
        {validRun && <span className="ml-auto text-[10px] text-muted">最近完成排名 {validRun.snapshot.signal_session}</span>}
      </div>

      {loading ? <div className="flex items-center gap-2 py-5 text-xs text-muted" role="status"><Loader2 className="h-4 w-4 animate-spin" />讀取最新已完成的 live 排名…</div> : null}
      {error ? <div className="flex items-center justify-between gap-2 py-2 text-xs text-muted" role="alert"><span>Live Quant 或行情資料目前無法完整讀取，請稍後重試。</span><button type="button" onClick={() => { void models.refetch(); void runs.refetch(); if (modelKey && latest) void run.refetch(); if (symbols.length) void quotes.refetch() }} className="inline-flex items-center gap-1"><RefreshCw className="h-3 w-3" />重試</button></div> : null}
      {!loading && !error && !latest ? <p className="py-4 text-xs text-muted">目前尚無已完成的 live 排名；資料不足或非交易日不會以歷史 OOS 代替。</p> : null}
      {!loading && !error && latest && !expectedRun ? <p className="py-4 text-xs text-muted">目前沒有符合預期交易日且通過稽核的新鮮 Live 排名；不顯示舊快照或歷史 OOS。</p> : null}
      {!loading && !error && expectedRun && !validRun ? <p className="py-4 text-xs text-muted">目前 Live 快照未通過稽核檢查，不顯示排名。</p> : null}
      {!loading && !error && expectedRun && latest && validRun && signals.length === 0 ? <p className="py-4 text-xs text-muted">{latest.session} 已完成 live 排名，但沒有符合既有選取門檻的標的。</p> : null}
      {!loading && !error && validRun && signals.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="text-[10px] text-muted"><tr><th className="px-2 py-1">排名 / 標的</th><th className="px-2 py-1">現價 / 漲跌</th><th className="px-2 py-1">Quant 分數</th><th className="px-2 py-1">動能 5 / 20 / 60D</th><th className="px-2 py-1">波動 / 流動性 / 相對量</th><th className="px-2 py-1">入選原因</th><th className="px-2 py-1">自選</th></tr></thead>
            <tbody>{signals.slice(0, 10).map(item => {
              const quote = quoteMap.get(item.symbol)
              const features = featureMap.get(item.symbol)
              const adv20 = finite(features?.adv20_twd)
              const current = quote?.last_price ?? item.reference_close
              const percent = quote?.last_price == null ? null : finite(quote.change_pct)
              const quoteLabel = quote == null ? quotes.isLoading ? '行情載入中，顯示最近收盤' : '最近收盤' : quote.last_price == null ?
                `無有效報價，顯示排名參考收盤 ${quote.trade_date}` : !quote.source_meta ?
                `行情狀態未知 ${quote.trade_date}` : quote.source_meta.is_stale ?
                  `行情偏舊 ${quote.trade_date}` : `${quote.source_meta.is_realtime ? '即時行情' : '收盤行情'} ${quote.trade_date}`
              const momentum = ['momentum_5d', 'momentum_20d', 'momentum_60d'].map(key => item.feature_percentiles[key])
              const reason = reasons(item)
              return <tr key={item.symbol} className="border-t border-border/60 hover:bg-elevated/40">
                <td className="px-2 py-2"><Link to={`/stocks/${encodeURIComponent(item.symbol)}`} className="font-medium text-foreground hover:text-accent"><span className="mr-1 text-muted">#{item.rank}</span>{quote?.name || item.symbol}<span className="ml-1 text-[10px] text-muted">{item.symbol}</span></Link></td>
                <td className="px-2 py-2 font-mono">{current.toFixed(2)}<span className={`ml-1 ${percent == null || percent === 0 ? 'text-muted' : percent > 0 ? 'text-bull' : 'text-bear'}`}>{percent == null ? '' : `${percent.toFixed(2)}%`}</span><small className="ml-1 font-sans text-muted">{quoteLabel}</small></td>
                <td className="px-2 py-2 font-mono">{formatPct(item.score)}</td>
                <td className="px-2 py-2 font-mono">{momentum.map(value => formatPct(finite(value))).join(' / ')}</td>
                <td className="px-2 py-2 text-secondary">{formatPct(finite(features?.volatility_20d))} / {adv20 == null ? '—' : `${(adv20 / 1_000_000).toFixed(0)}M`} / {finite(features?.relative_volume)?.toFixed(2) ?? '—'}</td>
                <td className="max-w-[220px] px-2 py-2 text-secondary">{reason.join('、') || '既有動能選取條件'}</td>
                <td className="px-2 py-2">{watchlistSymbols.has(item.symbol) ? <span className="text-[10px] text-muted">已加入</span> : <button type="button" aria-label={`將 ${item.symbol} 加入自選`} onClick={() => add.mutate(item.symbol)} disabled={add.isPending || watchlist.isError} className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-1 text-[10px] hover:text-accent disabled:opacity-50"><Star className="h-3 w-3" />{watchlist.isError ? '自選不可用' : '加入'}</button>}</td>
              </tr>
            })}</tbody>
          </table>
          {quotes.isError ? <p className="mt-1 text-[10px] text-muted">即時行情讀取失敗，仍顯示排名快照參考收盤價；漲跌暫不可用。</p> : quotes.data && signals.some(item => finite(quoteMap.get(item.symbol)?.last_price) == null) ? <p className="mt-1 text-[10px] text-muted">部分標的報價不可用，對應列以凍結排名參考收盤價顯示。</p> : null}
          {watchlist.isError && <p role="alert" className="mt-1 text-[10px] text-muted">自選清單目前無法讀取，已暫停加入操作。</p>}
          {add.isError && <p role="alert" className="mt-1 text-[10px] text-rose-500">加入自選失敗，請重試。</p>}
          <Link to="/taiwan-screener" className="mt-2 inline-flex items-center gap-1 text-[10px] text-muted hover:text-accent">查看台股選股 <ArrowUpRight className="h-3 w-3" /></Link>
        </div>
      ) : null}
    </section>
  )
}
