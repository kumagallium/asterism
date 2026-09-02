import { describe, expect, it } from 'vitest'
import type { Alignment } from './crosswalkApi'
import type { DatasetRules, RuleMap } from './galleryApi'
import type { GroundCandidate } from './groundingApi'
import { composeVocabGraph, datasetApiId } from './vocabGraph'

/** 地図の約束: 1 データセット分は ⑤ と同じ判定で組まれ、標準語彙の節は語 IRI で
 *  1 つに**合流**し、確定（使用）と候補は混ざらず、両端の解けない対応は描かない。 */

const NS = 'https://asterism.invalid/datasets/pt/ontology#'
const XNS = 'https://asterism.invalid/datasets/xrd/ontology#'

const rmap = (ns: string, id: string, over: Partial<RuleMap> = {}): RuleMap => ({
  id,
  subject: { template: `r:${id}/{k}`, classes: [`p:${id}`], class_iris: [`${ns}${id}`] },
  properties: [],
  ...over,
})
const rules = (maps: RuleMap[], labels: Record<string, string> = {}): DatasetRules => ({
  maps,
  prefixes: {},
  warnings: [],
  labels,
})
const cand = (over: Partial<GroundCandidate>): GroundCandidate => ({
  iri: 'https://schema.org/name',
  curie: 'schema:name',
  prefix: 'schema:',
  name: 'name',
  kind: 'property',
  label: 'name',
  vocab_title: 'schema.org',
  domain: 'generic',
  score: 100,
  match: 'exact',
  ...over,
})
const WORDS = {
  more: (n: number) => `…ほか ${n} 項目`,
  count: (n: number) => `${n}件`,
  aligned: '対応',
}

const pt = () =>
  rules([
    rmap(NS, 'record', {
      properties: [
        { predicate: 'p:name', predicate_iri: `${NS}name`, reference: 'name' },
        { predicate: 'p:mass', predicate_iri: `${NS}mass`, reference: 'mass', label: '原子質量' },
      ],
    }),
    rmap(NS, 'symbol', {
      properties: [
        { predicate: 'p:symbol', predicate_iri: `${NS}symbol`, reference: 'symbol' },
        { predicate: 'p:hasRecord', predicate_iri: `${NS}hasRecord`, template: 'r:record/{k}' },
      ],
    }),
  ])

const xrd = () =>
  rules([
    rmap(XNS, 'card', {
      properties: [
        { predicate: 'x:name', predicate_iri: `${XNS}name`, reference: 'Name' },
      ],
    }),
    rmap(XNS, 'peak', {
      properties: [
        // 既知名前空間（dcterms）の述語 = 「使っている」
        {
          predicate: 'dcterms:isPartOf',
          predicate_iri: 'http://purl.org/dc/terms/isPartOf',
          template: 'r:card/{k}',
          parent_map: 'card',
        },
        // 配管（rdfs:label）は使用の線にしない
        {
          predicate: 'rdfs:label',
          predicate_iri: 'http://www.w3.org/2000/01/rdf-schema#label',
          reference: 'hkl',
        },
      ],
    }),
  ])

