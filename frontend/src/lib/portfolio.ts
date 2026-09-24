export type PortfolioSide = 'buy' | 'sell'

export interface PortfolioTransaction {
  id: string
  symbol: string
  name: string
  side: PortfolioSide
  shares: number
  price: number
  fee: number
  tax?: number
  date: string
  createdAt: string
}

export interface PortfolioPosition {
  symbol: string
  name: string
  shares: number
  costBasis: number
  averageCost: number
  realizedPnl: number
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
}

export function todayTaipeiDate() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date())
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
    && typeof item.createdAt === 'string'
}

export function buildPortfolioPositions(transactions: readonly PortfolioTransaction[]): PortfolioPosition[] {
  const ordered = [...transactions].sort((a, b) => a.date.localeCompare(b.date) || a.createdAt.localeCompare(b.createdAt))
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
      position.realizedPnl += transaction.shares * (transaction.price - position.averageCost) - transaction.fee - (transaction.tax ?? 0)
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
    createdAt: new Date().toISOString(),
  }
  // Validate the dated sequence too, so a backdated sale cannot use shares
  // bought later in the ledger.
  buildPortfolioPositions([...transactions, transaction])
  return transaction
}
