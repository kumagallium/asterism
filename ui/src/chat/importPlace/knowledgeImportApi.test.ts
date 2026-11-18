import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImportApiError, listImports, normalizeResult, normalizeSources, removeImport, startBundleImport, startMcpImport } from './knowledgeImportApi'

const mockFetch = (res: Partial<Response> & { json?: () => Promise<unknown> }) => {
  const f = vi.fn(async () => ({ ok: true, status: 200, ...res }) as Response)
  vi.stubGlobal('fetch', f)
  return f
}
afterEach(() => vi.unstubAllGlobals())

describe('形の検査', () => {
  it('一覧: 壊れた行は捨て、数は 0 に寄せる', () => {
    const rows = normalizeSources({ sources: [{ source: 'a', counts: { claims: 3 }, running: 'import' }, { nope: 1 }, null] })
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ source: 'a', running: 'import', counts: { claims: 3, topics: 0 }, relations: { unconfirmed: 0 } })
    expect(normalizeSources('x')).toEqual([])
  })
  it('結果: 壊れていれば null・問いは形の合うものだけ', () => {
    expect(normalizeResult(null)).toBeNull()
    expect(normalizeResult({ source: 'a' })).toBeNull()
    const r = normalizeResult({
      source: 'a',
      copied: { claims: 2 },
      relations: { skipped: 'ai_unset' },
      questions: [{ op: 'x', term: { id: 't', name: '語' }, text: '問い', count: 2 }, { text: 1 }],
      questions_total: 9,
    })
    expect(r?.questions).toHaveLength(1)
    expect(r?.relations.skipped).toBe('ai_unset')
    expect(r?.questions_total).toBe(9)
  })
})

describe('呼び出しの形', () => {
  it('MCP の取り込み: POST・lang・AI のヘッダ・job_id を返す', async () => {
    const f = mockFetch({ json: async () => ({ job_id: 'j1', server: 'graphium' }) })
    const id = await startMcpImport('graphium', 'en', { apiKey: 'k', provider: 'openai', modelId: 'm' } as never)
    expect(id).toBe('j1')
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/mcp-servers/graphium/knowledge-import')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ lang: 'en' })
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json', 'X-API-Key': 'k', 'X-LLM-Provider': 'openai', 'X-LLM-Model': 'm' })
  })
  it('束の取り込み: source・bundle・lang を本文に', async () => {
    const f = mockFetch({ json: async () => ({ job_id: 'j2' }) })
    await startBundleImport('lab', { a: 1 }, 'ja', null)
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/knowledge-imports')
    expect(JSON.parse(init.body as string)).toEqual({ source: 'lab', bundle: { a: 1 }, lang: 'ja' })
  })
  it('409 は理由つきの ImportApiError', async () => {
    mockFetch({ ok: false, status: 409, json: async () => ({ detail: 'lab は別の作業 (import) が走っている途中です' }) })
    const e = await startMcpImport('lab', 'ja', null).catch((x: unknown) => x)
    expect(e).toBeInstanceOf(ImportApiError)
    expect((e as ImportApiError).status).toBe(409)
    expect((e as ImportApiError).message).toContain('別の作業')
  })
  it('一覧・外す', async () => {
    const f = mockFetch({ json: async () => ({ sources: [{ source: 'a' }] }) })
    expect(await listImports()).toHaveLength(1)
    await removeImport('a b')
    const [url, init] = f.mock.calls[1] as unknown as [string, RequestInit]
    expect(url).toBe('/api/knowledge-imports/a%20b')
    expect(init.method).toBe('DELETE')
  })
})
