import { useState, type ReactNode } from 'react'
import { Settings2, RadioTower, Star } from 'lucide-react'
import type { KlineRow, FinancialMetricRecord } from '@/lib/api'
import { fmtPrice, fmtBigNum, fmtVolume } from '@/lib/format'
import { ListColumnCustomizer } from '@/components/ListColumnCustomizer'
import { WatchlistAddMenu } from '@/components/WatchlistAddMenu'
import { INFO_GROUPS, type ColumnConfig } from '@/lib/stock-info-fields'

const BULL = '#C74040'
const BEAR = '#2D9B65'

interface Props {
  symbol: string
  name?: string
  stockInfo?: { name?: string; total_shares?: number; float_shares?: number; ext?: Record<string, unknown> }
  rows: KlineRow[]
  /** 信息條字段配置（由 StockPanel 提升，受控） */
  fields: ColumnConfig[]
  onFieldsChange: (fields: ColumnConfig[]) => void
  /** 財務指標最新一期（來自 useFinancialMetrics，受 Cap.FINANCIAL 門控） */
  financialMetrics?: FinancialMetricRecord
  /** 加監控回調 (個股彈窗傳入, 有值時渲染 RadioTower 圖標) */
  onMonitor?: () => void
  /** 自選狀態與操作（傳入對應回調時渲染 Star 圖標） */
  inWatchlist?: boolean
  onAddToWatchlist?: (groupId: string | null) => void
  onRemoveFromWatchlist?: () => void
  watchlistPending?: boolean
}

/**
 * 精簡渲染擴展數據值（信息條專用）。
 * 仍尊重 extDisplay 配置：text=純文本，tag(默認)=按分隔符拆成小標籤 + maxTags 截斷。
 * 與自選列表的差異：標籤模式無 maxWidth/排列方向，但保留 +N 展開交互。
 */
function renderExtInline(
  val: unknown,
  col: ColumnConfig,
  expanded: boolean,
  onToggle: () => void,
): ReactNode {
  if (val == null || (typeof val === 'number' && Number.isNaN(val))) {
    return <span className="text-muted">—</span>
  }
  if (typeof val === 'number') {
    const displayVal = Number.isInteger(val) ? fmtPrice(val, 0) : fmtPrice(val)
    return <span className="tabular-nums">{displayVal}</span>
  }
  if (typeof val === 'boolean') {
    return <span className={val ? 'text-bull' : 'text-muted'}>{val ? '是' : '否'}</span>
  }
  const str = String(val)
  // 純文本模式
  if (col.extDisplay?.displayMode === 'text') {
    return <span>{str}</span>
  }
  // 標籤模式（默認）：按分隔符拆成小標籤
  const sep = col.extDisplay?.separator?.trim() || null
  const tags = sep
    ? str.split(sep).map(s => s.trim()).filter(Boolean)
    : str.split(/[、,，;；\-]/).map(s => s.trim()).filter(Boolean)
  if (tags.length === 0) return <span className="text-muted">—</span>
  // maxTags 截斷 + 展開交互：收起時顯示前 N 個 + +N，展開時顯示全部 + 收起
  const maxTags = col.extDisplay?.maxTags ?? 0
  const hiddenIndices = maxTags > 0 ? col.extDisplay?.hiddenIndices : undefined
  const showAll = maxTags <= 0 || expanded
  const sliced = showAll ? tags : tags.slice(0, maxTags)
  const shown = hiddenIndices?.length ? sliced.filter((_, i) => !hiddenIndices.includes(i)) : sliced
  const overflow = tags.length - shown.length
  return (
    <span className="inline-flex flex-wrap items-center gap-0.5">
      {shown.map((tag, i) => (
        <span key={i} className="inline-block px-1 rounded text-[10px] leading-tight text-yellow-500 bg-yellow-500/10">
          {tag}
        </span>
      ))}
      {!showAll && overflow > 0 && (
        <button
          onClick={onToggle}
          className="inline-block px-1 rounded text-[10px] leading-tight text-accent bg-accent/10 hover:bg-accent/20 transition-colors"
        >
          +{overflow}
        </button>
      )}
      {showAll && maxTags > 0 && tags.length > maxTags && (
        <button
          onClick={onToggle}
          className="inline-block px-1 rounded text-[10px] leading-tight text-muted hover:text-foreground transition-colors"
        >
          收起
        </button>
      )}
    </span>
  )
}

