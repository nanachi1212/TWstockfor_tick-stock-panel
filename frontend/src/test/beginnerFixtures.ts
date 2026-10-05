import type { BeginnerCandidate, BeginnerSelectionResponse } from '@/lib/api'

export function beginnerCandidate(symbol: string, overrides: Partial<BeginnerCandidate> = {}): BeginnerCandidate {
  return {
    symbol,
    name: `股票${symbol.slice(0, 4)}`,
    industry: '半導體業',
    close: 99,
    as_of: '2026-09-30',
    rank: 1,
    selection_state: 'wait_pullback',
    signal_strength: 'medium',
    reasons: [
      { reason_code: 'trend_positive', evidence_key: 'trend', direction: 'positive', display_text: '股價站在 20 日均線之上，近 5 日也在上漲，趨勢目前偏強。' },
    ],
    risks: [],
    exclusion_reasons: [],
    action_summary: '股票目前偏強，但距離近期高點較近。觀察區：94～97。',
    invalidation: '若股價跌破 88.2（近 10 日低點），原本的理由就失效。',
    evidence_status: 'partial',
    data_gaps: ['資金：法人資料目前不可用。'],
    dimensions: [
      { key: 'trend', label: '趨勢', status: 'positive', explanation: '股價站在 20 日均線之上，近 5 日也在上漲，趨勢目前偏強。', evidence: { ma20: 95 }, source: 'screener.trend_pit' },
      { key: 'capital_flow', label: '資金', status: 'unavailable', explanation: '法人資料目前不可用。', evidence: {}, source: 'screener.institutional_5d' },
      { key: 'social_attention', label: '市場討論', status: 'unavailable', explanation: '來源目前不可用。', evidence: {}, source: 'social_sentiment.snapshot' },
    ],
    trade_plan: {
      rule_version: 'trade_plan_v1', entry_semantics: 'pullback_limit', entry_zone_low: 94, entry_zone_high: 97,
      breakout_trigger: 100, stop_price: 88.2, evidence_as_of: '2026-09-30', plan_identity: 'abc',
    },
    plan_unavailable_reason: null,
    ...overrides,
  }
}

export function beginnerSelection(count = 7, overrides: Partial<BeginnerSelectionResponse> = {}): BeginnerSelectionResponse {
  return {
    version: 'beginner-selection-v1',
    status: 'ready',
    as_of: '2026-09-30',
    generated_at: '2026-09-30T16:00:00+08:00',
    market: {
      state: 'favorable', headline: '今天市場偏強', explanation: '上漲 1500 家、下跌 500 家，多數股票表現偏正向。',
      guidance: '今天較適合尋找趨勢仍在、但沒有過度追高的股票。', as_of: '2026-09-30',
      advance_count: 1500, decline_count: 500, strongest_industries: ['半導體業'], source: 'market_intelligence',
    },
    candidates: Array.from({ length: count }, (_, i) => beginnerCandidate(`${1000 + i}.TWSE`, { rank: i + 1 })),
    not_selected: [
      beginnerCandidate('2330.TWSE', {
        name: '台積電', rank: null, selection_state: 'skip', signal_strength: 'weak', reasons: [], trade_plan: null,
        exclusion_reasons: [{ reason_code: 'quote_stale', evidence_key: 'eligibility.quote_date', direction: 'negative', display_text: '行情停在 2026-09-29，不是最新交易日，資料過舊。' }],
        action_summary: '行情停在 2026-09-29，不是最新交易日，資料過舊。', invalidation: null,
      }),
    ],
    universe_count: 200,
    eligible_count: 150,
    data_gaps: ['Dcard 來源目前不可用'],
    evidence_policy: { critical: ['quote'], optional: ['social_attention'] },
    disclaimer: '訊號強度代表目前條件符合程度，不代表上漲機率。',
    ...overrides,
  }
}
