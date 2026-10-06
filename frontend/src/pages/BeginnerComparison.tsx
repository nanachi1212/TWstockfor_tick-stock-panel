import { useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react'
import { api, type BeginnerCandidate, type BeginnerTechnicalPanel, type BeginnerComparisonResponse } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useSafeBack } from '@/lib/useSafeBack'
import { MarketSummaryCard, StateBadge, STRENGTH_DISCLAIMER } from '@/components/beginner/BeginnerPicker'

const strengthLabel = { strong: '強', medium: '中', weak: '弱' } as const
const missing = '資料不足'
const format = (value: number | null | undefined, suffix = '') => value == null || !Number.isFinite(value)
  ? missing : `${value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })}${suffix}`
const distance = (value: number | null | undefined) => value == null ? missing : `約 ${Math.abs(value).toFixed(1)}%`
const trendLabel = { strong: '偏強', neutral: '中性', weak: '偏弱', unavailable: missing } as const
const volumeLabel = {
  price_up_volume_up: '價漲量增', price_up_volume_down: '價漲量縮',
  price_down_volume_up: '價跌量增', price_down_volume_down: '價跌量縮', neutral: '量價變化不明顯', unavailable: missing,
} as const
const chipLabel = { buy: '偏買', sell: '偏賣', neutral: '中性', increase_fast: '快速增加', decrease: '減少', stable: '變化不大', unavailable: missing } as const
const relativeLabel = { stronger: '較 0050 強', similar: '接近 0050', weaker: '較 0050 弱', unavailable: missing } as const
const volatilityLabel = { low: '波動較低', normal: '波動一般', high: '波動偏大', unavailable: missing } as const

function PriceSummary({ candidate }: { candidate: BeginnerCandidate }) {
  const panel = candidate.technical_panel
  const support = panel?.support
  const resistance = panel?.resistance
  const inZone = support?.status === 'available' && support.support_distance_low_pct != null && support.support_distance_high_pct != null
    && support.support_distance_low_pct <= 0 && support.support_distance_high_pct >= 0
  const nearResistance = resistance?.status === 'available' && resistance.resistance_distance_pct != null
    && resistance.resistance_distance_pct >= 0 && resistance.resistance_distance_pct <= 3
  return (
    <section aria-label="價格摘要" className="rounded-lg bg-base/70 p-2.5">
      <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs">
        <dt className="text-muted">最後完整收盤</dt><dd>{format(candidate.close)} <span className="text-muted">{candidate.as_of ?? '日期不可用'}</span></dd>
        <dt className="text-muted">支撐區</dt><dd>{support?.status === 'available' ? `${format(support.support_zone_low)}～${format(support.support_zone_high)}` : missing}</dd>
        <dt className="text-muted">壓力</dt><dd>{resistance?.status === 'available' ? format(resistance.resistance) : missing}</dd>
        <dt className="text-muted">失效位</dt><dd>{panel?.invalidation.status === 'available' ? format(panel.invalidation.invalidation) : missing}</dd>
        <dt className="text-muted">距支撐</dt><dd>{inZone ? '支撐區內' : support?.status === 'available'
          ? `${distance(support.support_distance_high_pct)}～${distance(support.support_distance_low_pct)}` : missing}</dd>
        <dt className="text-muted">距壓力</dt><dd>{resistance?.status === 'available' ? `${distance(resistance.resistance_distance_pct)}${nearResistance ? ' · 近壓力' : ''}` : missing}</dd>
      </dl>
      {!support || support.status !== 'available' ? <p className="mt-1 text-xs text-muted">{candidate.plan_unavailable_reason ?? '目前資料不足，無法可靠提供完整支撐／壓力。'}</p> : null}
    </section>
  )
}

