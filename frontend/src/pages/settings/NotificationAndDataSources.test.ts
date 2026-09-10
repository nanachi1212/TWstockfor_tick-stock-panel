import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (relativePath: string) => readFileSync(resolve(__dirname, relativePath), 'utf-8')

describe('notification channel settings', () => {
  const notificationFiles = [
    'Monitoring.tsx',
    '../../components/monitor/RuleEditor.tsx',
    '../../components/stock-analysis/PriceAlertDialog.tsx',
  ]

  it('shows only LINE and Telegram external channels', () => {
    const code = notificationFiles.map(read).join('\n')
    expect(code).toContain("'line'")
    expect(code).toContain("'telegram'")
    expect(code).toContain('測試通知')
    expect(code).not.toMatch(/feishu|wecom|飛書|飛書|企業微信/iu)
  })

  it('keeps masked token inputs and configured-state checks', () => {
    const code = read('Monitoring.tsx')
    expect(code).toContain('lineTokenMasked')
    expect(code).toContain('telegramTokenMasked')
    expect(code).toContain('type="password"')
    expect(code).toContain('lineConfigured')
    expect(code).toContain('telegramConfigured')
  })
})

describe('data source settings', () => {
  it('removes TickFlow registration and tier upsell UI', () => {
    const code = read('DataSources.tsx') + read('Keys.tsx')
    expect(code).not.toContain('tickflow.org/auth/register')
    expect(code).not.toMatch(/Starter\+|Pro\+|Expert\+|訂閱檔位|已適配全檔位/u)
  })

  it('keeps operational Taiwan official data source and generic custom-provider entry points without TickFlow key UI', () => {
    const dataSources = read('DataSources.tsx')
    expect(dataSources).not.toContain('<TickFlowKeyConfig />')
    expect(dataSources).toContain('台灣官方資料源')
    expect(dataSources).toContain('<DataSourceEditor')
    expect(dataSources).toContain("selected === '__new__'")
  })

  it('includes collapsible setup guides for LINE and Telegram with official links', () => {
    const code = read('Monitoring.tsx')
    // LINE guide
    expect(code).toContain('LINE Messaging API 串接教學')
    expect(code).not.toContain('LINE Notify')
    expect(code).toContain('https://developers.line.biz/en/docs/messaging-api/getting-started/')
    expect(code).toContain('https://developers.line.biz/en/docs/basics/channel-access-token/')
    expect(code).toContain('Target ID')
    expect(code).toContain('Basic settings')
    expect(code).toContain('webhook event')
    expect(code).toContain('不等於一般 LINE ID')
    expect(code).toContain('Channel Access Token 視同密碼')

    // Telegram guide
    expect(code).toContain('Telegram Bot 串接教學')
    expect(code).toContain('@BotFather')
    expect(code).toContain('/newbot')
    expect(code).toContain('https://core.telegram.org/bots/tutorial')
    expect(code).toContain('https://core.telegram.org/bots/api')
    expect(code).toContain('getUpdates')
    expect(code).toContain('message.chat.id')
    expect(code).not.toContain('userinfobot')
    expect(code).toContain('Chat ID')
    expect(code).toContain('Bot Token 視同密碼')

    // Collapsible accordion
    expect(code).toContain('<details')
  })

  it('does not display TickFlow brand or API key in user data sources UI', () => {
    const dataSources = read('DataSources.tsx')
    expect(dataSources).not.toContain("? 'TickFlow'")
    expect(dataSources).not.toContain('TickFlowKeyConfig')
    expect(dataSources).toContain('台灣官方資料源 (TWSE/TPEx)')
  })
})
