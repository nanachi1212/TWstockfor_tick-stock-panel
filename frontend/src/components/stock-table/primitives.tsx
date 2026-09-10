/**
 * 股票列表單元格渲染原語（共享）。
 *
 * 只負責「無業務上下文」的純數據列：價格/成交/均線/區間/技術指標/動量/連板/財務等。
 * symbol、strategies、score、signals、candle 等需要頁面上下文（加自選按鈕、失效行、
 * kline 數據、信號提取）的列由各頁面的 renderCell 自行處理。
 *
 * 口徑與原 ScreenerTable 對齊（已和自選頁校準過）：amplitude 用 *100、annual_vol/
 * 財務率類用 fmtPct、kdj 用 toFixed(1)、vol_ma 用 fmtBigNum 等。
 */
import type { ReactNode } from 'react'
import { fmtPrice, fmtPct, fmtBigNum, priceColorClass } from '@/lib/format'
import type { ColumnConfig } from '@/lib/list-columns'
import { NUM_CELL_CLASS } from '@/lib/stock-table'

// ===== 板塊標識（自選/策略頁統一口徑） =====

export function boardTag(symbol: string): { label: string; color: string } | null {
  if (/^(300|301)/.test(symbol)) return { label: '創', color: 'text-[#f97316] bg-[#f97316]/12 border-[#f97316]/25' }
  if (/^688/.test(symbol))       return { label: '科', color: 'text-cyan-400 bg-cyan-400/12 border-cyan-400/25' }
  if (/\.BJ$/.test(symbol))      return { label: '北', color: 'text-purple-400 bg-purple-400/12 border-purple-400/25' }
  return null
}

// ===== RSI 指標帶顏色 =====

export function RSIBadge({ value }: { value: number | null | undefined }) {
  if (value == null || Number.isNaN(value)) return <span className="text-muted">—</span>
  let color = 'text-secondary'
  if (value >= 80) color = 'text-danger font-medium'
  else if (value >= 70) color = 'text-bull'
  else if (value <= 20) color = 'text-bear font-medium'
  else if (value <= 30) color = 'text-bear'
  return <span className={`tabular-nums ${color}`}>{value.toFixed(1)}</span>
}

// ===== 純數據列渲染 =====

function fmtMaybePrice(value: any) {
  return value != null && !Number.isNaN(value) ? fmtPrice(value) : '—'
}

/**
 * 渲染一個純數據內置列的 <td>。
 * 返回 null 表示該列不屬於純數據列（symbol/strategies/score/signals/candle 等），由調用方處理。
 */
