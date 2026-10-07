import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { TaiwanRulesList } from './TaiwanRulesList'
import { api, type TaiwanMonitorRule } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanRuleUpdate: vi.fn(),
    taiwanRuleDelete: vi.fn(),
    syncPlanRules: vi.fn(),
  },
}))

describe('TaiwanRulesList', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    vi.clearAllMocks()
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
  })

  const sampleRules: TaiwanMonitorRule[] = [
    {
      rule_id: 'auto-1',
      name: '進入承接區',
      symbol: '2330.TWSE',
      rule_type: 'price_below',
      threshold: 1050,
      enabled: true,
      cooldown_seconds: 21600,
      severity: 'warning',
      notify_channels: ['telegram', 'line'],
      source: 'trade_plan',
      plan_identity: 'plan-2330',
      plan_as_of: '2026-10-07',
    },
    {
      rule_id: 'auto-2',
      name: '跌破失效位',
      symbol: '2330.TWSE',
      rule_type: 'price_below',
      threshold: 985,
      enabled: true,
      cooldown_seconds: 21600,
      severity: 'critical',
      notify_channels: [],
      source: 'trade_plan',
      plan_identity: 'plan-2330',
      plan_as_of: '2026-10-07',
    },
    {
      rule_id: 'manual-1',
      name: '自訂突破提醒',
      symbol: '2454.TWSE',
      rule_type: 'price_above',
      threshold: 1200,
      enabled: true,
      cooldown_seconds: 300,
      severity: 'warning',
      source: 'manual',
    },
  ]

  it('renders automatic section with trade_plan rules grouped by stock', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <TaiwanRulesList rules={sampleRules} onEdit={vi.fn()} />
      </QueryClientProvider>,
    )

    // Check automatic section header
    expect(screen.getByText('自動監控（來自承接雷達）')).toBeInTheDocument()
    expect(screen.getByText('2330.TWSE')).toBeInTheDocument()
    expect(screen.getByText('計畫日 2026-10-07')).toBeInTheDocument()
    expect(screen.getByText('進入承接區')).toBeInTheDocument()
    expect(screen.getByText('1,050 元')).toBeInTheDocument()
    expect(screen.getByText('跌破失效位')).toBeInTheDocument()
    expect(screen.getByText('985 元')).toBeInTheDocument()
    expect(screen.getByText('Telegram')).toBeInTheDocument()
    expect(screen.getByText('LINE')).toBeInTheDocument()
    expect(screen.getByText('站內通知')).toBeInTheDocument()

    // Check manual section exists
    expect(screen.getByText('自訂監控規則')).toBeInTheDocument()
    expect(screen.getByText('2454.TWSE')).toBeInTheDocument()
    expect(screen.getByText('自訂突破提醒')).toBeInTheDocument()
    expect(screen.getAllByTitle('編輯規則')).toHaveLength(1)
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/trade_plan|price_below|PRICE_BELOW|warning|critical/)

  })

  it('allows disabling an automatic rule via disable toggle', async () => {
    vi.mocked(api.taiwanRuleUpdate).mockResolvedValue({
      ok: true,
      rule: { ...sampleRules[0], enabled: false },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <TaiwanRulesList rules={sampleRules} onEdit={vi.fn()} />
      </QueryClientProvider>,
    )

    // The first rule is enabled; clicking its toggle should send enabled: false
    const toggleButtons = screen.getAllByTitle('點擊停用')
    expect(toggleButtons.length).toBeGreaterThan(0)
    fireEvent.click(toggleButtons[0])

    await waitFor(() => {
      expect(api.taiwanRuleUpdate).toHaveBeenCalledWith('auto-1', { enabled: false })
    })
  })

  it('calls syncPlanRules and refreshes the query on 立即同步 click', async () => {
    vi.mocked(api.syncPlanRules).mockResolvedValue({
      created: 2,
      removed: 2,
      skipped: [],
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    render(
      <QueryClientProvider client={queryClient}>
        <TaiwanRulesList rules={sampleRules} onEdit={vi.fn()} />
      </QueryClientProvider>,
    )

    const syncButton = screen.getByRole('button', { name: /立即同步/ })
    fireEvent.click(syncButton)

    await waitFor(() => {
      expect(api.syncPlanRules).toHaveBeenCalledTimes(1)
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: QK.taiwanRules })
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: QK.beginnerRadarRoot })
      expect(screen.getByText(/同步完成：新增 2 筆，移除 2 筆/)).toBeInTheDocument()
    })
  })

  it('displays error in plain Chinese when syncPlanRules fails', async () => {
    vi.mocked(api.syncPlanRules).mockRejectedValue(new Error('自動監控同步失敗，原有規則已保留'))

    render(
      <QueryClientProvider client={queryClient}>
        <TaiwanRulesList rules={sampleRules} onEdit={vi.fn()} />
      </QueryClientProvider>,
    )

    const syncButton = screen.getByRole('button', { name: /立即同步/ })
    fireEvent.click(syncButton)

    await waitFor(() => {
      expect(screen.getByText('自動監控同步失敗，原有規則已保留')).toBeInTheDocument()
    })
  })

  it('shows empty states when no automatic or manual rules exist', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <TaiwanRulesList rules={[]} onEdit={vi.fn()} />
      </QueryClientProvider>,
    )

    expect(screen.getByText('尚未建立自動監控規則')).toBeInTheDocument()
    expect(screen.getByText('尚未建立自訂台股監控規則')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /立即同步/ })).toBeInTheDocument()
  })
  it('shows a Chinese error when toggling fails and retains the enabled state', async () => {
    vi.mocked(api.taiwanRuleUpdate).mockRejectedValueOnce(new Error('internal_warning'))
    render(<QueryClientProvider client={queryClient}>
      <TaiwanRulesList rules={sampleRules} onEdit={vi.fn()} />
    </QueryClientProvider>)
    const toggle = screen.getByRole('button', { name: '停用 2330.TWSE 進入承接區' })
    fireEvent.click(toggle)
    expect(await screen.findByRole('alert')).toHaveTextContent('變更監控狀態失敗')
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(document.body.textContent).not.toContain('internal_warning')
  })

  it('shows loading instead of empty state and disables sync', () => {
    render(<QueryClientProvider client={queryClient}>
      <TaiwanRulesList rules={[]} onEdit={vi.fn()} isLoading />
    </QueryClientProvider>)
    expect(screen.getByRole('status')).toHaveTextContent('正在載入監控規則')
    expect(screen.queryByText('尚未建立自動監控規則')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '立即同步' })).toBeDisabled()
  })

  it('shows query error and retries loading instead of claiming no rules exist', () => {
    const retry = vi.fn()
    render(<QueryClientProvider client={queryClient}>
      <TaiwanRulesList rules={[]} onEdit={vi.fn()} isError onRetry={retry} />
    </QueryClientProvider>)
    expect(screen.getByRole('alert')).toHaveTextContent('監控規則載入失敗')
    expect(screen.queryByText('尚未建立自動監控規則')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試載入' }))
    expect(retry).toHaveBeenCalledOnce()
  })

  it('keeps skipped sync reasons readable without exposing backend enum codes', async () => {
    vi.mocked(api.syncPlanRules).mockResolvedValueOnce({
      created: 0, removed: 2, skipped: [{ symbol: '2330.TWSE', reason: 'trade_plan_unavailable' }],
    })
    render(<QueryClientProvider client={queryClient}>
      <TaiwanRulesList rules={sampleRules} onEdit={vi.fn()} />
    </QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: '立即同步' }))
    expect(await screen.findByRole('status')).toHaveTextContent('略過 1 檔（目前沒有可用的承接計畫）')
    expect(document.body.textContent).not.toContain('trade_plan_unavailable')
  })

})
