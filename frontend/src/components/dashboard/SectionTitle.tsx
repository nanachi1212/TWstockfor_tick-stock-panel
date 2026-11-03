import { type ReactNode } from 'react'
import { Activity } from 'lucide-react'

export function SectionTitle({ icon: Icon, title, hint }: { icon: typeof Activity; title: string; hint?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      </div>
      {hint && <span className="font-mono text-xs text-muted">{hint}</span>}
    </div>
  )
}
