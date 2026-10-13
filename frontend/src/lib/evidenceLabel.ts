// AI 引用的證據鍵 (如 technical_context.above_ma20) 轉成新手看得懂的中文。
// 逐段翻譯；沒收錄的段落保留原文，不會因此壞掉。
const SEGMENTS: Record<string, string> = {
  // 區塊
  abnormal: '異常異動', beginner_selection: '新手選股', buy_point: '買點', etf_context: 'ETF',
  events: '事件', fundamentals_context: '基本面', identity: '基本資料', industry_context: '產業',
  institutional_context: '法人', margin_context: '融資融券', market_context: '大盤', market_rules: '市場規則',
  news: '新聞', ownership_context: '持股結構', personal: '我的資料', price_context: '價格',
  technical_context: '技術面', technical_panel: '技術面板', macro_context: '總經', fx_context: '匯率',
  intraday_context: '盤中資料', secondary_cross_checks: '交叉驗證', portfolio: '持倉', quant: '量化', quote: '報價',
  social: '社群', watchlist: '自選', fundamentals: '基本面', inner_outer: '內外盤', institutional: '法人',
  invalidation: '失效條件', margin: '融資融券', moving_averages: '均線', range_position: '區間位置',
  relative_strength: '相對強弱', resistance: '壓力', support: '支撐', volatility: '波動', volume: '成交量',
  // 欄位
  action_summary: '行動摘要', reasons: '理由', risks: '風險', key_risks: '主要風險', summary: '摘要',
  selection_state: '篩選結果', signal_strength: '訊號強度', current_price: '現價', eps: 'EPS', pe: '本益比',
  pb: '股價淨值比', revenue_mom_pct: '月營收月增率', revenue_yoy_pct: '月營收年增率', status: '狀態',
  state: '狀態', total_net_5d: '近5日合計買賣超', margin_state: '融資狀態', short_state: '融券狀態',
  industry_state: '產業狀態', market_state: '大盤狀態', ma5: '5日均線', ma20: '20日均線', ma60: '60日均線',
  position_pct: '區間位置%', benchmark_return_pct: '大盤報酬%', excess_return_pct: '相對大盤超額報酬%',
  stock_return_pct: '個股報酬%', support_zone_high: '支撐區上緣', support_zone_low: '支撐區下緣',
  atr_pct: '平均波幅%', level: '程度', pattern: '量價型態', ratio: '量比', data_as_of: '資料日期',
  failed_conditions: '未通過條件', triggered_conditions: '已觸發條件', freshness: '資料新鮮度', risk_flags: '風險旗標',
  benchmark: '追蹤指數', etf_type: 'ETF 類型', inverse: '反向', leverage_multiplier: '槓桿倍數',
  underlying_scope: '標的範圍', items: '清單', recent_count: '近期筆數', as_of_period: '財報期間',
  dividend_yield: '殖利率', latest_eps: '最新EPS', monthly_revenue_mom: '月營收月增率',
  monthly_revenue_yoy: '月營收年增率', net_income: '淨利', code: '代號', exchange: '市場別', industry: '產業',
  instrument_type: '商品類型', name: '名稱', symbol: '代號身分', advance_ratio: '上漲比例',
  relative_strength_20d: '近20日相對強弱', relative_strength_5d: '近5日相對強弱', turnover_share: '成交值占比',
  coverage_days_5d: '近5日資料天數', foreign_net_1d: '外資當日買賣超', foreign_net_5d: '外資近5日買賣超',
  foreign_net_20d: '外資近20日買賣超', investment_trust_net_1d: '投信當日買賣超',
  investment_trust_net_5d: '投信近5日買賣超', margin_balance_change_1d: '融資餘額當日變化',
  margin_balance_change_5d: '融資餘額近5日變化', short_balance_change_1d: '融券餘額當日變化',
  short_margin_ratio: '券資比', advance_count: '上漲家數', decline_count: '下跌家數',
  market_turnover: '大盤成交值', is_no_limit: '無漲跌幅限制', price_limit_pct: '漲跌幅限制',
  foreign_shareholding_change_20d: '外資持股近20日變化', foreign_shareholding_ratio: '外資持股比例',
  foreign_shareholding_trend: '外資持股趨勢', average_cost: '平均成本', change: '漲跌', return_pct: '報酬率%',
  shares: '持有股數', quote_coverage: '報價涵蓋', registered_market_value: '登記市值',
  registered_positions_count: '登記持股檔數', unrealized_pnl: '未實現損益', weight_of_registered_pct: '占登記持倉比重%',
  selected: '已選取', is_stale: '報價過期', last_price: '最新價', market_status: '市況', quote_time: '報價時間',
  source: '來源', trade_date: '交易日', as_of: '資料日期', engagement: '互動數', heat_score: '熱度分數',
  ptt_mentions: 'PTT 提及數', sentiment: '情緒', sentiment_status: '情緒狀態', source_coverage: '來源涵蓋',
  total_mentions: '總提及數', unique_posts: '不重複文章數', included: '已納入', change_pct: '漲跌幅%',
  close: '收盤價', distance_from_20d_high: '距20日高點', distance_from_20d_low: '距20日低點',
  return_20d: '近20日報酬', return_5d: '近5日報酬', above_ma20: '站上20日均線', distance_to_ma20: '距20日均線',
  rsi14: 'RSI(14)', vol_ratio_5d: '近5日量比', not_applicable_signals: '不適用訊號', price_move: '價格異動',
  relative_strength_outlier: '相對強弱異常', signal_count: '訊號數', turnover_spike: '成交值暴增',
  volume_spike: '成交量暴增',
}

export function evidenceLabel(key: string): string {
  return key.split('.').map(part => SEGMENTS[part] ?? part).join('．')
}

export function evidenceLabels(keys: string[]): string {
  return keys.map(evidenceLabel).join('、')
}
