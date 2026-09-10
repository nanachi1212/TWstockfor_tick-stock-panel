/**
 * 訪問認證頁 — 複用同一組件處理「首次設密碼」和「登錄」兩種狀態。
 *
 * 根據後端 /api/auth/status 的 configured 字段決定顯示:
 *   - configured=false → 顯示「設置訪問密碼」(首次)
 *   - configured=true  → 顯示「登錄」
 *
 * 安全:
 *   - 設密碼接口後端限本機/內網; 公網用戶設密碼會被 403 拒絕, 頁面據此提示。
 *   - 登錄失敗由後端限流(5次鎖5分鐘), 429 時前端顯示等待提示。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Eye, EyeOff, Loader2, Lock, ShieldCheck, ShieldAlert, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { Logo } from '@/components/Logo'
import { cn } from '@/lib/cn'

export function Auth() {
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')  // 僅設密碼時用
  const [showPwd, setShowPwd] = useState(false)
  const [localError, setLocalError] = useState('')

  // 取認證狀態(是否已設密碼)
  const [status, setStatus] = useState<{ configured: boolean } | null>(null)
  useEffect(() => {
    api.authStatus().then(s => {
      setStatus(s)
      // 已登錄的話直接進面板(避免登錄頁死循環)
      if (s.authenticated) navigate('/', { replace: true })
    }).catch(() => setStatus({ configured: false }))
  }, [navigate])

  const isSetup = !status?.configured  // configured=false → 設密碼模式

  // 登錄 / 設密碼 共用一個 mutation(按 isSetup 調不同接口)
  const submitMut = useMutation({
    mutationFn: async () => {
      if (isSetup) {
        return api.authSetup(password)
      }
      return api.authLogin(password)
    },
    onSuccess: () => {
      // 成功: 跳回原頁面(或首頁)
      const redirect = new URLSearchParams(window.location.search).get('redirect') || '/'
      navigate(redirect, { replace: true })
    },
    onError: (err: any) => {
      const msg = err?.message || (isSetup ? '設置失敗' : '登錄失敗')
      // 設密碼/登錄失敗必須顯示: 401(密碼錯)/403(公網設密碼被拒)/429(限流) 都要提示
      setLocalError(msg)
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setLocalError('')
    if (isSetup) {
      if (password.length < 6) { setLocalError('密碼至少 6 位'); return }
      if (password !== confirmPassword) { setLocalError('兩次密碼不一致'); return }
    }
    submitMut.mutate()
  }

  if (!status) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base">
        <Loader2 className="h-6 w-6 animate-spin text-muted" />
      </div>
    )
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-base px-4">
      {/* 背景輝光(與 Onboarding 風格一致) */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(139,92,246,0.15),transparent_40%),radial-gradient(circle_at_70%_80%,rgba(59,130,246,0.12),transparent_40%)]" />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="relative w-full max-w-sm"
      >
        {/* Logo */}
        <div className="mb-6 flex flex-col items-center gap-2">
          <Logo className="h-10 w-10" />
          <h1 className="text-lg font-semibold text-foreground">Nanachi 的台股監控看板</h1>
        </div>

        <div className="rounded-card border border-border bg-surface/90 p-6 shadow-2xl backdrop-blur">
          {/* 標題區: 圖標 + 文案隨模式切換 */}
          <div className="mb-5 flex items-center gap-2.5">
            <div className={cn(
              'grid h-9 w-9 place-items-center rounded-lg',
              isSetup ? 'bg-accent/15 text-accent' : 'bg-purple-500/15 text-purple-400',
            )}>
              {isSetup ? <ShieldCheck className="h-5 w-5" /> : <Lock className="h-5 w-5" />}
            </div>
            <div>
              <div className="text-sm font-medium text-foreground">
                {isSetup ? '設置訪問密碼' : '登錄訪問'}
              </div>
              <div className="text-[11px] text-muted">
                {isSetup ? '首次使用, 請為面板設置訪問密碼' : '請輸入訪問密碼以繼續'}
              </div>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            {/* 密碼輸入 */}
            <div className="relative">
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="訪問密碼"
                autoFocus
                className="h-10 w-full rounded-btn border border-border bg-base px-3 pr-9 text-sm text-foreground outline-none transition-colors focus:border-accent/50"
              />
              <button
                type="button"
                onClick={() => setShowPwd(s => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted hover:text-foreground"
                tabIndex={-1}
              >
                {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>

            {/* 確認密碼(僅設密碼模式) */}
            {isSetup && (
              <input
                type={showPwd ? 'text' : 'password'}
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="再次輸入密碼"
                className="h-10 w-full rounded-btn border border-border bg-base px-3 text-sm text-foreground outline-none transition-colors focus:border-accent/50"
              />
            )}

            {/* 錯誤提示 */}
            {(localError || submitMut.error) && (
              <div className="flex items-start gap-1.5 rounded-btn bg-danger/10 px-3 py-2 text-[11px] text-danger">
                <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0" />
                <span>{localError}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={submitMut.isPending || !password}
              className="inline-flex h-10 w-full items-center justify-center gap-1.5 rounded-btn bg-accent text-sm font-medium text-white transition-colors hover:bg-accent/90 disabled:opacity-50"
            >
              {submitMut.isPending ? (
                <><Loader2 className="h-4 w-4 animate-spin" />處理中…</>
              ) : (
                <>{isSetup ? '設置並進入' : '登錄'}</>
              )}
            </button>
          </form>

          {/* 提示: 設密碼模式告知本機限制 */}
          {isSetup && (
            <div className="mt-3 space-y-1.5 text-[10px] leading-relaxed text-muted/70">
              <p>
                出於安全考慮, 首次設置密碼需在服務器本機或內網訪問時操作。公網環境下僅可登錄。
              </p>
              <p>
                詳細配置說明見{' '}
                <a
                  href="https://github.com/nanachi1212/TWstockfor_tick-stock-panel/blob/main/docs/deploy-password.md"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline-offset-2 hover:underline"
                >
                  訪問密碼部署文檔
                </a>
              </p>
            </div>
          )}
        </div>

        <div className="mt-4 flex items-center justify-center gap-1.5 text-[10px] text-muted/60">
          <Sparkles className="h-3 w-3" />
          自託管量化工作台 · 數據完全掌握在自己手裡
        </div>
      </motion.div>
    </div>
  )
}
