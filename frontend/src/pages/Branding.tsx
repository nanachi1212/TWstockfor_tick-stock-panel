import { motion } from 'framer-motion'
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

interface Variant {
  id: string
  name: string
  tagline: string
  hint: string
  icon: React.ComponentType<{ className?: string }>
  iconAccent: string                  // tailwind text color
  nameClass: string                   // 文字本身樣式(字號/字重/字距/字體)
  glow?: string                       // 名字下方的發光線條 hex
}

// 同一個名字 "Nanachi 的台股監控看板" 在 4 種風格語言裡的呈現
// 長字符串自動用更小字號 + 更窄字距,免得撐爆卡片;但風格語言(字體/字重/配色/圖標)保持不變
const VARIANTS: Variant[] = [
  {
    id: 'pulsar',
    name: 'Nanachi 的台股監控看板',
    tagline: 'TAIWAN STOCK · SIGNAL TERMINAL',
    hint: '脈衝星、雷達波紋 — 青綠強調色,字重黑體,中等字距',
    icon: RadioTower,
    iconAccent: 'text-[#3DD68C]',
    nameClass: 'font-sans font-black text-base tracking-[0.10em]',
    glow: '#3DD68C',
  },
  {
    id: 'vanta',
    name: 'Nanachi 的台股監控看板',
    tagline: 'MARKET · INTELLIGENCE',
    hint: 'Vantablack — 純白單色,字重最重,字距最寬,monochrome 高級感',
    icon: Square,
    iconAccent: 'text-[#FAFAFA]',
    nameClass: 'font-sans font-black text-base tracking-[0.18em]',
    glow: '#FAFAFA',
  },
  {
    id: 'helix',
    name: 'Nanachi 的台股監控看板',
    tagline: 'QUANT · TERMINAL',
    hint: 'DNA 螺旋 — 紫色強調,等寬字體,賽博朋克經典意象',
    icon: GitFork,
    iconAccent: 'text-[#8B5CF6]',
    nameClass: 'font-mono font-bold text-base tracking-[0.08em]',
    glow: '#8B5CF6',
  },
  {
    id: 'aurora',
    name: 'Nanachi 的台股監控看板',
    tagline: 'TAIWAN STOCK · DASHBOARD',
    hint: '極光 — 青色強調,細字優雅,適中字距,與漲跌語義色不衝突',
    icon: Sparkles,
    iconAccent: 'text-[#22D3EE]',
    nameClass: 'font-sans font-light text-base tracking-[0.12em]',
    glow: '#22D3EE',
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
        subtitle="名字保持 Nanachi 的台股監控看板,4 種賽博朋克 + 高級感的視覺處理 — 字重、字距、配色、圖標各不同。挑你最喜歡的告訴我。"
      />

      <div className="px-8 py-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {VARIANTS.map((v) => (
            <Sample key={v.id} v={v} />
          ))}
        </div>

        <div className="mt-8 rounded-card border border-border bg-surface p-5 text-sm text-secondary leading-relaxed max-w-2xl">
          <div className="font-medium text-foreground mb-2">挑哪個?</div>
          回覆 <code className="font-mono text-accent">pulsar / vanta / helix / aurora</code> 任一,
          我把該風格的字體、配色、圖標、發光效果應用到真實側欄。
          也可以告訴我你想微調哪裡(比如"用 VANTA 但換青色"),都行。
        </div>
      </div>
    </>
  )
}

function Sample({ v }: { v: Variant }) {
  const Icon = v.icon

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="rounded-card border border-border overflow-hidden bg-base flex"
    >
      {/* 模擬側邊欄 */}
      <div className="w-56 bg-surface border-r border-border flex flex-col">
        {/* Logo 區 */}
        <div className="px-5 py-5 border-b border-border">
          <div className="flex items-center gap-2.5">
            <div
              className="grid place-items-center h-7 w-7 rounded-md"
              style={{
                background: `${v.glow}1a`,
                boxShadow: `0 0 12px ${v.glow}33`,
              }}
            >
              <Icon className={`h-4 w-4 ${v.iconAccent}`} />
            </div>
            <div className={`${v.nameClass} text-foreground leading-none`}>
              {v.name}
            </div>
          </div>
          <div className="mt-2 text-[10px] uppercase tracking-[0.18em] text-secondary">
            {v.tagline}
          </div>
          <div
            className="mt-3 h-px"
            style={{
              background: `linear-gradient(90deg, ${v.glow}66, transparent)`,
            }}
          />
          <div className="mt-2 text-xs text-secondary">
            檔位 · <span className="text-foreground font-medium font-mono">Pro</span>
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
          <div
            className={`${v.nameClass} text-foreground`}
            style={{
              textShadow: `0 0 24px ${v.glow}55`,
            }}
          >
            {v.name}
          </div>
          <div className="mt-1.5 text-[10px] uppercase tracking-[0.2em] text-secondary">
            {v.tagline}
          </div>
        </div>

        {/* 模擬一個數據卡片,看與配色協調度 */}
        <div className="mt-5 rounded-btn bg-surface border border-border px-3 py-2 flex items-baseline justify-between">
          <span className="text-xs text-secondary">2330.TWSE</span>
          <span className="font-mono text-sm" style={{ color: v.glow }}>
            +1.85%
          </span>
        </div>
      </div>
    </motion.div>
  )
}
