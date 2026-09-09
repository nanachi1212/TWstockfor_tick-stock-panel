// Data Freshness & Source Labels batch (Post-8C follow-up).
//
// TaiwanDataQuality.tsx 是 Watchlist / Monitor (TaiwanQuotePanel) / StockDetail
// header 共用的唯一新鮮度/來源判斷實作。這裡直接測試該共用邏輯本身 (純函式 +
// 一個無外部依賴的展示元件)，不重新掛載三個消費頁面 — Watchlist/StockDetail
// 是大型、依賴眾多 API 的頁面元件，本次改動只影響其中一個表格欄位/一行文字，
// 對整頁掛載測試投入的成本與本次變更範圍不成比例；三頁實際共用的「判斷邏輯」
// 才是本批次真正新增、有風險需要覆蓋的部分。
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  DataQualityBadge,
  formatQuoteSource,
  formatQuoteTime,
  freshnessLabel,
} from './TaiwanDataQuality'

describe('freshnessLabel', () => {
  it('回傳 null 當 meta 不存在 (不可偽造新鮮度狀態)', () => {
    expect(freshnessLabel(null)).toBeNull()
    expect(freshnessLabel(undefined)).toBeNull()
  })

  it('is_stale=true 一律優先顯示「資料過期」，不論 freshness_class 為何', () => {
    expect(freshnessLabel({ is_stale: true, freshness_class: 'best_effort_near_realtime' }))
      .toEqual({ label: '資料過期', tone: 'stale' })
  })

  it('freshness_class=delayed_15m 顯示「延遲 15m」', () => {
    expect(freshnessLabel({ is_stale: false, freshness_class: 'delayed_15m' }))
      .toEqual({ label: '延遲 15m', tone: 'delayed' })
  })

  it('freshness_class=eod_snapshot 顯示「盤後快照」', () => {
    expect(freshnessLabel({ is_stale: false, freshness_class: 'eod_snapshot' }))
      .toEqual({ label: '盤後快照', tone: 'snapshot' })
  })

  it('freshness_class=best_effort_near_realtime 顯示「近即時」', () => {
    expect(freshnessLabel({ is_stale: false, freshness_class: 'best_effort_near_realtime' }))
      .toEqual({ label: '近即時', tone: 'realtime' })
  })

  it('status=daily_fallback 顯示「日線備援」(不需要 freshness_class)', () => {
    expect(freshnessLabel({ is_stale: false, status: 'daily_fallback' }))
      .toEqual({ label: '日線備援', tone: 'snapshot' })
  })

  it('is_realtime=true 顯示「即時」', () => {
    expect(freshnessLabel({ is_stale: false, is_realtime: true }))
      .toEqual({ label: '即時', tone: 'realtime' })
  })

  it('StockDetail 用的 TaiwanSectionMeta 只有 is_stale=false、無 freshness_class 時，優雅退化為「近即時」而非「未知來源」', () => {
    expect(freshnessLabel({ is_stale: false, status: 'available', source: 'twse:STOCK_DAY_ALL' }))
      .toEqual({ label: '近即時', tone: 'realtime' })
  })

  it('完全沒有可判斷欄位 (is_stale 未提供) 時顯示中性的「未知來源」，不假裝新鮮', () => {
    expect(freshnessLabel({ source: 'something-unrecognized' }))
      .toEqual({ label: '未知來源', tone: 'unknown' })
  })
})

describe('formatQuoteSource', () => {
  it('已知 provider 識別碼轉成中文顯示名稱，不原樣顯示內部字串', () => {
    expect(formatQuoteSource('yahoo:chart')).toBe('Yahoo 延遲報價')
    expect(formatQuoteSource('twse:mis')).toBe('證交所即時報價')
    expect(formatQuoteSource('twse:STOCK_DAY_ALL')).toBe('證交所官方收盤快照')
    expect(formatQuoteSource('tpex:mainboard_quotes')).toBe('櫃買中心官方收盤快照')
  })

  it('未知來源顯示中性描述，不猜測、不原樣透傳', () => {
    expect(formatQuoteSource('some:unknown-provider')).toBe('其他資料來源')
    expect(formatQuoteSource(null)).toBe('其他資料來源')
    expect(formatQuoteSource(undefined)).toBe('其他資料來源')
  })
})

describe('formatQuoteTime', () => {
  it('格式化 ISO 時間字串為 HH:mm:ss', () => {
    const t = new Date(2026, 8, 9, 9, 53, 58).toISOString()
    expect(formatQuoteTime(t)).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('缺少時間且非 fallback 時顯示「時間不可用」', () => {
    expect(formatQuoteTime(null, false)).toBe('時間不可用')
  })

  it('缺少時間且為 daily fallback 時顯示「上一交易日 13:30」', () => {
    expect(formatQuoteTime(null, true)).toBe('上一交易日 13:30')
  })

  it('無法解析的字串原樣回傳，不偽造時間', () => {
    expect(formatQuoteTime('not-a-real-timestamp')).toBe('not-a-real-timestamp')
  })
})

describe('DataQualityBadge', () => {
  it('meta 為 null/undefined 時不渲染任何內容', () => {
    const { container: c1 } = render(<DataQualityBadge meta={null} />)
    expect(c1.textContent).toBe('')
    const { container: c2 } = render(<DataQualityBadge meta={undefined} />)
    expect(c2.textContent).toBe('')
  })

  it('新鮮 (is_stale=false) 的報價不顯示過期警示文字', () => {
    render(<DataQualityBadge meta={{ is_stale: false, freshness_class: 'best_effort_near_realtime', source: 'twse:mis' }} />)
    expect(screen.getByText('近即時')).toBeInTheDocument()
    expect(screen.queryByText('資料過期')).not.toBeInTheDocument()
  })

  it('過期/延遲報價顯示精簡新鮮度徽章', () => {
    render(<DataQualityBadge meta={{ is_stale: true, freshness_class: 'delayed_15m', source: 'yahoo:chart' }} />)
    expect(screen.getByText('資料過期')).toBeInTheDocument()
  })

  it('提供 quoteTime 時，tooltip 帶入格式化後的報價時間而非原始 ISO 字串或 fetched_at', () => {
    const qt = new Date(2026, 8, 9, 9, 53, 58).toISOString()
    render(
      <DataQualityBadge
        meta={{ is_stale: true, freshness_class: 'delayed_15m', source: 'yahoo:chart', fetched_at: '2026-09-09T10:14:52+08:00' }}
        quoteTime={qt}
      />,
    )
    const badge = screen.getByText('資料過期')
    expect(badge.title).toContain('報價時間: 09:53:58')
    expect(badge.title).not.toContain('yahoo:chart')
    expect(badge.title).toContain('Yahoo 延遲報價')
  })

  it('未提供 quoteTime (如既有 Monitor 呼叫方式) 時仍顯示 fetched_at，行為與變更前一致', () => {
    render(
      <DataQualityBadge
        meta={{ is_stale: false, freshness_class: 'best_effort_near_realtime', source: 'twse:mis', fetched_at: '2026-09-09T10:14:52+08:00' }}
      />,
    )
    const badge = screen.getByText('近即時')
    expect(badge.title).toContain('抓取時間: 2026-09-09T10:14:52+08:00')
  })
})