export function StockInfoBar({
  symbol,
  name,
  stockInfo,
  rows,
  fields,
  onFieldsChange,
  financialMetrics,
  onMonitor,
  inWatchlist,
  onAddToWatchlist,
  onRemoveFromWatchlist,
  watchlistPending,
}: Props) {
  // 彈窗開關：純本地狀態，與數據/配置無關，放早期 return 之前
  const [customizerOpen, setCustomizerOpen] = useState(false)
  // ext 標籤展開狀態：按 symbol::colId，切股/切字段時互不干擾
  const [expandedExt, setExpandedExt] = useState<Set<string>>(new Set())

  const toggleExtExpand = (key: string) => {
    setExpandedExt(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (rows.length === 0) return null

  const latest = rows[rows.length - 1]
  const prev = rows.length >= 2 ? rows[rows.length - 2] : null
  const close = Number(latest.close)
  const chg = prev ? close - Number(prev.close) : 0
  const chgPct = prev ? chg / Number(prev.close) * 100 : 0
  const isUp = chg >= 0
  const clr = isUp ? BULL : BEAR

  const totalShares = stockInfo?.total_shares
  const floatShares = stockInfo?.float_shares
  const marketCap = totalShares ? close * totalShares : null
  const floatMarketCap = floatShares ? close * floatShares : null
  const turnoverRate = floatShares && latest.volume
    ? (Number(latest.volume) * 100 / floatShares * 100)
    : null

  const displayName = stockInfo?.name ?? name ?? ''
  const extData = stockInfo?.ext ?? {}

  // 按指標 key 計算格式化值，無數據返回 null（渲染時跳過，與原行為一致）。
  // 普通函數：依賴行情值每次 render 都變，useCallback 無收益；且必須定義在早期 return 之後。
  const computeBuiltinValue = (key: string): string | null => {
    switch (key) {
      case 'market_cap':       return marketCap != null ? fmtBigNum(marketCap) : null
      case 'float_market_cap': return floatMarketCap != null ? fmtBigNum(floatMarketCap) : null
      case 'turnover':         return turnoverRate != null ? `${turnoverRate.toFixed(2)}%` : null
      case 'volume':           return latest.volume != null ? fmtVolume(Number(latest.volume)) : null
      case 'amplitude': {
        const prevClose = prev ? Number(prev.close) : null
        if (prevClose == null || prevClose === 0) return null
        const hi = Number(latest.high)
        const lo = Number(latest.low)
        return `${((hi - lo) / prevClose * 100).toFixed(2)}%`
      }
      case 'open': return fmtPrice(Number(latest.open))
      case 'high': return fmtPrice(Number(latest.high))
      case 'low':  return fmtPrice(Number(latest.low))
      // 財務指標：百分比字段存儲為百分點(12.3 表示 12.3%)，直接 toFixed(2) + %
      case 'eps':         return financialMetrics?.eps_basic != null ? fmtPrice(financialMetrics.eps_basic) : null
      case 'bps':         return financialMetrics?.bps != null ? fmtPrice(financialMetrics.bps) : null
      case 'roe':         return financialMetrics?.roe != null ? `${financialMetrics.roe.toFixed(2)}%` : null
      case 'gross_margin':return financialMetrics?.gross_margin != null ? `${financialMetrics.gross_margin.toFixed(2)}%` : null
      case 'net_margin':  return financialMetrics?.net_margin != null ? `${financialMetrics.net_margin.toFixed(2)}%` : null
      case 'debt_ratio':  return financialMetrics?.debt_to_asset_ratio != null ? `${financialMetrics.debt_to_asset_ratio.toFixed(2)}%` : null
      case 'revenue_yoy': return financialMetrics?.revenue_yoy != null ? `${financialMetrics.revenue_yoy.toFixed(2)}%` : null
      case 'net_income_yoy': return financialMetrics?.net_income_yoy != null ? `${financialMetrics.net_income_yoy.toFixed(2)}%` : null
      // PE/PB 後端無此字段，用現價現算（PE 基於最新一期 EPS，非嚴格 TTM）
      case 'pe_ttm': {
        const eps = financialMetrics?.eps_basic
        return eps && eps !== 0 ? fmtPrice(close / eps) : null
      }
      case 'pb': {
        const bps = financialMetrics?.bps
        return bps && bps !== 0 ? fmtPrice(close / bps) : null
      }
      default: return null
    }
  }

  const visibleFields = fields.filter(f => f.visible)
  // 按是否單獨顯示分組：普通列共一行，standalone 列各佔一行
  const inlineFields = visibleFields.filter(f => !f.standalone)
  const standaloneFields = visibleFields.filter(f => f.standalone)

  // 渲染單個字段（builtin / ext 通用）
  const renderField = (f: ColumnConfig): ReactNode => {
    if (f.source.type === 'ext') {
      const { configId, fieldName } = f.source
      const val = extData[`${configId}__${fieldName}`]
      // 無值的 ext 字段整體跳過（與 builtin 無數據行為一致）
      if (val == null || (typeof val === 'number' && Number.isNaN(val))) return null
      const cellKey = `${symbol}::${f.id}`
      return (
        <span key={f.id} className="inline-flex items-center gap-1">
          <span>{f.label}</span>
          <span className="text-secondary">
            {renderExtInline(val, f, expandedExt.has(cellKey), () => toggleExtExpand(cellKey))}
          </span>
        </span>
      )
    }
    // builtin
    const value = computeBuiltinValue(f.source.type === 'builtin' ? f.source.key : '')
    if (value == null) return null
    return (
      <span key={f.id}>
        {f.label} <span className="text-secondary">{value}</span>
      </span>
    )
  }

  return (
    <div className="px-2 pb-3 font-mono text-[12px] select-none space-y-1">
      {/* Row 1: code, name, price, change, change% */}
      <div className="flex items-baseline gap-x-3 flex-wrap">
        <span className="text-foreground font-bold text-sm tracking-wide">{symbol}</span>
        <span className="text-secondary font-medium">{displayName}</span>
        <span style={{ color: clr }} className="text-lg font-bold tabular-nums">
          {fmtPrice(close)}
        </span>
        <span style={{ color: clr }} className="tabular-nums">
          {isUp ? '+' : ''}{fmtPrice(chg)}
        </span>
        <span style={{ color: clr }} className="tabular-nums">
          {isUp ? '+' : ''}{fmtPrice(chgPct)}%
        </span>
        {/* 右側操作按鈕：加自選 + 加監控 + 信息條配置 */}
        <div className="ml-auto self-center flex items-center gap-1">
          {inWatchlist && onRemoveFromWatchlist ? (
            <button
              type="button"
              onClick={onRemoveFromWatchlist}
              disabled={watchlistPending}
              className="rounded-btn p-1 text-[#FACC15] transition-colors cursor-pointer hover:bg-elevated disabled:opacity-50"
              title="移出自選"
              aria-label={`將 ${symbol} 移出自選`}
            >
              <Star className="h-3.5 w-3.5" />
            </button>
          ) : !inWatchlist && onAddToWatchlist ? (
            <WatchlistAddMenu
              onSelect={onAddToWatchlist}
              disabled={watchlistPending}
              triggerClassName="rounded-btn p-1 text-muted transition-colors cursor-pointer hover:bg-elevated hover:text-foreground disabled:opacity-50"
              ariaLabel={`將 ${symbol} 加入自選`}
            >
              <Star className="h-3.5 w-3.5" />
            </WatchlistAddMenu>
          ) : null}
          {onMonitor && (
            <button
              onClick={onMonitor}
              className="p-1 rounded-btn text-amber-400 hover:bg-amber-400/10 transition-colors cursor-pointer"
              title="加監控"
            >
              <RadioTower className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            onClick={() => setCustomizerOpen(true)}
            className="p-1 rounded-btn text-muted hover:text-foreground hover:bg-elevated transition-colors"
            title="自訂資訊條"
          >
            <Settings2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Row 2: 普通指標（builtin + ext，共一行 flex-wrap） */}
      {inlineFields.length > 0 && (
        <div className="flex items-center gap-x-4 gap-y-1 text-[11px] flex-wrap text-muted">
          {inlineFields.map(renderField)}
        </div>
      )}

      {/* 單獨顯示的指標：各佔一行 */}
      {standaloneFields.map(f => {
        const node = renderField(f)
        if (node == null) return null
        return (
          <div key={f.id} className="flex items-center gap-x-4 text-[11px] flex-wrap text-muted">
            {node}
          </div>
        )
      })}

      <ListColumnCustomizer
        columns={fields}
        groups={INFO_GROUPS}
        onChange={onFieldsChange}
        open={customizerOpen}
        onClose={() => setCustomizerOpen(false)}
        title="資訊條指標"
        builtinSectionLabel="可選指標"
        extColumnAlign="left"
        showStandaloneToggle
      />
    </div>
  )
}
