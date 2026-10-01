import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MarketEvent } from '@/lib/api'
import { MopsEventEvidence } from './MopsEventEvidence'

const base: MarketEvent = {
  id: 'test', symbol: '2330.TWSE', code: '2330', name: '測試公司', exchange: 'TWSE',
  event_date: '2026-10-01', event_type: 'insider_transfer_declaration', event_type_label: '內部人持股轉讓申報',
  severity: 'info', title: '持股轉讓事前申報', summary: '事前申報', source: 'mops:transfer:TWSE',
  retrieved_at: '2026-10-01T08:00:00+08:00', published_at: null, available_at: null,
  status: 'data_insufficient', freshness: 'fresh',
}

describe('MOPS evidence shared by product surfaces', () => {
  it('clearly distinguishes declarations from execution and never uses report date as publication', () => {
    render(<MopsEventEvidence event={base} />)
    expect(screen.getByText(/不代表實際成交或已賣出/)).toBeInTheDocument()
    expect(screen.getByText('公告時間：無法確認；可得時間：無法確認')).toBeInTheDocument()
    expect(screen.getByText(/data_insufficient/)).toBeInTheDocument()
  })

  it('shows official presentation links and scheduled time separately', () => {
    render(<MopsEventEvidence event={{ ...base, event_type: 'investor_conference', details: {
      scheduled_time: '14:00', place: '線上', presentation_links: [
        { label: '中文簡報', url: 'https://mopsov.twse.com.tw/test.pdf' },
        { label: '假簡報', url: 'https://mops.twse.com.tw.evil.example/test.pdf' },
      ],
    } }} />)
    expect(screen.getByText(/舉行時間：2026-10-01 14:00/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '官方中文簡報' })).toHaveAttribute('href', 'https://mopsov.twse.com.tw/test.pdf')
    expect(screen.queryByRole('link', { name: /假簡報/ })).not.toBeInTheDocument()
    expect(screen.getByText(/舉行時間不等於公告時間/)).toBeInTheDocument()
  })

  it('shows missing slides and stale data explicitly, without causal claims', () => {
    render(<MopsEventEvidence event={{ ...base, event_type: 'investor_conference', freshness: 'stale' }} />)
    expect(screen.getByText('官方簡報連結尚未提供。')).toBeInTheDocument()
    expect(screen.getByText(/來源失敗，保留舊資料/)).toBeInTheDocument()
    expect(screen.getByText(/不能據此判定因果/)).toBeInTheDocument()
  })

  it('renders deterministic topic and confirmed timestamp in Taipei time', () => {
    render(<MopsEventEvidence event={{ ...base, event_type: 'material_information',
      published_at: '2026-09-30T22:07:06Z', available_at: '2026-10-01T06:07:06+08:00',
      details: { topic_label: '資產交易與投資', clause: '第20款' },
    }} />)
    expect(screen.getByText(/第20款，規則分類/)).toBeInTheDocument()
    expect(screen.getByText(/06:07:06.*06:07:06/)).toBeInTheDocument()
    expect(screen.queryByText(/data_insufficient/)).not.toBeInTheDocument()
  })
})
