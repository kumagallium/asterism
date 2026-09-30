import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api'
import { refreshSample, restoreSample } from './cardsApi'

// 画面から API への配線（URL・メソッド・Content-Type・見出し・本文）を固定する。
// 見出しの値はサーバ（api/src/asterism_api/sample_routes.py）の INTENT_REFRESH・INTENT_RESTORE
// と同じ文字列。どちらかを打ち間違えると、サーバは 403 で断り、2 つのボタンが本番で必ず失敗する。

type Call = { url: string; init: RequestInit }

function stubFetch(status = 200, body = '{"ok":true}'): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init })
      return new Response(body, { status })
    }),
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('refreshSample', () => {
  it('置き換えの URL・見出し・本文（seq・revision・units）を送る', async () => {
    const calls = stubFetch()
    await refreshSample('world', { seq: 3, revision: 'abc' }, ['design', 'tools'])
    expect(calls).toHaveLength(1)
    const { url, init } = calls[0]
    expect(url).toBe('/api/datasets/world/sample/refresh')
    expect(init.method).toBe('POST')
    const headers = init.headers as Record<string, string>
    expect(headers['Content-Type']).toBe('application/json')
    expect(headers['X-Asterism-Intent']).toBe('sample-refresh')
    expect(JSON.parse(init.body as string)).toEqual({
      seq: 3,
      revision: 'abc',
      units: ['design', 'tools'],
    })
  })

  it('データセット id は URL 用にエスケープする', async () => {
    const calls = stubFetch()
    await refreshSample('a/b c', { seq: 1, revision: 'r' }, ['design'])
    expect(calls[0].url).toBe('/api/datasets/a%2Fb%20c/sample/refresh')
  })

  it('失敗は、サーバの固定コードを持つ ApiError で返る', async () => {
    stubFetch(409, JSON.stringify({ detail: { error: 'stale' } }))
    const err = await refreshSample('world', { seq: 3, revision: 'abc' }, ['design']).catch(
      (e: unknown) => e,
    )
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('stale')
  })
})

describe('restoreSample', () => {
  it('戻すの URL・見出し（置き換えとは別）・本文（at）を送る', async () => {
    const calls = stubFetch()
    await restoreSample('world', '2026-09-30T01:00:00+00:00')
    expect(calls).toHaveLength(1)
    const { url, init } = calls[0]
    expect(url).toBe('/api/datasets/world/sample/restore')
    expect(init.method).toBe('POST')
    const headers = init.headers as Record<string, string>
    expect(headers['Content-Type']).toBe('application/json')
    expect(headers['X-Asterism-Intent']).toBe('sample-restore')
    expect(JSON.parse(init.body as string)).toEqual({ at: '2026-09-30T01:00:00+00:00' })
  })
})

describe('書き込み認証の見出し', () => {
  it('トークンがあるとき、置き換えも戻すも X-Asterism-Token を送る', async () => {
    vi.stubGlobal('sessionStorage', { getItem: () => 'secret-token' })
    const calls = stubFetch()
    await refreshSample('world', { seq: 3, revision: 'abc' }, ['design'])
    await restoreSample('world', '2026-09-30T01:00:00+00:00')
    for (const { init } of calls) {
      expect((init.headers as Record<string, string>)['X-Asterism-Token']).toBe('secret-token')
    }
  })

  it('トークンが無いときは見出しを付けない', async () => {
    vi.stubGlobal('sessionStorage', { getItem: () => null })
    const calls = stubFetch()
    await refreshSample('world', { seq: 3, revision: 'abc' }, ['design'])
    expect(Object.keys(calls[0].init.headers as Record<string, string>)).not.toContain(
      'X-Asterism-Token',
    )
  })
})
