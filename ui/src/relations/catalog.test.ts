import { afterEach, describe, expect, it, vi } from 'vitest'
import { BUILTIN_CATALOG } from './builtinCatalog'
import { BUILTIN, getCatalog, isCatalogShape, loadRelationCatalog, mergeCatalog, resetCatalogForTest, subscribeCatalog } from './catalog'
import { draftRelationLabel, exerciseCatalog, isDirectedPredicate, isRelationName, knowledgePredicatesFor, relationSentence } from './relationFns'

const reply = (body: unknown, ok = true) => vi.fn(async () => ({ ok, json: async () => body }) as Response) as unknown as typeof fetch

// API にだけある関係（R3 で利用者が足す形のつもり）
const EXTRA = {
  relations: [
    {
      name: 'inhibits',
      label: { ja: '妨げる', en: 'inhibits' },
      sentence: { ja: '{s} は {o} を妨げる', en: '{s} inhibits {o}' },
      predicates: [{ name: 'inhibits', iri: 'https://example.test/inhibits', when: null }],
      symmetric_draft: false,
      symmetric_pair: false,
      experiment: false,
      builtin: false,
    },
  ],
  predicates: [
    {
      name: 'inhibits',
      iri: 'https://example.test/inhibits',
      shapes: { from: ['class'], to: ['class'] },
      same_shape: false,
      roles: null,
      no_self: true,
      subsumption: false,
      symmetric: false,
      knowledge: true,
      own: null,
    },
  ],
}

afterEach(() => resetCatalogForTest())

describe('目録の store', () => {
  it('読む前は組み込み', () => {
    expect(getCatalog()).toBe(BUILTIN)
    expect(getCatalog().relations).toHaveLength(12)
    expect(getCatalog().predicates).toHaveLength(15)
  })

  it('成功: API にだけある関係が重なり、購読者に知らせる', async () => {
    const seen = vi.fn()
    subscribeCatalog(seen)
    await loadRelationCatalog(reply({ relations: [...BUILTIN_CATALOG.relations, ...EXTRA.relations], predicates: [...BUILTIN_CATALOG.predicates, ...EXTRA.predicates] }))
    expect(seen).toHaveBeenCalledTimes(1)
    expect(isRelationName('inhibits')).toBe(true)
    expect(draftRelationLabel('inhibits', 'ja')).toBe('妨げる')
    expect(relationSentence('inhibits', 'A', 'B', 'en')).toBe('A inhibits B')
    expect(isDirectedPredicate('inhibits')).toBe(true)
    expect(knowledgePredicatesFor('class', 'class')).toContain('inhibits')
    // 組み込みの並びは先頭のまま
    expect(getCatalog().relations.slice(0, 12).map((r) => r.name)).toEqual(BUILTIN.relations.map((r) => r.name))
  })

  it('成功: 組み込みと同じ内容なら store は変えない（通知しない・同じ object）', async () => {
    const seen = vi.fn()
    subscribeCatalog(seen)
    await loadRelationCatalog(reply(structuredClone(BUILTIN_CATALOG)))
    expect(seen).not.toHaveBeenCalled()
    expect(getCatalog()).toBe(BUILTIN)
  })

  it('失敗: 404・例外・形の違いでも組み込みのまま', async () => {
    for (const f of [reply({}, false), vi.fn(async () => Promise.reject(new Error('down'))) as unknown as typeof fetch, reply({ relations: 1 })]) {
      resetCatalogForTest()
      await loadRelationCatalog(f, 0)
      expect(getCatalog()).toBe(BUILTIN)
    }
  })

  it('読むのは 1 回だけ', async () => {
    const f = reply(structuredClone(BUILTIN_CATALOG))
    await loadRelationCatalog(f)
    await loadRelationCatalog(f)
    expect(f).toHaveBeenCalledTimes(1)
    expect(f).toHaveBeenCalledWith('/api/relations')
  })
})

describe('欠けた返り・重複・読み直し', () => {
  const clone = () => structuredClone(BUILTIN_CATALOG) as unknown as { relations: Record<string, unknown>[]; predicates: Record<string, unknown>[] }
  const broken: Record<string, () => unknown> = {
    'shapes が無い': () => {
      const c = clone()
      delete c.predicates[0].shapes
      return c
    },
    '述語が null': () => ({ ...clone(), predicates: [null] }),
    '関係の欄の型違い': () => {
      const c = clone()
      c.relations[0].symmetric_pair = 'yes'
      return c
    },
    'when の形違い': () => {
      const c = clone()
      c.relations[0].predicates = [{ name: 'x', iri: 'y', when: ['a'] }]
      return c
    },
    'roles の形違い': () => {
      const c = clone()
      c.predicates[1].roles = { from: 'material' }
      return c
    },
    'label が欠ける': () => {
      const c = clone()
      c.relations[0].label = { ja: 'a' }
      return c
    },
  }
  for (const [name, make] of Object.entries(broken)) {
    it(`${name}: 組み込みのまま（読み直しても同じ）`, async () => {
      const f = reply(make())
      await loadRelationCatalog(f, 0)
      expect(f).toHaveBeenCalledTimes(2)
      expect(getCatalog()).toBe(BUILTIN)
      expect(isCatalogShape(make())).toBe(false)
    })
  }

  it('同名が 2 つ返ったら後のを捨てる', () => {
    const second = { ...EXTRA.relations[0], label: { ja: '二つ目', en: 'second' } }
    const m = mergeCatalog(BUILTIN, { relations: [...EXTRA.relations, second], predicates: [] })
    expect(m.relations.filter((r) => r.name === 'inhibits')).toHaveLength(1)
    expect(m.relations.at(-1)?.label.ja).toBe('妨げる')
  })

  it('安全網: 形を通っても関数が落ちる目録は試し呼びで例外になる', () => {
    const bad = { relations: BUILTIN.relations, predicates: [{ ...BUILTIN.predicates[0], shapes: undefined }] } as unknown as typeof BUILTIN
    expect(() => exerciseCatalog(bad)).toThrow()
    expect(() => exerciseCatalog(BUILTIN)).not.toThrow()
  })

  it('1 回目が失敗しても 2 回目で重なる', async () => {
    const ok = { relations: [...BUILTIN_CATALOG.relations, ...EXTRA.relations], predicates: [...BUILTIN_CATALOG.predicates, ...EXTRA.predicates] }
    const f = vi.fn().mockRejectedValueOnce(new Error('starting')).mockResolvedValue({ ok: true, json: async () => ok }) as unknown as typeof fetch
    await loadRelationCatalog(f, 0)
    expect(f).toHaveBeenCalledTimes(2)
    expect(isRelationName('inhibits')).toBe(true)
  })
})

describe('mergeCatalog・isCatalogShape', () => {
  it('同じ名前は API の値で置き換え、無い名前は後ろに足す', () => {
    const changed = { ...BUILTIN_CATALOG.relations[0], label: { ja: '別名', en: 'alt' } }
    const m = mergeCatalog(BUILTIN, { relations: [changed, ...EXTRA.relations], predicates: [] })
    expect(m.relations[0].label.ja).toBe('別名')
    expect(m.relations.at(-1)?.name).toBe('inhibits')
    expect(m.predicates).toHaveLength(15)
  })
  it('形の検査', () => {
    expect(isCatalogShape(BUILTIN_CATALOG)).toBe(true)
    expect(isCatalogShape(null)).toBe(false)
    expect(isCatalogShape({ relations: [], predicates: [{}] })).toBe(false)
    expect(isCatalogShape({ relations: [], predicates: [] })).toBe(true)
  })
})
