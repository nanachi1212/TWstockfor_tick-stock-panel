import type { TaiwanRealtimeQuote } from './api'
import { storage } from './storage'

/** Window event fired after the browser ledger is written (same tab); other tabs get storage. */
export const PORTFOLIO_CHANGED = 'portfolio-transactions-changed'

export type PortfolioSide = 'buy' | 'sell'

export interface PortfolioTransaction {
  id: string
  symbol: string
  name: string
  side: PortfolioSide
  shares: number
  price: number
  fee: number
  tax?: number | null
  date: string
  tradeTime?: string
  createdAt: string
}

export interface PortfolioPosition {
  symbol: string
  name: string
  shares: number
  costBasis: number
  averageCost: number
  realizedPnl: number | null
}

export interface PortfolioTransactionInput {
  symbol: string
  name?: string
  side: PortfolioSide
  shares: number
  price: number
  fee?: number
  tax?: number
  date: string
  tradeTime: string
}

export interface RegisteredHoldingsSummary {
  registered_positions_count: number
  quote_coverage: 'complete' | 'partial'
  weight_of_registered_pct?: number
  registered_market_value?: number
}

type RegisteredQuote = Pick<TaiwanRealtimeQuote, 'symbol' | 'last_price'> | { symbol?: string; last_price?: number | null }

/**
 * Build the small, privacy-preserving portfolio context sent to research.
 * A quote is usable only when its current price is a finite positive number;
 * missing prices remain partial rather than being coerced to zero.
 */
export function registeredHoldingsSummary(
  positions: readonly PortfolioPosition[],
  quotes: readonly RegisteredQuote[] | ReadonlyMap<string, RegisteredQuote> | Record<string, RegisteredQuote | undefined>,
  focusSymbol?: string,
): RegisteredHoldingsSummary {
  const registered = positions.filter(position => Number.isFinite(position.shares) && position.shares > 0)
  const getQuote = (symbol: string): RegisteredQuote | undefined => {
    if (Array.isArray(quotes)) return quotes.find(quote => quote.symbol === symbol)
    if (quotes instanceof Map) return quotes.get(symbol)
    return (quotes as Record<string, RegisteredQuote | undefined>)[symbol]
  }
  const marketValues = registered.map(position => {
    const price = getQuote(position.symbol)?.last_price
    const valid = typeof price === 'number' && Number.isFinite(price) && price > 0
    return { position, value: valid ? price * position.shares : null }
  })
  const complete = registered.every(item => marketValues.find(value => value.position === item)?.value != null)
  const total = marketValues.reduce<number>((sum, item) => sum + (item.value ?? 0), 0)
  const result: RegisteredHoldingsSummary = {
    registered_positions_count: registered.length,
    quote_coverage: complete ? 'complete' : 'partial',
  }
  if (complete) {
    const focus = focusSymbol == null
      ? total
      : marketValues.find(item => item.position.symbol === focusSymbol)?.value
    if (total > 0 && focus != null) result.weight_of_registered_pct = focus / total * 100
    result.registered_market_value = total
  }
  return result
}

export function isTaiwanPortfolioSymbol(symbol: string) {
  // 主動式/槓桿 ETF 代號帶英文尾碼，例如 00981A、00631L。
  return /^\d{2,6}[A-Z]?\.(TWSE|TPEX)$/i.test(symbol.trim())
}

export function isSupportedPortfolioInstrument(value: {
  symbol: string
  instrument_type?: string | null
  is_supported?: boolean
} | null | undefined) {
  return !!value && isTaiwanPortfolioSymbol(value.symbol)
    && value.is_supported === true
    && (value.instrument_type === 'stock' || value.instrument_type === 'etf')
}

export function todayTaipeiDate() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date())
}

export function nowTaipeiTime() {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date())
}

export function isPortfolioTransaction(value: unknown): value is PortfolioTransaction {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<PortfolioTransaction>
  return typeof item.id === 'string'
    && typeof item.symbol === 'string'
    && typeof item.name === 'string'
    && (item.side === 'buy' || item.side === 'sell')
    && Number.isInteger(item.shares) && (item.shares ?? 0) > 0
    && typeof item.price === 'number' && Number.isFinite(item.price) && item.price > 0
    && typeof item.fee === 'number' && Number.isFinite(item.fee) && item.fee >= 0
    && (item.tax == null || (typeof item.tax === 'number' && Number.isFinite(item.tax) && item.tax >= 0))
    && typeof item.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(item.date)
    && (item.tradeTime == null || (typeof item.tradeTime === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(item.tradeTime)))
    && typeof item.createdAt === 'string'
}

