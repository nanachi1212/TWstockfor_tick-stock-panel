import {
  RadioTower,
  Square,
  GitFork,
  Sparkles,
  Star,
  LineChart,
  ScanSearch,
  History,
  Signal as SignalIcon,
  Eye,
  FileText,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { RADIX } from '@/lib/radix'

interface Variant {
  id: string
  name: string
  tagline: string
  hint: string
  icon: React.ComponentType<{ className?: string }>
  iconAccent: string                  // tailwind text color
  nameClass: string                   // 文字本身樣式(字號/字重/字距/字體)
  accent: string
}

// 同一個名字 "Nanachi 的台股監控看板" 在 4 種風格語言裡的呈現
// 長字符串自動用更小字號 + 更窄字距,免得撐爆卡片;但風格語言(字體/字重/配色/圖標)保持不變
const VARIANTS: Variant[] = [
  {
    id: 'pulsar',
    name: 'Nanachi 的台股監控看板',
    tagline: 'TAIWAN STOCK SIGNAL TERMINAL',
    hint: '高辨識度圖標與穩健字重',
    icon: RadioTower,
    iconAccent: 'text-success',
    nameClass: 'font-sans font-black text-base tracking-[0.10em]',
    accent: RADIX.success,
  },
  {
    id: 'vanta',
    name: 'Nanachi 的台股監控看板',
    tagline: 'MARKET INTELLIGENCE',
    hint: '單色圖標與寬字距',
    icon: Square,
    iconAccent: 'text-foreground',
    nameClass: 'font-sans font-black text-base tracking-[0.18em]',
    accent: RADIX.neutral,
  },
  {
    id: 'helix',
    name: 'Nanachi 的台股監控看板',
    tagline: 'QUANT TERMINAL',
    hint: '等寬字體與量化識別色',
    icon: GitFork,
    iconAccent: 'text-ai',
    nameClass: 'font-mono font-bold text-base tracking-[0.08em]',
    accent: RADIX.brand,
  },
  {
    id: 'aurora',
    name: 'Nanachi 的台股監控看板',
    tagline: 'TAIWAN STOCK DASHBOARD',
    hint: '輕字重與清晰資訊層級',
    icon: Sparkles,
    iconAccent: 'text-cyan-400',
    nameClass: 'font-sans font-light text-base tracking-[0.12em]',
    accent: RADIX.cyan,
  },
]

const MOCK_NAV = [
  { icon: Star, label: '自選' },
  { icon: LineChart, label: 'K 線' },
  { icon: ScanSearch, label: '策略' },
  { icon: History, label: '回測' },
  { icon: SignalIcon, label: '信號' },
  { icon: Eye, label: '監控' },
  { icon: FileText, label: '財務分析' },
]

export function Branding() {
  return (
    <>
      <PageHeader
        title="視覺風格預覽"
        subtitle="比較品牌標誌與字體配置"
      />

      <div className="px-8 py-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {VARIANTS.map((v) => (
            <Sample key={v.id} v={v} />
          ))}
        </div>

      </div>
    </>
  )
}

function Sample({ v }: { v: Variant }) {
  const Icon = v.icon

  return (
    <div className="flex overflow-hidden rounded-card border border-border bg-base">
      {/* 模擬側邊欄 */}
      <div className="w-56 bg-surface border-r border-border flex flex-col">
        {/* Logo 區 */}
        <div className="px-5 py-5 border-b border-border">
          <div className="flex items-center gap-2.5">
            <div className="grid h-7 w-7 place-items-center rounded-md bg-elevated">
              <Icon className={`h-4 w-4 ${v.iconAccent}`} />
            </div>
            <div className={`${v.nameClass} text-foreground leading-none`}>
              {v.name}
            </div>
          </div>
          <div className="mt-2 text-xs uppercase tracking-[0.18em] text-secondary">
            {v.tagline}
          </div>
          <div className="mt-3 border-t border-border" />
          <div className="mt-2 text-xs text-secondary">
            檔位，<span className="text-foreground font-medium font-mono">Pro</span>
          </div>
        </div>

        {/* 模擬導航 */}
        <nav className="px-2 py-3 space-y-0.5">
          {MOCK_NAV.slice(0, 5).map(({ icon: I, label }, i) => (
            <div
              key={label}
              className={`flex items-center gap-3 px-3 py-2 rounded-btn text-sm ${
                i === 0
                  ? 'bg-elevated text-foreground font-medium'
                  : 'text-foreground/80'
              }`}
            >
              <I className="h-4 w-4" />
              {label}
            </div>
          ))}
        </nav>
      </div>

      {/* 右側說明 + 大字預覽 */}
      <div className="flex-1 p-5 flex flex-col">
        <div className="flex-1">
          <div className="text-xs font-medium text-muted uppercase tracking-widest">{v.id}</div>
          <div className="mt-2 leading-relaxed text-sm text-secondary">
            {v.hint}
          </div>
        </div>

        {/* 大字 wordmark 預覽 */}
        <div className="mt-6 pt-6 border-t border-border">
          <div className={`${v.nameClass} text-foreground`}>
            {v.name}
          </div>
          <div className="mt-1.5 text-xs uppercase tracking-[0.2em] text-secondary">
            {v.tagline}
          </div>
        </div>

        {/* 模擬一個數據卡片,看與配色協調度 */}
        <div className="mt-5 rounded-btn bg-surface border border-border px-3 py-2 flex items-baseline justify-between">
          <span className="text-xs text-secondary">2330.TWSE</span>
          <span className="font-mono text-sm" style={{ color: v.accent }}>
            +1.85%
          </span>
        </div>
      </div>
    </div>
  )
}
