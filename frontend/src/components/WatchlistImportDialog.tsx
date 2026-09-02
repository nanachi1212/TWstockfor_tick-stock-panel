import { useCallback, useEffect, useRef, useState } from 'react'
import { ImagePlus, Loader2, Upload, X } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import { api, type WatchlistGroupColor, type WatchlistImportCandidate } from '@/lib/api'
import { useWatchlistBatchAdd } from '@/lib/useSharedMutations'
import { getOcrInstallHint } from '@/lib/ocrInstallHint'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'

interface Props {
  open: boolean
  onClose: () => void
  groupId?: string | null
  groupName?: string
  groupColor?: WatchlistGroupColor
}

/** 一次最多排队识别的图片数，避免误选大量文件拖垮小内存机器。 */
const MAX_IMPORT_IMAGES = 10

function isImageFile(file: File): boolean {
  return file.type.startsWith('image/') || /\.(jpe?g|png|webp|bmp|gif)$/i.test(file.name)
}

/** 按 code 合并多图 OCR 结果：优先保留已匹配项，已在自选取并集。 */
export function mergeImportCandidates(
  lists: WatchlistImportCandidate[][],
): WatchlistImportCandidate[] {
  const byCode = new Map<string, WatchlistImportCandidate>()
  for (const list of lists) {
    for (const c of list) {
      const prev = byCode.get(c.code)
      if (!prev) {
        byCode.set(c.code, c)
        continue
      }
      if (c.matched && !prev.matched) {
        byCode.set(c.code, c)
        continue
      }
      if (c.matched && prev.matched) {
        byCode.set(c.code, {
          ...prev,
          symbol: prev.symbol || c.symbol,
          name: prev.name || c.name,
          already_in_watchlist: prev.already_in_watchlist || c.already_in_watchlist,
        })
      }
    }
  }
  return [...byCode.values()]
}