export function buildPortfolioPositions(transactions: readonly PortfolioTransaction[]): PortfolioPosition[] {
  const bySymbolAndDate = new Map<string, PortfolioTransaction[]>()
  for (const transaction of transactions) {
    const key = `${transaction.symbol}\0${transaction.date}`
    const sameDay = bySymbolAndDate.get(key) ?? []
    sameDay.push(transaction)
    bySymbolAndDate.set(key, sameDay)
  }
  for (const sameDay of bySymbolAndDate.values()) {
    const mixedSides = new Set(sameDay.map(item => item.side)).size > 1
    const mixedSideAtSameTime = sameDay.some(item => item.tradeTime
      && sameDay.some(other => other.tradeTime === item.tradeTime && other.side !== item.side))
    if (mixedSides && (sameDay.some(item => !item.tradeTime) || mixedSideAtSameTime)) {
      throw new Error(`${sameDay[0].symbol} 同日買賣需填寫不同的成交時間`)
    }
  }
  const ordered = [...transactions].sort((a, b) => a.date.localeCompare(b.date)
    || (a.tradeTime ?? '').localeCompare(b.tradeTime ?? '')
    || a.createdAt.localeCompare(b.createdAt))
  const positions = new Map<string, PortfolioPosition>()

  for (const transaction of ordered) {
    const position = positions.get(transaction.symbol) ?? {
      symbol: transaction.symbol,
      name: transaction.name || transaction.symbol,
      shares: 0,
      costBasis: 0,
      averageCost: 0,
      realizedPnl: 0,
    }
    if (transaction.name) position.name = transaction.name

    if (transaction.side === 'buy') {
      position.shares += transaction.shares
      position.costBasis += transaction.shares * transaction.price + transaction.fee
      position.averageCost = position.costBasis / position.shares
    } else {
      if (transaction.shares > position.shares) {
        throw new Error(`${transaction.symbol} 賣出股數超過當時持有股數`)
      }
      if (transaction.tax == null) {
        position.realizedPnl = null
      } else if (position.realizedPnl != null) {
        position.realizedPnl += transaction.shares * (transaction.price - position.averageCost) - transaction.fee - transaction.tax
      }
      position.shares -= transaction.shares
      position.costBasis = position.shares * position.averageCost
    }
    positions.set(transaction.symbol, position)
  }

  return [...positions.values()]
}

export function createPortfolioTransaction(
  input: PortfolioTransactionInput,
  transactions: readonly PortfolioTransaction[],
): PortfolioTransaction {
  const symbol = input.symbol.trim().toUpperCase()
  if (!symbol) throw new Error('請先選擇股票')
  if (!Number.isInteger(input.shares) || input.shares <= 0) throw new Error('股數必須是大於 0 的整數')
  if (!Number.isFinite(input.price) || input.price <= 0) throw new Error('成交價必須大於 0')
  const fee = input.fee ?? 0
  if (!Number.isFinite(fee) || fee < 0) throw new Error('手續費不可小於 0')
  const tax = input.tax ?? 0
  if (!Number.isFinite(tax) || tax < 0) throw new Error('證交稅不可小於 0')
  const dateParts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(input.date)
  const parsedDate = dateParts ? new Date(Date.UTC(Number(dateParts[1]), Number(dateParts[2]) - 1, Number(dateParts[3]))) : null
  if (!parsedDate || parsedDate.toISOString().slice(0, 10) !== input.date) {
    throw new Error('請輸入有效成交日期')
  }
  if (input.date > todayTaipeiDate()) throw new Error('成交日期不可晚於今日')
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(input.tradeTime)) throw new Error('請輸入有效成交時間')
  if (input.tradeTime < '09:00' || input.tradeTime > '14:30') {
    throw new Error('成交時間必須落在台灣市場交易時段（09:00 至 14:30）')
  }
  if (input.date === todayTaipeiDate() && input.tradeTime > nowTaipeiTime()) {
    throw new Error('成交時間不可晚於現在（台北時間）')
  }
  if (input.side === 'sell') {
    const current = buildPortfolioPositions(transactions).find(position => position.symbol === symbol)
    if (!current || input.shares > current.shares) throw new Error(`最多可賣出 ${current?.shares ?? 0} 股`)
  }

  const transaction: PortfolioTransaction = {
    id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    symbol,
    name: input.name?.trim() || symbol,
    side: input.side,
    shares: input.shares,
    price: input.price,
    fee,
    tax,
    date: input.date,
    tradeTime: input.tradeTime,
    createdAt: new Date().toISOString(),
  }
  // Validate the dated sequence too, so a backdated sale cannot use shares
  // bought later in the ledger.
  buildPortfolioPositions([...transactions, transaction])
  return transaction
}

