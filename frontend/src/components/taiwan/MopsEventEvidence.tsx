import { ExternalLink } from 'lucide-react'
import type { MarketEvent } from '@/lib/api'

const MOPS_TYPES = new Set(['material_information', 'investor_conference', 'insider_transfer_declaration'])

function taipeiTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '未提供' : date.toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false })
}

function officialPresentation(url: unknown): url is string {
  if (typeof url !== 'string') return false
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password && !parsed.port &&
      ['mops.twse.com.tw', 'mopsov.twse.com.tw', 'doc.twse.com.tw'].includes(parsed.hostname)
  } catch {
    return false
  }
}

export function MopsConferenceAvailability({ sources }: { sources?: Record<string, string> }) {
  if (sources?.['mops:conference:t100sb02_1'] !== 'unavailable') return null
  return <p role="status" className="my-2 text-[11px] text-amber-500">官方 MOPS 法說會資料目前不可用，無法確認完整事件清單。<a href="https://mopsov.twse.com.tw/mops/web/t100sb02_1" target="_blank" rel="noreferrer" className="ml-1 text-accent hover:underline">前往官方查詢</a></p>
}

/** Shared evidence labels for Event Center and Stock Detail. */
export function MopsEventEvidence({ event }: { event: MarketEvent }) {
  if (!MOPS_TYPES.has(event.event_type)) return null
  const details = event.details ?? {}
  const links: Array<{ label: string; url: string; method?: string; parameters?: Record<string, string> }> = Array.isArray(details.presentation_links)
    ? details.presentation_links.filter((link: any) => link && typeof link.label === 'string' && officialPresentation(link.url))
    : []
  const postFields = (link: typeof links[number]): Record<string, string> | null => {
    const fields = link.parameters
    if (link.url !== 'https://mopsov.twse.com.tw/server-java/FileDownLoad' || !fields ||
        Object.keys(fields).sort().join(',') !== 'fileName,filePath,functionName,step' ||
        fields.step !== '9' || fields.filePath !== '/home/html/nas/STR/' || fields.functionName !== 't100sb02_1' ||
        !/^[A-Za-z0-9_.-]{1,120}$/.test(fields.fileName ?? '')) return null
    return fields
  }
  const safeLinks = links.filter(link => link.method === 'POST' ? postFields(link) : !link.method || link.method === 'GET')
  return (
    <div className="mt-2 space-y-1.5 break-words text-[11px] text-secondary" data-testid="mops-evidence">
      {event.event_type === 'material_information' && (
        <p>主題：{details.topic_label ?? '其他／未分類'}（{details.clause ?? '條款未提供'}，規則分類）</p>
      )}
      {event.event_type === 'insider_transfer_declaration' && (
        <p className="font-semibold text-amber-500">持股轉讓事前申報，不代表實際成交或已賣出。資料日期為日報出表日。</p>
      )}
      {event.event_type === 'investor_conference' && (
        <>
          <p>舉行時間：{details.scheduled_date_display ?? event.event_date} {details.scheduled_time ?? '時間未提供'}（台北時間）；地點：{details.place || '未提供'}</p>
          <p>資料直接取自官方 MOPS，舉行時間不等於公告時間。</p>
          {safeLinks.length ? (
            <div className="flex flex-wrap gap-3">
              {safeLinks.map(link => {
                const fields = postFields(link)
                if (link.method === 'POST') return fields ? (
                  <form key={`${link.url}-${fields.fileName}`} action={link.url} method="post" target="_blank" rel="noreferrer">
                    {Object.entries(fields).map(([name, value]) => <input key={name} type="hidden" name={name} value={value} />)}
                    <button type="submit" className="inline-flex items-center gap-1 text-accent hover:underline">官方{link.label}<ExternalLink className="h-3 w-3" /></button>
                  </form>
                ) : null
                if (link.method && link.method !== 'GET') return null
                return <a key={link.url} href={link.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">官方{link.label}<ExternalLink className="h-3 w-3" /></a>
              })}
            </div>
          ) : <p>官方簡報連結尚未提供。</p>}
        </>
      )}
      <p>公告時間：{event.published_at ? `${taipeiTime(event.published_at)}（台北）` : '無法確認'}；可得時間：{event.available_at ? `${taipeiTime(event.available_at)}（台北）` : '無法確認'}</p>
      {!event.available_at && <p className="text-amber-500">公告時間證據不足（data_insufficient），不推定公開時間。</p>}
      <p>檢索時間：{taipeiTime(event.retrieved_at)}（台北）；{event.freshness === 'stale' ? '來源失敗，保留舊資料' : event.freshness === 'cached' ? '已保存的歷史觀察' : '本次來源資料'}</p>
      <p className="text-muted">事件日或之後的價格變化僅供描述，不能據此判定因果。</p>
    </div>
  )
}
