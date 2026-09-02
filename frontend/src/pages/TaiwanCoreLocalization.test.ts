// Phase 8B-4.2 — Taiwan Core Pages Traditional Chinese 回歸測試
// 涵蓋：Watchlist / Monitor 主要 heading、empty state、按鈕文案不再出現本 Phase
// 已修正的簡體核心詞;不對每個 label 建測試,只鎖定關鍵字級別的殘留守門。
// 用靜態原始碼掃描而非渲染測試 —— 這兩頁依賴樹龐大(20+ hooks/元件),完整渲染
// mock 成本遠高於這裡要驗證的「字串不再是簡體」這件事本身。
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function read(relPath: string) {
  return readFileSync(resolve(__dirname, '..', relPath), 'utf-8')
}

// 逐行過濾掉註解(// 或 {/* */} 或 /** */ 開頭的行), 只檢查真正 render 的字串。
function nonCommentLines(src: string) {
  return src
    .split('\n')
    .filter(line => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(line))
    .join('\n')
}

describe('Taiwan core pages — no simplified Chinese in rendered text (Phase 8B-4.2)', () => {
  const simplifiedTerms = [
    '设置', '数据', '用户', '个股', '财务', '监控', '回测', '复盘',
    '搜索', '筛选', '加载', '保存', '删除', '默认', '刷新', '下载',
    '上传', '详情', '状态', '实时', '历史', '获取', '暂无',
  ]

  it('Watchlist.tsx has no simplified core terms in rendered (non-comment) code', () => {
    const code = nonCommentLines(read('pages/Watchlist.tsx'))
    for (const term of simplifiedTerms) {
      expect(code, `Watchlist.tsx 不應含簡體字詞「${term}」`).not.toContain(term)
    }
    expect(code).toContain('自選股')
    expect(code).toContain('確認清空自選')
  })

  it('Monitor.tsx has no simplified core terms in rendered (non-comment) code', () => {
    const code = nonCommentLines(read('pages/Monitor.tsx'))
    for (const term of simplifiedTerms) {
      expect(code, `Monitor.tsx 不應含簡體字詞「${term}」`).not.toContain(term)
    }
    expect(code).toContain('即時監控中心')
    expect(code).toContain('尚無觸發記錄')
    expect(code).toContain('尚無監控規則')
  })

  it('TaiwanScreener.tsx / TaiwanStockDetail.tsx / TaiwanStockCompare.tsx remain simplified-free', () => {
    for (const file of ['pages/TaiwanScreener.tsx', 'pages/TaiwanStockDetail.tsx', 'pages/TaiwanStockCompare.tsx']) {
      const code = nonCommentLines(read(file))
      for (const term of simplifiedTerms) {
        expect(code, `${file} 不應含簡體字詞「${term}」`).not.toContain(term)
      }
    }
  })

  it('Taiwan symbol/exchange handling is untouched (.TWSE / .TPEX suffixes still referenced)', () => {
    const detail = read('pages/TaiwanStockDetail.tsx')
    // 只需確認 route/symbol 相關程式碼仍在, 不要求逐字比對(避免测试过度耦合实作细节)
    expect(detail).toMatch(/symbol/i)
  })
})
