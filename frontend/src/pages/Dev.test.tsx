import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { Dev } from './Dev'

vi.mock('@/lib/api', () => ({ api: { klineMinute: vi.fn().mockResolvedValue({ rows: [], source: 'none' }) } }))

describe('Taiwan data diagnostics', () => {
  it('keeps read-only Taiwan minute probing without legacy sample or ladder mutations', async () => {
    render(<Dev />)
    expect(screen.queryByText('演示資料')).not.toBeInTheDocument()
    expect(screen.queryByText('封單監控')).not.toBeInTheDocument()
    expect(screen.queryByText('生成演示規則')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('2330.TWSE')).toHaveValue('2330.TWSE')
    expect(screen.getByText(/歷史分鐘資料需先設定/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: '開始探測' }))
    await waitFor(() => expect(api.klineMinute).toHaveBeenCalledTimes(1))
    expect(api.klineMinute).toHaveBeenCalledWith('2330.TWSE', expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/))
    await screen.findByText('none')
  })
})
