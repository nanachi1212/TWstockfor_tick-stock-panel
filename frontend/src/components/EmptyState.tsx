import { type LucideIcon, Construction } from 'lucide-react'

interface Props {
  icon?: LucideIcon
  title: string
  hint?: string
}

// §6.0.5 四態評審 — empty 狀態:圖示 + 引導,而不是一句"尚無資料"
export function EmptyState({ icon: Icon = Construction, title, hint }: Props) {
  return (
    <div className="h-full grid place-items-center px-8 py-16">
      <div className="text-center max-w-md">
        <Icon className="mx-auto h-8 w-8 text-muted" strokeWidth={1.5} />
        <h2 className="mt-3 text-base font-medium text-foreground">{title}</h2>
        {hint && <p className="mt-1.5 text-sm leading-relaxed text-secondary">{hint}</p>}
      </div>
    </div>
  )
}
