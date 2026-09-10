/**
 * 語音播報 — 用瀏覽器原生 speechSynthesis, 無需依賴/音頻文件。
 *
 * 與 notificationSound.ts 平行的獨立模塊:
 * - 引擎獨立: speechSynthesis (OS 層) vs Web Audio
 * - 配置獨立: voice_broadcast_* localStorage keys
 * - 開關獨立: voice_broadcast_enabled (默認關)
 *
 * 節流策略 (複用通知聲效"整批一聲"理念, 語音更嚴):
 * - speakAlerts() 一次接收一批告警, 合併成一句話
 * - 正在播報時直接丟棄新批次 (快行情下寧可漏念, 不疊成噪音)
 */

import type { AlertEvent } from './api'
import { strategyEventMeta, strategyName } from './strategyMonitorEvents'

const LS = {
  enabled: 'voice_broadcast_enabled',     // '1'/'0', 默認關
  voice: 'voice_broadcast_voice',         // 語音包 voiceURI (空=系統默認)
  rate: 'voice_broadcast_rate',            // 語速 0.5-2, 默認 1
} as const

// ===== 激活態: 瀏覽器自動播放策略要求用戶先交互一次 =====
let _activated = false

/**
 * 解鎖 speechSynthesis — 瀏覽器禁止頁面加載後自動發聲。
 * 由設置頁開關/試聽按鈕點擊時調用一次, 之後 SSE 推來的告警才能自動念。
 */
export function activateVoice() {
  try {
    if (!_activated && 'speechSynthesis' in window) {
      _activated = true
      // 空語句觸發激活, 不實際發聲
      const u = new SpeechSynthesisUtterance('')
      u.volume = 0
      window.speechSynthesis.speak(u)
    }
  } catch { /* ignore */ }
}

/** 是否支持語音播報 */
export function isVoiceSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

// ===== 中文語音包檢測 (供設置頁下拉) =====

/**
 * 返回系統可用的中文語音包。
 * 注意: getVoices() 首次調用可能返回空, 需監聽 voiceschanged 事件。
 */
export function listZhVoices(): SpeechSynthesisVoice[] {
  try {
    return window.speechSynthesis.getVoices().filter(v => v.lang.startsWith('zh'))
  } catch { return [] }
}

// ===== 語音包解析: 用戶手選 > zh-TW > 其他中文 > zh-CN 兜底 =====

/**
 * 解析當前應使用的語音包 (TAIWAN_LOCALIZATION_POLISH follow-up: 修正預設偏好,
 * 不再讓 zh-CN 優先於 zh-TW)。
 *
 * 優先級:
 *   1. 用戶在設置頁手選的 (voice_broadcast_voice) — 不受本次調整影響
 *   2. zh-TW 語音 (若同時存在多個 zh-TW 語音包, 優先其中的 Google 版本 — 因其
 *      音質通常接近真人; 但不要求一定要有 Google, 沒有 Google zh-TW 時用任一
 *      zh-TW 語音)
 *   3. 其他適合中文的語音 (zh-HK / 泛用 zh 等, 排除 zh-CN — zh-CN 只能是更後面
 *      的兜底, 不該在這一層出現)
 *   4. zh-CN 兜底 (沒有任何 zh-TW / 其他中文語音時, 至少用中文發音, 優於英文
 *      系統默認音)
 *   5. 都沒有: undefined (交瀏覽器系統默認, 可能不標準但不崩)
 */
function resolveVoice(): SpeechSynthesisVoice | undefined {
  try {
    const voices = window.speechSynthesis.getVoices()
    if (voices.length === 0) return undefined

    // 1. 用戶手選
    const configured = localStorage.getItem(LS.voice)
    if (configured) {
      const m = voices.find(v => v.voiceURI === configured)
      if (m) return m
    }

    // 2. zh-TW 優先 (同為 zh-TW 時, Google 版本優先, 但不要求一定要有 Google)
    const twVoices = voices.filter(v => v.lang === 'zh-TW')
    if (twVoices.length > 0) {
      return twVoices.find(v => /Google/i.test(v.name)) ?? twVoices[0]
    }

    // 3. 其他中文語音 (非 zh-CN)
    const otherZh = voices.find(v => v.lang.startsWith('zh') && v.lang !== 'zh-CN')
    if (otherZh) return otherZh

    // 4. zh-CN 兜底
    return voices.find(v => v.lang === 'zh-CN')
  } catch { return undefined }
}

/** 當前實際使用的語音 voiceURI (供設置頁下拉回顯) */
export function getCurrentVoiceURI(): string {
  return resolveVoice()?.voiceURI ?? ''
}

// ===== 文案拼接: 按 source 分類, 只念名稱不念代碼 =====

const MAX_SPEAK = 3  // 單批最多逐條念 3 只, 超出彙總成數量

