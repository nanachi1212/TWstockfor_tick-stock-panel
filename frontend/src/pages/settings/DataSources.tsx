import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import { Check, Database, Eye, EyeOff, KeyRound, Plus, RefreshCw, Zap, FileWarning, Puzzle, AlertCircle, CheckCircle2, Loader2, Save, Trash2 } from 'lucide-react'
import { api, type DataSourceItem, type PluginDataSourceItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { toast } from '@/components/Toast'
import { DataSourceEditor } from './DataSourceEditor'
import { TaiwanHistoryDataCard } from './TaiwanHistoryDataCard'

const DATASET_LABEL: Record<string, string> = {
  daily: '日K',
  adj_factor: '除權',
  realtime: '即時',
  minute: '分鐘',
  financial: '財務',
}

/** 數據集 → 路由偏好字段 + 默認值 + 展示標籤 (financial 無後端路由消費方, 僅展示不參與切換) */
/** 能力卡片定義: 數據集 + 說明 (路由選擇嵌入每張卡片) */
const CAPABILITY_CARDS = [
  { dataset: 'daily', label: '日K', desc: '歷史 + 即時覆寫' },
  { dataset: 'adj_factor', label: '除權因子', desc: '復權計算' },
  { dataset: 'realtime', label: '即時行情', desc: '全市場快照' },
  { dataset: 'minute', label: '分鐘K', desc: '分時圖 · 回測' },
] as const

/** 數據集 → 路由偏好字段 + 默認值 (financial 無後端路由消費方, 僅展示) */
const DATASET_ROUTE: Record<string, {
  field: 'daily_data_provider' | 'adj_factor_provider' | 'minute_data_provider' | 'realtime_data_provider'
  def: string
}> = {
  daily: { field: 'daily_data_provider', def: 'taiwan' },
  adj_factor: { field: 'adj_factor_provider', def: 'same_as_daily' },
  minute: { field: 'minute_data_provider', def: 'taiwan' },
  realtime: { field: 'realtime_data_provider', def: 'taiwan' },
}

/** 卡片內靜態數據集標籤: 只展示該源適配了哪些數據集 (路由選擇在下方能力卡片) */
function DatasetChipRow({ datasets }: { datasets: string[] }) {
  if (datasets.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1 ml-3.5 mt-1">
      <span className="text-[9px] font-medium text-accent bg-accent/10 px-1 py-0.5 rounded">已適配</span>
      {datasets.map(ds => (
        <span key={ds} className="text-[9px] text-muted/60 bg-elevated/60 px-1 py-0.5 rounded">
          {DATASET_LABEL[ds] || ds}
        </span>
      ))}
    </div>
  )
}

/** 按能力路由網格: 每個數據源詳情下展示一張能力卡,卡片上以可點標籤列出
 *  所有具備該能力的數據源 — 點誰,該數據集就立刻由誰提供,可跨源自由組合。
 *  TickFlow 詳情含全部能力; 其他源只列自己參與的能力。 */
function SourceCapabilityGrid({ sourceName, sourceDisplay, datasets, candidatesOf, providerOf, pending, onSelect, anyCustom, onReset }: {
  sourceName: string
  sourceDisplay: string
  datasets: string[]
  /** 該數據集的所有候選提供方 (含 TickFlow), 按推薦順序 */
  candidatesOf: (dataset: string) => { name: string; display: string }[]
  /** 該數據集當前的原始路由偏好值 (adj_factor 可能是 same_as_daily) */
  providerOf: (dataset: string) => string
  pending?: boolean
  onSelect: (dataset: string, provider: string) => void
  anyCustom?: boolean
  onReset?: () => void
}) {
  const isDefault = sourceName === 'taiwan' || sourceName === 'tickflow'
  const dsList = isDefault ? [...datasets, 'financial'] : datasets
  if (dsList.length === 0) return null

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] text-muted">
          {isDefault
            ? '每個能力可個別選擇提供者 — 點標籤即刻切換,未個別設定的由內建資料來源提供'
            : `${sourceDisplay} 參與的能力 — 每個能力都可個別選擇由哪個資料來源提供`}
        </span>
        {isDefault && anyCustom && (
          <button
            onClick={onReset}
            disabled={pending}
            className="text-[11px] text-muted/60 hover:text-accent transition-colors disabled:opacity-50"
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
                    {desc && <div className="text-[10px] text-muted mt-0.5">{desc}</div>}
                  </div>
                  <span className="shrink-0 px-1.5 py-0.5 rounded text-[10px] text-muted/50 bg-elevated/60">固定</span>
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
                  {desc && <div className="text-[10px] text-muted mt-0.5">{desc}</div>}
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
  return `px-1.5 py-0.5 rounded text-[10px] transition-colors select-none disabled:opacity-50 cursor-pointer ${
    active
      ? 'bg-accent/15 text-accent font-medium'
      : 'bg-elevated/60 text-muted/70 hover:bg-accent/15 hover:text-accent'
  }`
}


/** 詳情組件所需的路由上下文(由面板構造) */
interface RouteCtx {
  /** 數據集 → 候選提供方列表 (含 TickFlow) */
  candidatesOf: (dataset: string) => { name: string; display: string }[]
  /** 數據集 → 當前原始路由偏好值 (adj_factor 可能是 same_as_daily) */
  providerOf: (dataset: string) => string
  displayOf: (name?: string) => string
  pending: boolean
  onSelect: (dataset: string, provider: string) => void
  anyCustom: boolean
  onReset: () => void
}

/** 插件 API Key 配置區: 狀態 + 輸入 + 保存並檢測。
 *  先探後存: 後端用候選 Key 實探一次, 無效不落盤; secrets.json 優先於 .env。 */
function PluginKeyConfig({ plugin }: { plugin: PluginDataSourceItem }) {
  const qc = useQueryClient()
  const [keyInput, setKeyInput] = useState('')
  const [revealing, setRevealing] = useState(false)
  const [saved, setSaved] = useState(false)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: QK.dataSources })
    qc.invalidateQueries({ queryKey: QK.capabilities })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }

  const save = useMutation({
    mutationFn: () => api.savePluginKey(plugin.name, keyInput.trim()),
    onSuccess: (data) => {
      invalidate()
      if (data.ok) {
        setKeyInput('')
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
    },
    onError: (e: Error) => toast(`儲存失敗: ${e.message}`, 'error'),
  })

  const clear = useMutation({
    mutationFn: () => api.clearPluginKey(plugin.name),
    onSuccess: (data) => {
      invalidate()
      if (data.ok) {
        toast(
          data.plugin_available
            ? '已清除介面設定的 Key(.env 中的同名變數仍然生效)'
            : 'Key 已清除,插件不再可用',
          'success',
        )
      }
    },
    onError: (e: Error) => toast(`清除失敗: ${e.message}`, 'error'),
  })

  return (
    <section className="rounded-card border border-border bg-surface p-6">
      <div className="flex items-center gap-2.5 mb-3">
        <KeyRound className="h-4 w-4 text-secondary" />
        <h3 className="text-sm font-medium text-foreground">API Key</h3>
        <span className="text-[10px] text-muted/50 uppercase tracking-wider">{plugin.api_key_env}</span>
      </div>
      <p className="text-xs text-secondary leading-relaxed mb-4">
        Key 儲存為本機檔案(secrets.json,優先順序高於 .env),不會上傳任何第三方。儲存前會先用該 Key
        實際探測一次資料介面,無效則不落地儲存。
      </p>

      {/* 當前狀態 */}
      <div className="flex items-center justify-between mb-4">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-widest text-muted">狀態</div>
          <div className="mt-1 flex items-center gap-2 min-w-0">
            {plugin.available ? (
              <>
                <CheckCircle2 className="h-4 w-4 text-bear shrink-0" />
                <span className="text-sm font-medium shrink-0">已設定</span>
                {save.data?.ok && save.data.api_key_masked && (
                  <span className="font-mono text-xs text-secondary truncate">{save.data.api_key_masked}</span>
                )}
              </>
            ) : (
              <>
                <AlertCircle className="h-4 w-4 text-muted shrink-0" />
                <span className="text-sm font-medium text-muted shrink-0">未設定</span>
                <span className="text-xs text-muted/70 truncate" title={plugin.status}>{plugin.status}</span>
              </>
            )}
          </div>
        </div>
        {plugin.available && (
          <button
            onClick={() => clear.mutate()}
            disabled={clear.isPending}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-btn bg-elevated text-secondary hover:text-danger text-xs transition-colors duration-150 ease-smooth disabled:opacity-50 shrink-0"
          >
            <Trash2 className="h-3 w-3" />
            清除
          </button>
        )}
      </div>

      {/* 輸入 */}
      <form
        onSubmit={(e) => { e.preventDefault(); if (keyInput.trim()) save.mutate() }}
        className="space-y-2"
      >
        <div className="relative">
          <input
            type={revealing ? 'text' : 'password'}
            placeholder={plugin.available ? '貼上新 Key 取代目前' : `貼上 ${plugin.display_name} API Key`}
            value={keyInput}
            onChange={(e) => { setKeyInput(e.target.value); if (saved) setSaved(false) }}
            autoComplete="off"
            className="w-full px-3 py-2 pr-9 rounded-input bg-base border border-border text-sm font-mono focus:outline-none focus:border-accent transition-colors duration-150 ease-smooth"
          />
          <button
            type="button"
            onClick={() => setRevealing((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-foreground transition-colors duration-150 ease-smooth"
            tabIndex={-1}
            aria-label={revealing ? '隱藏' : '顯示'}
          >
            {revealing ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <button
          type="submit"
          disabled={save.isPending || (!keyInput.trim() && !saved)}
          className="w-full h-10 rounded-xl bg-accent text-white text-sm font-semibold flex items-center justify-center gap-2 hover:bg-accent/90 disabled:opacity-40 transition-all"
        >
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : saved ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {save.isPending ? '驗證中…' : saved ? '已儲存' : '儲存並檢測'}
        </button>
      </form>

      {/* 無效 Key —— 先探後存: 探測失敗時不存儲 */}
      {save.data && !save.data.ok && (
        <div className="mt-3 text-xs text-danger flex items-center gap-1.5">
          <AlertCircle className="h-3 w-3 shrink-0" />
          {save.data.error || 'Key 無效,未儲存'}
        </div>
      )}
      {save.isError && (
        <div className="mt-3 text-xs text-danger">
          儲存失敗:{String((save.error as Error).message)}
        </div>
      )}
    </section>
  )
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
      // 重載可能改變數據集聲明 → 能力與實時模式隨之變化
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
      // taiwan/tickflow: 全量重置為台灣官方默認路由
      if (name === 'taiwan' || name === 'tickflow') {
        return api.updateDataProviders({
          daily_data_provider: 'taiwan',
          adj_factor_provider: 'same_as_daily',
          realtime_data_provider: 'taiwan',
          minute_data_provider: 'taiwan',
          financial_data_provider: 'taiwan',
        })
      }
      // 非台灣官方: 該源適配了哪些數據集就接管哪些, 其餘回退默認
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
      // 切換會改變實時行情 provider → 模式(none/watchlist/full_market)立即刷新
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      toast('資料來源已切換', 'success')
    },
  })

  const editExisting = useMutation({
    mutationFn: (name: string) => api.dataSource(name),
    onSuccess: (_data, name) => setSelected(name),
  })

  const installMut = useMutation({
    mutationFn: (name: string) => api.installPlugin(name),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.dataSources })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      if (data.install_ok) {
        toast('插件相依套件安裝成功', 'success')
      } else {
        toast(data.install_message || '安裝失敗', 'error')
      }
    },
    onError: (e: Error) => toast(`安裝失敗: ${e.message}`, 'error'),
  })

  const uninstallMut = useMutation({
    mutationFn: (name: string) => api.uninstallPlugin(name),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.dataSources })
      qc.invalidateQueries({ queryKey: QK.preferences })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      if (data.uninstall_ok) {
        toast(data.uninstall_message || '已解除安裝', 'success')
      } else {
        toast(data.uninstall_message || '解除安裝失敗', 'error')
      }
    },
    onError: (e: Error) => toast(`解除安裝失敗: ${e.message}`, 'error'),
  })

  const builtin: DataSourceItem[] = sources.data?.builtin ?? []
  const pluginList: PluginDataSourceItem[] = sources.data?.plugins ?? []
  const customList: DataSourceItem[] = sources.data?.custom ?? []
  const errors = sources.data?.errors ?? []
  const activeName = prefs.data?.daily_data_provider || 'taiwan'

  // 插件 name → 狀態 (供卡片渲染時判斷 available/installing 等)
  const pluginMap = new Map(pluginList.map(p => [p.name, p]))
  const pluginNames = new Set(pluginList.map(p => p.name))

  // 頂部數據源選擇列表 (內置 + 所有插件 + 自定義 + 新增)
  const pluginItems: DataSourceItem[] = pluginList.map(p => ({
    name: p.name, display_name: p.display_name, datasets: p.datasets,
  }))
  const allItems = [
    ...builtin,
    ...pluginItems,
    ...customList,
  ]

  const selectedCustom = customList.find(s => s.name === selected)

  // ===== 各數據集當前的有效提供方 (除權 same_as_daily = 跟隨日K) =====
  // 用於"服務中"徽標: 改單個能力路由隻影響對應數據集, 不再產生"當前數據源被切換"的表現
  const dailyPref = prefs.data?.daily_data_provider || 'taiwan'
  const adjPref = prefs.data?.adj_factor_provider || 'same_as_daily'
  const effProvider: Record<string, string> = {
    daily: dailyPref,
    adj_factor: adjPref === 'same_as_daily' ? dailyPref : adjPref,
    minute: prefs.data?.minute_data_provider || 'taiwan',
    realtime: prefs.data?.realtime_data_provider || 'taiwan',
  }
  const servingDatasets = (name: string) =>
    Object.entries(effProvider).filter(([, v]) => (name === 'taiwan' || name === 'tickflow') ? (v === 'taiwan' || v === 'tickflow') : v === name).map(([k]) => k)
  const servingLabels = (name: string) =>
    servingDatasets(name).map(k => DATASET_LABEL[k] || k)

  const displayOf = (name?: string) =>
    (name === 'taiwan' || name === 'tickflow') ? '台灣官方資料源'
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

  // 恢復默認: 路由全部回 台灣官方資料源
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

  const anyCustomRouting = Object.values(effProvider).some(v => v !== 'taiwan' && v !== 'tickflow') || adjPref !== 'same_as_daily'

  // 某數據集的全部候選提供方 (台灣官方恆在首位, 其餘按聲明該數據集的數據源列出)
  const candidatesOf = (dataset: string) => {
    const list = [{ name: 'taiwan', display: '台灣官方資料源' }]
    for (const item of allItems) {
      if (item.name !== 'tickflow' && item.name !== 'taiwan' && item.datasets.includes(dataset)) {
        list.push({ name: item.name, display: item.display_name || item.name })
      }
    }
    return list
  }

  // 數據集 → 當前原始路由偏好值 (adj_factor 保留 same_as_daily 以驅動"跟隨日K"標籤態)
  const providerOf = (dataset: string) => {
    switch (dataset) {
      case 'daily': return dailyPref
      case 'adj_factor': return adjPref
      case 'minute': return prefs.data?.minute_data_provider || 'taiwan'
      case 'realtime': return prefs.data?.realtime_data_provider || 'taiwan'
      default: return 'taiwan'
    }
  }

  // 傳給各數據源詳情的路由上下文(能力卡標籤選擇 + 當前提供方展示)
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
      {/* ===== 頂部: 當前數據源 + 數據源選擇 (一個大卡片) ===== */}
      <section className="rounded-card border border-border bg-surface p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2.5">
            <Database className="h-4 w-4 text-secondary" />
            <h2 className="text-sm font-medium text-foreground">資料來源</h2>
            <span
              className="text-[10px] text-muted/40 font-mono truncate hidden lg:inline max-w-[480px]"
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
          <div className="text-[11px] leading-relaxed text-muted">
            <span className="text-secondary">資料來源已插件化</span>
            ,接入自有行情?把文件交給 AI 即可自動接入:
            <span className="mx-0.5 rounded bg-elevated/70 px-1 py-px font-mono text-[10px] text-secondary">docs/custom-data-source.md</span>
            (自有 HTTP 介面) ·
            <span className="mx-0.5 rounded bg-elevated/70 px-1 py-px font-mono text-[10px] text-secondary">docs/plugin-development.md</span>
            (插件開發)
          </div>
        </div>

        {/* 數據源選擇 - 橫向卡片列表 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
          {allItems.map(item => {
            const serving = servingLabels(item.name)
            const isSelected = selected === item.name
            const plugin = pluginMap.get(item.name)
            const pluginUnavailable = plugin && !plugin.available
            const installing = installMut.isPending && installMut.variables === item.name
            const uninstalling = uninstallMut.isPending && uninstallMut.variables === item.name
            return (
              <div
                key={item.name}
                onClick={() => {
                  // 未就緒插件僅當支持界面配 Key 時可點開(進詳情配置); 其餘不可選
                  if (pluginUnavailable && !plugin?.api_key_env) return
                  setSelected(item.name)
                  // 只有用戶自定義源 (YAML) 才進編輯器; tickflow 和插件不可編輯
                  if (customList.some(c => c.name === item.name)) {
                    editExisting.mutate(item.name)
                  }
                }}
                className={`relative text-left rounded-lg border px-3.5 py-3 transition-all ${
                  pluginUnavailable && !plugin?.api_key_env
                    ? 'border-border/40 bg-elevated/10 opacity-70'
                    : isSelected
                      ? 'border-accent/50 bg-accent/5 ring-1 ring-accent/20 cursor-pointer'
                      : 'border-border/60 bg-elevated/20 hover:bg-elevated/40 cursor-pointer'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                      pluginUnavailable ? 'bg-muted/30' : serving.length > 0 ? 'bg-accent' : 'bg-transparent border border-muted/40'
                    }`}
                  />
                  <span className={`text-sm truncate flex-1 ${serving.length > 0 ? 'font-medium text-foreground' : 'text-secondary'}`}>
                    {item.display_name}
                  </span>
                  {serving.length > 0 && (
                    <span
                      className="shrink-0 inline-flex items-center gap-0.5 text-[9px] text-accent"
                      title={`正在提供: ${serving.join(' · ')}`}
                    >
                      <Check className="h-2.5 w-2.5" /> 服務中
                    </span>
                  )}
                  {(item.name === 'taiwan' || item.name === 'tickflow') && (
                    <span className="shrink-0 rounded bg-accent/15 px-1 py-0.5 text-[9px] font-medium leading-none text-accent">官方預設</span>
                  )}
                  {pluginNames.has(item.name) && (
                    <span className="shrink-0 rounded bg-warning/15 px-1 py-0.5 text-[9px] font-medium leading-none text-warning">第三方</span>
                  )}
                  {/* 右側操作區: 插件未安裝→安裝按鈕(runtime=none 無依賴可裝,顯示配置提示); 否則→使用/卸載 */}
                  {pluginUnavailable ? (
                    plugin?.runtime === 'none' ? (
                      <span
                        className="text-[10px] text-muted/50 shrink-0 cursor-help"
                        title={plugin?.status || '未設定憑證'}
                      >
                        點擊設定 Key
                      </span>
                    ) : installing ? (
                      <span className="inline-flex items-center gap-1 text-[9px] text-accent shrink-0">
                        <RefreshCw className="h-2.5 w-2.5 animate-spin" /> 安裝中…
                      </span>
                    ) : (
                      <button
                        onClick={(e) => { e.stopPropagation(); installMut.mutate(item.name) }}
                        disabled={installMut.isPending}
                        className="shrink-0 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-accent/10 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50"
                      >
                        <Zap className="h-2.5 w-2.5" /> 安裝
                      </button>
                    )
                  ) : plugin ? (
                    /* 已安裝插件: 使用 + 卸載 */
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={(e) => { e.stopPropagation(); switchProvider.mutate(item.name) }}
                        disabled={switchProvider.isPending}
                        className="rounded px-1.5 py-0.5 text-[10px] font-medium bg-accent/10 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50"
                      >
                        使用
                      </button>
                      {plugin?.runtime !== 'none' && (uninstalling ? (
                        <RefreshCw className="h-2.5 w-2.5 animate-spin text-muted" />
                      ) : (
                        <button
                          onClick={(e) => { e.stopPropagation(); uninstallMut.mutate(item.name) }}
                          disabled={uninstallMut.isPending}
                          className="text-[10px] text-muted/50 hover:text-danger transition-colors disabled:opacity-40"
                          title="解除安裝依賴"
                        >
                          解除安裝
                        </button>
                      ))}
                    </div>
                  ) : (
                    <button
                      onClick={(e) => { e.stopPropagation(); switchProvider.mutate(item.name) }}
                      disabled={switchProvider.isPending}
                      className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium bg-accent/10 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50"
                    >
                      使用
                    </button>
                  )}
                </div>
                {/* 數據集標籤(靜態): 該源適配的數據集, 路由選擇在下方能力卡片 */}
                <DatasetChipRow datasets={(item.name === 'taiwan' || item.name === 'tickflow') ? [...item.datasets, 'financial'] : item.datasets} />
                {/* 未安裝插件顯示安裝命令提示 */}
                {pluginUnavailable && plugin?.install_hint && (
                  <div className="ml-3.5 mt-1 text-[10px] text-muted/40 font-mono truncate">{plugin.install_hint}</div>
                )}
              </div>
            )
          })}

          {/* 新增數據源卡片 */}
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
            <div className="text-[11px] text-danger/80 leading-relaxed space-y-0.5">
              {errors.map((err, idx) => (
                <div key={idx}>
                  <span className="font-mono">{err.name || err.path}</span>: {err.errors.join('; ')}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-3 flex items-center gap-3 text-[10px] text-muted/50">
          <span>點擊查看各來源能力</span>
          <span className="text-muted/30">·</span>
          <span>能力卡片上點擊標籤,個別選擇每個資料集的提供者</span>
          <span className="text-muted/30">·</span>
          <span>點「使用」一鍵套用該來源全部能力</span>
          <span className="text-muted/30">·</span>
          <span>未個別設定的由台灣官方資料源提供</span>
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
          {(selected === 'taiwan' || selected === 'tickflow') ? (
            <TaiwanOfficialDetail
              active={servingDatasets('taiwan').length > 0 || servingDatasets('tickflow').length > 0}
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
                  // 數據集聲明變化 → 能力增廣與實時模式立即刷新
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
                      {selectedCustom?.display_name || selected} · 資料集能力與路由
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
                    // 數據集聲明變化 → 能力增廣與實時模式立即刷新
                    qc.invalidateQueries({ queryKey: QK.preferences })
                    qc.invalidateQueries({ queryKey: QK.capabilities })
                    qc.invalidateQueries({ queryKey: QK.quoteStatus })
                    // 強制清除該源的詳情緩存, 下次編輯重新拉取最新配置
                    qc.removeQueries({ queryKey: ['data-source-detail', selected] })
                  }}
                  activeName={activeName}
                  onActivate={(name) => switchProvider.mutate(name)}
                  onDelete={selectedCustom ? () => setConfirmDelete(selected) : undefined}
                />
              </div>
            )
          ) : pluginList.find(x => x.name === selected) ? (
            /* 選中插件: 信息 + 能力卡片(路由) + Key 配置 */
            <PluginDetail
              plugin={pluginList.find(x => x.name === selected)!}
              isActive={servingDatasets(selected).length > 0}
              onSwitch={() => switchProvider.mutate(selected)}
              switching={switchProvider.isPending}
              route={routeCtx}
            />
          ) : null}
        </motion.div>
      </AnimatePresence>

      {/* 刪除確認彈窗 */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirmDelete(null)}
          />
          <div className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6">
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
          </div>
        </div>
      )}
    </div>
  )
}

