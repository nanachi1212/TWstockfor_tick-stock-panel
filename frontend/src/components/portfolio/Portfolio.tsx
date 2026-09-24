import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowDownToLine, ArrowUpFromLine, ExternalLink, Plus, Wallet } from 'lucide-react'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { useTodayQuantSelection } from '@/components/quant/TodaySelection'
import {
  buildPortfolioPositions,
  createPortfolioTransaction,
  isPortfolioTransaction,
  type PortfolioSide,
  type PortfolioTransaction,
} from '@/lib/portfolio'

const PORTFOLIO_CHANGED = 'portfolio-transactions-changed'

function readTransactions(): PortfolioTransaction[] {
  const saved = storage.portfolioTransactions.get([])
  return Array.isArray(saved) ? saved.filter(isPortfolioTransaction) : []
}

function usePortfolioTransactions() {
  const [transactions, setTransactions] = useState(readTransactions)
  useEffect(() => {
    const refresh = () => setTransactions(readTransactions())
    window.addEventListener(PORTFOLIO_CHANGED, refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(PORTFOLIO_CHANGED, refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])
  return transactions
}

function commitTransaction(input: Parameters<typeof createPortfolioTransaction>[0], current: PortfolioTransaction[]) {
  const transaction = createPortfolioTransaction(input, current)
  const next = [...current, transaction]
  storage.portfolioTransactions.set(next)
  window.dispatchEvent(new Event(PORTFOLIO_CHANGED))
}

function todayTaipei() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date())
}

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value)
    ? '—'
    : `NT$${value.toLocaleString('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function signedPct(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

export function PortfolioTradeDialog({
  symbol: initialSymbol,
  name: initialName,
  initialSide = 'buy',
  quote,
  onClose,
}: {
  symbol?: string
  name?: string
  initialSide?: PortfolioSide
  quote?: number | null
  onClose: () => void
}) {
  const transactions = usePortfolioTransactions()
  const [symbol, setSymbol] = useState(initialSymbol ?? '')
  const [name, setName] = useState(initialName ?? '')
  const [side, setSide] = useState<PortfolioSide>(initialSide)
  const [shares, setShares] = useState('')
  const [price, setPrice] = useState(quote && quote > 0 ? String(quote) : '')
  const [fee, setFee] = useState('0')
  const [date, setDate] = useState(todayTaipei)
  const [error, setError] = useState('')
  const position = useMemo(() => buildPortfolioPositions(transactions).find(item => item.symbol === symbol.trim().toUpperCase() && item.shares > 0), [transactions, symbol])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    try {
      commitTransaction({
        symbol,
        name,
        side,
        shares: Number(shares),
        price: Number(price),
        fee: Number(fee),
        date,
      }, transactions)
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '無法保存成交紀錄')
    }
  }

  return (
    <div className="fixed inset-0 z-[80] grid place-items-center bg-black/55 p-4" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl border border-border bg-surface p-4 shadow-2xl" aria-label="Portfolio 成交紀錄">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">記錄{side === 'buy' ? '買入' : '賣出'}</h2>
          <button type="button" onClick={onClose} className="text-xs text-muted hover:text-foreground">關閉</button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={() => { setSide('buy'); setError('') }} className={`rounded-lg border px-3 py-2 text-xs ${side === 'buy' ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary'}`}>買入</button>
          <button type="button" onClick={() => { setSide('sell'); setError('') }} className={`rounded-lg border px-3 py-2 text-xs ${side === 'sell' ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary'}`}>賣出</button>
        </div>
        <label className="block space-y-1 text-xs text-secondary">股票代碼
          <input value={symbol} onChange={event => setSymbol(event.target.value)} disabled={!!initialSymbol} required placeholder="例如 2330.TWSE" className="w-full rounded-lg border border-border bg-base px-3 py-2 font-mono text-foreground disabled:opacity-70" />
        </label>
        {initialSymbol ? <p className="text-[11px] text-muted">{name || initialSymbol}</p> : <label className="block space-y-1 text-xs text-secondary">股票名稱（選填）
          <input value={name} onChange={event => setName(event.target.value)} className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
        </label>}
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1 text-xs text-secondary">股數
            <input type="number" min="1" step="1" value={shares} onChange={event => setShares(event.target.value)} required className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
          <label className="space-y-1 text-xs text-secondary">成交價
            <input type="number" min="0.01" step="0.01" value={price} onChange={event => setPrice(event.target.value)} required className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
          <label className="space-y-1 text-xs text-secondary">日期
            <input type="date" value={date} onChange={event => setDate(event.target.value)} required className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
          <label className="space-y-1 text-xs text-secondary">手續費（選填）
            <input type="number" min="0" step="1" value={fee} onChange={event => setFee(event.target.value)} className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
        </div>
        {side === 'sell' && position && <p className="text-[11px] text-muted">目前持有 {position.shares.toLocaleString()} 股，平均成本 {money(position.averageCost)}</p>}
        {error && <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>}
        <button type="submit" className="w-full rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white hover:bg-accent/90">保存成交</button>
      </form>
    </div>
  )
}

