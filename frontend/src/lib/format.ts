// 數字 / 價格 / 漲跌幅 格式化(§6.0.2 等寬數字)

export function fmtPrice(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${(v * 100).toFixed(digits)}%`
}

// 台股語義色:紅漲綠跌 → 僅用於價格相關元素
export function priceColorClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-bull' : 'text-bear'
}

export function fmtBigNum(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1_000_000_000_000) return `${(v / 1_000_000_000_000).toFixed(2)}萬億`
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(2)}億`
  if (v >= 10_000) return `${(v / 10_000).toFixed(0)}萬`
  return v.toFixed(0)
}

export function fmtDate(s: string | Date | null | undefined): string {
  if (s == null) return '—'
  const d = typeof s === 'string' ? new Date(s) : s
  if (isNaN(d.getTime())) return String(s)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ===== Data 頁面工具函數 =====

export function formatNumber(n: number): string {
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(1)}億`
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)}萬`
  return n.toLocaleString()
}

export function isToday(iso: string): boolean {
  const d = new Date(iso)
  const now = new Date()
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate()
}

/**
 * 擴展資料列數字格式化 — 千分位逗號 + 單位換算 + 小數位。
 * 供自選/策略列表的擴展資料 number 單元格統一呼叫。
 *
 * opts:
 *   - thousandSeparator: true 則加英文逗號(1,234,567)
 *   - unitConvert: 'none'(預設不換算) / 'wan'(÷1e4 加"萬") / 'yi'(÷1e8 加"億") / 'auto'(≥1e8用億, ≥1e4用萬)
 *   - unitDecimals: 換算後保留小數位(預設 2); unitConvert=none 時此值僅控制原值小數位
 */
export function formatExtNumber(
  val: number,
  opts: { thousandSeparator?: boolean; unitConvert?: 'none' | 'wan' | 'yi' | 'auto'; unitDecimals?: number } = {},
): string {
  const { thousandSeparator, unitConvert = 'none', unitDecimals } = opts
  if (!Number.isFinite(val)) return '—'

  let n = val
  let suffix = ''
  let decimals = unitDecimals ?? 2

  if (unitConvert === 'wan') {
    n = val / 1e4
    suffix = '萬'
  } else if (unitConvert === 'yi') {
    n = val / 1e8
    suffix = '億'
  } else if (unitConvert === 'auto') {
    const abs = Math.abs(val)
    if (abs >= 1e8) { n = val / 1e8; suffix = '億' }
    else if (abs >= 1e4) { n = val / 1e4; suffix = '萬' }
    else { decimals = unitDecimals ?? 0 }
  } else {
    // none: 整數不帶小數, 小數保留原精度(最多 4 位去尾零)
    decimals = unitDecimals ?? (Number.isInteger(val) ? 0 : 4)
  }

  // toFixed 後去尾零(none 模式下小數原精度場景)
  let str = n.toFixed(decimals)
  if (unitConvert === 'none' && !unitDecimals && !Number.isInteger(val)) {
    // 去掉 toFixed(4) 產生的尾零, 如 1.2300 → 1.23
    str = String(Number(str))
  }

  // 千分位逗號(僅整數部分)
  if (thousandSeparator) {
    const [intPart, fracPart] = str.split('.')
    const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
    str = fracPart != null ? `${grouped}.${fracPart}` : grouped
  }

  return str + suffix
}
