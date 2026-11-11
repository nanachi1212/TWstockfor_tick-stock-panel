import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import { Check, Database, Plus, RefreshCw, Zap, FileWarning, Puzzle, CheckCircle2 } from 'lucide-react'
import { api, type DataSourceItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { toast } from '@/components/Toast'
import { DataSourceEditor } from './DataSourceEditor'
import { TaiwanHistoryDataCard } from './TaiwanHistoryDataCard'
import { FinMindSettingsCard } from './FinMindSettingsCard'
import { ExternalKeysCard } from './ExternalKeysCard'
import { Modal } from '@/components/Modal'

const DATASET_LABEL: Record<string, string> = {
  daily: '日K',
  adj_factor: '除權',
  realtime: '即時',
  minute: '分鐘',
  financial: '財務',
}

/** 資料集 → 路由偏好欄位 + 預設值 + 展示標籤 (financial 無後端路由消費方, 僅展示不參與切換) */
/** 能力卡片定義: 資料集 + 說明 (路由選擇嵌入每張卡片) */
const CAPABILITY_CARDS = [
  { dataset: 'daily', label: '日K', desc: '歷史 + 即時覆寫' },
  { dataset: 'adj_factor', label: '除權因子', desc: '復權計算' },
  { dataset: 'realtime', label: '即時行情', desc: '全市場快照' },
  { dataset: 'minute', label: '分鐘K', desc: '分時圖，回測' },
] as const

/** 資料集 → 路由偏好欄位 + 預設值 (financial 無後端路由消費方, 僅展示) */
const DATASET_ROUTE: Record<string, {
  field: 'daily_data_provider' | 'adj_factor_provider' | 'minute_data_provider' | 'realtime_data_provider'
  def: string
}> = {
  daily: { field: 'daily_data_provider', def: 'taiwan' },
  adj_factor: { field: 'adj_factor_provider', def: 'same_as_daily' },
  minute: { field: 'minute_data_provider', def: 'taiwan' },
  realtime: { field: 'realtime_data_provider', def: 'taiwan' },
}

/** 卡片內靜態資料集標籤: 只展示該源適配了哪些資料集 (路由選擇在下方能力卡片) */
function DatasetChipRow({ datasets }: { datasets: string[] }) {
  if (datasets.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1 ml-3.5 mt-1">
      <span className="text-xs font-medium text-accent bg-accent/10 px-1 py-0.5 rounded">已適配</span>
      {datasets.map(ds => (
        <span key={ds} className="text-xs text-muted/60 bg-elevated/60 px-1 py-0.5 rounded">
          {DATASET_LABEL[ds] || ds}
        </span>
      ))}
    </div>
  )
}

/** 按能力路由網格: 每個資料來源詳情下展示一張能力卡,卡片上以可點標籤列出
 *  所有具備該能力的資料來源 — 點誰,該資料集就立刻由誰提供,可跨源自由組合。
 *  台灣官方資料源詳情列出全部可設定資料集; 其他源只列自己參與的能力。 */
function SourceCapabilityGrid({ sourceName, sourceDisplay, datasets, candidatesOf, providerOf, pending, onSelect, anyCustom, onReset }: {
  sourceName: string
  sourceDisplay: string
  datasets: string[]
  /** 該資料集實際支援的候選提供方, 按推薦順序 */
  candidatesOf: (dataset: string) => { name: string; display: string }[]
  /** 該資料集當前的原始路由偏好值 (adj_factor 可能是 same_as_daily) */
  providerOf: (dataset: string) => string
  pending?: boolean
  onSelect: (dataset: string, provider: string) => void
  anyCustom?: boolean
  onReset?: () => void
}) {
  const isDefault = sourceName === 'taiwan'
  const dsList = datasets
  if (dsList.length === 0) return null

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-muted">
          {isDefault
            ? '每個能力可個別選擇提供者 — 點標籤即刻切換,可用來源依各資料集能力列出'
            : `${sourceDisplay} 參與的能力 — 每個能力都可個別選擇由哪個資料來源提供`}
        </span>
        {isDefault && anyCustom && (
          <button
            onClick={onReset}
            disabled={pending}
            className="text-xs text-muted/60 hover:text-accent transition-colors disabled:opacity-50"
          >
            恢復預設
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5 mb-2">
        {dsList.map(ds => {
          const route = DATASET_ROUTE[ds]
          const meta = CAPABILITY_CARDS.find(c => c.dataset === ds)
          const label = meta?.label || DATASET_LABEL[ds] || ds
          const desc = meta?.desc || ''
          if (!route) {
            return (
              <div key={ds} className="rounded-lg border border-border/50 bg-elevated/20 px-3 py-2.5" title="該資料集暫不支援切換資料來源">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-foreground truncate">{label}</div>
                    {desc && <div className="text-xs text-muted mt-0.5">{desc}</div>}
                  </div>
                  <span className="shrink-0 px-1.5 py-0.5 rounded text-xs text-muted/50 bg-elevated/60">固定</span>
                </div>
              </div>
            )
          }
          const raw = providerOf(ds)
          const candidates = candidatesOf(ds)
          return (
            <div key={ds} className="rounded-lg border border-border/50 bg-elevated/20 px-3 py-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs font-medium text-foreground">{label}</div>
                  {desc && <div className="text-xs text-muted mt-0.5">{desc}</div>}
                </div>
              </div>
              {/* 提供者標籤:點誰該資料集就由誰提供,目前項目高亮 */}
              <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-border/50">
                {ds === 'adj_factor' && (
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => onSelect(ds, 'same_as_daily')}
                    title="除權因子跟隨日K資料來源"
                    className={tagCls(raw === 'same_as_daily')}
                  >
                    跟隨日K
                  </button>
                )}
                {candidates.length === 0 && <span className="text-xs text-muted">尚未設定此資料集來源</span>}
                {candidates.map(c => (
                  <button
                    key={c.name}
                    type="button"
                    disabled={pending}
                    onClick={() => onSelect(ds, c.name)}
                    title={`「${label}」由 ${c.display} 提供`}
                    className={tagCls(raw === c.name)}
                  >
                    {c.display}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** 提供方標籤樣式: 當前項高亮(accent), 其餘弱化可點 */
function tagCls(active: boolean) {
  return `px-1.5 py-0.5 rounded text-xs transition-colors select-none disabled:opacity-50 cursor-pointer ${
    active
      ? 'bg-accent/15 text-accent font-medium'
      : 'bg-elevated/60 text-muted/70 hover:bg-accent/15 hover:text-accent'
  }`
}


/** 詳情元件所需的路由上下文(由面板構造) */
interface RouteCtx {
  /** 資料集 → 實際支援的候選提供方列表 */
  candidatesOf: (dataset: string) => { name: string; display: string }[]
  /** 資料集 → 當前原始路由偏好值 (adj_factor 可能是 same_as_daily) */
  providerOf: (dataset: string) => string
  displayOf: (name?: string) => string
  pending: boolean
  onSelect: (dataset: string, provider: string) => void
  anyCustom: boolean
  onReset: () => void
}

export function SettingsDataSourcesPanel() {
  const qc = useQueryClient()
  const prefs = usePreferences()
  const sources = useQuery({ queryKey: QK.dataSources, queryFn: api.dataSources })
  const [selected, setSelected] = useState<string>('taiwan') // 當前在右側編輯的源 name
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const reload = useMutation({
    mutationFn: api.reloadDataSources,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.dataSources })
      // 重載可能改變資料集聲明 → 能力與即時模式隨之變化
      qc.invalidateQueries({ queryKey: QK.capabilities })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      toast('設定已重新載入', 'success')
    },
  })

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteDataSource(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.dataSources })
      qc.invalidateQueries({ queryKey: QK.preferences })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      setSelected('taiwan')
      setConfirmDelete(null)
      toast('資料來源已刪除', 'success')
    },
  })

  const switchProvider = useMutation({
    mutationFn: async (name: string) => {
      // taiwan: 全量重置為台灣官方預設路由
      if (name === 'taiwan') {
        return api.updateDataProviders({
          daily_data_provider: 'taiwan',
          adj_factor_provider: 'same_as_daily',
          realtime_data_provider: 'taiwan',
          minute_data_provider: 'taiwan',
          financial_data_provider: 'taiwan',
        })
      }
      // 非台灣官方: 該源適配了哪些資料集就接管哪些, 其餘回退預設
      const supported = new Set(
        allItems.find(s => s.name === name)?.datasets ?? []
      )
      const pick = (dataset: string) =>
        supported.has(dataset) ? name : (dataset === 'adj_factor' ? 'same_as_daily' : 'taiwan')
      return api.updateDataProviders({
        daily_data_provider: pick('daily'),
        adj_factor_provider: pick('adj_factor'),
        realtime_data_provider: pick('realtime'),
        minute_data_provider: pick('minute'),
        financial_data_provider: 'taiwan',
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.preferences })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      // 切換會改變即時行情 provider → 模式(none/watchlist/full_market)立即重新整理
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      toast('資料來源已切換', 'success')
    },
  })

  const editExisting = useMutation({
    mutationFn: (name: string) => api.dataSource(name),
    onSuccess: (_data, name) => setSelected(name),
  })

  const builtin: DataSourceItem[] = sources.data?.builtin ?? []
  const customList: DataSourceItem[] = sources.data?.custom ?? []
  const errors = sources.data?.errors ?? []
  const activeName = prefs.data?.daily_data_provider || 'taiwan'

  const allItems = [
    ...builtin,
    ...customList,
  ]

  const selectedCustom = customList.find(s => s.name === selected)

  // ===== 各資料集當前的有效提供方 (除權 same_as_daily = 跟隨日K) =====
  // 用於"服務中"徽標: 改單個能力路由隻影響對應資料集, 不再產生"當前資料來源被切換"的表現
  const dailyPref = prefs.data?.daily_data_provider || 'taiwan'
  const adjPref = prefs.data?.adj_factor_provider || 'same_as_daily'
  const effProvider: Record<string, string> = {
    daily: dailyPref,
    adj_factor: adjPref === 'same_as_daily' ? dailyPref : adjPref,
    minute: prefs.data?.minute_data_provider || 'taiwan',
    realtime: prefs.data?.realtime_data_provider || 'taiwan',
  }
  const servingDatasets = (name: string) =>
    Object.entries(effProvider)
      .filter(([dataset, provider]) => provider === name && allItems.find(item => item.name === name)?.datasets.includes(dataset))
      .map(([dataset]) => dataset)
  const servingLabels = (name: string) =>
    servingDatasets(name).map(k => DATASET_LABEL[k] || k)

  const displayOf = (name?: string) =>
    name === 'taiwan' ? '台灣官方資料源'
      : name === 'same_as_daily' ? '跟隨日K'
        : allItems.find(s => s.name === name)?.display_name || name || ''

  const invalidateRouting = () => {
    qc.invalidateQueries({ queryKey: QK.preferences })
    qc.invalidateQueries({ queryKey: QK.capabilities })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }

  // 單個能力的提供方切換: 點標籤即刻生效 (same_as_daily 僅除權有效 = 跟隨日K)
  const routeMut = useMutation({
    mutationFn: ({ dataset, provider }: { dataset: string; provider: string }) => {
      const route = DATASET_ROUTE[dataset]
      if (!route) throw new Error(`資料集 ${dataset} 不支援切換資料來源`)
      return api.updateDataProviders({ [route.field]: provider })
    },
    onSuccess: (_d, v) => {
      invalidateRouting()
      const label = DATASET_LABEL[v.dataset] || v.dataset
      toast(`「${label}」已切換為 ${displayOf(v.provider)}`, 'success')
    },
    onError: (e: Error) => toast(`路由切換失敗: ${e.message}`, 'error'),
  })

  // 恢復預設: 路由全部回 台灣官方資料源
  const resetRouteMut = useMutation({
    mutationFn: () => api.updateDataProviders({
      daily_data_provider: 'taiwan',
      adj_factor_provider: 'same_as_daily',
      minute_data_provider: 'taiwan',
      realtime_data_provider: 'taiwan',
    }),
    onSuccess: () => {
      invalidateRouting()
      toast('資料集路由已恢復預設', 'success')
    },
    onError: (e: Error) => toast(`恢復失敗: ${e.message}`, 'error'),
  })

  const anyCustomRouting = Object.values(effProvider).some(v => v !== 'taiwan') || adjPref !== 'same_as_daily'

  // 僅列出實際宣告該能力的來源，避免把缺少的分鐘/除權資料當成官方能力。
  const candidatesOf = (dataset: string) => allItems
    .filter(item => item.datasets.includes(dataset))
    .map(item => ({ name: item.name, display: item.display_name || item.name }))

  // 資料集 → 當前原始路由偏好值 (adj_factor 保留 same_as_daily 以驅動"跟隨日K"標籤態)
  const providerOf = (dataset: string) => {
    switch (dataset) {
      case 'daily': return dailyPref
      case 'adj_factor': return adjPref
      case 'minute': return prefs.data?.minute_data_provider || 'taiwan'
      case 'realtime': return prefs.data?.realtime_data_provider || 'taiwan'
      default: return 'taiwan'
    }
  }

  // 傳給各資料來源詳情的路由上下文(能力卡標籤選擇 + 當前提供方展示)
  const routeCtx: RouteCtx = {
    candidatesOf,
    providerOf,
    displayOf,
    pending: routeMut.isPending,
    onSelect: (dataset, provider) => routeMut.mutate({ dataset, provider }),
    anyCustom: anyCustomRouting,
    onReset: () => resetRouteMut.mutate(),
  }

  return (
    <div className="space-y-5 max-w-5xl">
      {/* ===== 頂部: 當前資料來源 + 資料來源選擇 (一個大卡片) ===== */}
      <section className="rounded-card border border-border bg-surface p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2.5">
            <Database className="h-4 w-4 text-secondary" />
            <h2 className="text-sm font-medium text-foreground">資料來源</h2>
            <span
              className="text-xs text-muted/40 font-mono truncate hidden lg:inline max-w-[480px]"
              title={sources.data?.config_dir}
            >
              {sources.data?.config_dir}
            </span>
          </div>
          <button
            onClick={() => reload.mutate()}
            disabled={reload.isPending}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-btn text-xs text-muted hover:text-foreground hover:bg-elevated transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${reload.isPending ? 'animate-spin' : ''}`} />
            重新載入
          </button>
        </div>

        {/* 插件化說明 (置頂黃色提示條): 接入自有行情 → 把文檔交給 AI */}
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5">
          <Puzzle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <div className="text-xs leading-relaxed text-muted">
            <span className="text-secondary">接入自有行情?</span>
            把文件交給 AI 即可自動接入:
            <span className="mx-0.5 rounded bg-elevated/70 px-1 py-px font-mono text-xs text-secondary">docs/custom-data-source.md</span>
            (自有 HTTP 介面)
          </div>
        </div>

        {/* 資料來源選擇 - 橫向卡片列表 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
          {allItems.map(item => {
            const serving = servingLabels(item.name)
            const isSelected = selected === item.name
            return (
              <div
                key={item.name}
                onClick={() => {
                  setSelected(item.name)
                  // 只有使用者自定義源 (YAML) 才進編輯器; 官方資料源不可編輯
                  if (customList.some(c => c.name === item.name)) {
                    editExisting.mutate(item.name)
                  }
                }}
                className={`relative text-left rounded-lg border px-3.5 py-3 transition-all ${
                  isSelected
                    ? 'border-accent/50 bg-accent/5 ring-1 ring-accent/20 cursor-pointer'
                    : 'border-border/60 bg-elevated/20 hover:bg-elevated/40 cursor-pointer'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                      serving.length > 0 ? 'bg-accent' : 'bg-transparent border border-muted/40'
                    }`}
                  />
                  <span className={`text-sm truncate flex-1 ${serving.length > 0 ? 'font-medium text-foreground' : 'text-secondary'}`}>
                    {item.display_name}
                  </span>
                  {serving.length > 0 && (
                    <span
                      className="shrink-0 inline-flex items-center gap-0.5 text-xs text-accent"
                      title={`正在提供: ${serving.join('，')}`}
                    >
                      <Check className="h-2.5 w-2.5" /> 服務中
                    </span>
                  )}
                  {item.name === 'taiwan' && (
                    <span className="shrink-0 rounded bg-accent/15 px-1 py-0.5 text-xs font-medium leading-none text-accent">官方預設</span>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); switchProvider.mutate(item.name) }}
                    disabled={switchProvider.isPending}
                    className="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium bg-accent/10 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50"
                  >
                    使用
                  </button>
                </div>
                {/* 資料集標籤(靜態): 該源適配的資料集, 路由選擇在下方能力卡片 */}
                <DatasetChipRow datasets={item.datasets} />
              </div>
            )
          })}

          {/* 新增資料來源卡片 */}
          <button
            onClick={() => setSelected('__new__')}
            className={`rounded-lg border border-dashed px-3.5 py-3 transition-all flex items-center justify-center gap-1.5 text-sm ${
              selected === '__new__'
                ? 'border-accent/50 bg-accent/5 text-accent'
                : 'border-border/50 text-muted hover:text-foreground hover:border-border hover:bg-elevated/30'
            }`}
          >
            <Plus className="h-3.5 w-3.5" />
            新增資料來源
          </button>
        </div>

        {/* 錯誤提示 */}
        {errors.length > 0 && (
          <div className="mt-3 flex items-start gap-1.5 px-3 py-2 rounded-lg bg-danger/5 border border-danger/20">
            <FileWarning className="h-3.5 w-3.5 text-danger shrink-0 mt-0.5" />
            <div className="text-xs text-danger/80 leading-relaxed space-y-0.5">
              {errors.map((err, idx) => (
                <div key={idx}>
                  <span className="font-mono">{err.name || err.path}</span>: {err.errors.join('; ')}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-3 flex items-center gap-3 text-xs text-muted/50">
          <span>點擊查看各來源能力</span>
          <span className="text-muted/30">，</span>
          <span>能力卡片上點擊標籤,個別選擇每個資料集的提供者</span>
          <span className="text-muted/30">，</span>
          <span>點「使用」一鍵套用該來源全部能力</span>
          <span className="text-muted/30">，</span>
          <span>官方未提供的資料集需設定自訂來源</span>
        </div>

      </section>

      {/* ===== 下方: 編輯區 ===== */}
      <AnimatePresence mode="wait">
        <motion.div
          key={selected}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.15 }}
        >
          {selected === 'taiwan' ? (
            <TaiwanOfficialDetail
              active={servingDatasets('taiwan').length > 0}
              onSwitch={() => switchProvider.mutate('taiwan')}
              switching={switchProvider.isPending}
              route={routeCtx}
            />
          ) : selected === '__new__' || customList.some(c => c.name === selected) ? (
            selected === '__new__' ? (
              <DataSourceEditor
                key={selected}
                initial={null}
                existingName={undefined}
                onCancel={() => setSelected('taiwan')}
                onSaved={() => {
                  qc.invalidateQueries({ queryKey: QK.dataSources })
                  // 資料集聲明變化 → 能力增廣與即時模式立即重新整理
                  qc.invalidateQueries({ queryKey: QK.preferences })
                  qc.invalidateQueries({ queryKey: QK.capabilities })
                  qc.invalidateQueries({ queryKey: QK.quoteStatus })
                  setSelected('taiwan')
                }}
                activeName={activeName}
                onActivate={(name) => switchProvider.mutate(name)}
              />
            ) : (
              /* 自定義源詳情: 能力卡片(路由) + 編輯器 */
              <div className="space-y-5">
                <section className="rounded-card border border-border bg-surface p-6">
                  <div className="flex items-center gap-2.5 mb-4">
                    <Database className="h-4 w-4 text-secondary" />
                    <h3 className="text-sm font-medium text-foreground">
                      {selectedCustom?.display_name || selected}，資料集能力與路由
                    </h3>
                  </div>
                  <SourceCapabilityGrid
                    sourceName={selected}
                    sourceDisplay={selectedCustom?.display_name || selected}
                    datasets={selectedCustom?.datasets || []}
                    candidatesOf={routeCtx.candidatesOf}
                    providerOf={routeCtx.providerOf}
                    pending={routeCtx.pending}
                    onSelect={routeCtx.onSelect}
                  />
                </section>
                <DataSourceEditor
                  key={selected}
                  initial={null}
                  existingName={selected}
                  onCancel={() => setSelected('taiwan')}
                  onSaved={() => {
                    qc.invalidateQueries({ queryKey: QK.dataSources })
                    // 資料集聲明變化 → 能力增廣與即時模式立即重新整理
                    qc.invalidateQueries({ queryKey: QK.preferences })
                    qc.invalidateQueries({ queryKey: QK.capabilities })
                    qc.invalidateQueries({ queryKey: QK.quoteStatus })
                    // 強制清除該源的詳情快取, 下次編輯重新拉取最新設定
                    qc.removeQueries({ queryKey: ['data-source-detail', selected] })
                  }}
                  activeName={activeName}
                  onActivate={(name) => switchProvider.mutate(name)}
                  onDelete={selectedCustom ? () => setConfirmDelete(selected) : undefined}
                />
              </div>
            )
          ) : null}
        </motion.div>
      </AnimatePresence>

      {/* 刪除確認彈窗 */}
      {confirmDelete && (
        <Modal onClose={() => setConfirmDelete(null)} ariaLabel="刪除資料來源" overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" panelClassName="w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6">
            <h3 className="text-sm font-medium text-foreground mb-2">刪除資料來源</h3>
            <p className="text-xs text-secondary mb-5">
              確認刪除「{customList.find(s => s.name === confirmDelete)?.display_name || confirmDelete}」? 該資料來源的設定檔將被移除,此操作無法復原。
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-3 py-1.5 rounded-btn bg-elevated text-secondary hover:bg-elevated/80 text-sm transition-colors"
              >
                取消
              </button>
              <button
                onClick={() => remove.mutate(confirmDelete)}
                disabled={remove.isPending}
                className="px-3 py-1.5 rounded-btn bg-danger/15 text-danger hover:bg-danger/25 text-sm font-medium transition-colors disabled:opacity-50"
              >
                {remove.isPending ? '刪除中…' : '確認刪除'}
              </button>
            </div>
</Modal>
      )}
    </div>
  )
}

function TaiwanOfficialDetail({ active, onSwitch, switching, route }: {
  active: boolean
  onSwitch: () => void
  switching: boolean
  route: RouteCtx
}) {
  return (
    <div className="space-y-5">
      <section className="rounded-card border border-border bg-surface p-6">
        <div className="flex items-start gap-4 mb-5">
          <div className="h-11 w-11 rounded-xl bg-accent/10 flex items-center justify-center shrink-0">
            <Database className="h-5 w-5 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold text-foreground">台灣官方資料源 (TWSE/TPEx)</h2>
              <span className="rounded bg-accent/15 px-1.5 py-0.5 text-xs font-medium text-accent">官方公開</span>
              {active && (
                <span className="inline-flex items-center gap-1 text-xs text-accent bg-accent/10 px-2 py-1 rounded">
                  <Check className="h-2.5 w-2.5" /> 服務中
                </span>
              )}
            </div>
            <p className="text-xs text-secondary mt-1.5 leading-relaxed">
              整合台灣證券交易所 (TWSE) 與證券櫃檯買賣中心 (TPEx) 官方公開資料，提供日K歷史行情、即時報價、標的清單與基本面資訊，無需任何 API Key 即可開箱即用。在下方能力卡片上點標籤，可個別選擇該資料集由哪個資料來源提供。
            </p>
          </div>
        </div>

        {/* 全部能力的提供方標籤選擇 + 恢復預設 */}
        <SourceCapabilityGrid
          sourceName="taiwan"
          sourceDisplay="台灣官方資料源"
          datasets={['daily', 'adj_factor', 'realtime', 'minute', 'financial']}
          candidatesOf={route.candidatesOf}
          providerOf={route.providerOf}
          pending={route.pending}
          onSelect={route.onSelect}
          anyCustom={route.anyCustom}
          onReset={route.onReset}
        />

        {!active && (
          <button
            onClick={onSwitch}
            disabled={switching}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn bg-accent/10 text-accent text-xs font-medium hover:bg-accent/20 transition-colors disabled:opacity-50 mt-3"
          >
            <Zap className="h-3.5 w-3.5" />
            切換為目前資料來源
          </button>
        )}
      </section>

      {/* 台股歷史日 K 資料庫狀態與下載/更新 */}
      <TaiwanHistoryDataCard />

      {/* FinMind 基本面與籌碼資料來源設定 */}
      <FinMindSettingsCard />

      <ExternalKeysCard />

      <section className="rounded-card border border-border bg-surface p-6">
        <div className="flex items-center gap-2 mb-2">
          <CheckCircle2 className="h-4 w-4 text-success" />
          <h3 className="text-sm font-medium text-foreground">公開免費，免 API Key</h3>
        </div>
        <p className="text-xs text-secondary leading-relaxed">
          本資料源直接透過 TWSE 與 TPEx 官方公開端點取得盤後日K與最新報價，並提供 Yahoo Finance 與公開資訊作為備援與補充。所有資料均自動於本機建立 Parquet/DuckDB 快取，高速且不受第三方付費 API 限制。
        </p>
      </section>
    </div>
  )
}
