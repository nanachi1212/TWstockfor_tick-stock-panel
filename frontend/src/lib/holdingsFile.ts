// 持股匯入的「檔案 → 文字」轉換。解析本身交給 parsePortfolioImport (portfolio.ts)，這裡只負責把各種檔案變成 CSV/TSV 文字。

const cleanCell = (value: string) => value.replace(/[\t\r\n]+/g, ' ').trim()
const toTsv = (rows: string[][]) => rows.map(row => row.map(cleanCell).join('\t')).join('\n')

/** 券商常把「.xls」存成 HTML 表格；<table> 逐列轉成 TSV (多個表格接在一起，分段標題列由解析器略過)。 */
export function htmlTablesToText(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const rows = [...doc.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('th,td')].map(cell => cell.textContent ?? ''))
  return toTsv(rows)
}

const decodeXml = (value: string) => value
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'")
  .replace(/&#(\d+);/g, (_, n: string) => String.fromCodePoint(Number(n))).replace(/&amp;/g, '&')

/** 欄位字母 (A, B, … AA) → 0 起算的欄位序號。 */
const columnIndex = (ref: string) => [...ref.replace(/\d+/g, '')].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Response(data as BodyInit).body!.pipeThrough(new DecompressionStream('deflate-raw'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

/** 讀 zip 中央目錄，取出指定檔 (只支援 xlsx 會用到的 stored / deflate)。 */
async function unzipEntries(buffer: ArrayBuffer, wanted: (name: string) => boolean): Promise<Map<string, string>> {
  const bytes = new Uint8Array(buffer)
  const view = new DataView(buffer)
  let eocd = bytes.length - 22
  while (eocd >= 0 && view.getUint32(eocd, true) !== 0x06054b50) eocd--
  if (eocd < 0) throw new Error('不是有效的 Excel (.xlsx) 檔')
  const count = view.getUint16(eocd + 10, true)
  let pos = view.getUint32(eocd + 16, true)
  const utf8 = new TextDecoder()
  const out = new Map<string, string>()
  for (let i = 0; i < count; i++) {
    const method = view.getUint16(pos + 10, true)
    const compressedSize = view.getUint32(pos + 20, true)
    const nameLength = view.getUint16(pos + 28, true)
    const extraLength = view.getUint16(pos + 30, true)
    const commentLength = view.getUint16(pos + 32, true)
    const localOffset = view.getUint32(pos + 42, true)
    const name = utf8.decode(bytes.subarray(pos + 46, pos + 46 + nameLength))
    pos += 46 + nameLength + extraLength + commentLength
    if (!wanted(name)) continue
    const dataStart = localOffset + 30 + view.getUint16(localOffset + 26, true) + view.getUint16(localOffset + 28, true)
    const raw = bytes.subarray(dataStart, dataStart + compressedSize)
    if (method !== 0 && method !== 8) throw new Error('Excel 檔使用了不支援的壓縮方式，請另存成 CSV 後再匯入')
    out.set(name, utf8.decode(method === 0 ? raw : await inflateRaw(raw)))
  }
  return out
}

// Excel 內建的日期數字格式 (numFmtId)；自訂格式另由 formatCode 判斷。
const BUILTIN_DATE_FORMATS = new Set([14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 45, 46, 47, 50, 51, 52, 53, 54, 55, 56, 57, 58])

/** styles.xml 的 cellXfs 依序對應儲存格 s="N"；回傳哪些 style 是日期格式。 */
function dateStyleIndexes(styles: string): Set<number> {
  // XML 屬性沒有固定順序：逐個 <numFmt> 元素各自取 numFmtId / formatCode。
  const attr = (element: string, name: string) => new RegExp(`\\b${name}\\s*=\\s*"([^"]*)"`).exec(element)?.[1]
  const custom = new Map([...styles.matchAll(/<numFmt\b[^>]*>/g)]
    .map(m => [Number(attr(m[0], 'numFmtId')), decodeXml(attr(m[0], 'formatCode') ?? '')] as const))
  // 去掉引號字串、[顏色/條件] 與跳脫字元後，含 d 或 y 才算日期 (純時間格式不算)。
  const isDate = (id: number) => BUILTIN_DATE_FORMATS.has(id)
    || /[dy]/i.test((custom.get(id) ?? '').replace(/"[^"]*"|\[[^\]]*\]|\\./g, ''))
  const xfs = /<cellXfs\b[^>]*>([\s\S]*?)<\/cellXfs>/.exec(styles)?.[1] ?? ''
  const out = new Set<number>()
  ;[...xfs.matchAll(/<xf\b([^>]*)/g)].forEach((m, index) => {
    if (isDate(Number(/numFmtId="(\d+)"/.exec(m[1])?.[1] ?? -1))) out.add(index)
  })
  return out
}

/** Excel 日期序號 → YYYY-MM-DD (1900 或 1904 日期系統)。 */
export function excelSerialToDate(serial: number, date1904 = false): string {
  const epoch = date1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, 30)
  return new Date(epoch + Math.floor(serial) * 86_400_000).toISOString().slice(0, 10)
}

/** 讀 .xlsx 第一個工作表，轉成 TSV 文字；日期格式的儲存格轉成 YYYY-MM-DD，不留原始序號。 */
export async function xlsxToText(buffer: ArrayBuffer): Promise<string> {
  const files = await unzipEntries(buffer, name => name === 'xl/sharedStrings.xml' || name === 'xl/styles.xml'
    || name === 'xl/workbook.xml' || /^xl\/worksheets\/sheet\d+\.xml$/.test(name))
  const dateStyles = dateStyleIndexes(files.get('xl/styles.xml') ?? '')
  const date1904 = /<workbookPr\b[^>]*date1904="(1|true)"/.test(files.get('xl/workbook.xml') ?? '')
  const sheetName = [...files.keys()].filter(name => name.startsWith('xl/worksheets/')).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))[0]
  if (!sheetName) throw new Error('Excel 檔裡找不到工作表')
  const shared = [...(files.get('xl/sharedStrings.xml') ?? '').matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/g)]
    .map(match => decodeXml([...match[1].matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map(t => t[1]).join('')))
  const rows: string[][] = []
  for (const rowMatch of (files.get(sheetName) ?? '').matchAll(/<row\b[^>]*>([\s\S]*?)<\/row>/g)) {
    const cells: string[] = []
    for (const cell of rowMatch[1].matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
      const attrs = cell[1]
      const body = cell[2] ?? ''
      const ref = /\br="([A-Z]+)\d+"/.exec(attrs)?.[1] ?? ''
      const type = /\bt="(\w+)"/.exec(attrs)?.[1]
      const value = /<v>([\s\S]*?)<\/v>/.exec(body)?.[1] ?? ''
      const style = Number(/\bs="(\d+)"/.exec(attrs)?.[1] ?? -1)
      const serial = Number(value)
      cells[ref ? columnIndex(ref) : cells.length] = type === 's' ? shared[Number(value)] ?? ''
        : type === 'inlineStr' ? decodeXml([...body.matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map(t => t[1]).join(''))
          : type === 'd' ? decodeXml(value).slice(0, 10)
            : (!type || type === 'n') && dateStyles.has(style) && value !== '' && Number.isFinite(serial)
              ? excelSerialToDate(serial, date1904)
              : decodeXml(value)
    }
    rows.push(Array.from(cells, cell => cell ?? ''))
  }
  return toTsv(rows)
}

/** 依副檔名與內容把匯入檔轉成文字。支援 CSV/TSV/TXT、Excel (.xlsx)、HTML 表格 (.xls/.html)。 */
export async function holdingsFileToText(file: File): Promise<string> {
  const ext = file.name.toLowerCase().split('.').pop() ?? ''
  if (ext === 'xlsx') return xlsxToText(await file.arrayBuffer())
  const text = await file.text()
  if ((ext === 'xls' || ext === 'html' || ext === 'htm') && /<table[\s>]/i.test(text)) return htmlTablesToText(text)
  if (ext === 'xls' && !text.includes('\t') && !text.includes(',')) throw new Error('舊版 Excel (.xls) 無法直接讀取，請另存成 .xlsx 或 CSV，或直接複製貼上')
  return text
}
