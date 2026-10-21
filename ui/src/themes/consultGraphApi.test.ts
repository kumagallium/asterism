import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyGraph, dismissGraph, proposeGraph, validateApply, validateProposal } from './consultGraphApi'
import { ThemesApiError } from './themesApi'

vi.mock('../authToken', async (orig) => ({
  ...(await orig<typeof import('../authToken')>()),
  authHeaders: () => ({ 'X-Asterism-Token': 'tok' }),
}))

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

afterEach(() => vi.unstubAllGlobals())

const RETARGET = { op: 'retarget', from: 'c4', rel: 'lowers', to: 'c1', new_to: 'c2', reason: '格子の分' }

describe('POST /api/consult/graph (propose)', () => {
  it('sends the screen, the draft, the newest 12 messages and the language; reads {reply, ops, sentences, proposal_id}', async () => {
    const fetchMock = vi.fn(() =>
      json({ reply: '格子に効きます', ops: [RETARGET], sentences: ['粒界散乱 の線を 格子熱伝導率 へ'], proposal_id: 'p1' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const many = Array.from({ length: 15 }, (_, i) => ({ role: 'user' as const, content: `m${i}` }))
    const res = await proposeGraph('theme', { draft_id: 'd1' }, many, 'ja', null)
    expect(res.proposal_id).toBe('p1')
    expect(res.ops).toHaveLength(1)
    expect(res.sentences).toEqual(['粒界散乱 の線を 格子熱伝導率 へ'])
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/consult/graph')
    const body = JSON.parse(String(init.body))
    expect(body).toMatchObject({ screen: 'theme', context: { draft_id: 'd1' }, lang: 'ja' })
    expect(body.messages).toHaveLength(12)
    expect(body.messages[11].content).toBe('m14')
  })

  it('turns 503 (no AI) and 404 (no draft) into a ThemesApiError with the status', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: 'AI が設定されていません' }, 503)))
    await expect(proposeGraph('theme', { draft_id: 'd1' }, [{ role: 'user', content: 'x' }], 'ja', null)).rejects.toMatchObject({
      status: 503,
    })
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: '下書きがありません' }, 404)))
    await expect(proposeGraph('theme', { draft_id: 'zz' }, [{ role: 'user', content: 'x' }], 'ja', null)).rejects.toBeInstanceOf(
      ThemesApiError,
    )
  })

  it('pads sentences to the ops (the server keeps them parallel; the screen never composes one)', () => {
    const v = validateProposal({ reply: '', ops: [RETARGET, RETARGET], sentences: ['one'], proposal_id: 'p' })!
    expect(v.sentences).toEqual(['one', ''])
    expect(validateProposal({ reply: '', ops: [{ nope: 1 }], sentences: [], proposal_id: 'p' })).toBeNull()
    expect(validateProposal({ reply: '', ops: [], sentences: [] })).toBeNull()
  })
})

describe('POST /api/consult/graph/apply', () => {
  it('sends ops, proposal_id, drafted_by and only the person’s own words; reads the draft, dropped and unlinked', async () => {
    const fetchMock = vi.fn(() =>
      json({
        applied: 2,
        dropped: [{ index: 2, op: 'retarget', reason: 'cycle', ref: 'c4' }],
        unlinked: ['c9'],
        draft: { id: 'd1', step: 2, label: 'T', questions: ['Q'], concepts: [], relations: [], answers: [], chat: { messages: [{ role: 'user', content: '散乱は格子に', at: 'x' }], proposals: [] } },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const res = await applyGraph({
      context: { draft_id: 'd1' },
      ops: [RETARGET],
      proposalId: 'p1',
      draftedBy: 'ai',
      messages: ['散乱は格子に'],
    })
    expect(res.applied).toBe(2)
    expect(res.dropped[0]).toMatchObject({ index: 2, reason: 'cycle' })
    expect(res.unlinked).toEqual(['c9'])
    expect(res.draft?.chat?.messages[0].content).toBe('散乱は格子に')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/consult/graph/apply')
    expect((init.headers as Record<string, string>)['X-Asterism-Token']).toBe('tok')
    expect(JSON.parse(String(init.body))).toMatchObject({
      screen: 'theme',
      proposal_id: 'p1',
      drafted_by: 'ai',
      messages: [{ role: 'user', content: '散乱は格子に' }],
    })
  })

  it('a human edit has no messages and says drafted_by human', async () => {
    const fetchMock = vi.fn(() => json({ applied: 1, dropped: [], unlinked: [] }))
    vi.stubGlobal('fetch', fetchMock)
    const res = await applyGraph({ context: { draft_id: 'd1' }, ops: [RETARGET], proposalId: 'h-1', draftedBy: 'human' })
    expect(res.draft).toBeNull()
    const body = JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body))
    expect(body.drafted_by).toBe('human')
    expect(body.messages).toBeUndefined()
  })

  it('knows a duplicate application did nothing', () => {
    expect(validateApply({ applied: 0, dropped: [], unlinked: [], duplicate: true })?.duplicate).toBe(true)
    expect(validateApply({ nope: 1 })).toBeNull()
  })
})

describe('POST /api/consult/graph/dismiss', () => {
  it('sends the same ops as they are and returns the count', async () => {
    const fetchMock = vi.fn(() => json({ dismissed: 1 }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await dismissGraph({ draft_id: 'd1' }, [RETARGET])).toBe(1)
    const body = JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body))
    expect(body.ops).toEqual([RETARGET])
    expect(body.screen).toBe('theme')
  })
})
