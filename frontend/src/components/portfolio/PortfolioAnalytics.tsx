import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { BarChart3 } from 'lucide-react'
import { api, type TaiwanPortfolioAnalytics } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { buildPortfolioPositions, PORTFOLIO_CHANGED, readPortfolioLedger } from '@/lib/portfolio'

const REASONS: Record<string, string> = {
  no_holdings: '尚無持股',
  corporate_action_coverage_unavailable: '除權息資料尚未驗證，無法計算還原股價績效',
  no_daily_data: '尚無日 K 資料',
  symbol_data_insufficient: '部分持股缺少日 K 或還原資料',
  insufficient_sessions: '共同交易日不足 20 天',
  incomplete_sessions: '共同期間有交易日缺價或尚未確認休市，無法可靠計算報酬與回撤',
  invalid_portfolio_value: '持股數量換算的組合市值超出可計算範圍',
  asset_action_coverage_unavailable: '持股包含 ETF 或未確認資產，現有除權息來源無法證實其配息涵蓋',
  benchmark_unavailable: '0050 基準缺少日 K 或還原資料，僅顯示股票持股回放；基準報酬、波動度及 Beta 暫不提供',
}

export function usePortfolioAnalytics() {
  const [ledger, setLedger] = useState(readPortfolioLedger)
  useEffect(() => {
    const refresh = () => setLedger(readPortfolioLedger())
    window.addEventListener(PORTFOLIO_CHANGED, refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(PORTFOLIO_CHANGED, refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])
  const holdings = useMemo(() => ledger.error ? [] : buildPortfolioPositions(ledger.transactions)
    .filter(p => p.shares > 0)
    .map(p => ({ symbol: p.symbol, shares: p.shares }))
    .sort((a, b) => a.symbol.localeCompare(b.symbol)), [ledger])
  const key = holdings.map(h => `${h.symbol}:${h.shares}`).join(',')
  const query = useQuery({
    queryKey: QK.portfolioAnalytics(key),
    queryFn: () => api.taiwanPortfolioAnalytics(holdings),
    enabled: holdings.length > 0,
    staleTime: 10 * 60_000,
  })
  return { hasHoldings: holdings.length > 0, query }
}

const pct = (v: number | null | undefined) => v == null ? '—' : `${(v * 100).toFixed(1)}%`
const tone = (v: number | null | undefined) => v == null || v === 0 ? 'text-foreground' : v > 0 ? 'text-bull' : 'text-bear'

export function PortfolioAnalyticsPanel({ query }: { query: UseQueryResult<TaiwanPortfolioAnalytics> }) {
  return (
    <section aria-label="組合分析" className="rounded-2xl border border-border bg-surface p-4 space-y-3">
      <div className="flex items-center gap-2"><BarChart3 className="h-4 w-4 text-accent" /><h2 className="text-sm font-semibold">組合分析</h2></div>
      {query.isLoading && <p className="text-xs text-muted">計算中…</p>}
      {query.isError && <p role="alert" className="text-xs text-danger">組合分析暫時無法取得，請稍後再試。</p>}
      {query.data && !query.isError && <AnalyticsBody data={query.data} />}
    </section>
  )
}

function AnalyticsBody({ data }: { data: TaiwanPortfolioAnalytics }) {
  const { performance: perf, allocation } = data
  const m = perf.metrics
  const concentration = allocation.concentration
  return (
    <>
      <div className="space-y-2" aria-label="集中度健檢">
        <h3 className="text-xs font-semibold text-secondary">持股與產業集中度{allocation.as_of && <span className="ml-1 font-normal text-muted">（{allocation.as_of} 收盤）</span>}</h3>
        {allocation.status === 'available' && concentration ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Stat label="最大持股占比" value={pct(concentration.largest_position.weight)} hint={concentration.largest_position.symbol} />
            <Stat label="前三大持股合計" value={pct(concentration.top3_weight)} />
            <Stat label="最大產業占比" value={pct(concentration.largest_industry?.weight)} hint={concentration.largest_industry?.industry ?? '無法判定：僅有 ETF 或個股產業未分類'} />
          </div>
        ) : <p role="status" className="text-xs text-warning">收盤價不完整或日期不一致，暫不顯示整體權重與集中度。</p>}
        <p className="text-[11px] text-muted">占比以本頁已登錄持股的收盤市值計算，不含現金或其他帳戶。僅呈現集中程度，不設定風險門檻。</p>
      </div>
      <div className="grid gap-3 lg:grid-cols-[2fr_1fr]">
      <div className="min-w-0 space-y-2">
        <h3 className="text-xs font-semibold text-secondary">假設績效（以目前持股回放）</h3>
        {perf.status !== 'unavailable' && m ? (
          <>
            <p className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] text-secondary">
              用「現在的持股股數」回推 {perf.window_start && perf.window_end ? `${perf.window_start} ～ ${perf.window_end}` : '績效期間日期不可用（缺少有效起訖日）'}（{perf.sessions} 個交易日）的還原股價走勢，不是你實際買賣的損益紀錄。基準為{data.benchmark_name}；績效期間受已驗證除權息資料覆蓋範圍限制。
            </p>
            {perf.reason && <p role="status" className="text-xs text-warning">{REASONS[perf.reason] ?? perf.reason}</p>}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              <Stat label="回放報酬" value={pct(m.total_return)} valueTone={m.total_return} />
              <Stat label={`${data.benchmark_symbol.split('.')[0]} 報酬`} value={pct(m.benchmark_return)} valueTone={m.benchmark_return} />
              <Stat label="Beta" value={m.beta == null ? '—' : m.beta.toFixed(2)} hint="跟大盤連動程度，1 = 同步" />
              <Stat label="年化波動度" value={pct(m.volatility)} hint={`0050 為 ${pct(m.benchmark_volatility)}`} />
              <Stat label="最大回撤" value={pct(m.max_drawdown)} valueTone={m.max_drawdown} hint="期間內從高點最大跌幅" />
            </div>
            <PerformanceChart series={perf.series} benchmarkLabel={data.benchmark_symbol.split('.')[0]} />
          </>
        ) : (
          <p className="rounded-lg bg-base px-3 py-2 text-xs text-muted">
            績效與風險無法計算：{REASONS[perf.reason ?? ''] ?? perf.reason}
            {perf.excluded_symbols.length > 0 && `（${perf.excluded_symbols.map(s => s.symbol.split('.')[0]).join('、')}）`}
            {!!perf.missing_sessions?.length && <span className="mt-1 block">缺口：{perf.missing_sessions.slice(0, 10).map(s => `${s.symbol} ${s.date}`).join('、')}{perf.missing_sessions.length > 10 ? `，共 ${perf.missing_sessions.length} 筆` : ''}</span>}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <h3 className="text-xs font-semibold text-secondary">產業配置{allocation.as_of && <span className="ml-1 font-normal text-muted">（{allocation.as_of} 收盤）</span>}</h3>
        {allocation.sectors.length ? (
          <ul className="space-y-1.5">
            {allocation.sectors.map(s => (
              <li key={s.industry} className="text-xs">
                <div className="flex justify-between"><span>{s.industry}</span><span className="font-mono">{pct(s.weight)}</span></div>
                <div className="mt-0.5 h-1.5 rounded bg-base"><div className="h-1.5 rounded bg-accent" style={{ width: `${s.weight * 100}%` }} /></div>
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-muted">收盤資料不足，暫不顯示產業占比。</p>}
        {allocation.unpriced_symbols.length > 0 && (
          <p className="text-[11px] text-warning">缺收盤價：{allocation.unpriced_symbols.map(s => s.split('.')[0]).join('、')}</p>
        )}
        {(allocation.stale_symbols?.length ?? 0) > 0 && (
          <p className="text-[11px] text-warning">收盤價日期落後：{allocation.stale_symbols.map(s => s.split('.')[0]).join('、')}</p>
        )}
        <p className="text-[11px] text-muted">ETF 以商品分類列示，尚未拆解成分股產業；最大產業占比不含 ETF。個股產業未分類時，暫不判定最大產業。</p>
      </div>
      </div>
    </>
  )
}

function Stat({ label, value, valueTone, hint }: { label: string; value: string; valueTone?: number | null; hint?: string }) {
  return (
    <div className="rounded-lg bg-base px-2.5 py-2" title={hint}>
      <span className="block text-[10px] text-muted">{label}</span>
      <span className={`mt-1 block font-mono font-medium ${tone(valueTone)}`}>{value}</span>
      {hint && <span className="mt-0.5 block text-[10px] text-muted">{hint}</span>}
    </div>
  )
}

function PerformanceChart({ series, benchmarkLabel }: { series: TaiwanPortfolioAnalytics['performance']['series']; benchmarkLabel: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current || !series.length) return
    const chart = echarts.init(ref.current)
    chart.setOption({
      animation: false,
      grid: { left: 40, right: 12, top: 28, bottom: 28 },
      legend: { top: 0, textStyle: { fontSize: 11, color: '#94a3b8' } },
      tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => v == null ? '—' : `${Number(v).toFixed(1)}` },
      xAxis: { type: 'category', data: series.map(r => r.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        { name: '目前持股回放', type: 'line', showSymbol: false, data: series.map(r => r.portfolio), lineStyle: { width: 2, color: '#0ea5e9' }, itemStyle: { color: '#0ea5e9' } },
        ...(series.some(r => r.benchmark != null) ? [{ name: benchmarkLabel, type: 'line', showSymbol: false, data: series.map(r => r.benchmark), lineStyle: { width: 1.5, color: '#94a3b8' }, itemStyle: { color: '#94a3b8' } }] : []),
      ],
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(ref.current)
    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [series, benchmarkLabel])
  return <div ref={ref} className="h-56 w-full" aria-label="組合走勢對比基準（起點 = 100）" />
}
