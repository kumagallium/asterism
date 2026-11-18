// 置いた語（ストアの用語）の点へのまとめ・線の張り替え（PR 14e-1b・契約 §4）。
import type Graph from 'graphology'
import { describe, expect, it } from 'vitest'
import { EMPTY_SKY } from '../../sky/skyApi'
import type { DraftSky, SkyData, SkyTerm } from '../../sky/skyApi'
import { draftNodeId, termId } from '../../sky/skyLayout'
import { buildMapGraph, layoutMap, mapSignature, type MapEdgeAttrs, type MapNodeAttrs } from './mapModel'

const IRI = (s: string) => `urn:t:${s}`
const sterm = (s: string, over: Partial<SkyTerm> = {}): SkyTerm => ({ iri: IRI(s), slug: s, label: `語${s}`, label_en: s, kind: 'class', members: [], standard: null, answering: 0, questions: [], ...over })
type C = DraftSky['concepts'][number] & { role?: string | null; confirmed?: boolean; termIri?: string }
const concept = (key: string, over: Partial<C> = {}): C => ({ key, label: `台${key}`, kind: 'class', use: 'new', state: 'solid', role: 'concept', confirmed: false, ...over })
const rel = (from: string, to: string, r: string, confirmed = false) => ({ from, to, rel: r, state: confirmed ? 'solid' : 'thin', confirmed, evidence: 1 }) as DraftSky['relations'][number]
const ledger = (concepts: C[], relations: DraftSky['relations']): DraftSky => ({ draft_id: 'ledger:s', theme_label: '台帳', concepts, relations, questions: [] })
const attrs = (g: Graph, id: string) => g.getNodeAttributes(id) as unknown as MapNodeAttrs
const edgesOf = (g: Graph) => g.edges().map((e) => ({ ...(g.getEdgeAttributes(e) as unknown as MapEdgeAttrs), ends: g.extremities(e) }))

describe('置いた語はストアの用語の点 1 つにまとまる', () => {
  const sky: SkyData = { ...EMPTY_SKY, terms: [sterm('a', { role: 'material' }), sterm('b', { role: 'method' })] }

  it('term_iri がある語は台帳の点を作らず、線の端をストアの用語の点に張り替える', () => {
    const d = ledger([concept('a', { termIri: IRI('a'), confirmed: true }), concept('b', { termIri: IRI('b'), confirmed: true }), concept('c')], [rel('a', 'b', 'sub', true), rel('c', 'a', 'makes')])
    const g = buildMapGraph(sky, [d], [], 'ja')
    expect(g.hasNode(draftNodeId('ledger:s', 'a'))).toBe(false)
    expect(g.hasNode(draftNodeId('ledger:s', 'b'))).toBe(false)
    expect(g.hasNode(draftNodeId('ledger:s', 'c'))).toBe(true)
    // 一種（sky.edges に載らない関係）は台帳の線をストアの用語の点の間に張る
    const sub = edgesOf(g).find((e) => e.relation === 'sub')!
    expect([...sub.ends].sort()).toEqual([termId(IRI('a')), termId(IRI('b'))].sort())
    expect(sub).toMatchObject({ ledger: true, directed: true })
    expect(sub.thin).toBeUndefined() // 確認済みの濃い線
    // 台帳の点 c からストアの用語の点 a へ
    const mk = edgesOf(g).find((e) => e.relation === 'makes')!
    expect(mk.ends).toContain(termId(IRI('a')))
    expect(mk.ends).toContain(draftNodeId('ledger:s', 'c'))
  })

  it('ストアの用語の点が図に無い（sky の読み込み前・手で消した後）ときは、今までどおり台帳の点で描く', () => {
    const d = ledger([concept('a', { termIri: IRI('zzz'), confirmed: true }), concept('b', { confirmed: true })], [rel('a', 'b', 'sub', true)])
    const g = buildMapGraph(sky, [d], [], 'ja')
    expect(g.hasNode(draftNodeId('ledger:s', 'a'))).toBe(true)
    expect(g.hasNode(termId(IRI('zzz')))).toBe(false)
    expect(edgesOf(g).filter((e) => e.relation === 'sub')).toHaveLength(1)
    expect(mapSignature(buildMapGraph(EMPTY_SKY, [d], [], 'ja'))).toContain('draft|ledger:s|a')
  })

  it('sky.edges に同じ関係の線があれば 1 本（確認済みの濃い線・名前・矢じりは sky 側）', () => {
    const withEdge: SkyData = { ...sky, edges: [{ from: IRI('a'), to: IRI('b'), relation: 'lowers', reason: '' }] as SkyData['edges'] }
    const d = ledger([concept('a', { termIri: IRI('a'), confirmed: true }), concept('b', { termIri: IRI('b'), confirmed: true })], [rel('a', 'b', 'lowers', true)])
    const g = buildMapGraph(withEdge, [d], [], 'ja')
    const es = edgesOf(g).filter((e) => e.kind === 'concept' || e.kind === 'draft')
    expect(es).toHaveLength(1)
    expect(es[0]).toMatchObject({ kind: 'concept', relation: 'lowers', label: '下げる', directed: true })
  })

  it('向きの無い関係（競合する）は、sky 側が逆向きでも 1 本', () => {
    const withEdge: SkyData = { ...sky, edges: [{ from: IRI('b'), to: IRI('a'), relation: 'competesWith', reason: '' }] as SkyData['edges'] }
    const d = ledger([concept('a', { termIri: IRI('a'), confirmed: true }), concept('b', { termIri: IRI('b'), confirmed: true })], [rel('a', 'b', 'competes', true)])
    const es = edgesOf(buildMapGraph(withEdge, [d], [], 'ja')).filter((e) => e.kind === 'concept' || e.kind === 'draft')
    expect(es).toHaveLength(1)
    expect(es[0].kind).toBe('concept')
  })

  it('点は 1 つ: 同じ語が台帳の点とストアの点で 2 つにならない（点の数＝ストアの用語＋台帳だけの語）', () => {
    const d = ledger([concept('a', { termIri: IRI('a'), confirmed: true }), concept('b', { termIri: IRI('b'), confirmed: true }), concept('c')], [rel('a', 'b', 'sub', true), rel('c', 'b', 'makes')])
    const g = buildMapGraph(sky, [d], [], 'ja')
    expect(g.nodes().filter((n) => attrs(g, n).type === 'term')).toHaveLength(2)
    expect(g.nodes().filter((n) => attrs(g, n).type === 'draft')).toHaveLength(1)
  })
})

