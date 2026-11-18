import { describe, expect, it } from 'vitest'
import { EMPTY_SKY } from '../../sky/skyApi'
import { buildMapGraph, type MapEdgeAttrs, type MapNodeAttrs } from '../map/mapModel'
import { A, B, C, D, ev, ledger, row } from './fixtures'
import { ledgerDrafts, ledgerNodeId, parseLedgerNode } from './ledgerDrafts'
import { DEFAULT_FILTER } from './ledgerView'

const L = [
  ledger('src-a', [
    row({ from: B, to: A, rel: 'makes', evidence: ev(2), confirmed: true }),
    row({ from: A, to: C, rel: 'has_property' }),
    row({ from: A, to: D, rel: 'sub', state: 'dropped', rejected: true, reason: 'rejected' }),
    row({ from: C, to: D, state: 'none', rel: null }),
  ]),
]

describe('ledgerDrafts', () => {
  it('取り込み元ごとに 1 つ。draft_id は ledger:<source>。違うとした行・候補は既定で出ない', () => {
    const d = ledgerDrafts(L, DEFAULT_FILTER)
    expect(d).toHaveLength(1)
    expect(d[0].draft_id).toBe('ledger:src-a')
    expect(d[0].relations.map((r) => r.rel).sort()).toEqual(['has_property', 'makes'])
    expect(d[0].concepts.map((c) => c.key).sort()).toEqual(['t-a', 't-b', 't-c'])
  })
  it('確認済みは solid・未確認は thin。語は確認済みの関係が 1 本でもあれば確認済み', () => {
    const d = ledgerDrafts(L, DEFAULT_FILTER)[0]
    expect(d.relations.find((r) => r.rel === 'makes')).toMatchObject({ state: 'solid', confirmed: true })
    expect(d.relations.find((r) => r.rel === 'has_property')).toMatchObject({ state: 'thin', confirmed: false })
    const conf = Object.fromEntries(d.concepts.map((c) => [c.key, (c as { confirmed?: boolean }).confirmed]))
    expect(conf).toEqual({ 't-a': true, 't-b': true, 't-c': false })
  })
  it('候補は「候補も出す」がオンのときだけ、名前の無い線で', () => {
    const on = ledgerDrafts(L, { ...DEFAULT_FILTER, candidates: true })[0]
    const c = on.relations.find((r) => r.rel === 'candidate')
    expect(c).toMatchObject({ from: 't-c', to: 't-d', candidate: true })
    expect(on.concepts.map((x) => x.key)).toContain('t-d')
  })
  it('絞り込みが図に効く（取り込み元・状態）。何も残らない取り込み元は作らない', () => {
    expect(ledgerDrafts(L, { ...DEFAULT_FILTER, source: 'other' })).toEqual([])
    const only = ledgerDrafts(L, { ...DEFAULT_FILTER, status: 'confirmed' })[0]
    expect(only.relations.map((r) => r.rel)).toEqual(['makes'])
    expect(ledgerDrafts(L, { ...DEFAULT_FILTER, status: 'rejected' })).toEqual([])
  })
  it('点の id の往復', () => {
    const id = ledgerNodeId('src-a', 't-abc')
    expect(id).toBe('draft|ledger:src-a|t-abc')
    expect(parseLedgerNode(id)).toEqual({ source: 'src-a', termId: 't-abc' })
    expect(parseLedgerNode('draft|d-1|key')).toBeNull()
    expect(parseLedgerNode('t|urn:x')).toBeNull()
    expect(parseLedgerNode(null)).toBeNull()
  })
})

describe('図（mapModel）への写り方', () => {
  const g = buildMapGraph(EMPTY_SKY, ledgerDrafts(L, { ...DEFAULT_FILTER, candidates: true }), [], 'ja')
  const node = (k: string) => g.getNodeAttributes(ledgerNodeId('src-a', k)) as unknown as MapNodeAttrs
  it('役割の 4 色: 材料 m・手法 t・物性 p・概念 c', () => {
    expect(node('t-a').tone).toBe('m')
    expect(node('t-b').tone).toBe('t')
    expect(node('t-c').tone).toBe('p')
    expect(node('t-d').tone).toBe('c')
  })
  it('未確認の語は unconfirmed・確認済みは無い。「提案」の印（proposed）は付かない', () => {
    expect(node('t-a').unconfirmed).toBeUndefined()
    expect(node('t-c').unconfirmed).toBe(true)
    expect(node('t-c').proposed).toBeUndefined()
    expect(node('t-a').ledger).toBe(true)
  })
  it('線: 名前つき（確認済みは濃く・未確認は薄く）・候補は名前なしの薄い線', () => {
    const edges = g.edges().map((e) => g.getEdgeAttributes(e) as unknown as MapEdgeAttrs)
    expect(edges.find((e) => e.relation === 'makes')).toMatchObject({ label: '作る', ledger: true })
    expect(edges.find((e) => e.relation === 'makes')?.thin).toBeUndefined()
    expect(edges.find((e) => e.relation === 'has_property')).toMatchObject({ label: '物性', thin: true, unconfirmed: true })
    const cand = edges.find((e) => e.candidate)
    expect(cand).toMatchObject({ thin: true, unconfirmed: true })
    expect(cand?.label).toBeUndefined()
  })
  it('ストアの用語にも 4 色が付く（項目＝物性・それ以外＝概念）', () => {
    const sky = {
      ...EMPTY_SKY,
      terms: [
        { iri: 'urn:t:p', slug: 'p', label: 'ぴ', label_en: 'p', kind: 'property' as const, members: [], standard: null, answering: 0, questions: [] },
        { iri: 'urn:t:c', slug: 'c', label: 'し', label_en: 'c', kind: 'class' as const, members: [], standard: null, answering: 0, questions: [] },
      ],
    }
    const s = buildMapGraph(sky, [], [], 'ja')
    expect(s.getNodeAttribute('t|urn:t:p', 'tone')).toBe('p')
    expect(s.getNodeAttribute('t|urn:t:c', 'tone')).toBe('c')
  })
  it('台帳の下書きは本物の下書きの描き方を通らない（ledger は別の枝）', () => {
    expect(g.nodes().every((n) => n.startsWith('draft|ledger:src-a|'))).toBe(true)
  })
})
