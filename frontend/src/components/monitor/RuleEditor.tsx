import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Building2, ChartNoAxesCombined, Check, ChevronDown, ChevronUp, Eraser, Layers3, ListPlus, Plus, RadioTower, Save, Search, Tags, TrendingUp, Waypoints, X } from 'lucide-react'
import { api, genRuleId, type MonitorRule, type MonitorCondition, type SectorKind, type SectorMonitorTarget, type StrategyNotifyEvent } from '@/lib/api'
import { DEFAULT_STRATEGY_NOTIFY_EVENTS, LEGACY_STRATEGY_NOTIFY_EVENTS, STRATEGY_NOTIFY_EVENT_OPTIONS } from '@/lib/strategyMonitorEvents'
import { QK } from '@/lib/queryKeys'
import { boardTag } from '@/components/stock-table/primitives'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'
import { SignalPicker } from '@/components/screener/SignalPicker'
import { MONITOR_INTRADAY_SIGNAL_OPTIONS, SIGNAL_OPTIONS, cnSignal } from '@/lib/signals'
import { usePreferences } from '@/lib/useSharedQueries'

interface Props {
  /** 編輯現有規則;null=新建 */
  rule: MonitorRule | null
  /** 新建時的預填值 (如個股彈窗傳入 symbol/scope) */
  preset?: Partial<MonitorRule>
  /** 極簡模式: 個股場景, 隱藏 type/scope/閾值等, 只顯示信號點選 */
  simple?: boolean
  onClose: () => void
  onSaved?: () => void
}

const TYPE_DEFAULT_NAME: Record<string, string> = {
  signal: '訊號監控', price: '價格監控', market: '市場異動監控', strategy: '策略監控', sector: '板塊監控',
}

const TYPE_ICONS = {
  signal: Activity,
  price: TrendingUp,
  market: RadioTower,
  strategy: Waypoints,
  sector: Layers3,
}

const SECTOR_KIND_OPTIONS: Array<{ key: SectorKind; label: string; icon: typeof ChartNoAxesCombined }> = [
  { key: 'index', label: '大盤指數', icon: ChartNoAxesCombined },
  { key: 'concept', label: '概念題材', icon: Tags },
  { key: 'industry', label: '產業板塊', icon: Building2 },
]