export function renderBuiltinDataCell(r: any, col: ColumnConfig): ReactNode | null {
  if (col.source.type !== 'builtin') return null
  const key = col.source.key

  // 這些列需要業務上下文，不在此處理
  if (key === 'symbol' || key === 'strategies' || key === 'score' || key === 'signals' || key === 'candle') {
    return null
  }

  const numCls = `${alignTdClass(col.align)} ${NUM_CELL_CLASS}`

  switch (key) {
    // 價格
    case 'price':
      return <td key={col.id} className={`${numCls} text-secondary`}>{fmtMaybePrice(r.close)}</td>
    case 'pct':
      return <td key={col.id} className={`${numCls} font-medium ${priceColorClass(r.change_pct)}`}>{fmtPct(r.change_pct)}</td>
    case 'change_amount':
      return <td key={col.id} className={`${numCls} ${priceColorClass(r.change_amount)}`}>{r.change_amount != null ? fmtPrice(r.change_amount) : '—'}</td>
    case 'amplitude':
      return <td key={col.id} className={numCls}>{r.amplitude != null ? `${(r.amplitude * 100).toFixed(2)}%` : '—'}</td>
    // 成交
    case 'turnover':
      return <td key={col.id} className={numCls}>{r.turnover_rate != null ? `${Number(r.turnover_rate).toFixed(2)}%` : '—'}</td>
    case 'amount':
      return <td key={col.id} className={`${numCls} text-secondary`}>{fmtBigNum(r.amount)}</td>
    case 'float_val':
      return <td key={col.id} className={`${numCls} text-secondary`}>{r.float_shares && r.close ? fmtBigNum(r.float_shares * r.close) : '—'}</td>
    case 'vol_ratio':
      return (
        <td key={col.id} className={numCls}>
          <span className={r.vol_ratio_5d >= 2 ? 'text-accent font-medium' : ''}>
            {fmtMaybePrice(r.vol_ratio_5d)}
          </span>
        </td>
      )
    case 'annual_vol':
      return <td key={col.id} className={numCls}>{r.annual_vol_20d != null ? fmtPct(r.annual_vol_20d) : '—'}</td>
    // 均線
    case 'ma5':  return <td key={col.id} className={numCls}>{fmtMaybePrice(r.ma5)}</td>
    case 'ma10': return <td key={col.id} className={numCls}>{fmtMaybePrice(r.ma10)}</td>
    case 'ma20': return <td key={col.id} className={numCls}>{fmtMaybePrice(r.ma20)}</td>
    case 'ma60': return <td key={col.id} className={numCls}>{fmtMaybePrice(r.ma60)}</td>
    // 區間
    case 'high_60d': return <td key={col.id} className={numCls}>{r.high_60d != null ? fmtPrice(r.high_60d) : '—'}</td>
    case 'low_60d':  return <td key={col.id} className={numCls}>{r.low_60d != null ? fmtPrice(r.low_60d) : '—'}</td>
    // 技術指標
    case 'rsi6':  return <td key={col.id} className={numCls}><RSIBadge value={r.rsi_6} /></td>
    case 'rsi14': return <td key={col.id} className={numCls}><RSIBadge value={r.rsi_14} /></td>
    case 'rsi24': return <td key={col.id} className={numCls}><RSIBadge value={r.rsi_24} /></td>
    case 'macd_dif':
      return <td key={col.id} className={`${numCls} ${priceColorClass(r.macd_hist)}`}>{r.macd_dif != null ? fmtPrice(r.macd_dif) : '—'}</td>
    case 'macd_dea':
      return <td key={col.id} className={numCls}>{r.macd_dea != null ? fmtPrice(r.macd_dea) : '—'}</td>
    case 'macd_hist':
      return <td key={col.id} className={`${numCls} ${priceColorClass(r.macd_hist)}`}>{r.macd_hist != null ? fmtPrice(r.macd_hist) : '—'}</td>
    case 'kdj_k': return <td key={col.id} className={numCls}>{r.kdj_k != null ? r.kdj_k.toFixed(1) : '—'}</td>
    case 'kdj_d': return <td key={col.id} className={numCls}>{r.kdj_d != null ? r.kdj_d.toFixed(1) : '—'}</td>
    case 'kdj_j': return <td key={col.id} className={numCls}>{r.kdj_j != null ? r.kdj_j.toFixed(1) : '—'}</td>
    case 'boll_upper': return <td key={col.id} className={numCls}>{r.boll_upper != null ? fmtPrice(r.boll_upper) : '—'}</td>
    case 'boll_lower': return <td key={col.id} className={numCls}>{r.boll_lower != null ? fmtPrice(r.boll_lower) : '—'}</td>
    case 'atr14':    return <td key={col.id} className={numCls}>{r.atr_14 != null ? fmtPrice(r.atr_14) : '—'}</td>
    case 'vol_ma5':  return <td key={col.id} className={numCls}>{r.vol_ma5 != null ? fmtBigNum(r.vol_ma5) : '—'}</td>
    case 'vol_ma10': return <td key={col.id} className={numCls}>{r.vol_ma10 != null ? fmtBigNum(r.vol_ma10) : '—'}</td>
    // 動量
    case 'momentum_5d':  return <td key={col.id} className={`${numCls} ${priceColorClass(r.momentum_5d)}`}>{fmtPct(r.momentum_5d)}</td>
    case 'momentum_10d': return <td key={col.id} className={`${numCls} ${priceColorClass(r.momentum_10d)}`}>{fmtPct(r.momentum_10d)}</td>
    case 'momentum_20d': return <td key={col.id} className={`${numCls} ${priceColorClass(r.momentum_20d)}`}>{fmtPct(r.momentum_20d)}</td>
    case 'momentum_30d': return <td key={col.id} className={`${numCls} ${priceColorClass(r.momentum_30d)}`}>{fmtPct(r.momentum_30d)}</td>
    case 'momentum_60d': return <td key={col.id} className={`${numCls} ${priceColorClass(r.momentum_60d)}`}>{fmtPct(r.momentum_60d)}</td>
    case 'deviate_3d':  return <td key={col.id} className={`${numCls} ${priceColorClass(r.deviate_3d)}`} title="偏離值 = 個股3日漲跌幅 − 對應指數 (主板±20%/創業科創±30%/北交所±40% 觸發)">{fmtPct(r.deviate_3d)}</td>
    case 'deviate_10d': return <td key={col.id} className={`${numCls} ${priceColorClass(r.deviate_10d)}`} title="10日累計偏離 (+100% 觸發嚴重異常波動)">{fmtPct(r.deviate_10d)}</td>
    case 'deviate_30d': return <td key={col.id} className={`${numCls} ${priceColorClass(r.deviate_30d)}`} title="30日累計偏離 (+200% 觸發嚴重異常波動)">{fmtPct(r.deviate_30d)}</td>
    // 連板
    case 'limit_ups':
      return (
        <td key={col.id} className="px-3 py-2 text-center">
          {r.consecutive_limit_ups > 0 ? (
            <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1 rounded bg-danger/15 text-danger text-xs font-bold tabular-nums">
              {r.consecutive_limit_ups}
            </span>
          ) : (
            <span className="text-muted">—</span>
          )}
        </td>
      )
    case 'limit_downs':
      return (
        <td key={col.id} className="px-3 py-2 text-center">
          {r.consecutive_limit_downs > 0 ? (
            <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1 rounded bg-bear/15 text-bear text-xs font-bold tabular-nums">
              {r.consecutive_limit_downs}
            </span>
          ) : (
            <span className="text-muted">—</span>
          )}
        </td>
      )
    // 財務指標（後端 enriched 未返回時顯示 —）
    case 'eps':           return <td key={col.id} className={numCls}>{r.eps != null ? fmtPrice(r.eps) : '—'}</td>
    case 'bps':           return <td key={col.id} className={numCls}>{r.bps != null ? fmtPrice(r.bps) : '—'}</td>
    case 'roe':           return <td key={col.id} className={numCls}>{r.roe != null ? fmtPct(r.roe) : '—'}</td>
    case 'pe_ttm':        return <td key={col.id} className={numCls}>{r.pe_ttm != null ? fmtPrice(r.pe_ttm) : '—'}</td>
    case 'pb':            return <td key={col.id} className={numCls}>{r.pb != null ? fmtPrice(r.pb) : '—'}</td>
    case 'gross_margin':  return <td key={col.id} className={numCls}>{r.gross_margin != null ? fmtPct(r.gross_margin) : '—'}</td>
    case 'net_margin':    return <td key={col.id} className={numCls}>{r.net_margin != null ? fmtPct(r.net_margin) : '—'}</td>
    case 'revenue_yoy':   return <td key={col.id} className={numCls}>{r.revenue_yoy != null ? fmtPct(r.revenue_yoy) : '—'}</td>
    case 'net_income_yoy':return <td key={col.id} className={numCls}>{r.net_income_yoy != null ? fmtPct(r.net_income_yoy) : '—'}</td>
    case 'debt_ratio':    return <td key={col.id} className={numCls}>{r.debt_ratio != null ? fmtPct(r.debt_ratio) : '—'}</td>
    default:
      return <td key={col.id} className={`${alignTdClass(col.align)} text-muted`}>—</td>
  }
}

/** 根據列對齊返回 td 基礎 class（不含 num 樣式） */
function alignTdClass(align: ColumnConfig['align']): string {
  if (align === 'right') return 'px-3 py-2 text-right'
  if (align === 'center') return 'px-3 py-2 text-center'
  return 'px-3 py-2'
}
