import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { DailyBrief } from './DailyBrief'
import { api } from '@/lib/api'
import { PORTFOLIO_CHANGED } from '@/lib/portfolio'

vi.mock('@/lib/api', () => ({
  api: {
    dailyBrief: {
      buildDailyBrief: vi.fn(),
      generateAiSummary: vi.fn(),
      saveDailyBrief: vi.fn(),
      listHistory: vi.fn(),
      getHistoryDetail: vi.fn(),
    },
    selectionReview: {
      saveSnapshot: vi.fn(),
      listForwardBatches: vi.fn(),
      getForwardBatchStats: vi.fn(),
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
    schema_version: 2,
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
      strategy_status: 'available',
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
    localStorage.removeItem('portfolio_transactions')
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
    vi.mocked(api.dailyBrief.listHistory).mockResolvedValue([] as any)
    vi.mocked(api.selectionReview.listForwardBatches).mockResolvedValue([] as any)
    vi.mocked(api.selectionReview.getForwardBatchStats).mockResolvedValue({ batches_count: 0, picks_count: 0 } as any)
  })

  it('shows missing cached news instead of implying there was no news', async () => {
    const statusMessage = '新聞僅使用本機快取；部分標的尚無有效新聞資料，可至個股新聞重新整理。'
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue({
      ...buildMockBrief(),
      news: { items: [], status: 'unavailable', missing_symbols: ['2330.TWSE'], status_message: statusMessage },
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText(statusMessage)).toBeInTheDocument()
    expect(screen.queryByText('今日尚無相關個股新聞')).not.toBeInTheDocument()
    expect(api.dailyBrief.generateAiSummary).not.toHaveBeenCalled()
  })

  it('keeps missing official event coverage distinct from no risk', async () => {
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue({
      ...buildMockBrief(),
      events: { risk_events: [], attention_events: [], status: 'unavailable', status_message: '官方事件快取缺漏或過期，無法確認完整風險。' },
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('尚無完整官方事件資料，風險未確認')).toBeInTheDocument()
    expect(screen.getByText('官方事件快取缺漏或過期，無法確認完整風險。')).toBeInTheDocument()
    expect(screen.queryByText('今日無重大風險處置或事件')).not.toBeInTheDocument()
  })

  it('shows market facts before the slow strategy completes, then replaces the preview', async () => {
    const base = buildMockBrief()
    const preview = { ...base, candidates: { ...base.candidates, strategy_status: 'not_requested', strategy_matches: [] } }
    let finish!: (value: any) => void
    const delayed = new Promise<any>(resolve => { finish = resolve })
    vi.mocked(api.dailyBrief.buildDailyBrief).mockImplementation(body => body.include_strategy_matches === false ? Promise.resolve(preview as any) : delayed)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('22150.80')).toBeInTheDocument()
    expect(screen.getByText('策略候選載入中；市場與持股資料已可先閱讀。')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0]).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存本次為研究快照' })).toBeDisabled()
    expect(api.dailyBrief.buildDailyBrief).toHaveBeenCalledWith({ target_date: '2026-08-28', portfolio_holdings: [], include_strategy_matches: true })
    await act(async () => finish({ ...base, candidates: { ...base.candidates, strategy_status: 'available', strategy_matches: [{ ...base.candidates.new_top10[0], symbol: '2454.TWSE', name: '策略已完成', reason_type: 'strategy_match' }] } }))
    expect(await screen.findByText('策略已完成')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0]).not.toBeDisabled())
    expect(screen.getByRole('button', { name: '保存本次為研究快照' })).not.toBeDisabled()
  })

  it('keeps preview on strategy failure and allows AI and saving with partial status', async () => {
    const base = buildMockBrief()
    vi.mocked(api.dailyBrief.buildDailyBrief).mockImplementation(body => body.include_strategy_matches === false
      ? Promise.resolve({ ...base, candidates: { ...base.candidates, strategy_status: 'not_requested' } } as any)
      : Promise.reject(new Error('strategy offline')))
    vi.mocked(api.dailyBrief.generateAiSummary).mockResolvedValue({ section_a_market: '部分資料分析', section_b_key_changes: [], section_c_portfolio: '', section_d_watchlist: '', section_e_candidates: '', section_f_risks: '', section_g_tracking: '', evidence_sources: [] })
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('策略候選載入失敗；可先使用其他已就緒資料分析。')).toBeInTheDocument()
    expect(screen.getByText('22150.80')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存本次為研究快照' })).toBeDisabled()
    const aiButton = screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0]
    expect(aiButton).not.toBeDisabled()
    fireEvent.click(aiButton)
    expect(await screen.findByText('部分資料分析')).toBeInTheDocument()
    expect(api.dailyBrief.generateAiSummary).toHaveBeenCalledWith(expect.objectContaining({ candidates: expect.objectContaining({ strategy_status: 'unavailable' }) }))
    fireEvent.click(screen.getByRole('button', { name: '保存此份' }))
    await waitFor(() => expect(api.dailyBrief.saveDailyBrief).toHaveBeenCalledWith(expect.objectContaining({ structured_brief: expect.objectContaining({ candidates: expect.objectContaining({ strategy_status: 'unavailable' }) }) })))
  })

  it('blocks research snapshots with missing prices while keeping completed AI analysis available', async () => {
    const base = buildMockBrief()
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue({ ...base, candidates: { ...base.candidates, strategy_status: 'available', new_top10: [{ ...base.candidates.new_top10[0], close: null }] } } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('候選股缺少有效價格，暫時無法保存研究快照。')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0]).not.toBeDisabled())
    const save = screen.getByRole('button', { name: '保存本次為研究快照' })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(api.selectionReview.saveSnapshot).not.toHaveBeenCalled()
  })

  it('does not pair a partial AI result with a retried complete brief', async () => {
    const base = buildMockBrief()
    let fullCalls = 0
    vi.mocked(api.dailyBrief.buildDailyBrief).mockImplementation(body => {
      if (!body.include_strategy_matches) return Promise.resolve({ ...base, candidates: { ...base.candidates, strategy_status: 'not_requested' } } as any)
      return ++fullCalls === 1 ? Promise.reject(new Error('offline')) : Promise.resolve(base as any)
    })
    vi.mocked(api.dailyBrief.generateAiSummary).mockResolvedValue({ section_a_market: '舊部分資料分析', section_b_key_changes: [], section_c_portfolio: '', section_d_watchlist: '', section_e_candidates: '', section_f_risks: '', section_g_tracking: '', evidence_sources: [] })
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    await screen.findByRole('button', { name: '重試策略候選' })
    fireEvent.click(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0])
    expect(await screen.findByText('舊部分資料分析')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試策略候選' }))
    await waitFor(() => expect(screen.queryByText('舊部分資料分析')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '保存此份' })).not.toBeInTheDocument()
  })

  it('ignores late AI output from a previous holdings context', async () => {
    const base = buildMockBrief()
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(base as any)
    let finish!: (value: any) => void
    vi.mocked(api.dailyBrief.generateAiSummary).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    await waitFor(() => expect(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0]).not.toBeDisabled())
    fireEvent.click(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0])
    localStorage.setItem('portfolio_transactions', JSON.stringify([{ id: 'changed', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 1000, price: 100, fee: 0, date: '2026-08-03', createdAt: '2026-08-03T10:00:00Z' }]))
    fireEvent(window, new Event(PORTFOLIO_CHANGED))
    await waitFor(() => expect(api.dailyBrief.buildDailyBrief).toHaveBeenCalledWith(expect.objectContaining({ portfolio_holdings: [expect.objectContaining({ shares: 1000 })] })))
    await act(async () => finish({ section_a_market: '過期持股分析', section_b_key_changes: [], section_c_portfolio: '', section_d_watchlist: '', section_e_candidates: '', section_f_risks: '', section_g_tracking: '', evidence_sources: [] }))
    expect(screen.queryByText('過期持股分析')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存此份' })).not.toBeInTheDocument()
  })

  it('keeps unknown history incomplete while allowing its AI independently of live loading', async () => {
    const base = buildMockBrief()
    vi.mocked(api.dailyBrief.buildDailyBrief).mockImplementation(body => body.include_strategy_matches === false ? Promise.resolve(base as any) : new Promise(() => {}))
    vi.mocked(api.dailyBrief.listHistory).mockResolvedValue([{ brief_id: 'unknown', brief_date: '2026-08-27', structured_brief: { ...base, candidates: { ...base.candidates, strategy_status: 'unknown' } }, ai_summary: null }] as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('22150.80')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '歷史日報 (1)' }))
    fireEvent.click(screen.getByText('2026-08-27 每日摘要'))
    expect(screen.getByText('策略候選完整性未知；可分析已知資料，但無法保存完整研究快照。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存本次為研究快照' })).toBeDisabled()
    expect(screen.getAllByRole('button', { name: '產生今日 AI 摘要' })[0]).not.toBeDisabled()
  })

  it('renders deterministic market facts and candidates correctly', async () => {
    const mockBrief = buildMockBrief()
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(mockBrief as any)

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
      expect(screen.getByText('半導體業 +1.9%')).toBeInTheDocument()
      expect(screen.getByText('+1.80%')).toBeInTheDocument()
      expect(screen.getByText('偏多')).toBeInTheDocument()
      expect(screen.getAllByText('台積電')[0]).toBeInTheDocument()
      expect(screen.getByText(/處置有價證券/)).toBeInTheDocument()
      expect(screen.getByText('尚未產生今日 AI 深度摘要')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: '尚無正式批次，查看狀態' })).toHaveAttribute('href', '/selection-review?tab=forward')
    })
  })

  it('links directly to the newest official forward batch without triggering AI', async () => {
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(buildMockBrief() as any)
    vi.mocked(api.selectionReview.listForwardBatches).mockResolvedValue([{
      snapshot_id: 'forward_latest_1', record_type: 'forward_batch',
    }] as any)

    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)

    const link = await screen.findByRole('link', { name: '查看最新正式前瞻批次' })
    // 持股存在本機帳本，必須隨請求送出（帳本為空時送 []，讀不到時送 null）
    expect(api.dailyBrief.buildDailyBrief).toHaveBeenCalledWith(expect.objectContaining({ portfolio_holdings: [] }))
    expect(link).toHaveAttribute('href', '/selection-review?tab=forward&batch_id=forward_latest_1')
    expect(api.dailyBrief.generateAiSummary).not.toHaveBeenCalled()
  })

  it('sends ledger holdings and refreshes after a ledger change', async () => {
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(buildMockBrief() as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    await waitFor(() => expect(api.dailyBrief.buildDailyBrief).toHaveBeenCalledWith(expect.objectContaining({ portfolio_holdings: [] })))

    localStorage.setItem('portfolio_transactions', JSON.stringify([{
      id: 'buy-1', symbol: '2330.TWSE', name: '台積電', side: 'buy', shares: 1000,
      price: 100, fee: 0, date: '2026-08-03', createdAt: '2026-08-03T10:00:00Z',
    }]))
    fireEvent(window, new Event(PORTFOLIO_CHANGED))
    await waitFor(() => expect(api.dailyBrief.buildDailyBrief).toHaveBeenLastCalledWith(expect.objectContaining({
      portfolio_holdings: [{ symbol: '2330.TWSE', name: '台積電', shares: 1000, average_cost: 100 }],
    })))
  })

  it('does not present a failed official batch query as an empty result', async () => {
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(buildMockBrief() as any)
    vi.mocked(api.selectionReview.listForwardBatches).mockRejectedValueOnce(new Error('offline'))

    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByRole('link', { name: '正式批次載入失敗，查看重試' })).toHaveAttribute('href', '/selection-review?tab=forward')
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

    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(mockBrief as any)
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
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(mockBrief as any)
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

  it('renders negative sector and candidate percentages with one sign and keeps null unknown', async () => {
    const brief = buildMockBrief()
    brief.market.strongest_sectors[0].change_pct = -10
    brief.candidates.new_top10[0].change_pct = -10
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue({
      ...brief,
      candidates: {
        ...brief.candidates,
        strategy_matches: [{
          ...brief.candidates.new_top10[0], symbol: '2454.TWSE', name: '缺少漲跌幅',
          reason_type: 'strategy_match', change_pct: null,
        }],
      },
    } as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('半導體業 -10.0%')).toBeInTheDocument()
    expect(screen.getByText('-10.00%')).toBeInTheDocument()
    const missingCard = screen.getByText('缺少漲跌幅').closest('.bg-card') as HTMLElement
    expect(within(missingCard).getByText('—')).toHaveClass('text-muted-foreground')
    expect(screen.queryByText('+-%')).not.toBeInTheDocument()
    expect(screen.queryByText(/\+-10/)).not.toBeInTheDocument()
  })

  it('labels corrected historical numbers without replacing saved AI text', async () => {
    const brief = buildMockBrief()
    vi.mocked(api.dailyBrief.buildDailyBrief).mockResolvedValue(brief as any)
    vi.mocked(api.dailyBrief.listHistory).mockResolvedValue([{
      brief_id: 'legacy', brief_date: '2026-08-27', structured_brief: brief,
      legacy_unit_normalized: true,
      ai_summary: {
        section_a_market: '原始 AI 文字保留', section_b_key_changes: [], section_c_portfolio: '',
        section_d_watchlist: '', section_e_candidates: '', section_f_risks: '', section_g_tracking: '',
        evidence_sources: [],
      },
    }] as any)
    render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter><DailyBrief /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '歷史日報 (1)' }))
    fireEvent.click(screen.getByText('2026-08-27 每日摘要'))
    expect(await screen.findByText('原始 AI 文字保留')).toBeInTheDocument()
    expect(screen.getByText('歷史數值已校正百分比單位；原 AI 文字未重新計算，請以客觀數值為準。')).toBeInTheDocument()
    expect(screen.getByText('半導體業 +1.9%')).toBeInTheDocument()
    expect(api.dailyBrief.generateAiSummary).not.toHaveBeenCalled()
  })

})
