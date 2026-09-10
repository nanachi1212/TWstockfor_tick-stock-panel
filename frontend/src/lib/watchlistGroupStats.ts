/**
 * 自選分組漲跌幅 — 等權平均口徑。
 *
 * 組內每隻成員取「實時優先、收盤兜底」的漲跌幅(與自選表格展示同源:
 * rt_pct ?? change_pct, 小數單位), 算術平均即分組漲跌幅。等權最貼合
 * 自選的"個人組合"視角 — 透明且無需市值數據。
 */

import { fmtPct } from '@/lib/format'
import { storage } from '@/lib/storage'

export interface GroupPctInfo {
  /** 等權平均漲跌幅(小數, 0.0123 = +1.23%, 與 enriched change_pct 同單位); 無有效樣本為 null */
  pct: number | null
  up: number
  down: number
  flat: number
  /** 參與統計的樣本數(漲跌幅非空的成員) */
  sampled: number
  /** 中位數漲跌幅(小數); 無有效樣本為 null */
  median: number | null
  /** 組內最大漲跌幅(小數); 無有效樣本為 null */
  max: number | null
  /** 組內最小漲跌幅(小數); 無有效樣本為 null */
  min: number | null
}

/** key: 'all' | 'ungrouped' | 分組 id */
export type GroupPctMap = Record<string, GroupPctInfo>

/**
 * 單隻標的的展示漲跌幅: 實時優先、收盤兜底(與自選表格/卡片展示同源,
 * 小數單位), 無有效數據為 null。分組統計與分組卡片排序共用此口徑。
 */
export function rowPct(
  row: { rt_pct?: number | null; change_pct?: number | null } | undefined,
): number | null {
  const pct = row ? row.rt_pct ?? row.change_pct : null
  return pct == null || !Number.isFinite(pct) ? null : pct
}

export function computeGroupPcts(
  entries: { symbol: string; group_ids?: string[] | null }[],
  rowsBySymbol: Map<string, { rt_pct?: number | null; change_pct?: number | null }>,
): GroupPctMap {
  const buckets = new Map<string, { pcts: number[]; up: number; down: number; flat: number }>()
  const add = (key: string, pct: number | null | undefined) => {
    if (pct == null || !Number.isFinite(pct)) return
    const b = buckets.get(key) ?? { pcts: [], up: 0, down: 0, flat: 0 }
    b.pcts.push(pct)
    if (pct > 0) b.up++
    else if (pct < 0) b.down++
    else b.flat++
    buckets.set(key, b)
  }
  for (const entry of entries) {
    const row = rowsBySymbol.get(entry.symbol)
    const pct = rowPct(row)
    add('all', pct)
    // 多組並存: 一股計入每個所屬分組; 不屬於任何分組才計未分組
    const gids = entry.group_ids ?? []
    if (gids.length === 0) add('ungrouped', pct)
    else for (const gid of gids) add(gid, pct)
  }
  const out: GroupPctMap = {}
  for (const [key, b] of buckets) {
    const sortedPcts = [...b.pcts].sort((a, c) => a - c)
    const mid = Math.floor(sortedPcts.length / 2)
    const median = sortedPcts.length === 0
      ? null
      : sortedPcts.length % 2 === 1
        ? sortedPcts[mid]
        : (sortedPcts[mid - 1] + sortedPcts[mid]) / 2
    out[key] = {
      pct: b.pcts.length ? b.pcts.reduce((a, c) => a + c, 0) / b.pcts.length : null,
      up: b.up,
      down: b.down,
      flat: b.flat,
      sampled: b.pcts.length,
      median,
      max: sortedPcts.length ? sortedPcts[sortedPcts.length - 1] : null,
      min: sortedPcts.length ? sortedPcts[0] : null,
    }
  }
  return out
}

/** 漲跌色 (A 股慣例紅漲綠跌) */
export function groupPctColor(pct: number | null): string {
  if (pct == null || pct === 0) return 'text-muted'
  return pct > 0 ? 'text-bull' : 'text-bear'
}

// ===== 分組統計條指標契約 =====

/** 分組統計指標: 等權平均 / 中位數 / 上漲佔比(以50%為軸) / 組內最強 / 組內最弱 */
export type GroupMetric = 'mean' | 'median' | 'up_ratio' | 'max' | 'min'

export const GROUP_METRICS: ReadonlyArray<{ id: GroupMetric; label: string; hint: string }> = [
  { id: 'mean', label: '等權平均', hint: '組內漲跌幅算術平均' },
  { id: 'median', label: '中位數', hint: '組內漲跌幅中位值, 抗極值' },
  { id: 'up_ratio', label: '上漲佔比', hint: '上漲家數佔有效樣本比例, 50% 為強弱軸' },
  { id: 'max', label: '組內最強', hint: '組內最大漲幅 (龍頭強度)' },
  { id: 'min', label: '組內最弱', hint: '組內最小漲幅' },
]

export function isGroupMetric(v: unknown): v is GroupMetric {
  return typeof v === 'string' && GROUP_METRICS.some(m => m.id === v)
}

/** 分組排序方式: 定義順序 / 按指標降序 / 升序 (分組統計條與分組卡片共享) */
export type GroupSort = 'default' | 'desc' | 'asc'

export const GROUP_SORT_OPTIONS: ReadonlyArray<{ id: GroupSort; label: string }> = [
  { id: 'default', label: '定義順序' },
  { id: 'desc', label: '降序' },
  { id: 'asc', label: '升序' },
]

