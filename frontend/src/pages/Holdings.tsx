import { PageHeader } from '@/components/PageHeader'
import { PortfolioPanel } from '@/components/portfolio/Portfolio'
import { PortfolioAnalyticsPanel, usePortfolioAnalytics } from '@/components/portfolio/PortfolioAnalytics'

/** 左側選單「我的持股」：持倉清單，進入時直接開啟匯入視窗。 */
export function Holdings() {
  const { hasHoldings, query } = usePortfolioAnalytics()
  return (
    <div className="min-h-full bg-base p-2 sm:p-3">
      <div className="mx-auto w-full max-w-6xl space-y-2">
        <PageHeader title="我的持股" help="可選檔案、拖曳檔案，或直接貼上券商／Excel 的持股表格匯入。之後要再匯入，按持倉卡片右上角「⋯」裡的「匯入持股」。" />
        <PortfolioPanel openImportWhenEmpty allocation={query.isError ? undefined : query.data?.allocation} />
        {hasHoldings && <PortfolioAnalyticsPanel query={query} />}
      </div>
    </div>
  )
}
