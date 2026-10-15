import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, BellRing } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { QuantEvaluationCard } from '@/components/QuantEvaluationCard'
import { TodaySelection } from '@/components/quant/TodaySelection'
import { PortfolioPanel } from '@/components/portfolio/Portfolio'
import { MarketSentimentCard } from '@/components/dashboard/MarketSentimentCard'
import { TodayEventsWidget } from '@/components/dashboard/TodayEventsWidget'
import { DataHealthSummary } from '@/components/dashboard/DataHealthSummary'
import { BeginnerDashboardWidget } from '@/components/beginner/BeginnerPicker'
import { ExternalContextCard } from '@/components/ExternalContextCard'
import { BuyPointSummaryCard } from '@/components/dashboard/BuyPointSummaryCard'
import { DashboardDailyBriefWidget, DashboardSelectionReviewWidget, DashboardSocialSentimentWidget } from '@/components/dashboard/DashboardWidgets'
import { InstitutionalFlowChart } from '@/components/dashboard/InstitutionalFlowChart'
import { IndustryStrengthCard } from '@/components/dashboard/IndustryStrengthCard'
import { MarketAnomalyCard } from '@/components/dashboard/MarketAnomalyCard'
import { MarketOverviewCard } from '@/components/dashboard/MarketOverviewCard'
import { MonitorWidget } from '@/components/dashboard/MonitorWidget'
import { TaiwanOverviewCard } from '@/components/dashboard/TaiwanOverviewCard'
import { WatchlistQuickGlance } from '@/components/dashboard/WatchlistQuickGlance'

export function Dashboard() {
  const navigate = useNavigate()
  const openStock = (symbol: string) => navigate(`/stocks/${encodeURIComponent(symbol)}`)
  const marketDataStatus = useQuery({
    queryKey: QK.taiwanDataStatus,
    queryFn: api.taiwanDataStatus,
    staleTime: 60_000,
  })
  const latestDailyAsOf = marketDataStatus.data?.daily_as_of ?? null
  const marketDailyStatus = marketDataStatus.data?.daily_status ?? 'unavailable'
  const diagnostics = useQuery({
    queryKey: ['taiwanAbnormalDiagnostics', latestDailyAsOf, 'dashboard', 'context-snapshots'],
    queryFn: () => api.taiwanAbnormalDiagnostics({ date: latestDailyAsOf ?? undefined, include_context_snapshots: true }),
    staleTime: 5 * 60 * 1000,
    enabled: !marketDataStatus.isLoading,
  })
  const marketFallback = useQuery({
    queryKey: ['taiwanMarketIntelligence', latestDailyAsOf],
    queryFn: () => api.taiwanMarketIntelligence(latestDailyAsOf ?? undefined),
    staleTime: 5 * 60 * 1000,
    enabled: diagnostics.isError,
  })
  const industryFallback = useQuery({
    queryKey: ['taiwanIndustryIntelligence', latestDailyAsOf, 'turnover', 'desc'],
    queryFn: () => api.taiwanIndustryIntelligence({ date: latestDailyAsOf ?? undefined, sort_by: 'turnover', order: 'desc' }),
    staleTime: 5 * 60 * 1000,
    enabled: diagnostics.isError,
  })
  const todayAlerts = useQuery({
    queryKey: QK.alertsToday,
    queryFn: () => api.alertsList({ days: 1, limit: 5000 }),
    staleTime: 30_000,
  })

  return (
    <div className="min-h-full bg-base p-1.5">
      {/* 第一眼先看圖：法人買賣超走勢與市場概況，其次是選股、持股、觀察與提醒。
          其餘背景資訊收進「更多市場資訊」，預設收合，內容與功能不變。 */}
      <InstitutionalFlowChart />
      <MarketOverviewCard
        snapshot={diagnostics.data?.market_snapshot ?? (diagnostics.isError ? marketFallback.data : undefined)}
        loading={marketDataStatus.isLoading || diagnostics.isLoading || (diagnostics.isError && marketFallback.isLoading)}
        error={diagnostics.isError && marketFallback.isError}
        marketDailyStatus={marketDailyStatus}
      />
      <BeginnerDashboardWidget />
      <div className="mb-1.5"><PortfolioPanel /></div>
      <WatchlistQuickGlance
        anomalies={diagnostics.data}
        diagnosticsLoading={marketDataStatus.isLoading || diagnostics.isLoading}
        diagnosticsError={diagnostics.isError}
        marketDailyStatus={marketDailyStatus}
        onStockClick={(symbol) => openStock(symbol)}
      />

      <section className="mb-1.5 rounded-card border border-border bg-surface/80 p-1.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm transition-shadow hover:shadow-[0_2px_8px_hsl(var(--border)/0.5)]">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <BellRing className="h-3.5 w-3.5 text-accent" />
            <h2 className="text-xs font-semibold text-foreground">提醒</h2>
            <span className="font-mono text-[10px] text-muted">提醒中心</span>
          </div>
          <Link to="/monitor" className="inline-flex items-center justify-center h-5 w-5 rounded text-muted hover:text-accent hover:bg-accent/10 transition-colors" title="進入監控中心">
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </div>
        <MonitorWidget onStockClick={(symbol) => openStock(symbol)} />
      </section>

      <details className="mb-1.5 rounded-card border border-border bg-surface/80 p-1.5">
        <summary className="cursor-pointer select-none text-xs font-semibold text-foreground">
          更多市場資訊 <span className="font-normal text-muted">（情緒、事件、AI 盤勢、類股、異常、量化、總經、資料狀態）</span>
        </summary>
        <div className="mt-1.5">
          <div className="mb-1.5 grid grid-cols-1 gap-1.5 lg:grid-cols-2">
            <MarketSentimentCard targetDate={latestDailyAsOf} />
            <TodayEventsWidget />
          </div>
          <div className="mb-1.5 grid grid-cols-1 gap-1.5 lg:grid-cols-2">
            <DashboardDailyBriefWidget />
            <DashboardSelectionReviewWidget />
          </div>
          <div className="mb-1.5 grid grid-cols-1 gap-1.5 lg:grid-cols-2">
            <IndustryStrengthCard
              snapshot={diagnostics.data?.industry_snapshot ?? (diagnostics.isError ? industryFallback.data : undefined)}
              loading={marketDataStatus.isLoading || diagnostics.isLoading || (diagnostics.isError && industryFallback.isLoading)}
              error={diagnostics.isError && industryFallback.isError}
              marketDailyStatus={marketDailyStatus}
            />
            <MarketAnomalyCard snapshot={diagnostics.data} loading={marketDataStatus.isLoading || diagnostics.isLoading} error={diagnostics.isError} alerts={todayAlerts.data?.alerts ?? []} alertsLoading={todayAlerts.isLoading} alertsError={todayAlerts.isError} marketDailyStatus={marketDailyStatus} />
          </div>
          <DashboardSocialSentimentWidget />
          <div className="mb-1.5"><BuyPointSummaryCard /></div>
          <TodaySelection />
          <QuantEvaluationCard compact />
          <div className="mb-1.5"><ExternalContextCard compact /></div>
          <DataHealthSummary />
          <TaiwanOverviewCard />
        </div>
      </details>
    </div>
  )
}
