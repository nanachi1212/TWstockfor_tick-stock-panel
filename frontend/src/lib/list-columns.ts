/**
 * 通用列表列配置底座。
 *
 * 業務頁面只負責定義內置列、分組和持久化 adapter；拖拽、顯隱、擴展列參數等
 * 公共能力集中在這裡，避免每個股票列表重複實現。
 */

export type ColumnSource =
  | { type: 'builtin'; key: string }
  | { type: 'ext'; configId: string; fieldName: string; fieldLabel?: string; fieldType?: string }
  | { type: 'computed'; key: string }

/** 擴展列字符串值渲染配置 */
export interface ExtColumnDisplayConfig {
  /** 顯示模式: tag=分隔為標籤, text=純文本 */
  displayMode: 'tag' | 'text'
  /** 自定義分隔符，留空使用默認 [、,，;；-] */
  separator?: string
  /** 列最大寬度 CSS 值，如 "200px" */
  maxWidth?: string
  /** 標籤顯示上限，0 或 undefined=全部顯示 */
  maxTags?: number
  /** 隱藏的標籤索引（0-based），在 maxTags 範圍內按位置隱藏 */
  hiddenIndices?: number[]
  /** 標籤排列方向: horizontal=橫向(默認), vertical=豎向 */
  tagLayout?: 'horizontal' | 'vertical'
  /** 數字千分位逗號(僅 number 類型有效): true=1,234,567 */
  thousandSeparator?: boolean
  /** 單位換算(僅 number 類型有效): none=不換算(默認), wan=萬, yi=億, auto=自動 */
  unitConvert?: 'none' | 'wan' | 'yi' | 'auto'
  /** 單位換算後保留小數位(僅 number 類型 + unitConvert≠none 有效), 默認 2 */
  unitDecimals?: number
}

/** 日k列渲染配置（builtin: candle 列專用） */
export interface CandleColumnConfig {
  /** 開啟（顯示蠟燭圖）時單元格寬度 px */
  enabledWidth?: number
  /** 開啟時單元格高度 px */
  enabledHeight?: number
  /** 關閉（收起）時單元格寬度 px */
  disabledWidth?: number
  /** 關閉時單元格高度 px */
  disabledHeight?: number
  /** 顯示最近多少個交易日的日k */
  days?: number
}

/** 日k列配置默認值（與改造前 MiniCandlestick 硬編碼值一致） */
export const DEFAULT_CANDLE_CONFIG: Required<CandleColumnConfig> = {
  enabledWidth: 100,
  enabledHeight: 80,
  disabledWidth: 40,
  disabledHeight: 40,
  days: 12,
}

/** 分時列渲染配置（builtin: intraday 列專用） */
export interface IntradayColumnConfig {
  /** 單元格寬度 px */
  width?: number
  /** 單元格高度 px */
  height?: number
}

/** 分時列配置默認值 */
export const DEFAULT_INTRADAY_CONFIG: Required<IntradayColumnConfig> = {
  width: 150,
  height: 80,
}

/** 分時列數值邊界 */
const INTRADAY_BOUNDS = {
  width:  { min: 60, max: 300 },
  height: { min: 32, max: 200 },
} as const

export function resolveIntradayConfig(cfg: IntradayColumnConfig | undefined): Required<IntradayColumnConfig> {
  const c = cfg ?? {}
  return {
    width:  clampNum(c.width,  INTRADAY_BOUNDS.width,  DEFAULT_INTRADAY_CONFIG.width),
    height: clampNum(c.height, INTRADAY_BOUNDS.height, DEFAULT_INTRADAY_CONFIG.height),
  }
}

/** 數值邊界（設置過大取上限，過小取最小值） */
const CANDLE_BOUNDS = {
  enabledWidth:  { min: 40,  max: 300 },
  enabledHeight: { min: 32,  max: 200 },
  disabledWidth: { min: 20,  max: 200 },
  disabledHeight:{ min: 20,  max: 200 },
  days:          { min: 1,   max: 60 },
} as const

function clampNum(v: unknown, bounds: { min: number; max: number }, fallback: number): number {
  const n = typeof v === 'number' && Number.isFinite(v) ? v : fallback
  return Math.min(bounds.max, Math.max(bounds.min, n))
}

/**
 * 合併用戶配置與默認值，並對越界數值做鉗制（過大取上限，過小取最小值）。
 * 返回字段齊全的配置，調用方可直接解構使用。
 */
export function resolveCandleConfig(cfg: CandleColumnConfig | undefined): Required<CandleColumnConfig> {
  const c = cfg ?? {}
  return {
    enabledWidth:   clampNum(c.enabledWidth,    CANDLE_BOUNDS.enabledWidth,    DEFAULT_CANDLE_CONFIG.enabledWidth),
    enabledHeight:  clampNum(c.enabledHeight,   CANDLE_BOUNDS.enabledHeight,   DEFAULT_CANDLE_CONFIG.enabledHeight),
    disabledWidth:  clampNum(c.disabledWidth,   CANDLE_BOUNDS.disabledWidth,   DEFAULT_CANDLE_CONFIG.disabledWidth),
    disabledHeight: clampNum(c.disabledHeight,  CANDLE_BOUNDS.disabledHeight,  DEFAULT_CANDLE_CONFIG.disabledHeight),
    days:           clampNum(c.days,            CANDLE_BOUNDS.days,            DEFAULT_CANDLE_CONFIG.days),
  }
}

