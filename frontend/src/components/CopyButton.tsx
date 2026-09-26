import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { copyToClipboard } from '@/lib/copy-formatters'
import { cn } from '@/lib/cn'

interface CopyButtonProps {
  getText: () => string
  label?: string
  successLabel?: string
  className?: string
  size?: 'sm' | 'xs'
}

export function CopyButton({
  getText,
  label = '複製資料',
  successLabel = '已複製',
  className,
  size = 'sm',
}: CopyButtonProps) {
  const [state, setState] = useState<'idle' | 'success' | 'error'>('idle')

  async function handleClick() {
    const text = getText()
    const ok = await copyToClipboard(text)
    setState(ok ? 'success' : 'error')
    setTimeout(() => setState('idle'), 2500)
  }

  const sizeCls = size === 'xs'
    ? 'px-2 py-1 text-[10px] gap-1'
    : 'px-3 py-1.5 text-xs gap-1.5'

  return (
    <div className="flex flex-col items-start gap-0.5">
      <button
        type="button"
        onClick={handleClick}
        className={cn(
          'inline-flex items-center rounded-lg border transition-colors duration-150',
          sizeCls,
          state === 'success'
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
            : state === 'error'
              ? 'border-rose-500/40 bg-rose-500/10 text-rose-500'
              : 'border-border/60 bg-surface text-muted hover:text-foreground hover:border-accent/40',
          className,
        )}
      >
        {state === 'success'
          ? <Check className="h-3 w-3 shrink-0" />
          : <Copy className="h-3 w-3 shrink-0" />}
        {state === 'success' ? successLabel : state === 'error' ? '複製失敗' : label}
      </button>
      {state === 'success' && (
        <span className="text-[10px] text-muted">
          已複製，可貼到 ChatGPT、Claude 或 Gemini。
        </span>
      )}
    </div>
  )
}