const STRATEGY_SOURCE_META = {
  builtin: { label: '內建', className: 'border-accent/25 bg-accent/10 text-accent' },
  custom: { label: '自訂', className: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-400' },
  ai: { label: 'AI', className: 'border-amber-400/25 bg-amber-400/10 text-amber-400' },
  composite: { label: '疊加', className: 'border-teal-500/25 bg-teal-500/10 text-teal-400' },
} as const

const emptyRule = (preset?: Partial<MonitorRule>): MonitorRule => ({
  id: genRuleId(),
  name: '',
  enabled: true,
  type: 'signal',
  asset_type: 'stock',
  scope: 'symbols',
  symbols: [],
  group_id: null,
  sector: null,
  sector_kind: 'index',
  sector_targets: [],
  sector_trigger: 'change_pct',
  threshold_pct: 1,
  window_minutes: 5,
  strategy_id: null,
  score_min: null,
  score_max: null,
  direction: 'entry',
  conditions: [],
  logic: 'or',
  cooldown_seconds: 3600,
  severity: 'info',
  message: '',
  ...preset,
})

export function RuleEditor({ rule, preset, simple, onClose, onSaved }: Props) {
  const qc = useQueryClient()
  const options = useQuery({ queryKey: QK.monitorRuleOptions, queryFn: api.monitorRuleOptions })
  const { data: prefs } = usePreferences()
  const lineConfigured = !!prefs?.line_configured
  const telegramConfigured = !!prefs?.telegram_configured
  const [editing] = useState(!!rule)
  // 新建規則: 預填全局「默認推送渠道」(多選數組), preset 顯式指定時以 preset 為準。
  // 編輯規則: 完全沿用規則自身配置, 不受默認值影響。
  const [draft, setDraft] = useState<MonitorRule>(() => {
    if (rule) {
      return {
        ...rule,
        notify_events: rule.type === 'strategy'
          ? [...(rule.notify_events ?? LEGACY_STRATEGY_NOTIFY_EVENTS)]
          : undefined,
        conditions: rule.conditions.map(c => ({ ...c })),
        sector_targets: rule.sector_targets?.map(target => ({ ...target })) ?? [],
      }
    }
    const initial = {
      ...emptyRule(preset),
      webhook_channels: preset?.webhook_channels ?? (prefs?.webhook_default_channels ?? []),
    }
    if (initial.type === 'strategy' && !initial.notify_events) {
      initial.notify_events = [...DEFAULT_STRATEGY_NOTIFY_EVENTS]
    }
    return initial
  })
  const assetType = draft.asset_type ?? 'stock'
  // 策略列表跟隨資產類型: ETF 只列技術類策略。
  const strategies = useQuery({
    queryKey: QK.screenerStrategies(assetType),
    queryFn: () => api.screenerStrategies(assetType),
  })
  const [error, setError] = useState('')
  const [symbolQuery, setSymbolQuery] = useState('')
  const isGroupScope = draft.scope === 'watchlist_group'
  // 「自選導入」下拉: 從自選/自選分組批量併入標的 (與自選頁共用查詢緩存)。
  // 分組作用域模式同樣需要分組/成員數據 (選擇分組 + 成員預覽)。
  const [watchMenuOpen, setWatchMenuOpen] = useState(false)
  const watchMenuRef = useRef<HTMLDivElement>(null)
  const watchlistQ = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    enabled: watchMenuOpen || isGroupScope,
  })
  const watchGroupsQ = useQuery({
    queryKey: QK.watchlistGroups,
    queryFn: api.watchlistGroups,
    enabled: watchMenuOpen || isGroupScope,
  })
  // 分組選擇下拉 (scope=watchlist_group)
  const [groupMenuOpen, setGroupMenuOpen] = useState(false)
  const groupMenuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!groupMenuOpen) return
    const handleClick = (e: MouseEvent) => {
      if (groupMenuRef.current && !groupMenuRef.current.contains(e.target as Node)) {
        setGroupMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [groupMenuOpen])
  useEffect(() => {
    if (!watchMenuOpen) return
    const handleClick = (e: MouseEvent) => {
      if (watchMenuRef.current && !watchMenuRef.current.contains(e.target as Node)) {
        setWatchMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [watchMenuOpen])
  const [sectorQuery, setSectorQuery] = useState('')
  const [industryLevel, setIndustryLevel] = useState<1 | 2 | 3>(() => {
    const level = rule?.sector_targets?.[0]?.level
    return level === 1 || level === 3 ? level : 2
  })
  const [strategyQuery, setStrategyQuery] = useState('')
  const [strategyCategory, setStrategyCategory] = useState<'all' | 'builtin' | 'custom' | 'ai' | 'composite'>('all')
  // 標的搜索資產類型: ETF 一併搜股票; 指數只搜指數; 否則只搜股票。
  const symbolAssetTypes = assetType === 'etf' ? 'stock,etf' : assetType === 'index' ? 'index' : 'stock'
  const symbolSearch = useQuery({
    queryKey: QK.instrumentSearch(symbolQuery, symbolAssetTypes),
    queryFn: () => api.instrumentSearch(symbolQuery, 20, symbolAssetTypes),
    enabled: symbolQuery.length > 0,
  })

  const save = useMutation({
    mutationFn: () => {
      const d = { ...draft }
      delete d.runtime_warning
      // name 為空時用默認名
      if (!d.name.trim()) {
        const base = TYPE_DEFAULT_NAME[d.type] ?? '監控規則'
        d.name = d.type === 'sector' && d.sector_targets?.length
          ? `${base} · ${d.sector_targets[0].name}${d.sector_targets.length > 1 ? ` 等${d.sector_targets.length}個` : ''}`
          : d.scope === 'watchlist_group' && selectedGroup
          ? `${base} · 分組「${selectedGroup.name}」`
          : d.scope === 'symbols' && d.symbols.length > 0
          ? `${base} · ${d.symbols[0]}${d.symbols.length > 1 ? ` 等${d.symbols.length}檔` : ''}`
          : base
      }
      if (d.type === 'strategy') {
        if (!d.strategy_id) throw new Error('策略監控必須選擇一個策略')
        if (!d.notify_events?.length) throw new Error('至少選擇一個通知事件')
        for (const [label, value] of [['最低分', d.score_min], ['最高分', d.score_max]] as const) {
          if (value != null && (!Number.isFinite(value) || value < 0 || value > 100)) {
            throw new Error(`${label}必須在 0 到 100 之間`)
          }
        }
        if (d.score_min != null && d.score_max != null && d.score_min > d.score_max) {
          throw new Error('最低分不能高於最高分')
        }
      } else if (d.type === 'sector') {
        delete d.score_min
        delete d.score_max
        d.scope = 'all'
        d.symbols = []
        d.conditions = []
        delete d.notify_events
        if (!d.sector_targets?.length) throw new Error('請選擇至少一個監控對象')
        if ((d.threshold_pct ?? 0) <= 0 || (d.threshold_pct ?? 0) > 20) throw new Error('閾值必須大於 0 且不超過 20%')
      } else {
        delete d.score_min
        delete d.score_max
        delete d.notify_events
        if (d.conditions.length === 0) throw new Error('至少選擇一個觸發條件')
        for (const c of d.conditions) {
          if (!c.field || !c.op) throw new Error('條件填寫不完整')
          if (c.op !== 'truth' && (c.value === null || c.value === undefined)) throw new Error('閾值條件需要數值')
        }
      }
      if (d.type !== 'sector' && d.scope === 'symbols' && d.symbols.length === 0) throw new Error('請選擇至少一檔標的')
      if (d.type !== 'sector' && d.scope === 'watchlist_group' && !d.group_id) throw new Error('請選擇一個自選分組')
      return api.monitorRuleSave(d)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.monitorRules })
      onSaved?.()
      onClose()
    },
    onError: err => setError(String((err as any)?.message ?? err)),
  })

  // 條件編輯
  const updateCond = (idx: number, patch: Partial<MonitorCondition>) =>
    setDraft(d => ({ ...d, conditions: d.conditions.map((c, i) => i === idx ? { ...c, ...patch } : c) }))
  const addCond = (op: 'truth' | 'threshold') =>
    setDraft(d => ({
      ...d,
      conditions: [...d.conditions, op === 'truth'
        ? { field: 'signal_volume_surge', op: 'truth' }
        // simple 模式(個股彈窗)默認現價; 完整模式默認 RSI 超賣
        : { field: simple ? 'close' : 'rsi_14', op: '<', value: simple ? 0 : 30 }],
    }))
  const removeCond = (idx: number) =>
    setDraft(d => ({ ...d, conditions: d.conditions.filter((_, i) => i !== idx) }))

  const addSymbol = (sym: string) => {
    if (!draft.symbols.includes(sym)) {
      setDraft(d => ({ ...d, symbols: [...d.symbols, sym] }))
    }
    setSymbolQuery('')
  }

  // 併入一組標的 (去重); 選擇後關閉自選導入下拉
  const importSymbols = (syms: string[]) => {
    setDraft(d => {
      const merged = [...d.symbols]
      for (const s of syms) {
        if (!merged.includes(s)) merged.push(s)
      }
      return { ...d, symbols: merged }
    })
    setWatchMenuOpen(false)
  }

  // ── 標的標籤: 名稱 + 板標(創/科/北) + 代碼, 可逐個刪除 ──
  const [symbolsExpanded, setSymbolsExpanded] = useState(false)
  const symbolsKey = draft.symbols.join(',')
  // 名稱映射: 本地即時緩存(搜索/自選數據) 優先, 缺失的由批量名稱接口補齊
  // (覆蓋編輯舊規則等本地無名稱的場景)。key 隨標的集變化, staleTime 長防抖。
  const localNamesRef = useRef<Record<string, string>>({})
  const recordLocalNames = (pairs: { symbol: string; name?: string | null }[]) => {
    for (const p of pairs) {
      if (p.name) localNamesRef.current[p.symbol] = p.name
    }
  }
  if (watchlistQ.data?.symbols) recordLocalNames(watchlistQ.data.symbols)
  if (symbolSearch.data?.results) recordLocalNames(symbolSearch.data.results)
  const namesQ = useQuery({
    queryKey: ['instrument-names', symbolsKey],
    queryFn: () => api.instrumentNames(draft.symbols),
    enabled: draft.symbols.length > 0,
    staleTime: 5 * 60_000,
  })
  const nameBySymbol = useMemo(
    () => ({ ...localNamesRef.current, ...(namesQ.data?.names ?? {}) }),
    [symbolsKey, namesQ.data],
  )
  // 自選導入選項: 全部自選 + 各分組 (空分組隱藏) + 未分組
  const watchImportOptions = (() => {
    const entries = watchlistQ.data?.symbols ?? []
    if (entries.length === 0) return []
    const options = [{
      key: 'all',
      name: '全部自選',
      dot: 'bg-muted/60',
      symbols: entries.map(e => e.symbol),
    }]
    for (const group of watchGroupsQ.data?.groups ?? []) {
      const syms = entries.filter(e => e.group_ids?.includes(group.id)).map(e => e.symbol)
      if (syms.length === 0) continue
      options.push({ key: group.id, name: group.name, dot: resolveWatchlistGroupColor(group.color).dot, symbols: syms })
    }
    const ungrouped = entries.filter(e => !(e.group_ids?.length)).map(e => e.symbol)
    if (ungrouped.length > 0) {
      options.push({ key: 'ungrouped', name: '未分組', dot: 'bg-muted/60', symbols: ungrouped })
    }
    return options
  })()

  // ── 自選分組作用域 (scope=watchlist_group): 分組選擇 + 只讀成員預覽 ──
  const groupList = watchGroupsQ.data?.groups ?? []
  const watchEntries = watchlistQ.data?.symbols ?? []
  const groupCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const entry of watchEntries) {
      for (const gid of entry.group_ids ?? []) counts[gid] = (counts[gid] ?? 0) + 1
    }
    return counts
  }, [watchEntries])
  const selectedGroup = groupList.find(g => g.id === draft.group_id)
  const selectedGroupSymbols = useMemo(
    () => selectedGroup
      ? watchEntries.filter(e => e.group_ids?.includes(selectedGroup.id)).map(e => e.symbol)
      : [],
    [selectedGroup, watchEntries],
  )
  // 預覽區名稱補齊 (分組成員通常不在 draft.symbols 裡, 單獨批量查詢)
  const groupNamesQ = useQuery({
    queryKey: ['instrument-names', selectedGroupSymbols.join(',')],
    queryFn: () => api.instrumentNames(selectedGroupSymbols),
    enabled: isGroupScope && selectedGroupSymbols.length > 0,
    staleTime: 5 * 60_000,
  })
  const groupNameBySymbol = groupNamesQ.data?.names ?? {}

  const selectSectorKind = (kind: SectorKind) => {
    setDraft(d => ({ ...d, sector_kind: kind, sector_targets: [] }))
    setSectorQuery('')
  }

  const toggleSectorTarget = (target: SectorMonitorTarget) => {
    setDraft(d => {
      const current = d.sector_targets ?? []
      if (current.some(item => item.key === target.key)) {
        return { ...d, sector_targets: current.filter(item => item.key !== target.key) }
      }
      if (current.length >= 20) return d
      return { ...d, sector_targets: [...current, target] }
    })
  }

  // 勾選/取消勾選某個推播渠道 (LINE / Telegram 各自獨立)
  const toggleChannel = (ch: string) =>
    setDraft(d => {
      const cur = d.webhook_channels ?? []
      return { ...d, webhook_channels: cur.includes(ch) ? cur.filter(c => c !== ch) : [...cur, ch] }
    })

  const toggleStrategyEvent = (event: StrategyNotifyEvent) =>
    setDraft(d => {
      const current = d.notify_events ?? LEGACY_STRATEGY_NOTIFY_EVENTS
      return {
        ...d,
        notify_events: current.includes(event)
          ? current.filter(item => item !== event)
          : [...current, event],
      }
    })

  const thresholdFields = options.data?.threshold_fields ?? []
  const operators = options.data?.operators ?? ['>', '>=', '<', '<=', '==', '!=']
  const selectedSignals = draft.conditions.filter(c => c.op === 'truth').map(c => c.field)
  const hasIntradaySignal = selectedSignals.some(signal => MONITOR_INTRADAY_SIGNAL_OPTIONS.includes(signal))
  const intradaySupport = options.data?.intraday_signal_support
  const monitorBuiltinSignals = [
    ...SIGNAL_OPTIONS.map(key => ({ key, label: cnSignal(key) })),
    ...(options.data?.builtin_signals ?? []).filter(option => MONITOR_INTRADAY_SIGNAL_OPTIONS.includes(option.key)),
  ]
  // 指數: 隱藏漲跌停/連板類 (指數無這些列) 與分時信號 (無本地分鐘K, 會靜默不觸發)
  const INDEX_HIDDEN_SIGNALS = (key: string) =>
    key.includes('limit') || MONITOR_INTRADAY_SIGNAL_OPTIONS.includes(key)
  const pickerSignals = assetType === 'index'
    ? monitorBuiltinSignals.filter(o => !INDEX_HIDDEN_SIGNALS(o.key))
    : monitorBuiltinSignals
  // 分時穿越信號: 數據按標的清單訂閱且有上限, 自選分組是動態集合 (靜默超限風險) → 禁用
  const intradayDisabledSignals =
    intradaySupport?.available === false || isGroupScope ? MONITOR_INTRADAY_SIGNAL_OPTIONS : []
  const intradayDisabledHint = isGroupScope
    ? '分時穿越訊號需逐股訂閱, 暫不支援自選分組作用域'
    : intradaySupport?.reason
  // 指數: 監控類型僅 signal/price (無漲跌停/策略/封單語義)
  const visibleTypes = (options.data?.types ?? []).filter(
    t => assetType !== 'index' || t.key === 'signal' || t.key === 'price',
  )
  // 指數: 作用範圍僅 symbols (無全市場/板塊語義); ETF: 不支持自選分組 (分組為個股)
  const visibleScopes = (options.data?.scopes ?? []).filter(
    s => (assetType !== 'index' || s.key === 'symbols')
      && (assetType === 'stock' || s.key !== 'watchlist_group'),
  )
  const sectorKind = draft.sector_kind ?? 'index'
  const sectorTargets = options.data?.sector_targets?.[sectorKind] ?? []
  const visibleSectorTargets = sectorTargets.filter(target => {
    if (sectorKind === 'industry' && target.level !== industryLevel) return false
    const query = sectorQuery.trim().toLowerCase()
    if (!query) return true
    return `${target.name} ${target.symbol ?? ''} ${target.value ?? ''}`.toLowerCase().includes(query)
  }).slice(0, 100)
  const thresholdConds = draft.conditions.filter(c => c.op !== 'truth')
  const strategyPresets = strategies.data?.presets ?? []
  const normalizedStrategyQuery = strategyQuery.trim().toLowerCase()
  const visibleStrategies = strategyPresets.filter(strategy => {
    if (strategyCategory !== 'all' && strategy.source !== strategyCategory) return false
    if (!normalizedStrategyQuery) return true
    return [strategy.name, strategy.id, strategy.description, ...(strategy.tags ?? [])]
      .some(value => String(value ?? '').toLowerCase().includes(normalizedStrategyQuery))
  })
  const strategyCategories = [
    { key: 'all' as const, label: '全部', count: strategyPresets.length },
    { key: 'builtin' as const, label: '內建', count: strategyPresets.filter(strategy => strategy.source === 'builtin').length },
    { key: 'custom' as const, label: '自訂', count: strategyPresets.filter(strategy => strategy.source === 'custom').length },
    { key: 'ai' as const, label: 'AI', count: strategyPresets.filter(strategy => strategy.source === 'ai').length },
    { key: 'composite' as const, label: '疊加', count: strategyPresets.filter(strategy => strategy.source === 'composite').length },
  ]

  const onSignalPickerChange = (next: string[]) => {
    setDraft(d => {
      const nonTruthConds = d.conditions.filter(c => c.op !== 'truth')
      const truthConds: MonitorCondition[] = next.map(field => ({ field, op: 'truth' }))
      return {
        ...d,
        scope: next.some(signal => MONITOR_INTRADAY_SIGNAL_OPTIONS.includes(signal)) ? 'symbols' : d.scope,
        conditions: [...nonTruthConds, ...truthConds],
      }
    })
  }

  // ── 極簡模式: 只顯示信號點選 + 可選描述 ──
  if (simple) {
    return (
      <div className="rounded-card border border-border bg-surface p-5 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-foreground">{editing ? '編輯監控' : '加入監控'}</h3>
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-elevated hover:text-foreground cursor-pointer">
            <X className="h-4 w-4" />
          </button>
        </div>

        {draft.symbols.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {draft.symbols.map(s => (
              <span key={s} className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary font-mono">{s}</span>
            ))}
          </div>
        )}

        <div>
          <div className="mb-1.5 text-[11px] text-muted">選擇觸發訊號 (任一命中即報警)</div>
          <SignalPicker
            signals={selectedSignals}
            onChange={onSignalPickerChange}
            kind="entry"
            builtinSignals={pickerSignals}
            disabledSignals={intradaySupport?.available === false ? MONITOR_INTRADAY_SIGNAL_OPTIONS : []}
            disabledSignalHint={intradaySupport?.reason}
          />
          {hasIntradaySignal && (
            <div className={`mt-2 text-[10px] ${intradaySupport?.available === false ? 'text-danger' : 'text-muted'}`}>
              {intradaySupport?.available === false
                ? intradaySupport.reason
                : `按已完成的一分鐘判斷,當前最多監聽 ${intradaySupport?.max_symbols ?? 0} 檔標的。`}
            </div>
          )}
        </div>

        {/* 價位條件 (閾值) — 與信號共存, 可選添加 */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted">價位條件 (可選)</span>
            <button onClick={() => addCond('threshold')} className="inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent/80 cursor-pointer">
              <Plus className="h-3 w-3" />新增價位
            </button>
          </div>
          {thresholdConds.length > 0 && (
            <div className="space-y-1.5">
              {thresholdConds.map((c, i) => {
                const realIdx = draft.conditions.indexOf(c)
                return (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className="text-[10px] text-muted/60 w-6 text-right shrink-0">{i === 0 && selectedSignals.length === 0 ? '當' : draft.logic === 'and' ? '且' : '或'}</span>
                    <select value={c.field} onChange={e => updateCond(realIdx, { field: e.target.value })} className="flex-1 h-7 px-1.5 rounded bg-base border border-border text-[11px] text-foreground focus:outline-none focus:border-accent/50">
                      {thresholdFields.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                    </select>
                    <select value={c.op} onChange={e => updateCond(realIdx, { op: e.target.value })} className="w-12 h-7 px-1 rounded bg-base border border-border text-[11px] font-mono text-foreground text-center focus:outline-none focus:border-accent/50">
                      {operators.map(op => <option key={op} value={op}>{op}</option>)}
                    </select>
                    <input type="number" value={c.value ?? 0} onChange={e => updateCond(realIdx, { value: parseFloat(e.target.value) })} step="any" className="w-24 h-7 px-1.5 rounded bg-base border border-border text-[11px] font-mono text-foreground text-center focus:outline-none focus:border-accent/50" />
                    <button onClick={() => removeCond(realIdx)} className="p-1 rounded text-muted hover:text-danger hover:bg-danger/10 cursor-pointer">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <label className="space-y-1.5">
          <span className="text-[11px] text-muted">備註 (可選)</span>
          <input value={draft.message} onChange={e => setDraft(d => ({ ...d, message: e.target.value }))} placeholder="給這條監控加個備註" className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground" />
        </label>

        {error && <div className="rounded-btn border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger">{error}</div>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs cursor-pointer">取消</button>
          <button onClick={() => save.mutate()} disabled={save.isPending} className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer">
            <Save className="h-3.5 w-3.5" />加入監控
          </button>
        </div>
      </div>
    )
  }

  // ── 完整模式: 監控頁新建/編輯 ──
  return (
    <div className="rounded-card border border-border bg-surface p-5 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-foreground">{editing ? '編輯監控規則' : '新建監控規則'}</h3>
          <p className="mt-1 text-[11px] text-muted">規則標識自動生成,描述為可選。</p>
        </div>
        <button onClick={onClose} className="rounded p-1 text-muted hover:bg-elevated hover:text-foreground cursor-pointer">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* 資產類型: 股票 / ETF / 指數 (個股極簡模式不顯示; 板塊僅個股) */}
      {!simple && draft.type !== 'sector' && (
        <div className="space-y-1.5">
          <span className="text-[11px] text-muted">資產類型</span>
          <div className="inline-flex h-9 rounded-btn border border-border overflow-hidden">
            {(['stock', 'etf', 'index'] as const).map(t => (
              <button
                key={t}
                type="button"
                aria-pressed={assetType === t}
                onClick={() => {
                  if (assetType === t) return
                  setDraft(d => ({
                    ...d,
                    asset_type: t,
                    strategy_id: null,
                    symbols: [],
                    type: t === 'index' && d.type !== 'signal' && d.type !== 'price' ? 'signal' : d.type,
                    // 指數僅指定標的; ETF 不支持分組作用域 (自選分組為個股)
                    scope: t === 'index' || (t !== 'stock' && d.scope === 'watchlist_group')
                      ? 'symbols'
                      : d.scope,
                  }))
                  setStrategyQuery('')
                  setStrategyCategory('all')
                }}
                className={`h-full px-4 text-xs font-medium transition-colors cursor-pointer
                  ${assetType === t ? 'bg-accent/10 text-accent' : 'text-muted hover:text-foreground'}`}
              >
                {t === 'stock' ? '股票' : t === 'etf' ? 'ETF' : '指數'}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 監控類型 */}
      <div className="space-y-1.5">
        <span className="text-[11px] text-muted">監控類型</span>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-6">
          {visibleTypes.map(t => {
            const Icon = TYPE_ICONS[t.key as keyof typeof TYPE_ICONS] ?? Activity
            const active = draft.type === t.key
            return (
              <button
                key={t.key}
                type="button"
                aria-pressed={active}
                onClick={() => setDraft(d => {
                  const type = t.key as MonitorRule['type']
                  return {
                    ...d,
                    type,
                    notify_events: type === 'strategy'
                      ? [...(d.notify_events ?? DEFAULT_STRATEGY_NOTIFY_EVENTS)]
                      : undefined,
                    scope: type === 'sector'
                      ? 'all'
                      : type === 'strategy' && d.scope === 'symbols' && d.symbols.length === 0 ? 'all' : d.scope,
                    direction: type === 'sector' ? 'up'
                      : d.type === 'sector' ? 'entry' : d.direction,
                  }
                })}
                className={`inline-flex h-9 items-center justify-center gap-1.5 rounded-btn border px-2 text-xs font-medium transition-colors cursor-pointer ${
                  active
                    ? 'border-accent/40 bg-accent/12 text-accent'
                    : 'border-border bg-base text-secondary hover:border-accent/25 hover:text-foreground'
                }`}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <span>{t.label}</span>
              </button>
            )
          })}
        </div>
      </div>

      <label className="space-y-1.5">
        <span className="text-[11px] text-muted">描述 (可選)</span>
        <input value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} placeholder="留空用預設名稱" className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground" />
      </label>

      {draft.type === 'sector' && (
        <div className="space-y-4 border-t border-border/60 pt-4">
          <div className="space-y-1.5">
            <span className="text-[11px] text-muted">板塊分類</span>
            <div className="grid grid-cols-3 gap-1.5">
              {SECTOR_KIND_OPTIONS.map(option => {
                const Icon = option.icon
                const active = sectorKind === option.key
                return (
                  <button
                    key={option.key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => selectSectorKind(option.key)}
                    className={`inline-flex h-9 items-center justify-center gap-1.5 rounded-btn border text-xs font-medium transition-colors cursor-pointer ${
                      active ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border bg-base text-secondary hover:border-accent/25'
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {option.label}
                  </button>
                )
              })}
            </div>
          </div>

          {sectorKind === 'industry' && (
            <div className="space-y-1.5">
              <span className="text-[11px] text-muted">產業層級</span>
              <div className="inline-flex h-8 overflow-hidden rounded-btn border border-border bg-base">
                {([1, 2, 3] as const).map(level => (
                  <button
                    key={level}
                    type="button"
                    aria-pressed={industryLevel === level}
                    onClick={() => {
                      setIndustryLevel(level)
                      setDraft(d => ({ ...d, sector_targets: [] }))
                    }}
                    className={`px-3 text-[11px] transition-colors cursor-pointer ${
                      industryLevel === level ? 'bg-accent/10 text-accent' : 'text-muted hover:text-foreground'
                    }`}
                  >
                    {level}級
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[11px] text-muted">監控對象</span>
              <span className="text-[10px] font-mono text-muted">{draft.sector_targets?.length ?? 0}/20</span>
            </div>
            {(draft.sector_targets?.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-1">
                {draft.sector_targets?.map(target => (
                  <span key={target.key} className="inline-flex items-center gap-1 rounded bg-accent/8 px-1.5 py-1 text-[10px] text-accent">
                    {target.name}
                    <button type="button" onClick={() => toggleSectorTarget(target)} title="移除" className="text-accent/60 hover:text-danger cursor-pointer">
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <label className="relative block">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted" />
              <input
                value={sectorQuery}
                onChange={event => setSectorQuery(event.target.value)}
                placeholder={`搜尋${SECTOR_KIND_OPTIONS.find(option => option.key === sectorKind)?.label ?? '板塊'}`}
                className="h-9 w-full rounded-btn border border-border bg-base pl-8 pr-3 text-xs text-foreground placeholder:text-muted/50 focus:border-accent/50 focus:outline-none"
              />
            </label>
            <div className="grid max-h-48 grid-cols-1 gap-1 overflow-y-auto pr-1 sm:grid-cols-2">
              {visibleSectorTargets.length === 0 ? (
                <div className="col-span-full rounded-btn border border-dashed border-border py-6 text-center text-xs text-muted">
                  {options.isLoading ? '正在載入…' : '沒有可用的監控對象'}
                </div>
              ) : visibleSectorTargets.map(target => {
                const selected = draft.sector_targets?.some(item => item.key === target.key) ?? false
                const unavailable = !target.available || (target.kind !== 'index' && target.member_count < 5)
                const targetLabel = target.kind === 'industry'
                  ? (target.value ?? target.name).replaceAll('-', ' / ')
                  : target.name
                return (
                  <button
                    key={target.key}
                    type="button"
                    disabled={unavailable}
                    aria-pressed={selected}
                    onClick={() => toggleSectorTarget(target)}
                    title={!target.available ? '請先在即時監控設定中加入該指數' : target.member_count < 5 ? '有效成分少於 5 檔' : targetLabel}
                    className={`flex h-9 min-w-0 items-center gap-2 rounded-btn border px-2.5 text-left transition-colors ${
                      unavailable
                        ? 'cursor-not-allowed border-border/40 bg-base/40 text-muted/40'
                        : selected
                          ? 'cursor-pointer border-accent/40 bg-accent/10 text-accent'
                          : 'cursor-pointer border-border bg-base text-secondary hover:border-accent/25 hover:text-foreground'
                    }`}
                  >
                    <span className="min-w-0 flex-1 truncate text-[11px]">{targetLabel}</span>
                    {target.symbol && <span className="shrink-0 font-mono text-[9px] opacity-60">{target.symbol}</span>}
                    {target.kind !== 'index' && <span className="shrink-0 font-mono text-[9px] opacity-60">{target.member_count}</span>}
                    <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border ${selected ? 'border-accent bg-accent text-white' : 'border-border text-transparent'}`}>
                      <Check className="h-2.5 w-2.5" />
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="grid gap-3 border-t border-border/60 pt-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <span className="text-[11px] text-muted">觸發方式</span>
              <div className="grid h-9 grid-cols-2 overflow-hidden rounded-btn border border-border bg-base">
                {([
                  ['change_pct', '漲跌幅到達'],
                  ['momentum', '快速異動'],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={(draft.sector_trigger ?? 'change_pct') === key}
                    onClick={() => setDraft(d => ({ ...d, sector_trigger: key }))}
                    className={`text-[11px] font-medium transition-colors cursor-pointer ${
                      (draft.sector_trigger ?? 'change_pct') === key ? 'bg-accent/10 text-accent' : 'text-muted hover:text-foreground'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <span className="text-[11px] text-muted">方向</span>
              <div className="grid h-9 grid-cols-2 overflow-hidden rounded-btn border border-border bg-base">
                {([
                  ['up', draft.sector_trigger === 'momentum' ? '快速上漲' : '上漲'],
                  ['down', draft.sector_trigger === 'momentum' ? '快速下跌' : '下跌'],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={draft.direction === key}
                    onClick={() => setDraft(d => ({ ...d, direction: key }))}
                    className={`text-[11px] font-medium transition-colors cursor-pointer ${
                      draft.direction === key ? 'bg-accent/10 text-accent' : 'text-muted hover:text-foreground'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {draft.sector_trigger === 'momentum' && (
              <label className="space-y-1.5">
                <span className="text-[11px] text-muted">統計窗口</span>
                <select
                  value={draft.window_minutes ?? 5}
                  onChange={event => setDraft(d => ({ ...d, window_minutes: Number(event.target.value) as MonitorRule['window_minutes'] }))}
                  className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground"
                >
                  {[1, 3, 5, 10, 15].map(window => <option key={window} value={window}>{window} 分鐘</option>)}
                </select>
              </label>
            )}
            <label className="space-y-1.5">
              <span className="text-[11px] text-muted">{draft.sector_trigger === 'momentum' ? '窗口變化閾值' : '板塊漲跌幅閾值'}</span>
              <span className="relative block">
                <input
                  type="number"
                  min="0.01"
                  max="20"
                  step="0.1"
                  value={draft.threshold_pct ?? 1}
                  onChange={event => setDraft(d => ({ ...d, threshold_pct: Number(event.target.value) }))}
                  className="h-9 w-full rounded-btn border border-border bg-base pl-3 pr-8 text-xs font-mono text-foreground"
                />
                <span className="absolute right-3 top-2.5 text-xs text-muted">%</span>
              </span>
            </label>
          </div>
          {sectorKind !== 'index' && (
            <div className="flex flex-wrap gap-1.5 text-[9px] text-muted">
              <span className="rounded bg-elevated px-1.5 py-0.5">等權平均</span>
              <span className="rounded bg-elevated px-1.5 py-0.5">行情覆蓋 ≥ 80%</span>
              <span className="rounded bg-elevated px-1.5 py-0.5">有效成分 ≥ 5</span>
            </div>
          )}
        </div>
      )}


      {/* 作用範圍 */}
      {draft.type !== 'sector' && <div className="space-y-2">
        <span className="text-[11px] text-muted">作用範圍</span>
        <div className="flex items-start gap-1.5">
          <select value={draft.scope} onChange={e => setDraft(d => ({ ...d, scope: e.target.value as MonitorRule['scope'] }))} className="h-7 w-32 shrink-0 rounded border border-border bg-base px-2 text-[11px] text-foreground">
            {visibleScopes.map(s => <option key={s.key} value={s.key} disabled={hasIntradaySignal && s.key !== 'symbols'}>{s.label}</option>)}
          </select>
          {draft.scope === 'symbols' && (
            <div className="min-w-0 flex-1 space-y-1.5">
              {/* 導入與搜索: 與範圍下拉同一行等高(h-7), 不換行, 搜索框佔滿剩餘寬度 */}
              <div className="flex items-center gap-1.5">
                <div className="relative" ref={watchMenuRef}>
                  <button
                    type="button"
                    onClick={() => setWatchMenuOpen(v => !v)}
                    title="從自選 / 自選分組匯入當前成員 (一次性拷貝, 後續增刪自選不影響本規則); 需要動態跟隨分組請把作用範圍切到「自選分組」"
                    className={`inline-flex h-7 shrink-0 items-center gap-1 rounded border px-2 text-[11px] transition-colors cursor-pointer ${
                      watchMenuOpen
                        ? 'border-accent/40 bg-accent/10 text-accent'
                        : 'border-border bg-base text-secondary hover:border-accent/30 hover:text-foreground'
                    }`}
                  >
                    <ListPlus className="h-3 w-3" />自選匯入
                  </button>
                  {watchMenuOpen && (
                    <div className="absolute z-10 mt-1 max-h-56 w-44 overflow-y-auto rounded border border-border bg-surface py-1 shadow-lg">
                      {watchlistQ.isLoading ? (
                        <div className="px-2.5 py-2 text-[11px] text-muted">正在載入自選…</div>
                      ) : watchImportOptions.length === 0 ? (
                        <div className="px-2.5 py-2 text-[11px] text-muted">自選列表為空</div>
                      ) : watchImportOptions.map(option => (
                        <button
                          key={option.key}
                          type="button"
                          onClick={() => importSymbols(option.symbols)}
                          className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] text-secondary transition-colors hover:bg-elevated hover:text-foreground cursor-pointer"
                        >
                          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${option.dot}`} />
                          <span className="min-w-0 flex-1 truncate">{option.name}</span>
                          <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted">{option.symbols.length}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="relative min-w-0 flex-1">
                  <input
                    value={symbolQuery}
                    onChange={e => setSymbolQuery(e.target.value)}
                    placeholder="搜尋代號或名稱新增標的…"
                    className="h-7 w-full rounded border border-border bg-base pl-6 pr-2 text-[11px] text-foreground focus:outline-none focus:border-accent/50"
                  />
                  <Search className="absolute left-1.5 top-1.5 h-3.5 w-3.5 text-muted" />
                  {symbolSearch.data && symbolSearch.data.results.length > 0 && (
                    <div className="absolute z-10 mt-1 max-h-48 w-full overflow-auto rounded border border-border bg-surface shadow-lg">
                      {symbolSearch.data.results.map(r => (
                        <button key={r.symbol} onClick={() => addSymbol(r.symbol)} className="block w-full px-2 py-1 text-left text-[11px] hover:bg-elevated cursor-pointer">
                          <span className="font-mono text-foreground/80">{r.symbol}</span>
                          {(() => { const b = boardTag(r.symbol); return b && <span className={`ml-1 inline-flex items-center justify-center rounded px-0.5 text-[9px] font-bold leading-tight border ${b.color}`}>{b.label}</span> })()}
                          <span className="ml-1 text-muted">{r.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              {/* 「已加入 N 只」單獨成行(收起態, 與控件列左對齊) / 標籤管理區(展開態) */}
              {draft.symbols.length > 0 && !symbolsExpanded && (
                <button
                  type="button"
                  onClick={() => setSymbolsExpanded(true)}
                  title="展開管理標的列表"
                  className="inline-flex items-center gap-1 rounded border border-accent/30 bg-accent/10 px-2 py-0.5 text-[10px] text-accent transition-colors hover:bg-accent/20 cursor-pointer"
                >
                  已加入 <span className="font-mono font-semibold tabular-nums">{draft.symbols.length}</span> 檔
                  <ChevronDown className="h-3 w-3" />
                </button>
              )}
              {draft.symbols.length > 0 && symbolsExpanded && (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-muted">已加入 <span className="font-mono tabular-nums text-secondary">{draft.symbols.length}</span> 檔</span>
                    <span className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setDraft(d => ({ ...d, symbols: [] }))}
                        className="inline-flex items-center gap-0.5 text-[10px] text-muted transition-colors hover:text-warning cursor-pointer"
                        title="移除全部標的"
                      >
                        <Eraser className="h-3 w-3" />清空
                      </button>
                      <button
                        type="button"
                        onClick={() => setSymbolsExpanded(false)}
                        className="inline-flex items-center gap-0.5 text-[10px] text-muted transition-colors hover:text-foreground cursor-pointer"
                      >
                        收起<ChevronUp className="h-3 w-3" />
                      </button>
                    </span>
                  </div>
                  <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto rounded border border-border/60 bg-base/40 p-1.5">
                    {draft.symbols.map(sym => {
                      const b = boardTag(sym)
                      const name = nameBySymbol[sym]
                      return (
                        <span key={sym} className="inline-flex items-center gap-1 rounded border border-border bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">
                          <span className="max-w-24 truncate text-foreground/90" title={name ? `${name} ${sym}` : sym}>{name ?? sym}</span>
                          {b && <span className={`inline-flex items-center justify-center rounded px-0.5 text-[9px] font-bold leading-tight border ${b.color}`}>{b.label}</span>}
                          <span className="font-mono text-[9px] tabular-nums text-muted">{sym}</span>
                          <button
                            onClick={() => setDraft(d => ({ ...d, symbols: d.symbols.filter(s => s !== sym) }))}
                            className="text-muted transition-colors hover:text-danger cursor-pointer"
                            title={name ? `移除 ${name}` : `移除 ${sym}`}
                          >
                            <X className="h-2.5 w-2.5" />
                          </button>
                        </span>
                      )
                    })}
                  </div>
                </>
              )}
            </div>
          )}
          {draft.scope === 'watchlist_group' && (
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="relative" ref={groupMenuRef}>
                <button
                  type="button"
                  onClick={() => setGroupMenuOpen(v => !v)}
                  title="選擇要監控的自選分組 (動態綁定,分組內增刪標的自動生效)"
                  className={`inline-flex h-7 max-w-full items-center gap-1.5 rounded border px-2 text-[11px] transition-colors cursor-pointer ${
                    groupMenuOpen
                      ? 'border-accent/40 bg-accent/10 text-accent'
                      : 'border-border bg-base text-secondary hover:border-accent/30 hover:text-foreground'
                  }`}
                >
                  {selectedGroup ? (
                    <>
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${resolveWatchlistGroupColor(selectedGroup.color).dot}`} />
                      <span className="max-w-32 truncate">{selectedGroup.name}</span>
                      <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted">{groupCounts[selectedGroup.id] ?? 0}檔</span>
                    </>
                  ) : (
                    <span className="text-muted">{watchGroupsQ.isLoading ? '載入分組中…' : '選擇自選分組…'}</span>
                  )}
                  {groupMenuOpen ? <ChevronUp className="h-3 w-3 shrink-0" /> : <ChevronDown className="h-3 w-3 shrink-0" />}
                </button>
                {groupMenuOpen && (
                  <div className="absolute z-10 mt-1 max-h-56 w-56 overflow-y-auto rounded border border-border bg-surface py-1 shadow-lg">
                    {watchGroupsQ.isLoading ? (
                      <div className="px-2.5 py-2 text-[11px] text-muted">正在載入分組…</div>
                    ) : groupList.length === 0 ? (
                      <div className="px-2.5 py-2 text-[11px] text-muted">
                        還沒有自選分組,<Link to="/watchlist" className="text-accent hover:text-accent/80">去自選頁建立 →</Link>
                      </div>
                    ) : groupList.map(g => (
                      <button
                        key={g.id}
                        type="button"
                        onClick={() => {
                          setDraft(d => ({ ...d, group_id: g.id }))
                          setGroupMenuOpen(false)
                        }}
                        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] text-secondary transition-colors hover:bg-elevated hover:text-foreground cursor-pointer"
                      >
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${resolveWatchlistGroupColor(g.color).dot}`} />
                        <span className="min-w-0 flex-1 truncate">{g.name}</span>
                        <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted">{groupCounts[g.id] ?? 0}</span>
                        {draft.group_id === g.id && <Check className="h-3 w-3 shrink-0 text-accent" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {/* 成員預覽 (只讀): 讓用戶明確當前監控哪些標的; 與手動選標的的可編輯標籤區分 */}
              {selectedGroup && (
                <div className="space-y-1">
                  {selectedGroupSymbols.length > 0 ? (
                    <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto rounded border border-border/60 bg-base/40 p-1.5">
                      {selectedGroupSymbols.map(sym => {
                        const b = boardTag(sym)
                        return (
                          <span key={sym} className="inline-flex items-center gap-1 rounded border border-border bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">
                            <span className="max-w-24 truncate text-foreground/90" title={groupNameBySymbol[sym] ? `${groupNameBySymbol[sym]} ${sym}` : sym}>
                              {groupNameBySymbol[sym] ?? sym}
                            </span>
                            {b && <span className={`inline-flex items-center justify-center rounded px-0.5 text-[9px] font-bold leading-tight border ${b.color}`}>{b.label}</span>}
                            <span className="font-mono text-[9px] tabular-nums text-muted">{sym}</span>
                          </span>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="rounded border border-dashed border-border px-2 py-1.5 text-[10px] text-muted">
                      該分組目前沒有標的,後續在分組內新增自選會自動納入監控
                    </div>
                  )}
                  <div className="text-[10px] text-muted/70">
                    動態綁定:分組內增刪標的自動同步監控範圍,無需修改本規則
                  </div>
                </div>
              )}
            </div>
          )}
          {draft.scope === 'all' && <span className="text-[11px] text-muted">對全市場所有標的生效</span>}
          {draft.scope === 'sector' && <span className="text-[11px] text-muted/60">板塊精確過濾(開發中,當前等同全市場)</span>}
        </div>
      </div>}

      {/* 觸發條件 (非 strategy) */}
      {draft.type !== 'strategy' && draft.type !== 'sector' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted">觸發條件</span>
            <div className="flex items-center gap-2">
              <select value={draft.logic} onChange={e => setDraft(d => ({ ...d, logic: e.target.value as MonitorRule['logic'] }))} className="h-7 rounded border border-border bg-base px-1.5 text-[11px] text-foreground">
                {(options.data?.logics ?? []).map(l => <option key={l.key} value={l.key}>{l.label}</option>)}
              </select>
              <button onClick={() => addCond('truth')} className="inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent/80 cursor-pointer">
                <Plus className="h-3 w-3" />訊號條件
              </button>
              <button onClick={() => addCond('threshold')} className="inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent/80 cursor-pointer">
                <Plus className="h-3 w-3" />閾值條件
              </button>
            </div>
          </div>

          {selectedSignals.length > 0 || (options.data?.builtin_signals ?? []).length > 0 ? (
            <div>
              <div className="mb-1.5 text-[10px] text-muted/70">訊號條件 (點選)</div>
              <SignalPicker
                signals={selectedSignals}
                onChange={onSignalPickerChange}
                kind="entry"
                builtinSignals={pickerSignals}
                disabledSignals={intradayDisabledSignals}
                disabledSignalHint={intradayDisabledHint}
              />
              {hasIntradaySignal && (
                <div className={`mt-2 text-[10px] ${intradaySupport?.available === false ? 'text-danger' : 'text-muted'}`}>
                  {intradaySupport?.available === false
                    ? intradaySupport.reason
                    : `分時穿越按已完成的一分鐘判斷,僅支援指定標的,當前最多監聽 ${intradaySupport?.max_symbols ?? 0} 檔。`}
                </div>
              )}
            </div>
          ) : null}

          {thresholdConds.length > 0 && (
            <div className="space-y-1.5">
              {thresholdConds.map((c, i) => {
                const realIdx = draft.conditions.indexOf(c)
                return (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className="text-[10px] text-muted/60 w-6 text-right shrink-0">{i === 0 && selectedSignals.length === 0 ? '當' : draft.logic === 'and' ? '且' : '或'}</span>
                    <select value={c.field} onChange={e => updateCond(realIdx, { field: e.target.value })} className="w-32 h-7 px-1.5 rounded bg-base border border-border text-[11px] text-foreground focus:outline-none focus:border-accent/50">
                      {thresholdFields.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                    </select>
                    <select value={c.op} onChange={e => updateCond(realIdx, { op: e.target.value })} className="w-12 h-7 px-1 rounded bg-base border border-border text-[11px] font-mono text-foreground text-center focus:outline-none focus:border-accent/50">
                      {operators.map(op => <option key={op} value={op}>{op}</option>)}
                    </select>
                    <input type="number" value={c.value ?? 0} onChange={e => updateCond(realIdx, { value: parseFloat(e.target.value) })} step="any" className="w-24 h-7 px-1.5 rounded bg-base border border-border text-[11px] font-mono text-foreground text-center focus:outline-none focus:border-accent/50" />
                    <button onClick={() => removeCond(realIdx)} className="p-1 rounded text-muted hover:text-danger hover:bg-danger/10 cursor-pointer">
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {draft.conditions.length === 0 && (
            <div className="rounded border border-dashed border-border px-3 py-4 text-center text-[11px] text-muted">
              點擊上方「訊號條件」或「閾值條件」新增觸發規則
            </div>
          )}
        </div>
      )}

      {/* strategy 類型: 選策略 + 方向 */}
      {draft.type === 'strategy' && (
        <div className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="min-w-0 flex-1 space-y-1.5">
              <span className="text-[11px] text-muted">搜尋策略</span>
              <span className="relative block">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted" />
                <input
                  value={strategyQuery}
                  onChange={e => setStrategyQuery(e.target.value)}
                  placeholder="搜尋名稱、標籤或策略 ID"
                  className="h-9 w-full rounded-btn border border-border bg-base pl-8 pr-3 text-xs text-foreground placeholder:text-muted/50 focus:border-accent/50 focus:outline-none"
                />
              </span>
            </label>
            <div className="grid grid-cols-4 gap-1 rounded-btn border border-border bg-base p-1 sm:w-[19rem]">
              {strategyCategories.map(category => (
                <button
                  key={category.key}
                  type="button"
                  aria-pressed={strategyCategory === category.key}
                  onClick={() => setStrategyCategory(category.key)}
                  className={`flex h-7 min-w-0 items-center justify-center gap-1 rounded px-1 text-[10px] font-medium transition-colors cursor-pointer ${
                    strategyCategory === category.key
                      ? 'bg-elevated text-foreground'
                      : 'text-muted hover:text-secondary'
                  }`}
                >
                  <span className="truncate">{category.label}</span>
                  <span className="font-mono text-[9px] opacity-70">{category.count}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="grid max-h-56 grid-cols-1 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
            {strategies.isLoading ? (
              <div className="col-span-full py-8 text-center text-xs text-muted">正在載入策略…</div>
            ) : visibleStrategies.length === 0 ? (
              <div className="col-span-full rounded-btn border border-dashed border-border py-8 text-center text-xs text-muted">沒有相符的策略</div>
            ) : visibleStrategies.map(strategy => {
              const active = draft.strategy_id === strategy.id
              const sourceMeta = STRATEGY_SOURCE_META[strategy.source]
              const summary = strategy.tags?.length
                ? strategy.tags.slice(0, 3).join(' · ')
                : (strategy.description || strategy.id)
              return (
                <button
                  key={strategy.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setDraft(d => ({ ...d, strategy_id: strategy.id }))}
                  className={`flex min-h-14 min-w-0 items-start gap-2 rounded-btn border px-3 py-2 text-left transition-colors cursor-pointer ${
                    active
                      ? 'border-accent/45 bg-accent/10'
                      : 'border-border bg-base hover:border-accent/25 hover:bg-elevated/50'
                  }`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className={`shrink-0 rounded border px-1 py-px text-[9px] font-medium ${sourceMeta.className}`}>{sourceMeta.label}</span>
                      <span className="truncate text-xs font-medium text-foreground">{strategy.name}</span>
                    </span>
                    <span className="mt-1 block truncate text-[10px] text-muted" title={summary}>{summary}</span>
                  </span>
                  <span className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border ${
                    active ? 'border-accent bg-accent text-white' : 'border-border text-transparent'
                  }`}>
                    <Check className="h-2.5 w-2.5" />
                  </span>
                </button>
              )
            })}
          </div>

          <div className="border-t border-border/60 pt-3">
            <div
              className="mb-2 text-[11px] text-muted"
              title="評分範圍僅過濾選股結果與買入訊號，賣出訊號不受限制"
            >
              評分範圍
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
              <label className="space-y-1.5">
                <span className="text-[10px] text-muted">最低分（含）</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  value={draft.score_min ?? ''}
                  onChange={event => setDraft(d => ({
                    ...d,
                    score_min: event.target.value === '' ? null : Number(event.target.value),
                  }))}
                  placeholder="不限"
                  className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground placeholder:text-muted/50 focus:border-accent/50 focus:outline-none"
                />
              </label>
              <span className="mt-5 text-xs text-muted">至</span>
              <label className="space-y-1.5">
                <span className="text-[10px] text-muted">最高分（含）</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  value={draft.score_max ?? ''}
                  onChange={event => setDraft(d => ({
                    ...d,
                    score_max: event.target.value === '' ? null : Number(event.target.value),
                  }))}
                  placeholder="不限"
                  className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground placeholder:text-muted/50 focus:border-accent/50 focus:outline-none"
                />
              </label>
            </div>
          </div>

          <div className="border-t border-border/60 pt-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="text-[11px] text-muted">通知事件</span>
              <span className="text-[9px] text-muted">至少選擇一項</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {(['signal', 'pool'] as const).map(group => (
                <div key={group} className="rounded-btn border border-border bg-base p-2.5">
                  <div className="mb-2 text-[10px] font-medium text-secondary">
                    {group === 'signal' ? '交易訊號' : '選股結果'}
                  </div>
                  <div className="space-y-2">
                    {STRATEGY_NOTIFY_EVENT_OPTIONS.filter(option => option.group === group).map(option => (
                      <label key={option.key} className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={(draft.notify_events ?? LEGACY_STRATEGY_NOTIFY_EVENTS).includes(option.key)}
                          onChange={() => toggleStrategyEvent(option.key)}
                          className="h-3.5 w-3.5 accent-accent cursor-pointer"
                        />
                        <span className="text-[11px] text-foreground">{option.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            {(draft.notify_events ?? LEGACY_STRATEGY_NOTIFY_EVENTS).length === 0 && (
              <div className="mt-2 text-[10px] text-danger">至少選擇一個通知事件</div>
            )}
          </div>
        </div>
      )}

      {/* 通知設置 */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <label className="space-y-1.5">
          <span className="text-[11px] text-muted">冷卻期(秒)</span>
          <input type="number" value={draft.cooldown_seconds} onChange={e => setDraft(d => ({ ...d, cooldown_seconds: parseInt(e.target.value) || 0 }))} min={0} className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground" />
        </label>
        <label className="space-y-1.5">
          <span className="text-[11px] text-muted">嚴重級別</span>
          <select value={draft.severity} onChange={e => setDraft(d => ({ ...d, severity: e.target.value as MonitorRule['severity'] }))} className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground">
            {(options.data?.severities ?? []).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="space-y-1.5 md:col-span-1">
          <span className="text-[11px] text-muted">自訂提示(可選)</span>
          <input value={draft.message} onChange={e => setDraft(d => ({ ...d, message: e.target.value }))} placeholder="留空用預設文案" className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground" />
        </label>
      </div>

      {/* 外部推播 — LINE / Telegram */}
      <div className="rounded-btn border border-border/40 bg-base/40 p-3 space-y-2">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-medium text-foreground">Webhook 推送</span>
          <span className="text-[9px] text-muted">觸發時推送告警到外部</span>
        </div>

        {/* 渠道列表 */}
        <div className="space-y-1.5">
          {/* LINE Messaging API */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={(draft.webhook_channels ?? []).includes('line')}
              onChange={() => toggleChannel('line')}
              className="h-3 w-3 accent-accent cursor-pointer"
            />
            <span className="text-[11px] text-foreground">LINE</span>
            <span className="text-[9px] text-muted">Messaging API</span>
            {(draft.webhook_channels ?? []).includes('line') && (
              <span className={`ml-auto text-[9px] ${lineConfigured ? 'text-emerald-500' : 'text-warning'}`}>
                {lineConfigured ? '已配置' : '未配置'}
              </span>
            )}
          </label>

          {/* Telegram Bot API */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={(draft.webhook_channels ?? []).includes('telegram')}
              onChange={() => toggleChannel('telegram')}
              className="h-3 w-3 accent-accent cursor-pointer"
            />
            <span className="text-[11px] text-foreground">Telegram</span>
            <span className="text-[9px] text-muted">Bot API</span>
            {(draft.webhook_channels ?? []).includes('telegram') && (
              <span className={`ml-auto text-[9px] ${telegramConfigured ? 'text-emerald-500' : 'text-warning'}`}>
                {telegramConfigured ? '已配置' : '未配置'}
              </span>
            )}
          </label>

        </div>

        {/* 勾選了某渠道但該渠道地址未配置 → 提示前往設置 */}
        {(draft.webhook_channels ?? []).length > 0 && (() => {
          const selected = draft.webhook_channels ?? []
          const unconfigured: string[] = []
          if (selected.includes('line') && !lineConfigured) unconfigured.push('LINE')
          if (selected.includes('telegram') && !telegramConfigured) unconfigured.push('Telegram')
          if (unconfigured.length === 0) return null
          return (
            <p className="text-[10px] leading-relaxed text-warning/80">
              {unconfigured.join('、')}尚未配置,
              <Link to="/settings?tab=monitoring" className="text-accent hover:text-accent/80">前往設定頁設定 →</Link>
            </p>
          )
        })()}
        {(draft.webhook_channels ?? []).length > 0 && (() => {
          const selected = draft.webhook_channels ?? []
          const ready: string[] = []
          if (selected.includes('line') && lineConfigured) ready.push('LINE')
          if (selected.includes('telegram') && telegramConfigured) ready.push('Telegram')
          if (ready.length === 0) return null
          return (
            <p className="text-[10px] leading-relaxed text-muted">
              命中本規則時,告警將推送到已配置的{ready.join(' + ')}。
            </p>
          )
        })()}
      </div>

      {error && <div className="rounded-btn border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger">{error}</div>}

      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="px-4 py-1.5 rounded-btn bg-elevated text-secondary text-xs cursor-pointer">取消</button>
        <button onClick={() => save.mutate()} disabled={save.isPending} className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer">
          <Save className="h-3.5 w-3.5" />儲存
        </button>
      </div>
    </div>
  )
}
