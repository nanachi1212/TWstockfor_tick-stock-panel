import type { StrategyNotifyEvent } from './api'

export interface StrategyEventMeta {
  label: string
  action: string
  className: string
}

const META: Record<string, StrategyEventMeta> = {
  buy_signal: {
    label: '買入',
    action: '買入訊號',
    className: 'text-danger',
  },
  sell_signal: {
    label: '賣出',
    action: '賣出訊號',
    className: 'text-bear',
  },
  pool_entry: {
    label: '進入',
    action: '進入選股結果',
    className: 'text-danger',
  },
  pool_exit: {
    label: '移出',
    action: '移出選股結果',
    className: 'text-bear',
  },
  new_entry: {
    label: '進入',
    action: '進入選股結果',
    className: 'text-danger',
  },
  dropped: {
    label: '移出',
    action: '移出選股結果',
    className: 'text-bear',
  },
}

/** 新建策略監控的默認通知事件: 選股結果(進入/移出), 與後端省略字段時的回填一致 */
export const DEFAULT_STRATEGY_NOTIFY_EVENTS: StrategyNotifyEvent[] = [
  'pool_entry',
  'pool_exit',
]

export const LEGACY_STRATEGY_NOTIFY_EVENTS: StrategyNotifyEvent[] = [
  'pool_entry',
  'pool_exit',
]

export const STRATEGY_NOTIFY_EVENT_OPTIONS: {
  key: StrategyNotifyEvent
  label: string
  group: 'signal' | 'pool'
}[] = [
  { key: 'buy_signal', label: '買入訊號', group: 'signal' },
  { key: 'sell_signal', label: '賣出訊號', group: 'signal' },
  { key: 'pool_entry', label: '進入選股結果', group: 'pool' },
  { key: 'pool_exit', label: '移出選股結果', group: 'pool' },
]

export function strategyEventMeta(type: string): StrategyEventMeta {
  return META[type] ?? {
    label: '事件',
    action: '策略事件',
    className: 'text-secondary',
  }
}

export function strategyName(message: string): string {
  return message.match(/策略「([^」]+)」/)?.[1] ?? ''
}