describe('composeVocabGraph', () => {
  it('データセットごとの種類と中のつながりを ⑤ と同じ判定で組む', () => {
    const shape = composeVocabGraph({
      datasets: [{ id: 'pt', name: '元素表', rules: pt() }],
      classCounts: { [`${NS}record`]: 238 },
      words: WORDS,
    })
    const record = shape.nodes.find((n) => n.id === 'pt::record')!
    expect(record.label).toContain('238件')
    expect(record.cluster).toBe('pt')
    // hasRecord は行き先が種類そのもの → 項目ではなく線
    const links = shape.edges.filter((e) => e.kind === 'link')
    expect(links).toEqual([
      expect.objectContaining({ from: 'pt::symbol', to: 'pt::record' }),
    ])
    const symbol = shape.nodes.find((n) => n.id === 'pt::symbol')!
    expect(symbol.fields!.map((f) => f.name)).toEqual(['symbol'])
  })

  it('既知名前空間の述語は「使っている」、配管は描かない', () => {
    const shape = composeVocabGraph({
      datasets: [{ id: 'x', name: 'XRD', rules: xrd() }],
      words: WORDS,
    })
    const used = shape.edges.filter((e) => e.kind === 'used')
    expect(used).toEqual([
      expect.objectContaining({ to: 'http://purl.org/dc/terms/isPartOf' }),
    ])
    expect(shape.nodes.some((n) => n.id.includes('rdf-schema'))).toBe(false)
    expect(shape.stats.used).toBe(1)
  })

  it('接地の候補は自前の語だけ・同じ標準語には複数データセットの線が合流する', () => {
    const shape = composeVocabGraph({
      datasets: [
        { id: 'pt', name: '元素表', rules: pt() },
        { id: 'x', name: 'XRD', rules: xrd() },
      ],
      candidates: { name: [cand({})], Name: [cand({})] },
      words: WORDS,
    })
    const cands = shape.edges.filter((e) => e.kind === 'candidate')
    expect(cands.map((e) => [e.from, e.to])).toEqual([
      ['pt::record', 'https://schema.org/name'],
      ['x::card', 'https://schema.org/name'],
    ])
    // 標準語の節は 1 つに合流している（地図の存在理由）
    expect(shape.nodes.filter((n) => n.id === 'https://schema.org/name')).toHaveLength(1)
    expect(shape.nodes.find((n) => n.id === 'https://schema.org/name')!.vocab).toBe('schema.org')
  })

  it('対応は両端が解決できる事実だけ・両向きで描く', () => {
    const alignments: Alignment[] = [
      {
        alignment_iri: 'a1',
        source: `${XNS}name`,
        target: 'https://schema.org/name',
        relation: 'equivalentProperty',
        from_perspective: 'XRD',
        to_perspective: 'Schema.org',
        at: '',
      },
      {
        alignment_iri: 'a2',
        source: 'https://gone.example/ontology#x',
        target: 'https://schema.org/url',
        relation: 'equivalentProperty',
        from_perspective: 'gone',
        to_perspective: 'Schema.org',
        at: '',
      },
    ]
    const shape = composeVocabGraph({
      datasets: [{ id: 'x', name: 'XRD', rules: xrd() }],
      candidates: {},
      alignments,
      words: WORDS,
    })
    const al = shape.edges.filter((e) => e.kind === 'alignment')
    // a1: source は card の述語 → card が足場。target は既知標準 → 節を作って解決。
    expect(al).toEqual([
      expect.objectContaining({ from: 'x::card', to: 'https://schema.org/name', both: true }),
    ])
    // a2 は source が解決できない（消えたデータセット）→ 描かない
    expect(shape.stats.alignments).toBe(1)
  })

  it('項目は上限で畳み「…ほか N 項目」を足す', () => {
    const many = rules([
      rmap(NS, 'record', {
        properties: Array.from({ length: 9 }, (_, i) => ({
          predicate: `p:c${i}`,
          predicate_iri: `${NS}c${i}`,
          reference: `c${i}`,
        })),
      }),
    ])
    const shape = composeVocabGraph({
      datasets: [{ id: 'pt', name: '元素表', rules: many }],
      maxFields: 6,
      words: WORDS,
    })
    const fields = shape.nodes[0].fields!
    expect(fields).toHaveLength(7)
    expect(fields[6].name).toBe('…ほか 3 項目')
    expect(shape.stats.items).toBe(9)
  })
})

describe('datasetApiId', () => {
  it('カタログの表示用 id ではなく登録 id を返す', () => {
    // ⭐これを取り違えて `live-…` を API に投げていたため、地図は出荷後ずっと
    // 空だった（404 → catch → 対象 0 件 → 節が消える）。
    expect(datasetApiId({ id: 'live-starrydata-curves-13f12e94', live: { meta: { id: 'starrydata-curves-13f12e94' } } }))
      .toBe('starrydata-curves-13f12e94')
    // live が無い場合も表示用の接頭辞は落とす（API に live- は通らない）
    expect(datasetApiId({ id: 'live-xrd-1' })).toBe('xrd-1')
    // 既に登録 id ならそのまま
    expect(datasetApiId({ id: 'xrd-1' })).toBe('xrd-1')
  })
})
