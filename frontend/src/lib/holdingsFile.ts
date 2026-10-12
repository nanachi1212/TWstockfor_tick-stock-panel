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

/** 讀 .xlsx 第一個工作表，轉成 TSV 文字。 */
export async function xlsxToText(buffer: ArrayBuffer): Promise<string> {
  const files = await unzipEntries(buffer, name => name === 'xl/sharedStrings.xml' || /^xl\/worksheets\/sheet\d+\.xml$/.test(name))
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
      cells[ref ? columnIndex(ref) : cells.length] = type === 's' ? shared[Number(value)] ?? ''
        : type === 'inlineStr' ? decodeXml([...body.matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map(t => t[1]).join(''))
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