/** 漲跌幅用中文習慣念: 0.052 → 漲5.2%, -0.031 → 跌3.1%。
 *  入參是小數制 (後端 change_pct, 0.0366 = 3.66%), 需 ×100 再念, 與 format.ts 的 fmtPct 一致。 */
function fmtPctText(pct: number): string {
  const p = pct * 100
  if (p >= 0) return `漲${p.toFixed(1)}%`
  return `跌${Math.abs(p).toFixed(1)}%`
}

/**
 * 單條告警 → 播報文案。
 * 設計原則: 必念個股名稱 (不念代碼), source 分類拼接:
 *   strategy(≤5只單條): "[名稱] 進入/移出 策略「策略名」 [漲跌幅]"
 *   strategy(>5只批量): 直接念 message (後端已含名稱列表)
 *   signal: "[名稱] 入場/出場信號觸發 [漲跌幅]"
 *   price/market/其他: "[名稱] [message條件摘要] [漲跌幅]"
 */
function buildSingleText(a: AlertEvent): string {
  const name = a.name || '標的'
  const pctText = a.change_pct != null ? fmtPctText(a.change_pct) : ''

  // 板塊消息已包含名稱、觸發條件和當前漲跌幅，避免重複播報。
  if (a.source === 'sector') {
    return a.message || name
  }

  // 策略類: message 存的是策略名(單條) 或完整批量描述(>5只)
  if (a.source === 'strategy') {
    // 批量事件 (symbol 為空/為 _batch): message 已含 "策略「X」進入 N 只：…" 直接念
    if (!a.symbol || a.symbol === '_batch') {
      return a.message || name
    }
    const sname = strategyName(a.message ?? '')
    const parts = [name, strategyEventMeta(a.type).action]
    const strategyText = sname ? `策略「${sname}」` : (a.message || a.rule_name || '')
    if (strategyText) parts.push(strategyText)
    if (pctText) parts.push(pctText)
    return parts.join(' ')
  }

  // 信號類: message 形如 "入場信號觸發"/"出場信號觸發"
  if (a.source === 'signal') {
    const parts = [name]
    if (a.message) parts.push(a.message)
    if (pctText) parts.push(pctText)
    return parts.join(' ')
  }

  // 價格/異動/其他: message 是條件摘要 (如 "現價 ≥ 100 · 漲幅 5%")
  const parts = [name]
  if (a.message) parts.push(a.message)
  if (pctText) parts.push(pctText)
  return parts.join(' ')
}

function buildText(alerts: AlertEvent[]): string {
  const head = alerts.slice(0, MAX_SPEAK)
  const parts = head.map(buildSingleText)
  let text = parts.join('；')
  if (alerts.length > MAX_SPEAK) {
    text += `；還有${alerts.length - MAX_SPEAK}檔`
  }
  return text
}

// ===== 節流: 正在唸時丟棄新批次, 避免快行情疊加噪音 =====
let _speaking = false

/**
 * 播報一批監控告警 (從 localStorage 讀配置)。
 * 整批合併成一句話; 正在播報時丟棄新批次 (與"整批一聲"理念一致)。
 */
export function speakAlerts(alerts: AlertEvent[]) {
  try {
    if (alerts.length === 0) return
    if (localStorage.getItem(LS.enabled) !== '1') return   // 開關關: 不播報
    if (!isVoiceSupported()) return                          // 不支持: 靜默
    if (_speaking) return                                    // 正在唸: 丟棄新批次

    const text = buildText(alerts)
    const u = new SpeechSynthesisUtterance(text)
    u.rate = parseFloat(localStorage.getItem(LS.rate) || '1')

    // lang 跟隨實際解析到的語音包 (zh-TW 優先, 見 resolveVoice); 沒有匹配到任何
    // 語音包時, 預設語言也用 zh-TW 而非 zh-CN, 與台灣產品定位一致。
    const v = resolveVoice()
    if (v) { u.voice = v; u.lang = v.lang } else { u.lang = 'zh-TW' }

    _speaking = true
    u.onend = () => { _speaking = false }
    u.onerror = () => { _speaking = false }
    window.speechSynthesis.speak(u)
  } catch {
    // 語音不可用時靜默
  }
}

/** 停止當前播報 (關閉開關/試聽前調用) */
export function stopVoice() {
  try {
    if (isVoiceSupported()) {
      window.speechSynthesis.cancel()
      _speaking = false
    }
  } catch { /* ignore */ }
}

/** 試聽 (設置頁點"試聽"用) */
export function previewVoice(text = '語音播報已開啟, 這是試聽效果') {
  try {
    if (!isVoiceSupported()) return
    activateVoice()
    const u = new SpeechSynthesisUtterance(text)
    u.rate = parseFloat(localStorage.getItem(LS.rate) || '1')
    const v = resolveVoice()
    if (v) { u.voice = v; u.lang = v.lang } else { u.lang = 'zh-TW' }
    window.speechSynthesis.cancel()   // 試聽前停掉正在唸的
    window.speechSynthesis.speak(u)
  } catch { /* ignore */ }
}
