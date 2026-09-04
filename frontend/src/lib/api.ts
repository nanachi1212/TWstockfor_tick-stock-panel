// 后端 API 客户端 — 全项目统一入口
//
// Dev: Vite 按启动脚本解析出的 BACKEND_HOST/BACKEND_PORT 代理 /api
// Prod:同源(FastAPI 托管前端 dist)

import { toast } from '@/components/Toast'

const BASE = ''

type RequestOptions = RequestInit & {
  /** 为 true 时不弹错误 toast（由调用方自行汇总提示，如多图串行队列） */
  quiet?: boolean
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { quiet, ...fetchInit } = init ?? {}
  const isFormData = fetchInit.body instanceof FormData
  const headers: Record<string, string> = {}
  if (!isFormData) headers['Content-Type'] = 'application/json'
  // 合并调用方传入的 headers (此前会被整体覆盖丢弃)
  Object.assign(headers, fetchInit.headers as Record<string, string> | undefined)
  const res = await fetch(`${BASE}${path}`, { ...fetchInit, headers })
  if (!res.ok) {
    let detail = ''
    try {
      const j = JSON.parse(await res.text())
      const raw = j.detail ?? j.message ?? ''
      if (Array.isArray(raw)) {
        // FastAPI 422 校验错误: [{type, loc, msg, input}, ...] → 取 msg 拼接
        detail = raw.map((e: any) => e?.msg || String(e)).join('; ')
      } else if (typeof raw === 'string') {
        detail = raw
      } else if (raw && typeof raw === 'object') {
        detail = JSON.stringify(raw)
      }
    } catch { /* ignore */ }
    const msg = detail || `${res.status} ${res.statusText}`
    // 401 (未登录/会话过期) 不弹 toast — 由全局认证拦截器统一跳登录页, 避免刷屏
    if (res.status !== 401 && !quiet) toast(msg, 'error')
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

// ===== Capabilities =====
export interface CapabilityLimits {
  rpm: number | null
  batch: number | null
  subscribe: number | null
}

export interface CapabilitiesResponse {
  label: string
  capabilities: Record<string, CapabilityLimits>
}

// ===== Financials =====
// Phase 8B-5.3: A 股财务分析产品(状态/利润表/资产负债表/现金流量表/历史股本/
// AI 财务分析/报告)已整体下线。仅保留 FinancialMetricRecord —— 它被
// StockPanel/StockInfoBar 的信息条「财务」字段组 (EPS/BPS/ROE/PE/PB 等) 复用。
export interface FinancialMetricRecord {
  symbol?: string
  period_end: string
  announce_date?: string | null
  eps_basic?: number | null
  eps_diluted?: number | null
  bps?: number | null
  ocfps?: number | null
  roe?: number | null
  roe_diluted?: number | null
  roa?: number | null
  gross_margin?: number | null
  net_margin?: number | null
  debt_to_asset_ratio?: number | null
  revenue_yoy?: number | null
  net_income_yoy?: number | null
  operating_cash_to_revenue?: number | null
  inventory_turnover?: number | null
  [key: string]: any
}

// ===== 个股分析 =====
export type LevelType = 'sr' | 'pivot' | 'extreme' | 'boll' | 'keltner_s' | 'keltner_m' | 'keltner_l' | 'atr_stop' | 'gap' | 'fib' | 'round'

export interface PriceLevel {
  value: number
  label: string
  type: LevelType
  side: 'resistance' | 'support' | 'neutral'
  strength?: 'strong' | 'medium' | 'weak'
  /** 档位(仅 pivot 有):0=P, 1=R1/S1, 2=R2/S2, 3=R3/S3。前端按"显示到第几档"过滤。 */
  rank?: number
}

/** 带状曲线指标(布林带/Keltner/ATR)的每日时间序列,与 dates 对齐。 */
export interface LevelSeries {
  boll?: { upper: (number | null)[]; lower: (number | null)[]; mid?: (number | null)[] }
  keltner_s?: { upper: (number | null)[]; lower: (number | null)[] }
  keltner_m?: { upper: (number | null)[]; lower: (number | null)[] }
  keltner_l?: { upper: (number | null)[]; lower: (number | null)[] }
  atr?: { stop_loss: (number | null)[]; take_profit: (number | null)[] }
}

export interface StockLevels {
  levels: Record<LevelType, PriceLevel[]>
  close: number | null
  summary: string
  symbol: string
  /** dates 与 series 对齐;前端按自身 rows 的日期映射,缺失填 null */
  dates?: string[]
  series?: LevelSeries
}

export interface AiStockReport {
  id: string
  symbol: string
  name: string
  focus: string
  content: string
  summary?: string
  close?: number | null
  levels?: Record<LevelType, PriceLevel[]>
  created_at: string
}

// ===== Kline =====
export interface MinuteKlineRow {
  datetime: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
}

export interface MinuteKlineSession {
  date: string
  prev_close: number | null
  rows: MinuteKlineRow[]
}

export interface PriceLimitInfo {
  rate: number
  limit_up: number | null
  limit_down: number | null
  source: 'rule' | 'instrument'
}

export interface KlineRow {
  symbol?: string
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
  change_pct?: number
  ma5?: number | null
  ma20?: number | null
  ma60?: number | null
  macd_dif?: number | null
  macd_dea?: number | null
  macd_hist?: number | null
  rsi_14?: number | null
  vol_ratio_5d?: number | null
  [key: string]: any
}

// ===== Watchlist =====
export interface WatchlistEntry {
  symbol: string
  added_at: string
  note?: string
  name?: string | null
  /** 所属分组 id 列表 (同一标的可属于多个分组; 空数组=未分组) */
  group_ids?: string[]
}

export type WatchlistGroupColor =
  | 'sky'
  | 'blue'
  | 'indigo'
  | 'violet'
  | 'fuchsia'
  | 'rose'
  | 'orange'
  | 'amber'
  | 'lime'
  | 'emerald'
  | 'teal'
  | 'cyan'

export interface WatchlistGroup {
  id: string
  name: string
  color: WatchlistGroupColor
}

export interface WatchlistImportCandidate {
  code: string
  symbol: string | null
  name: string | null
  matched: boolean
  already_in_watchlist: boolean
}

export interface WatchlistImportResult {
  provider: string
  codes: string[]
  candidates: WatchlistImportCandidate[]
  matched_count: number
  unmatched_count: number
}

export interface Quote {
  symbol: string
  price?: number
  pct?: number
  close?: number
  change_pct?: number
  [key: string]: any
}

export interface IndexQuote {
  symbol: string
  name?: string | null
  last_price?: number | null
  close?: number | null
  prev_close?: number | null
  change_pct?: number | null
  change_amount?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  volume?: number | null
  amount?: number | null
  timestamp?: number | null
  [key: string]: any
}

// ===== Screener =====
export interface ScreenerStrategy {
  id: string
  name: string
  description: string
  source?: string
}

export interface StrategyLoadError {
  file: string
  error: string
}

export interface ScreenerResult {
  as_of: string
  strategy: string | null
  rows: any[]
  total: number
  elapsed_ms: number
}

export interface ScreenerResultSummary {
  total: number
  as_of: string
}

export interface ScreenerCachedSummary {
  as_of: string | null
  results: Record<string, ScreenerResultSummary>
  today_ever_counts: Record<string, number>
  updated_at: number | null
}

export interface ScreenerCachedResult {
  result: ScreenerResult | null
  today_ever_rows: Record<string, any> | null
  strategy_ids_by_symbol: Record<string, string[]>
  updated_at: number | null
}

export interface MarketSnapshotRow {
  symbol: string
  name?: string | null
  close?: number | null
  change_pct?: number | null
  amount?: number | null
  volume?: number | null
  turnover_rate?: number | null
  vol_ratio_5d?: number | null
  total_shares?: number | null
  float_shares?: number | null
  market_cap?: number | null
  float_market_cap?: number | null
  consecutive_limit_ups?: number | null
  [key: string]: any
}

export interface OverviewDimensionRankItem {
  name: string
  count: number
  avg_pct: number
  up_count: number
  down_count: number
  amount: number
  leader?: {
    symbol?: string | null
    name?: string | null
    change_pct?: number | null
  } | null
}

export interface OverviewMarket {
  as_of: string | null
  quote_status: {
    enabled?: boolean
    running?: boolean
    quote_age_ms?: number | null
    is_trading_hours?: boolean
    [key: string]: any
  }
  indices: IndexQuote[]
  breadth: {
    total: number
    up: number
    down: number
    flat: number
    up_pct: number
    down_pct: number
    avg_pct?: number | null
    median_pct?: number | null
    strong_up?: number
    strong_down?: number
  }
  amount: { total: number; avg: number }
  boards: { board: string; count: number; up: number; down: number; up_pct: number; amount: number }[]
  limit: { limit_up: number; broken: number; failed: number; limit_down: number; max_boards: number; seal_rate?: number; tiers: { boards: number; count: number; stocks?: { symbol: string; name?: string; amount?: number }[] }[]; sealed_ready?: boolean; fake_up?: number; fake_down?: number }
  distribution: { label: string; count: number; pct: number }[]
  trend: { above_ma5: number; above_ma20: number; above_ma60: number; above_ma5_pct: number; above_ma20_pct: number; above_ma60_pct: number; new_high: number; new_low: number }
  activity: { avg_turnover: number; high_turnover: number; high_vol_ratio: number; vol_ratio: number }
  radar: { key: string; label: string; value: number }[]
  emotion: { score: number; label: string }
  top_gainers: MarketSnapshotRow[]
  top_losers: MarketSnapshotRow[]
  turnover_leaders: MarketSnapshotRow[]
  active_leaders: MarketSnapshotRow[]
  concept_rank: { leading: OverviewDimensionRankItem[]; lagging: OverviewDimensionRankItem[] }
  industry_rank: { leading: OverviewDimensionRankItem[]; lagging: OverviewDimensionRankItem[] }
}

// ===== 市场环境(Regime) =====
export type RegimeState = 'strong' | 'lean_strong' | 'range' | 'lean_weak' | 'weak'

export const REGIME_STATE_LABELS: Record<RegimeState, string> = {
  strong: '强势',
  lean_strong: '偏强',
  range: '震荡',
  lean_weak: '偏弱',
  weak: '弱势',
}

export const REGIME_STATE_COLORS: Record<RegimeState, string> = {
  strong: '#ef4444',      // 红(强)
  lean_strong: '#f97316', // 橙
  range: '#6b7280',       // 灰
  lean_weak: '#3b82f6',   // 蓝
  weak: '#10b981',        // 绿(弱)
}

export interface RegimeRow {
  date: string
  state: RegimeState
  score: number
  limit_up: number
  limit_down: number
  broken_limit: number
  max_consecutive: number
  seal_rate: number
  up_count: number
  down_count: number
  up_ratio: number
  index_pct: number
  above_ma20_pct: number
  total_amount: number
  avg_turnover: number
  // 4 个子维度分(0-100, 重算后才有; 旧数据可能缺) — 综合分的加权来源
  avg_pct?: number
  median_pct?: number
  strong_up_pct?: number
  strong_down_pct?: number
  profit_score?: number
  speculation_score?: number
  resilience_score?: number
  trend_score?: number
  // 情绪周期阶段与梯队指标(重算后才有; 旧数据可能缺)
  phase?: MarketPhase | null
  first_board?: number | null
  ge2_count?: number | null
  ge3_count?: number | null
  ge5_count?: number | null
  ladder_completeness?: number | null
  promo_rate?: number | null
  promo_pool?: number | null
}

export interface RegimeHistory {
  rows: RegimeRow[]
  total: number
}

export interface RegimeStateItem {
  state: RegimeState
  label: string
  count: number
  pct: number
}

export interface RegimeStates {
  distribution: RegimeStateItem[]
  days: number
}

export interface RegimeCoverage {
  rows: number
  earliest_date: string | null
  latest_date: string | null
}

// ── 市场阶段(情绪周期) 与 主线 ──
export type MarketPhase = 'ice' | 'ignite' | 'rally' | 'climax' | 'ebb' | 'repair'

export const MARKET_PHASE_LABELS: Record<MarketPhase, string> = {
  ice: '冰点',
  ignite: '启动',
  rally: '主升',
  climax: '高潮',
  ebb: '退潮',
  repair: '修复',
}

export const MARKET_PHASE_COLORS: Record<MarketPhase, string> = {
  ice: '#38bdf8',     // 天蓝(冻结)
  ignite: '#f59e0b',  // 琥珀(升温)
  rally: '#ef4444',   // 红(主升)
  climax: '#d946ef',  // 品红(极端)
  ebb: '#14b8a6',     // 青(退潮)
  repair: '#94a3b8',  // 灰(修复)
}

export const MARKET_PHASE_ORDER: MarketPhase[] = ['ice', 'ignite', 'rally', 'climax', 'ebb', 'repair']

export interface MainlineMemberStat {
  member: string
  top5_days: number
  score_sum: number
  max_boards: number
  leader_symbol: string
}

export interface PhaseSegment {
  phase: MarketPhase
  label: string
  start: string
  end: string
  days: number
  avg_height: number
  avg_first_board: number
  avg_ge2: number
  avg_promo: number | null
  avg_seal_rate: number
  top_mainlines: MainlineMemberStat[]
}

export interface PhaseSegments {
  segments: PhaseSegment[]
  total: number
}

export interface MainlineRow {
  date: string
  kind: string
  member: string
  limit_up_count: number
  ge2_count: number
  max_boards: number
  boards_sum: number
  rungs_filled: number
  leader_symbol: string
  score: number
  rank: number
}

export interface MainlineLeader {
  member: string
  top1_days: number
  avg_score: number
  max_boards: number
}

export interface MainlineFilter {
  min_members: number
  max_members: number
  blacklist: string[]
  exclude_st: boolean
}

export interface MainlineResult {
  rows: MainlineRow[]
  leaders: MainlineLeader[]
  membership_note: string
  filter: MainlineFilter
}

// ===== 大盘复盘 =====
export interface AiReviewReport {
  id: string
  as_of: string
  focus?: string
  content: string
  summary?: string
  emotion_score?: number | null
  emotion_label?: string
  created_at: string
}

// ===== Strategy Engine =====
export interface StrategyParamDef {
  id: string
  label: string
  type: 'float' | 'int' | 'select' | 'bool'
  default: number | string | boolean
  min?: number
  max?: number
  step?: number
  options?: string[]
}

export interface CompositeChildInfo {
  id: string
  name: string
  source: string
  weight: number
}

export interface StrategyDetail {
  id: string
  name: string
  description: string
  tags: string[]
  source: 'builtin' | 'custom' | 'ai' | 'composite'
  execution_backend: 'polars_expr' | 'matrix_native' | 'python_history_legacy' | 'composite'
  asset_types: string[]
  timeframes: string[]
  version: string
  basic_filter: Record<string, any>
  params: StrategyParamDef[]
  params_defaults: Record<string, any>
  scoring: Record<string, number>
  scoring_directions: Record<string, ScoringDirection>
  entry_signals: string[]
  exit_signals: string[]
  minute_exit_trigger_supported_signals: string[]
  stop_loss: number | null
  take_profit: number | null
  trailing_stop: number | null
  trailing_take_profit_activate: number | null
  trailing_take_profit_drawdown: number | null
  max_hold_days: number | null
  display_limit?: number
  order_by: string
  descending: boolean
  limit: number
  // 叠加策略(composite)专属: 子策略列表与合并模式。非 composite 时为 null。
  composite_children?: CompositeChildInfo[] | null
}

export type ScoringDirection = 'high' | 'low'

export interface StrategyBuildResult {
  code: string
  meta: Record<string, any>
  valid: boolean
  error: string | null
}

export type StrategyBuildStreamEvent =
  | { type: 'meta'; strategy_id?: string; step?: number }
  | { type: 'delta'; content: string }
  | ({ type: 'result' } & StrategyBuildResult)
  | { type: 'error'; message: string }

export interface StrategyCodeSaveResult {
  ok: boolean
  strategy_id: string
  source: 'ai' | 'custom' | 'composite'
  path: string
  meta: Record<string, any>
}

// ===== Custom Signals (自定义信号) =====
export interface CustomSignalCondition {
  left: string     // 字段名
  op: string       // > >= < <= == !=
  right: string    // "field:xxx" 或数字字符串
  leftDays?: number   // 左字段取几日前 (0=当日, 默认)
  rightDays?: number  // 右字段取几日前 (仅 right 为字段时有意义)
}

export interface CustomSignal {
  id: string
  name: string
  kind: 'entry' | 'exit' | 'both'
  conditions: CustomSignalCondition[]
  enabled: boolean
}

export interface CustomSignalFieldGroup {
  key: string
  label: string
  fields: { key: string; label: string }[]
}

export interface CustomSignalOptions {
  fields: { key: string; label: string }[]
  groups?: CustomSignalFieldGroup[]
  maxDays?: number
  operators: string[]
  kinds: { key: string; label: string }[]
}

export interface CustomSignalAIGenerateResult {
  name: string
  conditions: CustomSignalCondition[]
}

// ===== Monitor (监控规则 + 触发记录) =====
export interface MonitorCondition {
  field: string
  op: string              // truth | > >= < <= == !=
  value?: number | null   // op 非 truth 时必填
}

export type StrategyNotifyEvent = 'buy_signal' | 'sell_signal' | 'pool_entry' | 'pool_exit'

export type SectorKind = 'index' | 'concept' | 'industry'

export interface SectorMonitorTarget {
  key: string
  kind: SectorKind
  name: string
  symbol?: string
  source_id?: string
  field?: string
  source_field?: string
  value?: string
  level?: number | null
  available: boolean
  member_count: number
}

export interface MonitorRule {
  id: string
  name: string
  enabled: boolean
  type: 'strategy' | 'signal' | 'price' | 'market' | 'ladder' | 'sector'
  asset_type?: 'stock' | 'etf' | 'index'
  scope: 'symbols' | 'all' | 'sector' | 'watchlist_group'
  symbols: string[]
  /** scope=watchlist_group 时绑定的自选分组 id (成员动态解析, 增删自选自动生效) */
  group_id?: string | null
  sector?: string | null
  sector_kind?: SectorKind | null
  sector_targets?: SectorMonitorTarget[]
  sector_trigger?: 'change_pct' | 'momentum'
  threshold_pct?: number
  window_minutes?: 1 | 3 | 5 | 10 | 15
  strategy_id?: string | null
  direction: 'entry' | 'exit' | 'both' | 'up' | 'down'
  notify_events?: StrategyNotifyEvent[]
  score_min?: number | null
  score_max?: number | null
  conditions: MonitorCondition[]
  logic: 'and' | 'or'
  cooldown_seconds: number
  severity: 'info' | 'warn' | 'critical'
  message: string
  webhook_url?: string
  webhook_enabled?: boolean  // 兼容老规则, 已由 webhook_channels 取代
  webhook_channels?: string[]  // 命中时推送的外部渠道 (合法值 'feishu' | 'wecom')
  created_at?: string
  runtime_warning?: string
  // ladder 专属: 封单监控
  metric?: 'sealed_vol' | 'sealed_amount'  // 量(手) / 额(元)
  threshold?: number                        // 封单 <= 此值时报警
}

export interface MonitorRuleOptions {
  threshold_fields: { key: string; label: string }[]
  builtin_signals: { key: string; label: string }[]
  custom_signals: { key: string; label: string }[]
  operators: string[]
  types: { key: string; label: string }[]
  scopes: { key: string; label: string }[]
  logics: { key: string; label: string }[]
  severities: { key: string; label: string }[]
  directions: { key: string; label: string }[]
  intraday_signal_support: {
    available: boolean
    source: string | null
    max_symbols: number
    reason: string
  }
  sector_targets: Record<SectorKind, SectorMonitorTarget[]>
}

export interface AlertEvent {
  ts: number
  rule_id?: string
  rule_name?: string
  source: string
  type: string
  symbol?: string
  name?: string | null
  message: string
  price?: number | null
  change_pct?: number | null
  signals?: string[]
  severity?: string
  strategy_id?: string
  conditions?: MonitorCondition[]
  logic?: 'and' | 'or'
  sector_kind?: SectorKind
  sector_key?: string
  sector_name?: string
  sector_source_field?: string
  sector_value?: string
  sector_level?: number | null
  window_change_pct?: number | null
  coverage_ratio?: number
  valid_count?: number
  total_count?: number
  up_count?: number
  down_count?: number
  leader?: { symbol?: string; name?: string; change_pct?: number } | null
  /** ext 富化字段 (行业/概念等), 键为 "{configId}__{fieldName}" */
  [key: string]: unknown
}

// ===== Taiwan Realtime & Monitor Types (Phase 5C) =====
export type TaiwanMarketStatus =
  | 'open'
  | 'pre_open'
  | 'post_close'
  | 'closed'
  | 'non_trading_day'
  | 'scheduled_open_unverified'

export interface TaiwanSourceMeta {
  source: string
  source_url: string
  fetched_at: string
  trade_date: string
  status: string
  is_realtime: boolean
  is_stale: boolean
  source_type: string
  freshness_class: string
  is_best_effort: boolean
  fallback_reason?: string | null
}

export interface TaiwanRealtimeQuote {
  symbol: string
  name: string
  exchange: 'TWSE' | 'TPEx' | string
  last_price: number | null
  prev_close: number | null
  open: number | null
  high: number | null
  low: number | null
  change: number | null
  change_pct: number | null
  volume: number | null               // In SHARES (股)
  amount: number | null               // In TWD
  quote_time: string | null           // ISO string
  trade_date: string                  // YYYY-MM-DD
  market_status: TaiwanMarketStatus | string
  source_meta: TaiwanSourceMeta
  bid_price?: number | null
  ask_price?: number | null
  bid_volume?: number | null          // In SHARES (股)
  ask_volume?: number | null          // In SHARES (股)
  bids?: [number, number][]           // 5-level order book: [price, shares]
  asks?: [number, number][]           // 5-level order book: [price, shares]
  limit_up?: number | null
  limit_down?: number | null
  price_limit_pct?: number | null
  is_no_limit?: boolean
}

export type TaiwanRuleType =
  | 'price_above'
  | 'price_below'
  | 'change_pct_above'
  | 'change_pct_below'
  | 'volume_above'
  | 'volume_spike'
  | 'near_upper_limit'
  | 'near_lower_limit'

export interface TaiwanMonitorRule {
  rule_id: string
  name: string
  symbol: string
  rule_type: TaiwanRuleType
  threshold: number
  enabled: boolean
  cooldown_seconds: number
  hysteresis?: number | null
  reference_volume?: number | null     // In SHARES (股)
  severity: 'info' | 'warning' | 'critical'
  created_at?: string
  updated_at?: string
}

export interface TaiwanAlertEvent {
  alert_id: string
  rule_id: string
  rule_name: string
  symbol: string
  name: string
  rule_type: string
  triggered_at: string
  quote_time?: string | null
  trigger_value: number
  threshold: number
  message: string
  source: string
  source_status: string
  market_status: string
  severity: 'info' | 'warning' | 'critical' | string
  field_name: string
  dedup_key: string
  ts: number
  price?: number | null
  change_pct?: number | null
}

export interface TaiwanSearchResult {
  symbol: string
  code: string
  name: string
  exchange: string
  instrument_type: string
  is_supported: boolean
  price_limit_pct?: number | null
  is_no_limit?: boolean
}

// ===== Phase 6A: Taiwan Stock Detail Workspace =====
export interface TaiwanSectionMeta {
  source: string
  trade_date?: string | null
  fetched_at?: string | null
  status: 'available' | 'unavailable' | 'stale' | 'fallback' | string
  is_stale: boolean
  fallback_reason?: string | null
}

export interface TaiwanStockIdentity {
  symbol: string
  code: string
  name: string
  exchange: string
  instrument_type: string
  is_supported: boolean
  listing_status?: string | null
  listing_date?: string | null
  industry?: string | null
  cfi_code?: string | null
  etf_category?: string | null
}

export interface TaiwanStockPriceLimit {
  limit_up?: number | null
  limit_down?: number | null
  price_limit_pct?: number | null
  is_no_limit: boolean
  rule_type: string
}

export interface TaiwanStockRealtimeDetail {
  last_price?: number | null
  prev_close?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  change?: number | null
  change_pct?: number | null
  volume?: number | null
  amount?: number | null
  quote_time?: string | null
  market_status: string
  bid_price?: number | null
  ask_price?: number | null
  bids: [number, number][]
  asks: [number, number][]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanDailyItem {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount?: number | null
}

export interface TaiwanHistoricalDailyDetail {
  status: string
  rows: TaiwanDailyItem[]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanInstitutionalDetail {
  status: string
  foreign_net?: number | null
  investment_trust_net?: number | null
  dealer_net?: number | null
  total_net?: number | null
  foreign_net_5d?: number | null
  investment_trust_net_5d?: number | null
  dealer_net_5d?: number | null
  trend?: Record<string, any>[]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanMarginDetail {
  status: string
  margin_balance?: number | null
  margin_change?: number | null
  short_balance?: number | null
  short_change?: number | null
  short_margin_ratio?: number | null
  trend?: Record<string, any>[]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanFactorsDetail {
  status: string
  foreign_net_5d?: number | null
  investment_trust_net_5d?: number | null
  dealer_net_5d?: number | null
  margin_balance_change?: number | null
  short_margin_ratio?: number | null
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanMarketContextDetail {
  benchmark_symbol: string
  benchmark_name: string
  close?: number | null
  change?: number | null
  change_pct?: number | null
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanMonitorSummaryDetail {
  rule_count: number
  active_count: number
  rules: Record<string, any>[]
}

export interface TaiwanRecentAlertDetail {
  alert_id: string
  rule_id: string
  rule_name: string
  rule_type: string
  severity: string
  trigger_price: number
  trigger_value: number
  message: string
  triggered_at: string
}

export interface TaiwanStockDetailResponse {
  symbol: string
  identity: TaiwanStockIdentity
  price_limit: TaiwanStockPriceLimit
  realtime: TaiwanStockRealtimeDetail
  daily_history: TaiwanHistoricalDailyDetail
  institutional: TaiwanInstitutionalDetail
  margin: TaiwanMarginDetail
  factors: TaiwanFactorsDetail
  market_context: TaiwanMarketContextDetail
  monitor_summary: TaiwanMonitorSummaryDetail
  recent_alerts: TaiwanRecentAlertDetail[]
  overall_data_quality: 'good' | 'partial' | 'stale' | 'degraded' | string
}

export type TaiwanUsageScope =
  | 'current_reference'
  | 'historical_reference'
  | 'pit_historical'
  | 'unsupported'

export interface TaiwanDatasetCapability {
  domain: 'company' | 'etf'
  dataset: string
  exchanges: Array<'TWSE' | 'TPEX'>
  production_supported: boolean
  current_reference: boolean
  pit_historical: boolean
  usage_scope: TaiwanUsageScope
  status: string
  reason: string
}

export interface TaiwanDataSection {
  dataset: string
  status: string
  usage_scope: TaiwanUsageScope
  historically_eligible: boolean
  available_at?: string | null
  source?: string | null
  provider?: string | null
  reason?: string | null
  data?: Record<string, unknown> | null
}

export interface TaiwanCurrentDataResponse {
  symbol: string
  exchange: string
  security_type: 'stock' | 'etf'
  identity: Record<string, unknown>
  sections: Record<string, TaiwanDataSection>
}

// ===== Taiwan Screener Types (Phase 6B) =====
export interface TaiwanScreenerRequest {
  exchange?: 'TWSE' | 'TPEX' | 'ALL'
  instrument?: 'stock' | 'etf' | 'ALL'
  industry?: string | null
  price_min?: number | null
  price_max?: number | null
  change_pct_min?: number | null
  change_pct_max?: number | null
  volume_min?: number | null
  volume_max?: number | null
  amount_min?: number | null
  amount_max?: number | null
  rsi_14_min?: number | null
  rsi_14_max?: number | null
  momentum_5d_min?: number | null
  momentum_5d_max?: number | null
  vol_ratio_5d_min?: number | null
  vol_ratio_5d_max?: number | null
  above_ma5?: boolean | null
  above_ma20?: boolean | null
  foreign_net_min?: number | null
  foreign_net_max?: number | null
  investment_trust_net_min?: number | null
  investment_trust_net_max?: number | null
  dealer_net_min?: number | null
  dealer_net_max?: number | null
  margin_balance_change_min?: number | null
  margin_balance_change_max?: number | null
  short_balance_min?: number | null
  short_balance_max?: number | null
  short_margin_ratio_min?: number | null
  short_margin_ratio_max?: number | null
  near_upper_limit?: boolean | null
  near_lower_limit?: boolean | null
  distance_to_upper_limit_max?: number | null
  distance_to_lower_limit_max?: number | null
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export interface ScreenerResultItem {
  symbol: string
  name: string
  exchange: string
  instrument_type: string
  industry?: string | null
  close?: number | null
  change_pct?: number | null
  volume?: number | null
  amount?: number | null
  quote_date?: string | null
  price_limit_pct?: number | null
  is_no_limit: boolean
  limit_up?: number | null
  limit_down?: number | null
  distance_to_upper_limit?: number | null
  distance_to_lower_limit?: number | null
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  rsi_14?: number | null
  momentum_5d?: number | null
  vol_ratio_5d?: number | null
  foreign_net?: number | null
  foreign_net_5d?: number | null
  investment_trust_net?: number | null
  investment_trust_net_5d?: number | null
  dealer_net?: number | null
  institutional_date?: string | null
  institutional_status?: string
  margin_balance?: number | null
  margin_balance_change?: number | null
  short_balance?: number | null
  short_margin_ratio?: number | null
  margin_date?: string | null
  margin_status?: string
}

export interface TaiwanScreenerResponse {
  items: ScreenerResultItem[]
  total: number
  page: number
  page_size: number
  sort_by: string
  sort_order: string
  data_dates: {
    daily_as_of?: string | null
    institutional_as_of?: string | null
    margin_as_of?: string | null
  }
  degraded_sections: string[]
}

export interface TaiwanDataStatus {
  daily_as_of: string | null
  institutional_as_of: string | null
  margin_as_of: string | null
  target_latest_trading_date: string
  is_fully_current: boolean
  daily_status: 'current' | 'stale' | 'unavailable'
  institutional_status: 'current' | 'stale' | 'unavailable'
  margin_status: 'current' | 'stale' | 'unavailable'
  daily_days_behind: number
  institutional_days_behind: number
  margin_days_behind: number
  scheduler_enabled: boolean
  scheduled_update_time: string
  scheduled_timezone: string
}

export interface TaiwanScreenerTranslation {
  request: TaiwanScreenerRequest | null
  recognized_conditions: string[]
  unsupported_conditions: string[]
  clarification_needed: boolean
  clarification_message: string | null
}

export interface MarketBreadthStats {
  supported_count: number
  snapshot_row_count: number
  traded_count: number
  advance_count: number
  decline_count: number
  flat_count: number
  uncompared_count: number
  upper_limit_count: number
  lower_limit_count: number
  turnover: number
}

export interface InstitutionalMarketAggregate {
  trade_date: string | null
  row_count: number
  foreign_net: number | null
  investment_trust_net: number | null
  dealer_net: number | null
  total_net: number | null
  status: 'current' | 'stale' | 'unavailable'
}

export interface MarginMarketAggregate {
  trade_date: string | null
  row_count: number
  margin_balance: number | null
  margin_balance_change: number | null
  short_balance: number | null
  short_balance_change: number | null
  aggregate_short_margin_ratio: number | null
  status: 'current' | 'stale' | 'unavailable'
}

export interface IndexSnapshot {
  symbol: string
  name: string
  trade_date: string | null
  close: number | null
  change: number | null
  change_pct: number | null
  status: string
}

export interface TaiwanMarketIntelligenceSnapshot {
  trade_date: string
  generated_at: string
  market_totals: MarketBreadthStats
  by_exchange: {
    twse: MarketBreadthStats
    tpex: MarketBreadthStats
  }
  by_instrument: {
    stock: MarketBreadthStats
    etf: MarketBreadthStats
  }
  institutional: InstitutionalMarketAggregate
  margin: MarginMarketAggregate
  indexes: {
    taiex: IndexSnapshot | null
    tpex_index: IndexSnapshot | null
  }
  data_quality: {
    target_trade_date: string
    previous_trade_date: string | null
    overall_status: 'complete' | 'partial' | 'unavailable'
    universe_supported_symbols: number
    daily_snapshot_symbols: number
    missing_symbols_count: number
  }
}

export interface IndustryConstituentMover {
  symbol: string
  name: string
  change_pct: number | null
  close: number | null
  turnover: number
}

export interface IndustryMetrics {
  industry: string
  supported_symbol_count: number
  snapshot_symbol_count: number
  traded_symbol_count: number
  comparable_symbol_count: number
  turnover: number
  turnover_share: number
  advance_count: number
  decline_count: number
  flat_count: number
  uncompared_count: number
  advance_ratio: number | null
  decline_ratio: number | null
  average_change_pct: number | null
  median_change_pct: number | null
  foreign_net: number | null
  investment_trust_net: number | null
  dealer_net: number | null
  margin_balance_change: number | null
  short_balance_change: number | null
  relative_strength_5d: number | null
  relative_strength_20d: number | null
  relative_strength_5d_comparable_count: number
  relative_strength_20d_comparable_count: number
  top_gainers: IndustryConstituentMover[]
  top_losers: IndustryConstituentMover[]
  top_turnover: IndustryConstituentMover[]
}

export interface TaiwanIndustryIntelligenceSnapshot {
  trade_date: string
  generated_at: string
  market_reference: {
    trade_date: string
    total_stock_turnover: number
    market_equal_weight_return_5d: number | null
    market_equal_weight_return_20d: number | null
    comparable_stocks_5d_count: number
    comparable_stocks_20d_count: number
  }
  industries: IndustryMetrics[]
  data_quality: {
    target_trade_date: string
    previous_trade_date: string | null
    base_date_5d: string | null
    base_date_20d: string | null
    supported_stock_count: number
    classified_stock_count: number
    unclassified_stock_count: number
    etfs_excluded_count: number
    industry_count: number
    classification_coverage_pct: number
    daily_status: 'current' | 'stale' | 'unavailable'
    institutional_status: 'current' | 'stale' | 'unavailable'
    margin_status: 'current' | 'stale' | 'unavailable'
    overall_status: 'complete' | 'partial' | 'unavailable'
  }
}

export interface EvidenceMeta {
  classification: 'KNOWN' | 'MISSING' | 'DERIVED'
  source: string
  formula?: string | null
  as_of?: string | null
}

export interface TaiwanStockResearchContext {
  symbol: string
  generated_at: string
  as_of_date: string
  identity: {
    canonical_symbol: string
    code: string
    name: string
    exchange: string
    instrument_type: string
    industry: string | null
    currency: string
    listing_status: string
    listing_date: string | null
    meta: EvidenceMeta
  }
  market_context: {
    trade_date: string
    market_turnover: number
    advance_count: number
    decline_count: number
    flat_count: number
    upper_limit_count: number
    lower_limit_count: number
    exchange_turnover: number | null
    institutional_market_net: number | null
    margin_market_change: number | null
    status: string
    meta: EvidenceMeta
  }
  industry_context: {
    industry: string | null
    turnover: number | null
    turnover_share: number | null
    advance_ratio: number | null
    average_change_pct: number | null
    median_change_pct: number | null
    foreign_net: number | null
    investment_trust_net: number | null
    relative_strength_5d: number | null
    relative_strength_20d: number | null
    status: string
    meta: EvidenceMeta
  }
  price_context: {
    trade_date: string
    open: number | null
    high: number | null
    low: number | null
    close: number | null
    volume: number | null
    amount: number | null
    previous_close: number | null
    change: number | null
    change_pct: number | null
    return_5d: number | null
    return_20d: number | null
    high_20d: number | null
    low_20d: number | null
    distance_from_20d_high: number | null
    distance_from_20d_low: number | null
    meta: EvidenceMeta
  }
  technical_context: {
    ma5: number | null
    ma20: number | null
    ma60: number | null
    rsi14: number | null
    above_ma5: boolean | null
    above_ma20: boolean | null
    distance_to_ma20: number | null
    vol_ratio_5d: number | null
    meta: EvidenceMeta
  }
  institutional_context: {
    as_of: string | null
    foreign_net_1d: number | null
    foreign_net_5d: number | null
    foreign_net_20d: number | null
    investment_trust_net_1d: number | null
    investment_trust_net_5d: number | null
    investment_trust_net_20d: number | null
    dealer_net_1d: number | null
    dealer_net_5d: number | null
    dealer_net_20d: number | null
    coverage_days_5d: number
    coverage_days_20d: number
    status: string
    meta: EvidenceMeta
  }
  margin_context: {
    as_of: string | null
    margin_balance: number | null
    margin_balance_change_1d: number | null
    margin_balance_change_5d: number | null
    short_balance: number | null
    short_balance_change_1d: number | null
    short_balance_change_5d: number | null
    short_margin_ratio: number | null
    status: string
    meta: EvidenceMeta
  }
  fundamentals_context: {
    status: string
    as_of_period: string | null
    available_at: string | null
    pe: number | null
    pb: number | null
    dividend_yield: number | null
    monthly_revenue_yoy: number | null
    latest_eps: number | null
    meta: EvidenceMeta
  }
  etf_context: {
    status: string
    etf_type: string | null
    underlying_scope: string | null
    leverage_multiplier: number | null
    inverse: boolean | null
    benchmark: string | null
    meta: EvidenceMeta
  }
  market_rules_context: {
    price_limit_pct: number | null
    is_no_limit: boolean
    limit_up: number | null
    limit_down: number | null
    tick_size: number | null
    meta: EvidenceMeta
  }
  realtime_context: {
    status: string
    last_price: number | null
    quote_ts: string | null
    meta: EvidenceMeta
  }
  monitor_context: {
    active_rule_count: number
    recent_alert_count: number
    meta: EvidenceMeta
  }
  data_quality: {
    overall_status: 'complete' | 'partial' | 'unavailable'
    sections: { section: string; status: string; as_of: string | null }[]
    target_trade_date: string
  }
  evidence_summary: {
    known_fields_count: number
    missing_fields_count: number
    derived_fields_count: number
    missing_sections: string[]
  }
}

export interface DiagnosticSignalEvidence {
  type: string
  subtype?: string | null
  severity: 'low' | 'moderate' | 'high' | 'extreme'
  observed: number
  baseline?: number | null
  ratio?: number | null
  delta?: number | null
  threshold: number
  formula: string
  lookback_sessions: number
  valid_sessions: number
  source: string
  status: string
}

export interface TaiwanAbnormalDiagnosticItem {
  symbol: string
  code: string
  name: string
  exchange: string
  industry: string | null
  close: number | null
  previous_close: number | null
  change: number | null
  change_pct: number | null
  volume: number | null
  amount: number | null
  volume_ratio_5d: number | null
  amount_ratio_5d: number | null
  foreign_net: number | null
  investment_trust_net: number | null
  margin_balance_change: number | null
  short_balance_change: number | null
  short_margin_ratio: number | null
  signal_count: number
  signals: DiagnosticSignalEvidence[]
  market_context: {
    trade_date: string
    advance_ratio: number | null
    market_turnover: number | null
    overall_status: string
  }
  industry_context: {
    industry: string | null
    turnover_share: number | null
    advance_ratio: number | null
    relative_strength_5d: number | null
    relative_strength_20d: number | null
  }
}

export interface TaiwanAbnormalDiagnosticsSnapshot {
  trade_date: string
  generated_at: string
  universe_count: number
  diagnostic_count: number
  items: TaiwanAbnormalDiagnosticItem[]
  data_quality: {
    target_trade_date: string
    universe_supported_count: number
    evaluated_symbol_count: number
    diagnostic_symbol_count: number
    daily_status: 'current' | 'stale' | 'unavailable'
    institutional_status: 'current' | 'stale' | 'unavailable'
    margin_status: 'current' | 'stale' | 'unavailable'
    overall_status: 'complete' | 'partial' | 'unavailable'
  }
  provenance: string[]
}

export interface ObservationItem {
  text: string
  evidence_refs: string[]
}

export interface TaiwanAIStockResearchReport {
  symbol: string
  code: string
  name: string
  industry: string | null
  instrument_type: string
  evidence_as_of: string
  generated_at: string
  prompt_version: string
  overview: string
  market_interpretation?: string | null
  industry_interpretation?: string | null
  price_technical_interpretation?: string | null
  institutional_interpretation?: string | null
  margin_interpretation?: string | null
  fundamentals_interpretation?: string | null
  abnormal_diagnostics_interpretation?: string | null
  key_observations: ObservationItem[]
  risk_factors: ObservationItem[]
  missing_information: string[]
  disclaimer: string
}

export interface TaiwanAIResearchResponse {
  status: 'success' | 'unavailable' | 'error'
  error_code?: string | null
  error_message?: string | null
  report?: TaiwanAIStockResearchReport | null
  provider?: string | null
  model?: string | null
  prompt_version: string
  evidence_as_of?: string | null
  generated_at: string
  evidence_registry_keys: string[]
}

// ── Phase 7G: Multi-Stock Objective Research Comparison ──────

export interface ComparisonInstrumentResult {
  symbol: string
  context: TaiwanStockResearchContext
  diagnostic_item?: TaiwanAbnormalDiagnosticItem | null
}

export interface TaiwanStockComparisonResponse {
  symbols_requested: string[]
  comparison_date: string
  generated_at: string
  instruments: ComparisonInstrumentResult[]
  unsupported_symbols: string[]
}

export interface ComparisonObservationItem {
  text: string
  evidence_refs: string[]
}

export interface TaiwanComparisonAIStockResearchReport {
  symbols: string[]
  comparison_date: string
  generated_at: string
  prompt_version: string
  provider?: string | null
  model?: string | null
  comparison_overview: string
  price_technical_comparison?: string | null
  institutional_comparison?: string | null
  margin_comparison?: string | null
  fundamentals_comparison?: string | null
  abnormal_diagnostics_comparison?: string | null
  key_observations: ComparisonObservationItem[]
  risk_factors: ComparisonObservationItem[]
  missing_information: string[]
  disclaimer: string
}

export interface TaiwanComparisonAIResearchResponse {
  status: 'success' | 'unavailable' | 'error'
  error_code?: string | null
  error_message?: string | null
  report?: TaiwanComparisonAIStockResearchReport | null
  provider?: string | null
  model?: string | null
  prompt_version: string
  comparison_date?: string | null
  generated_at: string
  evidence_registry_keys: string[]
}

/** 生成监控规则 id (时间戳 + 随机后缀), 用户无需手动填写。 */
export function genRuleId(): string {
  const ts = Date.now().toString(36)
  const rand = Math.random().toString(36).slice(2, 6)
  return `mr_${ts}_${rand}`
}


// ===== Limit Ladder =====
export interface LimitLadderStock {
  symbol: string
  name?: string | null
  close?: number | null
  change_pct?: number | null
  consecutive_limit_ups?: number | null
  consecutive_limit_downs?: number | null
  status?: 'limit_up' | 'broken' | 'failed' | 'limit_down' | 'recovery' | null
  /** 五档 sealed: real=真封板, fake=假涨停(已归炸板), pending=待确认, null=降级/无能力 */
  sealed_status?: 'real' | 'fake' | 'pending' | null
  /** 封单量(买一/卖一量), 仅真封板有值 */
  sealed_vol?: number | null
  /** 最终状态为涨跌停且当天开高低收四价相同 */
  is_one_word?: boolean
}

export interface LimitLadderTier {
  boards: number
  count: number
  stocks: LimitLadderStock[]
}

export interface LimitLadderResult {
  as_of: string
  tiers: LimitLadderTier[]
  /** 双方向涨跌停计数(修正后, 不论当前 direction) */
  counts?: { up: number; down: number }
  /** 双方向涨跌停原始计数(修正前, 供弹窗对比) */
  counts_raw?: { up: number; down: number }
  /** sealed 数据是否就绪(false→前端显示降级标识) */
  sealed_ready?: boolean
  /** sealed 数据 age(秒), null=盘后定版或无数据 */
  sealed_age?: number | null
  /** sealed 修正统计: real=真封板, fake=假涨停(归炸板), pending=待确认 */
  sealed_counts?: { real: number; fake: number; pending: number }
  /** 涨停侧 sealed 明细 */
  sealed_counts_up?: { real: number; fake: number; pending: number }
  /** 跌停侧 sealed 明细 */
  sealed_counts_down?: { real: number; fake: number; pending: number }
}

// ===== Backtest =====
export interface BacktestResult {
  run_id: string
  config: any
  stats: Record<string, any>
  equity_curve: { date: string; value: number }[]
  trades: any[]
  per_symbol_stats: { symbol: string; total_return: number }[]
}

// ===== Factor Backtest =====
export interface FactorColumn {
  id: string
  label: string
  group: string
  desc: string
}

export interface GroupStat {
  group: number
  label: string
  total_return: number
  annual_return: number
  max_drawdown: number
  sharpe: number
  win_rate: number
}

export interface FactorBacktestResult {
  run_id: string
  config: Record<string, any>
  ic_mean: number | null
  ic_std: number | null
  ir: number | null
  ic_win_rate: number | null
  ic_series: { date: string; ic: number }[]
  group_stats: GroupStat[]
  group_nav: Record<string, any>[]
  long_short_stats: Record<string, any>
  long_short_nav: { date: string; value: number }[]
  elapsed_ms: number
  n_symbols: number
  n_dates: number
  error: string | null
}

export interface FactorBatchItem {
  factor_name: string
  label: string
  group: string
  ic_mean: number | null
  ir: number | null
  ic_win_rate: number | null
  long_short_return: number | null
  long_short_max_drawdown: number | null
  n_symbols: number
  n_dates: number
  elapsed_ms: number
  error: string | null
}

export interface FactorBatchResult {
  run_id: string
  config: Record<string, any>
  results: FactorBatchItem[]
  elapsed_ms: number
  n_symbols: number
  n_dates: number
  error: string | null
}

// ===== Factor / strategy mining =====
export type MiningBudgetProfile = 'exploratory' | 'balanced' | 'strict'
export type MiningRunStatus =
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'succeeded'
  | 'succeeded_with_budget_exhausted'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
  | 'skipped_prerequisite'

export interface MiningAvailability {
  asset_type: 'stock' | 'etf'
  budget_profile: MiningBudgetProfile
  trading_bars: number
  required_bars: number
  outer_folds: number
  required_outer_folds: number
  eligible: boolean
  available_start: string | null
  available_end: string | null
  effective_start: string | null
  effective_end: string | null
  suggested_start: string | null
}

export interface MiningRequestV1 {
  factor_names: string[]
  strategy_ids?: string[]
  symbols?: string[] | null
  asset_type?: 'stock' | 'etf'
  start?: string | null
  end?: string | null
  budget_profile?: MiningBudgetProfile
  commission_pct?: number
  stamp_tax_pct?: number
  slippage_bps?: number
  correlation_threshold?: number
  max_combination_factors?: number
  beam_width?: number
  max_finalists?: number
  force?: boolean
}

export interface MiningRunProgress {
  phase: string
  label?: string
  done?: number
  total?: number
  percent?: number
  elapsed_ms?: number
  message?: string
}

export interface MiningRun {
  run_id: string
  signature: string
  status: MiningRunStatus
  request: MiningRequestV1
  source?: 'manual' | 'scheduled'
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  data_as_of?: string | null
  progress?: MiningRunProgress | null
  error?: string | null
  reused?: boolean
  summary?: MiningResultSummary | null
}

export interface MiningResultSummary {
  factor_count: number
  selected_factor_count: number
  candidate_count: number
  valid_fold_count: number
  skipped_fold_count: number
  confidence: 'low' | 'standard' | 'high'
  budget_exhausted?: boolean
  elapsed_ms?: number
  peak_rss_bytes?: number
}

export interface MiningFactorRow {
  factor_name: string
  label?: string
  direction: 1 | -1
  score: number | null
  ic_mean: number | null
  ir: number | null
  coverage: number | null
  turnover: number | null
  spread_return?: number | null
  spread_sharpe?: number | null
  selected: boolean
  excluded_reason?: string | null
}

export interface MiningRegimeRow {
  state: 'overall' | 'strong' | 'range' | 'weak' | string
  label: string
  n_dates: number
  total_return: number | null
  sharpe: number | null
  max_drawdown: number | null
}

export interface MiningFoldRow {
  fold: number
  label?: string
  train_start?: string
  train_end?: string
  test_start?: string
  test_end?: string
  selected_factors?: string[]
  total_return: number | null
  sharpe: number | null
  max_drawdown?: number | null
  n_trades?: number | null
  skipped?: boolean
  reason?: string | null
  evaluation_kind?: 'selected' | 'cross' | 'benchmark' | null
}

export interface MiningCandidateGate {
  qualified: boolean
  reasons: string[]
}

export interface MiningCandidateRow {
  signature: string
  name: string
  kind: 'factor_combination' | 'existing_strategy'
  factor_names?: string[]
  strategy_id?: string | null
  regime_state?: string | null
  score: number | null
  oos_return: number | null
  oos_sharpe: number | null
  oos_max_drawdown: number | null
  oos_positive_fold_ratio: number | null
  oos_n_trades: number | null
  confidence: 'low' | 'standard' | 'high'
  valid_folds?: number | null
  skipped_folds?: number | null
  promoted_candidate_id?: string | null
  published_strategy_id?: string | null
  gate?: MiningCandidateGate | null
  folds?: MiningFoldRow[]
}

export interface MiningTelemetry {
  elapsed_ms?: number
  peak_rss_bytes?: number
  panel_scans?: number
  matrix_bytes?: number
  cache_hits?: number
  fold_reuses?: number
  serialized_result_bytes?: number
  phase_ms?: Record<string, number>
}

export interface MiningRequestSummary {
  asset_type: string
  budget_profile: string
  start: string | null
  end: string | null
  factor_count: number
  strategy_count: number
  commission_pct: number | null
  stamp_tax_pct: number | null
  slippage_bps: number | null
  correlation_threshold: number | null
}

export interface MiningResult {
  run_id: string
  methodology_version: string
  algorithm_version: string
  data_as_of: string | null
  summary: MiningResultSummary
  request_summary?: MiningRequestSummary | null
  factors: MiningFactorRow[]
  correlation: {
    labels: string[]
    matrix: (number | null)[][]
    pair_counts?: (number | null)[][]
    threshold: number
  }
  regimes: MiningRegimeRow[]
  candidates: MiningCandidateRow[]
  folds: MiningFoldRow[]
  telemetry: MiningTelemetry
}

export interface MiningEvent {
  id: number
  type: string
  timestamp?: string
  payload?: Record<string, unknown>
  message?: string
}

export interface MiningScheduleConfig {
  mining_schedule_enabled: boolean
  mining_schedule_weekday: number
  mining_budget_profile: Exclude<MiningBudgetProfile, 'exploratory'>
}

export type ResearchCandidateKind = 'factor' | 'strategy'
export type ResearchCandidateStatus = 'pending' | 'validated' | 'rejected'

export interface ResearchCandidate {
  id: string
  kind: ResearchCandidateKind
  name: string
  source_id: string
  config: Record<string, unknown>
  metrics: Record<string, number | string | boolean | null>
  data_as_of: string | null
  status: ResearchCandidateStatus
  created_at: string
  updated_at: string
}

export interface ResearchCandidateCreate {
  kind: ResearchCandidateKind
  name: string
  source_id: string
  config: Record<string, unknown>
  metrics: Record<string, number | string | boolean | null>
  data_as_of?: string | null
  status?: ResearchCandidateStatus
}

// ===== Strategy Backtest =====
export interface StrategyBacktestTrade {
  symbol: string
  name?: string
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  pnl_pct: number
  duration: number
  exit_reason: string
  shares?: number
  lots?: number
  position_pct?: number
  entry_value?: number
  exit_value?: number
  pnl_amount?: number
  entry_score?: number | null
  entry_signal_date?: string | null
  exit_signal_date?: string | null
  blocked_exit_days?: number
  entry_signal_id?: string | null
  exit_signal_id?: string | null
}

export interface StrategyBacktestResult {
  run_id: string
  config: Record<string, any>
  stats: Record<string, any>
  equity_curve: { date: string; value: number; cash?: number; positions?: number; exposure?: number }[]
  drawdown_curve: { date: string; value: number }[]
  benchmark_curve?: { date: string; value: number; close?: number; name?: string; symbol?: string }[]
  trades: StrategyBacktestTrade[]
  per_symbol_stats: {
    symbol: string
    n_trades: number
    total_return: number
    win_rate: number
    best: number
    worst: number
  }[]
  strategy_info: {
    id: string
    name: string
    description: string
    entry_signals: string[]
    exit_signals: string[]
    stop_loss: number | null
    take_profit: number | null
    trailing_stop: number | null
    trailing_take_profit_activate: number | null
    trailing_take_profit_drawdown: number | null
    score_min: number | null
    score_max: number | null
    max_hold_days: number | null
    source: string
    execution_backend?: string
    // 叠加策略回测: 子策略构成与权重归因
    composite_children?: { id: string; weight: number }[]
  }
  elapsed_ms: number
  error: string | null
}

// ===== Settings =====

/** 端点发现清单 —— 对应 tickflow.org/endpoints.json */
export interface EndpointItem {
  id: string
  url: string
  label: string
  region?: string
  description?: string
  premium?: boolean
}

export interface EndpointManifest {
  version?: number
  description?: string
  healthPath?: string
  /** 每端点测试轮数,用于 /health 多轮探测取中位数 */
  testRounds?: number
  endpoints: EndpointItem[]
  /** 数据来源:remote=远程拉取 / fallback=内置回退列表 */
  source?: 'remote' | 'fallback'
}

export interface SettingsState {
  mode: 'none' | 'free' | 'api_key'
  tickflow_api_key_masked: string
  has_tickflow_key: boolean
  tier_label: string
  current_endpoint: string
  probe_log: string[]
  missing_caps: string[]
  extras_caps: string[]
  // 首次使用引导
  onboarding_completed: boolean
  // AI 配置
  ai_provider: string
  ai_base_url: string
  ai_api_key_masked: string
  has_ai_key: boolean
  ai_configured?: boolean
  ai_model: string
  ai_openai_model?: string
  ai_reasoning_effort?: string
  ai_codex_model?: string
  ai_codex_command?: string
  ai_codex_reasoning_effort?: string
  ai_user_agent: string
  ai_max_output_tokens?: number
  ai_context_window?: number
}

/** 保存 TickFlow Key 的响应(先探后存) */
export interface SaveTickflowKeyResult {
  ok: boolean
  /** ok=false 且 key 无效时的原因标识,前端据此提示「Key 无效」 */
  reason?: 'invalid'
  error?: string
  mode?: 'none' | 'free' | 'api_key'
  tier_label?: string
  current_endpoint?: string
  tickflow_api_key_masked?: string
  capabilities_count?: number
}

export interface DataSourceItem {
  name: string
  display_name: string
  datasets: string[]
  path?: string | null
}

/** 内置可选插件数据源 (plugins/ 目录, 需手动装依赖) */
export interface PluginDataSourceItem {
  name: string
  display_name: string
  datasets: string[]
  runtime: string          // node | python | none
  available: boolean       // 依赖是否已安装
  status: string           // 可用性原因 (供 UI 显示)
  description: string
  install_hint: string     // 未装依赖时显示的安装命令
  api_key_env?: string     // 声明后设置页提供 Key 输入框 (先探后存)
}

export interface DataSourceLoadError {
  name?: string
  path: string
  errors: string[]
}

export interface DataSourcesResponse {
  builtin: DataSourceItem[]
  plugins: PluginDataSourceItem[]
  custom: DataSourceItem[]
  errors: DataSourceLoadError[]
  config_dir: string
}

export interface DataSourceTestResult {
  provider: string
  dataset: string
  rows: number
  columns: string[]
  preview: Record<string, unknown>[]
}

/** 插件 Key 保存结果 (先探后存: 无效 Key 返回 ok=false 且不落盘) */
export interface PluginKeyResult {
  ok: boolean
  reason?: string
  error?: string
  api_key_masked?: string
  plugin_available?: boolean
  plugin?: PluginDataSourceItem | null
}

export interface DatasetConfig {
  url: string
  method: string
  batch?: number | null
  rpm?: number | null
  response_path: string
  field_map: Record<string, string>
  transforms?: Record<string, string>
  symbols_param?: string
  start_param?: string
  end_param?: string
  asset_type_param?: string | null
  freq_param?: string | null
  timeout?: number | null
}

export interface AuthConfig {
  type: string
  token_env?: string | null
  header?: string
  param?: string
}

export interface CustomSourceConfig {
  name: string
  display_name: string
  auth: AuthConfig
  datasets: Record<string, DatasetConfig>
}

export interface WecomBotStatus {
  enabled: boolean
  running: boolean
  connected: boolean
  bot_id_configured: boolean
  secret_configured: boolean
  last_error: string
}

export interface Preferences {
  realtime_quotes_enabled: boolean
  indices_nav_pinned: boolean
  watchlist_groups_in_nav: boolean
  show_ashare_legacy_features: boolean
  minute_sync_enabled: boolean
  minute_sync_days: number
  minute_sync_segment_days: number
  daily_data_provider?: string
  adj_factor_provider?: string
  minute_data_provider?: string
  realtime_data_provider?: string
  financial_data_provider?: string
  data_source_job_timeout_s: number
  data_source_long_job_timeout_s: number
  realtime_watchlist_symbols?: string[]
  realtime_pull_stock?: boolean
  realtime_pull_etf?: boolean
  realtime_pull_index?: boolean
  realtime_index_mode?: 'core' | 'all'
  realtime_index_symbols?: string[]
  pipeline_pull_a_share: boolean
  pipeline_pull_etf: boolean
  pipeline_pull_index: boolean
  pipeline_regime_enabled: boolean
  regime_batch_days: number
  regime_warmup_days: number
  pipeline_index_symbols: string
  pipeline_schedule: { hour: number; minute: number }
  instruments_schedule: { hour: number; minute: number }
  enriched_batch_size: number
  index_daily_batch_size: number
  limit_ladder_monitor_enabled: boolean
  depth_polling_interval: number
  depth_finalize_time: { hour: number; minute: number }
  review_schedule: { enabled: boolean; hour: number; minute: number }
  review_push_channels: string[]
  sse_refresh_pages: Record<string, boolean>
  strategy_monitor_enabled: boolean
  strategy_monitor_ids: string[]
  system_notify_enabled: boolean
  feishu_webhook_url?: string
  feishu_webhook_secret?: string
  wecom_webhook_url?: string
  wecom_bot_id?: string
  wecom_bot_secret?: string
  wecom_bot_enabled?: boolean
  webhook_enabled_default?: boolean
  webhook_default_channels?: string[]
  sidebar_index_symbols: string[]
  nav_order: string[]
  nav_hidden: string[]
  screener_auto_run: boolean
  minute_intraday_refresh: boolean
  minute_intraday_refresh_interval: number
  monitor_ext_fields: { concept: MonitorExtFieldItem | null; industry: MonitorExtFieldItem | null }
}

/** 监控中心 ext 字段单项配置 (行业/概念标签的来源 + 显示裁剪) */
export interface MonitorExtFieldItem {
  /** "configId.fieldName" */
  field: string
  /** 显示前N个标签, 0=不限制 */
  maxTags?: number
  /** 隐藏的位置 (0-based), 如 [0] 表示隐藏第一个 */
  hiddenIndices?: number[]
}
export interface StrategyAlertEvent {
  source: 'strategy' | 'depth'
  type: string
  strategy_id?: string
  symbol?: string
  name?: string | null
  message: string
  price?: number | null
  change_pct?: number | null
  signals?: string[]
  /** ext 富化字段 (行业/概念等), 键为 "{configId}__{fieldName}" */
  [key: string]: unknown
}

// ===== API surface =====
export const api = {
  health: () => request<{ status: string; version: string; mode: string }>('/health'),

  // ===== Auth (访问认证) =====
  authStatus: () =>
    request<{ configured: boolean; authenticated: boolean }>('/api/auth/status'),
  authSetup: (password: string) =>
    request<{ ok: boolean }>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authLogin: (password: string) =>
    request<{ ok: boolean }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authLogout: () =>
    request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  authChangePassword: (oldPassword: string, newPassword: string) =>
    request<{ ok: boolean }>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),

  settings: () => request<SettingsState>('/api/settings'),
  saveTickflowKey: (api_key: string) =>
    request<SaveTickflowKeyResult>('/api/settings/tickflow-key', {
      method: 'POST',
      body: JSON.stringify({ api_key }),
    }),
  clearTickflowKey: () =>
    request<any>('/api/settings/tickflow-key', { method: 'DELETE' }),

  /** 标记首次使用向导完成（持久化到后端 preferences） */
  completeOnboarding: () =>
    request<{ ok: boolean; onboarding_completed: boolean }>(
      '/api/settings/onboarding/complete', { method: 'POST' },
    ),

  /** 保存 AI 配置 */
  saveAiSettings: (ai: { provider?: string; base_url?: string; api_key?: string; model?: string; reasoning_effort?: string; codex_command?: string; codex_reasoning_effort?: string; user_agent?: string; max_output_tokens?: number; context_window?: number }) =>
    request<{ ok: boolean; ai_provider?: string; ai_model?: string; ai_openai_model?: string; ai_reasoning_effort?: string; ai_codex_model?: string; ai_codex_command?: string; ai_codex_reasoning_effort?: string; ai_configured?: boolean; ai_max_output_tokens?: number; ai_context_window?: number }>('/api/settings/ai', {
      method: 'POST',
      body: JSON.stringify(ai),
    }),

  /** 一键清空 AI 配置(保留自定义 UA) */
  clearAiSettings: () =>
    request<{ ok: boolean }>('/api/settings/ai', { method: 'DELETE' }),

  preferences: () => request<Preferences>('/api/settings/preferences'),
  dataSources: () => request<DataSourcesResponse>('/api/settings/data-sources'),
  dataSource: (name: string) => request<CustomSourceConfig>(`/api/settings/data-sources/${encodeURIComponent(name)}`),
  saveDataSource: (config: CustomSourceConfig) =>
    request<DataSourcesResponse>('/api/settings/data-sources', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  deleteDataSource: (name: string) =>
    request<DataSourcesResponse>(`/api/settings/data-sources/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  reloadDataSources: () => request<DataSourcesResponse>('/api/settings/data-sources/reload', { method: 'POST' }),
  installPlugin: (name: string) => {
    // npm install 可能耗时较长, 用 6 分钟超时
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 360_000)
    return request<DataSourcesResponse & { install_ok: boolean; install_message: string }>(
      `/api/settings/plugins/${encodeURIComponent(name)}/install`,
      { method: 'POST', signal: controller.signal },
    ).finally(() => clearTimeout(timer))
  },
  uninstallPlugin: (name: string) =>
    request<DataSourcesResponse & { uninstall_ok: boolean; uninstall_message: string }>(
      `/api/settings/plugins/${encodeURIComponent(name)}/install`,
      { method: 'DELETE' },
    ),
  savePluginKey: (plugin: string, apiKey: string) => {
    // 先探后存: 后端会用候选 Key 实探一次, 探测超时 10s + 余量
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 30_000)
    return request<PluginKeyResult>('/api/settings/plugin-key', {
      method: 'POST',
      body: JSON.stringify({ plugin, api_key: apiKey }),
      signal: controller.signal,
    }).finally(() => clearTimeout(timer))
  },
  clearPluginKey: (plugin: string) =>
    request<PluginKeyResult>(`/api/settings/plugin-key/${encodeURIComponent(plugin)}`, { method: 'DELETE' }),
  testDataSource: (
    provider: string,
    dataset: string,
    symbols?: string[],
    config?: CustomSourceConfig,
  ) =>
    request<DataSourceTestResult>('/api/settings/data-sources/test', {
      method: 'POST',
      body: JSON.stringify({ provider, dataset, symbols, config }),
    }),
  updateDataProviders: (cfg: Partial<Pick<Preferences, 'daily_data_provider' | 'adj_factor_provider' | 'minute_data_provider' | 'realtime_data_provider' | 'financial_data_provider'>>) =>
    request<Pick<Preferences, 'daily_data_provider' | 'adj_factor_provider' | 'minute_data_provider' | 'realtime_data_provider'>>(
      '/api/settings/preferences/data-providers',
      { method: 'PUT', body: JSON.stringify(cfg) },
    ),
  updateDataSourceJobTimeouts: (dataSourceJobTimeoutS: number, dataSourceLongJobTimeoutS: number) =>
    request<Pick<Preferences, 'data_source_job_timeout_s' | 'data_source_long_job_timeout_s'>>(
      '/api/settings/preferences/data-source-job-timeouts',
      {
        method: 'PUT',
        body: JSON.stringify({
          data_source_job_timeout_s: dataSourceJobTimeoutS,
          data_source_long_job_timeout_s: dataSourceLongJobTimeoutS,
        }),
      },
    ),
  updateMinuteSync: (enabled: boolean, days: number, segmentDays?: number) =>
    request<Preferences>('/api/settings/preferences/minute-sync', {
      method: 'PUT',
      body: JSON.stringify({
        minute_sync_enabled: enabled,
        minute_sync_days: days,
        ...(segmentDays != null ? { minute_sync_segment_days: segmentDays } : {}),
      }),
    }),
  updatePipelinePullTypes: (cfg: Partial<Pick<Preferences, 'pipeline_pull_a_share' | 'pipeline_pull_etf' | 'pipeline_pull_index'>>) =>
    request<{
      pipeline_pull_a_share: boolean
      pipeline_pull_etf: boolean
      pipeline_pull_index: boolean
    }>('/api/settings/preferences/pipeline-pull-types', {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  updatePipelineRegimeEnabled: (enabled: boolean) =>
    request<{ pipeline_regime_enabled: boolean }>('/api/settings/preferences/pipeline-regime-enabled', {
      method: 'PUT',
      body: JSON.stringify({ pipeline_regime_enabled: enabled }),
    }),
  updateRegimeBatchParams: (params: { batch_days?: number; warmup_days?: number }) =>
    request<{ regime_batch_days: number; regime_warmup_days: number }>('/api/settings/preferences/regime-batch-params', {
      method: 'PUT',
      body: JSON.stringify(params),
    }),
  updatePipelineIndexSymbols: (symbols: string) =>
    request<{ pipeline_index_symbols: string }>('/api/settings/preferences/pipeline-index-symbols', {
      method: 'PUT',
      body: JSON.stringify({ symbols }),
    }),
  updateRealtimeQuotes: (enabled: boolean) =>
    request<{ realtime_quotes_enabled: boolean; realtime_allowed?: boolean; mode?: string; error?: string }>('/api/settings/preferences/realtime-quotes', {
      method: 'PUT',
      body: JSON.stringify({ realtime_quotes_enabled: enabled }),
    }),
  updateRealtimeQuoteScope: (cfg: Partial<Pick<Preferences, 'realtime_pull_stock' | 'realtime_pull_etf' | 'realtime_pull_index' | 'realtime_index_mode' | 'realtime_index_symbols'>>) =>
    request<Partial<Preferences>>('/api/settings/preferences/realtime-quote-scope', {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  updateIndicesNavPinned: (pinned: boolean) =>
    request<{ indices_nav_pinned: boolean }>('/api/settings/preferences/indices-nav-pinned', {
      method: 'PUT',
      body: JSON.stringify({ indices_nav_pinned: pinned }),
    }),
  updateWatchlistGroupsInNav: (enabled: boolean) =>
    request<{ watchlist_groups_in_nav: boolean }>('/api/settings/preferences/watchlist-groups-in-nav', {
      method: 'PUT',
      body: JSON.stringify({ watchlist_groups_in_nav: enabled }),
    }),
  updateShowAshareLegacyFeatures: (enabled: boolean) =>
    request<{ show_ashare_legacy_features: boolean }>('/api/settings/preferences/show-ashare-legacy-features', {
      method: 'PUT',
      body: JSON.stringify({ show_ashare_legacy_features: enabled }),
    }),
  quoteStatus: () =>
    request<{
      enabled: boolean
      running: boolean
      paused?: boolean
      mode?: 'none' | 'watchlist' | 'full_market'
      realtime_allowed?: boolean
      interval_s: number
      symbol_count: number
      watchlist_symbol_count?: number
      index_symbol_count?: number
      etf_symbol_count?: number
      quote_age_ms: number | null
      is_trading_hours: boolean
      is_polling_window?: boolean
      market_phase?: string
      final_sync_done?: boolean
      final_sync_failed?: string | null
      last_fetch_ms: number | null
    }>('/api/intraday/status'),
  quoteInterval: () =>
    request<{ interval: number; min_interval: number; max_interval: number }>(
      '/api/settings/preferences/quote-interval',
    ),
  updateQuoteInterval: (interval: number) =>
    request<{ interval: number; min_interval: number; max_interval: number }>(
      '/api/settings/preferences/quote-interval',
      { method: 'PUT', body: JSON.stringify({ interval }) },
    ),
  intradayRefresh: () => request<{ status: string }>('/api/intraday/refresh', { method: 'POST' }),
  indexQuotes: (symbols?: string[]) =>
    request<{ rows: IndexQuote[]; count: number }>(
      `/api/intraday/indices${symbols?.length ? `?symbols=${encodeURIComponent(symbols.join(','))}` : ''}`,
    ),
  updateRealtimeMonitorConfig: (cfg: {
    sse_refresh_pages?: Record<string, boolean>
    strategy_monitor_enabled?: boolean
    strategy_monitor_ids?: string[]
    sidebar_index_symbols?: string[]
    screener_auto_run?: boolean
    minute_intraday_refresh?: boolean
    minute_intraday_refresh_interval?: number
    monitor_ext_fields?: { concept: MonitorExtFieldItem | null; industry: MonitorExtFieldItem | null }
  }) =>
    request<{
      sse_refresh_pages: Record<string, boolean>
      strategy_monitor_enabled: boolean
      strategy_monitor_ids: string[]
      sidebar_index_symbols: string[]
      screener_auto_run: boolean
      minute_intraday_refresh: boolean
      minute_intraday_refresh_interval: number
      monitor_ext_fields: { concept: MonitorExtFieldItem | null; industry: MonitorExtFieldItem | null }
    }>('/api/settings/preferences/realtime-monitor', {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  updateSystemNotify: (enabled: boolean) =>
    request<{ system_notify_enabled: boolean }>('/api/settings/preferences/system-notify', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  updateFeishuWebhook: (url: string, secret: string = '') =>
    request<{ feishu_webhook_url: string; feishu_webhook_secret: string }>('/api/settings/preferences/feishu-webhook', {
      method: 'PUT',
      body: JSON.stringify({ url, secret }),
    }),
  updateWecomWebhook: (url: string) =>
    request<{ wecom_webhook_url: string }>('/api/settings/preferences/wecom-webhook', {
      method: 'PUT',
      body: JSON.stringify({ url }),
    }),
  updateWecomBot: (botId: string, secret: string, enabled: boolean = true) =>
    request<{
      wecom_bot_id: string
      wecom_bot_secret: string
      wecom_bot_enabled: boolean
      wecom_bot_status: WecomBotStatus
    }>('/api/settings/preferences/wecom-bot', {
      method: 'PUT',
      body: JSON.stringify({ bot_id: botId, secret, enabled }),
    }),
  toggleWecomBot: (enabled: boolean) =>
    request<{ wecom_bot_enabled: boolean; wecom_bot_status: WecomBotStatus }>('/api/settings/preferences/wecom-bot-toggle', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  updateWebhookDefault: (enabled: boolean) =>
    request<{ webhook_enabled_default: boolean }>('/api/settings/preferences/webhook-enabled-default', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  updateWebhookDefaultChannels: (channels: string[]) =>
    request<{ webhook_default_channels: string[] }>('/api/settings/preferences/webhook-default-channels', {
      method: 'PUT',
      body: JSON.stringify({ channels }),
    }),
  updatePipelineSchedule: (hour: number, minute: number) =>
    request<{ hour: number; minute: number }>('/api/settings/preferences/pipeline-schedule', {
      method: 'PUT',
      body: JSON.stringify({ hour, minute }),
    }),
  updateReviewSchedule: (enabled: boolean, hour: number, minute: number) =>
    request<{ enabled: boolean; hour: number; minute: number }>('/api/settings/preferences/review-schedule', {
      method: 'PUT',
      body: JSON.stringify({ enabled, hour, minute }),
    }),
  updateReviewPush: (channels: string[]) =>
    request<{ review_push_channels: string[] }>('/api/settings/preferences/review-push', {
      method: 'PUT',
      body: JSON.stringify({ channels }),
    }),
  updateDepthPollingInterval: (interval: number) =>
    request<{ depth_polling_interval: number }>('/api/settings/preferences/depth-polling-interval', {
      method: 'PUT',
      body: JSON.stringify({ interval }),
    }),
  updateLimitLadderMonitor: (enabled: boolean) =>
    request<{ limit_ladder_monitor_enabled: boolean }>('/api/settings/preferences/limit-ladder-monitor', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  runLimitLadderFix: () =>
    request<{ ok: boolean; count: number; msg: string }>('/api/settings/preferences/limit-ladder-monitor/run', {
      method: 'POST',
    }),
  updateDepthFinalizeTime: (hour: number, minute: number) =>
    request<{ hour: number; minute: number }>('/api/settings/preferences/depth-finalize-time', {
      method: 'PUT',
      body: JSON.stringify({ hour, minute }),
    }),
  saveNavOrder: (nav_order: string[]) =>
    request<{ nav_order: string[] }>('/api/settings/preferences/nav-order', {
      method: 'PUT',
      body: JSON.stringify({ nav_order }),
    }),
  saveNavHidden: (nav_hidden: string[]) =>
    request<{ nav_hidden: string[] }>('/api/settings/preferences/nav-hidden', {
      method: 'PUT',
      body: JSON.stringify({ nav_hidden }),
    }),
  updateInstrumentsSchedule: (hour: number, minute: number) =>
    request<{ hour: number; minute: number }>('/api/settings/preferences/instruments-schedule', {
      method: 'PUT',
      body: JSON.stringify({ hour, minute }),
    }),
  updateEnrichedBatchSize: (size: number) =>
    request<{ enriched_batch_size: number }>('/api/settings/preferences/enriched-batch-size', {
      method: 'PUT',
      body: JSON.stringify({ size }),
    }),
  updateIndexDailyBatchSize: (size: number) =>
    request<{ index_daily_batch_size: number }>('/api/settings/preferences/index-daily-batch-size', {
      method: 'PUT',
      body: JSON.stringify({ size }),
    }),

  // 自选列表列配置
  watchlistColumns: () =>
    request<{ columns: any[] | null }>('/api/settings/preferences/watchlist-columns'),
  updateWatchlistColumns: (columns: any[]) =>
    request<{ columns: any[] }>('/api/settings/preferences/watchlist-columns', {
      method: 'PUT',
      body: JSON.stringify({ columns }),
    }),

  // 策略结果列表列配置
  screenerResultColumns: () =>
    request<{ columns: any[] | null }>('/api/settings/preferences/screener-result-columns'),
  updateScreenerResultColumns: (columns: any[]) =>
    request<{ columns: any[] }>('/api/settings/preferences/screener-result-columns', {
      method: 'PUT',
      body: JSON.stringify({ columns }),
    }),

  capabilities: () => request<CapabilitiesResponse>('/api/capabilities'),
  version: () => request<{ version: string }>('/api/data/version'),
  redetectCapabilities: () =>
    request<CapabilitiesResponse>('/api/capabilities/redetect', { method: 'POST' }),

  klineDaily: (symbol: string, days = 120, dateRange?: { start: string; end: string }, extColumns?: string) =>
    request<{
      symbol: string
      name?: string
      stock_info?: { name?: string; total_shares?: number; float_shares?: number; ext?: Record<string, unknown> }
      rows: KlineRow[]
      source?: string
    }>(
      (dateRange
        ? `/api/kline/daily?symbol=${encodeURIComponent(symbol)}&start_date=${dateRange.start}&end_date=${dateRange.end}`
        : `/api/kline/daily?symbol=${encodeURIComponent(symbol)}&days=${days}`)
      + (extColumns ? `&ext_columns=${encodeURIComponent(extColumns)}` : ''),
    ),
  klineDailyBatch: (symbols: string[], days = 12) =>
    request<{ data: Record<string, KlineRow[]> }>('/api/kline/daily-batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, days }),
    }),
  klineMinuteBatch: (symbols: string[], date?: string) =>
    request<{ data: Record<string, MinuteKlineRow[]> }>('/api/kline/minute-batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, date }),
    }),
  /**
   * market 省略 = 既有 A 股 legacy 行为(Financials 搜索 / A 股监控规则编辑器 /
   * A 股回测标的选择器等既有调用方不用改)。market='taiwan' 只查
   * TaiwanSecurityMaster, 不会混入 .SH/.SZ/.BJ 结果 —— 自选股搜索用这个。
   */
  instrumentSearch: (q: string, limit = 20, assetTypes?: string, market?: 'ashare' | 'taiwan' | 'all') =>
    request<{ results: { symbol: string; name: string; code: string; asset_type?: string; market: 'ashare' | 'taiwan' }[] }>(
      `/api/kline/instruments/search?q=${encodeURIComponent(q)}&limit=${limit}${assetTypes ? `&asset_types=${encodeURIComponent(assetTypes)}` : ''}${market ? `&market=${market}` : ''}`,
    ),

  /** 批量查股票名称 (传入 symbol 列表, 返回 {symbol: name}) */
  instrumentNames: (symbols: string[]) =>
    request<{ names: Record<string, string> }>('/api/kline/instruments/names', {
      method: 'POST',
      body: JSON.stringify(symbols),
    }),
  klineMinute: (symbol: string, date?: string) =>
    request<{
      symbol: string
      name?: string
      stock_info?: { name?: string; total_shares?: number; float_shares?: number }
      date: string | null
      rows: MinuteKlineRow[]
      source?: 'local' | 'live' | 'none'
      asset_type?: 'stock' | 'etf' | 'index'
      price_limit?: PriceLimitInfo | null
      prev_close?: number | null
    }>(
      `/api/kline/minute?symbol=${encodeURIComponent(symbol)}${date ? `&date=${date}` : ''}`,
    ),
  klineMinuteRange: (symbol: string, days = 10) =>
    request<{
      symbol: string
      name?: string
      asset_type: 'stock' | 'etf' | 'index'
      requested_days: number
      sessions: MinuteKlineSession[]
      source: 'local' | 'none'
    }>(
      `/api/kline/minute-range?symbol=${encodeURIComponent(symbol)}&days=${days}`,
    ),
  syncIndexDaily: (days = 365) =>
    request<{ status: string; index_count: number; rows_written: number }>(
      `/api/index/sync_daily?days=${days}`,
      { method: 'POST' },
    ),
  syncSymbol: (symbol: string, days = 250) =>
    request<{ symbol: string; rows_written: number }>(
      `/api/kline/sync?symbol=${encodeURIComponent(symbol)}&days=${days}`,
      { method: 'POST' },
    ),
  syncMinute: (days?: number, extend?: boolean) =>
    request<{ status: string; job_id: string }>('/api/kline/sync_minute', {
      method: 'POST',
      body: JSON.stringify({ ...(days ? { days } : {}), ...(extend ? { extend: true } : {}) }),
    }),
  syncMinuteSingle: (symbol: string, days?: number) =>
    request<{ status: string; symbol: string; rows: number }>('/api/kline/sync_minute_single', {
      method: 'POST',
      body: JSON.stringify({ symbol, ...(days != null ? { days } : {}) }),
    }),
  clearMinute: () =>
    request<{ status: string; removed: number }>('/api/kline/clear_minute', {
      method: 'POST',
      body: JSON.stringify({ confirm: true }),
    }),
  extendHistory: (value: number, unit: 'day' | 'month' | 'year') =>
    request<{ status: string; job_id: string }>('/api/kline/extend_history', {
      method: 'POST',
      body: JSON.stringify({ value, unit }),
    }),
  repairDaily: (startDate: string) =>
    request<{ status: string; job_id: string }>('/api/kline/repair_daily', {
      method: 'POST',
      body: JSON.stringify({ start_date: startDate }),
    }),
  rebuildEnriched: () =>
    request<{ status: string; job_id: string }>('/api/kline/rebuild_enriched', {
      method: 'POST',
    }),

  watchlistList: () => request<{ symbols: WatchlistEntry[] }>('/api/watchlist'),
  watchlistAdd: (symbol: string, note = '', groupId?: string | null) =>
    request<{ symbols: WatchlistEntry[] }>('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ symbol, note, group_id: groupId ?? null }),
    }),
  watchlistBatchAdd: (symbols: string[], note = '', groupId?: string | null) =>
    request<{ symbols: WatchlistEntry[]; added: number }>('/api/watchlist/batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, note, group_id: groupId ?? null }),
    }),
  watchlistGroups: () =>
    request<{ groups: WatchlistGroup[] }>('/api/watchlist/groups'),
  watchlistGroupCreate: (name: string, color: WatchlistGroupColor) =>
    request<{ groups: WatchlistGroup[]; group: WatchlistGroup }>('/api/watchlist/groups', {
      method: 'POST',
      body: JSON.stringify({ name, color }),
    }),
  watchlistGroupRename: (groupId: string, name: string, color: WatchlistGroupColor) =>
    request<{ groups: WatchlistGroup[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}`,
      { method: 'PUT', body: JSON.stringify({ name, color }) },
    ),
  watchlistGroupReorder: (orderedIds: string[]) =>
    request<{ groups: WatchlistGroup[] }>('/api/watchlist/groups/reorder', {
      method: 'PUT',
      body: JSON.stringify({ ordered_ids: orderedIds }),
    }),
  watchlistGroupDelete: (groupId: string) =>
    request<{ groups: WatchlistGroup[]; symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}`,
      { method: 'DELETE' },
    ),
  watchlistGroupClear: (groupId: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}/clear`,
      { method: 'POST' },
    ),
  watchlistSetGroup: (symbol: string, groupId: string | null) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}/group`,
      { method: 'PUT', body: JSON.stringify({ group_id: groupId }) },
    ),
  watchlistGroupAddMember: (groupId: string, symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(symbol)}`,
      { method: 'POST' },
    ),
  watchlistGroupRemoveMember: (groupId: string, symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(symbol)}`,
      { method: 'DELETE' },
    ),
  watchlistOcrStatus: () =>
    request<{ provider: string; available: boolean }>('/api/watchlist/ocr-status'),
  watchlistImportImage: (file: File, signal?: AbortSignal, quiet = false) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<WatchlistImportResult>('/api/watchlist/import-image', {
      method: 'POST',
      body: fd,
      signal,
      quiet,
    })
  },
  watchlistRemove: (symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}`,
      { method: 'DELETE' },
    ),
  watchlistMoveToTop: (symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}/top`,
      { method: 'POST' },
    ),
  watchlistClear: () =>
    request<{ removed: number }>('/api/watchlist', { method: 'DELETE' }),
  watchlistQuotes: () => request<{ quotes: Quote[] }>('/api/watchlist/quotes'),
  watchlistEnriched: (extColumns?: string) =>
    request<{ rows: any[]; as_of: string | null; elapsed_ms: number }>(
      extColumns
        ? `/api/watchlist/enriched?ext_columns=${encodeURIComponent(extColumns)}`
        : '/api/watchlist/enriched',
    ),

  screenerStrategies: async (assetType?: 'stock' | 'etf' | 'index') => {
    const data = await request<{ strategies: StrategyDetail[]; load_errors?: StrategyLoadError[] }>(
      `/api/strategies?${assetType ? `asset_type=${assetType}&` : ''}timeframe=1d`,
    )
    return { presets: data.strategies, load_errors: data.load_errors }
  },
  screenerRunPreset: (strategy_id: string, pool?: string[], asOf?: string, extColumns?: string, assetType: 'stock' | 'etf' = 'stock') =>
    request<ScreenerResult>('/api/screener/run_preset', {
      method: 'POST',
      body: JSON.stringify({ strategy_id, pool, as_of: asOf ?? null, ext_columns: extColumns || null, asset_type: assetType }),
    }),
  screenerRunCustom: (conditions: string[], orderBy?: string, limit = 30, pool?: string[], extColumns?: string, assetType: 'stock' | 'etf' = 'stock') =>
    request<ScreenerResult>('/api/screener/run', {
      method: 'POST',
      body: JSON.stringify({ conditions, order_by: orderBy, limit, pool, ext_columns: extColumns || null, asset_type: assetType }),
    }),
  screenerRunAll: (asOf?: string, strategyIds?: string[], assetType: 'stock' | 'etf' = 'stock') =>
    request<{ as_of: string | null; results: Record<string, ScreenerResultSummary> }>(
      '/api/screener/run_all', { method: 'POST', body: JSON.stringify({ as_of: asOf ?? null, strategy_ids: strategyIds ?? null, asset_type: assetType, timeframe: '1d', summary_only: true }) },
    ),
  screenerCachedSummary: () =>
    request<ScreenerCachedSummary>('/api/screener/cached-summary'),
  screenerCachedResult: (strategyId: string, extColumns?: string) =>
    request<ScreenerCachedResult>(
      extColumns
        ? `/api/screener/cached-result/${encodeURIComponent(strategyId)}?ext_columns=${encodeURIComponent(extColumns)}`
        : `/api/screener/cached-result/${encodeURIComponent(strategyId)}`,
    ),
  screenerCached: (extColumns?: string) =>
    request<{ as_of: string | null; results: Record<string, { total: number; as_of: string; rows: any[] }>; today_ever_matched: Record<string, string[]> | null; today_ever_rows: Record<string, Record<string, any>> | null; updated_at: number | null }>(
      extColumns
        ? `/api/screener/cached?ext_columns=${encodeURIComponent(extColumns)}`
        : '/api/screener/cached',
    ),
  marketSnapshot: () =>
    request<{ as_of: string | null; rows: MarketSnapshotRow[] }>('/api/screener/market-snapshot'),
  overviewMarket: (asOf?: string) => request<OverviewMarket>(`/api/overview/market${asOf ? `?as_of=${asOf}` : ''}`),

  // 市场环境(Regime)
  regimeHistory: (start?: string, end?: string, limit?: number) => {
    const params = new URLSearchParams()
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    if (limit) params.set('limit', String(limit))
    const qs = params.toString()
    return request<RegimeHistory>(`/api/regime/history${qs ? `?${qs}` : ''}`)
  },
  regimeLatest: () => request<{ row: RegimeRow | null }>('/api/regime/latest'),
  regimeStates: (days = 60) => request<RegimeStates>(`/api/regime/states?days=${days}`),
  regimeCoverage: () => request<RegimeCoverage>('/api/regime/coverage'),
  regimeRecompute: (start?: string, end?: string) => {
    const params = new URLSearchParams()
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    const qs = params.toString()
    return request<{ ok: boolean; computed: number; phase_days?: number; mainline_rows?: number }>(`/api/regime/recompute${qs ? `?${qs}` : ''}`, { method: 'POST' })
  },
  regimePhases: (start?: string, end?: string) => {
    const params = new URLSearchParams()
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    const qs = params.toString()
    return request<PhaseSegments>(`/api/regime/phases${qs ? `?${qs}` : ''}`)
  },
  regimeMainline: (start?: string, end?: string, top = 10, kind: 'concept' | 'industry' = 'concept') => {
    const params = new URLSearchParams({ top: String(top), kind })
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    return request<MainlineResult>(`/api/regime/mainline?${params.toString()}`)
  },
  regimeMainlineRecompute: () =>
    request<{ ok: boolean; rows: number }>('/api/regime/mainline/recompute', { method: 'POST' }),
  mainlineFilterUpdate: (payload: { min_members?: number; max_members?: number; blacklist?: string[]; exclude_st?: boolean }) =>
    request<MainlineFilter>('/api/settings/preferences/mainline-filter', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  limitLadder: (asOf?: string, extColumns?: string, direction?: 'up' | 'down') => {
    const params = new URLSearchParams()
    if (asOf) params.set('as_of', asOf)
    if (extColumns) params.set('ext_columns', extColumns)
    if (direction === 'down') params.set('direction', 'down')
    const qs = params.toString()
    return request<LimitLadderResult>(
      `/api/screener/limit-ladder${qs ? `?${qs}` : ''}`,
    )
  },

  backtestStatus: () => request<{ available: boolean }>('/api/backtest/status'),

  backtestRun: (payload: {
    symbols: string[]
    entries: string[]
    exits: string[]
    start?: string
    end?: string
    stop_loss_pct?: number
    max_hold_days?: number
    matching?: 'close_t' | 'open_t+1'
    asset_type?: 'stock' | 'etf' | 'index'
  }) =>
    request<BacktestResult>('/api/backtest/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  factorColumns: () =>
    request<{ columns: FactorColumn[] }>('/api/backtest/factor/columns'),

  factorRun: (payload: {
    factor_name: string
    symbols?: string[] | null
    start?: string | null
    end?: string | null
    n_groups?: number
    rebalance?: 'daily' | 'weekly' | 'monthly'
    weight?: 'equal' | 'factor_weight'
    fees_pct?: number
    slippage_bps?: number
    asset_type?: 'stock' | 'etf' | 'index'
  }) =>
    request<FactorBacktestResult>('/api/backtest/factor/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  factorBatch: (payload: {
    factor_names: string[]
    symbols?: string[] | null
    start?: string | null
    end?: string | null
    n_groups?: number
    rebalance?: 'daily' | 'weekly' | 'monthly'
    weight?: 'equal' | 'factor_weight'
    fees_pct?: number
    slippage_bps?: number
    asset_type?: 'stock' | 'etf' | 'index'
  }) =>
    request<FactorBatchResult>('/api/backtest/factor/batch', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  miningRuns: () =>
    request<{ items: MiningRun[] }>('/api/backtest/mining/runs'),

  miningAvailability: (params: {
    assetType: 'stock' | 'etf'
    budgetProfile: MiningBudgetProfile
    start?: string
    end?: string
  }) => {
    const query = new URLSearchParams({
      asset_type: params.assetType,
      budget_profile: params.budgetProfile,
    })
    if (params.start) query.set('start', params.start)
    if (params.end) query.set('end', params.end)
    return request<MiningAvailability>(`/api/backtest/mining/availability?${query}`, {
      quiet: true,
    })
  },

  miningRun: (runId: string) =>
    request<MiningRun>(`/api/backtest/mining/runs/${encodeURIComponent(runId)}`),

  miningStart: (payload: MiningRequestV1) =>
    request<MiningRun>('/api/backtest/mining/runs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  miningResult: (runId: string) =>
    request<MiningResult>(`/api/backtest/mining/runs/${encodeURIComponent(runId)}/result`),

  miningCancel: (runId: string) =>
    request<MiningRun>(`/api/backtest/mining/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    }),

  miningPromote: (runId: string, signature: string) =>
    request<ResearchCandidate>(
      `/api/backtest/mining/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(signature)}/promote`,
      { method: 'POST' },
    ),

  miningPublish: (runId: string, signature: string) =>
    request<{ ok: boolean; strategy_id: string }>(
      `/api/backtest/mining/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(signature)}/publish`,
      { method: 'POST' },
    ),

  miningConfig: () =>
    request<MiningScheduleConfig>('/api/backtest/mining/config'),

  updateMiningConfig: (payload: Partial<MiningScheduleConfig>) =>
    request<MiningScheduleConfig>('/api/backtest/mining/config', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  researchCandidates: () =>
    request<{ items: ResearchCandidate[] }>('/api/backtest/candidates'),

  researchCandidateCreate: (payload: ResearchCandidateCreate) =>
    request<ResearchCandidate>('/api/backtest/candidates', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  researchCandidateUpdate: (
    id: string,
    payload: { name?: string; status?: ResearchCandidateStatus },
  ) =>
    request<ResearchCandidate>(`/api/backtest/candidates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  researchCandidateDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/backtest/candidates/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  strategyBacktestRun: (payload: {
    strategy_id: string
    symbols?: string[] | null
    start?: string | null
    end?: string | null
    params?: Record<string, any> | null
    overrides?: Record<string, any> | null
    matching?: 'close_t' | 'open_t+1'
    entry_fill?: 'close_t' | 'open_t+1' | null
    exit_fill?: 'close_t' | 'open_t+1' | 'signal_next_minute' | null
    fees_pct?: number
    commission_pct?: number
    stamp_tax_pct?: number
    slippage_bps?: number
    max_positions?: number
    initial_capital?: number
    position_sizing?: 'equal' | 'score_weight'
    asset_type?: 'stock' | 'etf' | 'index'
    minute_fill?: boolean
  }) =>
    request<StrategyBacktestResult>('/api/backtest/strategy/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  pipelineRun: () => request<{ job_id: string; reused: boolean }>(
    '/api/pipeline/run', { method: 'POST' },
  ),
  pipelineJob: (id: string) => request<PipelineJob>(`/api/pipeline/jobs/${id}`),
  pipelineJobs: (limit = 20) =>
    request<{ active_id: string | null; jobs: PipelineJobSummary[] }>(
      `/api/pipeline/jobs?limit=${limit}`,
    ),

  dataStatus: () => request<DataStatus>('/api/data/status'),
  dataClear: () => request<{ deleted_files: number }>('/api/data/clear', { method: 'POST' }),
  refreshCache: () => request<{ ok: boolean }>('/api/data/refresh-cache', { method: 'POST' }),
  enrichedSchema: (table: string) => request<EnrichedField[]>(`/api/data/schema/${table}`),

  testEndpoint: (url: string, rounds?: number) =>
    request<{
      ok: boolean
      url: string
      rounds: number
      success: number
      median_ms: number | null
      min_ms?: number | null
      max_ms?: number | null
      /** 兼容旧字段,等于 median_ms */
      latency_ms?: number | null
      error?: string
    }>(
      '/api/settings/test_endpoint', {
        method: 'POST',
        body: JSON.stringify({ url, rounds }),
      },
    ),

  // 端点发现 —— 后端代理拉取 tickflow.org/endpoints.json(前端无法跨域直连)
  listEndpoints: () =>
    request<EndpointManifest>('/api/settings/endpoints'),

  switchEndpoint: (url: string) =>
    request<{ ok: boolean; current_endpoint: string; error?: string }>(
      '/api/settings/switch_endpoint', {
        method: 'POST',
        body: JSON.stringify({ url }),
      },
    ),

  // ===== 扩展数据 =====
  extDataList: () =>
    request<{ items: ExtDataConfig[] }>('/api/ext-data'),

  extDataRows: (id: string, opts?: { date?: string; limit?: number; columns?: string[] }) => {
    const qs = new URLSearchParams()
    if (opts?.date) qs.set('date', opts.date)
    if (opts?.limit) qs.set('limit', String(opts.limit))
    if (opts?.columns?.length) qs.set('columns', opts.columns.join(','))
    const suffix = qs.toString()
    return request<ExtDataRowsResult>(`/api/ext-data/${encodeURIComponent(id)}/rows${suffix ? `?${suffix}` : ''}`)
  },

  dimensionMembers: (id: string, opts: { field: string; value: string; date?: string; limit?: number }) => {
    const qs = new URLSearchParams({ field: opts.field, value: opts.value })
    if (opts.date) qs.set('date', opts.date)
    if (opts.limit) qs.set('limit', String(opts.limit))
    return request<DimensionMembersResult>(`/api/ext-data/${encodeURIComponent(id)}/dimension-members?${qs.toString()}`)
  },

  analysisMenus: () =>
    request<{ items: AnalysisMenu[] }>('/api/analysis-menus'),

  analysisMenu: (id: string) =>
    request<AnalysisMenu>(`/api/analysis-menus/${encodeURIComponent(id)}`),

  analysisMenuSave: (id: string, body: Omit<AnalysisMenu, 'id' | 'created_at' | 'updated_at' | 'builtin'>) =>
    request<AnalysisMenu>(`/api/analysis-menus/${encodeURIComponent(id)}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  analysisMenuReorder: (ids: string[]) =>
    request<{ items: AnalysisMenu[] }>('/api/analysis-menus/reorder', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),

  analysisMenuDelete: (id: string) =>
    request<{ status: string }>(`/api/analysis-menus/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  extDataCreate: (body: { id: string; label: string; mode: 'snapshot' | 'timeseries'; fields: { name: string; dtype: string; label: string }[]; description?: string; symbol_map?: Record<string, string>; code_map?: Record<string, string> }) =>
    request<ExtDataConfig>('/api/ext-data', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  extDataUpdate: (id: string, body: { label?: string; fields?: { name: string; dtype: string; label: string }[]; description?: string }) =>
    request<ExtDataConfig>(`/api/ext-data/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  extDataDelete: (id: string) =>
    request<{ status: string }>(`/api/ext-data/${id}`, { method: 'DELETE' }),

  extDataUpload: (id: string, file: File, snapshotDate?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ status: string; rows: number; date: string }>(
      `/api/ext-data/${id}/upload${snapshotDate ? `?snapshot_date=${snapshotDate}` : ''}`,
      { method: 'POST', body: fd },
    )
  },

  extDataIngest: (id: string, body: { date?: string; rows: Record<string, unknown>[] }) =>
    request<{ status: string; rows: number; date: string }>(
      `/api/ext-data/${id}/ingest`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  extDataSchemaAll: () =>
    request<{ items: { id: string; label: string; mode: string; columns: { name: string; type: string; label: string }[] }[] }>('/api/ext-data/schema-all'),

  extDataPullConfig: (id: string, body: {
    url: string; method?: string; headers?: Record<string, string>; body?: string;
    response_path?: string; field_map?: Record<string, string>;
    schedule_minutes?: number; enabled?: boolean;
    time_window_start?: string | null; time_window_end?: string | null;
  }) =>
    request<{ status: string; pull: PullConfig }>(
      `/api/ext-data/${id}/pull`,
      { method: 'PUT', body: JSON.stringify(body) },
    ),

  extDataPullTest: (id: string) =>
    request<{ status: string; total_rows: number; preview: Record<string, unknown>[]; has_symbol: boolean }>(
      `/api/ext-data/${id}/pull/test`,
      { method: 'POST' },
    ),

  extDataPullRun: (id: string) =>
    request<{ status: string; rows: number; date: string }>(
      `/api/ext-data/${id}/pull/run`,
      { method: 'POST' },
    ),

  // 内置预设 (概念/行业) 手动获取数据: 走结构转换, 保证 schema 一致
  extDataPresetFetch: (id: string) =>
    request<{ status: string; rows: number }>(
      `/api/ext-data/presets/${id}/fetch`,
      { method: 'POST' },
    ),

  extDataDetectFields: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ fields: { name: string; dtype: string; label: string }[]; rows: number; symbol_candidates: string[]; code_candidates: string[] }>(
      '/api/ext-data/detect-fields',
      { method: 'POST', body: fd },
    )
  },

  extDataDetectUrl: (body: ExtDataDetectUrlRequest) =>
    request<ExtDataDetectUrlResult>('/api/ext-data/detect-url', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  extDataFixSymbol: (id: string) =>
    request<{ status: string; fixed_files: number }>(
      `/api/ext-data/${id}/fix-symbol`,
      { method: 'POST' },
    ),

  // ===== Financials =====
  // Phase 8B-5.3: 仅保留 financialMetrics —— 见上方 FinancialMetricRecord 注释。
  financialMetrics: (symbol?: string) =>
    request<{ data: FinancialMetricRecord[] }>(
      `/api/financials/metrics${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`,
    ),

  // ===== 个股分析 =====
  stockAnalysisLevels: (symbol: string, days = 120) =>
    request<StockLevels>(`/api/stock-analysis/levels?symbol=${encodeURIComponent(symbol)}&days=${days}`),

  stockAnalysisReportsList: () =>
    request<{ reports: AiStockReport[] }>('/api/stock-analysis/reports'),

  stockAnalysisReportSave: (r: {
    symbol: string; name?: string; focus?: string; content: string
    summary?: string; close?: number | null
    levels?: Record<LevelType, PriceLevel[]>
  }) =>
    request<{ ok: boolean; report: AiStockReport }>('/api/stock-analysis/reports', {
      method: 'POST', body: JSON.stringify(r),
    }),

  stockAnalysisReportDelete: (reportId: string) =>
    request<{ ok: boolean }>(`/api/stock-analysis/reports/${encodeURIComponent(reportId)}`, { method: 'DELETE' }),

  /**
   * AI 个股四维分析 — 流式调用(NDJSON,与财务分析同协议)。
   * meta 里额外带 levels(关键价位)供图表回放。
   */
  async *stockAnalyzeStream(symbol: string, focus?: string): AsyncGenerator<{
    type: 'meta' | 'delta' | 'error' | 'done'
    symbol?: string
    summary?: string
    levels?: Record<LevelType, PriceLevel[]>
    close?: number | null
    content?: string
    message?: string
  }> {
    const res = await fetch('/api/stock-analysis/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, focus: focus ?? '' }),
    })
    if (!res.ok) {
      let detail = ''
      try { const j = JSON.parse(await res.text()); detail = j.detail ?? j.message ?? '' } catch { /* ignore */ }
      const msg = detail || `${res.status} ${res.statusText}`
      toast(msg, 'error')
      throw new Error(msg)
    }
    if (!res.body) throw new Error('响应无 body')

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        const s = line.trim()
        if (!s) continue
        try { yield JSON.parse(s) } catch { /* ignore */ }
      }
    }
    if (buf.trim()) {
      try { yield JSON.parse(buf.trim()) } catch { /* ignore */ }
    }
  },

  // ===== 大盘复盘 =====
  reviewReportsList: () =>
    request<{ reports: AiReviewReport[] }>('/api/market-recap/reports'),

  reviewReportSave: (r: {
    as_of: string; focus?: string; content: string
    summary?: string; emotion_score?: number | null; emotion_label?: string
  }) =>
    request<{ ok: boolean; report: AiReviewReport }>('/api/market-recap/reports', {
      method: 'POST', body: JSON.stringify(r),
    }),

  reviewReportDelete: (reportId: string) =>
    request<{ ok: boolean }>(`/api/market-recap/reports/${encodeURIComponent(reportId)}`, { method: 'DELETE' }),

  /**
   * AI 大盘复盘 — 流式调用(NDJSON,与个股/财务分析同协议)。
   * meta 里带 as_of / emotion_score / emotion_label / summary,供前端先渲染信号灯。
   */
  async *reviewStream(asOf?: string, focus?: string): AsyncGenerator<{
    type: 'meta' | 'delta' | 'error' | 'done'
    as_of?: string
    emotion_score?: number
    emotion_label?: string
    summary?: string
    content?: string
    message?: string
  }> {
    const res = await fetch('/api/market-recap/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ as_of: asOf ?? null, focus: focus ?? '' }),
    })
    if (!res.ok) {
      let detail = ''
      try { const j = JSON.parse(await res.text()); detail = j.detail ?? j.message ?? '' } catch { /* ignore */ }
      const msg = detail || `${res.status} ${res.statusText}`
      toast(msg, 'error')
      throw new Error(msg)
    }
    if (!res.body) throw new Error('响应无 body')

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        const s = line.trim()
        if (!s) continue
        try { yield JSON.parse(s) } catch { /* ignore */ }
      }
    }
    if (buf.trim()) {
      try { yield JSON.parse(buf.trim()) } catch { /* ignore */ }
    }
  },

  // ===== Strategy Engine =====
  strategyList: (assetType?: 'stock' | 'etf', timeframe = '1d') => {
    const params = new URLSearchParams()
    if (assetType) params.set('asset_type', assetType)
    if (timeframe) params.set('timeframe', timeframe)
    const qs = params.toString()
    return request<{ strategies: StrategyDetail[]; load_errors?: StrategyLoadError[] }>(
      `/api/strategies${qs ? `?${qs}` : ''}`,
    )
  },

  strategyGet: (id: string) =>
    request<StrategyDetail>(`/api/strategies/${id}`),

  strategyRun: (strategyId: string, params?: Record<string, any>, asOf?: string, pool?: string[]) =>
    request<ScreenerResult>('/api/strategies/run', {
      method: 'POST',
      body: JSON.stringify({ strategy_id: strategyId, params, as_of: asOf ?? null, pool }),
    }),

  strategyRunAll: (asOf?: string) =>
    request<{ as_of: string | null; results: Record<string, { total: number; as_of: string }> }>(
      '/api/strategies/run-all',
      { method: 'POST', body: JSON.stringify({ as_of: asOf ?? null }) },
    ),

  strategySaveConfig: (strategyId: string, overrides: Record<string, any>) =>
    request<{ ok: boolean }>('/api/strategies/config', {
      method: 'POST',
      body: JSON.stringify({ strategy_id: strategyId, overrides }),
    }),

  strategyPatchConfig: (strategyId: string, overrides: Record<string, any>) =>
    request<{ ok: boolean }>('/api/strategies/config', {
      method: 'PATCH',
      body: JSON.stringify({ strategy_id: strategyId, overrides }),
    }),

  strategyResetConfig: (strategyId: string) =>
    request<{ ok: boolean }>(`/api/strategies/config/${strategyId}`, { method: 'DELETE' }),

  /** 删除自定义策略（内置策略不可删除） */
  strategyDelete: (strategyId: string) =>
    request<{ ok: boolean }>(`/api/strategies/${strategyId}`, { method: 'DELETE' }),

  strategyReload: () =>
    request<{ ok: boolean; count: number }>('/api/strategies/reload', { method: 'POST' }),

  // ===== Custom Signals (自定义信号) =====
  customSignalsList: () =>
    request<{ signals: CustomSignal[] }>('/api/custom-signals'),

  customSignalsOptions: () =>
    request<CustomSignalOptions>('/api/custom-signals/options'),

  customSignalSave: (signal: CustomSignal) =>
    request<{ ok: boolean; signal: CustomSignal }>('/api/custom-signals', {
      method: 'POST',
      body: JSON.stringify(signal),
    }),

  customSignalDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/custom-signals/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  customSignalsAiGenerate: (description: string) =>
    request<CustomSignalAIGenerateResult>('/api/custom-signals/ai/generate', {
      method: 'POST',
      body: JSON.stringify({ description }),
    }),

  // ===== Monitor Rules (监控规则) =====
  monitorRulesList: () =>
    request<{ rules: MonitorRule[] }>('/api/monitor-rules'),

  monitorRuleOptions: () =>
    request<MonitorRuleOptions>('/api/monitor-rules/options'),

  monitorRuleSave: (rule: MonitorRule) =>
    request<{ ok: boolean; rule: MonitorRule }>('/api/monitor-rules', {
      method: 'POST',
      body: JSON.stringify(rule),
    }),

  monitorRuleDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/monitor-rules/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // ===== Taiwan Realtime & Monitor APIs (Phase 5C) =====
  taiwanQuotes: (symbols: string[], force = false) =>
    request<{ quotes: TaiwanRealtimeQuote[]; count: number }>(
      `/api/intraday/quotes?symbols=${encodeURIComponent(symbols.join(','))}&force=${force}`,
    ),

  taiwanSearch: (query: string, limit = 20) =>
    request<{ results: TaiwanSearchResult[]; count: number }>(
      `/api/intraday/taiwan/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),

  taiwanStockDetail: (symbol: string, days = 120) =>
    request<TaiwanStockDetailResponse>(
      `/api/taiwan/stocks/${encodeURIComponent(symbol)}?days=${days}`,
    ),

  taiwanCapabilities: () =>
    request<TaiwanDatasetCapability[]>('/api/taiwan/capabilities'),

  taiwanCurrentData: (symbol: string) =>
    request<TaiwanCurrentDataResponse>(`/api/taiwan/data/${encodeURIComponent(symbol)}`),

  taiwanScreenerRun: (payload: TaiwanScreenerRequest) =>
    request<TaiwanScreenerResponse>('/api/taiwan/screener/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  taiwanDataStatus: () =>
    request<TaiwanDataStatus>('/api/taiwan/data-status'),

  taiwanScreenerTranslate: (query: string) =>
    request<TaiwanScreenerTranslation>('/api/taiwan/screener/translate', {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),

  taiwanMarketIntelligence: (date?: string) =>
    request<TaiwanMarketIntelligenceSnapshot>(
      date ? `/api/taiwan/market-intelligence?date=${encodeURIComponent(date)}` : '/api/taiwan/market-intelligence',
    ),

  taiwanIndustryIntelligence: (params?: { date?: string; sort_by?: string; order?: string }) => {
    const q = new URLSearchParams()
    if (params?.date) q.set('date', params.date)
    if (params?.sort_by) q.set('sort_by', params.sort_by)
    if (params?.order) q.set('order', params.order)
    const qs = q.toString()
    return request<TaiwanIndustryIntelligenceSnapshot>(
      qs ? `/api/taiwan/industry-intelligence?${qs}` : '/api/taiwan/industry-intelligence',
    )
  },

  taiwanStockResearchContext: (symbol: string, date?: string) =>
    request<TaiwanStockResearchContext>(
      date
        ? `/api/taiwan/stocks/${encodeURIComponent(symbol)}/research-context?date=${encodeURIComponent(date)}`
        : `/api/taiwan/stocks/${encodeURIComponent(symbol)}/research-context`,
    ),

  taiwanStockAIResearch: (symbol: string, date?: string) =>
    request<TaiwanAIResearchResponse>(
      `/api/taiwan/stocks/${encodeURIComponent(symbol)}/ai-research`,
      {
        method: 'POST',
        body: JSON.stringify({ date: date || null }),
      },
    ),

  taiwanStockCompare: (symbols: string[], date?: string) =>
    request<TaiwanStockComparisonResponse>('/api/taiwan/stocks/compare', {
      method: 'POST',
      body: JSON.stringify({ symbols, date: date || null }),
    }),

  taiwanStockCompareAIResearch: (symbols: string[], date?: string) =>
    request<TaiwanComparisonAIResearchResponse>('/api/taiwan/stocks/compare/ai-research', {
      method: 'POST',
      body: JSON.stringify({ symbols, date: date || null }),
    }),

  taiwanAbnormalDiagnostics: (params?: {
    date?: string
    include_all?: boolean
    signal_type?: string
    industry?: string
    exchange?: string
  }) => {
    const q = new URLSearchParams()
    if (params?.date) q.set('date', params.date)
    if (params?.include_all !== undefined) q.set('include_all', String(params.include_all))
    if (params?.signal_type) q.set('signal_type', params.signal_type)
    if (params?.industry) q.set('industry', params.industry)
    if (params?.exchange) q.set('exchange', params.exchange)
    const qs = q.toString()
    return request<TaiwanAbnormalDiagnosticsSnapshot>(
      qs ? `/api/taiwan/abnormal-diagnostics?${qs}` : '/api/taiwan/abnormal-diagnostics',
    )
  },

  taiwanRulesList: () =>
    request<{ rules: TaiwanMonitorRule[]; total: number }>('/api/monitor-rules/taiwan'),

  taiwanRuleSave: (rule: {
    rule_id?: string
    name: string
    symbol: string
    rule_type: string
    threshold: number
    enabled?: boolean
    cooldown_seconds?: number
    hysteresis?: number | null
    reference_volume?: number | null
    severity?: string
  }) =>
    request<{ ok: boolean; rule: TaiwanMonitorRule }>('/api/monitor-rules/taiwan', {
      method: 'POST',
      body: JSON.stringify(rule),
    }),

  taiwanRuleUpdate: (ruleId: string, updates: Partial<TaiwanMonitorRule>) =>
    request<{ ok: boolean; rule: TaiwanMonitorRule }>(`/api/monitor-rules/taiwan/${encodeURIComponent(ruleId)}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  taiwanRuleDelete: (ruleId: string) =>
    request<{ ok: boolean; deleted: string }>(`/api/monitor-rules/taiwan/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    }),

  taiwanEvaluateRules: () =>
    request<{ ok: boolean; evaluated_rules: number; alerts_count: number; alerts: TaiwanAlertEvent[] }>(
      '/api/monitor-rules/taiwan/evaluate',
      { method: 'POST' },
    ),


  /** 模拟触发 ladder 封单监控 (Dev 调试, 不落盘不推送) */
  monitorRuleTestLadder: () =>
    request<{
      ok: boolean
      as_of: string
      sealed_count: number
      triggered: Array<{
        rule_id: string; rule_name: string; symbol: string; name?: string
        type: string; message: string; severity: string
        sealed_value: number; sealed_metric: string
        current_sealed_vol?: number; current_sealed_amount?: number
      }>
      not_triggered: Array<{
        rule_id: string; rule_name: string; symbol: string
        metric: string; threshold: number; current_value: number | null
        current_sealed_vol?: number; current_sealed_amount?: number | null
        reason: string
      }>
    }>('/api/monitor-rules/test-ladder', { method: 'POST' }),

  /** 真实触发 ladder 预警 (落盘+飞书+SSE), Dev 调试用 */
  monitorRuleTriggerLadder: () =>
    request<{
      ok: boolean
      triggered: number
      events: Array<{ symbol: string; name: string; message: string }>
    }>('/api/monitor-rules/trigger-ladder', { method: 'POST' }),

  /** 生成演示监控规则 (Dev 页用) */
  monitorRuleSeed: () =>
    request<{ ok: boolean; generated: number }>('/api/monitor-rules/seed', { method: 'POST' }),

  // ===== Alerts (触发记录) =====
  alertsList: (params?: { days?: number; limit?: number; source?: string; type?: string; extColumns?: string }) => {
    const qs = new URLSearchParams()
    if (params?.days) qs.set('days', String(params.days))
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.source) qs.set('source', params.source)
    if (params?.type) qs.set('type', params.type)
    if (params?.extColumns) qs.set('ext_columns', params.extColumns)
    const s = qs.toString()
    return request<{ alerts: AlertEvent[]; total: number }>(`/api/alerts${s ? `?${s}` : ''}`)
  },

  alertsClear: () =>
    request<{ ok: boolean; cleared: number }>('/api/alerts', { method: 'DELETE' }),

  alertDelete: (ts: number) =>
    request<{ ok: boolean }>(`/api/alerts/${ts}`, { method: 'DELETE' }),

  /** 生成演示触发记录 (Dev 页用) */
  alertSeed: (count = 12, recent = true) =>
    request<{ ok: boolean; generated: number }>(`/api/alerts/seed?count=${count}&recent=${recent}`, { method: 'POST' }),

  /** 检查 AI 配置状态 */
  strategyAiStatus: () =>
    request<{ configured: boolean; has_key: boolean; has_model: boolean; provider?: string }>('/api/strategies/ai/status'),

  /** 测试 AI 连通性 */
  strategyAiTest: () =>
    request<{ ok: boolean; error?: string; model?: string; response?: string; usage?: { prompt: number; completion: number } }>(
      '/api/strategies/ai/test',
      { method: 'POST' },
    ),

  /** 获取策略源文件内容 */
  strategyGetSource: (id: string) =>
    request<{ code: string; source: string }>(`/api/strategies/${id}/source`),
  strategyBuild: (step: number, payload: Record<string, any>) =>
    request<StrategyBuildResult>(
      '/api/strategies/build',
      { method: 'POST', body: JSON.stringify({ step, ...payload }) },
    ),

  async *strategyBuildStream(step: number, payload: Record<string, any>): AsyncGenerator<StrategyBuildStreamEvent> {
    const res = await fetch('/api/strategies/build/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ step, ...payload }),
    })
    if (!res.ok) {
      let detail = ''
      try { const j = JSON.parse(await res.text()); detail = j.detail ?? j.message ?? '' } catch { /* ignore */ }
      const msg = detail || `${res.status} ${res.statusText}`
      toast(msg, 'error')
      throw new Error(msg)
    }
    if (!res.body) throw new Error('响应无 body')

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        const s = line.trim()
        if (!s) continue
        try { yield JSON.parse(s) } catch { /* ignore */ }
      }
    }
    if (buf.trim()) {
      try { yield JSON.parse(buf.trim()) } catch { /* ignore */ }
    }
  },

  strategyValidateCode: (payload: { code: string; strategy_id?: string; name?: string; description?: string }) =>
    request<StrategyBuildResult>('/api/strategies/code/validate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  strategySaveCodeV2: (payload: {
    strategy_id: string
    code: string
    target_source: 'ai' | 'custom'
    mode: 'create' | 'update'
    name?: string
    description?: string
  }) =>
    request<StrategyCodeSaveResult>('/api/strategies/code/save', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** 创建/更新叠加策略(composite): 声明式引用多个子策略 */
  strategySaveComposite: (payload: {
    strategy_id: string
    name: string
    description?: string
    children: { strategy_id: string; weight: number }[]
    merge_mode: 'union' | 'intersect'
    min_confirm?: number
    mode: 'create' | 'update'
  }) =>
    request<StrategyCodeSaveResult>('/api/strategies/composite/save', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** 保存 AI 生成的策略文件 */
  strategySaveCode: (strategyId: string, code: string, meta?: { name?: string; description?: string }) =>
    request<{ ok: boolean; path: string }>('/api/strategies/ai/save', {
      method: 'POST',
      body: JSON.stringify({ strategy_id: strategyId, code, name: meta?.name ?? '', description: meta?.description ?? '' }),
    }),
}

// ===== Pipeline =====
export interface PipelineJob {
  id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  stage: string
  progress: number          // 0-100 整体进度
  stage_pct: number         // 0-100 当前阶段内进度
  log: { ts: string; stage: string; msg: string }[]
  started_at: string | null
  finished_at: string | null
  duration_s: number | null
  result: {
    universe_size: number
    daily_days: number
    adj_factor_symbols: number
    enriched_days: number
    index_count?: number
    index_daily_rows?: number
    minute_rows: number
    skipped_stages?: string[]
  } | null
  error: string | null
}

export type PipelineJobSummary = Omit<PipelineJob, 'log'>

// ===== Data status =====
interface TableStats {
  rows: number
  earliest_date: string | null
  latest_date: string | null
  symbols_covered: number
  trading_days: number
}

interface InstrumentsStats {
  rows: number
  symbols_covered: number
  latest_as_of: string | null
  named: number
}

export interface DataStatus {
  daily: TableStats | null
  enriched: TableStats | null
  index_daily: TableStats | null
  index_enriched: TableStats | null
  index_instruments: InstrumentsStats | null
  etf_daily: TableStats | null
  etf_enriched: TableStats | null
  etf_instruments: InstrumentsStats | null
  minute: TableStats | null
  adj_factor: TableStats | null
  instruments: InstrumentsStats | null
  financials: { rows: number; tables: Record<string, { rows: number; symbols: number }> } | null
  storage: {
    daily_files: number
    daily_size_mb: number
    enriched_files: number
    enriched_size_mb: number
    index_daily_files?: number
    index_daily_size_mb?: number
    index_enriched_files?: number
    index_enriched_size_mb?: number
    index_instruments_files?: number
    index_instruments_size_mb?: number
    etf_daily_files?: number
    etf_daily_size_mb?: number
    etf_enriched_files?: number
    etf_enriched_size_mb?: number
    etf_instruments_files?: number
    etf_instruments_size_mb?: number
    etf_adj_factor_files?: number
    etf_adj_factor_size_mb?: number
    minute_files: number
    minute_size_mb: number
    adj_factor_files: number
    adj_factor_size_mb: number
    instruments_files: number
    instruments_size_mb: number
    financials_files?: number
    financials_size_mb?: number
    ext_data_files?: number
    ext_data_size_mb?: number
    total_size_mb: number
  }
  next_pipeline_run: string | null
  next_instruments_run: string | null
  last_pipeline_run: string | null
  last_instruments_run: string | null
  checked_at: string
  indicators_ready?: boolean
}

export interface EnrichedField {
  name: string
  type: string
  desc: string
}

// ===== 扩展数据 =====
export interface ExtDataField {
  name: string
  dtype: string
  label: string
}

export interface PullConfig {
  url: string
  method: string
  headers?: Record<string, string>
  body?: string | null
  response_path: string
  field_map?: Record<string, string>
  schedule_minutes: number
  enabled: boolean
  last_run?: string | null
  last_status?: string | null
  last_message?: string | null
  last_rows?: number | null
  next_run?: string | null
  time_window_start?: string | null
  time_window_end?: string | null
}

export interface ExtDataDetectUrlRequest {
  url: string
  method?: string
  headers?: Record<string, string>
  body?: string
  response_path?: string
  field_map?: Record<string, string>
}

export interface ExtDataDetectUrlResult {
  status: string
  total_rows: number
  response_path: string
  response_path_candidates: string[]
  fields: ExtDataField[]
  symbol_candidates: string[]
  code_candidates: string[]
  preview: Record<string, unknown>[]
}

export interface ExtDataConfig {
  id: string
  label: string
  mode: 'snapshot' | 'timeseries'
  fields: ExtDataField[]
  description?: string
  symbol_map?: Record<string, string>
  code_map?: Record<string, string>
  created_at: string
  updated_at: string
  latest_sync_date?: string | null
  date_range?: string[] | null
  pull?: PullConfig | null
}

export interface ExtDataRowsResult {
  id: string
  label: string
  mode: 'snapshot' | 'timeseries'
  date: string | null
  total: number
  limit: number
  fields: ExtDataField[]
  rows: Record<string, any>[]
}

export interface DimensionMembersResult {
  id: string
  label: string
  date: string | null
  field: string
  value: string
  total: number
  limit: number
  rows: Record<string, any>[]
}

export interface AnalysisColumn {
  field: string
  label?: string
  type?: 'string' | 'number' | 'percent' | 'amount' | 'date'
  width?: number | null
  sortable?: boolean
  precision?: number | null
  format?: string | null
  aggregate?: 'count' | 'avg' | 'sum' | 'min' | 'max' | null
  visible?: boolean
}

export interface AnalysisMenu {
  id: string
  label: string
  icon: string
  data_source: string
  template: 'dimension_rank' | 'ranking' | 'table'
  dimension_field?: string | null
  rank_field?: string | null
  group_columns: AnalysisColumn[]
  detail_columns: AnalysisColumn[]
  default_sort?: { field: string; order: 'asc' | 'desc' } | null
  visible: boolean
  order: number
  created_at?: string | null
  updated_at?: string | null
  builtin?: boolean
}
