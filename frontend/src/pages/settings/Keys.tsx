import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Key,
  Eye,
  EyeOff,
  Trash2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Loader2,
  Save,
  Check,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useCapabilities, useSettings } from '@/lib/useSharedQueries'
import { QK } from '@/lib/queryKeys'
import { CAP_LABELS } from '@/lib/capability-labels'

// ===== TickFlow Key 配置主體 (可嵌入 DataSources 的 TickFlow 詳情區) =====

export function TickFlowKeyConfig() {
  const qc = useQueryClient()

  const settings = useSettings()
  const caps = useCapabilities()

  const [keyInput, setKeyInput] = useState('')
  const [revealing, setRevealing] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [saved, setSaved] = useState(false)

  const save = useMutation({
    mutationFn: () => api.saveTickflowKey(keyInput.trim()),
    onSuccess: (data) => {
      setKeyInput('')
      qc.invalidateQueries({ queryKey: QK.settings })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      // 可用能力變化會改變即時行情模式,立即刷新側邊欄狀態。
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      if (data.ok) {
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
      // ok=false 由 save.data 在下方渲染提示(reason=invalid),無需額外處理
    },
  })

  const clear = useMutation({
    mutationFn: () => api.clearTickflowKey(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.settings })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
    },
  })

  const redetect = useMutation({
    mutationFn: api.redetectCapabilities,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.settings })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
    },
  })

  const mode = settings.data?.mode
  const masked = settings.data?.tickflow_api_key_masked
  const capCount = caps.data ? Object.keys(caps.data.capabilities).length : 0

  return (
    <>
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-6 max-w-5xl">
        {/* ========== 左列: Key 配置 ========== */}
        <div className="space-y-6">
          <Card icon={Key} title="內建資料來源 API Key">
            <p className="text-sm text-secondary leading-relaxed mb-4">
              供系統內建資料來源使用。API Key 存放為本機秘密資料,畫面只顯示遮罩值。
            </p>

            {/* 當前狀態 */}
            <div className="flex items-center justify-between mb-4">
              <div className="min-w-0">
                <div className="text-[10px] uppercase tracking-widest text-muted">狀態</div>
                <div className="mt-1 flex items-center gap-2 min-w-0">
                  {mode === 'api_key' ? (
                    <>
                      <CheckCircle2 className="h-4 w-4 text-bear shrink-0" />
                      <span className="text-sm font-medium shrink-0">已設定</span>
                      <span className="font-mono text-xs text-secondary truncate">{masked}</span>
                    </>
                  ) : mode === 'free' ? (
                    <>
                      <CheckCircle2 className="h-4 w-4 text-bear shrink-0" />
                      <span className="text-sm font-medium shrink-0">免費 Key</span>
                      <span className="font-mono text-xs text-secondary truncate">{masked}</span>
                    </>
                  ) : (
                    <>
                      <AlertCircle className="h-4 w-4 text-muted shrink-0" />
                      <span className="text-sm font-medium text-muted">未設定</span>
                    </>
                  )}
                </div>
              </div>
              {(mode === 'api_key' || mode === 'free') && (
                <button
                  onClick={() => setConfirmClear(true)}
                  disabled={clear.isPending}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-btn bg-elevated text-secondary hover:text-danger text-xs transition-colors duration-150 ease-smooth disabled:opacity-50 shrink-0"
                >
                  <Trash2 className="h-3 w-3" />
                  清除
                </button>
              )}
            </div>

            {/* 輸入 */}
            <form
              onSubmit={(e) => {
                e.preventDefault()
                if (keyInput.trim()) save.mutate()
              }}
              className="space-y-2"
            >
              <div className="relative">
                <input
                  type={revealing ? 'text' : 'password'}
                  placeholder={mode === 'none' ? '貼上內建資料來源 API Key' : '貼上新 Key 取代目前'}
                  value={keyInput}
                  onChange={(e) => { setKeyInput(e.target.value); if (saved) setSaved(false) }}
                  className="w-full px-3 py-2 pr-9 rounded-input bg-base border border-border text-sm font-mono focus:outline-none focus:border-accent transition-colors duration-150 ease-smooth"
                />
                <button
                  type="button"
                  onClick={() => setRevealing((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-foreground transition-colors duration-150 ease-smooth"
                  tabIndex={-1}
                  aria-label={revealing ? '隱藏' : '顯示'}
                >
                  {revealing ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <button
                type="submit"
                disabled={save.isPending || (!keyInput.trim() && !saved)}
                className="w-full h-10 rounded-xl bg-accent text-white text-sm font-semibold flex items-center justify-center gap-2 hover:bg-accent/90 disabled:opacity-40 transition-all"
              >
                {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : saved ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
                {save.isPending ? '儲存中…' : saved ? '已儲存' : '儲存並檢測'}
              </button>

              {/* 檢測中提示 —— 成功/失敗後自動消失 */}
              {save.isPending && (
                <div className="flex items-start gap-1.5 rounded-btn border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] leading-snug text-warning">
                  <AlertCircle className="h-3.5 w-3.5 mt-px shrink-0" />
                  <span>
                    驗證通過前請不要離開目前頁面 · 如遇網路問題請點擊
                    <button
                      type="button"
                      onClick={() => { save.reset(); redetect.mutate() }}
                      disabled={redetect.isPending}
                      className="font-semibold underline underline-offset-2 hover:text-warning/80 disabled:opacity-50"
                    >
                      {redetect.isPending ? '重新檢測中…' : '重新檢測'}
                    </button>
                  </span>
                </div>
              )}
            </form>

            {save.isError && (
              <div className="mt-3 text-xs text-danger">
                儲存失敗:{String((save.error as any).message)}
              </div>
            )}
            {/* 無效 key —— 先探後存:探測失敗(key 無效/亂填)時不存儲,提示用戶 */}
            {save.data && !save.data.ok && (
              <div className="mt-3 text-xs text-danger flex items-center gap-1.5">
                <AlertCircle className="h-3 w-3 shrink-0" />
                {save.data.reason === 'invalid'
                  ? 'Key 無效或已過期,請檢查後重試(未儲存該 Key)'
                  : save.data.error ?? '儲存失敗'}
              </div>
            )}
            {save.data?.ok && (
              <div className="mt-3 text-xs text-bear flex items-center gap-1.5">
                <CheckCircle2 className="h-3 w-3" />
                儲存成功,已更新可用功能。
              </div>
            )}
          </Card>
        </div>

        {/* ========== 右列: 實際可用能力 ========== */}
        <div className="space-y-6">

          <Card
            icon={CheckCircle2}
            title="可用功能"
            badge={`${capCount} 項`}
            right={
              <button
                onClick={() => redetect.mutate()}
                disabled={redetect.isPending}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-btn bg-elevated hover:bg-elevated/80 text-xs text-secondary transition-colors duration-150 ease-smooth disabled:opacity-50"
              >
                <RefreshCw className={`h-3 w-3 ${redetect.isPending ? 'animate-spin' : ''}`} />
                重新檢測
              </button>
            }
          >
            {caps.data && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                className="-mx-5 -mb-5"
              >
                <div className="border-t border-border">
                  {Object.entries(caps.data.capabilities).map(([cap, lim]) => {
                    const meta = CAP_LABELS[cap]
                    return (
                      <div
                        key={cap}
                        className="px-5 py-3 border-b border-border last:border-b-0 flex items-baseline justify-between gap-4"
                      >
                        <div className="min-w-0">
                          <div className="text-sm text-foreground truncate">
                            {meta?.name ?? cap}
                          </div>
                          {meta?.hint && (
                            <div className="mt-0.5 text-[11px] text-muted truncate">
                              {meta.hint}
                            </div>
                          )}
                        </div>
                        <div className="text-right shrink-0 text-xs">
                          <div className="font-mono text-foreground">
                            {lim.rpm ? `${lim.rpm}/min` : lim.subscribe ? `${lim.subscribe} 訂閱` : '—'}
                          </div>
                          {lim.batch && (
                            <div className="font-mono text-muted">{lim.batch} 檔/次</div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </motion.div>
            )}

            {settings.data?.probe_log && settings.data.probe_log.length > 0 && (
              <details className="mt-4 -mx-5 -mb-5 border-t border-border">
                <summary className="cursor-pointer px-5 py-3 text-xs text-muted hover:text-secondary transition-colors duration-150 ease-smooth select-none">
                  查看檢測日誌
                </summary>
                <div className="px-5 pb-4 font-mono text-[11px] space-y-0.5 text-secondary">
                  {settings.data.probe_log.map((line, i) => (
                    <div key={i}>{line}</div>
                  ))}
                </div>
              </details>
            )}
          </Card>
        </div>
      </div>

      {/* 確認清除 Key 彈窗 */}
      {confirmClear && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirmClear(false)}
          />
          <div className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6">
            <h3 className="text-sm font-medium text-foreground mb-2">清除 API Key</h3>
            <p className="text-xs text-secondary mb-5">
              清除後將移除目前儲存的內建資料來源 API Key；需要時可重新輸入。
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setConfirmClear(false)}
                className="px-3 py-1.5 rounded-btn bg-elevated text-secondary hover:bg-elevated/80 text-sm transition-colors"
              >
                取消
              </button>
              <button
                onClick={() => { setConfirmClear(false); clear.mutate() }}
                disabled={clear.isPending}
                className="px-3 py-1.5 rounded-btn bg-danger/15 text-danger hover:bg-danger/25 text-sm font-medium transition-colors disabled:opacity-50"
              >
                {clear.isPending ? '清除中…' : '確認清除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

// ===== 通用卡片 =====

interface CardProps {
  icon: React.ComponentType<{ className?: string }>
  title: string
  badge?: string
  right?: React.ReactNode
  children: React.ReactNode
}

function Card({ icon: Icon, title, badge, right, children }: CardProps) {
  return (
    <section className="rounded-card border border-border bg-surface p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <Icon className="h-4 w-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground">{title}</h2>
          {badge && (
            <span className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-elevated text-muted">
              {badge}
            </span>
          )}
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}
