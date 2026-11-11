/**
 * 股票列表共享邏輯(無 JSX)：訊號提取、排序取值、不可排序列集合。
 *
 * 自選頁與策略頁共用，避免兩邊各維護一份導致口徑漂移（歷史上策略頁 14 個訊號、
 * 自選頁 13 個，缺一個均線黃金交叉/死亡交叉變體）。
 */
import type { ColumnConfig } from '@/lib/list-columns'
import type { MinuteKlineRow } from '@/lib/api'

// ===== 訊號 =====

export type SignalType = 'bull' | 'bear' | 'neutral'

export interface Signal {
  label: string
  type: SignalType
}

/** 訊號欄位定義：[行欄位名, 展示標籤, 訊號類型] */
export const SIGNAL_FIELDS: [string, string, SignalType][] = [
  ['signal_limit_up', '漲停', 'bull'],
  ['signal_volume_surge', '放量', 'neutral'],
  ['signal_ma_golden_5_20', 'MA黃金交叉', 'bull'],
  ['signal_ma_dead_5_20', 'MA死亡交叉', 'bear'],
  ['signal_ma_golden_20_60', '均線黃金交叉', 'bull'],
  ['signal_macd_golden', 'MACD黃金交叉', 'bull'],
  ['signal_macd_dead', 'MACD死亡交叉', 'bear'],
  ['signal_ma20_breakout', '站上MA20', 'bull'],
  ['signal_ma20_breakdown', '跌破MA20', 'bear'],
  ['signal_n_day_high', '60日新高', 'bull'],
  ['signal_n_day_low', '60日新低', 'bear'],
  ['signal_boll_breakout_upper', '布林突破', 'bull'],
  ['signal_boll_breakdown_lower', '布林下破', 'bear'],
]

/** 從一行資料中提取已命中的訊號列表 */
export function getSignals(r: Record<string, any>): Signal[] {
  return SIGNAL_FIELDS.filter(([key]) => r[key]).map(([, label, type]) => ({ label, type }))
}

/** 訊號類型 → tailwind 顏色類 */
export function signalCls(type: SignalType): string {
  if (type === 'bull') return 'text-bull bg-bull/10'
  if (type === 'bear') return 'text-bear bg-bear/10'
  return 'text-accent bg-accent/10'
}

// ===== 排序 =====

/** 不可參與數值/文字排序的內建列 key（渲染為標籤/圖表，無單一標量值） */
export const UNSORTABLE_KEYS = new Set(['signals', 'candle', 'intraday', 'strategies'])

/**
 * 取一列在某行上的排序標量值。builtin 列按 key 映射到行欄位；ext 列走
 * `${configId}__${fieldName}`；不可排序列返回 null。
 */
export function getSortValue(r: any, col: ColumnConfig): any {
  if (col.source.type === 'ext') {
    return r[`${col.source.configId}__${col.source.fieldName}`]
  }
  const key = col.source.key
  switch (key) {
    case 'symbol':        return r.symbol
    case 'price':         return r.rt_price ?? r.close
    case 'pct':           return r.rt_pct ?? r.change_pct
    case 'change_amount': return r.change_amount
    case 'amplitude':     return r.amplitude
    case 'turnover':      return r.turnover_rate
    case 'amount':        return r.rt_amount ?? r.amount
    case 'float_val':     return r.float_shares && (r.rt_price ?? r.close) ? r.float_shares * (r.rt_price ?? r.close) : null
    case 'vol_ratio':     return r.vol_ratio_5d
    case 'annual_vol':    return r.annual_vol_20d
    case 'ma5':           return r.ma5
    case 'ma10':          return r.ma10
    case 'ma20':          return r.ma20
    case 'ma60':          return r.ma60
    case 'high_60d':      return r.high_60d
    case 'low_60d':       return r.low_60d
    case 'rsi6':          return r.rsi_6
    case 'rsi14':         return r.rsi_14
    case 'rsi24':         return r.rsi_24
    case 'macd_dif':      return r.macd_dif
    case 'macd_dea':      return r.macd_dea
    case 'macd_hist':     return r.macd_hist
    case 'kdj_k':         return r.kdj_k
    case 'kdj_d':         return r.kdj_d
    case 'kdj_j':         return r.kdj_j
    case 'boll_upper':    return r.boll_upper
    case 'boll_lower':    return r.boll_lower
    case 'atr14':         return r.atr_14
    case 'vol_ma5':       return r.vol_ma5
    case 'vol_ma10':      return r.vol_ma10
    case 'momentum_5d':   return r.momentum_5d
    case 'momentum_10d':  return r.momentum_10d
    case 'momentum_20d':  return r.momentum_20d
    case 'momentum_30d':  return r.momentum_30d
    case 'momentum_60d':  return r.momentum_60d
    case 'score':         return r.score
    default: return null
  }
}

/**
 * 分時列排序標量: 最新一根分鐘收盤相對昨收的漲跌幅 (小數), 與分時圖最後一點同口徑。
 * 無分鐘資料或昨收缺失時返回 null (排序時排在最後)。
 */
export function getIntradaySortValue(r: any, minuteRows: MinuteKlineRow[] | undefined): number | null {
  if (!minuteRows?.length || !r.prev_close) return null
  const last = minuteRows[minuteRows.length - 1]
  return last.close / r.prev_close - 1
}

// ===== 共享樣式 =====

/** 數值儲存格統一樣式（含 tabular-nums 等寬數字） */
export const NUM_CELL_CLASS = 'num tabular-nums'
