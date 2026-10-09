import { describe, expect, it } from 'vitest'
import type { Alignment } from './crosswalkApi'
import type { DatasetRules, RuleMap } from './galleryApi'
import { SV_NS } from './lineChoice'
import { place } from './VocabMap'
import type { SharedTerm } from './vocabApi'
import { composeVocabGraph, pickEndOfVocabNode } from './vocabGraph'

/** 「詳しく」の地図に足した共有のことばの帯: 標準の帯の上・データセット 0 件でも描く・
 *  上位への線（upper）は向きあり（≡ は両向き）・配置は決定論。 */

const NS = 'https://example.org/xrd#'
const STD = 'https://schema.org/Thing'
const sv = (slug: string) => `${SV_NS}${slug}`
const rmap = (id: string): RuleMap => ({
  id,
  subject: { template: `x:${id}/{k}`, classes: [`x:${id}`], class_iris: [`${NS}${id}`] },
  properties: [{ predicate: `x:m`, predicate_iri: `${NS}mass`, reference: 'mass' }],
})
const rules = (ids: string[]): DatasetRules => ({
  maps: ids.map(rmap),
  prefixes: {},
  warnings: [],
  labels: {},
})
const term = (slug: string, over: Partial<SharedTerm> = {}): SharedTerm => ({
  iri: sv(slug),
  slug,
  kind: 'class',
  label: slug,
  label_en: null,
  comment: null,
  created_at: '2026-10-09T00:00:00Z',
  wired: true,
  answering_datasets: 1,
  narrower: [],
  standards: [],
  cqs: [],
  ...over,
})
const al = (source: string, target: string, relation: string): Alignment => ({
  alignment_iri: `a:${source}>${target}`,
  source,
  target,
  relation,
  from_perspective: '',
  to_perspective: '',
  at: '',
})
const WORDS = {
  more: (n: number) => `ほか ${n}`,
  count: (n: number) => `${n}件`,
  aligned: '対応',
  shared: (kids: number, orphan: boolean) => (orphan ? '孤立' : `子 ${kids}`),
}
const datasets = [{ id: 'live-x1', name: 'XRD', rules: rules(['Record', 'Peak']) }]

describe('composeVocabGraph — 共有のことば', () => {
  it('語はすべて節になり（孤立も）、種類の箱 → 共有語は向きのある upper', () => {
    const shape = composeVocabGraph({
      datasets,
      classCountsByDataset: { 'live-x1': { [`${NS}Record`]: 1000, [`${NS}Peak`]: 40 } },
      sharedTerms: [term('dp'), term('lonely', { wired: false })],
      alignments: [al(`${NS}Record`, sv('dp'), 'subClassOf'), al(sv('dp'), STD, 'equivalentClass')],
      words: WORDS,
    })
    const dp = shape.nodes.find((n) => n.id === sv('dp'))!
    expect(dp.shared).toMatchObject({ slug: 'dp', orphan: false, kids: 1, count: 1000 })
    expect(shape.nodes.find((n) => n.id === sv('lonely'))!.shared!.orphan).toBe(true)
    expect(shape.stats.shared).toBe(2)
    const up = shape.edges.find((e) => e.from === 'live-x1::Record' && e.to === sv('dp'))!
    expect(up.kind).toBe('upper')
    expect(up.both).toBeUndefined() // ⊂ は向きあり
    const eq = shape.edges.find((e) => e.from === sv('dp') && e.to === STD)!
    expect(eq.both).toBe(true) // ≡ は両向き
    expect(shape.nodes.find((n) => n.id === STD)).toBeTruthy() // 行き先の標準の語も節に出る
  })

  it('データセット 0 件でも共有のことばの節は出る', () => {
    const shape = composeVocabGraph({ datasets: [], sharedTerms: [term('a')], words: WORDS })
    expect(shape.nodes.map((n) => n.id)).toEqual([sv('a')])
    expect(shape.clusters).toEqual([])
  })

  it('データセットの種類どうしの ⊂ は 向きなしの対応ではなく upper（向きあり）。≡ は対応のまま', () => {
    const base = { datasets, words: WORDS }
    const sub = composeVocabGraph({ ...base, alignments: [al(`${NS}Peak`, `${NS}Record`, 'subClassOf')] })
    expect(sub.edges.filter((e) => e.kind === 'alignment')).toHaveLength(0)
    const up = sub.edges.find((e) => e.kind === 'upper')!
    expect([up.from, up.to]).toEqual(['live-x1::Peak', 'live-x1::Record'])
    expect(up.both).toBeUndefined()
    const eq = composeVocabGraph({ ...base, alignments: [al(`${NS}Peak`, `${NS}Record`, 'equivalentClass')] })
    expect(eq.edges.find((e) => e.kind === 'alignment')!.both).toBe(true)
  })

  it('項目の語は掛かる箱の線（項目の持ち主の箱）に結ばれる', () => {
    const shape = composeVocabGraph({
      datasets,
      sharedTerms: [term('mass', { kind: 'property' })],
      alignments: [al(`${NS}mass`, sv('mass'), 'subPropertyOf')],
      words: WORDS,
    })
    const e = shape.edges.find((x) => x.to === sv('mass'))!
    expect(e.from).toBe('live-x1::Record') // 項目を最初に名乗った箱
    expect(e.relation).toBe('subPropertyOf')
  })

  it('標準の語の kind（種類／項目）を覚え、線を引く関係の絞り込みに使う', () => {
    const shape = composeVocabGraph({
      datasets: [
        {
          id: 'live-x1',
          name: 'XRD',
          rules: {
            ...rules(['Record']),
            maps: [
              {
                ...rmap('Record'),
                subject: { template: 'x', classes: ['x'], class_iris: [STD] },
                properties: [{ predicate: 'schema:name', predicate_iri: 'https://schema.org/name', reference: 'n' }],
              },
            ],
          },
        },
      ],
      words: WORDS,
    })
    expect(shape.nodes.find((n) => n.id === STD)!.termKind).toBe('class')
    expect(shape.nodes.find((n) => n.id === 'https://schema.org/name')!.termKind).toBe('property')
  })
})

