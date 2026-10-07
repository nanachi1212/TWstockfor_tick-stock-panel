import type { BeginnerCandidate, BeginnerRadarResponse, BeginnerSelectionResponse, RadarItem } from '@/lib/api'

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
      reference_high: 100, breakout_trigger: 100, stop_price: 88.2, evidence_as_of: '2026-09-30', plan_identity: 'abc',
    },
    plan_unavailable_reason: null,
    technical_panel: {
      summary: '趨勢偏強，已接近上方壓力。等待回檔，不追價。', current_price: 99,
      support: {
        status: 'available', current_price: 99, support_zone_low: 94, support_zone_high: 97,
        resistance: 100, invalidation: 88.2, support_distance_low_pct: -5.05,
        support_distance_high_pct: -2.02, resistance_distance_pct: 1.01,
        explanation: '支撐、壓力與失效位沿用正式 TradePlan。', source: 'trade_plan',
        as_of: '2026-09-30', freshness: 'current',
      },
      resistance: {
        status: 'available', current_price: 99, support_zone_low: 94, support_zone_high: 97,
        resistance: 100, invalidation: 88.2, support_distance_low_pct: -5.05,
        support_distance_high_pct: -2.02, resistance_distance_pct: 1.01,
        explanation: '支撐、壓力與失效位沿用正式 TradePlan。', source: 'trade_plan',
        as_of: '2026-09-30', freshness: 'current',
      },
      invalidation: {
        status: 'available', current_price: 99, support_zone_low: 94, support_zone_high: 97,
        resistance: 100, invalidation: 88.2, support_distance_low_pct: -5.05,
        support_distance_high_pct: -2.02, resistance_distance_pct: 1.01,
        explanation: '支撐、壓力與失效位沿用正式 TradePlan。', source: 'trade_plan',
        as_of: '2026-09-30', freshness: 'current',
      },
      moving_averages: {
        status: 'available', state: 'strong', ma5: 98, ma20: 95, ma60: 90,
        bullish_alignment: true, explanation: '現價站在 5 日、20 日、60 日均線之上，中短期趨勢目前偏強。短、中期均線依序向上（多頭排列）。',
        source: 'pit_adjusted_daily', as_of: '2026-09-30', freshness: 'current',
      },
      inner_outer: {
        status: 'data_insufficient', outer_pct: null, inner_pct: null,
        last_price: null, trade_volume: null, trade_value: null, bids: [], asks: [],
        explanation: '目前沒有可靠的即時內外盤資料。', source: 'none', as_of: '2026-09-30', freshness: 'unavailable',
        disclaimer: '內外盤反映成交主動性，不等於真正買方／賣方人數，不能單獨作為買賣依據。',
      },
      volume: {
        status: 'available', today_volume: 2_000_000, average_20d: 1_500_000, ratio: 1.33,
        pattern: 'price_up_volume_up', explanation: '股價上漲且成交量同步放大，今天的上漲有較多成交參與。',
        source: 'taiwan_daily_store', as_of: '2026-09-30', freshness: 'current',
      },
      institutional: {
        status: 'available', state: 'buy', total_net_5d: 500_000, foreign_net_5d: 400_000,
        investment_trust_net_5d: 80_000, dealer_net_5d: 20_000, complete_sessions: 5,
        explanation: '近 5 個交易日法人整體偏買，其中以外資買超為主。', source: 'taiwan_institutional_store',
        as_of: '2026-09-30', freshness: 'latest_official',
      },
      margin: {
        status: 'available', margin_state: 'increase_fast', short_state: 'stable', margin_balance: 10_300_000,
        margin_change: 300_000, short_balance: 1_000_000, short_change: 5_000,
        explanation: '融資快速增加，融券變化不大。近期融資增加較快，短線籌碼可能較擁擠。',
        source: 'taiwan_margin_store', as_of: '2026-09-30', freshness: 'latest_official',
      },
      relative_strength: {
        status: 'available', state: 'stronger', period_sessions: 20, stock_return_pct: 12,
        benchmark_return_pct: 3, excess_return_pct: 9, benchmark_symbol: '0050.TWSE',
        explanation: '這檔最近表現明顯強於大盤。', source: 'pit_adjusted_daily+0050',
        as_of: '2026-09-30', freshness: 'current',
      },
      range_position: {
        status: 'available', low_20d: 82, high_20d: 100, position_pct: 94,
        explanation: '現價位於近 20 日區間約 94% 的位置。已接近近期高位，現在追價的安全空間較小。',
        source: 'pit_adjusted_daily', as_of: '2026-09-30', freshness: 'current',
      },
      volatility: {
        status: 'available', level: 'normal', atr_14: 3, atr_pct: 3.03,
        explanation: '近期每天上下震盪幅度一般。', source: 'pit_adjusted_daily',
        as_of: '2026-09-30', freshness: 'current',
      },
      market_context: {
        status: 'available', market_state: 'favorable', industry_state: 'strong', industry: '半導體業',
        explanation: '大盤環境偏正向，所屬類股相對強。', source: 'market_intelligence+industry_intelligence',
        as_of: '2026-09-30', freshness: 'current',
      },
      fundamentals: {
        status: 'available', revenue_yoy_pct: 20, revenue_mom_pct: 5, eps: 8, pe: 18,
        warning: null, explanation: '月營收年增 +20.0%、月增 +5.0%、EPS 8。',
        source: 'screener.fundamentals', as_of: '2026-09-30', freshness: 'latest_available',
      },
      key_risks: [
        { code: 'near_resistance', text: '現價已接近上方壓力，追價空間有限。', source: 'trade_plan' },
        { code: 'margin_crowded', text: '融資增加較快，短線籌碼可能較擁擠。', source: 'taiwan_margin_store' },
      ],
    },
    intraday_context: null,
    fx_context: null,
    macro_context: null,
    secondary_cross_checks: null,
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
        technical_panel: null,
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

export function beginnerRadar(items: RadarItem[], overrides: Partial<BeginnerRadarResponse> = {}): BeginnerRadarResponse {
  const selection = beginnerSelection(0)
  return {
    version: 'entry-radar-v1',
    selection_version: selection.version,
    status: 'ready',
    as_of: selection.as_of,
    generated_at: selection.generated_at,
    market_session: 'open',
    market: selection.market,
    items,
    not_selected: selection.not_selected,
    universe_count: 200,
    eligible_count: 150,
    data_gaps: [],
    disclaimer: '雷達只比對現價和計畫價位，不是買賣指令，也不代表上漲機率。',
    ...overrides,
  }
}