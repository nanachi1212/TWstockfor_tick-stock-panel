import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, BellRing, ChevronDown } from 'lucide-react'
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
    <div className="dashboard-canvas min-h-full bg-base">
      <div className="mx-auto w-full max-w-[1600px] px-3 py-5 sm:px-5 sm:py-6 lg:px-7 lg:py-8">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-border pb-5">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-[-0.025em] text-foreground">市場看板</h1>
          </div>
          {latestDailyAsOf && <span className="font-mono text-xs text-muted">最近資料 {latestDailyAsOf}</span>}
        </header>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
          <div className="min-w-0 xl:col-span-5">
            <MarketOverviewCard
              snapshot={diagnostics.data?.market_snapshot ?? (diagnostics.isError ? marketFallback.data : undefined)}
              loading={marketDataStatus.isLoading || diagnostics.isLoading || (diagnostics.isError && marketFallback.isLoading)}
              error={diagnostics.isError && marketFallback.isError}
              marketDailyStatus={marketDailyStatus}
            />
          </div>
          <div className="min-w-0 xl:col-span-7">
            <InstitutionalFlowChart />
          </div>
        </div>

        <div className="mt-4">
          <BeginnerDashboardWidget />
        </div>

        <div className="mt-4 grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
          <div className="min-w-0 xl:col-span-8"><PortfolioPanel /></div>
          <div className="grid min-w-0 gap-4 xl:col-span-4">
            <WatchlistQuickGlance
              anomalies={diagnostics.data}
              diagnosticsLoading={marketDataStatus.isLoading || diagnostics.isLoading}
              diagnosticsError={diagnostics.isError}
              marketDailyStatus={marketDailyStatus}
              onStockClick={(symbol) => openStock(symbol)}
            />

            <section className="rounded-card border border-border bg-surface p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <BellRing className="h-4 w-4 text-accent" />
                  <h2 className="text-sm font-semibold text-foreground">提醒</h2>
                </div>
                <Link to="/monitor" className="interactive-target inline-flex items-center justify-center rounded-btn text-muted transition-colors hover:bg-accent/10 hover:text-accent" title="進入監控中心" aria-label="進入監控中心">
                  <ArrowUpRight className="h-4 w-4" />
                </Link>
              </div>
              <MonitorWidget onStockClick={(symbol) => openStock(symbol)} />
            </section>
          </div>
        </div>

        <details className="group mt-4 rounded-card border border-border bg-surface">
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-4 text-sm font-semibold text-foreground [&::-webkit-details-marker]:hidden">
            <span>延伸市場資訊</span>
            <ChevronDown className="h-4 w-4 text-muted transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="border-t border-border p-4">
          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <MarketSentimentCard targetDate={latestDailyAsOf} />
            <TodayEventsWidget />
          </div>
          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <DashboardDailyBriefWidget />
            <DashboardSelectionReviewWidget />
          </div>
          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <IndustryStrengthCard
              snapshot={diagnostics.data?.industry_snapshot ?? (diagnostics.isError ? industryFallback.data : undefined)}
              loading={marketDataStatus.isLoading || diagnostics.isLoading || (diagnostics.isError && industryFallback.isLoading)}
              error={diagnostics.isError && industryFallback.isError}
              marketDailyStatus={marketDailyStatus}
            />
            <MarketAnomalyCard snapshot={diagnostics.data} loading={marketDataStatus.isLoading || diagnostics.isLoading} error={diagnostics.isError} alerts={todayAlerts.data?.alerts ?? []} alertsLoading={todayAlerts.isLoading} alertsError={todayAlerts.isError} marketDailyStatus={marketDailyStatus} />
          </div>
          <DashboardSocialSentimentWidget />
          <div className="mb-4"><BuyPointSummaryCard /></div>
          <TodaySelection />
          <QuantEvaluationCard compact />
          <div className="mb-4"><ExternalContextCard compact /></div>
          <DataHealthSummary />
          <TaiwanOverviewCard />
          </div>
        </details>
      </div>
    </div>
  )
}
