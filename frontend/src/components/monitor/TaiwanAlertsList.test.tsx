import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { TaiwanAlertsList } from './TaiwanAlertsList'

describe('TaiwanAlertsList AI Research entry', () => {
  it('passes only the selected Taiwan stock and alert ID to the interpretation action', () => {
    const onInterpretAlert = vi.fn()
    render(<TaiwanAlertsList
      alerts={[{
        ts: 1,
        alert_id: 'alert-1',
        source: 'twse:mis',
        type: 'price_below',
        symbol: '2330.TWSE',
        name: '台積電',
        message: '價格跌破 900',
      }]}
      onInterpretAlert={onInterpretAlert}
    />)

    fireEvent.click(screen.getByRole('button', { name: 'AI 解讀' }))
    expect(onInterpretAlert).toHaveBeenCalledWith('2330.TWSE', 'alert-1')
  })
})
