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

describe('TaiwanAlertsList change_pct', () => {
  it('formats the alert-event ratio as a percentage for rule and anomaly alerts', () => {
    render(<TaiwanAlertsList
      alerts={[
        { ts: 2, alert_id: 'anomaly-1', source: 'watchlist_anomaly', type: 'watchlist_anomaly',
          symbol: '2317.TWSE', name: '鴻海', message: '自選股大跌 -5.65%，現價 200', change_pct: -0.0565 },
        { ts: 1, alert_id: 'rule-1', source: 'twse:mis', type: 'change_pct_above',
          symbol: '2330.TWSE', name: '台積電', message: '漲幅超過門檻', change_pct: 0.0366 },
      ]}
    />)

    expect(screen.getByText('-5.65%')).toBeInTheDocument()
    expect(screen.getByText('+3.66%')).toBeInTheDocument()
    expect(screen.queryByText('-0.06%')).not.toBeInTheDocument()
  })
})
