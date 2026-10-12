import { describe, expect, it } from 'vitest'
import { DecompressionStream as NodeDecompressionStream } from 'node:stream/web'
import { excelSerialToDate, holdingsFileToText, htmlTablesToText, xlsxToText } from './holdingsFile'
import { parsePortfolioImport } from './portfolio'

// jsdom 沒有 DecompressionStream；Node 有，測試時補上。
if (typeof globalThis.DecompressionStream === 'undefined') {
  Object.assign(globalThis, { DecompressionStream: NodeDecompressionStream })
}

// 內容：A欄=商品名稱(含代號)、C欄=股數、D欄=成交價、E欄=投資成本(負數)，欄位 B 留空以驗證欄位對位。
const XLSX_B64 = 'UEsDBBQAAAAIAORdTF3HHBc8CgAAAAgAAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbLMJqSxILda3AwBQSwMEFAAAAAgA5F1MXZCzz23sAAAAMQEAABQAAAB4bC9zaGFyZWRTdHJpbmdzLnhtbGWLzUrDMBzAXyXkpAebTJhMSTO8+AT6AGWLa6FJSv9BenQb6lBkBelNhOnAHTxsiNjN12na+hYyvEi9/j5YN5EhuhAxBFq5uOVQjITq6X6gBi4+Oz3Z6+AuZwAGJTJU4GLfmOiIEOj5Qnrg6EioRIbnOpaeAUfHAwJRLLw++EIYGZJ9Sg+I9AKFOYOAM8Ntdm0fhja9rxYrRgxnZMt/XT2alVnepOUkLTZzO17/E7dZ/X5TTtLy8a3p7NXYzl/tdFkNX1Cboh1K23S3WRX5l73Lqo9VkV/a6bIezezz03f2uc0PO63jPwMBMPwHUEsDBBQAAAAIAORdTF1r3bVy8QAAABICAAAYAAAAeGwvd29ya3NoZWV0cy9zaGVldDEueG1sXZHtboMgFIZvxZz/Cmj96II0y+yuYLsAYmk1EzBA1MtfrJtF/hGe981zcg69LHKIJmFsr1UNJMEQCdXqW68eNXx/fcYVXBidtfmxnRAuWuSgbA2dc+MbQrbthOQ20aNQixzu2kjubKLNA9nRCH57luSAUowLJHmvgNHnX8MdZ9ToOTI1EGC0XR/vBCJXgwVGJ4YpmhhF7R/78Bk5ssZn6ZFdfZbtDBk97wOk+wCpFz4FA6ypiRFcBaD5B2VSvgSbfEMxIcU5e9UO8myXZ548D+RramI5xjjYS7ORtErKoHPdSExO2TnPi8COvDug/cDsF1BLAQIUABQAAAAIAORdTF3HHBc8CgAAAAgAAAATAAAAAAAAAAAAAACAAQAAAABbQ29udGVudF9UeXBlc10ueG1sUEsBAhQAFAAAAAgA5F1MXZCzz23sAAAAMQEAABQAAAAAAAAAAAAAAIABOwAAAHhsL3NoYXJlZFN0cmluZ3MueG1sUEsBAhQAFAAAAAgA5F1MXWvdtXLxAAAAEgIAABgAAAAAAAAAAAAAAIABWQEAAHhsL3dvcmtzaGVldHMvc2hlZXQxLnhtbFBLBQYAAAAAAwADAMkAAACAAgAAAAA='
const xlsxBuffer = () => Uint8Array.from(atob(XLSX_B64), ch => ch.charCodeAt(0)).buffer

const EXPECTED = [
  { code: '0050', name: '元大台灣 50', shares: 1084, price: 107.73, fee: 154.68, date: '2026-10-09' },
  { code: '00981A', name: '主動統一台股增長', shares: 50000, price: 28.75, fee: 2056, date: '2026-10-09' },
]

describe('holdings file import', () => {
  it('reads an .xlsx sheet (shared strings, empty column, negative cost) into importable rows', async () => {
    const text = await xlsxToText(xlsxBuffer())
    expect(parsePortfolioImport(text, '2026-10-09')).toEqual({ rows: EXPECTED, errors: [] })
  })

  it('keeps Excel date cells (built-in, custom format, 1904 system) instead of the import day', async () => {
    const toBuffer = (b64: string) => Uint8Array.from(atob(b64), ch => ch.charCodeAt(0)).buffer
    const expected = [
      { code: '2330', name: '', shares: 10, price: 900, fee: 0, date: '2026-09-30' },
      { code: '2308', name: '', shares: 200, price: 2010, fee: 0, date: '2026-10-01' },
    ]
    for (const b64 of ['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']) {
      expect(parsePortfolioImport(await xlsxToText(toBuffer(b64)), '2026-10-12').rows).toEqual(expected)
    }
    expect(excelSerialToDate(46295)).toBe('2026-09-30')
    expect(excelSerialToDate(44833, true)).toBe('2026-09-30')
  })

  it('converts an HTML-table ".xls" export and routes by extension', async () => {
    const html = '<html><body><table><tr><td>現股損益</td></tr><tr><th>商品名稱</th><th>股數</th><th>成交價</th><th>投資成本</th></tr>'
      + '<tr><td>元大台灣 50 (0050)</td><td>1,084</td><td>107.73</td><td>-116,934</td></tr>'
      + '<tr><td>主動統一台股增長 (00981A)</td><td>50,000</td><td>28.75</td><td>-1,439,556</td></tr></table></body></html>'
    expect(parsePortfolioImport(htmlTablesToText(html), '2026-10-09').rows).toEqual(EXPECTED)
    const file = new File([html], '庫存.xls')
    expect(parsePortfolioImport(await holdingsFileToText(file), '2026-10-09').rows).toEqual(EXPECTED)
  })

  it('passes CSV through and rejects a non-xlsx file named .xlsx', async () => {
    const csv = '代號,名稱,股數,均價\n2330,台積電,10,900'
    expect(await holdingsFileToText(new File([csv], 'a.csv'))).toBe(csv)
    await expect(xlsxToText(new TextEncoder().encode('not a zip').buffer)).rejects.toThrow('Excel')
  })
})
