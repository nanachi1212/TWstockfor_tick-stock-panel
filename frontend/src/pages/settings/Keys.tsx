import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Key,
  Eye,
  EyeOff,
  Trash2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Activity,
  ExternalLink,
  Loader2,
  Save,
  Check,
  HelpCircle,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useCapabilities, useSettings } from '@/lib/useSharedQueries'
import { QK } from '@/lib/queryKeys'
import { CAP_LABELS, tierTextStyle, tierStyle, tierBaseName, ALL_TIERS, TierTag } from '@/lib/capability-labels'

// ===== TickFlow Key 配置主体 (可嵌入 DataSources 的 TickFlow 详情区) =====

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
      // 档位变化会改变实时行情模式(none/watchlist/full_market), 立即刷新侧边栏状态
      qc.invalidateQueries({ queryKey: QK.quoteStatus })
      if (data.ok) {
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
      // ok=false 由 save.data 在下方渲染提示(reason=invalid),无需额外处理
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
          <Card icon={Key} title="TickFlow API Key">
            <p className="text-sm text-secondary leading-relaxed mb-4">
              在{' '}
              <a
                href="https://tickflow.org/auth/register?ref=V3KDKGXPEA"
                target="_blank"
                rel="noreferrer"
                className="text-accent hover:underline inline-flex items-baseline gap-0.5"
              >
                tickflow.org
                <ExternalLink className="h-3 w-3 self-center" />
              </a>{' '}
              註冊取得。API Key 存放為本機檔案,不會上傳任何第三方,請妥善保管。
            </p>

            {/* 当前状态 */}
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

            {/* 输入 */}
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
                  placeholder={mode === 'none' ? '貼上 TickFlow API Key' : '貼上新 Key 取代目前'}
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

              {/* 检测中提示 —— 成功/失败后自动消失 */}
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
            {/* 无效 key —— 先探后存:探测失败(key 无效/乱填)时不存储,提示用户 */}
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
                儲存成功 — 檔位 {save.data.tier_label}
                {save.data.mode === 'free' && '(免費檔 · 歷史日K + 自選即時監控)'}
              </div>
            )}
          </Card>
        </div>

        {/* ========== 右列: 档位 + 能力 ========== */}
        <div className="space-y-6">
          <Card
            icon={Activity}
            title="訂閱檔位"
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
            {caps.data ? (
              <>
                <div className="flex items-center gap-1.5">
                  <div className="font-mono text-3xl font-bold tracking-tight" style={tierTextStyle(caps.data.label)}>
                    {caps.data.label}
                  </div>
                  <TierHelpPopover currentLabel={caps.data.label} />
                </div>
                <div className="mt-1 text-xs text-muted">
                  根據 API Key 自動檢測 · 擁有「代表性 capability」任一即認定該檔
                </div>

                {settings.data?.missing_caps && settings.data.missing_caps.length > 0 && (
                  <div className="mt-3 rounded-btn border border-warning/40 bg-warning/5 px-3 py-2 text-xs">
                    <div className="font-medium text-warning mb-1">
                      本檔應有但未偵測到({settings.data.missing_caps.length} 項)
                    </div>
                    <div className="text-secondary space-y-0.5">
                      {settings.data.missing_caps.map((c) => (
                        <div key={c} className="font-mono">
                          {CAP_LABELS[c]?.name ?? c}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="text-sm text-muted">載入中…</div>
            )}
          </Card>

          <Card icon={CheckCircle2} title="可用功能" badge={`${capCount} 項`}>
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

      {/* 确认清除 Key 弹窗 */}
      {confirmClear && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirmClear(false)}
          />
          <div className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6">
            <h3 className="text-sm font-medium text-foreground mb-2">清除 API Key</h3>
            <p className="text-xs text-secondary mb-5">
              清除後將退回 None 檔(僅歷史日K),需要重新輸入 Key 才能恢復。
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

// ===== 档位说明弹窗 =====

function TierHelpPopover({ currentLabel }: { currentLabel: string }) {
  const [open, setOpen] = useState(false)
  const currentBase = tierBaseName(currentLabel)

  return (
    <div className="relative inline-flex items-center">
      <HelpCircle
        className="h-4 w-4 text-muted/60 cursor-help hover:text-muted transition-colors"
        onClick={() => setOpen(v => !v)}
      />
      <AnimatePresence>
        {open && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
            <motion.div
              initial={{ opacity: 0, y: -4, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, scale: 0.95 }}
              transition={{ duration: 0.15 }}
              className="absolute top-full left-0 mt-1 z-50 w-72 bg-surface border border-border rounded-lg shadow-xl p-3.5 text-[11px] leading-relaxed"
              onClick={e => e.stopPropagation()}
            >
              {/* 档位 tag 横排 */}
              <div className="flex items-center gap-1.5 mb-3">
                {ALL_TIERS.map(t => (
                  <div key={t} className={`flex flex-col items-center gap-1 ${t === currentBase ? '' : 'opacity-60'}`}>
                    <TierTag label={t} />
                  </div>
                ))}
              </div>

              {/* 每档说明 */}
              <div className="space-y-1 mb-3 pb-3 border-b border-border">
                {ALL_TIERS.map(t => {
                  const s = tierStyle(t)
                  return (
                    <div key={t} className="flex items-center gap-2">
                      <span className="h-1.5 w-1.5 rounded-full shrink-0" style={s.dotStyle} />
                      <span className="font-mono font-bold w-12 shrink-0" style={s.labelTextStyle}>{t === 'none' ? 'None' : t}</span>
                      <span className="text-secondary">{s.desc}</span>
                    </div>
                  )
                })}
              </div>

              <div className="mb-3 rounded-btn border border-warning/30 bg-warning/10 px-2.5 py-1.5 text-[11px] font-medium text-warning">
                較高檔位包含較低檔位的全部權益。
              </div>

              {/* 检测说明 */}
              <div className="text-secondary space-y-1.5">
                <div className="font-medium text-foreground">檔位檢測說明</div>
                <p>儲存 Key 後系統會在付費端點逐一試探資料能力:連單一檔日K都拿不到則判為「None」(不存 Key);有日K但無除權因子則判為「Free」;有除權因子再依代表能力判定 Starter/Pro/Expert。</p>
                <p className="text-muted">None 檔與 Free 檔執行時都走免費資料通道(僅歷史日K),差別僅在於是否儲存了 Key。付費檔走付費端點,享有即時行情等完整能力。</p>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}


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
