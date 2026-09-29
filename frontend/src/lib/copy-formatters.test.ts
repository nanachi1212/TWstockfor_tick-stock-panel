import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  formatStockDetailCopy,
  formatStockDetailPrompt,
  formatDailyBriefCopy,
  formatDailyBriefPrompt,
  formatScreenerCopy,
  formatScreenerPrompt,
  formatSelectionReviewCopy,
  formatSelectionReviewPrompt,
  copyToClipboard,
  sanitize,
  num,
  pct,
  sharesAndLots,
  EXTERNAL_AI_PROMPT_FOOTER,
} from './copy-formatters'

describe('copy-formatters helper sanitize & numbers', () => {
  it('masks secret patterns', () => {
    expect(sanitize('sk-1234567890abcdef')).toBe('[敏感資訊已隱藏]')
    expect(sanitize('ghp_abcdef1234567890')).toBe('[敏感資訊已隱藏]')
    expect(sanitize('Bearer my-secret-token')).toBe('[敏感資訊已隱藏]')
    expect(sanitize('C:\\Users\\User\\project')).toBe('[路徑已隱藏]')
    expect(sanitize('/home/user/project')).toBe('[路徑已隱藏]')
  })

  it('preserves missing values and does not convert to 0', () => {
    expect(num(null)).toBe('不可用 (unavailable)')
    expect(num(undefined)).toBe('不可用 (unavailable)')
    expect(num(0)).toBe('0.00')
    expect(pct(null)).toBe('不可用 (unavailable)')
    expect(pct(0)).toBe('+0.00%')
  })

  it('labels canonical shares and only rounds the secondary lot display', () => {
    expect(sharesAndLots(13_167_000)).toBe('13,167,000 股（13,167 張）')
    expect(sharesAndLots(-2_708_654)).toBe('-2,708,654 股（約 -2,709 張）')
    expect(sharesAndLots(null)).toBe('不可用 (unavailable)')
  })
})

