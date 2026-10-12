import { PortfolioPanel } from '@/components/portfolio/Portfolio'

/** 左側選單「我的持股」：持倉清單，進入時直接打開匯入視窗。 */
export function Holdings() {
  return (
    <div className="min-h-full bg-base p-2 sm:p-3">
      <div className="mx-auto w-full max-w-6xl space-y-2">
        <header>
          <h1 className="text-lg font-semibold text-foreground">我的持股</h1>
          <p className="text-xs text-muted">可選檔案、拖曳檔案，或直接貼上券商／Excel 的持股表格匯入。之後要再匯入，按右上角「匯入持股」。</p>
        </header>
        <PortfolioPanel openImportOnMount />
      </div>
    </div>
  )
}
