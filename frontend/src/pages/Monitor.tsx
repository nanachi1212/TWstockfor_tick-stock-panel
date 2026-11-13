import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus, ListChecks, BellRing } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { api, type TaiwanMonitorRule, type TaiwanRealtimeQuote } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { markSeen, leaveMonitorPage } from '@/lib/monitorBadge'
import { TaiwanQuotePanel } from '@/components/monitor/TaiwanQuotePanel'
import { TaiwanRuleEditorDialog } from '@/components/monitor/TaiwanRuleEditorDialog'
import { TaiwanAlertsList } from '@/components/monitor/TaiwanAlertsList'
import { TaiwanRulesList } from '@/components/monitor/TaiwanRulesList'

// Phase 8C-D — Legacy Product Removal: 「A 股策略監控」分頁與其專屬觸發記錄/
// 規則管理 UI (含行業/概念標籤設定) 已隨產品介面正式移除。strategy_alert SSE、
// 通用告警串流 (alertsQuery/api.alertsList)、通用監控規則 API
// (monitorRuleSave/monitorRuleDelete/monitorRulesList, 由 components/monitor/
// RuleEditor.tsx 經 StockPreviewDialog 等處繼續重用)、Taiwan quote/monitor
// merge 完全未動。Monitor 最終只剩台股即時監控一種畫面, 不再需要市場切換分頁。
export function Monitor() {
  const navigate = useNavigate()
  const [twEditorOpen, setTwEditorOpen] = useState(false)
  const [editingTwRule, setEditingTwRule] = useState<TaiwanMonitorRule | null>(null)
  const [presetTwQuote, setPresetTwQuote] = useState<TaiwanRealtimeQuote | null>(null)

  // 台灣監控規則 Query
  const twRulesQuery = useQuery({
    queryKey: QK.taiwanRules,
    queryFn: () => api.taiwanRulesList(),
  })
  const twRules = twRulesQuery.data?.rules || []

  // 台股觸發記錄 (與 strategy_alert SSE 共用同一份 alertsQuery/api.alertsList,
  // 只是 Monitor 頁面級的備援輪詢 — 未讀徽標/即時重新整理走既有 monitorBadge/SSE)
  const alertsQuery = useQuery({
    queryKey: QK.alerts(undefined),
    queryFn: () => api.alertsList({ days: 7, limit: 500 }),
    // New alerts arrive through SSE (useQuoteStream invalidates ['alerts']); this is only the fallback.
    refetchInterval: 60_000,
  })
  const total = alertsQuery.data?.total ?? 0

  // 進入監控頁: 清零未讀徽標 + 記錄"進入時刻", 之後新增的記錄會閃爍
  // 離開監控頁: 停止同步, 之後新增才計入未讀
  const enterTsRef = useRef<number>(Date.now())
  useEffect(() => {
    enterTsRef.current = Date.now()
    markSeen()
    return () => leaveMonitorPage()
  }, [])

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="即時監控中心"
        titleExtra={<span className="font-mono text-xs text-muted">台股 TWSE / TPEx</span>}
        primary={
          <button
            type="button"
            onClick={() => {
              setEditingTwRule(null)
              setPresetTwQuote(null)
              setTwEditorOpen(true)
            }}
            title="新增台股規則"
            className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" />新增規則
          </button>
        }
      />

      <div className="flex-1 min-h-0 px-5 py-4">
        {/* ===== 台股即時監控區塊 ===== */}
        <div className="mx-auto flex h-full max-w-7xl flex-col gap-4">
          {/* 上部: 台股即時行情 + 五檔盤口 */}
          <div className="h-auto">
            <TaiwanQuotePanel
              onAddRuleForSymbol={(q) => {
                setPresetTwQuote(q)
                setEditingTwRule(null)
                setTwEditorOpen(true)
              }}
            />
          </div>

          {/* 下部: 左右雙欄 (左: 警報觸發記錄, 右: 監控規則列表) */}
          <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
            {/* 左欄: 台股告警觸發紀錄 */}
            <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-surface/40 shadow-lg shadow-black/5">
              <div className="flex items-center justify-between border-b border-border/60 bg-surface/60 px-4 py-2.5">
                <SectionHeader icon={BellRing} title="台股觸發記錄" />
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-elevated/50 px-1.5 py-0.5 text-xs font-medium text-muted">
                    {total} 筆事件
                  </span>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-3.5">
                {alertsQuery.isError && !alertsQuery.data && <p role="alert" className="p-6 text-center text-xs text-warning">觸發紀錄載入失敗，請稍後再試。</p>}
                {!(alertsQuery.isError && !alertsQuery.data) && <TaiwanAlertsList
                  alerts={alertsQuery.data?.alerts || []}
                  onInterpretAlert={(symbol, alertId) => navigate(`/stocks/${encodeURIComponent(symbol)}`, { state: { aiResearchRequested: true, alertId } })}
                />}
              </div>
            </section>

            {/* 右欄: 台股監控規則管理 */}
            <section className="flex min-h-0 w-full flex-col overflow-hidden rounded-xl border border-border bg-surface/40 shadow-lg shadow-black/5 lg:w-[420px] lg:shrink-0">
              <div className="flex items-center justify-between border-b border-border/60 bg-surface/60 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <SectionHeader icon={ListChecks} title="台股規則管理" />
                  <span className="rounded-md bg-elevated/50 px-1.5 py-0.5 text-xs font-medium text-muted">
                    {twRules.length}
                  </span>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-3.5">
                <TaiwanRulesList
                  isLoading={twRulesQuery.isPending}
                  isError={twRulesQuery.isError}
                  onRetry={() => { void twRulesQuery.refetch() }}
                  rules={twRules}
                  onEdit={(r) => {
                    setEditingTwRule(r)
                    setPresetTwQuote(null)
                    setTwEditorOpen(true)
                  }}
                />
              </div>
            </section>
          </div>
        </div>
      </div>

      <TaiwanRuleEditorDialog
        open={twEditorOpen}
        rule={editingTwRule}
        presetQuote={presetTwQuote}
        onClose={() => { setTwEditorOpen(false); setEditingTwRule(null); setPresetTwQuote(null) }}
      />
    </div>
  )
}

function SectionHeader({ icon: Icon, title }: { icon: any; title: string }) {
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <Icon className="h-4 w-4 text-accent" />
      <h2 className="text-sm font-semibold text-foreground whitespace-nowrap">{title}</h2>
    </div>
  )
}
