import { CopyButton } from '@/components/CopyButton'

interface FieldChange { path: string; before: unknown; after: unknown }
interface SideMeta { id: string; date: string | null }

const changesOf = (section: unknown): FieldChange[] => {
  const list = (section as { field_changes?: unknown } | null)?.field_changes
  return Array.isArray(list) ? list.filter((c): c is FieldChange => typeof (c as FieldChange)?.path === 'string') : []
}

const ENUM_LABEL: Record<string, string> = {
  watch: '可列入觀察', wait_pullback: '等拉回', wait_breakout: '等突破', no_chase: '不宜追價', skip: '暫時略過',
  strong: '偏強', neutral: '中性', weak: '偏弱', buy: '偏買', sell: '偏賣',
  increase_fast: '快速增加', decrease: '減少', stable: '變化不大',
  stronger: '強於大盤', similar: '接近大盤', weaker: '弱於大盤',
  price_up_volume_up: '價漲量增', price_up_volume_down: '價漲量縮', price_down_volume_up: '價跌量增', price_down_volume_down: '價跌量縮',
  low: '低', normal: '一般', high: '偏大',
  complete: '完整', partial: '部分', insufficient: '不足', unavailable: '資料不足', data_insufficient: '資料不足',
}

/** 人看得懂的重點欄位；其餘差異收在「完整欄位差異」。 */
const KEY_FIELDS: [string, string][] = [
  ['price_context.trade_date', '行情資料日'],
  ['price_context.close', '收盤價'],
  ['beginner_selection.selection_state', '選股結論'],
  ['beginner_selection.action_summary', '下一步'],
  ['beginner_selection.evidence_status', '證據完整度'],
  ['beginner_selection.technical_panel.moving_averages.state', '均線趨勢'],
  ['beginner_selection.technical_panel.volume.pattern', '量價'],
  ['beginner_selection.technical_panel.institutional.state', '法人 5 日'],
  ['beginner_selection.technical_panel.margin.margin_state', '融資'],
  ['beginner_selection.technical_panel.relative_strength.state', '相對大盤'],
  ['beginner_selection.technical_panel.volatility.level', '波動'],
  ['beginner_selection.technical_panel.fundamentals.explanation', '基本面'],
]

const TEXT_FIELDS: [string, string][] = [
  ['overview', '總覽'],
  ['price_technical_interpretation', '價格與技術面'],
  ['institutional_interpretation', '法人'],
  ['margin_interpretation', '融資融券'],
  ['fundamentals_interpretation', '基本面'],
  ['market_interpretation', '大盤與類股'],
  ['events_news_interpretation', '事件與新聞'],
]

const SECTION_LABEL: Record<string, string> = {
  price_context: '價格', technical_context: '技術指標', institutional_context: '法人', margin_context: '融資融券',
  fundamentals_context: '基本面', ownership_context: '外資持股與借券', market_context: '大盤', industry_context: '類股',
  personal_context: '個人情境（持股/報價/社群）', recent_events: '官方事件', recent_news: '新聞', news_status: '新聞狀態',
  'beginner_selection.technical_panel': '初學者面板', beginner_selection: '初學者結論',
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '（無）'
  if (typeof value === 'string') return ENUM_LABEL[value] ?? value
  if (typeof value === 'number') return value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })
  if (typeof value === 'boolean') return value ? '是' : '否'
  return '（多筆資料）'
}

const empty = (value: unknown) => value === null || value === undefined || value === ''

