import { useState, useMemo } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Edit2,
  ListChecks,
  Power,
  RefreshCw,
  Trash2,
} from 'lucide-react'

import { api, type TaiwanMonitorRule, type TaiwanRuleType } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

interface TaiwanRulesListProps {
  rules: TaiwanMonitorRule[]
  onEdit: (rule: TaiwanMonitorRule) => void
  isLoading?: boolean
  isError?: boolean
  onRetry?: () => void
}

const RULE_TYPE_LABELS: Record<TaiwanRuleType, { label: string; unit: string }> = {
  price_above: { label: '價格高於', unit: 'TWD' },
  price_below: { label: '價格低於', unit: 'TWD' },
  change_pct_above: { label: '漲幅高於', unit: '%' },
  change_pct_below: { label: '跌幅低於', unit: '%' },
  volume_above: { label: '成交量高於', unit: '股' },
  volume_spike: { label: '成交量異常放大', unit: '倍' },
  near_upper_limit: { label: '接近漲停', unit: '%' },
  near_lower_limit: { label: '接近跌停', unit: '%' },
  quant_top10_enter: { label: '進入 Quant Top 10', unit: '' },
  quant_top10_exit: { label: '離開 Quant Top 10', unit: '' },
}

export function TaiwanRulesList({ rules, onEdit, isLoading = false, isError = false, onRetry }: TaiwanRulesListProps) {
  const qc = useQueryClient()
  const [actionError, setActionError] = useState<string | null>(null)
  const [syncMessage, setSyncMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // 啟用 / 停用 Mutation
  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.taiwanRuleUpdate(id, { enabled }),
    onMutate: () => setActionError(null),
    onError: () => setActionError('變更監控狀態失敗，請稍後再試。'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.taiwanRules })
    },
  })

  // 刪除 Mutation (手動規則)
  const deleteMut = useMutation({
    mutationFn: (id: string) => api.taiwanRuleDelete(id),
    onMutate: () => setActionError(null),
    onError: () => setActionError('刪除規則失敗，請稍後再試。'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.taiwanRules })
    },
  })

  // 立即同步 Mutation (自動規則)
  const syncMut = useMutation({
    mutationKey: QK.syncPlanRules,
    mutationFn: () => api.syncPlanRules(),
    onSuccess: async (res) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: QK.taiwanRules }),
        qc.invalidateQueries({ queryKey: QK.beginnerRadarRoot }),
      ])
      setSyncMessage({
        type: 'success',
        text: '同步完成：新增 ' + res.created + ' 筆，移除 ' + res.removed + ' 筆' +
          (res.skipped.length ? '；略過 ' + res.skipped.length + ' 檔（目前沒有可用的承接計畫）' : ''),
      })
    },
    onError: () => {
      setSyncMessage({
        type: 'error',
        text: '自動監控同步失敗，原有規則已保留',
      })
    },
  })

  const autoRules = useMemo(() => rules.filter(r => r.source === 'trade_plan'), [rules])
  const manualRules = useMemo(() => rules.filter(r => r.source !== 'trade_plan'), [rules])

  const autoRulesBySymbol = useMemo(() => {
    const map = new Map<string, TaiwanMonitorRule[]>()
    for (const r of autoRules) {
      const list = map.get(r.symbol) || []
      list.push(r)
      map.set(r.symbol, list)
    }
    return Array.from(map.entries()).map(([symbol, groupRules]) => ({
      symbol,
      rules: groupRules,
      planDate: groupRules.find(r => r.plan_as_of)?.plan_as_of || null,
    }))
  }, [autoRules])

  return (
    <div className="min-w-0 space-y-4">
      {isLoading && <p role="status" className="text-xs text-muted">正在載入監控規則…</p>}
      {isError && (
        <div role="alert" className="text-xs text-danger">
          監控規則載入失敗，請稍後再試。
          {onRetry && <button type="button" onClick={onRetry} className="ml-2 underline">重試載入</button>}
        </div>
      )}
      {actionError && <p role="alert" className="break-words text-xs text-danger">{actionError}</p>}
      {/* ===== 自動監控（來自承接雷達） ===== */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <h3 className="text-xs font-bold text-foreground">自動監控（來自承接雷達）</h3>
            <span className="rounded-md bg-elevated/50 px-1.5 py-0.5 text-[10px] font-medium text-muted">
              {autoRules.length}
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              setSyncMessage(null)
              syncMut.mutate()
            }}
            disabled={syncMut.isPending || isLoading || isError}
            className="inline-flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/10 px-2 py-1 text-xs font-semibold text-accent hover:bg-accent/20 transition-all disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={cn('h-3 w-3', syncMut.isPending && 'animate-spin')} />
            {syncMut.isPending ? '同步中…' : '立即同步'}
          </button>
        </div>

        {syncMessage && (
          <div role={syncMessage.type === 'error' ? 'alert' : 'status'}
            className={cn(
              'rounded-lg break-words p-2 text-xs',
              syncMessage.type === 'error'
                ? 'border border-danger/30 bg-danger/10 text-danger'
                : 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
            )}
          >
            {syncMessage.text}
          </div>
        )}

        {isLoading || isError ? null : autoRules.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/70 p-5 text-center text-muted">
            <ListChecks className="h-5 w-5 text-muted/60 mb-1" />
            <p className="text-xs font-medium text-foreground">尚未建立自動監控規則</p>
            <p className="text-[10px] text-muted mt-0.5">點擊「立即同步」同步自選股承接計畫，或至每日選股加入自選</p>
          </div>
        ) : (
          <div className="space-y-2">
            {autoRulesBySymbol.map(({ symbol, rules: stockRules, planDate }) => (
              <motion.div
                key={symbol}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-xl border border-border/80 bg-surface/90 p-3 text-xs shadow-sm space-y-2"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-1.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-accent">{symbol}</span>
                    {(
                      <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">
                        計畫日 {planDate || '資料不足'}
                      </span>
                    )}
                  </div>
                  <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                    承接雷達
                  </span>
                </div>

                <div className="space-y-1.5">
                  {stockRules.map(r => (
                    <div
                      key={r.rule_id}
                      className={cn(
                        'flex items-center justify-between gap-2 rounded-lg p-2 border transition-all',
                        r.enabled
                          ? 'border-border/60 bg-base/50'
                          : 'border-border/30 bg-elevated/20 opacity-60'
                      )}
                    >
                      <div className="flex flex-col min-w-0">
                        <span className="break-words font-semibold text-foreground">{r.name}</span>
                        <div className="flex flex-wrap items-center gap-1.5 mt-0.5 text-[11px]">
                          <span className="font-mono font-bold text-accent">
                            {r.threshold.toLocaleString()} 元
                          </span>
                          <span className="text-muted">·</span>
                          {r.notify_channels && r.notify_channels.length > 0 ? (
                            <div className="flex shrink-0 flex-wrap items-center gap-1">
                              {r.notify_channels.map(ch => (
                                <span key={ch} className="rounded bg-elevated px-1 py-0.5 text-[10px] text-muted font-medium">
                                  {ch === 'telegram' ? 'Telegram' : ch === 'line' ? 'LINE' : '通知'}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span className="rounded bg-elevated px-1 py-0.5 text-[10px] text-muted font-medium">
                              站內通知
                            </span>
                          )}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => toggleMut.mutate({ id: r.rule_id, enabled: !r.enabled })}
                        title={r.enabled ? '點擊停用' : '點擊啟用'}
                        aria-label={(r.enabled ? '停用 ' : '啟用 ') + r.symbol + ' ' + r.name}
                        aria-pressed={r.enabled}
                        disabled={toggleMut.isPending}
                        className={cn(
                          'p-1.5 rounded-lg border transition-all cursor-pointer shrink-0',
                          r.enabled
                            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                            : 'border-border text-muted hover:bg-elevated'
                        )}
                      >
                        <Power className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </section>

      {/* ===== 自訂監控規則 ===== */}
      <section className="space-y-2 pt-2 border-t border-border/40">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <h3 className="text-xs font-bold text-foreground">自訂監控規則</h3>
            <span className="rounded-md bg-elevated/50 px-1.5 py-0.5 text-[10px] font-medium text-muted">
              {manualRules.length}
            </span>
          </div>
        </div>

        {isLoading || isError ? null : manualRules.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-6 text-center text-muted">
            <ListChecks className="h-5 w-5 text-muted/60 mb-1.5" />
            <p className="text-xs font-medium">尚未建立自訂台股監控規則</p>
            <p className="text-[10px] text-muted mt-0.5">點擊右上角「+」或自左側報價卡快速建立</p>
          </div>
        ) : (
          <div className="space-y-2">
            {manualRules.map(r => {
              const typeCfg = RULE_TYPE_LABELS[r.rule_type as TaiwanRuleType] || {
                label: '提醒',
                unit: '',
              }
              const isVolumeAbove = r.rule_type === 'volume_above'
              const isQuant = r.rule_type === 'quant_top10_enter' || r.rule_type === 'quant_top10_exit'
              const thresholdText = isQuant ? '通過稽核的 Live 排名' : isVolumeAbove
                ? r.threshold >= 1000
                  ? `${(r.threshold / 1000).toLocaleString()} 張 (${r.threshold.toLocaleString()} 股)`
                  : `${r.threshold.toLocaleString()} 股`
                : `${r.threshold} ${typeCfg.unit}`

              return (
                <motion.div
                  key={r.rule_id}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={cn(
                    'group relative flex flex-col rounded-xl border p-3 text-xs transition-all shadow-sm',
                    r.enabled
                      ? 'border-border/80 bg-surface/90 hover:border-accent/40'
                      : 'border-border/40 bg-elevated/20 opacity-60'
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-accent">{r.symbol}</span>
                      <span className="break-words font-semibold text-foreground">{r.name}</span>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-1">
                      {/* 啟用/停用 開關按鈕 */}
                      <button
                        type="button"
                        onClick={() => toggleMut.mutate({ id: r.rule_id, enabled: !r.enabled })}
                        title={r.enabled ? '點擊停用' : '點擊啟用'}
                        aria-label={(r.enabled ? '停用 ' : '啟用 ') + r.symbol + ' ' + r.name}
                        aria-pressed={r.enabled}
                        disabled={toggleMut.isPending}
                        className={cn(
                          'p-1.5 rounded-lg border transition-all cursor-pointer',
                          r.enabled
                            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                            : 'border-border text-muted hover:bg-elevated'
                        )}
                      >
                        <Power className="h-3 w-3" />
                      </button>
                      {/* 編輯 */}
                      <button
                        type="button"
                        onClick={() => onEdit(r)}
                        title="編輯規則"
                        className="p-1.5 rounded-lg border border-border bg-surface text-muted hover:text-accent hover:border-accent/40 transition-colors cursor-pointer"
                      >
                        <Edit2 className="h-3 w-3" />
                      </button>
                      {/* 刪除 */}
                      <button
                        type="button"
                        onClick={() => deleteMut.mutate(r.rule_id)}
                        title="刪除規則"
                        className="p-1.5 rounded-lg border border-border bg-surface text-muted hover:text-danger hover:border-danger/40 transition-colors cursor-pointer"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>

                  {/* 條件描述與數值 */}
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="rounded bg-elevated px-1.5 py-0.5 font-medium text-foreground/90">
                      {typeCfg.label}
                    </span>
                    <span className="font-mono font-bold text-accent">
                      {thresholdText}
                    </span>
                    {!isQuant && r.cooldown_seconds > 0 && (
                      <span className="rounded bg-surface border border-border/50 px-1 py-0.5 text-[10px] text-muted">
                        冷卻 {r.cooldown_seconds}s
                      </span>
                    )}
                    {r.hysteresis != null && (
                      <span className="rounded bg-surface border border-border/50 px-1 py-0.5 text-[10px] text-muted">
                        防抖遲滯 {r.hysteresis}
                      </span>
                    )}
                  </div>
                </motion.div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
