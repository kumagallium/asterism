import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchLedger, KnowledgeApiError, normalizeRow, postAssign, REVIEW_MAX, reviewInChunks, type ImportSource } from './knowledgeRelationsApi'

const info: ImportSource = { source: 's1', title: '題', datasetId: null, copiedAt: null, running: false }
const T = (id: string, name: string) => ({ id, name, kind: 'class', role: 'material' })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

describe('normalizeRow', () => {
  it('confirmed は dict でも bool でも真偽に直す。dropped の rejected は rejected になる', () => {
    const base = { key: 'k', state: 'relation', rel: 'makes', from: T('t-a', 'あ'), to: T('t-b', 'い') }
    expect(normalizeRow('s', { ...base, confirmed: { at: 'x' } }, 'relation')?.confirmed).toBe(true)
    expect(normalizeRow('s', { ...base, confirmed: null }, 'relation')?.confirmed).toBe(false)
    expect(normalizeRow('s', { ...base, state: 'dropped', reason: 'rejected' }, 'dropped')?.rejected).toBe(true)
    expect(normalizeRow('s', { ...base, state: 'dropped', reason: 'cycle' }, 'dropped')?.rejected).toBe(false)
    expect(normalizeRow('s', { ...base, rejected: true }, 'dropped')?.rejected).toBe(true)
  })
  it('両端の語が無い行・壊れた行は捨てる。根拠の source_url を取る', () => {
    expect(normalizeRow('s', { key: 'k', from: T('t-a', 'あ') }, 'relation')).toBeNull()
    expect(normalizeRow('s', 'x', 'relation')).toBeNull()
    const r = normalizeRow('s', { key: 'k', from: T('t-a', 'あ'), to: T('t-b', 'い'), evidence: [{ id: 'c1', title: '題', source_url: 'https://example.org/x' }] }, 'relation')
    expect(r?.evidence[0]).toEqual({ id: 'c1', title: '題', sourceUrl: 'https://example.org/x' })
  })
})

describe('fetchLedger', () => {
  it('state=all の 1 回で読み、relations と origin を束ねる。origin の行は ai の判定で状態を決め、failed は捨てる', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', async (url: string) => {
      calls.push(url)
      return json({
        summary: { shown: 2, terms: 3, candidates: 1 },
        relations: [
          { key: 'k1', pair: 't-a|t-b', state: 'relation', rel: 'makes', from: T('t-a', 'あ'), to: T('t-b', 'い') },
          { key: 'k2', pair: 't-a|t-c', state: 'none', from: T('t-a', 'あ'), to: T('t-c', 'う') },
          { key: 'k3', pair: 't-a|t-d', state: 'dropped', reason: 'rejected', rel: 'makes', from: T('t-a', 'あ'), to: T('t-d', 'え') },
          { key: 'k4', pair: 't-a|t-e', state: 'failed', from: T('t-a', 'あ'), to: T('t-e', 'お') },
        ],
        origin: [
          { key: 'o:t-b|sub|t-c', pair: 't-b|t-c', rel: 'sub', from: T('t-b', 'い'), to: T('t-c', 'う'), by: 'origin', ai: 'unseen' },
          { key: 'o:t-a|sub|t-c', pair: 't-a|t-c', rel: 'sub', from: T('t-a', 'あ'), to: T('t-c', 'う'), by: 'origin', ai: 'disputes' },
        ],
      })
    })
    const l = await fetchLedger(info, 'ja')
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('state=all')
    expect(l.summary).toEqual({ shown: 2, terms: 3, candidates: 1 })
    expect(l.rows.map((r) => `${r.state}:${r.key}:${r.pair}`)).toEqual([
      'relation:k1:t-a|t-b',
      'none:k2:t-a|t-c',
      'dropped:k3:t-a|t-d',
      'relation:o:t-b|sub|t-c:t-b|t-c',
      'none:o:t-a|sub|t-c:t-a|t-c',
    ])
    expect(l.rows[2].rejected).toBe(true)
  })
  it('失敗は status つきで投げる', async () => {
    vi.stubGlobal('fetch', async () => json({ detail: '作り直している途中です' }, 409))
    await expect(fetchLedger(info, 'ja')).rejects.toMatchObject({ status: 409 })
  })
})

describe('書き込み', () => {
  it('review は 50 件ずつに分けて送る。action と keys が渡る', async () => {
    const bodies: { action: string; keys: string[] }[] = []
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      expect(url).toBe('/api/knowledge-copies/s%2F1/relations/review')
      expect(init.method).toBe('POST')
      bodies.push(JSON.parse(String(init.body)))
      const keys = bodies[bodies.length - 1].keys
      return json({ changed: keys, skipped: [], summary: {} })
    })
    const keys = Array.from({ length: 120 }, (_, i) => `k${i}`)
    const r = await reviewInChunks('s/1', 'confirm', keys)
    expect(bodies.map((b) => b.keys.length)).toEqual([REVIEW_MAX, REVIEW_MAX, 20])
    expect(bodies.every((b) => b.action === 'confirm')).toBe(true)
    expect(r.changed).toHaveLength(120)
  })
  it('skipped を返す', async () => {
    vi.stubGlobal('fetch', async () => json({ changed: [], skipped: [{ key: 'k', reason: 'state' }] }))
    expect((await reviewInChunks('s', 'reject', ['k'])).skipped).toEqual([{ key: 'k', reason: 'state' }])
  })
  it('assign は key・rel・subject を送り、422 の理由を message に持つ', async () => {
    let sent: unknown = null
    vi.stubGlobal('fetch', async (_u: string, init: RequestInit) => {
      sent = JSON.parse(String(init.body))
      return json({ detail: '自分自身には付けられません' }, 422)
    })
    await expect(postAssign('s', 'k', 'makes', 'b')).rejects.toSatisfy((e) => e instanceof KnowledgeApiError && e.status === 422 && e.message === '自分自身には付けられません')
    expect(sent).toEqual({ key: 'k', rel: 'makes', subject: 'b' })
  })
})
