import { describe, expect, it } from 'vitest'
import { EMPTY_SKY, type DraftSky, type SkyData, type SkyDataset, type SkyTerm } from '../../sky/skyApi'
import { draftNodeId, hubNodeId, kindId, termId } from '../../sky/skyLayout'
import { localSky, type LocalFocus } from './neighborhood'

const NS = 'urn:t/'
const kind = (ds: string, n: string, links: string[] = []) => ({ iri: `${NS}${ds}/${n}`, label: n, count: 10, links: links.map((l) => `${NS}${ds}/${l}`), standard: null })
const ds = (id: string, names: string[]): SkyDataset => ({ id, label: id, created_at: '2026-01-01T00:00:00Z', kinds: names.map((n) => kind(id, n, names[0] === n ? [] : [names[0]])) })
const term = (slug: string, members: [string, string][] = [], std = false): SkyTerm => ({
  iri: `${NS}${slug}`,
  slug,
  label: slug,
  label_en: slug,
  kind: 'class',
  members: members.map(([d, n]) => ({ iri: `${NS}${d}/${n}`, dataset_id: d, relation: 'equivalentClass' as const })),
  standard: std ? { iri: 'urn:std/S', relation: 'x' } : null,
  answering: 0,
  questions: [],
})
const sky: SkyData = {
  ...EMPTY_SKY,
  datasets: [ds('d1', ['A', 'B']), ds('d2', ['C', 'D']), ds('d3', ['E'])],
  terms: [term('a', [['d1', 'A']], true), term('b', [['d1', 'B']]), term('c', [['d2', 'C']]), term('far', [['d3', 'E']]), term('lone')],
  standards: [{ iri: 'urn:std/S', label: 'S', prefix: 'std' }],
  hubs: [{ id: 'h1', label: 'H', members: [`${NS}d1/A`, `${NS}d3/E`] }],
  edges: [{ from: `${NS}a`, to: `${NS}b`, relation: 'lowers', reason: null }],
  questions: [{ tool_name: 'q', title: 'q', theme_id: null, theme_label: '', answering: 1, total_datasets: 3, needs: [], shape: '' } as never],
}
const draft = (id: string, existing?: string): DraftSky => ({
  draft_id: id,
  theme_label: 'T',
  concepts: [
    { key: 'n1', label: 'new', kind: 'class', use: 'new' },
    ...(existing ? [{ key: 'e1', label: 'ex', kind: 'class' as const, use: `existing:${existing}` }] : []),
  ],
  relations: [],
  questions: [],
})
const focus = (over: Partial<LocalFocus> = {}): LocalFocus => ({ termIris: new Set(), draftIds: new Set(), selectedId: null, ...over })
const slugs = (r: ReturnType<typeof localSky>) => r.sky.terms.map((t) => t.slug)

describe('localSky', () => {
  it('核が空なら empty', () => {
    const r = localSky(sky, [draft('x')], [], focus())
    expect(r.empty).toBe(true)
    expect(r.drafts).toEqual([])
    expect(r.more).toBe(0)
  })

  it('この話の用語＋知識の線の 1 ホップ。遠い用語は入らない', () => {
    const r = localSky(sky, [], [], focus({ termIris: new Set([`${NS}a`]) }))
    expect(slugs(r)).toEqual(['a', 'b'])
    expect(r.empty).toBe(false)
    expect(r.sky.edges).toHaveLength(1)
    expect(r.sky.questions).toEqual([])
  })

  it('データの種類は結ばれた用語が入っているものだけ。入っていないデータセットは落ちる', () => {
    const r = localSky(sky, [], [], focus({ termIris: new Set([`${NS}a`]) }))
    expect(r.sky.datasets.map((d) => d.id)).toEqual(['d1'])
    expect(r.sky.datasets[0].kinds.map((k) => k.label)).toEqual(['A', 'B'])
    expect(r.sky.standards).toHaveLength(1)
    expect(r.sky.hubs[0].members).toEqual([`${NS}d1/A`])
  })

  it('提案は focus.draftIds のものだけ。existing の相手が核に入る', () => {
    const r = localSky(sky, [draft('x', 'c'), draft('y', 'far')], [], focus({ draftIds: new Set(['x']) }))
    expect(r.drafts.map((d) => d.draft_id)).toEqual(['x'])
    expect(slugs(r)).toEqual(['c'])
    expect(r.empty).toBe(false)
  })

  it('提案の札だけでも empty でない', () => {
    expect(localSky(sky, [draft('x')], [], focus({ draftIds: new Set(['x']) })).empty).toBe(false)
  })

  it('選んだ用語・種類・提案・視点', () => {
    expect(slugs(localSky(sky, [], [], focus({ selectedId: termId(`${NS}c`) })))).toEqual(['c'])
    expect(slugs(localSky(sky, [], [], focus({ selectedId: kindId('d2', `${NS}d2/D`) })))).toEqual([])
    const k = localSky(sky, [], [], focus({ selectedId: kindId('d1', `${NS}d1/A`) }))
    expect(slugs(k)).toEqual(['a'])
    expect(k.sky.datasets[0].kinds.map((x) => x.label)).toEqual(['A'])
    const d = localSky(sky, [draft('y')], [], focus({ selectedId: draftNodeId('y', 'n1') }))
    expect(d.drafts.map((x) => x.draft_id)).toEqual(['y'])
    const h = localSky(sky, [], [], focus({ selectedId: hubNodeId('h1') }))
    expect(slugs(h).sort()).toEqual(['a', 'far'])
  })

  it('cap を超えたら核を優先して切り、more に残りの数', () => {
    const many: SkyData = { ...sky, terms: [...sky.terms, ...Array.from({ length: 10 }, (_, i) => term(`n${i}`))] }
    const edges = many.terms.filter((t) => t.slug.startsWith('n')).map((t) => ({ from: `${NS}a`, to: t.iri, relation: 'raises' as const, reason: null }))
    const r = localSky({ ...many, edges }, [], [], focus({ termIris: new Set([`${NS}a`, `${NS}c`]) }), 4)
    expect(slugs(r).slice(0, 2)).toEqual(['a', 'c'])
    expect(r.sky.terms).toHaveLength(4)
    expect(r.more).toBe(8)
    expect(r.sky.edges?.every((e) => slugs(r).includes(e.from.slice(NS.length)) && slugs(r).includes(e.to.slice(NS.length)))).toBe(true)
  })

  it('決定論: 同じ入力で同じ結果', () => {
    const f = focus({ termIris: new Set([`${NS}c`, `${NS}a`]) })
    expect(localSky(sky, [], [], f)).toEqual(localSky(sky, [], [], f))
  })

  it('未公開のデータセットも結ばれた種類だけに絞る', () => {
    const p: SkyDataset = { ...ds('p1', ['P']), proposed: true }
    const withP: SkyData = { ...sky, terms: [...sky.terms, term('pt', [['p1', 'P']])] }
    const r = localSky(withP, [], [p, ds('p2', ['Q'])], focus({ termIris: new Set([`${NS}pt`]) }))
    expect(r.proposedDatasets.map((d) => d.id)).toEqual(['p1'])
  })
})