describe('formatStockDetailCopy & Prompt', () => {
  const mockStockData = {
    symbol: '2330.TWSE',
    name: '台積電',
    exchange: 'TWSE',
    instrument_type: '股票',
    sector: '半導體業',
    data_as_of: '2026-09-25 13:30:00',
    freshness: 'fresh',
    quote: {
      close: 950,
      change: 15,
      change_pct: 1.6,
      open: 940,
      high: 955,
      low: 938,
      volume: 35000,
      turnover: 33250000,
      turnover_source: 'official_daily',
      turnover_status: 'fallback',
      turnover_as_of: '2026-09-25',
      quote_time: '2026-09-25 13:30:00',
    },
    quant: {
      score: 88.5,
      rank: 3,
      reasons: ['籌碼集中', '技術突破'],
      factors: { momentum: 85, value: 72 },
    },
    valuation: {
      pe: 22.5,
      pb: 5.2,
      dividend_yield: 2.1,
    },
    fundamentals: {
      revenue_yoy: 32.5,
      revenue_mom: 4.8,
      revenue_date: '2026-08',
      eps: 10.5,
      eps_date: '2026-Q2',
      gross_margin: 54.2,
      operating_margin: 43.1,
      net_margin: 38.5,
    },
    institutional_flows: {
      foreign_buy_sell: 5200,
      trust_buy_sell: 1200,
      dealer_buy_sell: -400,
      total_buy_sell: 6000,
      date: '2026-09-25',
    },
    foreign_shareholding: {
      ratio: 74.2,
      change_20d: 1.5,
    },
    margin_lending: {
      margin_balance: 15200,
      short_balance: 120,
      lending_latest_volume: 85000,
    },
    market_context: {
      benchmark_symbol: 'TAIEX',
      benchmark_name: '發行量加權股價指數',
      close: 22800,
      change_pct: 0.8,
      as_of: '2026-09-25',
      status: 'available',
    },
    official_events: [
      {
        event_date: '2026-09-20',
        event_type_label: '除息',
        title: '除息每股4.0元',
        summary: '現金股利發放',
        source: 'TWSE',
      },
    ],
    news: [
      {
        published_at: '2026-09-25',
        title: '先進製程訂單滿載',
        source: '經濟日報',
        summary: '外資持續回補',
      },
    ],
    portfolioPositions: [
      {
        symbol: '2330.TWSE',
        shares: 3,
        avg_cost: 820,
        invested_amount: 2460000,
        unrealized_pnl: 390000,
        unrealized_pnl_pct: 15.85,
      },
    ],
  }

  it('Stock Detail: respects Portfolio privacy OFF by default', () => {
    const text = formatStockDetailCopy({
      ...mockStockData,
      includePortfolio: false,
    })
    expect(text).toContain('台積電（2330.TWSE）')
    expect(text).toContain('收盤價：950.00 元')
    expect(text).toContain('成交量：35,000 股（35 張）')
    expect(text).toContain('成交金額：33,250,000 元')
    expect(text).toContain('外資：5,200 股（約 5 張）')
    expect(text).toContain('借券最新成交量 85,000 股（85 張）')
    expect(text).toContain('發行量加權股價指數（TAIEX）')
    expect(text).not.toContain('借券賣出餘額')
    expect(text).toContain('官方事件 (Official Events)')
    expect(text).toContain('相關新聞 (News)')
    // Privacy protection: OFF must NEVER leak shares, avg_cost, or pnl
    expect(text).toContain('個人持股資料：未包含（隱私保護已啟用）')
    expect(text).not.toContain('820 元')
    expect(text).not.toContain('390000')
    expect(text).not.toContain('持股數量：3 張')
  })

  it('Stock Detail: outputs portfolio details only when privacy ON', () => {
    const text = formatStockDetailCopy({
      ...mockStockData,
      includePortfolio: true,
    })
    expect(text).toContain('持股數量：3 張')
    expect(text).toContain('平均成本：820.00 元')
    expect(text).toContain('投入金額：2460000 元')
    expect(text).toContain('未實現損益：390000 元 (+15.85%)')
  })

  it('Stock Detail Prompt: appends the 8-point external AI prompt footer', () => {
    const prompt = formatStockDetailPrompt(mockStockData)
    expect(prompt).toContain('請只根據以上提供的資料與你明確標示的推論進行分析')
    expect(prompt).toContain('1. 區分資料事實與推論')
    expect(prompt).toContain('2. 不得把缺失資料當成 0')
    expect(prompt).toContain('8. 資料不足時明確指出，不自行補值')
  })

  it('Stock Detail: missing fields are preserved as unavailable', () => {
    const emptyStock = {
      symbol: '9999.TWSE',
      name: '測試股',
      quote: null,
      quant: null,
      valuation: null,
      fundamentals: null,
      institutional_flows: null,
    }
    const text = formatStockDetailCopy(emptyStock)
    expect(text).toContain('即時行情：不可用 (unavailable)')
    expect(text).toContain('量化評分：不可用 (unavailable)')
    expect(text).toContain('估值指標：不可用 (unavailable)')
    expect(text).toContain('營收與獲利：不可用 (unavailable)')
    expect(text).toContain('法人買賣超：不可用 (unavailable)')
  })

  it('Stock Detail: exports explicit unavailable metadata without relabeling units', () => {
    const text = formatStockDetailCopy({
      symbol: '8358.TPEX',
      name: '金居',
      quote: {
        close: 100,
        volume: 13_167_000,
        turnover: null,
        turnover_source: 'realtime+daily',
        turnover_status: 'unavailable',
        turnover_reason: 'no amount is available for the exact quote trade date',
        turnover_as_of: '2026-09-29',
        turnover_freshness: 'unavailable',
      },
      quant: {
        score: null,
        rank: null,
        unavailable_reason: 'not in ranking universe',
        meta: { source: 'live_quant:frozen_ranking', status: 'unavailable', reason: 'not in ranking universe', data_date: '2026-09-24', freshness: 'current_audited_snapshot' },
      },
      institutional_flows: {
        foreign_buy_sell: -2_708_654,
        meta: { source: 'tpex:daily_trade', status: 'available', data_date: '2026-09-24', freshness: 'available' },
      },
      margin_lending: {
        margin_balance: null,
        short_balance: null,
        lending_latest_volume: null,
        margin_meta: { source: 'tpex:margin_balance', status: 'unavailable', reason: 'no row', data_date: '2026-09-24', freshness: 'unavailable' },
        lending_meta: { source: 'finmind:TaiwanStockSecuritiesLending', status: 'unavailable', reason: 'no rows', data_date: null, freshness: 'unavailable' },
      },
    })

    expect(text).toContain('成交量：13,167,000 股（13,167 張）')
    expect(text).toContain('成交金額：不可用 (unavailable)')
    expect(text).toContain('source=realtime+daily | status=unavailable | reason=no amount is available for the exact quote trade date | data_date=2026-09-29 | freshness=unavailable')
    expect(text).toContain('外資：-2,708,654 股（約 -2,709 張）')
    expect(text).toContain('借券最新成交量 不可用 (unavailable)')
    expect(text).not.toContain('借券賣出餘額')
  })
})

describe('formatDailyBriefCopy & Prompt', () => {
  it('formats daily brief with freshness and privacy protection', () => {
    const text = formatDailyBriefCopy({
      brief_date: '2026-09-25',
      evidence_date: '2026-09-24',
      freshness: 'cached',
      market: {
        taiex_close: 22800,
        change: 150,
        change_pct: 0.66,
        turnover: 3500,
        foreign_net: 120,
        trust_net: 30,
        dealer_net: -15,
        advances: 620,
        declines: 310,
        unchanged: 100,
        limit_up: 12,
        limit_down: 1,
        sentiment_label: '偏多',
      },
      portfolio: {
        holdings_count: 5,
        items: [{ symbol: '2330.TWSE', name: '台積電', shares: 2, avg_cost: 800, close: 950, change_pct: 1.5 }],
      },
      includePortfolio: false,
      candidates: [{ symbol: '2454.TWSE', name: '聯發科', score: 85, rank: 2, reason: 'IC強勢', close: 1200, change_pct: 2.5 }],
    })

    expect(text).toContain('晨報日期：2026-09-25')
    expect(text).toContain('數據基礎日 (evidence_date)：2026-09-24')
    expect(text).toContain('資料狀態：cached')
    expect(text).toContain('個人持股詳細資料：未包含（隱私保護已啟用，持有檔數：5 檔）')
    expect(text).not.toContain('800 元')
    expect(text).toContain('聯發科（2454.TWSE）')
  })
})

