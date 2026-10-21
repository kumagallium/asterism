import { describe, expect, it } from 'vitest'
import {
  COPILOT_PROVIDER,
  PROVIDERS,
  isModelReady,
  llmHeaders,
  providerAsksApiBase,
  providerNeedsKey,
  providerOfPastedKey,
} from './store'

describe('プロバイダの判定', () => {
  it('Copilot はキー無しで準備完了、他はキーが要る', () => {
    expect(isModelReady(COPILOT_PROVIDER, false, false)).toBe(true)
    expect(isModelReady('gemini', false, false)).toBe(false)
    expect(isModelReady('gemini', true, false)).toBe(true)
    expect(isModelReady('openai', false, true)).toBe(true)
  })

  it('Copilot は、サーバが動かせないとき（共有サーバ）は準備完了にしない', () => {
    expect(isModelReady(COPILOT_PROVIDER, false, false, false)).toBe(false)
    expect(isModelReady(COPILOT_PROVIDER, false, false, true)).toBe(true)
    // 他のプロバイダは copilotAvailable に左右されない
    expect(isModelReady('gemini', true, false, false)).toBe(true)
  })

  it('Gemini と Copilot は base URL を聞かない', () => {
    expect(providerAsksApiBase('gemini')).toBe(false)
    expect(providerAsksApiBase(COPILOT_PROVIDER)).toBe(false)
    expect(providerAsksApiBase('openai-compatible')).toBe(true)
    expect(PROVIDERS.find((p) => p.id === 'gemini')?.needsApiBase).toBe(false)
  })

  it('キー欄は Copilot だけ無い', () => {
    expect(providerNeedsKey(COPILOT_PROVIDER)).toBe(false)
    expect(providerNeedsKey('gemini')).toBe(true)
  })

  it('貼ったキーから推定する（AIza は Gemini）', () => {
    expect(providerOfPastedKey('AIzaSyDummy')).toBe('gemini')
    expect(providerOfPastedKey('sk-ant-xxx')).toBe('anthropic')
    expect(providerOfPastedKey('sk-xxx')).toBe('openai')
    expect(providerOfPastedKey('zzz')).toBeNull()
  })

  it('Copilot のヘッダにキーは載らない', () => {
    const h = llmHeaders({
      provider: COPILOT_PROVIDER,
      modelId: 'auto',
      apiBase: null,
      apiKey: '',
      maxTokens: null,
    })
    expect(h['X-LLM-Provider']).toBe(COPILOT_PROVIDER)
    expect(h['X-API-Key']).toBeUndefined()
  })
})
