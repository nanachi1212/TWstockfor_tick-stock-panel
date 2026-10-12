import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { ResearchCompareView } from './ResearchCompareView'

vi.mock('@/lib/copy-formatters', () => ({ copyToClipboard: vi.fn().mockResolvedValue(true) }))

// 舊紀錄 (10/07) 資料大量缺失，新紀錄 (10/12) 完整：差異多半來自資料不完整。
const lostFields = Array.from({ length: 12 }, (_, i) => ({ path: `technical_context.f${i}`, before: null, after: i }))
const data = {
  evidence_data_changes: { field_changes: [
    { path: 'price_context.trade_date', before: '2026-10-07', after: '2026-10-12' },
    { path: 'beginner_selection.selection_state', before: 'skip', after: 'watch' },
    { path: 'beginner_selection.technical_panel.institutional.state', before: 'unavailable', after: 'sell' },
    ...lostFields,
  ] },
  model_prompt_config_changes: { field_changes: [] },
  interpretation_report_changes: { field_changes: [
    { path: 'overview', before: '價格資料目前不可用。', after: '台積電於 2026-10-12 收盤 2540 元，當日下跌 0.39%。' },
  ] },
}

describe('ResearchCompareView', () => {
  it('shows old -> new key changes in plain language and warns when one side is incomplete', () => {
    render(<ResearchCompareView data={data} older={{ id: 'a', date: '2026-10-07' }} newer={{ id: 'b', date: '2026-10-12' }} />)
    expect(screen.getByText(/舊紀錄（2026-10-07） → 新紀錄（2026-10-12）/)).toBeInTheDocument()
    const table = screen.getByRole('region', { name: '重點欄位變化' })
    const row = within(table).getByText('選股結論').closest('tr')!
    expect(row).toHaveTextContent('暫時略過')
    expect(row).toHaveTextContent('可列入觀察')
    expect(within(table).getByText('法人 5 日').closest('tr')).toHaveTextContent('偏賣')
    expect(screen.getByRole('note')).toHaveTextContent('舊紀錄有大量資料缺失')
    expect(screen.getByText(/新：台積電於 2026-10-12/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /複製給 AI 比較/ })).toBeInTheDocument()
  })
})
