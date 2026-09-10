/**
 * 通知聲效 — 用 Web Audio API 合成, 無需音頻文件。
 *
 * 聲效列表 (純代碼合成, 不同頻率/波形/節奏):
 * - ding:   清脆"叮" (默認, 適合一般提醒)
 * - chime:  兩音階風鈴 (適合策略信號)
 * - alert:  急促警報 (適合重要/異動)
 * - soft:   柔和低音 (適合價格提醒)
 * - none:   無聲
 */

let _audioCtx: AudioContext | null = null

function getCtx(): AudioContext | null {
  try {
    if (!_audioCtx) {
      _audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)()
    }
    if (_audioCtx.state === 'suspended') _audioCtx.resume()
    return _audioCtx
  } catch {
    return null
  }
}

/** 播放單個音符 */
function playTone(ctx: AudioContext, freq: number, start: number, duration: number, type: OscillatorType = 'sine', gain: number = 0.15) {
  const osc = ctx.createOscillator()
  const g = ctx.createGain()
  osc.type = type
  osc.frequency.value = freq
  osc.connect(g)
  g.connect(ctx.destination)
  const t = ctx.currentTime + start
  g.gain.setValueAtTime(0, t)
  g.gain.linearRampToValueAtTime(gain, t + 0.01)
  g.gain.exponentialRampToValueAtTime(0.001, t + duration)
  osc.start(t)
  osc.stop(t + duration + 0.05)
}

const SOUND_PRESETS: Record<string, (ctx: AudioContext) => void> = {
  // 清脆"叮" — 一個正弦波短音
  ding: (ctx) => {
    playTone(ctx, 880, 0, 0.3, 'sine', 0.2)
  },
  // 兩音階風鈴 — 高低兩個音
  chime: (ctx) => {
    playTone(ctx, 660, 0, 0.25, 'sine', 0.15)
    playTone(ctx, 990, 0.12, 0.35, 'sine', 0.15)
  },
  // 急促警報 — 三個快速方波
  alert: (ctx) => {
    playTone(ctx, 800, 0, 0.1, 'square', 0.12)
    playTone(ctx, 800, 0.15, 0.1, 'square', 0.12)
    playTone(ctx, 1000, 0.3, 0.15, 'square', 0.12)
  },
  // 柔和低音 — 低頻三角波
  soft: (ctx) => {
    playTone(ctx, 440, 0, 0.5, 'triangle', 0.15)
    playTone(ctx, 330, 0.2, 0.5, 'triangle', 0.12)
  },
  // 上升音階 — C-E-G-C 遞進 (積極感)
  rise: (ctx) => {
    playTone(ctx, 523, 0, 0.12, 'sine', 0.18)      // C5
    playTone(ctx, 659, 0.1, 0.12, 'sine', 0.18)     // E5
    playTone(ctx, 784, 0.2, 0.12, 'sine', 0.18)     // G5
    playTone(ctx, 1047, 0.3, 0.3, 'sine', 0.2)      // C6
  },
  // 下降音階 — C-A-F-D (消極/警示感)
  fall: (ctx) => {
    playTone(ctx, 523, 0, 0.15, 'sine', 0.18)       // C5
    playTone(ctx, 440, 0.12, 0.15, 'sine', 0.18)    // A4
    playTone(ctx, 349, 0.24, 0.15, 'sine', 0.18)    // F4
    playTone(ctx, 294, 0.36, 0.3, 'sine', 0.18)     // D4
  },
  // 電子提示音 — 鋸齒波短促
  electronic: (ctx) => {
    playTone(ctx, 1200, 0, 0.08, 'sawtooth', 0.1)
    playTone(ctx, 1600, 0.06, 0.08, 'sawtooth', 0.1)
    playTone(ctx, 1200, 0.12, 0.15, 'sawtooth', 0.1)
  },
  // 水滴 — 極高頻短音, 清脆
  drop: (ctx) => {
    playTone(ctx, 1800, 0, 0.06, 'sine', 0.15)
    playTone(ctx, 2400, 0.04, 0.1, 'sine', 0.12)
  },
  // 鐘聲 — 低頻持續共鳴
  bell: (ctx) => {
    playTone(ctx, 523, 0, 0.8, 'sine', 0.15)
    playTone(ctx, 784, 0.02, 0.8, 'sine', 0.1)      // 泛音
    playTone(ctx, 1047, 0.04, 0.6, 'sine', 0.06)    // 高泛音
  },
  // 乒乓 — 兩個交替音
  pingpong: (ctx) => {
    playTone(ctx, 1000, 0, 0.08, 'sine', 0.15)
    playTone(ctx, 700, 0.1, 0.08, 'sine', 0.15)
    playTone(ctx, 1000, 0.2, 0.08, 'sine', 0.15)
    playTone(ctx, 700, 0.3, 0.15, 'sine', 0.15)
  },
  // 魔法 — 快速上升掃頻感
  magic: (ctx) => {
    playTone(ctx, 400, 0, 0.05, 'sine', 0.12)
    playTone(ctx, 600, 0.04, 0.05, 'sine', 0.12)
    playTone(ctx, 800, 0.08, 0.05, 'sine', 0.12)
    playTone(ctx, 1100, 0.12, 0.05, 'sine', 0.12)
    playTone(ctx, 1400, 0.16, 0.2, 'sine', 0.15)
  },
}

/** 播放通知聲效 (從 localStorage 讀配置) */
export function playNotificationSound() {
  try {
    const enabled = localStorage.getItem('alert_sound_enabled')
    if (enabled === '0') return  // 關閉聲效

    const sound = localStorage.getItem('alert_sound') || 'ding'
    if (sound === 'none') return

    const ctx = getCtx()
    if (!ctx) return

    const preset = SOUND_PRESETS[sound]
    if (preset) preset(ctx)
  } catch {
    // 音頻不可用時靜默
  }
}

/** 聲效選項 (供設置頁下拉) */
export const SOUND_OPTIONS = [
  { key: 'ding', label: '清脆叮' },
  { key: 'chime', label: '風鈴' },
  { key: 'rise', label: '上升音階' },
  { key: 'fall', label: '下降音階' },
  { key: 'alert', label: '急促警報' },
  { key: 'electronic', label: '電子音' },
  { key: 'drop', label: '水滴' },
  { key: 'bell', label: '鐘聲' },
  { key: 'pingpong', label: '乒乓' },
  { key: 'magic', label: '魔法' },
  { key: 'soft', label: '柔和' },
  { key: 'none', label: '無聲' },
]

/** 預覽聲效 (設置頁點"試聽"用) */
export function previewSound(sound: string) {
  try {
    const ctx = getCtx()
    if (!ctx) return
    const preset = SOUND_PRESETS[sound]
    if (preset) preset(ctx)
  } catch { /* ignore */ }
}
