// 數字 / 價格 / 漲跌幅 / 台北時間 格式化。全站只有這一份實作 (§6.0.2 等寬數字)。

type Num = number | null | undefined

function isNum(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

export function fmtPrice(v: Num, digits = 2): string {
  if (!isNum(v)) return '—'
  return v.toFixed(digits)
}

/** 百分比,輸入是小數 (0.0366 = 3.66%)。signed=true 時正數加 +(漲跌幅用);命中率等比率傳 false。 */
export function fmtPct(v: Num, digits = 2, signed = true): string {
  if (!isNum(v)) return '—'
  return `${signed && v > 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`
}

/** 百分比,輸入已經是百分點 (3.66 = 3.66%)。 */
export function fmtPctPoint(v: Num, digits = 2, signed = true): string {
  if (!isNum(v)) return '—'
  return `${signed && v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

// 台股語義色:紅漲綠跌 → 僅用於價格相關元素。平盤或缺值一律中性色。
export function priceColorClass(v: Num): string {
  if (!isNum(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-bull' : 'text-bear'
}

/**
 * 股數轉張 (1 張 = 1000 股),四捨五入到整張。signed=true 時正數加 +。
 * 帶單位時,不足一張的零股直接顯示股數,避免 300 股變成「0 張」。
 */
export function fmtLots(shares: Num, signed = false, unit = true): string {
  if (!isNum(shares)) return '—'
  if (unit && shares !== 0 && Math.abs(shares) < 1000) {
    return `${signed && shares > 0 ? '+' : ''}${shares.toLocaleString('zh-TW')} 股`
  }
  const lots = Math.round(shares / 1000)
  const text = lots.toLocaleString('zh-TW')
  return `${signed && lots > 0 ? '+' : ''}${text}${unit ? ' 張' : ''}`
}

/** 金額縮寫:兆 / 億 / 萬。wanDigits 控制「萬」的小數位數,億固定 2 位。 */
export function fmtBigNum(v: Num, wanDigits = 0): string {
  if (!isNum(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1_000_000_000_000) return `${(v / 1_000_000_000_000).toFixed(2)}兆`
  if (abs >= 100_000_000) return `${(v / 100_000_000).toFixed(2)}億`
  if (abs >= 10_000) return `${(v / 10_000).toFixed(wanDigits)}萬`
  return v.toFixed(0)
}

// ===== 台北時間 (Asia/Taipei) — 全站唯一的時區定義 =====

export const TAIPEI_TZ = 'Asia/Taipei'

type DateInput = string | number | Date | null | undefined

function toDate(v: DateInput): Date | null {
  if (v == null || v === '') return null
  const d = v instanceof Date ? v : new Date(v)
  return Number.isNaN(d.getTime()) ? null : d
}

/** 台北時間的 YYYY-MM-DD。缺值或無法解析回傳 null (不會退回「現在」)。 */
export function taipeiDate(v: DateInput): string | null {
  const d = toDate(v)
  return d ? d.toLocaleDateString('sv-SE', { timeZone: TAIPEI_TZ }) : null
}

/** 台北「今天」的 YYYY-MM-DD。 */
export function todayTaipei(): string {
  return new Date().toLocaleDateString('sv-SE', { timeZone: TAIPEI_TZ })
}

/** 台北「現在」的 HH:mm。 */
export function nowTaipeiTime(): string {
  return new Date().toLocaleTimeString('sv-SE', { timeZone: TAIPEI_TZ, hourCycle: 'h23' }).slice(0, 5)
}

/** 台北時間的 HH:mm[:ss]。缺值或無法解析回傳 null。 */
export function taipeiTime(v: DateInput, seconds = true): string | null {
  const d = toDate(v)
  if (!d) return null
  const text = d.toLocaleTimeString('sv-SE', { timeZone: TAIPEI_TZ, hourCycle: 'h23' })
  return seconds ? text : text.slice(0, 5)
}

/** 台北時間的 YYYY-MM-DD HH:mm[:ss]。無法解析回傳 null。 */
export function taipeiDateTime(v: DateInput, seconds = false): string | null {
  const date = taipeiDate(v)
  const time = taipeiTime(v, seconds)
  return date && time ? `${date} ${time}` : null
}

/** 顯示用:台北時間日期時間,無法解析時回傳 fallback。 */
export function fmtTaipeiDateTime(v: DateInput, fallback = '—', seconds = false): string {
  return taipeiDateTime(v, seconds) ?? fallback
}

// ===== 擴展資料列 =====

/**
 * 擴展資料列數字格式化 — 千分位逗號 + 單位換算 + 小數位。
 * 供自選/策略列表的擴展資料 number 儲存格統一呼叫。
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
