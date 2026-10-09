import { afterEach, describe, expect, it, vi } from 'vitest'
import { getAlignments } from './crosswalkApi'
import { addCq, fit, listTerms, mintTerm, removeTerm, VocabApiError } from './vocabApi'

function stubFetch(status: number, body: unknown) {
  const f = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  }))
  vi.stubGlobal('fetch', f)
  return f
}

afterEach(() => vi.unstubAllGlobals())

describe('vocabApi — ことばの端点クライアント', () => {
  it('listTerms は {terms} を開いて配列で返す（無ければ空）', async () => {
    stubFetch(200, { terms: [{ slug: 'composition' }] })
    expect(await listTerms()).toEqual([{ slug: 'composition' }])
    stubFetch(200, {})
    expect(await listTerms()).toEqual([])
  })

  it('mintTerm は POST して {term} を開く', async () => {
    const f = stubFetch(201, { term: { slug: 'x' } })
    const t = await mintTerm({
      slug: 'x',
      kind: 'class',
      label_ja: 'エックス',
      cqs: [{ title: '何件', op: 'count' }],
    })
    expect(t).toEqual({ slug: 'x' })
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/vocab/shared')
    expect(init.method).toBe('POST')
  })

  it('失敗は status 付きの VocabApiError（409 / 422 を言い分けられる）', async () => {
    stubFetch(409, { detail: '同じ slug' })
    const err = await mintTerm({ slug: 'x', kind: 'class', label_ja: 'x', cqs: [] }).catch((e) => e)
    expect(err).toBeInstanceOf(VocabApiError)
    expect((err as VocabApiError).status).toBe(409)
    expect((err as VocabApiError).detail).toBe('同じ slug')
  })

  it('removeTerm は DELETE（204 で解決）、slug は URL エンコードする', async () => {
    const f = stubFetch(204, '')
    await removeTerm('a b')
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/vocab/shared/a%20b')
    expect(init.method).toBe('DELETE')
  })

  it('addCq は /cq に POST する', async () => {
    const f = stubFetch(201, { term: 'x', cq: { tool_name: 'cq_x_count', title: 't' } })
    await addCq('x', { title: 't', op: 'count' })
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe('/api/vocab/shared/x/cq')
  })

  it('fit は label / column をクエリに載せ、candidates を返す', async () => {
    const f = stubFetch(200, { candidates: [{ term: 'iri', kind: 'standard' }] })
    expect(await fit('組成', 'composition')).toHaveLength(1)
    const url = (f.mock.calls[0] as unknown as [string])[0]
    expect(url).toContain('/api/vocab/fit?')
    expect(url).toContain('column=composition')
  })
})

describe('crosswalkApi.getAlignments — scope を通す', () => {
  it('引数なしは従来どおり（クエリ無し）', async () => {
    const f = stubFetch(200, { alignments: [], relations: [] })
    await getAlignments()
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe('/api/crosswalk/alignments')
  })

  it('scope を ?scope= に載せる', async () => {
    const f = stubFetch(200, { alignments: [], relations: [] })
    await getAlignments('shared')
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe('/api/crosswalk/alignments?scope=shared')
  })
})