describe('formatScreenerCopy & formatSelectionReviewCopy', () => {
  it('Screener: reports coverage sufficiency and warning when insufficient', () => {
    const text = formatScreenerCopy({
      strategyName: '低本益比價值選股',
      as_of: '2026-09-25',
      coverage: {
        total_universe: 1800,
        covered_count: 1200,
        coverage_pct: 0.667,
        insufficient_reason: '財報部分缺漏',
      },
      results: [
        {
          symbol: '2330.TWSE',
          name: '台積電',
          price: 950,
          change_pct: 1.6,
          score: 88,
          rank: 1,
          match_reasons: ['本益比 < 25', 'ROE > 20%'],
        },
      ],
    })

    expect(text).toContain('低本益比價值選股')
    expect(text).toContain('⚠️ 覆蓋率不足警告：財報部分缺漏')
    expect(text).toContain('#1 台積電（2330.TWSE）')
  })

  it('Selection Review: explicitly states excess return != alpha and descriptive analytics', () => {
    const text = formatSelectionReviewCopy({
      strategy_id: 'strat_value_01',
      strategy_name: '價值精選',
      snapshot_date: '2026-09-01',
      as_of_date: '2026-09-25',
      strategy_stats: {
        win_rate_5d: 65.0,
        avg_return_5d: 3.2,
        benchmark_5d: 1.2,
        excess_return_5d: 2.0,
        win_rate_20d: 70.0,
        avg_return_20d: 6.5,
        benchmark_20d: 2.5,
        excess_return_20d: 4.0,
      },
      picks: [
        {
          symbol: '2330.TWSE',
          name: '台積電',
          rank: 1,
          quant_score: 90,
          initial_price: 900,
          h5d_status: 'completed',
          h5d_return_pct: 3.5,
          h20d_status: 'completed',
          h20d_return_pct: 5.5,
          match_reasons: ['高量化分'],
        },
      ],
    })

    expect(text).toContain('超額報酬 (excess return) 僅為歷史描述統計，不等於未來超額收益 (alpha)')
    expect(text).toContain('描述性分析 (descriptive analytics)')
    expect(text).toContain('超額報酬 +2.00%')
    expect(text).toContain('台積電（2330.TWSE）')
  })

  it('generates external AI analysis prompts with 8 objective guidelines', () => {
    expect(EXTERNAL_AI_PROMPT_FOOTER).toContain('區分資料事實與推論')
    expect(EXTERNAL_AI_PROMPT_FOOTER).toContain('不得捏造價格')
    expect(EXTERNAL_AI_PROMPT_FOOTER).toContain('資料不足時明確指出，不自行補值')

    const briefPrompt = formatDailyBriefPrompt({
      brief_date: '2026-09-25',
      market: { taiex_close: 22800 },
    })
    expect(briefPrompt).toContain('以下是台股每日市場摘要報告')
    expect(briefPrompt).toContain('區分資料事實與推論')

    const screenerPrompt = formatScreenerPrompt({
      strategyName: '高殖利率選股',
      results: [
        { symbol: '2330.TWSE', name: '台積電', price: 950 },
      ],
    })
    expect(screenerPrompt).toContain('高殖利率選股')
    expect(screenerPrompt).toContain('區分資料事實與推論')

    const reviewPrompt = formatSelectionReviewPrompt({
      strategy_name: '動能突破策略',
      picks: [
        { symbol: '2330.TWSE', name: '台積電' },
      ],
    })
    expect(reviewPrompt).toContain('動能突破策略')
    expect(reviewPrompt).toContain('區分資料事實與推論')
  })
})

describe('copyToClipboard', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('returns true on navigator.clipboard.writeText success', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    })
    const ok = await copyToClipboard('test copy text')
    expect(ok).toBe(true)
    expect(writeTextMock).toHaveBeenCalledWith('test copy text')
  })

  it('falls back to document.execCommand if clipboard API fails', async () => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockRejectedValue(new Error('Permission denied')),
      },
    })
    document.execCommand = vi.fn().mockReturnValue(true)
    const ok = await copyToClipboard('fallback copy text')
    expect(ok).toBe(true)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
  })

  it('returns false when empty string provided', async () => {
    const ok = await copyToClipboard('')
    expect(ok).toBe(false)
  })
})