function AdvancedEvidence({ candidate }: { candidate: BeginnerCandidate }) {
  const panel = candidate.technical_panel
  const evidence = panel ? [
    ['價位', panel.support, [['收盤', candidate.close], ['支撐下緣', panel.support.support_zone_low], ['支撐上緣', panel.support.support_zone_high]]],
    ['壓力', panel.resistance, [['壓力', panel.resistance.resistance]]],
    ['失效位', panel.invalidation, [['失效位', panel.invalidation.invalidation]]],
    ['均線', panel.moving_averages, [['MA5', panel.moving_averages.ma5], ['MA20', panel.moving_averages.ma20], ['MA60', panel.moving_averages.ma60]]],
    ['量價', panel.volume, [['成交量（股）', panel.volume.today_volume], ['20 日均量（股）', panel.volume.average_20d], ['量比', panel.volume.ratio]]],
    ['法人 5 日', panel.institutional, [['合計（股）', panel.institutional.total_net_5d], ['外資（股）', panel.institutional.foreign_net_5d], ['投信（股）', panel.institutional.investment_trust_net_5d], ['自營商（股）', panel.institutional.dealer_net_5d], ['完整交易日', panel.institutional.complete_sessions]]],
    ['融資融券', panel.margin, [['融資餘額（股）', panel.margin.margin_balance], ['融資增減（股）', panel.margin.margin_change], ['融券餘額（股）', panel.margin.short_balance], ['融券增減（股）', panel.margin.short_change]]],
    ['相對 0050', panel.relative_strength, [['個股 20 日（%）', panel.relative_strength.stock_return_pct], ['0050 20 日（%）', panel.relative_strength.benchmark_return_pct], ['超額報酬（%）', panel.relative_strength.excess_return_pct]]],
    ['20 日價格位置', panel.range_position, [['20 日低點', panel.range_position.low_20d], ['20 日高點', panel.range_position.high_20d], ['區間位置（%）', panel.range_position.position_pct]]],
    ['波動', panel.volatility, [['ATR14', panel.volatility.atr_14], ['ATR（%）', panel.volatility.atr_pct]]],
    ['基本面', panel.fundamentals, [['營收年增（%）', panel.fundamentals.revenue_yoy_pct], ['營收月增（%）', panel.fundamentals.revenue_mom_pct], ['EPS', panel.fundamentals.eps], ['本益比', panel.fundamentals.pe]]],
    ['即時力道', panel.inner_outer, [['內盤（%）', panel.inner_outer.inner_pct], ['外盤（%）', panel.inner_outer.outer_pct], ['即時價', panel.inner_outer.last_price]]],
  ] as Array<[string, { status: string; source: string; as_of: string | null; freshness: string; explanation: string }, Array<[string, number | null]>]> : []
  return (
    <details className="rounded-lg border border-border p-2.5">
      <summary className="min-h-7 cursor-pointer text-xs font-semibold text-accent">展開進階資料</summary>
      <div className="mt-2 space-y-2 text-xs">
        <p>最後完整收盤：{format(candidate.close)} · 資料日 {candidate.as_of ?? missing}</p>
        {evidence.length === 0 && <p>{missing}，沒有可靠技術證據。</p>}
        {evidence.map(([label, item, values]) => (
          <section key={label} className="min-w-0 border-t border-border/60 pt-2">
            <h4 className="font-semibold">{label} · {item.status === 'available' ? '可用' : missing}</h4>
            <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-1">
              {values.map(([name, value]) => <div key={name} className="contents"><dt className="text-muted">{name}</dt><dd>{item.status === 'available' ? format(value) : missing}</dd></div>)}
            </dl>
            <p className="mt-1 break-words text-muted">{item.explanation}</p>
            <p className="mt-1 break-all text-[10px] text-muted">source: {item.source} · as of: {item.as_of ?? missing} · freshness: {item.freshness}</p>
          </section>
        ))}
        {panel && <p className="break-words text-muted">營收期別 {panel.fundamentals.revenue_as_of ?? missing} · 財報資料日 {panel.fundamentals.financials_as_of ?? missing} · 估值資料日 {panel.fundamentals.valuation_as_of ?? missing}</p>}
        {candidate.dimensions.map(d => <p key={d.key} className="break-words text-muted">{d.label}：{d.status === 'unavailable' ? missing : d.explanation}<br /><span className="break-all text-[10px]">source: {d.source}</span></p>)}
        {candidate.trade_plan && <p className="break-all text-[10px] text-muted">TradePlan {candidate.trade_plan.rule_version} · as of: {candidate.trade_plan.evidence_as_of} · identity: {candidate.trade_plan.plan_identity}</p>}
        {candidate.data_gaps.length > 0 && <p className="break-words text-warning">資料缺口：{candidate.data_gaps.join('；')}</p>}
      </div>
    </details>
  )
}