export function WatchlistImportDialog({ open, onClose, groupId, groupName, groupColor }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const genRef = useRef(0)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [provider, setProvider] = useState<string>('')
  const [candidates, setCandidates] = useState<WatchlistImportCandidate[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [previewUrls, setPreviewUrls] = useState<string[]>([])
  const [ocrAvailable, setOcrAvailable] = useState<boolean | null>(null)
  const [installHint, setInstallHint] = useState('')
  const batchAdd = useWatchlistBatchAdd()

  const abortInFlight = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    genRef.current += 1
  }, [])

  const revokePreviews = useCallback((urls: string[]) => {
    for (const url of urls) URL.revokeObjectURL(url)
  }, [])

  const reset = useCallback(() => {
    abortInFlight()
    setBusy(false)
    setProgress(null)
    setCandidates([])
    setSelected(new Set())
    setProvider('')
    setOcrAvailable(null)
    setInstallHint('')
    setPreviewUrls(prev => {
      revokePreviews(prev)
      return []
    })
    if (inputRef.current) inputRef.current.value = ''
  }, [abortInFlight, revokePreviews])

  useEffect(() => {
    if (!open) {
      reset()
      return
    }
    let cancelled = false
    void api.watchlistOcrStatus().then(
      res => {
        if (cancelled) return
        setOcrAvailable(res.available)
        if (!res.available) setInstallHint(getOcrInstallHint())
      },
      () => {
        if (cancelled) return
        setOcrAvailable(false)
        setInstallHint(getOcrInstallHint())
      },
    )
    return () => {
      cancelled = true
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const runRecognizeQueue = async (files: File[]) => {
    const images = files.filter(isImageFile)
    if (images.length === 0) {
      toast('請選擇圖片檔案', 'error')
      return
    }
    if (images.length < files.length) {
      toast('已忽略非圖片檔案', 'error')
    }
    const queue = images.slice(0, MAX_IMPORT_IMAGES)
    if (images.length > MAX_IMPORT_IMAGES) {
      toast(`一次最多辨識 ${MAX_IMPORT_IMAGES} 張,已取前 ${MAX_IMPORT_IMAGES} 張`, 'error')
    }

    abortInFlight()
    const controller = new AbortController()
    abortRef.current = controller
    const gen = genRef.current

    setPreviewUrls(prev => {
      revokePreviews(prev)
      return queue.map(f => URL.createObjectURL(f))
    })
    setBusy(true)
    setProgress({ done: 0, total: queue.length })
    setCandidates([])
    setSelected(new Set())
    setProvider('')

    const mergedLists: WatchlistImportCandidate[][] = []
    let lastProvider = ''
    let failed = 0
    let lastError = ''

    try {
      for (let i = 0; i < queue.length; i++) {
        if (gen !== genRef.current || controller.signal.aborted) return
        try {
          // quiet：避免每张失败各弹一条 toast，结束时统一提示
          const res = await api.watchlistImportImage(queue[i], controller.signal, true)
          if (gen !== genRef.current) return
          lastProvider = res.provider
          mergedLists.push(res.candidates)
        } catch (err) {
          if (gen !== genRef.current) return
          if (controller.signal.aborted) return
          failed += 1
          lastError = err instanceof Error ? err.message : ''
        }
        if (gen === genRef.current) {
          setProgress({ done: i + 1, total: queue.length })
        }
      }

      if (gen !== genRef.current) return

      const merged = mergeImportCandidates(mergedLists)
      setProvider(lastProvider)
      setCandidates(merged)
      const defaults = new Set(
        merged
          .filter(c => c.matched && c.symbol && !c.already_in_watchlist)
          .map(c => c.symbol!),
      )
      setSelected(defaults)

      if (merged.length === 0) {
        toast(
          lastError
            || (failed > 0
              ? '辨識失敗或未辨識到股票代號,請換更清晰的截圖'
              : '未辨識到股票代號,請換一張更清晰的自選清單截圖'),
          'error',
        )
      } else if (merged.every(c => !c.matched)) {
        toast('辨識到代號但未能比對證券主檔資料', 'error')
      } else if (failed > 0) {
        toast(`有 ${failed} 張辨識失敗,已合併其餘結果`, 'error')
      }
    } finally {
      if (gen === genRef.current) {
        setBusy(false)
        setProgress(null)
      }
    }
  }

  const onPick = (list: FileList | File[] | null | undefined) => {
    if (!list || list.length === 0) return
    void runRecognizeQueue(Array.from(list))
  }

  const toggle = (symbol: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(symbol)) next.delete(symbol)
      else next.add(symbol)
      return next
    })
  }

  const matched = candidates.filter(c => c.matched && c.symbol)
  const selectable = matched.filter(c => !c.already_in_watchlist)
  const allSelected = selectable.length > 0 && selectable.every(c => selected.has(c.symbol!))

  const toggleAll = () => {
    if (allSelected) setSelected(new Set())
    else setSelected(new Set(selectable.map(c => c.symbol!)))
  }

  const confirmAdd = async () => {
    const symbols = [...selected]
    if (symbols.length === 0) {
      toast('請至少選擇一檔股票', 'error')
      return
    }
    try {
      const data = await batchAdd.mutateAsync({ symbols, groupId })
      toast(`已新增 ${data.added} 檔自選`, 'success')
      onClose()
    } catch {
      /* toast in request */
    }
  }

  if (!open) return null

  const ocrBlocked = ocrAvailable === false
  const progressLabel =
    progress && progress.total > 1
      ? `辨識中 ${progress.done}/${progress.total}…`
      : progress
        ? '辨識中…'
        : null
  const selectedGroupColor = resolveWatchlistGroupColor(groupColor)

  return (
    <Modal
      onClose={onClose}
      labelledBy="watchlist-import-title"
      panelClassName="w-[92vw] max-w-lg max-h-[85vh] flex flex-col bg-surface border border-border rounded-card shadow-xl"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div>
          <h2 id="watchlist-import-title" className="text-sm font-semibold text-foreground">
            從截圖匯入自選
          </h2>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <p className="text-[11px] text-muted">
              {ocrBlocked
                ? 'OCR 引擎不可用'
                : '可多選截圖,將逐張辨識並合併結果後確認新增'}
              {provider ? ` · ${provider}` : ''}
            </p>
            {groupName && (
              <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${selectedGroupColor.text} ${selectedGroupColor.border} ${selectedGroupColor.background}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${selectedGroupColor.dot}`} />
                {groupName}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="h-8 w-8 inline-flex items-center justify-center rounded-btn text-secondary hover:bg-elevated"
          aria-label="關閉"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="px-4 py-3 overflow-y-auto flex-1 space-y-3">
        {ocrBlocked ? (
          <div className="rounded-btn border border-border bg-elevated/40 px-4 py-5 text-xs text-secondary leading-relaxed whitespace-pre-wrap">
            {installHint}
          </div>
        ) : (
          <>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept="image/jpeg,image/png,image/webp,image/bmp,image/gif,.jpg,.jpeg,.png"
              className="hidden"
              onChange={e => {
                onPick(e.target.files)
                e.target.value = ''
              }}
            />

            <button
              type="button"
              disabled={busy || ocrAvailable === null}
              onClick={() => inputRef.current?.click()}
              onDragOver={e => { e.preventDefault(); e.stopPropagation() }}
              onDrop={e => {
                e.preventDefault()
                onPick(e.dataTransfer.files)
              }}
              className="w-full flex flex-col items-center justify-center gap-2 rounded-btn border border-dashed border-border bg-elevated/40 hover:bg-elevated/70 px-4 py-6 text-secondary transition-colors disabled:opacity-50"
            >
              {busy || ocrAvailable === null ? (
                <Loader2 className="h-6 w-6 animate-spin text-accent" />
              ) : (
                <ImagePlus className="h-6 w-6 text-accent" />
              )}
              <span className="text-xs">
                {progressLabel
                  ?? (ocrAvailable === null ? '檢查 OCR…' : '點擊選擇或拖曳截圖（支援多選）')}
              </span>
            </button>

            {previewUrls.length > 0 && (
              <div className="flex gap-2 overflow-x-auto pb-1">
                {previewUrls.map((url, i) => (
                  <div
                    key={url}
                    className="shrink-0 w-20 h-20 rounded-btn overflow-hidden border border-border bg-black/40"
                  >
                    <img
                      src={url}
                      alt={`預覽 ${i + 1}`}
                      className="w-full h-full object-contain"
                    />
                  </div>
                ))}
              </div>
            )}

            {candidates.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-secondary">
                    辨識 {candidates.length} 個代號 · 比對 {matched.length} · 已選 {selected.size}
                  </span>
                  {selectable.length > 0 && (
                    <button
                      type="button"
                      onClick={toggleAll}
                      className="text-[11px] text-accent hover:underline"
                    >
                      {allSelected ? '取消全選' : '全選可新增'}
                    </button>
                  )}
                </div>
                <ul className="divide-y divide-border/60 rounded-btn border border-border overflow-hidden">
                  {candidates.map(c => {
                    const key = c.symbol || c.code
                    const disabled = !c.matched || !c.symbol || c.already_in_watchlist
                    const checked = !!(c.symbol && selected.has(c.symbol))
                    return (
                      <li key={key}>
                        <label
                          className={`flex items-center gap-3 px-3 py-2.5 text-sm ${
                            disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:bg-elevated/50'
                          }`}
                        >
                          <input
                            type="checkbox"
                            disabled={disabled}
                            checked={checked}
                            onChange={() => c.symbol && toggle(c.symbol)}
                            className="rounded border-border"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-baseline gap-2">
                              <span className="font-medium text-foreground truncate">
                                {c.name || (c.matched ? c.symbol : '未比對')}
                              </span>
                              <span className="text-[11px] text-muted tabular-nums shrink-0">
                                {c.code}
                                {c.symbol ? ` · ${c.symbol}` : ''}
                              </span>
                            </div>
                            {c.already_in_watchlist && (
                              <span className="text-[10px] text-muted">已在自選</span>
                            )}
                            {!c.matched && (
                              <span className="text-[10px] text-warning/90">主檔資料未找到,已跳過</span>
                            )}
                          </div>
                        </label>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border shrink-0">
        <button
          type="button"
          onClick={onClose}
          className="h-8 px-3 rounded-btn text-xs text-secondary hover:bg-elevated"
        >
          取消
        </button>
        <button
          type="button"
          disabled={ocrBlocked || selected.size === 0 || batchAdd.isPending || busy}
          onClick={() => void confirmAdd()}
          className="h-8 px-3 rounded-btn text-xs inline-flex items-center gap-1.5 bg-accent text-white hover:bg-accent/90 disabled:opacity-40"
        >
          {batchAdd.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Upload className="h-3.5 w-3.5" />
          )}
          新增所選 ({selected.size})
        </button>
      </div>
    </Modal>
  )
}
