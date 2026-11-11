import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Loader2,
  CheckCircle2,
  AlertCircle,
  ArrowRight,
  ArrowLeft,
  Sparkles,
  ScanSearch,
  TrendingUp,
  Scale,
  Database,
  ShieldCheck,
} from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { Logo } from '@/components/Logo'
import { RADIX } from '@/lib/radix'

// ===== 引導頁：4 步流程 =====
// 0. 使用須知  1. 歡迎  2. 台股資料狀態  3. 完成 → 寫入標記 → 進主介面
//
// 設計原則(Phase 8B-1):台股分析工具以台灣股市為預設市場,不需要任何第三方 API Key
// 或 AI API Key 就能完成引導、進入核心功能。資料來源與 AI 設定一律移到
// 「設定 → 資料來源」「設定 → AI」,不再是引導流程的必經步驟。

const STEPS = ['使用須知', '歡迎', '台股資料狀態', '完成'] as const

const BRAND = RADIX.brand

// 僅列出目前已有可靠 backend + frontend 支援的台股功能,避免宣傳尚未完成的項目。
const HIGHLIGHTS = [
  { icon: ScanSearch,  title: '台股選股',   desc: '以篩選策略掃描上市櫃標的', tint: 'text-accent' },
  { icon: TrendingUp,  title: '個股分析',   desc: '個股資料含三大法人買賣超、融資融券、ETF 資訊', tint: 'text-warning' },
  { icon: Scale,       title: '多股比較',   desc: '並列比較多檔台股的相對表現', tint: 'text-accent' },
  { icon: Database,    title: '本機資料優先', desc: '資料儲存在本機，離線仍可查閱', tint: 'text-accent' },
  { icon: Sparkles,    title: 'AI 研究（選配）', desc: '可在「設定 → AI」自行開啟', tint: 'text-accent' },
]

export function Onboarding() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [step, setStep] = useState(0)

  // 完成引導 —— 寫後端標記,讓路由守衛放行
  const complete = useMutation({
    mutationFn: api.completeOnboarding,
    onSuccess: (data) => {
      // 用回傳值同步更新快取,確保跳轉時守衛立即看到 onboarding_completed: true
      // (避免 invalidate 後台重取未返回時, 守衛用舊快取 false 誤導回引導頁)
      qc.setQueryData(QK.settings, (old: any) =>
        old ? { ...old, onboarding_completed: data.onboarding_completed } : old,
      )
      qc.invalidateQueries({ queryKey: QK.settings })
      navigate('/', { replace: true })
    },
    onError: () => {
      // A failed save must not lock the user out. Mark it done in the cache for this session,
      // otherwise the route guard sends them straight back here.
      qc.setQueryData(QK.settings, (old: any) => old ? { ...old, onboarding_completed: true } : old)
      navigate('/', { replace: true })
    },
  })

  const finish = () => complete.mutate()

  return (
    <div className="relative min-h-screen bg-base overflow-hidden flex flex-col">
      {/* 頂欄:logo + 進度指示 */}
      <header className="relative z-10 flex items-center justify-between gap-3 border-b border-border px-4 py-4 sm:px-6">
        <div className="flex items-center gap-2.5 text-foreground">
          <Logo
            size={24}
            className="shrink-0"
            style={{ color: BRAND }}
          />
          <span className="hidden text-sm font-semibold tracking-tight sm:inline">Nanachi 的台股監控看板</span>
        </div>
        {/* 步驟進度條 —— 膠囊式 */}
        <div className="flex items-center gap-1.5">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-1.5">
              {i > 0 && <div className="h-px w-3 bg-border" />}
              <div className={`h-1.5 rounded-full ${i === step ? 'w-16 bg-accent' : i < step ? 'w-6 bg-accent/60' : 'w-6 bg-border'}`} />
            </div>
          ))}
        </div>
        <div className="shrink-0 text-right">
          <span className="text-xs text-muted tabular">
            {step + 1} / {STEPS.length}
          </span>
        </div>
      </header>

      {/* 步驟內容 */}
      <main className="relative z-10 flex-1 flex items-center justify-center px-6 py-10">
        <div className="w-full max-w-xl">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -24 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
            >
              {step === 0 && <DisclaimerStep onNext={() => setStep(1)} />}
              {step === 1 && <WelcomeStep onNext={() => setStep(2)} onSkip={finish} />}
              {step === 2 && (
                <TaiwanDataStatusStep onNext={() => setStep(3)} onBack={() => setStep(1)} />
              )}
              {step === 3 && <FinishStep onNext={finish} onBack={() => setStep(2)} pending={complete.isPending} />}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  )
}

