/**
 * 個股日K信息條（StockInfoBar Row 2）的指標自定義配置。
 *
 * 與自選列表列配置同源：複用 list-columns 的通用列模型與合併/序列化底座，
 * 僅做純 localStorage 同步持久化（無後端雙寫）。個股預覽彈窗與回測成交K線
 * Modal 共用同一份配置。
 */

import { storage } from '@/lib/storage'
import {
  buildExtColumnsParam as buildExtColumnsParamBase,
  mergeColumns as mergeColumnsBase,
  serializeColumns as serializeColumnsBase,
  type ColumnConfig,
  type ColumnGroup,
} from '@/lib/list-columns'

export type { ColumnConfig, ColumnGroup }

// ===== 內置指標註冊表 =====

export const BUILTIN_INFO_FIELDS: ColumnConfig[] = [
  // 規模
  { id: 'builtin:market_cap', source: { type: 'builtin', key: 'market_cap' }, label: '市值', visible: true, align: 'left' },
  { id: 'builtin:float_market_cap', source: { type: 'builtin', key: 'float_market_cap' }, label: '流通值', visible: true, align: 'left' },
  // 成交
  { id: 'builtin:turnover', source: { type: 'builtin', key: 'turnover' }, label: '換手', visible: true, align: 'left' },
  { id: 'builtin:volume', source: { type: 'builtin', key: 'volume' }, label: '成交量', visible: false, align: 'left' },
  { id: 'builtin:amplitude', source: { type: 'builtin', key: 'amplitude' }, label: '振幅', visible: false, align: 'left' },
  // 行情
  { id: 'builtin:open', source: { type: 'builtin', key: 'open' }, label: '開盤', visible: false, align: 'left' },
  { id: 'builtin:high', source: { type: 'builtin', key: 'high' }, label: '最高', visible: false, align: 'left' },
  { id: 'builtin:low', source: { type: 'builtin', key: 'low' }, label: '最低', visible: false, align: 'left' },
  // 財務（數據來自 financials metrics 接口，默認隱藏；pe_ttm/pb 用 close 現算）
  { id: 'builtin:eps', source: { type: 'builtin', key: 'eps' }, label: 'EPS', visible: false, align: 'left' },
  { id: 'builtin:bps', source: { type: 'builtin', key: 'bps' }, label: 'BPS', visible: false, align: 'left' },
  { id: 'builtin:roe', source: { type: 'builtin', key: 'roe' }, label: 'ROE', visible: false, align: 'left' },
  { id: 'builtin:pe_ttm', source: { type: 'builtin', key: 'pe_ttm' }, label: 'PE', visible: false, align: 'left' },
  { id: 'builtin:pb', source: { type: 'builtin', key: 'pb' }, label: 'PB', visible: false, align: 'left' },
  { id: 'builtin:gross_margin', source: { type: 'builtin', key: 'gross_margin' }, label: '毛利率', visible: false, align: 'left' },
  { id: 'builtin:net_margin', source: { type: 'builtin', key: 'net_margin' }, label: '淨利率', visible: false, align: 'left' },
  { id: 'builtin:debt_ratio', source: { type: 'builtin', key: 'debt_ratio' }, label: '負債率', visible: false, align: 'left' },
  { id: 'builtin:revenue_yoy', source: { type: 'builtin', key: 'revenue_yoy' }, label: '營收增速', visible: false, align: 'left' },
  { id: 'builtin:net_income_yoy', source: { type: 'builtin', key: 'net_income_yoy' }, label: '淨利增速', visible: false, align: 'left' },
]

export const INFO_GROUPS: ColumnGroup[] = [
  { id: 'scale', label: '規模', icon: '🏦', keys: ['market_cap', 'float_market_cap'] },
  { id: 'volume', label: '成交', icon: '📊', keys: ['turnover', 'volume', 'amplitude'] },
  { id: 'quote', label: '行情', icon: '📈', keys: ['open', 'high', 'low'] },
  { id: 'finance', label: '財務', icon: '📋', keys: ['eps', 'bps', 'roe', 'pe_ttm', 'pb', 'gross_margin', 'net_margin', 'debt_ratio', 'revenue_yoy', 'net_income_yoy'] },
]

// ===== localStorage 持久化 =====

/** 加載信息條指標配置：localStorage → 默認值，自動補齊新增默認項。 */
export function loadInfoFields(): ColumnConfig[] {
  const saved = storage.stockInfoBarFields.get([]) as ColumnConfig[]
  if (saved.length === 0) return [...BUILTIN_INFO_FIELDS]
  return mergeFields(saved, BUILTIN_INFO_FIELDS)
}

/** 保存信息條指標配置到 localStorage。 */
export function saveInfoFields(columns: ColumnConfig[]): void {
  storage.stockInfoBarFields.set(serializeFields(columns))
}

/** 序列化（此處無 pinned/action 列，直接用底座默認實現）。 */
function serializeFields(columns: ColumnConfig[]): ColumnConfig[] {
  return serializeColumnsBase(columns)
}

/** 從信息條字段配置中提取 ext 列參數（逗號分隔 config_id.field_name），用於 klineDaily 接口。 */
export function buildInfoExtColumnsParam(columns: ColumnConfig[]): string {
  return buildExtColumnsParamBase(columns)
}

/** 合併用戶保存的配置與默認配置。 */
function mergeFields(saved: ColumnConfig[], defaults: ColumnConfig[]): ColumnConfig[] {
  // 無固定列，傳入空的 pinnedFirstIds 跳過「代碼置頂」邏輯
  return mergeColumnsBase(saved, defaults, { pinnedFirstIds: [] })
}
