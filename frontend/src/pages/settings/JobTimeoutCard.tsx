/**
 * 数据任务超时配置卡片 — 从 DataSources 抽出, 放在系统设置页。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Clock3 } from 'lucide-react'
import { api, type Preferences } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { toast } from '@/components/Toast'

type TimeoutUnit = 'second' | 'minute' | 'hour'

const TIMEOUT_UNIT_SECONDS: Record<TimeoutUnit, number> = {
  second: 1,
  minute: 60,
  hour: 3600,
}

function preferredTimeoutUnit(seconds: number): TimeoutUnit {
  if (seconds >= 3600 && seconds % 1800 === 0) return 'hour'
  if (seconds % 60 === 0) return 'minute'
  return 'second'
}

function formatTimeoutValue(seconds: number, unit: TimeoutUnit): string {
  if (!Number.isFinite(seconds)) return ''
  const value = seconds / TIMEOUT_UNIT_SECONDS[unit]
  return String(Number(value.toFixed(4)))
}

export function JobTimeoutCard() {
  const qc = useQueryClient()
  const prefs = usePreferences()
  const [timeoutDraft, setTimeoutDraft] = useState<{ regular: string; long: string } | null>(null)
  const [regularUnitOverride, setRegularUnitOverride] = useState<TimeoutUnit | null>(null)
  const [longUnitOverride, setLongUnitOverride] = useState<TimeoutUnit | null>(null)

  const currentRegularTimeout = prefs.data?.data_source_job_timeout_s ?? 1200
  const currentLongTimeout = prefs.data?.data_source_long_job_timeout_s ?? 1800
  const regularTimeoutUnit = regularUnitOverride ?? preferredTimeoutUnit(currentRegularTimeout)
  const longTimeoutUnit = longUnitOverride ?? preferredTimeoutUnit(currentLongTimeout)
  const regularTimeoutInput = timeoutDraft?.regular
    ?? formatTimeoutValue(currentRegularTimeout, regularTimeoutUnit)
  const longTimeoutInput = timeoutDraft?.long
    ?? formatTimeoutValue(currentLongTimeout, longTimeoutUnit)
  const regularInputNumber = Number(regularTimeoutInput)
  const longInputNumber = Number(longTimeoutInput)
  const regularTimeout = Math.round(regularInputNumber * TIMEOUT_UNIT_SECONDS[regularTimeoutUnit])
  const longTimeout = Math.round(longInputNumber * TIMEOUT_UNIT_SECONDS[longTimeoutUnit])
  const timeoutValuesValid = Number.isFinite(regularInputNumber) && regularInputNumber > 0
    && Number.isFinite(longInputNumber) && longInputNumber > 0
    && regularTimeout >= 60 && longTimeout >= 60
  const timeoutValuesChanged = regularTimeout !== currentRegularTimeout
    || longTimeout !== currentLongTimeout

  const saveJobTimeouts = useMutation({
    mutationFn: () => api.updateDataSourceJobTimeouts(regularTimeout, longTimeout),
    onSuccess: (saved) => {
      qc.setQueryData<Preferences>(QK.preferences, current => (
        current ? { ...current, ...saved } : current
      ))
      setTimeoutDraft(null)
      toast('任務逾時設定已儲存', 'success')
    },
    onError: (e: Error) => toast(`儲存失敗: ${e.message}`, 'error'),
  })

  return (
    <section className="rounded-card border border-border bg-surface p-5">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div className="flex items-start gap-2.5">
          <Clock3 className="h-4 w-4 text-secondary mt-0.5" />
          <div>
            <h2 className="text-sm font-medium text-foreground">逾時設定</h2>
            <p className="text-[11px] text-muted mt-1 leading-relaxed">
              背景任務超過對應時間<b>沒有任何進度</b>才判定卡死並自動終止;只要任務仍在推進(如慢頻寬下的冷啟動全市場拉取),無論總時長多久都不會被中斷。儲存時自動換算為秒,修改後對新建任務生效。
            </p>
          </div>
        </div>
        <button
          onClick={() => saveJobTimeouts.mutate()}
          disabled={!timeoutValuesValid || !timeoutValuesChanged || saveJobTimeouts.isPending}
          className="shrink-0 px-3 py-1.5 rounded-btn bg-accent text-white text-xs font-medium hover:bg-accent/90 disabled:opacity-40 transition-colors"
        >
          {saveJobTimeouts.isPending ? '儲存中…' : '儲存'}
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="rounded-lg border border-border/60 bg-elevated/20 px-3.5 py-3">
          <span className="block text-xs font-medium text-foreground mb-1">一般任務停滯逾時</span>
          <span className="block text-[10px] text-muted mb-2">日 K 管道、擴展、修正與重算任務</span>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={regularTimeoutUnit === 'second' ? 60 : regularTimeoutUnit === 'minute' ? 1 : 1 / 60}
              step={regularTimeoutUnit === 'second' ? 60 : regularTimeoutUnit === 'minute' ? 1 : 0.5}
              value={regularTimeoutInput}
              onChange={e => setTimeoutDraft({ regular: e.target.value, long: longTimeoutInput })}
              className="w-full rounded-btn border border-border bg-base px-2.5 py-1.5 text-sm text-foreground font-mono outline-none focus:border-accent"
            />
            <select
              value={regularTimeoutUnit}
              onChange={e => {
                const nextUnit = e.target.value as TimeoutUnit
                setTimeoutDraft({
                  regular: formatTimeoutValue(regularTimeout, nextUnit),
                  long: longTimeoutInput,
                })
                setRegularUnitOverride(nextUnit)
              }}
              className="w-20 shrink-0 rounded-btn border border-border bg-base px-2 py-1.5 text-xs text-foreground outline-none focus:border-accent"
            >
              <option value="second">秒</option>
              <option value="minute">分鐘</option>
              <option value="hour">小時</option>
            </select>
          </div>
          <span className="block text-[10px] text-muted/60 mt-1.5">預設 20 分鐘無進度,最小 1 分鐘</span>
        </label>

        <label className="rounded-lg border border-border/60 bg-elevated/20 px-3.5 py-3">
          <span className="block text-xs font-medium text-foreground mb-1">長任務停滯逾時</span>
          <span className="block text-[10px] text-muted mb-2">分鐘 K 全市場同步任務</span>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={longTimeoutUnit === 'second' ? 60 : longTimeoutUnit === 'minute' ? 1 : 1 / 60}
              step={longTimeoutUnit === 'second' ? 60 : longTimeoutUnit === 'minute' ? 1 : 0.5}
              value={longTimeoutInput}
              onChange={e => setTimeoutDraft({ regular: regularTimeoutInput, long: e.target.value })}
              className="w-full rounded-btn border border-border bg-base px-2.5 py-1.5 text-sm text-foreground font-mono outline-none focus:border-accent"
            />
            <select
              value={longTimeoutUnit}
              onChange={e => {
                const nextUnit = e.target.value as TimeoutUnit
                setTimeoutDraft({
                  regular: regularTimeoutInput,
                  long: formatTimeoutValue(longTimeout, nextUnit),
                })
                setLongUnitOverride(nextUnit)
              }}
              className="w-20 shrink-0 rounded-btn border border-border bg-base px-2 py-1.5 text-xs text-foreground outline-none focus:border-accent"
            >
              <option value="second">秒</option>
              <option value="minute">分鐘</option>
              <option value="hour">小時</option>
            </select>
          </div>
          <span className="block text-[10px] text-muted/60 mt-1.5">預設 30 分鐘無進度,最小 1 分鐘</span>
        </label>
      </div>
    </section>
  )
}
