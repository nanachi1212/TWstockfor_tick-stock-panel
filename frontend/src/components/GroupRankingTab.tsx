import { Link } from 'react-router-dom'
import { toneClass } from '@/components/ChipRankingTab'
import { CoverageNotice, pct, previousIncomplete, yi } from '@/components/MarketHeatmap'
import type { GroupRankingRow, GroupRankingSnapshot } from '@/lib/marketResearch'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'

const MOBILE_HIDDEN = 'hidden md:table-cell'
const HEADERS = [['名次', ''], ['變動', ''], ['族群', ''], ['量能加權漲跌', ''], ['金額加權', MOBILE_HIDDEN], ['簡單平均', MOBILE_HIDDEN], ['中位數', MOBILE_HIDDEN],
  ['上漲／下跌／平盤', MOBILE_HIDDEN], ['上漲比例', ''], ['成交金額（億）', ''], ['成分', MOBILE_HIDDEN]]
const share = (v: number) => `${(v * 100).toFixed(1)}%`

function RankChange({ change }: { change: number | null }) {
  if (change == null) return null
  if (change === 0) return <>—</>
  return <span className={change > 0 ? 'text-bull' : 'text-bear'}>{change > 0 ? '▲' : '▼'}{Math.abs(change)}</span>
}

function Change({ value, className = '' }: { value: number | null; className?: string }) {
  return <td className={`p-3 text-right tabular-nums ${toneClass(value)} ${className}`}>{pct(value, 2)}</td>
}

function GroupRows({ group }: { group: GroupRankingRow }) {
  return <tbody className="border-t border-border">
    <tr>
      <td className="p-3 tabular-nums">{group.rank ?? '—'}</td>
      <td className="p-3 tabular-nums"><RankChange change={group.rank_change} /></td>
      <td className="p-3"><span className="inline-flex items-center gap-2"><span className={`h-2 w-2 shrink-0 rounded-full ${resolveWatchlistGroupColor(group.color).dot}`} />{group.name}
        {!group.complete && <span title={group.missing_symbols.join('、')} className="rounded bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">缺 {group.missing_symbols.length} 檔</span>}</span></td>
      <Change value={group.score} />
      <Change value={group.weighted_avg_change} className={MOBILE_HIDDEN} />
      <Change value={group.average_change} className={MOBILE_HIDDEN} />
      <Change value={group.median_change} className={MOBILE_HIDDEN} />
      <td className={`p-3 text-right tabular-nums ${MOBILE_HIDDEN}`}><span className="text-bull">{group.advance}</span>／<span className="text-bear">{group.decline}</span>／{group.flat}</td>
      <td className="p-3 text-right tabular-nums">{group.breadth == null ? '—' : share(group.breadth)}</td>
      <td className="p-3 text-right tabular-nums">{yi(group.amount)}</td>
      <td className={`p-3 text-right tabular-nums ${MOBILE_HIDDEN}`}>{group.quoted}/{group.members}{group.excluded > 0 && <span className="ml-1 text-xs text-muted">排除 {group.excluded}</span>}</td>
    </tr>
    {group.items.length > 0 && <tr><td colSpan={HEADERS.length} className="px-3 pb-3">
      <details>
        <summary className="cursor-pointer text-xs text-muted">成分股明細（{group.items.length} 檔）</summary>
        <table className="mt-2 w-full text-xs">
          <thead className="text-muted"><tr>{['股票', '收盤', '漲跌', '成交金額（億）', '占族群', '權重'].map(t => <th key={t} className="px-2 py-1 text-right font-normal first:text-left">{t}</th>)}</tr></thead>
          <tbody>{group.items.map(item => <tr key={item.symbol} className="border-t border-border/60">
            <td className="px-2 py-1"><Link className="text-accent hover:underline" to={`/stocks/${encodeURIComponent(item.symbol)}`}>{item.symbol.split('.')[0]} {item.name}</Link>
              {item.excluded && <span className="ml-2 rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">低量排除</span>}</td>
            <td className="px-2 py-1 text-right tabular-nums">{item.close}</td>
            <td className={`px-2 py-1 text-right tabular-nums ${toneClass(item.change_pct)}`}>{pct(item.change_pct, 2)}</td>
            <td className="px-2 py-1 text-right tabular-nums">{yi(item.amount)}</td>
            <td className="px-2 py-1 text-right tabular-nums">{share(item.share)}</td>
            <td className="px-2 py-1 text-right tabular-nums">{share(item.weight)}</td>
          </tr>)}</tbody>
        </table>
      </details>
    </td></tr>}
  </tbody>
}

export function GroupRankingTab({ data }: { data: GroupRankingSnapshot }) {
  if (data.status === 'unavailable') return <p role="status" className="rounded-lg border border-border bg-surface p-6 text-muted">{data.date} 尚無日資料</p>
  if (data.groups.length === 0) return <p className="rounded-lg border border-border bg-surface p-6 text-muted">
    尚未建立自選分組。到自選頁建立分組後，這裡會依族群排名。<Link className="ml-2 text-accent hover:underline" to="/watchlist">前往自選</Link>
  </p>
  return <div className="space-y-4">
    {data.status === 'partial' && <CoverageNotice date={data.date} reason={data.reason} coverage={data.coverage} impact="名次可能因缺資料而失真" />}
    <p className="text-xs text-muted leading-relaxed">量能加權漲跌＝成分股原始收盤漲跌幅以 √成交金額加權；成交金額占族群不到 0.5% 或不到 1,000 萬元的成分股不計入分數（仍列出並標示）。名次變動相對前一交易日{data.previous_date && `（${data.previous_date}）`}{previousIncomplete(data.coverage) && '，但前一交易日資料不完整，不計算名次變動'}，成員關係以目前的分組為準。標「缺 n 檔」的族群只用有資料的成分股計算。不是買賣訊號。</p>
    <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-sm md:min-w-[960px] max-md:[&_th]:px-2 max-md:[&_td]:px-2">
      <thead className="bg-elevated text-xs text-secondary"><tr>{HEADERS.map(([t, hide], i) => <th key={t} className={`p-3 ${i < 3 ? 'text-left' : 'text-right'} ${hide}`}>{t}</th>)}</tr></thead>
      {data.groups.map(group => <GroupRows key={group.id} group={group} />)}
    </table></div>
  </div>
}
