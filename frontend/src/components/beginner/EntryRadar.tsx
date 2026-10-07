import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type BeginnerSelectionState, type RadarItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { buildPortfolioPositions, isTaiwanPortfolioSymbol, PORTFOLIO_CHANGED, readPortfolioLedger } from '@/lib/portfolio'
import { CompareBar, PickCard, useCompareSelection } from './BeginnerPicker'

const GROUPS: { state: BeginnerSelectionState; title: string; hint: string; tone: string }[] = [
  { state: 'watch', title: '可觀察', hint: '價格已在承接區附近，可以開始留意。', tone: 'bg-accent' },
  { state: 'wait_pullback', title: '等拉回', hint: '股票偏強但離高點近，等價格回到承接區。', tone: 'bg-warning' },
  { state: 'wait_breakout', title: '等突破', hint: '等價格站上突破價，再重新評估。', tone: 'bg-warning' },
  { state: 'no_chase', title: '不追', hint: '短線漲多，現在追價風險高，等拉回。', tone: 'bg-danger' },
  { state: 'skip', title: '你的股票：暫不操作', hint: '持股或自選股目前條件不足，先看原因。', tone: 'bg-muted' },
]

/**
 * Holdings still live in the browser ledger until the portfolio moves to the backend (P1).
 * An invalid ledger is reported, never trimmed: a partial holdings list would look normal but be wrong.
 */
function readHoldings(): { symbols: string[]; error: string | null } {
  const { transactions, error } = readPortfolioLedger()
  if (error) return { symbols: [], error }
  return {
    symbols: buildPortfolioPositions(transactions)
      .filter(position => position.shares > 0 && isTaiwanPortfolioSymbol(position.symbol))
      .map(position => position.symbol.toUpperCase()),
    error: null,
  }
}

export function useEntryRadar() {
  const [holdings, setHoldings] = useState(readHoldings)
  useEffect(() => {
    const refresh = () => setHoldings(readHoldings())
    window.addEventListener(PORTFOLIO_CHANGED, refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(PORTFOLIO_CHANGED, refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])
  const query = useQuery({
    queryKey: QK.beginnerRadar(holdings.symbols),
    queryFn: () => api.beginnerRadar(holdings.symbols),
    staleTime: 15 * 1000,
    refetchInterval: q => (q.state.data?.market_session === 'open' ? 30 * 1000 : 5 * 60 * 1000),
  })
  return { query, ledgerError: holdings.error }
}

export function EntryRadarBoard({ items }: { items: RadarItem[] }) {
  const compare = useCompareSelection(items.map(item => item.candidate.symbol))
  return (
    <div className="min-w-0 space-y-2">
      <CompareBar {...compare} />
      {GROUPS.map(group => {
        const groupItems = items.filter(item => item.candidate.selection_state === group.state)
        if (groupItems.length === 0) return null
        return (
          <section key={group.state} aria-label={group.title} className="min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-baseline gap-x-2 px-0.5">
              <span className={`inline-block h-2.5 w-2.5 rounded-full ${group.tone}`} aria-hidden />
              <h2 className="text-sm font-semibold text-foreground">{group.title}（{groupItems.length}）</h2>
              <p className="text-xs text-muted">{group.hint}</p>
            </div>
            <div className="grid min-w-0 grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {groupItems.map(item => (
                <PickCard key={item.candidate.symbol} candidate={item.candidate}
                  comparison={compare.comparisonFor(item.candidate.symbol)}
                  radar={{ live: item.live, sources: item.sources }} />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
