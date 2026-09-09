// TAIWAN_LOCALIZATION_POLISH follow-up — Voice default logic 回歸測試。
//
// resolveVoice() 本身沒有 export，透過已 export 的 getCurrentVoiceURI()
// (內部直接呼叫 resolveVoice() 並回傳其 voiceURI) 間接驗證優先序，不需要
// 為了測試改動模組的公開 API 形狀。
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { getCurrentVoiceURI } from './voiceBroadcast'

function fakeVoice(overrides: Partial<SpeechSynthesisVoice>): SpeechSynthesisVoice {
  return {
    voiceURI: overrides.name ?? 'voice',
    name: overrides.name ?? 'voice',
    lang: 'zh-TW',
    localService: true,
    default: false,
    ...overrides,
  } as SpeechSynthesisVoice
}

function mockVoices(voices: SpeechSynthesisVoice[]) {
  ;(window as any).speechSynthesis = {
    getVoices: () => voices,
    speak: () => {},
    cancel: () => {},
  }
}

describe('voice resolution priority (resolveVoice via getCurrentVoiceURI)', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    localStorage.clear()
  })

  it('prefers the user-saved voice over everything else, including a zh-TW voice', () => {
    const saved = fakeVoice({ voiceURI: 'saved-1', name: 'Saved Voice', lang: 'zh-CN' })
    const tw = fakeVoice({ voiceURI: 'tw-1', name: 'Some TW Voice', lang: 'zh-TW' })
    mockVoices([tw, saved])
    localStorage.setItem('voice_broadcast_voice', 'saved-1')

    expect(getCurrentVoiceURI()).toBe('saved-1')
  })

  it('prefers a zh-TW voice over a zh-CN voice when nothing is saved', () => {
    const cn = fakeVoice({ voiceURI: 'cn-1', name: 'Some CN Voice', lang: 'zh-CN' })
    const tw = fakeVoice({ voiceURI: 'tw-1', name: 'Some TW Voice', lang: 'zh-TW' })
    mockVoices([cn, tw])

    expect(getCurrentVoiceURI()).toBe('tw-1')
  })

  it('prefers the Google zh-TW voice over a non-Google zh-TW voice, when both exist', () => {
    const twPlain = fakeVoice({ voiceURI: 'tw-plain', name: 'Microsoft Yating', lang: 'zh-TW' })
    const twGoogle = fakeVoice({ voiceURI: 'tw-google', name: 'Google 國語（臺灣）', lang: 'zh-TW' })
    mockVoices([twPlain, twGoogle])

    expect(getCurrentVoiceURI()).toBe('tw-google')
  })

  it('does not require a Google voice — any zh-TW voice is selected when no Google zh-TW exists', () => {
    const twPlain = fakeVoice({ voiceURI: 'tw-plain', name: 'Microsoft Yating', lang: 'zh-TW' })
    mockVoices([twPlain])

    expect(getCurrentVoiceURI()).toBe('tw-plain')
  })

  it('prefers another Chinese voice (e.g. zh-HK) over zh-CN when no zh-TW voice exists', () => {
    const cn = fakeVoice({ voiceURI: 'cn-1', name: 'Some CN Voice', lang: 'zh-CN' })
    const hk = fakeVoice({ voiceURI: 'hk-1', name: 'Some HK Voice', lang: 'zh-HK' })
    mockVoices([cn, hk])

    expect(getCurrentVoiceURI()).toBe('hk-1')
  })

  it('falls back to zh-CN only when no zh-TW and no other Chinese voice exists', () => {
    const cn = fakeVoice({ voiceURI: 'cn-1', name: 'Some CN Voice', lang: 'zh-CN' })
    const en = fakeVoice({ voiceURI: 'en-1', name: 'Some EN Voice', lang: 'en-US' })
    mockVoices([en, cn])

    expect(getCurrentVoiceURI()).toBe('cn-1')
  })

  it('does not hardcode any specific voice name — a differently-named zh-TW voice still wins over zh-CN', () => {
    const cn = fakeVoice({ voiceURI: 'cn-1', name: 'Whatever CN Voice', lang: 'zh-CN' })
    const tw = fakeVoice({ voiceURI: 'tw-1', name: 'A Completely Different TW Voice Name', lang: 'zh-TW' })
    mockVoices([cn, tw])

    expect(getCurrentVoiceURI()).toBe('tw-1')
  })

  it('returns empty string when no voices are available at all (falls back to browser default)', () => {
    mockVoices([])
    expect(getCurrentVoiceURI()).toBe('')
  })
})
