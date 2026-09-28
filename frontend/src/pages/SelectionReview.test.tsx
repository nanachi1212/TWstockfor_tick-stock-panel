import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { SelectionReview } from './SelectionReview'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    selectionReview: {
      listSnapshots: vi.fn(),
      getSnapshotDetail: vi.fn(),
      listForwardBatches: vi.fn(),
      getForwardBatchDetail: vi.fn(),
      getForwardBatchStats: vi.fn(),
      lockForwardBatch: vi.fn(),
      deleteSnapshot: vi.fn(),
      getStrategyStats: vi.fn(),
      getConditionStats: vi.fn(),
    },
    taiwanQuantLiveModels: vi.fn(),
    taiwanQuantLiveRuns: vi.fn(),
    taiwanQuantLiveRun: vi.fn(),
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

function BackNavigationControl() {
  const navigate = useNavigate()
  return <button type="button" onClick={() => navigate(-1)}>返回前一頁</button>
}

describe('SelectionReview Page (A12)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.selectionReview.listSnapshots).mockResolvedValue([] as any)
    vi.mocked(api.selectionReview.listForwardBatches).mockResolvedValue([] as any)
    vi.mocked(api.selectionReview.getForwardBatchStats).mockResolvedValue({
      batches_count: 0, picks_count: 0,
      h1d_evaluated_count: 0, h1d_pending_count: 0, h1d_unavailable_count: 0, h1d_hit_rate_pct: null, h1d_avg_return_pct: null, h1d_bm_evaluated_count: 0, h1d_bm_avg_return_pct: null, h1d_excess_evaluated_count: 0, h1d_avg_excess_pct: null, h1d_reference_close_evaluated_count: 0, h1d_reference_close_avg_return_pct: null,
      h5d_evaluated_count: 0, h5d_pending_count: 0, h5d_unavailable_count: 0, h5d_hit_rate_pct: null, h5d_avg_return_pct: null, h5d_bm_evaluated_count: 0, h5d_bm_avg_return_pct: null, h5d_excess_evaluated_count: 0, h5d_avg_excess_pct: null, h5d_reference_close_evaluated_count: 0, h5d_reference_close_avg_return_pct: null,
      h20d_evaluated_count: 0, h20d_pending_count: 0, h20d_unavailable_count: 0, h20d_hit_rate_pct: null, h20d_avg_return_pct: null, h20d_bm_evaluated_count: 0, h20d_bm_avg_return_pct: null, h20d_excess_evaluated_count: 0, h20d_avg_excess_pct: null, h20d_reference_close_evaluated_count: 0, h20d_reference_close_avg_return_pct: null,
      hit_rate_definition: '未四捨五入報酬率 > 0%',
      horizons: {}, timeline: [],
    } as any)
    vi.mocked(api.taiwanQuantLiveRuns).mockResolvedValue({ runs: [] } as any)
    vi.mocked(api.taiwanQuantLiveModels).mockResolvedValue({ recommendation_status: 'unavailable', recommendation_reason: 'live_run_missing' } as any)
  })

  it('shows immutable live recommendation status and strict-positive horizon outcomes', async () => {
    vi.mocked(api.taiwanQuantLiveRuns).mockResolvedValue({ runs: [{
      model_key: 'live-model', session: '2026-09-23', snapshot_hash: 'hash',
      frozen_at: '2026-09-23T16:30:00+08:00', signal_count: 1,
      recommendation_status: 'tracking',
    }] } as any)
    vi.mocked(api.taiwanQuantLiveRun).mockResolvedValue({
      model_key: 'live-model', session: '2026-09-23', snapshot_hash: 'hash', frozen_at: '2026-09-23T16:30:00+08:00',
      audit_status: 'ok', recommendation_status: 'tracking', recommendation_reason: 'current',
      snapshot: { signal_session: '2026-09-23', data_cutoff: '2026-09-23T17:00:00+08:00', usage_scope: 'experimental_live', validation_state: 'unvalidated', model: { version: 'v1', top_n: 10, validation_state: 'unvalidated' },
        signals: [{ symbol: '2330.TWSE', name: '台積電', rank: 1, score: 0.9, selected: true, reference_close: 100, feature_percentiles: {}, reason_summary: '進入既有 Top 10。' }], features: [] },
      outcome_summary: {
        '1D': { horizon: '1D', evaluated_count: 1, pending_count: 0, unavailable_count: 0, hit_count: 1, hit_rate_pct: 100, average_return_pct: 2 },
        '5D': { horizon: '5D', evaluated_count: 0, pending_count: 1, unavailable_count: 0, hit_count: 0, hit_rate_pct: null, average_return_pct: null },
        '20D': { horizon: '20D', evaluated_count: 0, pending_count: 1, unavailable_count: 0, hit_count: 0, hit_rate_pct: null, average_return_pct: null },
      }, outcomes: [{ symbol: '2330.TWSE', horizon: 1, status: 'verified', value: 0.02, reason: null }],
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=live']}><SelectionReview /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByRole('heading', { name: '每日推薦與命中率' })).toBeInTheDocument()
    expect((await screen.findAllByText('追蹤中')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /2026-09-23/ }))
    expect(await screen.findByRole('link', { name: /台積電/ })).toBeInTheDocument()
    expect(await screen.findByText('100.0%／1')).toBeInTheDocument()
    expect(screen.getAllByText('追蹤中').length).toBeGreaterThan(0)
  })

  it('shows current-live unavailable status separately from an available zero-candidate run', async () => {
    vi.mocked(api.taiwanQuantLiveModels).mockResolvedValue({
      recommendation_status: 'unavailable', recommendation_reason: 'daily_refresh_not_ready',
      live_readiness: { status: 'unavailable', source: 'current_live_gate', reasons: ['daily_refresh_not_ready'] },
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=live']}><SelectionReview /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByText(/資料不可用（daily_refresh_not_ready）/)).toBeInTheDocument()
    expect(screen.getByText('尚無正式推薦快照；資料不足時不以歷史結果代替。')).toBeInTheDocument()
  })

  it('keeps official forward batches separate and does not offer delete or reselection', async () => {
    const qc = createTestQueryClient()
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/selection-review?tab=forward']}>
          <SelectionReview />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('heading', { name: 'Forward Selection Performance Center' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '尚無正式前瞻批次' })).toBeInTheDocument()
    expect(screen.queryByTitle('刪除快照')).not.toBeInTheDocument()
  })

  it('renders cohort metrics with zero and pending values without changing the backend denominator', async () => {
    const summary = {
      batch_count: 1, matured_batch_count: 0, pick_count: 2, evaluable_count: 1,
      pending_count: 1, unavailable_count: 0, positive_return_count: 0,
      hit_rate: 0, average_return_pct: 0, median_return_pct: 0,
      benchmark_evaluable_count: 1, average_benchmark_return_pct: -1,
      excess_evaluable_count: 1, average_excess_return_pct: 1,
      median_excess_return_pct: 1, beat_benchmark_count: 1, beat_benchmark_rate: 100,
    }
    vi.mocked(api.selectionReview.getForwardBatchStats).mockResolvedValue({
      batches_count: 1, picks_count: 2, horizons: {
        '1D': { top10: summary, full_batch: summary },
        '5D': { top10: summary, full_batch: summary },
        '20D': { top10: summary, full_batch: summary },
      }, timeline: [],
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=forward']}><SelectionReview /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByRole('heading', { name: 'Forward Selection Performance Center' })).toBeInTheDocument()
    expect(screen.getAllByText('Top10').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Top20／整批').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/0\.00%/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('100.0%').length).toBeGreaterThan(0)
    expect(screen.getByText('尚無正式前瞻批次')).toBeInTheDocument()
  })

  it('shows retry state when cumulative forward stats request fails', async () => {
    vi.mocked(api.selectionReview.getForwardBatchStats).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ batches_count: 0, picks_count: 0 } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=forward']}><SelectionReview /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByRole('alert')).toHaveTextContent('前瞻績效統計載入失敗')
    fireEvent.click(screen.getByRole('button', { name: '重新載入' }))
    expect(await screen.findByRole('heading', { name: '尚無正式前瞻批次' })).toBeInTheDocument()
  })

  it('shows a retry state when the formal batch list request fails', async () => {
    vi.mocked(api.selectionReview.listForwardBatches).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([] as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=forward']}><SelectionReview /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByRole('alert')).toHaveTextContent('尚無法確認是否有批次')
    expect(screen.queryByRole('heading', { name: '尚無正式前瞻批次' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重新載入' }))
    expect(await screen.findByRole('heading', { name: '尚無正式前瞻批次' })).toBeInTheDocument()
  })

  it('follows browser history changes to the URL-selected tab', async () => {
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=snapshots', '/selection-review?tab=forward']}><BackNavigationControl /><SelectionReview /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByRole('region', { name: '正式前瞻批次' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回前一頁' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: '正式前瞻批次' })).not.toBeInTheDocument())
  })

  it('loads selected formal batch detail from the real forward batch query', async () => {
    vi.mocked(api.selectionReview.listForwardBatches).mockResolvedValue([{
      snapshot_id: 'forward_trend_liquidity_v1_20260925', created_at: '2026-09-25T08:00:00+08:00',
      strategy_id: 'trend_liquidity_v1', strategy_name: '趨勢流動性 v1', as_of_date: '2026-09-25',
      selected_count: 1, record_type: 'forward_batch', source_data_date: '2026-09-25',
      target_trade_date: '2026-09-26', rule_version: 'trend_liquidity_v1',
    }] as any)
    vi.mocked(api.selectionReview.getForwardBatchDetail).mockResolvedValue({
      snapshot: { snapshot_id: 'forward_trend_liquidity_v1_20260925', record_type: 'forward_batch', items: [{ symbol: '2330.TWSE', name: '台積電', rank: 1, price: 1000 }], locked_at: '2026-09-25T08:00:00+08:00', target_trade_date: '2026-09-28', target_trade_date_status: 'scheduled_unverified', rule_version: 'trend_liquidity_v1', price_adjustment: 'split_adjusted_price', cost_assumption: '未扣成本與滑價；紙上開盤價不保證成交' },
      evaluated_items: [{
        symbol: '2330.TWSE', name: '台積電', rank: 1, entry_price: 1000, paper_entry_price: 1010,
        entry_status: 'completed', h1d_reference_close_return_pct: null, h1d_reference_close_status: 'pending', h1d_return_pct: null, h1d_status: 'pending', h1d_bm_return_pct: null, h1d_excess_pct: null,
        h5d_reference_close_return_pct: null, h5d_reference_close_status: 'pending', h5d_return_pct: null, h5d_status: 'pending', h5d_bm_return_pct: null, h5d_excess_pct: null,
        h20d_reference_close_return_pct: null, h20d_reference_close_status: 'pending', h20d_return_pct: null, h20d_status: 'pending', h20d_bm_return_pct: null, h20d_excess_pct: null,
      }],
      h1d_evaluated_count: 0, h1d_avg_return_pct: null, h1d_bm_avg_return_pct: null, h1d_avg_excess_pct: null, h1d_bm_evaluated_count: 0, h1d_excess_evaluated_count: 0, h1d_reference_close_evaluated_count: 0, h1d_reference_close_avg_return_pct: null, h1d_pending_count: 1, h1d_unavailable_count: 0,
      h5d_evaluated_count: 0, h5d_pending_count: 1, h5d_unavailable_count: 0,
      h20d_evaluated_count: 0, h20d_pending_count: 1, h20d_unavailable_count: 0,
      h5d_reference_close_evaluated_count: 0, h5d_reference_close_avg_return_pct: null, h5d_bm_evaluated_count: 0, h5d_excess_evaluated_count: 0,
      h20d_reference_close_evaluated_count: 0, h20d_reference_close_avg_return_pct: null, h20d_bm_evaluated_count: 0, h20d_excess_evaluated_count: 0,
      h5d_avg_return_pct: null, h20d_avg_return_pct: null,
      h5d_bm_avg_return_pct: null, h20d_bm_avg_return_pct: null,
      h5d_avg_excess_pct: null, h20d_avg_excess_pct: null,
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/selection-review?tab=forward&batch_id=forward_trend_liquidity_v1_20260925']}><SelectionReview /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('台積電 (2330.TWSE)')).toBeInTheDocument()
    expect(api.selectionReview.getForwardBatchDetail).toHaveBeenCalledWith('forward_trend_liquidity_v1_20260925')
    expect(api.selectionReview.getForwardBatchDetail).toHaveBeenCalledTimes(1)
    expect(api.selectionReview.lockForwardBatch).not.toHaveBeenCalled()
    expect(screen.getByLabelText('正式前瞻批次')).toHaveTextContent('1010.00')
    expect(screen.getAllByText('追蹤中').length).toBeGreaterThan(0)
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
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