describe('ストアの用語の色は役割の 4 色（sky の用語の role から）', () => {
  it('role があれば役割の色・無ければ今までの 2 色（物性＝p・それ以外＝c）', () => {
    const sky: SkyData = { ...EMPTY_SKY, terms: [sterm('m', { role: 'material' }), sterm('t', { role: 'method' }), sterm('c', { role: 'concept' }), sterm('p', { kind: 'property', role: null }), sterm('n')] }
    const g = buildMapGraph(sky, [], [], 'ja')
    const tone = (s: string) => attrs(g, termId(IRI(s))).tone
    expect([tone('m'), tone('t'), tone('c'), tone('p'), tone('n')]).toEqual(['m', 't', 'c', 'p', 'c'])
  })
})

describe('配置: 置いた語は台帳の語と同じ群（離れて散らばらない）', () => {
  it('ストアの用語の点が台帳の群に入り、データセット側の点だけが横に寄る。毎回同じ', () => {
    const sky: SkyData = {
      ...EMPTY_SKY,
      terms: [sterm('a'), sterm('b'), sterm('free')],
      datasets: [{ id: 'w', label: '世界', created_at: '2026-01-01', kinds: [{ iri: 'urn:k:x', label: '国', count: 3, links: [], standard: null }] }],
    }
    const d = ledger([concept('a', { termIri: IRI('a'), confirmed: true }), concept('b', { termIri: IRI('b'), confirmed: true }), concept('c')], [rel('a', 'b', 'sub', true), rel('c', 'b', 'makes')])
    const g = buildMapGraph(sky, [d], [], 'ja')
    expect(attrs(g, termId(IRI('a'))).placed).toBe(true)
    expect(attrs(g, termId(IRI('free'))).placed).toBeUndefined()
    layoutMap(g)
    const group = g.nodes().filter((n) => attrs(g, n).placed || attrs(g, n).ledger)
    const rest = g.nodes().filter((n) => !group.includes(n))
    expect(group).toHaveLength(3)
    const maxGroupX = Math.max(...group.map((n) => attrs(g, n).x))
    for (const n of rest) expect(attrs(g, n).x).toBeGreaterThan(maxGroupX)
    const g2 = buildMapGraph(sky, [d], [], 'ja')
    layoutMap(g2)
    expect(g2.mapNodes((n, a) => [n, a.x, a.y])).toEqual(g.mapNodes((n, a) => [n, a.x, a.y]))
  })
})
