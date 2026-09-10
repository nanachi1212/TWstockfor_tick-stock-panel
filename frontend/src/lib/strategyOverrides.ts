import type { StrategyDetail } from './api'

/** 信號 id 歸一 (與策略回測頁一致): 裸名補 signal_ 前綴。 */
export const toSignalId = (sig: string) =>
  sig.startsWith('signal_') || sig.startsWith('csg_') ? sig : `signal_${sig}`

/** 從策略詳情構建默認 overrides (basic_filter / 信號 / 風控)。
 * 優化器與策略回測頁共用, 保證優化的就是用戶當前配置的策略。 */
export function buildDefaultOverrides(detail: StrategyDetail): Record<string, any> {
  return {
    basic_filter: { ...detail.basic_filter },
    entry_signals: detail.entry_signals.map(toSignalId),
    exit_signals: detail.exit_signals.map(toSignalId),
    scoring: { ...detail.scoring },
    scoring_directions: { ...(detail.scoring_directions ?? {}) },
    scoring_replace: true,
    stop_loss: detail.stop_loss,
    take_profit: detail.take_profit,
    trailing_stop: detail.trailing_stop,
    trailing_take_profit_activate: detail.trailing_take_profit_activate,
    trailing_take_profit_drawdown: detail.trailing_take_profit_drawdown,
    score_min: null,
    score_max: null,
    max_hold_days: detail.max_hold_days,
  }
}
