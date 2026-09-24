import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowDownToLine, ArrowUpFromLine, ExternalLink, Plus, Wallet } from 'lucide-react'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { useTodayQuantSelection } from '@/components/quant/TodaySelection'
import { DataQualityBadge, freshnessLabel, type TaiwanQuoteMetaLike } from '@/components/taiwan/TaiwanDataQuality'
import {
  buildPortfolioPositions,
  createPortfolioTransaction,
  isTaiwanPortfolioSymbol,
  isPortfolioTransaction,
  nowTaipeiTime,
  todayTaipeiDate,
  type PortfolioSide,
  type PortfolioTransaction,
} from '@/lib/portfolio'

const PORTFOLIO_CHANGED = 'portfolio-transactions-changed'
const PORTFOLIO_LOCK_DB = 'tick-stock-panel-portfolio-lock'
const PORTFOLIO_LOCK_STORE = 'locks'

function withIndexedDbPortfolioLock<T>(operation: () => T): Promise<T> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('目前瀏覽器無法安全同步多分頁成交紀錄，請更新瀏覽器後重試'))
      return
    }

    let openRequest: IDBOpenDBRequest
    try {
      openRequest = indexedDB.open(PORTFOLIO_LOCK_DB, 1)
    } catch (cause) {
      reject(cause)
      return
    }
    openRequest.onupgradeneeded = () => {
      openRequest.result.createObjectStore(PORTFOLIO_LOCK_STORE)
    }
    openRequest.onerror = () => reject(openRequest.error ?? new Error('無法開啟成交同步鎖'))
    openRequest.onsuccess = () => {
      const db = openRequest.result
      let transaction: IDBTransaction
      try {
        transaction = db.transaction(PORTFOLIO_LOCK_STORE, 'readwrite')
      } catch (cause) {
        db.close()
        reject(cause)
        return
      }

      const store = transaction.objectStore(PORTFOLIO_LOCK_STORE)
      const request = store.get('portfolio')
      let result!: T
      let operationFailed = false
      request.onsuccess = () => {
        try {
          result = operation()
          store.put(true, 'portfolio')
        } catch (cause) {
          operationFailed = true
          reject(cause)
          transaction.abort()
        }
      }
      request.onerror = () => {
        operationFailed = true
        reject(request.error ?? new Error('無法取得成交同步鎖'))
        transaction.abort()
      }
      transaction.oncomplete = () => {
        db.close()
        if (!operationFailed) resolve(result)
      }
      transaction.onabort = () => {
        db.close()
        if (!operationFailed) reject(transaction.error ?? new Error('成交同步鎖交易失敗'))
      }
    }
  })
}

function withPortfolioWriteLock<T>(operation: () => T): Promise<T> {
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request('portfolio_transactions', operation)
  }
  return withIndexedDbPortfolioLock(operation)
}

function readTransactions(): { transactions: PortfolioTransaction[]; error: string | null } {
  try {
    const saved = storage.portfolioTransactions.get([])
    if (!Array.isArray(saved) || !saved.every(isPortfolioTransaction)) {
      return { transactions: [], error: '成交紀錄格式錯誤，已停止計算持倉。請先保留瀏覽器資料並修復紀錄。' }
    }
    buildPortfolioPositions(saved)
    return { transactions: saved, error: null }
  } catch {
    return { transactions: [], error: '無法讀取或驗證成交紀錄，已停止計算持倉。原始資料仍保留在瀏覽器中。' }
  }
}