// ===== Step 0: 使用須知 =====

function DisclaimerStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="text-center">
      <div className="mx-auto w-fit rounded-card border border-warning/40 bg-warning/10 p-4">
        <AlertCircle className="h-8 w-8 text-warning" />
      </div>

      <h1 className="mt-6 text-lg font-bold text-foreground tracking-tight">使用前請詳閱</h1>

      <div className="mt-5 rounded-card border border-border bg-surface/80 backdrop-blur-sm p-5 text-left">
        <div className="flex items-start gap-2.5">
          <ShieldCheck className="h-4 w-4 text-accent shrink-0 mt-0.5" />
          <div className="space-y-2.5 text-sm text-secondary leading-relaxed">
            <p>
              本專案為<strong className="text-warning">個人開源專案</strong>，由個人獨立維護，與任何商業資料服務
              <span className="text-warning">無官方關聯</span>。資料能力依賴第三方資料服務提供。
            </p>
            <p>
              僅供學習研究使用，不構成任何投資建議。股市有風險，使用本專案產生的任何盈虧由使用者自行承擔。
            </p>
            <p>
              本專案基於 MIT 授權開源。使用時請遵守資料來源的服務條款，並自行評估第三方介面套件的合規風險。
            </p>
          </div>
        </div>
      </div>

      <div className="mt-6 flex items-center justify-center">
        <button
          onClick={onNext}
          className="inline-flex items-center gap-2 px-6 h-11 rounded-xl bg-accent text-white text-sm font-semibold shadow-lg shadow-accent/20 hover:bg-accent/90 hover:shadow-accent/30 transition-all"
        >
          我已了解，繼續
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}

// ===== Step 1: 歡迎 =====

function WelcomeStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  return (
    <div className="text-center">
      {/* 品牌 badge */}
      <div className="mx-auto w-fit rounded-card border border-border bg-surface p-4">
        <Sparkles className="h-8 w-8" style={{ color: BRAND }} />
      </div>

      <h1 className="mt-6 text-lg font-bold text-foreground tracking-tight">
        歡迎使用台股分析工具
      </h1>
      <p className="mt-3 text-sm text-secondary leading-relaxed max-w-md mx-auto">
        選股、個股分析與多股比較一次到位，無須第三方 API 金鑰即可使用核心功能。
      </p>

      {/* 特性卡片 */}
      <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-left">
        {HIGHLIGHTS.map(h => (
          <div
            key={h.title}
            className="group flex items-start gap-2.5 rounded-card border border-border bg-surface p-2.5 transition-colors hover:border-border-interactive"
          >
            <div className="rounded-lg bg-elevated/50 p-1.5 shrink-0">
              <h.icon className={`h-4 w-4 ${h.tint} transition-transform group-hover:scale-110`} />
            </div>
            <div className="min-w-0">
              <div className="text-xs font-medium text-foreground">{h.title}</div>
              <div className="mt-0.5 text-xs text-muted leading-snug line-clamp-2">{h.desc}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-8 flex items-center justify-center gap-3">
        <button
          onClick={onNext}
          className="inline-flex items-center gap-2 px-6 h-11 rounded-xl bg-accent text-white text-sm font-semibold shadow-lg shadow-accent/20 hover:bg-accent/90 hover:shadow-accent/30 transition-all"
        >
          開始使用
          <ArrowRight className="h-4 w-4" />
        </button>
        <button
          onClick={onSkip}
          className="px-4 h-11 rounded-xl text-sm text-secondary hover:text-foreground hover:bg-elevated transition-colors"
        >
          稍後再說
        </button>
      </div>
    </div>
  )
}

// ===== Step 2: 台股資料狀態 =====
// 只讀既有的 /api/taiwan/data-status,不建立重複的 backend 邏輯。
// 沒有資料也不阻擋使用者完成引導 —— 之後仍可到「資料管理」更新。

const FRESHNESS_LABEL: Record<string, string> = {
  current: '最新',
  stale: '過期',
  unavailable: '尚無資料',
}

const FRESHNESS_TINT: Record<string, string> = {
  current: 'text-success',
  stale: 'text-warning',
  unavailable: 'text-muted',
}

function TaiwanDataStatusStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const status = useQuery({
    queryKey: QK.taiwanDataStatus,
    queryFn: api.taiwanDataStatus,
    staleTime: 60_000,
  })

  const rows = status.data
    ? [
        { label: '日K', asOf: status.data.daily_as_of, freshness: status.data.daily_status },
        { label: '三大法人', asOf: status.data.institutional_as_of, freshness: status.data.institutional_status },
        { label: '融資融券', asOf: status.data.margin_as_of, freshness: status.data.margin_status },
      ]
    : []

  const hasAnyData = rows.some(r => r.asOf)

  return (
    <div>
      <div className="flex items-center gap-2.5">
        <div className="rounded-lg bg-accent/10 p-2">
          <Database className="h-4 w-4 text-accent" />
        </div>
        <h2 className="text-lg font-bold text-foreground">台股資料狀態</h2>
      </div>
      <p className="mt-2.5 text-sm text-secondary leading-relaxed">
        檢查目前本機台股資料，不影響後續使用。
      </p>

      <div className="mt-5 rounded-card border border-border bg-surface p-5">
        {status.isLoading ? (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            正在讀取台股資料狀態…
          </div>
        ) : status.isError ? (
          <p className="text-xs text-muted leading-relaxed">
            目前無法讀取資料狀態，請稍後再試。
          </p>
        ) : hasAnyData ? (
          <div className="space-y-2.5">
            {rows.map(r => (
              <div key={r.label} className="flex items-center justify-between text-sm">
                <span className="text-secondary">{r.label}</span>
                <span className="flex items-center gap-2">
                  <span className="font-mono text-xs text-muted">{r.asOf ?? '—'}</span>
                  <span className={`text-xs font-medium ${FRESHNESS_TINT[r.freshness] ?? 'text-muted'}`}>
                    {FRESHNESS_LABEL[r.freshness] ?? r.freshness}
                  </span>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-start gap-2.5">
            <AlertCircle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
            <p className="text-sm text-secondary leading-relaxed">
              尚未下載台股資料，仍可先進入系統。
            </p>
          </div>
        )}
      </div>

      {/* 底部操作 */}
      <div className="mt-6 flex items-center justify-between">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 px-3 h-9 rounded-btn text-sm text-secondary hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          上一步
        </button>
        <button
          onClick={onNext}
          className="inline-flex items-center gap-2 px-5 h-9 rounded-xl bg-accent text-white text-sm font-semibold hover:bg-accent/90 transition-colors"
        >
          下一步
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}

// ===== Step 3: 完成 =====

function FinishStep({ onNext, onBack, pending }: { onNext: () => void; onBack: () => void; pending: boolean }) {
  const tips = [
    { icon: ScanSearch, text: '「台股選股」以篩選策略掃描上市櫃標的' },
    { icon: TrendingUp, text: '「個股分析」檢視法人、融資融券與關鍵價位' },
    { icon: Scale, text: '「多股比較」並列檢視多檔台股表現' },
  ]

  return (
    <div className="text-center">
      <div className="mx-auto w-fit">
        <div className="rounded-card border border-border bg-surface p-5">
          <CheckCircle2 className="h-12 w-12 text-success" />
        </div>
      </div>

      <h1 className="mt-6 text-lg font-bold text-foreground">設定完成</h1>
      <p className="mt-2.5 text-sm text-secondary leading-relaxed max-w-md mx-auto">
        可以開始使用台股核心功能；資料來源與 AI 可隨時到「設定」調整。
      </p>

      {/* 快速上手入口 */}
      <div className="mt-6 space-y-2 text-left">
        {tips.map(t => (
          <div
            key={t.text}
            className="flex items-center gap-3 rounded-card border border-border bg-surface px-3.5 py-2.5"
          >
            <div className="rounded-lg bg-accent/10 p-1.5 shrink-0">
              <t.icon className="h-3.5 w-3.5 text-accent" />
            </div>
            <span className="text-xs text-secondary">{t.text}</span>
          </div>
        ))}
      </div>

      {/* 底部操作 */}
      <div className="mt-8 flex items-center justify-between">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 px-3 h-10 rounded-btn text-sm text-secondary hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          上一步
        </button>
        <button
          onClick={onNext}
          disabled={pending}
          className="inline-flex items-center gap-2 px-6 h-10 rounded-xl bg-accent text-white text-sm font-semibold shadow-lg shadow-accent/20 hover:bg-accent/90 hover:shadow-accent/30 disabled:opacity-60 transition-all"
        >
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {pending ? '正在進入…' : '進入面板'}
        </button>
      </div>
    </div>
  )
}
