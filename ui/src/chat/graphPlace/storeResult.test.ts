import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchLedger, mergeStore, normalizeRow, normalizeStoreReport, postAssign, reviewInChunks, type ImportSource } from './knowledgeRelationsApi'

const info: ImportSource = { source: 's1', title: '題', datasetId: null, copiedAt: null, running: false }
const T = (id: string, name: string, extra: object = {}) => ({ id, name, kind: 'class', role: 'material', ...extra })
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
afterEach(() => vi.unstubAllGlobals())

describe('行の term_iri・store', () => {
  const base = { key: 'k', state: 'relation', rel: 'makes' }
  it('term_iri は無ければ null（欄ごと無い語）・store は無ければ null', () => {
    const r = normalizeRow('s', { ...base, from: T('t-a', 'あ', { term_iri: 'urn:a' }), to: T('t-b', 'い') }, 'relation')!
    expect(r.from.termIri).toBe('urn:a')
    expect(r.to.termIri).toBeNull()
    expect(r.store).toBeNull()
  })
  it('store: {ok, reason?} を読む。形が壊れていれば null', () => {
    const f = (store: unknown) => normalizeRow('s', { ...base, from: T('t-a', 'あ'), to: T('t-b', 'い'), store }, 'relation')!.store
    expect(f({ ok: true })).toEqual({ ok: true })
    expect(f({ ok: false, reason: 'role_mismatch' })).toEqual({ ok: false, reason: 'role_mismatch' })
    expect(f({ reason: 'x' })).toBeNull()
    expect(f('x')).toBeNull()
  })
})

describe('確認の返りの store', () => {
  it('normalizeStoreReport: 数・failed・error・remaining。無ければ null', () => {
    expect(normalizeStoreReport(undefined)).toBeNull()
    expect(normalizeStoreReport({ placed: 2, removed: 1, failed: [{ key: 'k', reason: 'cycle' }], remaining: 0 })).toEqual({ placed: 2, removed: 1, failed: [{ key: 'k', reason: 'cycle' }], remaining: 0, error: false })
    expect(normalizeStoreReport({ placed: 0, removed: 0, failed: [], remaining: 0, error: true })?.error).toBe(true)
  })
  it('50 件を超えて分けたとき store は足し合わせる（置いた数・失敗の並び・error は 1 回でもあれば）', async () => {
    const bodies = [
      { changed: ['a'], skipped: [], store: { placed: 50, removed: 0, failed: [], remaining: 20 } },
      { changed: ['b'], skipped: [], store: { placed: 10, removed: 1, failed: [{ key: 'k', reason: 'self' }], remaining: 0 } },
    ]
    let n = 0
    vi.stubGlobal('fetch', async () => json(bodies[n++]))
    const r = await reviewInChunks('s1', 'confirm', Array.from({ length: 60 }, (_, i) => `k${i}`))
    expect(r.store).toEqual({ placed: 60, removed: 1, failed: [{ key: 'k', reason: 'self' }], remaining: 0, error: false })
    expect(mergeStore(null, null)).toBeNull()
  })
  it('postAssign は返りの store を返す（古い裏側なら null）', async () => {
    vi.stubGlobal('fetch', async () => json({ changed: ['k'], skipped: [], store: { placed: 1, removed: 0, failed: [], remaining: 0 } }))
    expect((await postAssign('s1', 'p', 'makes', 'a'))?.placed).toBe(1)
    vi.stubGlobal('fetch', async () => json({ changed: ['k'], skipped: [] }))
    expect(await postAssign('s1', 'p', 'makes', 'a')).toBeNull()
  })
  it('fetchLedger: 行の term_iri・store がそのまま台帳に載る', async () => {
    vi.stubGlobal('fetch', async () => json({ summary: {}, relations: [{ key: 'k', state: 'relation', rel: 'makes', from: T('t-a', 'あ', { term_iri: 'urn:a' }), to: T('t-b', 'い'), store: { ok: false, reason: 'kind_mismatch' } }] }))
    const l = await fetchLedger(info, 'ja')
    expect(l.rows[0].from.termIri).toBe('urn:a')
    expect(l.rows[0].store).toEqual({ ok: false, reason: 'kind_mismatch' })
  })
})

describe('backlog_failed（この操作と関係のない落ちた件の数）', () => {
  it('あれば数を持つ・無い返りでも落ちない。failed はその操作の件だけ', () => {
    expect(normalizeStoreReport({ placed: 1, removed: 0, failed: [{ key: 'k', reason: 'cycle' }], remaining: 0, backlog_failed: 3 })).toMatchObject({ failed: [{ key: 'k', reason: 'cycle' }], backlogFailed: 3 })
    expect(normalizeStoreReport({ placed: 1, removed: 0, failed: [], remaining: 0 })?.backlogFailed).toBeUndefined()
  })
})
