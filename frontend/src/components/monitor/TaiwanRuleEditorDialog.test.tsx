import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TaiwanRuleEditorDialog } from './TaiwanRuleEditorDialog'

describe('TaiwanRuleEditorDialog', () => {
  it('keeps an existing rule type fixed while editing', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <TaiwanRuleEditorDialog
          open
          rule={{
            rule_id: 'rule-1',
            name: '價格提醒',
            symbol: '2330.TWSE',
            rule_type: 'price_above',
            threshold: 2500,
            enabled: true,
            cooldown_seconds: 300,
            severity: 'warning',
          }}
          onClose={() => undefined}
        />
      </QueryClientProvider>,
    )

    expect(screen.getByRole('button', { name: /進入 Quant Top 10/ })).toBeDisabled()
    expect(screen.getByText('規則建立後無法更換型態；請新增規則以使用其他提醒條件。')).toBeInTheDocument()
  })
})