function sectionOf(path: string): string {
  const parts = path.split(/[.[]/)
  const two = parts.slice(0, 2).join('.')
  return SECTION_LABEL[two] ?? SECTION_LABEL[parts[0]] ?? parts[0]
}

/** 兩份紀錄：older = 舊、newer = 新；API 呼叫需讓 before = 舊、after = 新。 */
export function ResearchCompareView({ data, older, newer }: { data: Record<string, unknown>; older: SideMeta; newer: SideMeta }) {
  const evidence = changesOf(data.evidence_data_changes)
  const report = changesOf(data.interpretation_report_changes)
  const config = changesOf(data.model_prompt_config_changes)
  const byPath = new Map(evidence.map(c => [c.path, c]))
  const keyRows = KEY_FIELDS.flatMap(([path, label]) => {
    const c = byPath.get(path)
    return c ? [{ label, before: show(c.before), after: show(c.after) }] : []
  })
  const lost = new Set<string>()
  const gained = new Set<string>()
  for (const c of evidence) {
    if (!empty(c.before) && empty(c.after)) lost.add(sectionOf(c.path))
    if (empty(c.before) && !empty(c.after)) gained.add(sectionOf(c.path))
  }
  const lostCount = evidence.filter(c => !empty(c.before) && empty(c.after)).length
  const gainedCount = evidence.filter(c => empty(c.before) && !empty(c.after)).length
  const reportByPath = new Map(report.map(c => [c.path, c]))
  const textRows = TEXT_FIELDS.flatMap(([path, label]) => {
    const c = reportByPath.get(path)
    return c ? [{ label, before: show(c.before), after: show(c.after) }] : []
  })
  const degraded = lostCount >= 10 ? '新紀錄' : gainedCount >= 10 ? '舊紀錄' : null
  const oldLabel = `舊紀錄（${older.date ?? '日期不明'}）`
  const newLabel = `新紀錄（${newer.date ?? '日期不明'}）`

  const aiText = [
    '# 兩份台股 AI 研究紀錄比較',
    `- 舊紀錄：${older.id}（資料日 ${older.date ?? '不明'}）`,
    `- 新紀錄：${newer.id}（資料日 ${newer.date ?? '不明'}）`,
    `- 模型／提示詞：${config.length ? '有變更' : '相同'}`,
    '',
    '## 重點欄位（舊 → 新）',
    '| 項目 | 舊 | 新 |', '|---|---|---|',
    ...keyRows.map(r => `| ${r.label} | ${r.before} | ${r.after} |`),
    '',
    '## 資料完整度',
    `- 新紀錄比舊紀錄少了 ${lostCount} 個欄位${lost.size ? `（${[...lost].join('、')}）` : ''}`,
    `- 新紀錄比舊紀錄多了 ${gainedCount} 個欄位${gained.size ? `（${[...gained].join('、')}）` : ''}`,
    '',
    '## AI 解讀前後',
    ...textRows.flatMap(r => [`### ${r.label}`, `- 舊：${r.before}`, `- 新：${r.after}`]),
    '',
    '## 請回答',
    '1. 哪些差異是行情、籌碼或基本面真的改變？哪些只是某一份紀錄資料缺漏造成？',
    '2. 依真實的改變，原本的結論需要調整嗎？為什麼？',
    '3. 還有哪些資料需要再確認（資料日、缺漏欄位）？不得補值或推測缺漏資料。',
  ].join('\n')

  return (
    <div className="mt-2 space-y-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted">{oldLabel} → {newLabel}{config.length ? '・模型或提示詞有變更' : '・模型與提示詞相同'}</p>
        <CopyButton size="xs" label="複製給 AI 比較" successLabel="已複製比較" getText={() => aiText} />
      </div>
      {degraded && (
        <p role="note" className="rounded-md bg-warning/10 p-2 text-warning">
          {degraded}有大量資料缺失（{degraded === '新紀錄' ? lostCount : gainedCount} 個欄位），下方差異多半來自資料不完整，不代表股票本身改變。
        </p>
      )}
      <section aria-label="重點欄位變化">
        <h5 className="font-semibold text-foreground">重點欄位變化</h5>
        {keyRows.length === 0 ? <p className="text-muted">重點欄位沒有變化。</p> : (
          <table className="mt-1 w-full table-fixed border-collapse">
            <thead className="text-muted"><tr><th className="w-24 py-1 text-left font-normal">項目</th><th className="py-1 text-left font-normal">舊</th><th className="py-1 text-left font-normal">新</th></tr></thead>
            <tbody>{keyRows.map(r => (
              <tr key={r.label} className="border-t border-border/50 align-top">
                <td className="py-1 text-muted">{r.label}</td><td className="break-words py-1 pr-2">{r.before}</td><td className="break-words py-1 font-medium text-foreground">{r.after}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
      <section aria-label="資料完整度">
        <h5 className="font-semibold text-foreground">資料完整度</h5>
        <p className="text-muted">新紀錄少了 {lostCount} 個欄位{lost.size ? `：${[...lost].join('、')}` : ''}；多了 {gainedCount} 個欄位{gained.size ? `：${[...gained].join('、')}` : ''}。</p>
      </section>
      {textRows.length > 0 && (
        <section aria-label="AI 解讀前後">
          <h5 className="font-semibold text-foreground">AI 解讀前後</h5>
          <div className="mt-1 space-y-2">{textRows.map(r => (
            <div key={r.label} className="grid gap-2 md:grid-cols-[6rem_1fr_1fr]">
              <span className="text-muted">{r.label}</span>
              <p className="break-words text-muted">舊：{r.before}</p>
              <p className="break-words text-foreground">新：{r.after}</p>
            </div>
          ))}</div>
        </section>
      )}
      <details>
        <summary className="cursor-pointer text-muted">完整欄位差異（除錯用，{evidence.length + report.length + config.length} 項）</summary>
        <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap text-[10px] text-muted">{JSON.stringify({ evidence, report, config }, null, 2)}</pre>
      </details>
    </div>
  )
}