export interface PortfolioImportRow {
  code: string
  name: string
  shares: number
  price: number
  fee: number
  date: string
}

/** CSV/TSV 解析: 支援引號內逗號與換行 (券商/試算表備註常見)。分隔符依第一行判斷。 */
function parseDelimited(text: string): string[][] {
  const delimiter = (text.split('\n', 1)[0] ?? '').includes('\t') ? '\t' : ','
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch !== '"') cell += ch
      else if (text[i + 1] === '"') { cell += '"'; i++ }
      else quoted = false
    } else if (ch === '"') quoted = true
    else if (ch === delimiter) { row.push(cell.trim()); cell = '' }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell.trim()); rows.push(row); row = []; cell = ''
    } else cell += ch
  }
  if (cell || row.length) { row.push(cell.trim()); rows.push(row) }
  return rows
}

const importNumber = (value: string | undefined) => Number((value ?? '').replace(/[,\s]/g, ''))

// 表頭關鍵字 → 欄位；沒有表頭時用預設順序 代號,名稱,股數,均價,總成本,日期。
const IMPORT_HEADERS = {
  code: /代號|代碼|^code$|^symbol$/i,
  name: /名稱|^name$/i,
  shares: /股數|^shares$/i,
  price: /均價|^price$/i,
  cost: /總成本|^cost$/i,
  date: /日期|^date$/i,
} as const
type ImportColumn = keyof typeof IMPORT_HEADERS

/**
 * 解析持股表 (券商匯出、Google/Excel 試算表、手打皆可)。
 * 非代號列(小計/現金/表頭)與股數 0 或 N/A 的列略過。總成本 − 股數×均價 視為手續費。
 */
export function parsePortfolioImport(text: string, defaultDate: string): { rows: PortfolioImportRow[]; errors: string[] } {
  const rows: PortfolioImportRow[] = []
  const errors: string[] = []
  let columns: Record<ImportColumn, number> = { code: 0, name: 1, shares: 2, price: 3, cost: 4, date: 5 }
  parseDelimited(text.replace(/^\uFEFF/, '')).forEach((cells, index) => {
    const header = Object.fromEntries(
      (Object.keys(IMPORT_HEADERS) as ImportColumn[]).map(key => [key, cells.findIndex(cell => IMPORT_HEADERS[key].test(cell))]),
    ) as Record<ImportColumn, number>
    if (header.code >= 0 && header.shares >= 0) { columns = header; return }
    const at = (key: ImportColumn) => (columns[key] >= 0 ? cells[columns[key]] : undefined)
    const code = (at('code') ?? '').toUpperCase().replace(/\.(TWSE|TPEX)$/, '')
    if (!/^\d{2,6}[A-Z]?$/.test(code)) return
    const sharesText = (at('shares') ?? '').trim()
    if (!sharesText || /^n\/?a$/i.test(sharesText)) return
    const shares = importNumber(sharesText)
    const price = importNumber(at('price'))
    if (shares === 0) return
    if (!Number.isInteger(shares) || shares < 0 || !Number.isFinite(price) || price <= 0) {
      errors.push(`第 ${index + 1} 列 ${code}：股數或均價不是有效數字`)
      return
    }
    const cost = importNumber(at('cost'))
    const fee = at('cost') && Number.isFinite(cost) ? Math.max(0, Math.round((cost - shares * price) * 100) / 100) : 0
    const rawDate = (at('date') ?? '').replace(/\//g, '-')
    const date = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : defaultDate
    rows.push({ code, name: at('name') ?? '', shares, price, fee, date })
  })
  return { rows, errors }
}

/** Read and validate the whole browser ledger; any invalid entry rejects the ledger instead of dropping rows. */
export function readPortfolioLedger(): { transactions: PortfolioTransaction[]; error: string | null } {
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