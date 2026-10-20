import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyOnScreen, dismissOnScreen, proposeOnScreen } from './graphChatApi'

vi.mock('../authToken', async (orig) => ({
  ...(await orig<typeof import('../authToken')>()),
  authHeaders: () => ({ 'X-Asterism-Token': 'tok' }),
}))

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

afterEach(() => vi.unstubAllGlobals())

const REL = { op: 'add_relation', from: 'kind:ab12cd34ef', rel: 'eq', to: 'specimen', reason: '同じ試料' }

describe('/api/consult/graph（home・dataset）', () => {
  it('提案: screen と文脈（選んでいる星）・新しい 12 件・言語を送り、{reply, ops, sentences, proposal_id} を読む', async () => {
    const fetchMock = vi.fn(() => json({ reply: 'はい', ops: [REL], sentences: ['食材 と 試料 は同じもの'], proposal_id: 'p1' }))
    vi.stubGlobal('fetch', fetchMock)
    const many = Array.from({ length: 14 }, (_, i) => ({ role: 'user' as const, content: `m${i}` }))
    const res = await proposeOnScreen('home', { focus_kind: 'https://ex/K' }, many, 'ja', null)
    expect(res.ops).toEqual([REL])
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/consult/graph')
    const body = JSON.parse(String(init.body))
    expect(body).toMatchObject({ screen: 'home', context: { focus_kind: 'https://ex/K' }, lang: 'ja' })
    expect(body.messages).toHaveLength(12)
  })

  it('適用: 来歴は ai・選んだ ops だけ・書いた線の端（lines）を読む', async () => {
    const fetchMock = vi.fn(() =>
      json({
        applied: 1,
        dropped: [],
        unlinked: [],
        lines: [{ action: 'add', source: 'https://ex/K', target: 'https://sv#specimen', relation: 'equivalentClass' }, { nope: 1 }],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const res = await applyOnScreen({ screen: 'home', context: {}, ops: [REL], proposalId: 'p1' })
    expect(res.applied).toBe(1)
    expect(res.lines).toEqual([
      { action: 'add', source: 'https://ex/K', target: 'https://sv#specimen', relation: 'equivalentClass' },
    ])
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toMatchObject({ screen: 'home', drafted_by: 'ai', proposal_id: 'p1', ops: [REL] })
    expect((init.headers as Record<string, string>)['X-Asterism-Token']).toBe('tok')
  })

  it('dataset は dataset_id を文脈に送る。却下は件数を返す。503・404 は status つきで投げる', async () => {
    const fetchMock = vi.fn(() => json({ dismissed: 1 }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await dismissOnScreen('dataset', { dataset_id: 'ds1' }, [REL])).toBe(1)
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toMatchObject({ screen: 'dataset', context: { dataset_id: 'ds1' } })
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: 'AI が設定されていません' }, 503)))
    await expect(proposeOnScreen('dataset', { dataset_id: 'ds1' }, [{ role: 'user', content: 'x' }], 'ja', null)).rejects.toMatchObject({
      status: 503,
      detail: 'AI が設定されていません',
    })
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: 'unknown dataset_id' }, 404)))
    await expect(applyOnScreen({ screen: 'dataset', context: { dataset_id: 'zz' }, ops: [REL], proposalId: 'p' })).rejects.toMatchObject({
      status: 404,
    })
  })
})