export function isGroupSort(v: unknown): v is GroupSort {
  return v === 'default' || v === 'desc' || v === 'asc'
}

/** 分組指標+排序配置 (分組統計條 / 分組卡片兩個視圖共享同一份持久化) */
export interface GroupStatsConfig {
  metric: GroupMetric
  sort: GroupSort
  /** 分組卡片默認展示的成員條數 (前 N, 可展開全部) */
  cardTopN: number
  /** 分組卡片頭部是否顯示分組顏色底條 */
  cardColorBar: boolean
  /** 分組卡片成員行是否顯示序號 */
  cardRank: boolean
}

/** 卡片默認條數與上下限 (超出範圍的持久化值會被夾回) */
export const GROUP_CARD_TOP_N_DEFAULT = 8
export const GROUP_CARD_TOP_N_MIN = 1
export const GROUP_CARD_TOP_N_MAX = 50
/** 卡片頭部彩條 / 成員行序號默認開啟 (舊持久化缺失該字段時回退到默認) */
export const GROUP_CARD_COLOR_BAR_DEFAULT = true
export const GROUP_CARD_RANK_DEFAULT = true

function normalizeBool(v: unknown, fallback: boolean): boolean {
  return typeof v === 'boolean' ? v : fallback
}

export function normalizeGroupCardTopN(v: unknown): number {
  const n = typeof v === 'number' ? Math.round(v) : Number.NaN
  if (!Number.isFinite(n)) return GROUP_CARD_TOP_N_DEFAULT
  return Math.min(GROUP_CARD_TOP_N_MAX, Math.max(GROUP_CARD_TOP_N_MIN, n))
}

export function loadGroupStatsConfig(): GroupStatsConfig {
  const saved = storage.watchlistGroupStats.get({
    metric: 'mean',
    sort: 'default',
    cardTopN: GROUP_CARD_TOP_N_DEFAULT,
    cardColorBar: GROUP_CARD_COLOR_BAR_DEFAULT,
    cardRank: GROUP_CARD_RANK_DEFAULT,
  })
  return {
    metric: isGroupMetric(saved.metric) ? saved.metric : 'mean',
    sort: isGroupSort(saved.sort) ? saved.sort : 'default',
    cardTopN: normalizeGroupCardTopN(saved.cardTopN),
    cardColorBar: normalizeBool(saved.cardColorBar, GROUP_CARD_COLOR_BAR_DEFAULT),
    cardRank: normalizeBool(saved.cardRank, GROUP_CARD_RANK_DEFAULT),
  }
}

/** 配置局部更新 (設置彈層 -> 持有方), 新增卡片顯示項時在此處擴展 */
export type GroupStatsConfigPatch = Partial<Pick<GroupStatsConfig, 'metric' | 'sort' | 'cardTopN' | 'cardColorBar' | 'cardRank'>>

/** 按配置排序分組鍵列表 (null 排最後), sort='default' 時原序返回 */
export function sortGroupKeys<T>(items: T[], keyOf: (item: T) => string, pcts: GroupPctMap, config: GroupStatsConfig): T[] {
  if (config.sort === 'default') return items
  return [...items].sort((a, b) => {
    const va = groupMetricValue(pcts[keyOf(a)], config.metric)
    const vb = groupMetricValue(pcts[keyOf(b)], config.metric)
    if (va == null && vb == null) return 0
    if (va == null) return 1
    if (vb == null) return -1
    return config.sort === 'desc' ? vb - va : va - vb
  })
}

/**
 * 取分組在指定指標下的數值。
 * 漲跌幅類指標返回小數 (0.0123 = +1.23%); 上漲佔比返回 0~1 佔比,
 * 條形渲染時以 0.5 為強弱軸。無有效樣本為 null。
 */
export function groupMetricValue(info: GroupPctInfo | undefined, metric: GroupMetric): number | null {
  if (!info || info.sampled === 0) return null
  switch (metric) {
    case 'mean': return info.pct
    case 'median': return info.median
    case 'up_ratio': return info.up / info.sampled
    case 'max': return info.max
    case 'min': return info.min
  }
}

/** 懸停明細: 按當前指標給出數值 + 全套統計, 任意指標下信息完整 */
export function groupMetricTitle(info: GroupPctInfo | undefined, metric: GroupMetric): string {
  if (!info || info.sampled === 0) return '暫無漲跌幅數據'
  const value = groupMetricValue(info, metric)
  const metricLabel = GROUP_METRICS.find(m => m.id === metric)?.label ?? ''
  const valueText = value == null
    ? '—'
    : metric === 'up_ratio'
      ? `${(value * 100).toFixed(1)}%`
      : fmtPct(value)
  return `${metricLabel} ${valueText} · 等權 ${fmtPct(info.pct)} · 中位 ${fmtPct(info.median)} · 最強 ${fmtPct(info.max)} · 最弱 ${fmtPct(info.min)} · 上漲${info.up} 下跌${info.down} 平${info.flat} (共${info.sampled}檔)`
}

/** 懸停明細: 等權平均 +1.23% · 上漲12 下跌5 平1 (格式化複用全站 fmtPct) */
export function groupPctTitle(info: GroupPctInfo | undefined): string {
  if (!info || info.pct == null) return '暫無漲跌幅數據'
  return `等權平均 ${fmtPct(info.pct)} · 上漲${info.up} 下跌${info.down} 平${info.flat} (共${info.sampled}檔)`
}
