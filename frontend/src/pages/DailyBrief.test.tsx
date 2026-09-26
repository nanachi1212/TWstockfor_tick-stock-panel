import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { DailyBrief } from './DailyBrief'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    dailyBrief: {
      getDailyBrief: vi.fn(),
      generateAiSummary: vi.fn(),
      saveDailyBrief: vi.fn(),
      listHistory: vi.fn(),
      getHistoryDetail: vi.fn(),
    },
    selectionReview: {
      saveSnapshot: vi.fn(),
    },
    watchlistList: vi.fn(),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
  },
}))

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
}

function buildMockBrief() {
  return {
    brief_date: '2026-08-28',
    generated_at: '2026-08-28T14:30:00Z',
    market: {
      trade_date: '2026-08-28',
      taiex_close: 22150.8,
      taiex_change: 120.5,
      taiex_change_pct: 0.55,
      advance_count: 550,
      decline_count: 320,
      flat_count: 80,
      upper_limit_count: 12,
      lower_limit_count: 2,
      total_turnover: 350000000000,
      foreign_net: 5500000000,
      investment_trust_net: 1800000000,
      dealer_net: -500000000,
      total_institutional_net: 6800000000,
      sentiment_label: '偏多',
      sentiment_description: '半導體引領大盤反彈，外資投信同步站在買方',
      strongest_sectors: [
        { industry: '半導體業', change_pct: 1.85, turnover: 120000000000, advance_ratio: 0.75 },
      ],
      weakest_sectors: [],
    },
    portfolio: {
      holdings_count: 0,
      biggest_movers: [],
      quant_changes: [],
      event_risks: [],
      active_alerts: [],
    },
    watchlist: {
      items_count: 0,
      quant_leaders: [],
      unusual_volume: [],
      events: [],
    },
    candidates: {
      new_top10: [
        {
          symbol: '2330.TWSE',
          name: '台積電',
          reason_type: 'new_top10',
          source_name: 'Live Quant Top 10',
          quant_score: 92.4,
          rank: 1,
          close: 1020.0,
          change_pct: 1.8,
          match_reasons: ['突破短期均線', '外資買超擴大'],
        },
      ],
      dropped_top10: [],
      strategy_matches: [],
    },
    events: {
      risk_events: [
        {
          symbol: '3008.TWSE',
          title: '處置有價證券',
          description: '自 8/29 起改以人工管制搓合 20 分鐘撮合一次',
        },
      ],
      attention_events: [],
    },
    news: {
      items: [
        {
          title: '台積電先進封裝產能持續吃緊',
          source: '經濟日報',
          summary: '預計下半年擴產進度超前',
        },
      ],
    },
  }
}

describe('DailyBrief Page (A12)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
    vi.mocked(api.dailyBrief.listHistory).mockResolvedValue([] as any)
  })

  it('renders deterministic market facts and candidates correctly', async () => {
    const mockBrief = buildMockBrief()
    vi.mocked(api.dailyBrief.getDailyBrief).mockResolvedValue(mockBrief as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DailyBrief />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText('每日台股摘要與 AI 解讀')).toBeInTheDocument()
      expect(screen.getByText('2026-08-28')).toBeInTheDocument()
      expect(screen.getByText('22150.80')).toBeInTheDocument()
      expect(screen.getByText('+0.55%')).toBeInTheDocument()
      expect(screen.getByText('偏多')).toBeInTheDocument()
      expect(screen.getAllByText('台積電')[0]).toBeInTheDocument()
      expect(screen.getByText(/處置有價證券/)).toBeInTheDocument()
      expect(screen.getByText('尚未產生今日 AI 深度摘要')).toBeInTheDocument()
    })
  })

  it('generates on-demand 7-section AI interpretation upon user click', async () => {
    const mockBrief = buildMockBrief()
    const mockAiSummary = {
      section_a_market: '今日台股由台積電領軍，加權指數穩健收紅。',
      section_b_key_changes: [
        '外資反手大幅買超 55 億元',
        '半導體產業成交佔比擴大至 35%',
        '處置股風險新增 3008 大立光',
      ],
      section_c_portfolio: '目前無持股，保持充裕現金部位。',
      section_d_watchlist: '觀察名單動態平穩。',
      section_e_candidates: '台積電以 92.4 高分強勢新進 Top 10。',
      section_f_risks: '注意 3008 處置交易流動性風險。',
      section_g_tracking: '明日觀察成交金額能否維持 3500 億以上水準。',
      evidence_sources: ['台灣證券交易所', '盤後三大法人買賣超', 'Live Quant 綜合模型'],
    }

    vi.mocked(api.dailyBrief.getDailyBrief).mockResolvedValue(mockBrief as any)
    vi.mocked(api.dailyBrief.generateAiSummary).mockResolvedValue(mockAiSummary as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DailyBrief />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText('22150.80')).toBeInTheDocument()
    })

    // 點擊「產生今日 AI 摘要」按鈕
    const aiBtn = screen.getAllByRole('button', { name: /產生今日 AI 摘要/ })[0]
    expect(aiBtn).not.toBeDisabled()
    fireEvent.click(aiBtn)

    await waitFor(() => {
      expect(api.dailyBrief.generateAiSummary).toHaveBeenCalledTimes(1)
      expect(screen.getByText('A. 今日市場概況與盤勢解讀')).toBeInTheDocument()
      expect(screen.getByText(/今日台股由台積電領軍/)).toBeInTheDocument()
      expect(screen.getByText('B. 今日最重要的 3～5 個核心變化')).toBeInTheDocument()
      expect(screen.getByText('外資反手大幅買超 55 億元')).toBeInTheDocument()
      expect(screen.getByText('E. 今日候選股與新機會亮點')).toBeInTheDocument()
      expect(screen.getByText('G. 明日／下一交易日核心觀察重點')).toBeInTheDocument()
      expect(screen.getByText('Live Quant 綜合模型')).toBeInTheDocument()
    })
  })

  it('allows adding candidate to watchlist', async () => {
    const mockBrief = buildMockBrief()
    vi.mocked(api.dailyBrief.getDailyBrief).mockResolvedValue(mockBrief as any)
    vi.mocked(api.watchlistAdd).mockResolvedValue({} as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DailyBrief />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText('加入自選')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('加入自選'))

    await waitFor(() => {
      expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE', '')
    })
  })
})
