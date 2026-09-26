import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { SelectionReview } from './SelectionReview'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    selectionReview: {
      listSnapshots: vi.fn(),
      getSnapshotDetail: vi.fn(),
      deleteSnapshot: vi.fn(),
      getStrategyStats: vi.fn(),
      getConditionStats: vi.fn(),
    },
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

describe('SelectionReview Page (A12)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders snapshot list and displays metrics correctly', async () => {
    const mockSnapshots = [
      {
        snapshot_id: 'snap_001',
        created_at: '2026-08-25T09:30:00Z',
        strategy_id: 'momentum_top',
        strategy_name: '動能領頭羊策略',
        as_of_date: '2026-08-25',
        selected_count: 5,
        h5d_evaluated_count: 5,
        h20d_evaluated_count: 0,
        h5d_avg_return_pct: 3.52,
        h20d_avg_return_pct: null,
        h5d_bm_return_pct: 1.2,
        h20d_bm_return_pct: null,
        h5d_excess_pct: 2.32,
        h20d_excess_pct: null,
      },
    ]

    vi.mocked(api.selectionReview.listSnapshots).mockResolvedValue(mockSnapshots as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SelectionReview />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(screen.getByText('選股復盤與策略追蹤')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getAllByText('動能領頭羊策略')[0]).toBeInTheDocument()
      expect(screen.getByText('2026-08-25')).toBeInTheDocument()
      expect(screen.getByText('5 檔標的')).toBeInTheDocument()
      expect(screen.getByText('+3.52%')).toBeInTheDocument()
      expect(screen.getByText(/超額 \+2.32%/)).toBeInTheDocument()
    })
  })

  it('navigates to snapshot detail and shows horizon items and pending status', async () => {
    const mockSnapshots = [
      {
        snapshot_id: 'snap_001',
        created_at: '2026-08-25T09:30:00Z',
        strategy_id: 'strat_1',
        strategy_name: '高殖利率成長股',
        as_of_date: '2026-08-25',
        selected_count: 1,
        h5d_evaluated_count: 1,
        h20d_evaluated_count: 0,
        h5d_avg_return_pct: 2.1,
        h20d_avg_return_pct: null,
        h5d_bm_return_pct: 1.0,
        h20d_bm_return_pct: null,
        h5d_excess_pct: 1.1,
        h20d_excess_pct: null,
      },
    ]

    const mockDetail = {
      snapshot: {
        snapshot_id: 'snap_001',
        created_at: '2026-08-25T09:30:00Z',
        strategy_id: 'strat_1',
        strategy_name: '高殖利率成長股',
        as_of_date: '2026-08-25',
        market_context_summary: '台股成交量 3500 億，指數小跌',
        selected_symbols: ['2330.TWSE'],
        items: [],
      },
      evaluated_items: [
        {
          symbol: '2330.TWSE',
          name: '台積電',
          rank: 1,
          entry_price: 1000.0,
          quant_score: 88.5,
          match_reasons: ['高動能', '外資大買'],
          fundamental_summary: '營收年增 25%',
          chips_summary: '外資連 3 買',
          event_risk_summary: null,
          h1d_price: 1010.0,
          h1d_return_pct: 1.0,
          h1d_status: 'completed',
          h1d_bm_return_pct: 0.5,
          h1d_excess_pct: 0.5,
          h5d_price: 1030.0,
          h5d_return_pct: 3.0,
          h5d_status: 'completed',
          h5d_bm_return_pct: 1.2,
          h5d_excess_pct: 1.8,
          h20d_price: null,
          h20d_return_pct: null,
          h20d_status: 'pending',
          h20d_bm_return_pct: null,
          h20d_excess_pct: null,
          benchmark_symbol: '0050.TWSE',
          benchmark_name: '台灣50',
        },
      ],
      h5d_evaluated_count: 1,
      h20d_evaluated_count: 0,
      h5d_avg_return_pct: 3.0,
      h20d_avg_return_pct: null,
      h5d_bm_avg_return_pct: 1.2,
      h20d_bm_avg_return_pct: null,
      h5d_avg_excess_pct: 1.8,
      h20d_avg_excess_pct: null,
    }

    vi.mocked(api.selectionReview.listSnapshots).mockResolvedValue(mockSnapshots as any)
    vi.mocked(api.selectionReview.getSnapshotDetail).mockResolvedValue(mockDetail as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SelectionReview />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getAllByText('高殖利率成長股')[0]).toBeInTheDocument()
    })

    // 點擊卡片進入詳情
    fireEvent.click(screen.getByText('查看評估明細'))

    await waitFor(() => {
      expect(screen.getByText(/高殖利率成長股.*快照詳情/)).toBeInTheDocument()
      expect(screen.getByText('台積電')).toBeInTheDocument()
      expect(screen.getByText('1000.00')).toBeInTheDocument()
      expect(screen.getAllByText('+3.00%')[0]).toBeInTheDocument()
      expect(screen.getAllByText('追蹤中')[0]).toBeInTheDocument()
    })

    // 點擊返回
    fireEvent.click(screen.getByText('返回快照清單'))
    await waitFor(() => {
      expect(screen.getByText('選股復盤與策略追蹤')).toBeInTheDocument()
    })
  })

  it('switches to strategy review stats and displays hit rate (>0%)', async () => {
    const mockStrategyStats = [
      {
        strategy_id: 'value_strat',
        strategy_name: '價值優選策略',
        snapshots_count: 4,
        evaluated_picks_5d: 20,
        evaluated_picks_20d: 15,
        avg_return_5d: 2.85,
        avg_return_20d: 6.12,
        hit_rate_5d: 70.0,
        hit_rate_20d: 66.7,
        hit_rate_definition: '個股期間報酬率 > 0% 之比率',
        bm_excess_5d: 1.45,
        bm_excess_20d: 3.2,
      },
    ]

    vi.mocked(api.selectionReview.listSnapshots).mockResolvedValue([] as any)
    vi.mocked(api.selectionReview.getStrategyStats).mockResolvedValue(mockStrategyStats as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SelectionReview />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    // 切換到策略統計 Tab
    fireEvent.click(screen.getByText('策略統計'))

    await waitFor(() => {
      expect(screen.getByText('價值優選策略')).toBeInTheDocument()
      expect(screen.getByText('70.0%')).toBeInTheDocument()
      expect(screen.getByText('+2.85%')).toBeInTheDocument()
      expect(screen.getByText(/個股期間報酬率大於 0% 之比率/)).toBeInTheDocument()
    })
  })

  it('switches to condition review stats and handles insufficient sample alert', async () => {
    const mockConditionStats = [
      {
        condition_label: '投信連 3 買',
        sample_count_5d: 12,
        sample_count_20d: 10,
        is_sample_sufficient: true,
        avg_return_5d: 3.2,
        avg_return_20d: 5.8,
        hit_rate_5d: 66.7,
        hit_rate_20d: 70.0,
        disclaimer: '僅為歷史樣本敘述性統計',
      },
      {
        condition_label: '低本益比轉機',
        sample_count_5d: 3,
        sample_count_20d: 2,
        is_sample_sufficient: false,
        avg_return_5d: 1.1,
        avg_return_20d: -0.5,
        hit_rate_5d: 33.3,
        hit_rate_20d: 0.0,
        disclaimer: '僅為歷史樣本敘述性統計',
      },
    ]

    vi.mocked(api.selectionReview.listSnapshots).mockResolvedValue([] as any)
    vi.mocked(api.selectionReview.getConditionStats).mockResolvedValue(mockConditionStats as any)

    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SelectionReview />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    // 切換到條件成效 Tab
    fireEvent.click(screen.getByText('條件成效'))

    await waitFor(() => {
      expect(screen.getByText('投信連 3 買')).toBeInTheDocument()
      expect(screen.getByText('樣本充分')).toBeInTheDocument()
      expect(screen.getByText('低本益比轉機')).toBeInTheDocument()
      expect(screen.getByText('樣本不足 (<5)')).toBeInTheDocument()
      expect(screen.getByText(/此處僅呈現歷史入選條件之/)).toBeInTheDocument()
    })
  })
})