function PluginDetail({ plugin, isActive, onSwitch, switching, route }: {
  plugin: PluginDataSourceItem
  isActive: boolean
  onSwitch: () => void
  switching: boolean
  route: RouteCtx
}) {
  return (
    <div className="space-y-5">
      <section className="rounded-card border border-border bg-surface p-6">
        <div className="flex items-start gap-4 mb-5">
          <div className="h-11 w-11 rounded-xl bg-accent/10 flex items-center justify-center shrink-0">
            <Zap className="h-5 w-5 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h3 className="text-base font-semibold text-foreground">{plugin.display_name}</h3>
              <span className="text-[10px] text-muted/50 uppercase tracking-wider">插件 · {plugin.runtime}</span>
            </div>
            {plugin.description && <p className="text-xs text-secondary leading-relaxed">{plugin.description}</p>}
          </div>
        </div>

        {/* 本源參與的能力: 標籤選擇提供方, 即刻生效 */}
        <SourceCapabilityGrid
          sourceName={plugin.name}
          sourceDisplay={plugin.display_name}
          datasets={plugin.datasets}
          candidatesOf={route.candidatesOf}
          providerOf={route.providerOf}
          pending={route.pending}
          onSelect={route.onSelect}
        />

        <div className="flex items-center gap-3 mt-4">
          {isActive ? (
            <span className="inline-flex items-center gap-1 text-[10px] text-accent bg-accent/10 px-2 py-1 rounded">
              <Check className="h-2.5 w-2.5" /> 服務中
            </span>
          ) : plugin.available ? (
            <button
              onClick={onSwitch}
              disabled={switching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn bg-accent/10 text-accent text-xs font-medium hover:bg-accent/20 transition-colors disabled:opacity-50"
            >
              <Zap className="h-3.5 w-3.5" />
              整體切換為該來源
            </button>
          ) : (
            <span className="text-xs text-muted">{plugin.status}</span>
          )}
        </div>
      </section>

      {/* API Key 配置 (聲明了 api_key_env 的插件) */}
      {plugin.api_key_env && <PluginKeyConfig plugin={plugin} />}
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
              <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent">官方公開</span>
              {active && (
                <span className="inline-flex items-center gap-1 text-[10px] text-accent bg-accent/10 px-2 py-1 rounded">
                  <Check className="h-2.5 w-2.5" /> 服務中
                </span>
              )}
            </div>
            <p className="text-xs text-secondary mt-1.5 leading-relaxed">
              整合台灣證券交易所 (TWSE) 與證券櫃檯買賣中心 (TPEx) 官方公開資料，提供日K歷史行情、即時報價、標的清單與基本面資訊，無需任何 API Key 即可開箱即用。在下方能力卡片上點標籤，可個別選擇該資料集由哪個資料來源提供。
            </p>
          </div>
        </div>

        {/* 全部能力的提供方標籤選擇 + 恢復默認 */}
        <SourceCapabilityGrid
          sourceName="taiwan"
          sourceDisplay="台灣官方資料源"
          datasets={['daily', 'adj_factor', 'realtime', 'minute']}
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

      <section className="rounded-card border border-border bg-surface p-6">
        <div className="flex items-center gap-2 mb-2">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          <h3 className="text-sm font-medium text-foreground">公開免費，免 API Key</h3>
        </div>
        <p className="text-xs text-secondary leading-relaxed">
          本資料源直接透過 TWSE 與 TPEx 官方公開端點獲取盤後日K與最新報價，並提供 Yahoo Finance 與公開資訊作為備援與補充。所有資料均自動於本機建立 Parquet/DuckDB 快取，高速且不受第三方付費 API 限制。
        </p>
      </section>
    </div>
  )
}