function usePortfolioTransactions() {
  const [ledger, setLedger] = useState(readTransactions)
  useEffect(() => {
    const refresh = () => setLedger(readTransactions())
    window.addEventListener(PORTFOLIO_CHANGED, refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(PORTFOLIO_CHANGED, refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])
  return ledger
}

async function commitTransaction(input: Parameters<typeof createPortfolioTransaction>[0]) {
  await withPortfolioWriteLock(() => {
    const ledger = readTransactions()
    if (ledger.error) throw new Error(ledger.error)
    const transaction = createPortfolioTransaction(input, ledger.transactions)
    storage.portfolioTransactions.set([...ledger.transactions, transaction])
    window.dispatchEvent(new Event(PORTFOLIO_CHANGED))
  })
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
  const { transactions, error: ledgerError } = usePortfolioTransactions()
  const [symbol, setSymbol] = useState(initialSymbol ?? '')
  const [name, setName] = useState(initialName ?? '')
  const [side, setSide] = useState<PortfolioSide>(initialSide)
  const [shares, setShares] = useState('')
  const [price, setPrice] = useState(quote && quote > 0 ? String(quote) : '')
  const [fee, setFee] = useState('0')
  const [date, setDate] = useState(todayTaipeiDate)
  const [tradeTime, setTradeTime] = useState(nowTaipeiTime)
  const [error, setError] = useState('')
  const position = useMemo(() => buildPortfolioPositions(transactions).find(item => item.symbol === symbol.trim().toUpperCase() && item.shares > 0), [transactions, symbol])
  const instrumentQuery = useQuery({
    queryKey: ['portfolio-instrument', symbol.trim().toUpperCase(), date],
    queryFn: () => api.taiwanPortfolioInstrument(symbol.trim().toUpperCase(), date),
    enabled: side === 'buy' && isTaiwanPortfolioSymbol(symbol) && date <= todayTaipeiDate(),
    retry: false,
  })
  const taxInputsValid = !!symbol.trim() && Number.isInteger(Number(shares)) && Number(shares) > 0
    && Number.isFinite(Number(price)) && Number(price) > 0 && /^\d{4}-\d{2}-\d{2}$/.test(date) && date <= todayTaipeiDate()
  const taxQuery = useQuery({
    queryKey: ['portfolio-sell-tax', symbol.trim().toUpperCase(), shares, price, date],
    queryFn: () => api.taiwanTransactionTax(symbol.trim().toUpperCase(), Number(shares) * Number(price), date),
    enabled: side === 'sell' && taxInputsValid,
    retry: false,
  })

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    try {
      if (ledgerError) throw new Error(ledgerError)
      const shareCount = Number(shares)
      if (side === 'sell' && (!position || shareCount > position.shares)) {
        throw new Error(`最多可賣出 ${position?.shares ?? 0} 股`)
      }
      let tax = 0
      if (side === 'sell') {
        const result = await taxQuery.refetch()
        if (!result.data) throw new Error('目前無法依台灣市場規則估算證交稅，請稍後重試')
        tax = result.data.tax_amount
      } else {
        const result = await instrumentQuery.refetch()
        if (!result.data) throw new Error('目前無法確認這是可交易的台股股票或 ETF，請稍後重試')
      }
      await commitTransaction({
        symbol,
        name,
        side,
        shares: shareCount,
        price: Number(price),
        fee: Number(fee),
        tax,
        date,
        tradeTime,
      })
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
            <input type="date" max={todayTaipeiDate()} value={date} onChange={event => setDate(event.target.value)} required className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
          <label className="space-y-1 text-xs text-secondary">成交時間（台北）
            <input type="time" value={tradeTime} onChange={event => setTradeTime(event.target.value)} required className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
          <label className="space-y-1 text-xs text-secondary">手續費（選填）
            <input type="number" min="0" step="1" value={fee} onChange={event => setFee(event.target.value)} className="w-full rounded-lg border border-border bg-base px-3 py-2 text-foreground" />
          </label>
        </div>
        {side === 'sell' && <>
          {!taxInputsValid ? <p className="text-[11px] text-muted">輸入有效股數、價格與不晚於今日的成交日期後，會依台灣市場規則估算證交稅。</p>
            : taxQuery.isFetching ? <p role="status" className="text-[11px] text-muted">正在依台灣市場規則估算證交稅…</p>
              : taxQuery.data ? <p className="text-[11px] text-muted">預估證交稅：{money(taxQuery.data.tax_amount)}（一般稅率 {(taxQuery.data.tax_rate * 100).toFixed(2)}%，{taxQuery.data.tax_class}；當沖資格與配對股數尚無可驗證資料）</p>
                : taxQuery.isError ? <p role="alert" className="text-[11px] text-warning">無法取得此標的適用的證交稅規則，請稍後重試。</p> : null}
        </>}
        {side === 'buy' && symbol.trim() && <>
          {!isTaiwanPortfolioSymbol(symbol) ? <p role="alert" className="text-[11px] text-warning">請輸入台股股票或 ETF 的標準代碼，例如 2330.TWSE。</p>
            : instrumentQuery.isFetching ? <p role="status" className="text-[11px] text-muted">正在確認台股標的與成交日期…</p>
              : instrumentQuery.data?.trading_day_status === 'unverified' ? <p role="status" className="text-[11px] text-warning">台灣交易日曆尚未確認此平日，請依成交單核對日期。</p>
                : instrumentQuery.isError ? <p role="alert" className="text-[11px] text-warning">無法確認此標的是支援的台股股票或 ETF，請檢查代碼與成交日期。</p> : null}
        </>}
        {side === 'sell' && position && <p className="text-[11px] text-muted">目前持有 {position.shares.toLocaleString()} 股，平均成本 {money(position.averageCost)}</p>}
        {ledgerError && <p role="alert" className="text-[11px] text-danger">{ledgerError}</p>}
        {error && <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>}
        <button type="submit" disabled={!!ledgerError || (side === 'sell' ? (!taxQuery.data || taxQuery.isFetching) : (!instrumentQuery.data || instrumentQuery.isFetching))} className="w-full rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50">保存成交</button>
      </form>
    </div>
  )
}

export function PortfolioPanel({ symbol, name, quote: detailQuote, change: detailChange, changePct: detailChangePct, quoteMeta, quoteUnavailable = false }: {
  symbol?: string
  name?: string
  quote?: number | null
  change?: number | null
  changePct?: number | null
  quoteMeta?: TaiwanQuoteMetaLike | null
  quoteUnavailable?: boolean
}) {
  const { transactions, error: ledgerError } = usePortfolioTransactions()
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
  const quoteFetchFailed = symbol ? quoteUnavailable : quotesQuery.isError
  const quoteFor = (position: typeof positions[number]): TaiwanRealtimeQuote | undefined => quotes.get(position.symbol)
  const hasMissingQuote = quoteFetchFailed || shownPositions.some(position => (symbol ? detailQuote : quoteFor(position)?.last_price) == null)
  const hasMissingDailyChange = positions.some(position => quotes.get(position.symbol)?.change == null)
  const hasDegradedQuote = shownPositions.some(position => {
    const meta = symbol ? quoteMeta : quoteFor(position)?.source_meta
    const freshnessMeta = quoteFetchFailed && meta ? { ...meta, is_stale: true, status: 'stale' } : meta
    const freshness = freshnessLabel(freshnessMeta)
    return !freshness || freshness.tone !== 'realtime'
  })
  const totalCost = positions.reduce((total, position) => total + position.costBasis, 0)
  const totalMarket = positions.reduce((total, position) => total + (quotes.get(position.symbol)?.last_price ?? 0) * position.shares, 0)
  const totalUnrealized = totalMarket - totalCost
  const totalDailyChange = positions.reduce((total, position) => total + (quotes.get(position.symbol)?.change ?? 0) * position.shares, 0)
  const unavailableQuoteValue = quoteFetchFailed ? '行情更新失敗' : '報價不完整'

  return (
    <section className="rounded-2xl border border-border bg-surface p-4 space-y-3" aria-label={symbol ? '我的持股' : '我的持倉'}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><Wallet className="h-4 w-4 text-accent" /><h2 className="text-sm font-semibold">{symbol ? '我的持股' : '我的持倉'}</h2></div>
        {!ledgerError && <button type="button" onClick={() => setTrade({ symbol: symbol ?? '', name: name ?? '', side: 'buy', quote: detailQuote })} className="inline-flex items-center gap-1 rounded-lg bg-accent px-2.5 py-1.5 text-xs font-medium text-white"><Plus className="h-3.5 w-3.5" />買入</button>}
      </div>
      {ledgerError && <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">{ledgerError}</p>}
      {!symbol && positions.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 text-xs">
          <Summary label={hasDegradedQuote ? '總市值（含非即時報價）' : '總市值'} value={hasMissingQuote ? unavailableQuoteValue : money(totalMarket)} />
          <Summary label="總成本" value={money(totalCost)} />
          <Summary label={hasDegradedQuote ? '未實現損益（含非即時報價）' : '未實現損益'} value={hasMissingQuote ? unavailableQuoteValue : money(totalUnrealized)} tone={hasMissingQuote ? null : totalUnrealized} />
          <Summary label={hasDegradedQuote ? '未實現報酬率（含非即時報價）' : '未實現報酬率'} value={hasMissingQuote || totalCost === 0 ? (hasMissingQuote ? unavailableQuoteValue : '—') : signedPct(totalUnrealized / totalCost * 100)} tone={hasMissingQuote ? null : totalUnrealized} />
          <Summary label={hasDegradedQuote ? '今日持股變化（含非即時報價）' : '今日持股變化'} value={hasMissingQuote ? unavailableQuoteValue : hasMissingDailyChange ? '報價不完整' : money(totalDailyChange)} tone={hasMissingQuote || hasMissingDailyChange ? null : totalDailyChange} />
        </div>
      )}
      {!symbol && quotesQuery.isLoading && positions.length > 0 && <p role="status" className="text-[11px] text-muted">正在載入持股報價…</p>}
      {!symbol && quotesQuery.isError && <p role="alert" className="text-[11px] text-warning">報價載入失敗，請稍後重試；持股與成本資料仍已保存。</p>}
      {shownPositions.length === 0 ? ledgerError ? null : (
        <p className="rounded-lg bg-base px-3 py-5 text-center text-xs text-muted">{symbol ? '目前沒有這檔持股，記錄買入後會顯示在這裡。' : '目前沒有持股，從觀察清單或個股頁記錄第一筆買入。'}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="text-[10px] text-muted"><tr>{!symbol && <th className="px-2 py-2">股票</th>}<th className="px-2 py-2">持有股數</th><th className="px-2 py-2">平均成本</th><th className="px-2 py-2">現價</th><th className="px-2 py-2">市值</th><th className="px-2 py-2">未實現損益</th><th className="px-2 py-2">報酬率</th><th className="px-2 py-2">今日漲跌</th><th className="px-2 py-2">Quant 分數</th><th className="px-2 py-2">今日排名</th><th className="px-2 py-2">操作</th></tr></thead>
            <tbody>
              {shownPositions.map(position => {
                const quote = quoteFor(position)
                const meta = symbol ? quoteMeta : quote?.source_meta
                const displayMeta = quoteFetchFailed && meta ? { ...meta, is_stale: true, status: 'stale' } : meta
                const currentPrice = symbol ? detailQuote : quote?.last_price
                const unrealized = currentPrice == null ? null : currentPrice * position.shares - position.costBasis
                const returnPct = unrealized == null || position.costBasis === 0 ? null : unrealized / position.costBasis * 100
                const daily = symbol ? detailChange : quote?.change
                const dailyPct = symbol ? detailChangePct : quote?.change_pct
                const quant = quantBySymbol.get(position.symbol)
                return <tr key={position.symbol} className="border-t border-border/60">
                  {!symbol && <td className="px-2 py-2"><Link to={`/stocks/${encodeURIComponent(position.symbol)}`} className="font-medium text-foreground hover:text-accent">{position.name}</Link><span className="ml-1 font-mono text-muted">{position.symbol}</span></td>}
                  <td className="px-2 py-2 font-mono">{position.shares.toLocaleString()}</td><td className="px-2 py-2 font-mono">{money(position.averageCost)}</td>
                  <td className="px-2 py-2 font-mono">{currentPrice == null ? <span className="text-muted">目前無法取得報價</span> : <>{money(currentPrice)}<DataQualityBadge meta={displayMeta} quoteTime={quote?.quote_time} className="ml-1" /></>}</td>
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
