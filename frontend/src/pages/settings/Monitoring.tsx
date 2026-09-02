import { useState, useCallback, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient, useMutation, useQuery } from '@tanstack/react-query'
import {
  Activity,
  Wifi,
  BarChart3,
  Flame,
  Zap,
  Webhook,
  ChevronDown,
} from 'lucide-react'
import {
  usePreferences,
  useQuoteStatus,
  useQuoteInterval,
  useCapabilities,
} from '@/lib/useSharedQueries'
import { useUpdateQuoteInterval, useToggleRealtimeQuotes } from '@/lib/useSharedMutations'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { DepthConfigContent } from '@/components/data/DepthConfigCard'

// 页面 → 显示名
const PAGE_LABELS: Record<string, string> = {
  'overview-market': '看板',
  watchlist: '自選頁',
  'limit-ladder': '連板梯隊',
}

const SIDEBAR_INDEX_OPTIONS = [
  { symbol: '000001.SH', name: '上證指數' },
  { symbol: '399001.SZ', name: '深證成指' },
  { symbol: '399006.SZ', name: '創業板指' },
  { symbol: '000680.SH', name: '科創綜指' },
]

// ===== 导出为 Panel 组件 (由 Settings.tsx 嵌入) =====

export function SettingsMonitoringPanel({ highlight }: { highlight?: string } = {}) {
  const qc = useQueryClient()
  const { data: prefs } = usePreferences()
  const { data: caps } = useCapabilities()
  const { data: quoteStatus } = useQuoteStatus()
  const { data: intervalData } = useQuoteInterval()
  const updateInterval = useUpdateQuoteInterval()
  const toggleQuote = useToggleRealtimeQuotes()
  // 实时模式以 quote_status 为准 (数据源无关): watchlist=自选实时 / full_market=全市场 / none=不可用
  const quoteMode = quoteStatus?.mode ?? 'none'
  const isWatchlistMode = quoteMode === 'watchlist'
  const realtimeEnabled = prefs?.realtime_quotes_enabled ?? false
  // 分时图实时刷新间隔 (秒), 与后端 [3,60] clamp 对齐; 默认 6
  const intradayInterval = prefs?.minute_intraday_refresh_interval ?? 6
  // 滑块本地草稿: 拖动时即时反馈, 停顿 2s 后落库 (与行情轮询滑块一致)
  const [intradayIntervalDraft, setIntradayIntervalDraft] = useState(intradayInterval)
  const refreshPages = prefs?.sse_refresh_pages ?? {}
  const limitLadderMonitor = prefs?.limit_ladder_monitor_enabled ?? false
  const hasDepth = !!caps?.capabilities?.['depth5.batch']
  // 新建监控规则时默认勾选的推送渠道 (全局默认值数组, 单条规则可独立修改)
  const webhookDefaultChannels = prefs?.webhook_default_channels ?? []
  const sidebarIndexSymbols = prefs?.sidebar_index_symbols ?? SIDEBAR_INDEX_OPTIONS.map(i => i.symbol)
  const indicesPinned = prefs?.indices_nav_pinned ?? true
  const isRunning = quoteStatus?.running ?? false
  const isTrading = quoteStatus?.is_trading_hours ?? false
  // 管道/数据修正运行期间实时行情被临时暂停 — 此时禁止开启
  const isPaused = quoteStatus?.paused ?? false
  const interval = intervalData?.interval ?? 6
  const minInterval = intervalData?.min_interval ?? 6
  const maxInterval = intervalData?.max_interval ?? 60
  const [intervalDraft, setIntervalDraft] = useState(interval)
  const feishuWebhookUrl = prefs?.feishu_webhook_url ?? ''
  const feishuWebhookSecret = prefs?.feishu_webhook_secret ?? ''
  const [feishuDraft, setFeishuDraft] = useState(feishuWebhookUrl)
  const [feishuSecretDraft, setFeishuSecretDraft] = useState(feishuWebhookSecret)
  const [feishuError, setFeishuError] = useState('')
  // 企业微信 webhook
  const wecomWebhookUrl = prefs?.wecom_webhook_url ?? ''
  const [wecomDraft, setWecomDraft] = useState(wecomWebhookUrl)
  const [wecomError, setWecomError] = useState('')
  // 企业微信智能机器人 (BotID + Secret, 长连接通道)
  const wecomBotId = prefs?.wecom_bot_id ?? ''
  const wecomBotSecret = prefs?.wecom_bot_secret ?? ''
  const wecomBotEnabled = prefs?.wecom_bot_enabled ?? false
  const [botIdDraft, setBotIdDraft] = useState(wecomBotId)
  const [botSecretDraft, setBotSecretDraft] = useState(wecomBotSecret)
  const [botError, setBotError] = useState('')
  const [botStatus, setBotStatus] = useState<{connected: boolean; last_error: string} | null>(null)
  // 飞书渠道配置区展开态 (推送通知卡片内)
  const [channelOpen, setChannelOpen] = useState(false)
  // 企业微信渠道配置区展开态
  const [wecomOpen, setWecomOpen] = useState(false)
  // 智能机器人配置区展开态
  const [botOpen, setBotOpen] = useState(false)
  useEffect(() => {
    setFeishuDraft(feishuWebhookUrl)
    setFeishuSecretDraft(feishuWebhookSecret)
  }, [feishuWebhookUrl, feishuWebhookSecret])
  useEffect(() => {
    setWecomDraft(wecomWebhookUrl)
  }, [wecomWebhookUrl])
  useEffect(() => {
    setBotIdDraft(wecomBotId)
    setBotSecretDraft(wecomBotSecret)
  }, [wecomBotId, wecomBotSecret])
  const watchlistSymbols = prefs?.realtime_watchlist_symbols ?? []
  const watchlist = useQuery({
    queryKey: QK.watchlist,
    queryFn: () => api.watchlistList(),
    enabled: isWatchlistMode && watchlistSymbols.length > 0,
  })
  const watchlistNameBySymbol = new Map(
    (watchlist.data?.symbols ?? []).map(row => [row.symbol, row.name] as const),
  )

  const save = useCallback(async (cfg: Record<string, unknown>) => {
    try {
      await api.updateRealtimeMonitorConfig(cfg)
      qc.invalidateQueries({ queryKey: QK.preferences })
    } catch (e) {
      // 忽略 — Toast 已在 request 层处理
    }
  }, [qc])

  const handleToggleQuote = useCallback(async (enabled: boolean) => {
    await toggleQuote.mutateAsync(enabled)
    qc.invalidateQueries({ queryKey: QK.preferences })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }, [toggleQuote, qc])

  const toggleSidebarIndex = useCallback((symbol: string, visible: boolean) => {
    const selected = new Set(sidebarIndexSymbols)
    if (visible) selected.add(symbol)
    else selected.delete(symbol)
    const next = SIDEBAR_INDEX_OPTIONS
      .map(item => item.symbol)
      .filter(s => selected.has(s))
    save({ sidebar_index_symbols: next })
  }, [save, sidebarIndexSymbols])

  const toggleIndicesPin = useCallback((pinned: boolean) => {
    api.updateIndicesNavPinned(pinned).then(() => qc.invalidateQueries({ queryKey: QK.preferences }))
  }, [qc])

  const toggleLimitLadderMonitor = useCallback(async (enabled: boolean) => {
    await api.updateLimitLadderMonitor(enabled)
    qc.invalidateQueries({ queryKey: QK.preferences })
  }, [qc])

  // 勾选/取消勾选某个默认推送渠道 (飞书 / 企业微信 各自独立)
  const toggleDefaultChannel = useCallback(async (ch: string, enabled: boolean) => {
    const cur = prefs?.webhook_default_channels ?? []
    const next = enabled ? [...cur, ch] : cur.filter(c => c !== ch)
    await api.updateWebhookDefaultChannels(next)
    qc.invalidateQueries({ queryKey: QK.preferences })
  }, [qc, prefs])

  const saveFeishuWebhook = useMutation({
    mutationFn: ({ url, secret }: { url: string; secret: string }) => api.updateFeishuWebhook(url, secret),
    onSuccess: () => {
      setFeishuError('')
      toast('飛書 Webhook 已儲存', 'success')
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
    onError: (err: any) => setFeishuError(String(err?.message ?? '儲存失敗')),
  })
  const FEISHU_PREFIX = 'https://open.feishu.cn/open-apis/bot/v2/hook/'
  const submitFeishu = useCallback(() => {
    const url = feishuDraft.trim()
    const secret = feishuSecretDraft.trim()
    if (url && !url.startsWith(FEISHU_PREFIX)) {
      setFeishuError('位址需以 ' + FEISHU_PREFIX + ' 開頭')
      return
    }
    saveFeishuWebhook.mutate({ url, secret })
  }, [feishuDraft, feishuSecretDraft, saveFeishuWebhook])

  const saveWecomWebhook = useMutation({
    mutationFn: (url: string) => api.updateWecomWebhook(url),
    onSuccess: () => {
      setWecomError('')
      toast('企業微信 Webhook 已儲存', 'success')
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
    onError: (err: any) => setWecomError(String(err?.message ?? '儲存失敗')),
  })
  const WECOM_PREFIX = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
  const submitWecom = useCallback(() => {
    const url = wecomDraft.trim()
    // 允许完整 URL 或纯 key (36位UUID样式)
    if (url && !url.startsWith(WECOM_PREFIX) && url.length < 20) {
      setWecomError('請輸入完整 Webhook 位址或純 key (至少 20 位)')
      return
    }
    saveWecomWebhook.mutate(url)
  }, [wecomDraft, saveWecomWebhook])

  // 智能机器人 (BotID + Secret) 保存 → 后端立即重建连接
  const saveWecomBot = useMutation({
    mutationFn: ({ botId, secret }: { botId: string; secret: string }) =>
      api.updateWecomBot(botId, secret, true),
    onSuccess: (data) => {
      setBotError('')
      toast('智慧型機器人憑證已儲存,正在連線…', 'success')
      setBotStatus({
        connected: data.wecom_bot_status?.connected ?? false,
        last_error: data.wecom_bot_status?.last_error ?? '',
      })
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
    onError: (err: any) => setBotError(String(err?.message ?? '儲存失敗')),
  })
  const submitBot = useCallback(() => {
    saveWecomBot.mutate({ botId: botIdDraft.trim(), secret: botSecretDraft.trim() })
  }, [botIdDraft, botSecretDraft, saveWecomBot])

  // 智能机器人长连接开关(不改动凭证): 开启→连接, 关闭→断开
  const toggleBotConnection = useMutation({
    mutationFn: (enabled: boolean) => api.toggleWecomBot(enabled),
    onSuccess: (data) => {
      setBotStatus({
        connected: data.wecom_bot_status?.connected ?? false,
        last_error: data.wecom_bot_status?.last_error ?? '',
      })
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
  })

  const runFix = useMutation({
    mutationFn: () => api.runLimitLadderFix(),
    onSuccess: (data) => {
      toast(data.msg, data.ok ? 'success' : 'error')
      // 修正后连板梯队数据变了, 刷新相关缓存
      qc.invalidateQueries({ queryKey: ['limit-ladder'] })
    },
    onError: () => toast('修正請求失敗', 'error'),
  })

  useEffect(() => {
    setIntervalDraft(interval)
  }, [interval])

  useEffect(() => {
    if (intervalDraft === interval) return
    const t = window.setTimeout(() => {
      updateInterval.mutate(intervalDraft)
    }, 2000)
    return () => window.clearTimeout(t)
  }, [intervalDraft, interval, updateInterval])

  // 分时刷新间隔: 服务端值变化时同步本地草稿
  useEffect(() => {
    setIntradayIntervalDraft(intradayInterval)
  }, [intradayInterval])

  // 分时刷新间隔: 草稿与已保存值不同时, 2s 防抖落库
  useEffect(() => {
    if (intradayIntervalDraft === intradayInterval) return
    const t = window.setTimeout(() => {
      save({ minute_intraday_refresh_interval: intradayIntervalDraft })
    }, 2000)
    return () => window.clearTimeout(t)
  }, [intradayIntervalDraft, intradayInterval, save])

  // highlight=depth-fix 时闪烁高亮连板梯队修正卡片
  const [flash, setFlash] = useState(false)
  const flashedRef = useRef(false)
  useEffect(() => {
    if (highlight === 'depth-fix' && !flashedRef.current) {
      flashedRef.current = true
      // 延迟一帧确保 DOM 已渲染, 再触发闪烁
      requestAnimationFrame(() => {
        setFlash(true)
        const t = setTimeout(() => setFlash(false), 2000)
        return () => clearTimeout(t)
      })
    }
  }, [highlight])

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-6 max-w-5xl">
      {/* ========== 左列 ========== */}
      <div className="space-y-6">
        {/* 行情状态 — 开关 + 间隔 */}
        <Card icon={Activity} title="行情輪詢">
          <ToggleRow
            label="即時行情"
            desc={
              isPaused ? '資料同步進行中,已暫時暫停'
              : isRunning && isTrading ? '執行中'
              : isRunning ? '執行中 (非交易時段)'
              : '已關閉'
            }
            checked={realtimeEnabled}
            onChange={handleToggleQuote}
            disabled={isPaused}
          />

          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex items-center justify-between gap-4 py-1">
              <div className="min-w-0">
                <div className="text-sm text-foreground">輪詢間隔</div>
                <div className="text-[11px] text-muted">
                  {isWatchlistMode ? '每輪拉取自選股即時行情的時間間隔' : '每輪拉取全市場行情的時間間隔'}
                </div>
              </div>
              <span className="text-[11px] font-mono text-foreground shrink-0 tabular-nums">
                {intervalDraft < 1 ? intervalDraft.toFixed(1) : intervalDraft.toFixed(0)}s
              </span>
            </div>
            <div className="flex items-center gap-3 mt-2">
              <input
                type="range"
                min={minInterval}
                max={maxInterval}
                step={minInterval < 1 ? 0.1 : minInterval < 3 ? 0.5 : 1}
                value={intervalDraft}
                onChange={(e) => setIntervalDraft(parseFloat(e.target.value))}
                className="flex-1 h-1 accent-accent cursor-pointer"
              />
              <span className="text-[10px] text-muted shrink-0">
                {intervalDraft !== interval ? '2秒後儲存' : `${minInterval}s — ${maxInterval}s`}
              </span>
            </div>
          </div>
        </Card>

        {isWatchlistMode && (
        <Card icon={Activity} title="自選股即時">
          <div className="mb-3 rounded-btn border border-accent/25 bg-accent/10 px-3 py-2 text-xs font-medium leading-snug text-accent">
            自選即時模式下自動監控「自選」頁面前 5 檔標的,最低 6 秒重新整理。
          </div>
          {watchlistSymbols.length > 0 ? (
            <div className="space-y-1.5">
              {watchlistSymbols.map(symbol => {
                const name = watchlistNameBySymbol.get(symbol)
                return (
                  <div key={symbol} className="flex items-center justify-between rounded-btn bg-base/50 border border-border px-2 py-1.5">
                    <div className="min-w-0 flex items-baseline gap-1.5">
                      <span className="text-xs font-mono text-foreground">{symbol}</span>
                      {name && <span className="truncate text-[11px] text-secondary">{name}</span>}
                    </div>
                    <span className="text-[10px] text-muted shrink-0">自選頁</span>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="rounded-btn border border-border bg-base/40 px-3 py-3 text-xs text-muted">
              自選清單為空,開啟自選即時前請先新增自選股。
            </div>
          )}
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="text-[10px] text-muted">目前 {watchlistSymbols.length}/5 檔</span>
            <Link
              to="/watchlist"
              className="px-3 py-1 rounded-btn bg-elevated text-secondary text-xs font-medium hover:text-foreground transition-colors"
            >
              管理自選
            </Link>
          </div>
        </Card>
        )}
        {!isWatchlistMode && (
        <Card icon={Wifi} title="頁面即時重新整理">
          <p className="text-xs text-secondary mb-4">
            選擇哪些頁面跟隨 SSE 即時重新整理資料。關閉的頁面不會被推送,
            但行情輪詢和策略監控不受影響。
          </p>
          <div className="space-y-2">
            {Object.entries(PAGE_LABELS).map(([key, label]) => (
              <ToggleRow
                key={key}
                label={label}
                desc={`SSE 推送時重新整理 ${label} 資料`}
                checked={refreshPages[key] !== false}
                onChange={(v) => save({ sse_refresh_pages: { ...refreshPages, [key]: v } })}
              />
            ))}
          </div>
        </Card>
        )}

        {/* 自选列表分时图实时刷新 (默认关闭, 开启后盘中按设定间隔轮询刷新分时数据) */}
        <Card icon={Activity} title="分時圖重新整理">
          <ToggleRow
            label="自選/策略分時圖即時重新整理"
            desc={`開啟後自選與策略清單的分時圖盤中每 ${intradayInterval} 秒自動重新整理（依賴分鐘K批次資料 + 即時行情執行）。關閉時僅開啟頁面時拉取一次,可點表頭重新整理按鈕手動更新。`}
            checked={prefs?.minute_intraday_refresh ?? false}
            onChange={(v) => save({ minute_intraday_refresh: v })}
          />
          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex items-center justify-between gap-4 py-1">
              <div className="min-w-0">
                <div className="text-sm text-foreground">重新整理間隔</div>
                <div className="text-[11px] text-muted">
                  間隔越短更新越即時,但越耗資料來源配額 (rpm)
                </div>
              </div>
              <span className="text-[11px] font-mono text-foreground shrink-0 tabular-nums">
                {intradayIntervalDraft}s
              </span>
            </div>
            <div className="flex items-center gap-3 mt-2">
              <input
                type="range"
                min={3}
                max={60}
                step={1}
                value={intradayIntervalDraft}
                onChange={(e) => setIntradayIntervalDraft(parseInt(e.target.value, 10))}
                className="flex-1 h-1 accent-accent cursor-pointer"
              />
              <span className="text-[10px] text-muted shrink-0">
                {intradayIntervalDraft !== intradayInterval ? '2秒後儲存' : '3s — 60s'}
              </span>
            </div>
          </div>
        </Card>

        {!isWatchlistMode && (
        <Card icon={BarChart3} title="左側選單指數">
          <p className="text-xs text-secondary mb-4">
            選擇即時行情開啟時,左側選單底部顯示哪些指數點位和漲跌幅。
          </p>
          <div className="space-y-2">
            {SIDEBAR_INDEX_OPTIONS.map(item => (
              <ToggleRow
                key={item.symbol}
                label={item.name}
                desc={item.symbol}
                checked={sidebarIndexSymbols.includes(item.symbol)}
                onChange={(v) => toggleSidebarIndex(item.symbol, v)}
              />
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-border">
            <ToggleRow
              label="固定顯示"
              desc={indicesPinned ? '指數卡片常駐顯示（即使即時行情關閉）' : '跟隨即時行情開關（僅即時開啟時顯示）'}
              checked={indicesPinned}
              onChange={toggleIndicesPin}
            />
          </div>
        </Card>
        )}
      </div>

      {/* ========== 右列 ========== */}
      <div className="space-y-6">
        {/* 连板梯队降级修正 (移至右列顶部) */}
        <div
          id="depth-fix"
          className={`rounded-card transition-all duration-500 ${flash ? 'ring-2 ring-accent/60 ring-offset-2 ring-offset-base scale-[1.01]' : 'ring-0 ring-transparent'}`}
        >
        <Card
          icon={Flame}
          title="連板梯隊降級修正"
          badge={!hasDepth ? '五檔盤口不可用' : undefined}
          right={hasDepth ? (
            <button
              onClick={() => runFix.mutate()}
              disabled={runFix.isPending}
              className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px]
                         bg-accent/15 text-accent hover:bg-accent/25 transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Zap className="h-3 w-3" />
              {runFix.isPending ? '修正中…' : '立即修正'}
            </button>
          ) : undefined}
        >
          {hasDepth ? (
            <>
              <p className="text-xs text-secondary mb-4">
                透過五檔盤口即時修正真假漲停/跌停。真封板顯示封單量,假漲停(收盤價=漲停價但賣一有量)歸入炸板。
                盤中依設定間隔輪詢,收盤後自動定版。
              </p>
              <ToggleRow
                label="啟用真假板修正"
                desc="開啟後盤中自動拉取五檔盤口修正真假板"
                checked={limitLadderMonitor}
                onChange={toggleLimitLadderMonitor}
              />
              <div className="mt-4 pt-3 border-t border-border">
                <div className="text-[10px] uppercase tracking-widest text-muted mb-3">
                  五檔盤口設定
                </div>
                <DepthConfigContent disabled={!limitLadderMonitor} />
              </div>
            </>
          ) : (
            <DepthConfigContent disabled />
          )}
        </Card>
        </div>

        {/* 推送通知 — 监控告警的外部推送渠道 (全局配置)。
            飞书 / 企业微信。
            每个渠道合并成一行: 勾选=新建规则默认推送, 点行展开地址配置。 */}
        <Card icon={Webhook} title="推播通知">
          <p className="text-xs text-secondary mb-3">
            監控規則命中後,可把告警推送到外部。勾選管道作為<b className="text-foreground/80">新建規則的預設推播</b>,
            單條規則仍可在編輯頁獨立修改。
          </p>

          {/* 渠道列表 — 每行一个渠道, 勾选默认 + 点行展开地址配置 */}
          <div className="space-y-2">
            {/* 飞书 (可用): 勾选默认 + 展开地址配置 */}
            <div className="rounded-btn border border-border/60 bg-base/40 overflow-hidden">
              <div
                onClick={() => setChannelOpen(o => !o)}
                className="flex items-center gap-2 px-2.5 py-2 cursor-pointer transition-colors hover:bg-base/60"
              >
                <input
                  type="checkbox"
                  checked={webhookDefaultChannels.includes('feishu')}
                  onChange={e => { e.stopPropagation(); toggleDefaultChannel('feishu', e.target.checked) }}
                  onClick={e => e.stopPropagation()}
                  title="作為新建規則的預設推播管道"
                  className="h-3 w-3 accent-accent cursor-pointer"
                />
                <span className="text-[11px] font-medium text-foreground">飛書</span>
                <span className="text-[9px] text-muted">群推播 Webhook</span>
                {webhookDefaultChannels.includes('feishu') && (
                  <span className="rounded bg-accent/15 px-1 py-px text-[9px] text-accent">預設</span>
                )}
                <span className={`ml-auto text-[9px] ${feishuWebhookUrl ? 'text-emerald-500' : 'text-warning'}`}>
                  {feishuWebhookUrl ? '已設定' : '未設定'}
                </span>
                <ChevronDown className={`h-3 w-3 text-muted transition-transform ${channelOpen ? 'rotate-180' : ''}`} />
              </div>

              {/* 飞书地址配置 — 行内展开 */}
              {channelOpen && (
                <div className="border-t border-border/60 bg-base/30 p-3">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] text-muted">Webhook 位址</span>
                    <input
                      value={feishuDraft}
                      onChange={e => setFeishuDraft(e.target.value)}
                      placeholder={FEISHU_PREFIX + 'xxxxxxxx'}
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>

                  <label className="block mt-2 space-y-1.5">
                    <span className="text-[11px] text-muted">簽章金鑰 (可選 · 啟用簽章驗證時填)</span>
                    <input
                      type="password"
                      value={feishuSecretDraft}
                      onChange={e => setFeishuSecretDraft(e.target.value)}
                      placeholder="機器人未啟用簽章驗證則留空"
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>

                  {feishuError && (
                    <div className="mt-2 text-[11px] text-danger">{feishuError}</div>
                  )}

                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={submitFeishu}
                      disabled={saveFeishuWebhook.isPending || (feishuDraft.trim() === feishuWebhookUrl && feishuSecretDraft.trim() === feishuWebhookSecret)}
                      className="px-3 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer hover:bg-accent/90 transition-colors"
                    >
                      {saveFeishuWebhook.isPending ? '儲存中…' : '儲存'}
                    </button>
                    {feishuWebhookUrl && (
                      <span className="text-[10px] text-emerald-500">● 已設定</span>
                    )}
                  </div>

                  <details className="mt-3 text-[10px] text-muted">
                    <summary className="cursor-pointer hover:text-secondary">如何取得飛書 Webhook 位址?</summary>
                    <ol className="mt-1.5 space-y-1 pl-4 list-decimal leading-relaxed">
                      <li>開啟飛書,進入目標群聊 → 群設定 → <b>群推播 Webhook</b></li>
                      <li>點擊「新增機器人」→ 選擇「<b>自訂機器人</b>」</li>
                      <li>填寫機器人名稱後新增,複製產生的 Webhook 位址</li>
                      <li>安全設定若啟用了「<b>簽章驗證</b>」,把金鑰一併複製填到「簽章金鑰」框</li>
                      <li>貼上到上方輸入框並儲存</li>
                    </ol>
                    <p className="mt-1.5 pl-4 text-muted/70">
                      📖 官方文档:
                      <a href="https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN" target="_blank" rel="noreferrer" className="text-accent hover:text-accent/80">
                        自訂機器人使用指南 ↗
                      </a>
                    </p>
                  </details>
                </div>
              )}
            </div>

            {/* 企业微信群推送 Webhook (可用): 与飞书并列, 勾选默认 + 展开地址配置 */}
            <div className="rounded-btn border border-border/60 bg-base/40 overflow-hidden">
              <div
                onClick={() => setWecomOpen(o => !o)}
                className="flex items-center gap-2 px-2.5 py-2 cursor-pointer transition-colors hover:bg-base/60"
              >
                <input
                  type="checkbox"
                  checked={webhookDefaultChannels.includes('wecom')}
                  onChange={e => { e.stopPropagation(); toggleDefaultChannel('wecom', e.target.checked) }}
                  onClick={e => e.stopPropagation()}
                  title="作為新建規則的預設推播管道"
                  className="h-3 w-3 accent-accent cursor-pointer"
                />
                <span className="text-[11px] font-medium text-foreground">企業微信</span>
                <span className="text-[9px] text-muted">群推播 Webhook</span>
                {webhookDefaultChannels.includes('wecom') && (
                  <span className="rounded bg-accent/15 px-1 py-px text-[9px] text-accent">預設</span>
                )}
                <span className={`ml-auto text-[9px] ${wecomWebhookUrl ? 'text-emerald-500' : 'text-warning'}`}>
                  {wecomWebhookUrl ? '已設定' : '未設定'}
                </span>
                <ChevronDown className={`h-3 w-3 text-muted transition-transform ${wecomOpen ? 'rotate-180' : ''}`} />
              </div>

              {wecomOpen && (
                <div className="border-t border-border/60 bg-base/30 p-3">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] text-muted">Webhook 位址 或 Key</span>
                    <input
                      value={wecomDraft}
                      onChange={e => setWecomDraft(e.target.value)}
                      placeholder={WECOM_PREFIX + '?key=xxxxxxxx' + ' 或直接填 key'}
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>

                  {wecomError && (
                    <div className="mt-2 text-[11px] text-danger">{wecomError}</div>
                  )}

                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={submitWecom}
                      disabled={saveWecomWebhook.isPending || wecomDraft.trim() === wecomWebhookUrl}
                      className="px-3 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer hover:bg-accent/90 transition-colors"
                    >
                      {saveWecomWebhook.isPending ? '儲存中…' : '儲存'}
                    </button>
                    {wecomWebhookUrl && (
                      <span className="text-[10px] text-emerald-500">● 已設定</span>
                    )}
                  </div>

                  <details className="mt-3 text-[10px] text-muted">
                    <summary className="cursor-pointer hover:text-secondary">如何取得企業微信 Webhook 位址?</summary>
                    <ol className="mt-1.5 space-y-1 pl-4 list-decimal leading-relaxed">
                      <li>開啟企業微信,進入目標群聊 → 右上角「...」→ <b>群推播 Webhook</b></li>
                      <li>點擊「新增」→ 選擇「<b>訊息推播</b>」→ 填寫名稱</li>
                      <li>複製產生的 <b>Webhook 位址</b>(含 key 參數),貼上到上方輸入框</li>
                      <li>也可只複製 key 參數部分(= 後面的內容)填入</li>
                      <li>企業微信群的訊息可同步到綁定的個人微信,實現「微信推播」</li>
                    </ol>
                    <p className="mt-1.5 pl-4 text-muted/70">
                      📖 官方文档:
                      <a href="https://developer.work.weixin.qq.com/document/path/91770" target="_blank" rel="noreferrer" className="text-accent hover:text-accent/80">
                        群推播 Webhook 使用指南 ↗
                      </a>
                    </p>
                  </details>
                </div>
              )}
            </div>

            {/* 企业微信智能机器人 (BotID + Secret): 长连接通道, 与群推送 Webhook 并列 */}
            <div className="rounded-btn border border-border/60 bg-base/40 overflow-hidden">
              <div
                onClick={() => setBotOpen(o => !o)}
                className="flex items-center gap-2 px-2.5 py-2 cursor-pointer transition-colors hover:bg-base/60"
              >
                <input
                  type="checkbox"
                  checked={wecomBotEnabled}
                  onChange={e => { e.stopPropagation(); toggleBotConnection.mutate(e.target.checked) }}
                  onClick={e => e.stopPropagation()}
                  disabled={!wecomBotId || toggleBotConnection.isPending}
                  title="開啟後建立長連線保活,關閉則斷開"
                  className="h-3 w-3 accent-accent cursor-pointer disabled:opacity-40"
                />
                <span className="text-[11px] font-medium text-foreground">企業微信</span>
                <span className="text-[9px] text-muted">智慧型機器人</span>
                <span className={`ml-auto text-[9px] ${wecomBotId ? (botStatus?.connected ? 'text-emerald-500' : 'text-warning') : 'text-muted'}`}>
                  {wecomBotId ? (botStatus?.connected ? '已連線' : (wecomBotEnabled ? '連線中' : '已設定')) : '未設定'}
                </span>
                <ChevronDown className={`h-3 w-3 text-muted transition-transform ${botOpen ? 'rotate-180' : ''}`} />
              </div>

              {botOpen && (
                <div className="border-t border-border/60 bg-base/30 p-3">
                  <p className="mb-2.5 text-[10px] text-muted leading-relaxed">
                    勾選卡片左側開關可啟用長連線保活(開啟後後端持續保持與企業微信的
                    WebSocket 連線)。儲存憑證後需勾選才會連線,取消勾選則立即斷開。
                  </p>
                  <label className="block space-y-1.5">
                    <span className="text-[11px] text-muted">BotID</span>
                    <input
                      value={botIdDraft}
                      onChange={e => setBotIdDraft(e.target.value)}
                      placeholder="智慧型機器人的唯一識別碼"
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>

                  <label className="block mt-2 space-y-1.5">
                    <span className="text-[11px] text-muted">Secret (長連線專用金鑰)</span>
                    <input
                      type="password"
                      value={botSecretDraft}
                      onChange={e => setBotSecretDraft(e.target.value)}
                      placeholder="開啟長連線 API 模式後取得的金鑰"
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>

                  {botError && (
                    <div className="mt-2 text-[11px] text-danger">{botError}</div>
                  )}

                  {botStatus?.last_error && !botError && (
                    <div className="mt-2 text-[11px] text-warning">連線異常: {botStatus.last_error}</div>
                  )}

                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={submitBot}
                      disabled={saveWecomBot.isPending || (botIdDraft.trim() === wecomBotId && botSecretDraft.trim() === wecomBotSecret)}
                      className="px-3 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer hover:bg-accent/90 transition-colors"
                    >
                      {saveWecomBot.isPending ? '儲存中…' : '儲存並連線'}
                    </button>
                    {wecomBotId && (
                      <span className="text-[10px] text-emerald-500">● 已設定</span>
                    )}
                  </div>

                  <details className="mt-3 text-[10px] text-muted">
                    <summary className="cursor-pointer hover:text-secondary">如何取得 BotID 和 Secret?</summary>
                    <ol className="mt-1.5 space-y-1 pl-4 list-decimal leading-relaxed">
                      <li>登入<b>企業微信管理後台</b> → 應用程式管理 → <b>智慧型機器人</b> → 建立機器人</li>
                      <li>填寫名稱、頭像後進入機器人設定頁</li>
                      <li>開啟「<b>API 模式</b>」→ 選擇「<b>長連線</b>」方式(另一項「回呼URL」需公網IP)</li>
                      <li>頁面顯示 <b>BotID</b> 和 <b>Secret</b>,複製填到上方輸入框</li>
                    </ol>
                    <p className="mt-1.5 pl-4 text-muted/70">
                      💡 智慧型機器人支援 @互動和串流回覆,與群推播 Webhook(單向推播)互補。
                      儲存後後端會自動建立 WebSocket 長連線保活。
                    </p>
                    <p className="mt-1.5 pl-4 text-muted/70">
                      📖 官方文档:
                      <a href="https://developer.work.weixin.qq.com/document/path/101463" target="_blank" rel="noreferrer" className="text-accent hover:text-accent/80">
                        智慧型機器人長連線 ↗
                      </a>
                    </p>
                  </details>
                </div>
              )}
            </div>

          </div>
        </Card>
      </div>
    </div>
  )
}


// ===== ToggleRow =====

function ToggleRow({
  label,
  desc,
  checked,
  onChange,
  icon: Icon,
  disabled,
}: {
  label: string
  desc: string
  checked: boolean
  onChange: (v: boolean) => void
  icon?: React.ComponentType<{ className?: string }>
  disabled?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0 flex items-start gap-2">
        {Icon && <Icon className="h-3.5 w-3.5 text-secondary shrink-0 mt-0.5" />}
        <div className="min-w-0">
          <div className="text-sm text-foreground">{label}</div>
          <div className="text-[11px] text-muted truncate">{desc}</div>
        </div>
      </div>
      <button
        onClick={() => !disabled && onChange(!checked)}
        disabled={disabled}
        className={`relative inline-flex h-5 w-9 items-center rounded-full shrink-0 transition-colors duration-200 ${
          checked ? 'bg-accent' : 'bg-elevated'
        } ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
            checked ? 'translate-x-[18px]' : 'translate-x-[3px]'
          }`}
        />
      </button>
    </div>
  )
}


// ===== 通用卡片 =====

interface CardProps {
  icon: React.ComponentType<{ className?: string }>
  title: string
  badge?: string
  right?: React.ReactNode
  children: React.ReactNode
}

function Card({ icon: Icon, title, badge, right, children }: CardProps) {
  return (
    <section className="rounded-card border border-border bg-surface p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <Icon className="h-4 w-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground">{title}</h2>
          {badge && (
            <span className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-elevated text-muted">
              {badge}
            </span>
          )}
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}
