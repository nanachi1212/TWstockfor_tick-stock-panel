import { useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Edit2,
  ListChecks,
  Power,
  Trash2,
} from 'lucide-react'

import { api, type TaiwanMonitorRule, type TaiwanRuleType } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

interface TaiwanRulesListProps {
  rules: TaiwanMonitorRule[]
  onEdit: (rule: TaiwanMonitorRule) => void
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
}

export function TaiwanRulesList({ rules, onEdit }: TaiwanRulesListProps) {
  const qc = useQueryClient()

  // 啟用 / 停用 Mutation
  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.taiwanRuleUpdate(id, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.taiwanRules })
    },
  })

  // 刪除 Mutation
  const deleteMut = useMutation({
    mutationFn: (id: string) => api.taiwanRuleDelete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.taiwanRules })
    },
  })

  if (!rules || rules.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-center text-muted">
        <ListChecks className="h-6 w-6 text-muted/60 mb-2" />
        <p className="text-xs font-medium">尚未建立台股監控規則</p>
        <p className="text-[10px] text-muted mt-0.5">點擊右上角「+」或自左側報價卡快速建立</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {rules.map(r => {
        const typeCfg = RULE_TYPE_LABELS[r.rule_type as TaiwanRuleType] || {
          label: r.rule_type,
          unit: '',
        }
        const isVolumeAbove = r.rule_type === 'volume_above'
        const thresholdText = isVolumeAbove
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
                <span className="font-semibold text-foreground">{r.name}</span>
              </div>
              <div className="flex items-center gap-1">
                {/* 啟用/停用 開關按鈕 */}
                <button
                  onClick={() => toggleMut.mutate({ id: r.rule_id, enabled: !r.enabled })}
                  title={r.enabled ? '點擊停用' : '點擊啟用'}
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
                  onClick={() => onEdit(r)}
                  title="編輯規則"
                  className="p-1.5 rounded-lg border border-border bg-surface text-muted hover:text-accent hover:border-accent/40 transition-colors cursor-pointer"
                >
                  <Edit2 className="h-3 w-3" />
                </button>
                {/* 刪除 */}
                <button
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
              {r.cooldown_seconds > 0 && (
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
  )
}