describe('pickEndOfVocabNode', () => {
  const shape = composeVocabGraph({ datasets, sharedTerms: [term('dp')], words: WORDS })
  it('種類の箱は dataset・共有語は shared', () => {
    expect(pickEndOfVocabNode(shape.nodes.find((n) => n.id === 'live-x1::Record'))).toMatchObject({
      role: 'dataset',
      iri: `${NS}Record`,
      termKind: 'class',
    })
    expect(pickEndOfVocabNode(shape.nodes.find((n) => n.id === sv('dp')))).toMatchObject({
      role: 'shared',
      iri: sv('dp'),
    })
    expect(pickEndOfVocabNode(undefined)).toBeNull()
  })
})

describe('place — 共有のことばの帯は標準の帯の上', () => {
  const build = () =>
    composeVocabGraph({
      datasets,
      sharedTerms: [term('dp'), term('pk')],
      alignments: [
        al(`${NS}Record`, sv('dp'), 'subClassOf'),
        al(`${NS}Peak`, sv('pk'), 'subClassOf'),
        al(sv('dp'), STD, 'equivalentClass'),
      ],
      words: WORDS,
    })

  it('同じ入力は同じ座標', () => {
    const a = place(build())
    const b = place(build())
    expect(b.nodes.map((n) => [n.id, n.position])).toEqual(a.nodes.map((n) => [n.id, n.position]))
    expect(b.height).toBe(a.height)
  })

  it('帯は 枠 < 共有のことば < 標準のことば の順に積まれ、図の高さが標準の帯の下端', () => {
    const p = place(build())
    const y = (id: string) => p.nodes.find((n) => n.id === id)!.position.y
    const h = (id: string) => (p.nodes.find((n) => n.id === id)!.data as { height: number }).height
    const frameBottom = y('cluster:live-x1') + h('cluster:live-x1')
    expect(y('band:shared')).toBeGreaterThan(frameBottom)
    expect(y('band:standard')).toBeGreaterThanOrEqual(y('band:shared') + h('band:shared'))
    expect(p.height).toBe(y('band:standard') + h('band:standard'))
    expect(y(sv('dp'))).toBeGreaterThanOrEqual(y('band:shared'))
    expect(y(STD)).toBeGreaterThanOrEqual(y('band:standard'))
  })

  it('データセット 0 件でも帯を先頭から描く', () => {
    const p = place(composeVocabGraph({ datasets: [], sharedTerms: [term('a'), term('b')], words: WORDS }))
    expect(p.nodes.find((n) => n.id === 'band:shared')!.position.y).toBe(0)
    expect(p.nodes.some((n) => n.id === 'band:standard')).toBe(false)
    expect(p.nodes.filter((n) => n.type === 'std')).toHaveLength(2)
  })

  it('標準の帯へ降りる線は、上の共有のことばの帯の行を避けて席を通る（通り道が付く）', () => {
    const shape = composeVocabGraph({
      datasets,
      sharedTerms: [term('dp')],
      alignments: [al(`${NS}Record`, sv('dp'), 'subClassOf')],
      words: WORDS,
    })
    // 標準の語へ降りる線（共有の帯を通り抜ける）を足す。
    shape.edges.push({ from: 'live-x1::Record', to: STD, kind: 'used', label: 'x' })
    shape.nodes.push({ id: STD, label: 'Thing', tone: 'record', vocab: 'schema' })
    const p = place(shape)
    const i = shape.edges.findIndex((e) => e.to === STD)
    const sharedIdx = shape.edges.findIndex((e) => e.to === sv('dp'))
    // 標準の語（標準の帯の 1 行目）へは共有の帯の行の席を 1 つ多く通る。
    expect(p.routes[i]!.via.length).toBe(p.routes[sharedIdx]!.via.length + 1)
  })
})
