import { describe, expect, it } from 'vitest'
import {
  COPILOT_PROVIDER,
  PROVIDERS,
  answerCredentials,
  isModelReady,
  withActiveModel,
  withAddedModel,
  withoutModel,
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

describe('役割ごとの AI（design §21）', () => {
  const make = { provider: 'anthropic', modelId: 'big', apiBase: null, apiKey: 'mk', maxTokens: null }
  const answer = { provider: 'openai-compatible', modelId: 'small', apiBase: 'https://s/v1', apiKey: 'ak', maxTokens: 8000 }

  it('答える AI が同じなら X-LLM-Answer-* を送らない', () => {
    const h = llmHeaders(make)
    expect(Object.keys(h).some((k) => k.startsWith('X-LLM-Answer'))).toBe(false)
    expect(answerCredentials(make)).toEqual(make)
  })

  it('別のモデルなら 2 組を送り、Ask には答える AI だけ', () => {
    const h = llmHeaders({ ...make, answer })
    expect(h['X-LLM-Provider']).toBe('anthropic')
    expect(h['X-API-Key']).toBe('mk')
    expect(h['X-LLM-Answer-Provider']).toBe('openai-compatible')
    expect(h['X-LLM-Answer-Key']).toBe('ak')
    expect(h['X-LLM-Answer-Api-Base']).toBe('https://s/v1')
    expect(h['X-LLM-Answer-Max-Tokens']).toBe('8000')
    expect(llmHeaders(answerCredentials({ ...make, answer }))).toEqual({
      'X-API-Key': 'ak',
      'X-LLM-Provider': 'openai-compatible',
      'X-LLM-Model': 'small',
      'X-LLM-Api-Base': 'https://s/v1',
      'X-LLM-Max-Tokens': '8000',
    })
  })
})

describe('役割の状態の移り変わり（design §21）', () => {
  const m = (id: string) => ({ id, name: id, provider: 'openai' as const, modelId: id, apiBase: null })
  const base = { models: [m('a'), m('b')], activeModelId: 'a', answerModelId: 'b' }

  it('モデルを足しても答える AI の指定は残る', () => {
    expect(withAddedModel(base, m('c')).answerModelId).toBe('b')
  })
  it('答える AI と同じモデルを作る AI にすると「同じ」に戻り、戻しても復活しない', () => {
    const same = withActiveModel(base, 'b')
    expect(same.answerModelId).toBeNull()
    expect(withActiveModel(same, 'a').answerModelId).toBeNull()
    expect(withActiveModel(base, 'a').answerModelId).toBe('b')
  })
  it('答える AI のモデルを消すと「同じ」に戻る', () => {
    expect(withoutModel(base, 'b')).toEqual({ models: [m('a')], activeModelId: 'a', answerModelId: null })
    // 作る AI を消して答える AI のモデルが作る AI になったら、答える AI は「同じ」
    expect(withoutModel(base, 'a')).toMatchObject({ activeModelId: 'b', answerModelId: null })
  })
})
