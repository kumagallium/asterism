import { afterEach, describe, expect, it, vi } from 'vitest'
import '../i18n'
import { contentsOf, fetchContents } from './chatApi'
import { CACHE_MAX, keyString, pickState, putCache } from './useContents'

const table = (label: string) => ({
  kind: { iri: 'k:1', label },
  columns: [{ iri: 'c:1', label: '列' }],
  rows: [{ iri: 'r:1', label: '行', cells: { 'c:1': '値' } }],
  total: 1,
  truncated: false,
})

afterEach(() => vi.unstubAllGlobals())

describe('useContents の部品', () => {
  it('key が違えば別の文字列、同じなら同じ', () => {
    expect(keyString({ kind: 'a' })).toBe(keyString({ kind: 'a' }))
    expect(keyString({ kind: 'a' })).not.toBe(keyString({ term: 'a' }))
  })
  it('cache は上限を超えたら古いものから捨てる', () => {
    const m = new Map<string, number>()
    for (let i = 0; i < CACHE_MAX + 5; i++) putCache(m, `k${i}`, i)
    expect(m.size).toBe(CACHE_MAX)
    expect(m.has('k0')).toBe(false)
    expect(m.has(`k${CACHE_MAX + 4}`)).toBe(true)
  })
})

describe('pickState', () => {
  const data = { title: '', tables: [], more: 0, note: null }
  const done = { id: 'ja|1|K', ks: 'K', state: { status: 'ok' as const, data } }
  it('同じ組なら返りそのまま', () => {
    expect(pickState(done, 'ja|1|K', 'K')).toBe(done.state)
  })
  it('signature だけ変わったら前の表を残して refreshing', () => {
    expect(pickState(done, 'ja|2|K', 'K')).toEqual({ status: 'ok', data, refreshing: true })
  })
  it('key が変わったら loading、key が無ければ idle', () => {
    expect(pickState(done, 'ja|2|J', 'J')).toEqual({ status: 'loading' })
    expect(pickState(done, null, null)).toEqual({ status: 'idle' })
  })
  it('retry で done が空なら loading（失敗の表示を消す）', () => {
    expect(pickState(null, 'ja|1|K', 'K')).toEqual({ status: 'loading' })
  })
})

describe('contentsOf / fetchContents', () => {
  it('表の形を検査して読む（壊れた表は全体を失敗にする）', () => {
    const ok = contentsOf({ title: 'T', tables: [{ dataset: { id: 'd', label: 'D' }, table: table('種類') }], more: 2, note: null })
    expect(ok?.tables[0].table.rows[0].cells['c:1'].text).toBe('値')
    expect(ok?.more).toBe(2)
    expect(contentsOf({ title: 'T', tables: [{ dataset: { id: 'd' }, table: 1 }] })).toBeNull()
    expect(contentsOf(null)).toBeNull()
  })
  it('key を query にして呼ぶ', async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ title: '', tables: [], more: 0, note: null }) })
    vi.stubGlobal('fetch', f)
    const r = await fetchContents({ term: 'http://x/y' }, 'en')
    expect(r.tables).toEqual([])
    const url = String(f.mock.calls[0][0])
    expect(url).toContain('/api/chat/contents?')
    expect(url).toContain('term=http%3A%2F%2Fx%2Fy')
    expect(url).toContain('lang=en')
  })
  it('形が悪い返りは失敗', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ nope: 1 }) }))
    await expect(fetchContents({ session: 's' })).rejects.toBeTruthy()
  })
})
