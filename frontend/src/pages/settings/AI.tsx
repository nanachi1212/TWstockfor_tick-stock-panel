import { useState, useEffect, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Save, Loader2, Check, Wifi, WifiOff, Eye, EyeOff, Shield,
  Shuffle, Plug, Zap, Settings2, ExternalLink, Trash2,
} from 'lucide-react'
import { useSettings } from '@/lib/useSharedQueries'
import { api, type SettingsState } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

// 統一的輸入框樣式(與項目其他設置頁一致)
const INPUT_CLS =
  'w-full h-9 px-2.5 rounded-lg bg-base border-0 ring-1 ring-border/30 text-xs font-mono text-foreground placeholder:text-muted/30 focus:outline-none focus:ring-2 focus:ring-accent/30 transition-shadow'

// 空/非法輸入 → undefined (後端保持原值), 合法正整數 → int
const toPositiveInt = (v: string) => {
  const n = parseInt(v, 10)
  return Number.isInteger(n) && n > 0 ? n : undefined
}

const OPENAI_PROVIDER = 'openai'
const OPENAI_COMPAT_PROVIDER = 'openai_compat'
const DEFAULT_OPENAI_MODEL = 'gpt-5.5'
const DEFAULT_REASONING_EFFORT = 'high'

type AiPreset = { label: string; provider?: string; url: string; model: string; website: string; websiteLabel: string; description: string; custom?: boolean }

const PRESETS: AiPreset[] = [
  { label: '自訂', url: '', model: '', website: '', websiteLabel: '', description: '不自動填入任何設定,完全手動填寫 API 位址、模型和金鑰。', custom: true },
  { label: 'OpenAI', provider: OPENAI_PROVIDER, url: 'https://api.openai.com/v1', model: DEFAULT_OPENAI_MODEL, website: 'https://platform.openai.com/', websiteLabel: 'platform.openai.com', description: 'OpenAI 官方介面,可個別設定模型支援的推理強度。' },
]

const findPreset = (provider: string) => PRESETS.find(p => p.provider === provider) ?? PRESETS[0]

