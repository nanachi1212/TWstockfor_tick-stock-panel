import { Loader2, Bookmark, Save } from 'lucide-react'

interface SaveStrategyModalProps {
  open: boolean
  name: string
  setName: (value: string) => void
  description: string
  setDescription: (value: string) => void
  saving: boolean
  onClose: () => void
  onSave: () => void
}

/** 儲存自訂選股策略對話框。條件組裝與 API 呼叫仍由頁面持有。 */
export function SaveStrategyModal({ open, name, setName, description, setDescription, saving, onClose, onSave }: SaveStrategyModalProps) {
  const showSaveModal = open
  const newStrategyName = name
  const setNewStrategyName = setName
  const newStrategyDesc = description
  const setNewStrategyDesc = setDescription
  return (
    <>
    {showSaveModal && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 w-full max-w-md shadow-2xl space-y-4">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
            <div className="flex items-center gap-2">
              <Bookmark className="w-5 h-5 text-purple-400" />
              <h3 className="text-sm font-semibold text-zinc-100">儲存自訂選股策略</h3>
            </div>
            <button
              type="button"
              onClick={() => onClose()}
              className="text-zinc-500 hover:text-zinc-300 text-sm"
            >
              ✕
            </button>
          </div>

          <p className="text-xs text-zinc-400 leading-relaxed">
            將目前的篩選條件（交易所、價量、技術面、法人、基本面估值與 Quant 分數等）儲存為專屬策略，未來可一鍵載入。
          </p>

          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-zinc-300 block mb-1">
                策略名稱 <span className="text-red-400">*</span>
              </label>
              <input
                type="text"
                placeholder="例: 外資低估值營收成長股"
                value={newStrategyName}
                onChange={e => setNewStrategyName(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-800 focus:border-purple-500 rounded-lg px-3 py-2 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-zinc-300 block mb-1">
                策略說明 (選填)
              </label>
              <textarea
                placeholder="說明策略的投資邏輯或適用情境..."
                rows={3}
                value={newStrategyDesc}
                onChange={e => setNewStrategyDesc(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-800 focus:border-purple-500 rounded-lg px-3 py-2 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-zinc-800">
            <button
              type="button"
              onClick={() => onClose()}
              className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs font-medium transition-colors"
            >
              取消
            </button>
            <button
              type="button"
              disabled={!newStrategyName.trim() || saving}
              onClick={onSave}
              className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-medium transition-colors flex items-center gap-1.5 shadow-sm"
            >
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              儲存策略
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  )
}