function SummaryRows({ panel, candidate }: { panel: BeginnerTechnicalPanel | null; candidate: BeginnerCandidate }) {
  const fundamental = candidate.dimensions.find(d => d.key === 'fundamental_context')
  const rows = [
    ['價格位置', panel?.range_position.status === 'available' ? '已有近期區間證據，請配合支撐與壓力觀察' : missing],
    ['趨勢', panel?.moving_averages.status === 'available' ? trendLabel[panel.moving_averages.state] : missing],
    ['量價', panel?.volume.status === 'available' ? volumeLabel[panel.volume.pattern] : missing],
    ['法人 5 日', panel?.institutional.status === 'available' ? chipLabel[panel.institutional.state] : missing],
    ['融資融券', panel?.margin.status === 'available' ? `融資${chipLabel[panel.margin.margin_state]} · 融券${chipLabel[panel.margin.short_state]}` : missing],
    ['相對 0050', panel?.relative_strength.status === 'available' ? relativeLabel[panel.relative_strength.state] : missing],
    ['基本面', fundamental?.status === 'positive' ? '營收條件偏正向' : fundamental?.status === 'negative' ? '營收或獲利有警訊' : fundamental?.status === 'neutral' ? '營收條件中性' : missing],
    ['波動', panel?.volatility.status === 'available' ? volatilityLabel[panel.volatility.level] : missing],
  ]
  return <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-2 gap-y-2 text-xs">{rows.map(([label, value]) => <div className="contents" key={label}><dt className="text-muted">{label}</dt><dd>{value}</dd></div>)}</dl>
}

function ComparisonCard({ candidate, insufficient }: { candidate: BeginnerCandidate; insufficient: boolean }) {
  const panel = candidate.technical_panel
  const riskTexts = [...new Set([
    ...candidate.exclusion_reasons.map(reason => reason.display_text),
    ...(panel?.key_risks.map(risk => risk.text) ?? []),
  ])]
  return (
    <article aria-label={`${candidate.name || candidate.symbol} ${candidate.symbol} 比較`} className="min-w-0 space-y-3 rounded-card border border-border bg-surface p-3 text-foreground [overflow-wrap:anywhere]">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <Link to={`/stocks/${encodeURIComponent(candidate.symbol)}`} className="text-sm font-semibold hover:text-accent">{candidate.name || candidate.symbol}<span className="block text-[11px] font-normal text-muted">{candidate.symbol} · {candidate.rank == null ? '未列入原排名' : `原排名 #${candidate.rank}`}</span></Link>
        {insufficient ? <span className="text-xs text-warning">資料不足 · 暫不評價</span> : <StateBadge state={candidate.selection_state} />}
      </header>
      <div className="rounded-lg bg-accent/5 p-2.5">
        <p className="text-xs font-semibold text-muted">現在怎麼做</p>
        <p className="mt-1 text-sm">{insufficient ? '資料不足，先補齊資料再判斷。' : candidate.selection_state === 'no_chase' ? '現價距離合理觀察區過遠，不宜追價，等拉回再評估。' : candidate.action_summary}</p>
        <p className="mt-1 text-xs text-muted">訊號：{insufficient ? '資料不足' : strengthLabel[candidate.signal_strength]} · 強股不等於當下適合買</p>
      </div>
      <PriceSummary candidate={candidate} />
      <SummaryRows panel={panel} candidate={candidate} />
      <section aria-label="主要風險">
        <p className="text-xs font-semibold text-muted">主要風險</p>
        <ul className="mt-1 space-y-1 text-xs text-warning">{riskTexts.map(text => <li key={text}>• {text}</li>)}</ul>
        {riskTexts.length === 0 && <p className="text-xs text-muted">資料不足，無法確認風險。</p>}
      </section>
      <section aria-label="即時力道" className="text-xs text-muted">
        <p>{panel?.inner_outer.status === 'available' ? `即時力道快照：${panel.inner_outer.explanation}` : '即時力道不可用'}</p>
        {panel?.inner_outer.status === 'available' && <p className="mt-1">資料時間 {panel.inner_outer.as_of ?? missing} · 僅補充盤中背景；按「更新比較」讀取最新快照。</p>}
      </section>
      <AdvancedEvidence candidate={candidate} />
    </article>
  )
}

