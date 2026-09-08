// Phase 8C-D — Legacy Product Removal: 確認 /screener, /backtest, /mining
// 三個 legacy A 股 route 已從 router 移除, 且沒有殘留的 lazy import 掛在
// router 設定上(pending route element 本身不會在此測試觸發 lazy() 執行,
// 但至少能鎖住 path 集合本身不再包含這三個 legacy path)。
import { describe, expect, it } from 'vitest'
import { router } from './router'

function collectPaths(routes: typeof router.routes): string[] {
  const paths: string[] = []
  for (const r of routes) {
    if (r.path) paths.push(r.path)
    if (r.children) paths.push(...collectPaths(r.children as typeof router.routes))
  }
  return paths
}

describe('router — Legacy A-share route removal (Phase 8C-D)', () => {
  const allPaths = collectPaths(router.routes)

  it('does not register /screener, /backtest, or /mining', () => {
    expect(allPaths).not.toContain('screener')
    expect(allPaths).not.toContain('backtest')
    expect(allPaths).not.toContain('mining')
  })

  it('still registers the 5 Taiwan core routes', () => {
    expect(allPaths).toContain('watchlist')
    expect(allPaths).toContain('taiwan-screener')
    expect(allPaths).toContain('monitor')
    expect(allPaths).toContain('stocks/compare')
  })
})