export function PortfolioPanel({ symbol, name, quote: detailQuote, change: detailChange, changePct: detailChangePct }: {
  symbol?: string
  name?: string
  quote?: number | null
  change?: number | null
  changePct?: number | null
}) {
  const transactions = usePortfolioTransactions()
  const [trade, setTrade] = useState<{ symbol?: string; name?: string; side: PortfolioSide; quote?: number | null } | null>(null)
  const ledgerPositions = useMemo(() => buildPortfolioPositions(transactions), [transactions])
  const positions = useMemo(() => ledgerPositions.filter(position => position.shares > 0), [ledgerPositions])
  const { signals } = useTodayQuantSelection()
  const quantBySymbol = new Map(signals.map(signal => [signal.symbol, signal]))
  const symbols = positions.map(position => position.symbol)
  const quotesQuery = useQuery({
    queryKey: QK.portfolioQuotes(symbols.join(',')),
    queryFn: () => api.taiwanQuotes(symbols),
    enabled: !symbol && symbols.length > 0,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const quotes = new Map((quotesQuery.data?.quotes ?? []).map(item => [item.symbol, item]))
  const targetPosition = symbol ? positions.find(position => position.symbol === symbol.toUpperCase()) : undefined
  const targetLedgerPosition = symbol ? ledgerPositions.find(position => position.symbol === symbol.toUpperCase()) : undefined
  const shownPositions = symbol ? (targetPosition ? [targetPosition] : []) : positions
  const quoteFor = (position: typeof positions[number]): TaiwanRealtimeQuote | undefined => quotes.get(position.symbol)
  const hasMissingQuote = shownPositions.some(position => (symbol ? detailQuote : quoteFor(position)?.last_price) == null)
  const totalCost = positions.reduce((total, position) => total + position.costBasis, 0)
  const totalMarket = positions.reduce((total, position) => total + (quotes.get(position.symbol)?.last_price ?? 0) * position.shares, 0)
  const totalUnrealized = totalMarket - totalCost
  const totalDailyChange = positions.reduce((total, position) => total + (quotes.get(position.symbol)?.change ?? 0) * position.shares, 0)

  return (
    <section className="rounded-2xl border border-border bg-surface p-4 space-y-3" aria-label={symbol ? '我的持股' : '我的持倉'}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><Wallet className="h-4 w-4 text-accent" /><h2 className="text-sm font-semibold">{symbol ? '我的持股' : '我的持倉'}</h2></div>
        <button type="button" onClick={() => setTrade({ symbol: symbol ?? '', name: name ?? '', side: 'buy', quote: detailQuote })} className="inline-flex items-center gap-1 rounded-lg bg-accent px-2.5 py-1.5 text-xs font-medium text-white"><Plus className="h-3.5 w-3.5" />買入</button>
      </div>
      {!symbol && positions.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 text-xs">
          <Summary label="總市值" value={hasMissingQuote ? '報價不完整' : money(totalMarket)} />
          <Summary label="總成本" value={money(totalCost)} />
          <Summary label="未實現損益" value={hasMissingQuote ? '報價不完整' : money(totalUnrealized)} tone={totalUnrealized} />
          <Summary label="未實現報酬率" value={hasMissingQuote || totalCost === 0 ? '—' : signedPct(totalUnrealized / totalCost * 100)} tone={hasMissingQuote ? null : totalUnrealized} />
          <Summary label="今日持股變化" value={hasMissingQuote ? '報價不完整' : money(totalDailyChange)} tone={hasMissingQuote ? null : totalDailyChange} />
        </div>
      )}
      {!symbol && quotesQuery.isLoading && positions.length > 0 && <p role="status" className="text-[11px] text-muted">正在載入持股報價…</p>}
      {!symbol && quotesQuery.isError && <p role="alert" className="text-[11px] text-warning">報價載入失敗，請稍後重試；持股與成本資料仍已保存。</p>}
      {shownPositions.length === 0 ? (
        <p className="rounded-lg bg-base px-3 py-5 text-center text-xs text-muted">{symbol ? '目前沒有這檔持股，記錄買入後會顯示在這裡。' : '目前沒有持股，從觀察清單或個股頁記錄第一筆買入。'}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="text-[10px] text-muted"><tr>{!symbol && <th className="px-2 py-2">股票</th>}<th className="px-2 py-2">持有股數</th><th className="px-2 py-2">平均成本</th><th className="px-2 py-2">現價</th><th className="px-2 py-2">市值</th><th className="px-2 py-2">未實現損益</th><th className="px-2 py-2">報酬率</th><th className="px-2 py-2">今日漲跌</th><th className="px-2 py-2">Quant 分數</th><th className="px-2 py-2">今日排名</th><th className="px-2 py-2">操作</th></tr></thead>
            <tbody>
              {shownPositions.map(position => {
                const quote = quoteFor(position)
                const currentPrice = symbol ? detailQuote : quote?.last_price
                const unrealized = currentPrice == null ? null : currentPrice * position.shares - position.costBasis
                const returnPct = unrealized == null || position.costBasis === 0 ? null : unrealized / position.costBasis * 100
                const daily = symbol ? detailChange : quote?.change
                const dailyPct = symbol ? detailChangePct : quote?.change_pct
                const quant = quantBySymbol.get(position.symbol)
                return <tr key={position.symbol} className="border-t border-border/60">
                  {!symbol && <td className="px-2 py-2"><Link to={`/stocks/${encodeURIComponent(position.symbol)}`} className="font-medium text-foreground hover:text-accent">{position.name}</Link><span className="ml-1 font-mono text-muted">{position.symbol}</span></td>}
                  <td className="px-2 py-2 font-mono">{position.shares.toLocaleString()}</td><td className="px-2 py-2 font-mono">{money(position.averageCost)}</td>
                  <td className="px-2 py-2 font-mono">{currentPrice == null ? <span className="text-muted">目前無法取得報價</span> : <>{money(currentPrice)}{!symbol && quote?.source_meta?.is_stale && <small className="ml-1 text-warning">報價已過期</small>}</>}</td>
                  <td className="px-2 py-2 font-mono">{currentPrice == null ? '—' : money(currentPrice * position.shares)}</td>
                  <td className={`px-2 py-2 font-mono ${tone(unrealized)}`}>{unrealized == null ? '—' : money(unrealized)}</td><td className={`px-2 py-2 font-mono ${tone(unrealized)}`}>{signedPct(returnPct)}</td>
                  <td className={`px-2 py-2 font-mono ${tone(daily)}`}>{daily == null ? '—' : `${money(daily)} (${signedPct(dailyPct)})`}</td>
                  <td className="px-2 py-2 font-mono">{quant ? `${(quant.score * 100).toFixed(1)}%` : '—'}</td><td className="px-2 py-2 font-mono">{quant ? `#${quant.rank}` : '—'}</td>
                  <td className="px-2 py-2"><div className="flex items-center gap-1"><button type="button" aria-label={`買入 ${position.symbol}`} onClick={() => setTrade({ symbol: position.symbol, name: position.name, side: 'buy', quote: currentPrice })} className="rounded p-1 text-bull hover:bg-bull/10"><ArrowDownToLine className="h-3.5 w-3.5" /></button><button type="button" aria-label={`賣出 ${position.symbol}`} onClick={() => setTrade({ symbol: position.symbol, name: position.name, side: 'sell', quote: currentPrice })} className="rounded p-1 text-bear hover:bg-bear/10"><ArrowUpFromLine className="h-3.5 w-3.5" /></button>{!symbol && <Link aria-label={`查看 ${position.symbol} 個股`} to={`/stocks/${encodeURIComponent(position.symbol)}`} className="rounded p-1 text-muted hover:text-accent"><ExternalLink className="h-3.5 w-3.5" /></Link>}</div></td>
                </tr>
              })}
            </tbody>
          </table>
          {shownPositions.some(position => !symbol && !quoteFor(position)?.last_price) && <p className="mt-2 text-[11px] text-muted">部分標的目前無法取得報價，市值與損益摘要暫不顯示。</p>}
        </div>
      )}
      {symbol && targetLedgerPosition && <p className="text-[11px] text-muted">已實現損益（平均成本法）：{money(targetLedgerPosition.realizedPnl)}</p>}
      {!symbol && ledgerPositions.length > 0 && <p className="text-[11px] text-muted">已實現損益（平均成本法）：{money(ledgerPositions.reduce((sum, position) => sum + position.realizedPnl, 0))}</p>}
      {trade && <PortfolioTradeDialog symbol={trade.symbol} name={trade.name} initialSide={trade.side} quote={trade.quote} onClose={() => setTrade(null)} />}
    </section>
  )
}

function Summary({ label, value, tone: valueTone }: { label: string; value: string; tone?: number | null }) {
  return <div className="rounded-lg bg-base px-2.5 py-2"><span className="block text-[10px] text-muted">{label}</span><span className={`mt-1 block font-mono font-medium ${tone(valueTone)}`}>{value}</span></div>
}

function tone(value: number | null | undefined) {
  return value == null || value === 0 ? 'text-foreground' : value > 0 ? 'text-bull' : 'text-bear'
}
