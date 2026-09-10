// 板塊判斷工具函數

export const BOARDS = ['滬主板', '深主板', '創業板', '科創板', '北交所'] as const
export type BoardType = (typeof BOARDS)[number]

/** 根據股票代碼判斷板塊 */
export function getBoardType(symbol: string): BoardType | null {
  if (/^(300|301)/.test(symbol)) return '創業板'
  if (/^688/.test(symbol)) return '科創板'
  if (/\.BJ$/.test(symbol)) return '北交所'
  if (/^60[0135]/.test(symbol)) return '滬主板'
  if (/^00[012]/.test(symbol)) return '深主板'
  return null
}

/** 板塊簡稱標籤: 主板返回空字符串(不顯示), 創/科/北 等返回簡稱 */
export function boardTag(symbol: string): string {
  const b = getBoardType(symbol)
  if (!b) return ''
  if (b === '滬主板' || b === '深主板') return ''
  if (b === '創業板') return '創'
  if (b === '科創板') return '科'
  if (b === '北交所') return '北'
  return ''
}
