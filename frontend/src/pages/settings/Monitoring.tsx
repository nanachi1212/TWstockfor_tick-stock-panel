import { useState, useCallback, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient, useMutation, useQuery } from '@tanstack/react-query'
import {
  Activity,
  Wifi,
  Webhook,
  ChevronDown,
  BookOpen,
  ExternalLink,
} from 'lucide-react'
import {
  usePreferences,
  useQuoteStatus,
  useQuoteInterval,
} from '@/lib/useSharedQueries'
import { useUpdateQuoteInterval, useToggleRealtimeQuotes } from '@/lib/useSharedMutations'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'

// 頁面 → 顯示名
const PAGE_LABELS: Record<string, string> = {
  'overview-market': '看板',
  watchlist: '自選頁',
}

// ===== 導出為 Panel 組件 (由 Settings.tsx 嵌入) =====

export function SettingsMonitoringPanel(_props: { highlight?: string } = {}) {
  const qc = useQueryClient()
  const { data: prefs } = usePreferences()
  const { data: quoteStatus } = useQuoteStatus()
  const { data: intervalData } = useQuoteInterval()
  const updateInterval = useUpdateQuoteInterval()
  const toggleQuote = useToggleRealtimeQuotes()
  // 實時模式以 quote_status 為準 (數據源無關): watchlist=自選實時 / full_market=全市場 / none=不可用
  const quoteMode = quoteStatus?.mode ?? 'none'
  const isWatchlistMode = quoteMode === 'watchlist'
  const realtimeEnabled = prefs?.realtime_quotes_enabled ?? false
  // 分時圖實時刷新間隔 (秒), 與後端 [3,60] clamp 對齊; 默認 6
  const intradayInterval = prefs?.minute_intraday_refresh_interval ?? 6
  // 滑塊本地草稿: 拖動時即時反饋, 停頓 2s 後落庫 (與行情輪詢滑塊一致)
  const [intradayIntervalDraft, setIntradayIntervalDraft] = useState(intradayInterval)
  const refreshPages = prefs?.sse_refresh_pages ?? {}
  // 新建監控規則時默認勾選的推送渠道 (全局默認值數組, 單條規則可獨立修改)
  const webhookDefaultChannels = prefs?.webhook_default_channels ?? []
  const isRunning = quoteStatus?.running ?? false
  const isTrading = quoteStatus?.is_trading_hours ?? false
  // 管道/數據修正運行期間實時行情被臨時暫停 — 此時禁止開啟
  const isPaused = quoteStatus?.paused ?? false
  const interval = intervalData?.interval ?? 6
  const minInterval = intervalData?.min_interval ?? 6
  const maxInterval = intervalData?.max_interval ?? 60
  const [intervalDraft, setIntervalDraft] = useState(interval)
  const lineTargetId = prefs?.line_target_id ?? ''
  const lineTokenMasked = prefs?.line_channel_access_token_masked ?? ''
  const lineConfigured = prefs?.line_configured ?? false
  const telegramChatId = prefs?.telegram_chat_id ?? ''
  const telegramTokenMasked = prefs?.telegram_bot_token_masked ?? ''
  const telegramConfigured = prefs?.telegram_configured ?? false
  const [lineTargetDraft, setLineTargetDraft] = useState(lineTargetId)
  const [lineTokenDraft, setLineTokenDraft] = useState('')
  const [telegramChatDraft, setTelegramChatDraft] = useState(telegramChatId)
  const [telegramTokenDraft, setTelegramTokenDraft] = useState('')
  const [lineOpen, setLineOpen] = useState(false)
  const [telegramOpen, setTelegramOpen] = useState(false)
  useEffect(() => {
    setLineTargetDraft(lineTargetId)
  }, [lineTargetId])
  useEffect(() => {
    setTelegramChatDraft(telegramChatId)
  }, [telegramChatId])
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
      // 忽略 — Toast 已在 request 層處理
    }
  }, [qc])

  const handleToggleQuote = useCallback(async (enabled: boolean) => {
    await toggleQuote.mutateAsync(enabled)
    qc.invalidateQueries({ queryKey: QK.preferences })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }, [toggleQuote, qc])

  // 勾選/取消勾選某個預設推播渠道 (LINE / Telegram 各自獨立)
  const toggleDefaultChannel = useCallback(async (ch: string, enabled: boolean) => {
    const cur = prefs?.webhook_default_channels ?? []
    const next = enabled ? [...cur, ch] : cur.filter(c => c !== ch)
    await api.updateWebhookDefaultChannels(next)
    qc.invalidateQueries({ queryKey: QK.preferences })
  }, [qc, prefs])

  const saveLine = useMutation({
    mutationFn: ({ recipient, token, clearToken = false }: { recipient: string; token?: string; clearToken?: boolean }) =>
      api.updateLineMessaging(recipient, token, clearToken),
    onSuccess: () => {
      setLineTokenDraft('')
      toast('LINE Messaging API 設定已儲存', 'success')
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
  })
  const saveTelegram = useMutation({
    mutationFn: ({ recipient, token, clearToken = false }: { recipient: string; token?: string; clearToken?: boolean }) =>
      api.updateTelegramBot(recipient, token, clearToken),
    onSuccess: () => {
      setTelegramTokenDraft('')
      toast('Telegram Bot API 設定已儲存', 'success')
      qc.invalidateQueries({ queryKey: QK.preferences })
    },
  })
  const testLine = useMutation({
    mutationFn: api.testLineMessaging,
    onSuccess: ({ ok }) => toast(ok ? 'LINE 測試通知已送出' : 'LINE 測試通知失敗', ok ? 'success' : 'error'),
  })
  const testTelegram = useMutation({
    mutationFn: api.testTelegramBot,
    onSuccess: ({ ok }) => toast(ok ? 'Telegram 測試通知已送出' : 'Telegram 測試通知失敗', ok ? 'success' : 'error'),
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

  // 分時刷新間隔: 服務端值變化時同步本地草稿
  useEffect(() => {
    setIntradayIntervalDraft(intradayInterval)
  }, [intradayInterval])

  // 分時刷新間隔: 草稿與已保存值不同時, 2s 防抖落庫
  useEffect(() => {
    if (intradayIntervalDraft === intradayInterval) return
    const t = window.setTimeout(() => {
      save({ minute_intraday_refresh_interval: intradayIntervalDraft })
    }, 2000)
    return () => window.clearTimeout(t)
  }, [intradayIntervalDraft, intradayInterval, save])

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-6 max-w-5xl">
      {/* ========== 左列 ========== */}
      <div className="space-y-6">
        {/* 行情狀態 — 開關 + 間隔 */}
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

        {/* 自選列表分時圖實時刷新 (默認關閉, 開啟後盤中按設定間隔輪詢刷新分時數據) */}
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

      </div>

      {/* ========== 右列 ========== */}
      <div className="space-y-6">
        {/* 外部推播渠道；告警落盤與 SSE 流程不依賴這些 API。 */}
        <Card icon={Webhook} title="推播通知">
          <p className="text-xs text-secondary mb-3">
            監控規則命中後,可把告警推送到外部。勾選管道作為<b className="text-foreground/80">新建規則的預設推播</b>,
            單條規則仍可在編輯頁獨立修改。
          </p>

          <div className="space-y-2">
            <div className="rounded-btn border border-border/60 bg-base/40 overflow-hidden">
              <div
                onClick={() => setLineOpen(open => !open)}
                className="flex items-center gap-2 px-2.5 py-2 cursor-pointer transition-colors hover:bg-base/60"
              >
                <input
                  type="checkbox"
                  checked={webhookDefaultChannels.includes('line')}
                  onChange={event => { event.stopPropagation(); toggleDefaultChannel('line', event.target.checked) }}
                  onClick={event => event.stopPropagation()}
                  title="作為新建規則的預設推播管道"
                  className="h-3 w-3 accent-accent cursor-pointer"
                />
                <span className="text-[11px] font-medium text-foreground">LINE</span>
                <span className="text-[9px] text-muted">Messaging API</span>
                {webhookDefaultChannels.includes('line') && (
                  <span className="rounded bg-accent/15 px-1 py-px text-[9px] text-accent">預設</span>
                )}
                <span className={`ml-auto text-[9px] ${lineConfigured ? 'text-emerald-500' : 'text-warning'}`}>
                  {lineConfigured ? '已設定' : '未設定'}
                </span>
                <ChevronDown className={`h-3 w-3 text-muted transition-transform ${lineOpen ? 'rotate-180' : ''}`} />
              </div>

              {lineOpen && (
                <div className="border-t border-border/60 bg-base/30 p-3">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] text-muted">Target ID（開發者 User ID / 群組 ID，非一般 LINE ID）</span>
                    <input
                      value={lineTargetDraft}
                      onChange={event => setLineTargetDraft(event.target.value)}
                      placeholder="U… (個人) / C… (群組)"
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>
                  <label className="block mt-2 space-y-1.5">
                    <span className="text-[11px] text-muted">Channel access token</span>
                    <input
                      type="password"
                      value={lineTokenDraft}
                      onChange={event => setLineTokenDraft(event.target.value)}
                      placeholder={lineTokenMasked || '貼上 Channel access token'}
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>
                  <p className="mt-1.5 text-[10px] text-muted">Token 留空會保留目前已儲存的值。</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => saveLine.mutate({
                        recipient: lineTargetDraft.trim(),
                        token: lineTokenDraft.trim() || undefined,
                      })}
                      disabled={saveLine.isPending || (lineTargetDraft.trim() === lineTargetId && !lineTokenDraft.trim())}
                      className="px-3 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer hover:bg-accent/90 transition-colors"
                    >
                      {saveLine.isPending ? '儲存中…' : '儲存'}
                    </button>
                    <button
                      onClick={() => testLine.mutate()}
                      disabled={!lineConfigured || testLine.isPending}
                      className="px-3 py-1.5 rounded-btn bg-elevated text-xs text-secondary disabled:opacity-50"
                    >
                      {testLine.isPending ? '測試中…' : '測試通知'}
                    </button>
                    {lineConfigured && (
                      <button
                        onClick={() => saveLine.mutate({ recipient: '', clearToken: true })}
                        disabled={saveLine.isPending}
                        className="ml-auto px-2 py-1 text-[10px] text-danger disabled:opacity-50"
                      >
                        清除設定
                      </button>
                    )}
                  </div>

                  {/* LINE 串接教學 (可展開折疊區塊，預設收合) */}
                  <details className="mt-3 rounded-btn border border-border/60 bg-base/40 p-2.5 group">
                    <summary className="cursor-pointer text-xs font-medium text-secondary hover:text-foreground flex items-center justify-between select-none">
                      <span className="flex items-center gap-1.5">
                        <BookOpen className="h-3.5 w-3.5 text-accent" />
                        LINE Messaging API 串接教學
                      </span>
                      <ChevronDown className="h-3 w-3 text-muted transition-transform group-open:rotate-180" />
                    </summary>
                    <div className="mt-2.5 pt-2.5 border-t border-border/50 text-[11px] text-secondary space-y-2 leading-relaxed">
                      <ol className="list-decimal list-inside space-y-2 pl-0.5 text-foreground/90">
                        <li><b>建立 LINE Official Account</b>：前往 LINE Official Account 平台註冊並建立專屬官方帳號。</li>
                        <li><b>啟用 Messaging API</b>：登入後台管理頁面，在「設定 &gt; Messaging API」分頁點選啟用。</li>
                        <li><b>取得 Channel Access Token</b>：登入 LINE Developers Console，在頻道「Messaging API」頁籤發行並複製長期 Channel Access Token。</li>
                        <li>
                          <b>取得 Target ID（接收對象識別碼）</b>：
                          <div className="mt-1 space-y-1 pl-1 text-[10.5px] text-secondary">
                            <p>
                              <b>⚠️ 重要觀念</b>：Target ID <b>不等於一般 LINE ID</b>（不能填寫個人自訂的好友搜尋帳號或行動條碼）。
                            </p>
                            <p>
                              • <b>自己的開發者 User ID</b>：登入 LINE Developers Console，進入所屬頻道之「<b>Basic settings</b>」頁籤，滑至最下方複製「Your user ID」（以 <code className="px-1 rounded bg-elevated text-accent font-mono">U</code> 開頭的 33 碼字串）。
                            </p>
                            <p>
                              • <b>其他使用者或群組 / 聊天室 ID</b>：其他人的 User ID（<code className="px-1 rounded bg-elevated font-mono">U…</code>）或群組 ID（<code className="px-1 rounded bg-elevated font-mono">C…</code>）通常需透過伺服器設定 <b>webhook event</b>（如好友訊息或加入群組事件）動態取得。
                            </p>
                          </div>
                        </li>
                        <li><b>填入設定</b>：回到本系統填入 Channel Access Token 與 Target ID。</li>
                        <li><b>儲存並驗證</b>：點擊「儲存」按鈕後，按「測試通知」確認能否正常收到測試推播。</li>
                        <li><span className="text-warning font-medium">⚠️ Channel Access Token 視同密碼，請妥善保管，切勿分享給他人。</span></li>
                      </ol>
                      <div className="pt-1 flex flex-wrap gap-3 text-[11px]">
                        <a
                          href="https://developers.line.biz/en/docs/messaging-api/getting-started/"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline inline-flex items-center gap-1"
                        >
                          <ExternalLink className="h-3 w-3" />
                          LINE 官方 Messaging API 起步教學
                        </a>
                        <a
                          href="https://developers.line.biz/en/docs/basics/channel-access-token/"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline inline-flex items-center gap-1"
                        >
                          <ExternalLink className="h-3 w-3" />
                          Channel Access Token 官方文件
                        </a>
                      </div>
                    </div>
                  </details>
                </div>
              )}
            </div>

            <div className="rounded-btn border border-border/60 bg-base/40 overflow-hidden">
              <div
                onClick={() => setTelegramOpen(open => !open)}
                className="flex items-center gap-2 px-2.5 py-2 cursor-pointer transition-colors hover:bg-base/60"
              >
                <input
                  type="checkbox"
                  checked={webhookDefaultChannels.includes('telegram')}
                  onChange={event => { event.stopPropagation(); toggleDefaultChannel('telegram', event.target.checked) }}
                  onClick={event => event.stopPropagation()}
                  title="作為新建規則的預設推播管道"
                  className="h-3 w-3 accent-accent cursor-pointer"
                />
                <span className="text-[11px] font-medium text-foreground">Telegram</span>
                <span className="text-[9px] text-muted">Bot API</span>
                {webhookDefaultChannels.includes('telegram') && (
                  <span className="rounded bg-accent/15 px-1 py-px text-[9px] text-accent">預設</span>
                )}
                <span className={`ml-auto text-[9px] ${telegramConfigured ? 'text-emerald-500' : 'text-warning'}`}>
                  {telegramConfigured ? '已設定' : '未設定'}
                </span>
                <ChevronDown className={`h-3 w-3 text-muted transition-transform ${telegramOpen ? 'rotate-180' : ''}`} />
              </div>

              {telegramOpen && (
                <div className="border-t border-border/60 bg-base/30 p-3">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] text-muted">Chat ID</span>
                    <input
                      value={telegramChatDraft}
                      onChange={event => setTelegramChatDraft(event.target.value)}
                      placeholder="例如 -1001234567890"
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>
                  <label className="block mt-2 space-y-1.5">
                    <span className="text-[11px] text-muted">Bot token</span>
                    <input
                      type="password"
                      value={telegramTokenDraft}
                      onChange={event => setTelegramTokenDraft(event.target.value)}
                      placeholder={telegramTokenMasked || '貼上 Bot token'}
                      className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50"
                    />
                  </label>
                  <p className="mt-1.5 text-[10px] text-muted">Token 留空會保留目前已儲存的值。</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => saveTelegram.mutate({
                        recipient: telegramChatDraft.trim(),
                        token: telegramTokenDraft.trim() || undefined,
                      })}
                      disabled={saveTelegram.isPending || (telegramChatDraft.trim() === telegramChatId && !telegramTokenDraft.trim())}
                      className="px-3 py-1.5 rounded-btn bg-accent text-base text-xs font-medium disabled:opacity-50 cursor-pointer hover:bg-accent/90 transition-colors"
                    >
                      {saveTelegram.isPending ? '儲存中…' : '儲存'}
                    </button>
                    <button
                      onClick={() => testTelegram.mutate()}
                      disabled={!telegramConfigured || testTelegram.isPending}
                      className="px-3 py-1.5 rounded-btn bg-elevated text-xs text-secondary disabled:opacity-50"
                    >
                      {testTelegram.isPending ? '測試中…' : '測試通知'}
                    </button>
                    {telegramConfigured && (
                      <button
                        onClick={() => saveTelegram.mutate({ recipient: '', clearToken: true })}
                        disabled={saveTelegram.isPending}
                        className="ml-auto px-2 py-1 text-[10px] text-danger disabled:opacity-50"
                      >
                        清除設定
                      </button>
                    )}
                  </div>

                  {/* Telegram 串接教學 (可展開折疊區塊，預設收合) */}
                  <details className="mt-3 rounded-btn border border-border/60 bg-base/40 p-2.5 group">
                    <summary className="cursor-pointer text-xs font-medium text-secondary hover:text-foreground flex items-center justify-between select-none">
                      <span className="flex items-center gap-1.5">
                        <BookOpen className="h-3.5 w-3.5 text-accent" />
                        Telegram Bot 串接教學
                      </span>
                      <ChevronDown className="h-3 w-3 text-muted transition-transform group-open:rotate-180" />
                    </summary>
                    <div className="mt-2.5 pt-2.5 border-t border-border/50 text-[11px] text-secondary space-y-2 leading-relaxed">
                      <ol className="list-decimal list-inside space-y-2 pl-0.5 text-foreground/90">
                        <li><b>在 Telegram 尋找 @BotFather</b>：開啟 Telegram 搜尋官方機器人管理員 <code className="px-1 rounded bg-elevated text-accent font-mono">@BotFather</code> 並開啟對話。</li>
                        <li><b>使用 /newbot 建立 Bot</b>：向 BotFather 發送 <code className="px-1 rounded bg-elevated text-foreground font-mono">/newbot</code>，依引導設定 Bot 名稱與 username（結尾須為 bot）。</li>
                        <li><b>取得 Bot Token</b>：建立完成後，BotFather 會回傳專屬 API Token（例如 <code className="px-1 rounded bg-elevated text-muted font-mono">123456:ABC-DEF...</code>）。</li>
                        <li><b>傳訊息給剛建立的 Bot</b>：在 Telegram 搜尋剛建立的 Bot，點擊「Start」並傳送任意一則文字訊息（Bot 必須先收到訊息才能建立通訊通道）。</li>
                        <li>
                          <b>呼叫 getUpdates 讀取 message.chat.id（官方方式）</b>：
                          <div className="mt-1 space-y-1 pl-1 text-[10.5px] text-secondary">
                            <p>
                              在瀏覽器中開啟官方 API 網址（將 <code className="px-1 rounded bg-elevated font-mono">&lt;YourBotToken&gt;</code> 替換為您的 Bot Token）：
                            </p>
                            <p className="font-mono text-xs text-accent break-all select-all">
                              https://api.telegram.org/bot&lt;YourBotToken&gt;/getUpdates
                            </p>
                            <p>
                              在回傳的 JSON 內容中，展開 <code className="px-1 rounded bg-elevated font-mono">result</code> 找到 <code className="px-1 rounded bg-elevated font-mono">message.chat.id</code>（個人聊天為正整數，群組通常為以 <code className="px-1 rounded bg-elevated font-mono">-</code> 開頭的負整數），該數值即為 Chat ID。
                            </p>
                          </div>
                        </li>
                        <li><b>填入設定</b>：回到本系統填入 Chat ID 與 Bot Token。</li>
                        <li><b>儲存並驗證</b>：點擊「儲存」按鈕後，按「測試通知」確認能否正常收到測試推播。</li>
                        <li><span className="text-warning font-medium">⚠️ Bot Token 視同密碼，請妥善保管，切勿分享給他人。</span></li>
                      </ol>
                      <div className="pt-1 flex flex-wrap gap-3 text-[11px]">
                        <a
                          href="https://core.telegram.org/bots/tutorial"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline inline-flex items-center gap-1"
                        >
                          <ExternalLink className="h-3 w-3" />
                          Telegram Bots 官方起步教程
                        </a>
                        <a
                          href="https://core.telegram.org/bots/api"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline inline-flex items-center gap-1"
                        >
                          <ExternalLink className="h-3 w-3" />
                          Telegram Bot API 官方文件
                        </a>
                      </div>
                    </div>
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