export interface ColumnConfig {
  id: string        // 唯一標識，如 "builtin:price" 或 "ext:my_table:score"
  source: ColumnSource
  label: string     // 用戶看到的表頭名
  visible: boolean  // 是否顯示
  pinned?: boolean  // 固定列不可隱藏（代碼/名稱、操作）
  align?: 'left' | 'center' | 'right'
  /** 擴展列顯示配置（僅 ext 類型生效） */
  extDisplay?: ExtColumnDisplayConfig
  /** 日k列渲染配置（僅 builtin: candle 列生效） */
  candleConfig?: CandleColumnConfig
  /** 分時列渲染配置（僅 builtin: intraday 列生效） */
  intradayConfig?: IntradayColumnConfig
  /** 信息條場景：是否單獨佔一行顯示（僅 StockInfoBar 生效，表格場景忽略） */
  standalone?: boolean
}

export interface ColumnGroup {
  id: string
  label: string
  icon?: string
  /** builtin/computed source key 列表 */
  keys: string[]
}

export const DEFAULT_ACTION_COLUMN_ID = 'builtin:action'

/** 序列化列配置（只保存用戶可自定義的列，排除 pinned 和 action） */
export function serializeColumns(
  columns: ColumnConfig[],
  actionColumnId = DEFAULT_ACTION_COLUMN_ID,
): ColumnConfig[] {
  return columns.filter(c => !c.pinned && c.id !== actionColumnId)
}

export interface MergeColumnsOptions {
  actionColumnId?: string
  pinnedFirstIds?: string[]
}

/** 合併用戶保存的列與默認列，保留用戶順序並補齊新增默認列。 */
export function mergeColumns(
  saved: ColumnConfig[] | null | undefined,
  defaults: ColumnConfig[],
  options: MergeColumnsOptions = {},
): ColumnConfig[] {
  const actionColumnId = options.actionColumnId ?? DEFAULT_ACTION_COLUMN_ID
  const pinnedFirstIds = options.pinnedFirstIds ?? ['builtin:symbol']
  const normalizedSaved = Array.isArray(saved) ? saved : []
  const result: ColumnConfig[] = []
  const savedMap = new Map(normalizedSaved.map(c => [c.id, c]))
  const defaultMap = new Map(defaults.map(c => [c.id, c]))

  // 1. 按用戶保存順序排列
  for (const col of normalizedSaved) {
    if (!col || col.id === actionColumnId) continue
    const def = defaultMap.get(col.id)
    if (def) {
      // 內置列: label/source/align/pinned 以默認定義為準；visible 使用用戶配置；
      // 用戶自定義的渲染配置（如日k的 candleConfig、分時的 intradayConfig、策略列的 extDisplay、信息條 standalone）需保留，否則刷新後丟失
      result.push({
        ...def,
        visible: col.visible,
        ...(col.candleConfig ? { candleConfig: col.candleConfig } : {}),
        ...(col.intradayConfig ? { intradayConfig: col.intradayConfig } : {}),
        ...(col.extDisplay ? { extDisplay: col.extDisplay } : {}),
        ...(col.standalone ? { standalone: col.standalone } : {}),
      })
    } else if (col.source?.type === 'ext') {
      // ext 列: 保留用戶配置，清理舊 label 中的括號後綴
      let extCol = col
      if (col.label.includes('(') || col.label.includes('（')) {
        extCol = {
          ...col,
          label: col.source.fieldLabel || col.label.replace(/[(（].*/, '').trim() || col.source.fieldName,
        }
      }
      result.push(extCol)
    }
  }

  // 2. 補充新增的默認列
  for (const def of defaults) {
    if (!savedMap.has(def.id)) result.push(def)
  }

  // 3. 固定優先列放到最前，例如代碼/名稱
  for (let i = pinnedFirstIds.length - 1; i >= 0; i -= 1) {
    const id = pinnedFirstIds[i]
    const idx = result.findIndex(c => c.id === id)
    if (idx > 0) {
      const [col] = result.splice(idx, 1)
      result.unshift(col)
    }
  }

  return result
}

/** 從列配置中提取 ext 列參數，用於後端 enriched 接口。 */
export function buildExtColumnsParam(columns: ColumnConfig[]): string {
  return columns
    .filter(c => c.visible && c.source.type === 'ext')
    .map(c => `${(c.source as { type: 'ext'; configId: string; fieldName: string }).configId}.${(c.source as { type: 'ext'; configId: string; fieldName: string }).fieldName}`)
    .join(',')
}

/** 根據 ext schema 數據創建 ext 列配置。 */
export function createExtColumn(
  configId: string,
  _configLabel: string,
  fieldName: string,
  fieldLabel?: string,
): ColumnConfig {
  return {
    id: `ext:${configId}:${fieldName}`,
    source: { type: 'ext', configId, fieldName, fieldLabel },
    label: fieldLabel || fieldName,
    visible: false,
    align: 'center',
  }
}