export function SettingsAIPanel() {
  const qc = useQueryClient()
  const settings = useSettings()
  const s = settings.data

  const [provider, setProvider] = useState(OPENAI_COMPAT_PROVIDER)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [reasoningEffort, setReasoningEffort] = useState(DEFAULT_REASONING_EFFORT)
  const [customUa, setCustomUa] = useState(false)
  const [userAgent, setUserAgent] = useState('')
  const [maxOutputTokens, setMaxOutputTokens] = useState('')
  const [contextWindow, setContextWindow] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [saved, setSaved] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [selectedPresetLabel, setSelectedPresetLabel] = useState(PRESETS[0].label)
  const directDrafts = useRef({
    custom: { baseUrl: '', model: '' },
    openai: { baseUrl: 'https://api.openai.com/v1', model: DEFAULT_OPENAI_MODEL },
  })
  const draftsInitialized = useRef(false)

  const isOpenAIProvider = provider === OPENAI_PROVIDER
  const configured = s?.ai_configured ?? s?.has_ai_key
  const selectedPreset = PRESETS.find(p => p.label === selectedPresetLabel) ?? PRESETS[0]
  const configTitle = isOpenAIProvider ? 'OpenAI 設定' : '自訂設定'
  const canSave = !!baseUrl.trim() && !!model.trim()

  useEffect(() => {
    if (!s) return
    // 未配置過 AI (無 api_key): 字段留空, 默認選中"自定義"預設, 不預填充後端默認值
    const unconfigured = !s.has_ai_key && !s.ai_configured
    // ponytail: legacy/unknown providers remain persisted, but the current UI only exposes generic custom or OpenAI.
    const savedProvider = s.ai_provider === OPENAI_PROVIDER ? OPENAI_PROVIDER : OPENAI_COMPAT_PROVIDER
    const savedBaseUrl = unconfigured ? '' : (s.ai_base_url ?? '')
    const savedOpenAIModel = unconfigured ? '' : (s.ai_openai_model ?? s.ai_model ?? '')
    const savedPreset = unconfigured ? PRESETS[0] : findPreset(savedProvider)
    if (!draftsInitialized.current) {
      if (savedProvider === OPENAI_PROVIDER) {
        directDrafts.current.openai = { baseUrl: savedBaseUrl, model: savedOpenAIModel }
      } else {
        directDrafts.current.custom = { baseUrl: savedBaseUrl, model: savedOpenAIModel }
      }
      draftsInitialized.current = true
    }
    setProvider(savedProvider)
    setSelectedPresetLabel(savedPreset.label)
    setBaseUrl(savedBaseUrl)
    setModel(savedOpenAIModel)
    setReasoningEffort(s.ai_reasoning_effort ?? DEFAULT_REASONING_EFFORT)
    const ua = s.ai_user_agent ?? ''
    setCustomUa(!!ua)
    setUserAgent(ua)
    setMaxOutputTokens(String(s?.ai_max_output_tokens ?? 8192))
    setContextWindow(String(s?.ai_context_window ?? 64000))
  }, [s])

  const payload = () => ({
    provider,
    base_url: baseUrl,
    api_key: apiKey || undefined,
    model,
    ...(isOpenAIProvider ? { reasoning_effort: reasoningEffort } : {}),
    user_agent: customUa ? userAgent : '',
    max_output_tokens: toPositiveInt(maxOutputTokens),
    context_window: toPositiveInt(contextWindow),
  })

  const save = useMutation({
    mutationFn: () => api.saveAiSettings(payload()),
    onSuccess: (result) => {
      setSaved(true)
      setApiKey('')
      qc.setQueryData<SettingsState>(QK.settings, prev => prev ? {
        ...prev,
        ai_provider: result.ai_provider ?? provider,
        ai_base_url: baseUrl,
        ai_model: result.ai_model ?? model,
        ai_openai_model: result.ai_openai_model ?? model,
        ai_reasoning_effort: result.ai_reasoning_effort ?? reasoningEffort,
        ai_configured: result.ai_configured ?? (apiKey ? true : prev.ai_configured),
        ai_max_output_tokens: result.ai_max_output_tokens ?? toPositiveInt(maxOutputTokens),
        ai_context_window: result.ai_context_window ?? toPositiveInt(contextWindow),
        ...(apiKey ? {
          has_ai_key: true,
          ai_api_key_masked: `${apiKey.slice(0, 4)}......${apiKey.slice(-4)}`,
        } : {}),
      } : prev)
      qc.invalidateQueries({ queryKey: QK.settings })
      setTimeout(() => setSaved(false), 2000)
    },
  })

  const clear = useMutation({
    mutationFn: () => api.clearAiSettings(),
    onSuccess: () => {
      setConfirmClear(false)
      setProvider(OPENAI_COMPAT_PROVIDER)
      setSelectedPresetLabel(PRESETS[0].label)
      setBaseUrl('')
      setApiKey('')
      setModel('')
      setReasoningEffort(DEFAULT_REASONING_EFFORT)
      directDrafts.current = {
        custom: { baseUrl: '', model: '' },
        openai: { baseUrl: 'https://api.openai.com/v1', model: DEFAULT_OPENAI_MODEL },
      }
      setMaxOutputTokens('8192')
      setContextWindow('64000')
      setTestResult(null)
      qc.setQueryData<SettingsState>(QK.settings, prev => prev ? {
        ...prev,
        ai_provider: OPENAI_COMPAT_PROVIDER,
        ai_base_url: '',
        ai_model: '',
        ai_openai_model: '',
        ai_reasoning_effort: DEFAULT_REASONING_EFFORT,
        ai_codex_model: '',
        ai_codex_command: '',
        ai_codex_reasoning_effort: '',
        ai_max_output_tokens: 8192,
        ai_context_window: 64000,
        has_ai_key: false,
        ai_configured: false,
        ai_api_key_masked: '',
      } : prev)
      qc.invalidateQueries({ queryKey: QK.settings })
    },
  })

  const genRandomUa = () => {
    const major = 128 + Math.floor(Math.random() * 8)
    const platforms = [
      'Windows NT 10.0; Win64; x64',
      'Macintosh; Intel Mac OS X 10_15_7',
      'X11; Linux x86_64',
    ]
    const pf = platforms[Math.floor(Math.random() * platforms.length)]
    setUserAgent(`Mozilla/5.0 (${pf}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${major}.0.0.0 Safari/537.36`)
  }

  const handlePreset = (p: AiPreset) => {
    setSelectedPresetLabel(p.label)
    if (p.custom) {
      setProvider(OPENAI_COMPAT_PROVIDER)
      setBaseUrl(directDrafts.current.custom.baseUrl)
      setModel(directDrafts.current.custom.model)
      return
    }
    const nextProvider = p.provider ?? OPENAI_COMPAT_PROVIDER
    setProvider(nextProvider)
    if (nextProvider === OPENAI_PROVIDER) {
      setBaseUrl(directDrafts.current.openai.baseUrl)
      setModel(directDrafts.current.openai.model)
    } else {
      setBaseUrl(p.url)
      setModel(p.model)
    }
  }

  const handleBaseUrlChange = (value: string) => {
    setBaseUrl(value)
    if (selectedPreset.custom) directDrafts.current.custom.baseUrl = value
    if (isOpenAIProvider) directDrafts.current.openai.baseUrl = value
  }

  const handleModelChange = (value: string) => {
    setModel(value)
    if (selectedPreset.custom) directDrafts.current.custom.model = value
    if (isOpenAIProvider) directDrafts.current.openai.model = value
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      if (canSave) await api.saveAiSettings(payload())
      const r = await api.strategyAiTest()
      setTestResult({ ok: r.ok, msg: r.ok ? `連線成功 · ${r.model ?? provider}` : (r.error ?? '未知錯誤') })
    } catch (e: any) {
      setTestResult({ ok: false, msg: String(e?.message ?? '測試失敗') })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-5 max-w-2xl">
      <Card icon={Plug} title="連線狀態" right={
        configured && (
          <button onClick={handleTest} disabled={testing}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-btn bg-elevated hover:bg-elevated/80 text-xs text-secondary transition-colors duration-150 ease-smooth disabled:opacity-50">
            {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wifi className="h-3 w-3" />}
            {testing ? '測試中' : '測試'}
          </button>
        )
      }>
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${configured ? 'bg-emerald-400/10 text-emerald-400' : 'bg-amber-400/10 text-amber-400'}`}>
            {configured ? <Wifi className="h-4.5 w-4.5" /> : <WifiOff className="h-4.5 w-4.5" />}
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">{configured ? 'AI 已連線' : 'AI 未設定'}</div>
            <div className="text-xs text-muted mt-0.5 truncate">
              {configured
                ? [s?.ai_model, s?.ai_api_key_masked].filter(Boolean).join(' · ') || '已儲存 AI 設定'
                : '設定 API Key 後即可使用 AI 功能。'}
            </div>
          </div>
        </div>
        {testResult && (
          <div className={`mt-3 rounded-btn border px-3 py-2 text-xs flex items-center gap-2 ${testResult.ok ? 'border-emerald-400/20 bg-emerald-400/[0.04] text-emerald-400' : 'border-danger/20 bg-danger/[0.04] text-danger'}`}>
            <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${testResult.ok ? 'bg-emerald-400' : 'bg-danger'}`} />
            {testResult.msg}
          </div>
        )}
      </Card>

      <Card icon={Zap} title="快速預設">
        <div className="flex flex-wrap items-start gap-2">
          {PRESETS.map(p => (
            <button key={p.label} onClick={() => handlePreset(p)} aria-pressed={selectedPreset.label === p.label}
              className={`rounded-lg border px-3 py-2 text-left transition-all ${selectedPreset?.label === p.label ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border bg-base text-secondary hover:border-accent/30'}`}>
              <div className="flex items-center gap-1.5 text-xs font-medium">
                <span>{p.label}</span>
              </div>
            </button>
          ))}
        </div>
        {selectedPreset && (
          <div className="mt-3 rounded-btn border border-border/30 bg-base/30 px-3 py-2 text-[11px] leading-relaxed">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-secondary">{selectedPreset.description}</span>
            </div>
            {selectedPreset.website && (
              <a href={selectedPreset.website} target="_blank" rel="noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-muted hover:text-accent transition-colors">
                {selectedPreset.websiteLabel}
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
        )}
      </Card>

      <Card
        icon={Settings2}
        title={configTitle}
        right={
          <span className="inline-flex items-center gap-1.5 text-[10px] text-muted/60" title="Use OpenAI-compatible Chat Completions API">
            <span className="rounded-full border border-border/40 bg-base/50 px-1.5 py-px font-mono">Chat Completions</span>
            介面
          </span>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="API 位址">
              <input type="text" value={baseUrl} onChange={e => handleBaseUrlChange(e.target.value)} placeholder="https://api.example.com/v1" className={INPUT_CLS} />
            </Field>
            <Field label="模型">
              <input type="text" value={model} onChange={e => handleModelChange(e.target.value)} placeholder="your-model-id" className={INPUT_CLS} />
            </Field>
          </div>

          {isOpenAIProvider && (
            <div className="rounded-lg border border-accent/15 bg-accent/[0.03] p-3">
              <div className="mb-2.5">
                <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">OpenAI 專屬</span>
              </div>
              <div className="max-w-xs">
                <Field label="推理強度">
                  <input type="text" value={reasoningEffort} onChange={e => setReasoningEffort(e.target.value)} placeholder={DEFAULT_REASONING_EFFORT} className={INPUT_CLS} />
                </Field>
              </div>
            </div>
          )}

          <Field label="API Key">
            <div className="flex gap-2">
              <div className="flex-1 relative">
                <input type={showKey ? 'text' : 'password'} value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder={configured ? `${s?.ai_api_key_masked} · 留空不修改` : 'sk-...'} className={`${INPUT_CLS} pr-9`} />
                <button onClick={() => setShowKey(v => !v)} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted/40 hover:text-muted" tabIndex={-1} aria-label={showKey ? '隱藏' : '顯示'}>
                  {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
              <button onClick={handleTest} disabled={testing || !apiKey} className="h-9 px-3 rounded-lg border border-border/50 text-xs text-secondary hover:text-accent hover:border-accent/30 disabled:opacity-40 transition-all flex items-center gap-1.5 shrink-0">
                {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wifi className="h-3 w-3" />}
                測試
              </button>
            </div>
          </Field>

          <div className="border-t border-border/20" />

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Field label="自訂 User-Agent" inline>
                <Toggle checked={customUa} onChange={() => setCustomUa(v => !v)} />
              </Field>
            </div>
            {customUa && (
              <div className="flex gap-2">
                <input type="text" value={userAgent} onChange={e => setUserAgent(e.target.value)} placeholder="貼上瀏覽器 User-Agent" className={`${INPUT_CLS} flex-1`} />
                <button type="button" onClick={genRandomUa} title="隨機產生瀏覽器 User-Agent" className="h-9 px-2.5 rounded-lg border border-border/50 text-xs text-secondary hover:text-accent hover:border-accent/30 transition-all flex items-center gap-1.5 shrink-0">
                  <Shuffle className="h-3 w-3" /> 隨機
                </button>
              </div>
            )}
          </div>

          <div className="border-t border-border/20 pt-4">
            <div className="grid grid-cols-2 gap-4">
              <Field label="輸出上限 max_tokens" hint="所有 AI 任務的輸出 token 上限,任務請求會被鉗制到此值;預設 8192">
                <input type="number" min={1} value={maxOutputTokens} onChange={e => setMaxOutputTokens(e.target.value)} placeholder="8192" className={INPUT_CLS} />
              </Field>
              <Field label="上下文視窗 (輸入上限)" hint="輸入估算超出此視窗時會報錯並提示調大;預設 64000">
                <input type="number" min={1} value={contextWindow} onChange={e => setContextWindow(e.target.value)} placeholder="64000" className={INPUT_CLS} />
              </Field>
            </div>
          </div>
        </div>
      </Card>

      <div className="rounded-card border border-amber-400/20 bg-amber-400/[0.04] px-4 py-3 flex items-start gap-3">
        <Shield className="h-4 w-4 text-amber-400/70 mt-0.5 shrink-0" />
        <div className="text-[11px] text-amber-400/70 leading-relaxed">
          API Key 僅儲存在本機專案檔案中,不會上傳到任何伺服器。請妥善保管。
        </div>
      </div>

      <div className="flex gap-2">
        <button onClick={() => save.mutate()} disabled={save.isPending || !canSave} className="flex-1 h-10 rounded-xl bg-accent text-white text-sm font-semibold flex items-center justify-center gap-2 hover:bg-accent/90 disabled:opacity-40 transition-all">
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : saved ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {save.isPending ? '儲存中…' : saved ? '已儲存' : '儲存設定'}
        </button>
        {configured && (
          <button onClick={() => setConfirmClear(true)} disabled={clear.isPending} className="h-10 px-4 rounded-xl bg-elevated text-secondary hover:text-danger text-sm flex items-center justify-center gap-1.5 hover:bg-elevated/80 disabled:opacity-50 transition-all shrink-0" title="Clear AI provider configuration">
            <Trash2 className="h-4 w-4" />
            清空
          </button>
        )}
      </div>

      {confirmClear && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setConfirmClear(false)} />
          <div className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base shadow-2xl p-6">
            <h3 className="text-sm font-medium text-foreground mb-2">清空 AI 設定</h3>
            <p className="text-xs text-secondary mb-5 leading-relaxed">
              這會清空已儲存的 provider、API Key、API 位址、模型和相關 AI 設定。之後可以重新設定。
            </p>
            <div className="flex items-center justify-end gap-2">
              <button onClick={() => setConfirmClear(false)} className="px-3 py-1.5 rounded-btn bg-elevated text-secondary hover:bg-elevated/80 text-sm transition-colors">
                取消
              </button>
              <button onClick={() => clear.mutate()} disabled={clear.isPending} className="px-3 py-1.5 rounded-btn bg-danger/15 text-danger hover:bg-danger/25 text-sm font-medium transition-colors disabled:opacity-50">
                {clear.isPending ? '清空中…' : '確認'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ===== 通用卡片(與 Keys 頁風格統一) =====

interface CardProps {
  icon: React.ComponentType<{ className?: string }>
  title: string
  right?: React.ReactNode
  children: React.ReactNode
}

function Card({ icon: Icon, title, right, children }: CardProps) {
  return (
    <section className="rounded-card border border-border bg-surface p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <Icon className="h-4 w-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground">{title}</h2>
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}

// ===== 表單字段(統一 label + 輸入框樣式) =====

function Field({ label, hint, inline, children }: {
  label: string
  hint?: string
  inline?: boolean
  children: React.ReactNode
}) {
  if (inline) {
    return (
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[10px] text-muted/50 uppercase tracking-wider">{label}</div>
          {hint && <div className="text-[10px] text-muted mt-0.5">{hint}</div>}
        </div>
        {children}
      </div>
    )
  }
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] text-muted/50 uppercase tracking-wider">{label}</div>
      {children}
      {hint && <div className="text-[10px] text-muted">{hint}</div>}
    </div>
  )
}

// ===== 開關 =====

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      onClick={onChange}
      className={`relative inline-flex h-5 w-9 items-center rounded-full shrink-0 transition-colors duration-200 ${checked ? 'bg-accent' : 'bg-elevated'}`}
      aria-pressed={checked}
    >
      <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${checked ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
    </button>
  )
}
