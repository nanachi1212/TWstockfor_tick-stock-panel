/**
 * 設定 → 備份與轉移。
 *
 * 單檔 .twstock-backup 匯出 / 匯入, 用於家裡與辦公室電腦之間搬移個人設定與資料。
 * 不做雲端同步。API Keys 預設不備份; 勾選時必須設定密碼 (AES-256-GCM 加密)。
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, Upload, ShieldCheck, AlertTriangle, RotateCcw, RefreshCw } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { api, type BackupPreview } from '@/lib/api'
import {
  collectBrowserStorage, exportBackup, restoreBackup, saveBlob, undoRestore, type BrowserSnapshot,
} from '@/lib/deviceTransfer'

const PRESETS = [
  { id: 'settings', label: '只備份設定', desc: 'App 設定、介面偏好、監控規則、買點與選股策略' },
  { id: 'settings_portfolio', label: '設定 + 投資組合', desc: '再加上自選股與 Portfolio 成交紀錄' },
  { id: 'full', label: '完整私人備份', desc: '再加上選股回顧快照、提醒與每日摘要歷史' },
] as const

const inputCls = 'h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground focus:outline-none focus:border-accent/50'
const btnCls = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn text-xs transition-colors disabled:opacity-50 shrink-0'

function ErrorLine({ msg }: { msg: string }) {
  return (
    <div role="alert" className="mt-3 flex items-start gap-1.5 text-xs text-red-400">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />{msg}
    </div>
  )
}

export function SettingsBackupPanel() {
  const { data: options, error: optionsError } = useQuery({
    queryKey: ['device-transfer-options'],
    queryFn: api.deviceTransferOptions,
    staleTime: Infinity,
  })

  // ── 匯出 ──
  const [preset, setPreset] = useState<string>('settings')
  const [selected, setSelected] = useState<string[]>([])
  const [includeSecrets, setIncludeSecrets] = useState(false)
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportMsg, setExportMsg] = useState('')
  const [exportError, setExportError] = useState('')

  useEffect(() => {
    if (options) setSelected(options.presets[preset] ?? [])
  }, [options, preset])

  const minPw = options?.min_password_length ?? 8
  const pwProblem = !includeSecrets ? ''
    : password.length < minPw ? `密碼至少 ${minPw} 個字元`
    : password !== password2 ? '兩次輸入的密碼不一致' : ''

  const toggle = (list: string[], id: string, on: boolean) =>
    on ? [...list, id] : list.filter(x => x !== id)

  const handleExport = async () => {
    if (!options) return
    setExporting(true); setExportError(''); setExportMsg('')
    try {
      const { blob, filename } = await exportBackup({
        categories: selected,
        include_secrets: includeSecrets,
        password: includeSecrets ? password : undefined,
        browser_storage: collectBrowserStorage(options, selected),
      })
      saveBlob(blob, filename)
      setExportMsg(`已建立 ${filename}`)
      setPassword(''); setPassword2('')
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e))
    } finally {
      setExporting(false)
    }
  }

  // ── 匯入 ──
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<BackupPreview | null>(null)
  const [restoreCats, setRestoreCats] = useState<string[]>([])
  const [restorePw, setRestorePw] = useState('')
  const [busy, setBusy] = useState(false)
  const [importError, setImportError] = useState('')
  const [done, setDone] = useState<{ id: string; snapshot: BrowserSnapshot; written: number; removed: number } | null>(null)
  const [undone, setUndone] = useState(false)

  const handleFile = async (f: File | null) => {
    setFile(f); setPreview(null); setImportError(''); setDone(null); setUndone(false); setRestorePw('')
    if (!f) return
    setBusy(true)
    try {
      const p = await api.deviceTransferPreview(f)
      setPreview(p)
      setRestoreCats(p.categories.filter(c => c.compatible && c.id !== 'secrets').map(c => c.id))
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleRestore = async () => {
    if (!options || !file) return
    setBusy(true); setImportError('')
    try {
      const { result, browserSnapshot } = await restoreBackup(
        options, file, restoreCats, restoreCats.includes('secrets') ? restorePw : undefined,
      )
      setRestorePw('')
      setDone({ id: result.restore_point, snapshot: browserSnapshot, written: result.written, removed: result.removed })
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleUndo = async () => {
    if (!done) return
    setBusy(true); setImportError('')
    try {
      await undoRestore(done.id, done.snapshot)
      setUndone(true)
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const restoreBlocked = !preview || restoreCats.length === 0 || busy || !!done
    || (preview.issues.length > 0)
    || (restoreCats.includes('secrets') && !restorePw)

  return (
    <>
      <PageHeader title="備份與轉移" subtitle="用單一備份檔在家裡與辦公室電腦之間搬移個人設定與資料（不做雲端同步）" />
      {optionsError && <ErrorLine msg="無法載入備份選項" />}

      <section className="rounded-card border border-border bg-surface p-5 mb-4">
        <div className="flex items-center gap-2 mb-4">
          <Download className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-medium text-foreground">建立備份</h3>
        </div>

        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="備份範圍">
          {PRESETS.map(p => (
            <label key={p.id} className="flex gap-2 rounded-btn border border-border p-3 cursor-pointer hover:bg-elevated/40">
              <input type="radio" name="backup-preset" checked={preset === p.id} onChange={() => setPreset(p.id)}
                     className="mt-0.5 accent-accent" />
              <span>
                <span className="block text-sm text-foreground">{p.label}</span>
                <span className="block text-[11px] text-muted">{p.desc}</span>
              </span>
            </label>
          ))}
        </div>

        {options && (
          <fieldset className="mt-4">
            <legend className="text-[11px] text-muted mb-2">包含類別</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {options.categories.map(c => (
                <label key={c.id} className="flex items-center gap-1.5 text-xs text-secondary">
                  <input type="checkbox" checked={selected.includes(c.id)} className="accent-accent"
                         onChange={e => setSelected(s => toggle(s, c.id, e.target.checked))} />
                  {c.label}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div className="mt-4 rounded-btn border border-border p-3">
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input type="checkbox" checked={includeSecrets} className="accent-accent"
                   onChange={e => setIncludeSecrets(e.target.checked)} />
            <ShieldCheck className="h-3.5 w-3.5 text-accent" />
            包含 API Keys / FinMind / LINE / Telegram Token 與 AI Profile 金鑰（加密）
          </label>
          {includeSecrets && (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <input type="password" aria-label="備份密碼" placeholder={`備份密碼（至少 ${minPw} 字元）`}
                     value={password} onChange={e => setPassword(e.target.value)} className={inputCls} autoComplete="new-password" />
              <input type="password" aria-label="再次輸入備份密碼" placeholder="再次輸入備份密碼"
                     value={password2} onChange={e => setPassword2(e.target.value)} className={inputCls} autoComplete="new-password" />
              <p className="sm:col-span-2 text-[11px] text-muted">
                密碼不會儲存在任何地方；忘記密碼將無法還原這些金鑰。
              </p>
            </div>
          )}
          {!includeSecrets && <p className="mt-1 text-[11px] text-muted">預設不備份任何金鑰，到新電腦需重新輸入。</p>}
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button onClick={handleExport} disabled={!options || exporting || selected.length === 0 || !!pwProblem}
                  className={`${btnCls} bg-accent text-white hover:bg-accent/90`}>
            {exporting ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            {exporting ? '建立中…' : '建立備份'}
          </button>
          {pwProblem && <span className="text-[11px] text-amber-400">{pwProblem}</span>}
          {exportMsg && <span className="text-[11px] text-emerald-400">{exportMsg}</span>}
        </div>
        {exportError && <ErrorLine msg={exportError} />}
      </section>

      <section className="rounded-card border border-border bg-surface p-5">
        <div className="flex items-center gap-2 mb-4">
          <Upload className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-medium text-foreground">匯入備份</h3>
        </div>

        <input type="file" accept=".twstock-backup" aria-label="選擇備份檔"
               onChange={e => handleFile(e.target.files?.[0] ?? null)}
               className="text-xs text-secondary file:mr-3 file:rounded-btn file:border-0 file:bg-elevated file:px-3 file:py-1.5 file:text-xs file:text-secondary" />

        {busy && !preview && <p className="mt-3 text-xs text-muted">讀取備份內容…</p>}

        {preview && (
          <div className="mt-4 space-y-3" data-testid="backup-preview">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted">建立時間</dt>
              <dd className="text-foreground">{preview.created_at ? new Date(preview.created_at).toLocaleString('zh-TW') : '—'}</dd>
              <dt className="text-muted">App 版本</dt>
              <dd className="text-foreground">
                {preview.app_version ?? '—'}
                {!preview.version_match && (
                  <span className="ml-2 text-amber-400">與本機 {preview.current_app_version} 不同，已逐類檢查相容性</span>
                )}
              </dd>
              <dt className="text-muted">加密金鑰</dt>
              <dd className="text-foreground">{preview.secrets.included ? '包含（需密碼）' : '不包含'}</dd>
            </dl>

            {preview.issues.length > 0 && (
              <ErrorLine msg={`備份含無法識別的內容，不進行匯入：${preview.issues.join('；')}`} />
            )}

            <fieldset>
              <legend className="text-[11px] text-muted mb-2">選擇要還原的類別</legend>
              <div className="space-y-1.5">
                {preview.categories.map(c => (
                  <label key={c.id} className="flex items-start gap-2 text-xs">
                    <input type="checkbox" className="mt-0.5 accent-accent" disabled={!c.compatible || !!done}
                           checked={restoreCats.includes(c.id)}
                           onChange={e => setRestoreCats(s => toggle(s, c.id, e.target.checked))} />
                    <span>
                      <span className="text-foreground">{c.label}</span>
                      {c.id !== 'secrets' && <span className="ml-1 text-muted">（{c.file_count} 個檔案）</span>}
                      {!c.compatible && (
                        <span className="block text-red-400">不相容：{c.issues.join('；')}</span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            {restoreCats.includes('secrets') && !done && (
              <input type="password" aria-label="備份檔密碼" placeholder="輸入建立備份時設定的密碼"
                     value={restorePw} onChange={e => setRestorePw(e.target.value)} className={`${inputCls} max-w-sm`}
                     autoComplete="off" />
            )}

            {!done && (
              <>
                <p className="text-[11px] text-muted">
                  所選類別會以備份內容取代本機資料。寫入前會自動建立還原點，任何一步失敗都會整體復原。
                </p>
                <button onClick={handleRestore} disabled={restoreBlocked}
                        className={`${btnCls} bg-accent text-white hover:bg-accent/90`}>
                  {busy ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                  確認還原
                </button>
              </>
            )}

            {done && (
              <div role="status" className="rounded-btn border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
                <p className="text-emerald-400">
                  {undone ? '已復原到匯入前的狀態。' : `還原完成：寫入 ${done.written} 個檔案、移除 ${done.removed} 個舊檔案。`}
                </p>
                <p className="mt-1 text-muted">重新載入 App 讓所有設定生效。</p>
                <div className="mt-2 flex gap-2">
                  <button onClick={() => window.location.reload()} className={`${btnCls} bg-elevated text-secondary hover:text-foreground`}>
                    <RefreshCw className="h-3.5 w-3.5" />重新載入 App
                  </button>
                  {!undone && (
                    <button onClick={handleUndo} disabled={busy} className={`${btnCls} bg-elevated text-secondary hover:text-foreground`}>
                      <RotateCcw className="h-3.5 w-3.5" />復原此次還原
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
        {importError && <ErrorLine msg={importError} />}
      </section>
    </>
  )
}
