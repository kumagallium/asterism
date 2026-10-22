// 会話で受けた当てはめを外す（`PUT /upper` の remove: true）— ③の「外す」が送る形。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { removeDatasetUpper } from '../api'

vi.mock('../authToken', async (orig) => ({
  ...(await orig<typeof import('../authToken')>()),
  authHeaders: () => ({ 'X-Asterism-Token': 'tok' }),
}))

afterEach(() => vi.unstubAllGlobals())

describe('removeDatasetUpper', () => {
  it('外す線だけを remove: true で PUT する（ほかの線を送らない）', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ upper: [] }), { status: 200, headers: { 'content-type': 'application/json' } })),
    )
    vi.stubGlobal('fetch', fetchMock)
    await removeDatasetUpper('ds 1', [{ subject: 'property:M/粒径', term: 'https://sv#g', relation: 'equivalentProperty' }])
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/datasets/ds%201/upper')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      upper: [{ subject: 'property:M/粒径', term: 'https://sv#g', relation: 'equivalentProperty', remove: true }],
    })
  })

  it('失敗は投げる', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"detail":"x"}', { status: 404 }))))
    await expect(removeDatasetUpper('d', [])).rejects.toBeTruthy()
  })
})
