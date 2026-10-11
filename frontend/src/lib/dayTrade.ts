// 適合當沖: 只看「好進好出、今天有波動」的客觀條件，不是買賣建議。
// 處置股需預收款券、不能當沖，所以風險未知時一律不標示。
export const DAY_TRADE_RULES = {
  minAmount: 500_000_000, // 成交金額 ≥ 5 億: 流動性夠，進出不卡
  minVolRatio: 1.5, // 今日量 ≥ 5 日均量 1.5 倍: 有人在交易
  minMovePct: 0.02, // 漲跌幅 ≥ 2%: 有足夠價差空間
  minPrice: 10, // 股價 ≥ 10 元: 避免一檔跳動占比過大
} as const

export interface DayTradeInput {
  close?: number | null
  change_pct?: number | null
  amount?: number | null
  vol_ratio_5d?: number | null
  risk_status?: 'clear' | 'unknown' | null
}

export function dayTradeFit(item: DayTradeInput): { fit: boolean; reason: string } {
  const r = DAY_TRADE_RULES
  if (item.risk_status === 'unknown') return { fit: false, reason: '事件風險未知（可能是處置股）' }
  if (item.amount == null || item.amount < r.minAmount) return { fit: false, reason: '成交金額不到 5 億，流動性不足' }
  if (item.vol_ratio_5d == null || item.vol_ratio_5d < r.minVolRatio) return { fit: false, reason: '量能沒有放大' }
  if (item.change_pct == null || Math.abs(item.change_pct) < r.minMovePct) return { fit: false, reason: '今日波動小於 2%' }
  if (item.close == null || item.close < r.minPrice) return { fit: false, reason: '股價低於 10 元' }
  return { fit: true, reason: '成交金額 ≥ 5 億、量比 ≥ 1.5、波動 ≥ 2%：好進好出且有價差。當沖風險高，請設停損。' }
}