function ComparisonResult({ data }: { data: BeginnerComparisonResponse }) {
  const bySymbol = new Map(data.candidates.map(c => [c.symbol, c]))
  return <>
    <MarketSummaryCard market={data.market} />
    <section aria-label="這幾檔怎麼選" className="rounded-card border border-accent/30 bg-surface p-3">
      <h2 className="text-base font-semibold text-foreground">這幾檔怎麼選？</h2>
      {data.market.state === 'cautious' && <p className="mt-1 text-sm text-warning">市場偏保守，先觀察與等待，不積極追買。</p>}
      {data.market.state === 'unavailable' && <p className="mt-1 text-sm text-warning">市場資料不足，先確認資料，再判斷是否觀察。</p>}
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {data.groups.filter(g => g.symbols.length).map(group => <div key={group.key} className="min-w-0 rounded-lg bg-base/60 p-2 text-sm">
          <h3 className="font-semibold text-foreground">{group.label}</h3>
          <p className="mt-1 break-words text-muted">{group.symbols.map((symbol, i) => `${group.key === 'observe' ? `${['①', '②', '③', '④', '⑤'][i]} ` : ''}${bySymbol.get(symbol)?.name || symbol}`).join('、')}</p>
        </div>)}
      </div>
      <p className="mt-2 text-xs text-muted">依原本行動狀態分組，保留各股原排名；優先觀察代表觀察順序。{STRENGTH_DISCLAIMER}</p>
    </section>
    <section aria-label="優先順序的理由" className="rounded-card border border-border bg-surface p-3 text-xs">
      <h2 className="text-sm font-semibold text-foreground">為何 A 比 B 優先？</h2>
      {data.differences.length ? data.differences.map(d => <div key={`${d.higher_symbol}-${d.lower_symbol}`} className="mt-2">
        <h3 className="font-medium text-foreground">{bySymbol.get(d.higher_symbol)?.name || d.higher_symbol} 相較於 {bySymbol.get(d.lower_symbol)?.name || d.lower_symbol}</h3>
        <ul className="mt-1 space-y-1 text-muted">{d.reasons.slice(0, 3).map(text => <li key={text}>• {text}</li>)}</ul>
      </div>) : <p className="mt-1 text-muted">資料不足或沒有可支持的條件差異，不追加優先理由。</p>}
    </section>
    <div className="grid min-w-0 grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {data.groups.flatMap(group => group.symbols.map(symbol => {
        const candidate = bySymbol.get(symbol)
        return candidate ? <ComparisonCard key={symbol} candidate={candidate} insufficient={group.key === 'insufficient'} /> : null
      }))}
    </div>
    <p className="break-words text-[11px] text-muted">{data.disclaimer} · 資料日 {data.as_of ?? missing} · 規則版本 {data.version}</p>
  </>
}

export function BeginnerComparison() {
  const [params] = useSearchParams()
  const raw = params.get('symbols') ?? ''
  const symbols = raw ? raw.split(',').map(s => s.trim().toUpperCase()) : []
  const invalid = symbols.some(s => !/^\d{4,6}[A-Z]?\.(TWSE|TPEX)$/.test(s)) || new Set(symbols).size !== symbols.length
  const valid = symbols.length >= 2 && symbols.length <= 5 && !invalid
  const goBack = useSafeBack('/picks')
  const refreshing = useRef(false)
  const query = useQuery({
    queryKey: QK.beginnerComparison(symbols), queryFn: () => api.beginnerComparison([...symbols].sort()),
    enabled: valid, staleTime: 5 * 60 * 1000, retry: false,
    // Manual refresh captures Fugle context without putting history generation scans on a timer.
  })
  return (
    <div className="min-h-full min-w-0 bg-base p-2 sm:p-3">
      <div className="mx-auto w-full min-w-0 max-w-6xl space-y-3">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div><h1 className="text-lg font-semibold text-foreground">初學者股票比較</h1><p className="text-xs text-muted">比較已選股票的行動條件，沿用今日選股證據。</p></div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={goBack} className="inline-flex min-h-9 items-center gap-1 rounded-md border border-border px-3 text-xs text-foreground"><ArrowLeft className="h-3.5 w-3.5" />返回</button>
            {valid && <button type="button" disabled={query.isFetching} onClick={() => {
              if (refreshing.current || query.isFetching) return
              refreshing.current = true
              void query.refetch().finally(() => { refreshing.current = false })
            }} className="inline-flex min-h-9 items-center gap-1 rounded-md border border-border px-3 text-xs text-foreground disabled:opacity-40"><RefreshCw className="h-3.5 w-3.5" />更新比較</button>}
          </div>
        </header>
        {!valid && <div role="alert" className="rounded-card border border-border bg-surface p-4 text-sm text-warning">
          {invalid ? '股票代號無效或重複，請回今日選股重新勾選。' : symbols.length > 5 ? '最多比較 5 檔股票，請重新勾選。' : '請先勾選 2 至 5 檔股票，再開始比較。'}
          <Link to="/picks" className="mt-2 block text-accent">回今日選股</Link>
        </div>}
        {valid && query.isLoading && <p role="status" className="flex items-center gap-2 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" />整理比較證據中…</p>}
        {valid && query.isError && <p role="alert" className="rounded-card border border-border bg-surface p-3 text-sm text-danger">比較資料暫時無法讀取，請確認代號或稍後更新比較。</p>}
        {valid && query.data && <ComparisonResult data={query.data} />}
      </div>
    </div>
  )
}
