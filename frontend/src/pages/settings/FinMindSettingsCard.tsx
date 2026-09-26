import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  KeyRound,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertCircle,
  Trash2,
  Loader2,
  Save,
  Activity,
  Layers,
} from 'lucide-react'
import { api } from '@/lib/api'
import { toast } from '@/components/Toast'

export function FinMindSettingsCard() {
  const qc = useQueryClient()
  const [tokenInput, setTokenInput] = useState('')
  const [revealing, setRevealing] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [isTesting, setIsTesting] = useState(false)

  // 1. 查詢當前 FinMind 設定
  const { data: finmindData, isLoading } = useQuery({
    queryKey: ['finmindPreference'],
    queryFn: api.getFinMindPreference,
  })

  const hasToken = Boolean(finmindData?.has_token)
  const isEnabled = Boolean(finmindData?.enabled)
  const tokenMasked = finmindData?.token_masked || ''

  // 2. 儲存 Token mutation
  const saveMutation = useMutation({
    mutationFn: (token: string) => api.updateFinMindPreference({ token }),
    onSuccess: () => {
      toast('FinMind API Token 已儲存', 'success')
      setTokenInput('')
      setTestResult(null)
      qc.invalidateQueries({ queryKey: ['finmindPreference'] })
    },
    onError: (err: any) => {
      toast(`儲存失敗: ${err.message || err}`, 'error')
    },
  })

  // 3. 切換啟用開關
  const toggleEnableMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updateFinMindPreference({ enabled }),
    onSuccess: (_, enabled) => {
      toast(enabled ? '已啟用 FinMind 資料補強' : '已停用 FinMind 資料補強', 'success')
      qc.invalidateQueries({ queryKey: ['finmindPreference'] })
    },
    onError: (err: any) => {
      toast(`更新失敗: ${err.message || err}`, 'error')
    },
  })

  // 4. 清除 Token
  const clearTokenMutation = useMutation({
    mutationFn: () => api.updateFinMindPreference({ clear_token: true }),
    onSuccess: () => {
      toast('已清除 FinMind Token', 'success')
      setTestResult(null)
      qc.invalidateQueries({ queryKey: ['finmindPreference'] })
    },
    onError: (err: any) => {
      toast(`清除失敗: ${err.message || err}`, 'error')
    },
  })

  // 5. 測試連線
  const handleTestConnection = async () => {
    setIsTesting(true)
    setTestResult(null)
    try {
      // 若目前輸入框有新 Token，先提示或以輸入測試；API 測試當前儲存之 Token
      const res = await api.testFinMindConnection()
      setTestResult(res)
      if (res.ok) {
        toast('FinMind 連線測試成功', 'success')
      } else {
        toast('FinMind 連線測試失敗', 'error')
      }
    } catch (err: any) {
      setTestResult({ ok: false, message: err.message || '連線測試失敗' })
      toast('FinMind 連線測試失敗', 'error')
    } finally {
      setIsTesting(false)
    }
  }

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault()
    if (!tokenInput.trim()) return
    saveMutation.mutate(tokenInput.trim())
  }

  return (
    <section className="rounded-card border border-border bg-surface p-6 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400">
            <Layers className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">
                FinMind 基本面與籌碼資料來源
              </h3>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 font-medium">
                擴充資料集
              </span>
            </div>
            <p className="text-xs text-secondary mt-0.5">
              提供台股月營收、損益表、外資持股及借券成交等深度補強資料。
            </p>
          </div>
        </div>

        {/* 啟用開關 */}
        <div className="flex items-center gap-3">
          <span className="text-xs text-secondary">
            {isEnabled ? '已啟用補強' : '已停用補強'}
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={isEnabled}
            disabled={toggleEnableMutation.isPending || isLoading}
            onClick={() => toggleEnableMutation.mutate(!isEnabled)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
              isEnabled ? 'bg-purple-600' : 'bg-elevated'
            } ${toggleEnableMutation.isPending ? 'opacity-50' : ''}`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                isEnabled ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
          </button>
        </div>
      </div>

      <p className="text-xs text-secondary leading-relaxed">
        未填寫 Token 時享有每小時 300 次公開請求額度；於{' '}
        <a
          href="https://finmindtrade.com"
          target="_blank"
          rel="noopener noreferrer"
          className="text-purple-400 hover:underline"
        >
          FinMind 官方網站
        </a>{' '}
        免費註冊會員並設定 Token 後，可享有每小時 600 次請求額度。Token 將安全保存在本機，絕不寫入 Git、前端 Bundle 或對外洩漏。
      </p>

      {/* 當前 Token 狀態 */}
      <div className="rounded-lg border border-border/60 bg-elevated/40 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-secondary" />
            <span className="text-xs font-medium text-foreground">API Token 狀態</span>
          </div>

          {hasToken && (
            <button
              type="button"
              onClick={() => clearTokenMutation.mutate()}
              disabled={clearTokenMutation.isPending}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              清除 Token
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {hasToken ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              <span className="text-xs text-foreground font-medium">已設定 Token：</span>
              <span className="font-mono text-xs text-secondary bg-surface px-2 py-0.5 rounded border border-border">
                {tokenMasked}
              </span>
            </>
          ) : (
            <>
              <AlertCircle className="h-4 w-4 text-amber-400 shrink-0" />
              <span className="text-xs text-amber-300">
                尚未設定 Token（將使用未註冊公開額度 300 次/小時，或僅讀取本地快取）
              </span>
            </>
          )}
        </div>

        {/* Token 輸入表單 */}
        <form onSubmit={handleSave} className="space-y-3 pt-2 border-t border-border/40">
          <label className="block text-xs text-secondary">
            {hasToken ? '更新 Token' : '輸入 Token'}
          </label>
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <input
                type={revealing ? 'text' : 'password'}
                placeholder="貼上 FinMind API Token (例如: eyJhbGciOi...)"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                autoComplete="off"
                className="w-full rounded-btn border border-border bg-surface px-3 py-1.5 pr-10 text-xs text-foreground placeholder:text-muted/60 focus:border-purple-500 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setRevealing((prev) => !prev)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-foreground"
                title={revealing ? '隱藏' : '顯示'}
              >
                {revealing ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={saveMutation.isPending || !tokenInput.trim()}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-btn bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium transition-colors disabled:opacity-50"
              >
                {saveMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Save className="h-3.5 w-3.5" />
                )}
                儲存 Token
              </button>

              <button
                type="button"
                onClick={handleTestConnection}
                disabled={isTesting}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-btn bg-elevated hover:bg-elevated/80 border border-border text-foreground text-xs font-medium transition-colors disabled:opacity-50"
              >
                {isTesting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-purple-400" />
                ) : (
                  <Activity className="h-3.5 w-3.5 text-purple-400" />
                )}
                測試連線
              </button>
            </div>
          </div>
        </form>

        {/* 測試結果訊息展示 */}
        {testResult && (
          <div
            className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
              testResult.ok
                ? 'bg-emerald-950/30 border-emerald-800/60 text-emerald-300'
                : 'bg-red-950/30 border-red-800/60 text-red-300'
            }`}
          >
            {testResult.ok ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400 mt-0.5" />
            ) : (
              <AlertCircle className="h-4 w-4 shrink-0 text-red-400 mt-0.5" />
            )}
            <div>
              <div className="font-semibold">
                {testResult.ok ? '連線測試成功' : '連線測試未通過'}
              </div>
              <p className="mt-0.5 leading-relaxed text-[11px] opacity-90">{testResult.message}</p>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
