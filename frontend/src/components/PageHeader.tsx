import { useEffect, useRef } from 'react'
import { MoreHorizontal } from 'lucide-react'
import { cn } from '@/lib/cn'

interface Props {
  title: string
  /** Small status next to the title (a count, a date) — never a sentence. */
  titleExtra?: React.ReactNode
  /** The page's one main action. */
  primary?: React.ReactNode
  /** Secondary actions, folded into the「⋯」menu. */
  more?: React.ReactNode
  /** Terms, chart readings and category meanings for beginners, shown open under「名詞說明」. */
  glossary?: React.ReactNode
  /** Units, sources and caveats, folded under「說明」. */
  help?: React.ReactNode
  className?: string
}

/** Page convention: title, one primary action, everything else folded (more / help). */
export function PageHeader({ title, titleExtra, primary, more, glossary, help, className }: Props) {
  return (
    <header className={cn('border-b border-border px-5 py-3', className)}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          {titleExtra}
        </div>
        {(primary || more) && <div className="flex items-center gap-2">{primary}{more && <MoreMenu>{more}</MoreMenu>}</div>}
      </div>
      {glossary && <Help open label="名詞說明" className="mt-2">{glossary}</Help>}
      {help && <Help className="mt-2">{help}</Help>}
    </header>
  )
}

/** One row inside MoreMenu: icon + text label, so folded actions never rely on a tooltip. */
export const menuItemClass = 'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-foreground/85 hover:bg-elevated disabled:opacity-50 [&_svg]:h-4 [&_svg]:w-4 [&_svg]:shrink-0 [&_svg]:text-muted'

/** Secondary actions behind「⋯」; closes on an outside click or after choosing an item. */
export function MoreMenu({ children, label = '更多動作' }: { children: React.ReactNode; label?: string }) {
  const ref = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const close = (e: MouseEvent) => {
      const el = ref.current
      const item = (e.target as HTMLElement).closest('[data-menu-items] button, [data-menu-items] a')
      // Items that show their own result (copy → 已複製) mark themselves data-keep-open.
      if (el?.open && (!el.contains(e.target as Node) || (item && !item.closest('[data-keep-open]')))) el.open = false
    }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [])
  return (
    <details ref={ref} className="relative">
      <summary aria-label={label} title={label}
        className="flex h-9 w-9 cursor-pointer list-none items-center justify-center rounded-btn border border-border text-muted hover:bg-elevated hover:text-foreground [&::-webkit-details-marker]:hidden">
        <MoreHorizontal className="h-4 w-4" />
      </summary>
      <div data-menu-items
        className="absolute right-0 z-30 mt-1 flex min-w-52 flex-col gap-1 rounded-lg border border-border bg-surface p-1.5 shadow-lg [&>*]:w-full [&_button]:justify-start">
        {children}
      </div>
    </details>
  )
}

/** Folded text: 說明 (sources, units, caveats) starts closed; 名詞說明 (terms, charts) starts open. */
export function Help({ children, className, open = false, label = '說明' }: {
  children: React.ReactNode; className?: string; open?: boolean; label?: string
}) {
  return (
    <details open={open} className={cn('text-xs text-muted', className)}>
      <summary className="w-fit cursor-pointer select-none hover:text-foreground">{label}</summary>
      <div className="mt-2 max-w-3xl space-y-1 leading-relaxed">{children}</div>
    </details>
  )
}
