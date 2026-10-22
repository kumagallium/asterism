import { describe, expect, it } from 'vitest'
import { EMPTY_SKY } from '../../sky/skyApi'
import type { DraftSky, SkyData, SkyDataset, SkyTerm } from '../../sky/skyApi'
import { draftNodeId, hubNodeId, kindId, termId } from '../../sky/skyLayout'
import { selectionOfNode } from '../chatState'
import { buildMapGraph, datasetNodeId, kindSize, layoutMap, mapNeighborhood, mapSelection, mapSignature, relationLabel, safeResize, syncMapAttributes, termSize } from './mapModel'

const term = (slug: string, label: string, members: SkyTerm['members'] = []): SkyTerm => ({
  iri: `urn:t:${slug}`,
  slug,
  label,
  label_en: label,
  kind: 'class',
  members,
  standard: null,
  answering: 0,
  questions: [],
})

const sky: SkyData = {
  ...EMPTY_SKY,
  datasets: [
    {
      id: 'ds1',
      label: 'データ1',
      created_at: '2026-01-01',
      kinds: [
        { iri: 'urn:k:a', label: '材料', count: 100, links: [], standard: null },
        { iri: 'urn:k:b', label: '測定', count: 5, links: [], standard: null },
      ],
    },
  ],
  terms: [
    term('material', '材料', [{ iri: 'urn:k:a', dataset_id: 'ds1', relation: 'subClassOf' }]),
    term('measure', '測定', [{ iri: 'urn:k:b', dataset_id: 'ds1', relation: 'subClassOf' }]),
    term('alone', 'ひとり'),
  ],
  hubs: [{ id: 'v1', label: '視点', members: ['urn:k:a', 'urn:k:b'] }],
  edges: [{ from: 'urn:t:material', to: 'urn:t:measure', relation: 'affects', reason: null }],
}
const proposed: SkyDataset[] = [
  {
    id: 'ds2',
    label: '未公開データ',
    created_at: '2026-02-01',
    proposed: true,
    kinds: [{ iri: 'urn:k:c', label: '新種', count: 3, links: [], standard: null }],
  },
]
const draft: DraftSky = {
  draft_id: 'd1',
  theme_label: 'テーマ',
  concepts: [
    { key: 'n1', label: '新語', kind: 'class', use: 'new', state: 'thin' },
    { key: 'e1', label: '材料', kind: 'class', use: 'existing:material' },
    { key: 'e2', label: '測定', kind: 'class', use: 'existing:measure' },
  ],
  relations: [
    { from: 'n1', to: 'e1', rel: 'lowers', state: 'thin' },
    { from: 'e1', to: 'e2', rel: 'affects', state: 'thin' }, // 本物と同じ関係 → 張らない
    { from: 'e2', to: 'e1', rel: 'raises', state: 'solid' },
  ],
  questions: [],
}

describe('buildMapGraph', () => {
  const g = buildMapGraph(sky, [draft], proposed, 'ja')

  it('点の数と id', () => {
    // 種類 3（公開 2＋未公開 1）・データセットの丸 2・用語 3・視点 1・提案の札 1（existing は寄せる）
    expect(g.order).toBe(10)
    expect(g.hasNode(datasetNodeId('ds1'))).toBe(true)
    expect(g.getNodeAttribute(datasetNodeId('ds2'), 'proposed')).toBe(true)
    expect(mapSelection(g, datasetNodeId('ds1'))).toBeNull() // データセットの丸は 1 枚を持たない
    expect(g.hasNode(termId('urn:t:material'))).toBe(true)
    expect(g.hasNode(kindId('ds1', 'urn:k:a'))).toBe(true)
    expect(g.hasNode(kindId('ds2', 'urn:k:c'))).toBe(true)
    expect(g.hasNode(hubNodeId('v1'))).toBe(true)
    expect(g.hasNode(draftNodeId('d1', 'n1'))).toBe(true)
    expect(g.hasNode(draftNodeId('d1', 'e1'))).toBe(false)
  })

  it('線は二重に張らず、薄い提案は本物と同じ関係なら張らない', () => {
    const kinds: Record<string, number> = {}
    g.forEachEdge((_e, a) => (kinds[a.kind] = (kinds[a.kind] ?? 0) + 1))
    expect(kinds.member).toBe(2)
    expect(kinds.concept).toBe(1)
    expect(kinds.hub).toBe(2)
    expect(kinds.dataset).toBe(3) // データセットの丸とその種類
    // n1→材料 lowers（薄い）と 測定→材料 raises（solid）。affects の薄い線は張らない
    expect(kinds.draft).toBe(2)
    const rels: string[] = []
    g.forEachEdge((_e, a) => a.kind === 'draft' && rels.push(a.relation))
    expect(rels.sort()).toEqual(['lowers', 'raises'])
  })

  it('提案の札・未公開の種類は proposed', () => {
    expect(g.getNodeAttribute(draftNodeId('d1', 'n1'), 'proposed')).toBe(true)
    expect(g.getNodeAttribute(kindId('ds2', 'urn:k:c'), 'proposed')).toBe(true)
    expect(g.getNodeAttribute(kindId('ds1', 'urn:k:a'), 'proposed')).toBeUndefined()
  })

  it('外す提案の用語に remove が付く', () => {
    const g2 = buildMapGraph(sky, [{ ...draft, removals: { terms: ['urn:t:alone'], lines: [] } }], [], 'ja')
    expect(g2.getNodeAttribute(termId('urn:t:alone'), 'remove')).toBe(true)
  })

  it('同じ入力は同じ座標（決定論）で NaN が無い', () => {
    const a = buildMapGraph(sky, [draft], proposed, 'ja')
    const b = buildMapGraph(sky, [draft], proposed, 'ja')
    layoutMap(a)
    layoutMap(b)
    layoutMap(b) // 何度呼んでも同じ
    const dump = (g: ReturnType<typeof buildMapGraph>) => JSON.stringify(g.mapNodes((id, at) => [id, at]))
    expect(dump(a)).toBe(dump(b))
    a.forEachNode((_id, at) => {
      expect(Number.isFinite(at.x)).toBe(true)
      expect(Number.isFinite(at.y)).toBe(true)
      expect(Number.isFinite(at.size)).toBe(true)
    })
  })
})

describe('大きさ', () => {
  it('kindSize は 3〜12 で件数に単調', () => {
    const counts = [0, 1, 10, 100, 1000, 100000]
    const s = counts.map(kindSize)
    for (let i = 1; i < s.length; i++) expect(s[i]).toBeGreaterThanOrEqual(s[i - 1])
    expect(Math.min(...s)).toBeGreaterThanOrEqual(3)
    expect(Math.max(...s)).toBeLessThanOrEqual(12)
    expect(kindSize(1000)).toBeGreaterThan(kindSize(1))
  })
  it('termSize は 4〜12 で単調', () => {
    expect(termSize(0, 0)).toBe(4)
    expect(termSize(2, 2)).toBeGreaterThan(termSize(1, 0))
    expect(termSize(1000, 1000)).toBe(12)
  })
})

describe('mapNeighborhood / mapSelection', () => {
  const g = buildMapGraph(sky, [draft], proposed, 'ja')
  it('近傍は自分と相手', () => {
    const near = mapNeighborhood(g, termId('urn:t:material'))
    expect(near.has(termId('urn:t:material'))).toBe(true)
    expect(near.has(kindId('ds1', 'urn:k:a'))).toBe(true)
    expect(near.has(termId('urn:t:alone'))).toBe(false)
    expect(mapNeighborhood(g, null).size).toBe(0)
  })
  it('selectionOfNode と同じ形', () => {
    expect(mapSelection(g, termId('urn:t:material'))).toEqual(
      selectionOfNode({ type: 'term', iri: 'urn:t:material', label: '材料' } as never),
    )
    expect(mapSelection(g, kindId('ds1', 'urn:k:a'))).toEqual({ kind: 'kind', iri: 'urn:k:a', label: '材料' })
    expect(mapSelection(g, kindId('ds2', 'urn:k:c'))).toEqual({
      kind: 'kind',
      iri: 'urn:k:c',
      label: '新種',
      thin: { dataset: '未公開データ' },
    })
    expect(mapSelection(g, hubNodeId('v1'))).toEqual({ kind: 'hub', iri: 'v1', label: '視点' })
    expect(mapSelection(g, draftNodeId('d1', 'n1'))).toEqual({ kind: 'draft', iri: 'n1', draftId: 'd1', label: '新語' })
    expect(mapSelection(g, 'nope')).toBeNull()
  })
})

describe('入力の順に依らない・形が同じなら配置を使い回せる', () => {
  const coords = (g: ReturnType<typeof buildMapGraph>): string =>
    JSON.stringify(g.nodes().sort().map((id) => [id, Math.round(g.getNodeAttribute(id, 'x') * 1000), Math.round(g.getNodeAttribute(id, 'y') * 1000)]))
  it('terms・edges・datasets・kinds・drafts の順を入れ替えても座標は同じ', () => {
    const big: SkyData = {
      ...sky,
      terms: [...sky.terms, term('t4', '四'), term('t5', '五', [{ iri: 'urn:k:b', dataset_id: 'ds1', relation: 'subClassOf' }])],
      edges: [...(sky.edges ?? []), { from: 'urn:t:t4', to: 'urn:t:material', relation: 'isPartOf', reason: null }, { from: 'urn:t:t5', to: 'urn:t:t4', relation: 'lowers', reason: null }],
    }
    const a = buildMapGraph(big, [draft], proposed, 'ja')
    layoutMap(a)
    const shuffled: SkyData = {
      ...big,
      terms: [...big.terms].reverse(),
      edges: [...(big.edges ?? [])].reverse(),
      datasets: big.datasets.map((d) => ({ ...d, kinds: [...d.kinds].reverse() })).reverse(),
      hubs: [...big.hubs].reverse(),
    }
    const b = buildMapGraph(shuffled, [{ ...draft, concepts: [...draft.concepts].reverse(), relations: [...draft.relations].reverse() }], proposed, 'ja')
    layoutMap(b)
    expect(coords(a)).toBe(coords(b))
    expect(mapSignature(a)).toBe(mapSignature(b))
  })
  it('属性だけ変わった（薄い → 濃い）ときは署名が同じで、位置を保って属性を写せる', () => {
    const a = buildMapGraph(sky, [draft], proposed, 'ja')
    layoutMap(a)
    const solid: DraftSky = { ...draft, concepts: draft.concepts.map((c) => ({ ...c, state: 'solid' as const })) }
    const b = buildMapGraph(sky, [solid], proposed, 'ja')
    expect(mapSignature(b)).toBe(mapSignature(a))
    const id = draftNodeId('d1', 'n1')
    const before = [a.getNodeAttribute(id, 'x'), a.getNodeAttribute(id, 'y')]
    expect(a.getNodeAttribute(id, 'proposed')).toBe(true)
    syncMapAttributes(a, b)
    expect(a.getNodeAttribute(id, 'proposed')).toBeUndefined()
    expect([a.getNodeAttribute(id, 'x'), a.getNodeAttribute(id, 'y')]).toEqual(before)
  })
  it('safeResize は隠れている（0 幅）と例外を投げる sigma でも落ちない', () => {
    const calls: string[] = []
    const ok = { resize: () => calls.push('resize'), refresh: () => calls.push('refresh') }
    expect(safeResize(ok, 0, 100)).toBe(false)
    expect(calls).toEqual([])
    expect(safeResize(ok, 300, 200)).toBe(true)
    expect(calls).toEqual(['resize', 'refresh'])
    const bad = { resize: () => { throw new Error('Container has no width') }, refresh: () => undefined }
    expect(safeResize(bad, 300, 200)).toBe(false)
  })
})

describe('線の名前（label）', () => {
  const labels = (g: ReturnType<typeof buildMapGraph>, kind: string) =>
    g.edges().filter((e) => g.getEdgeAttribute(e, 'kind') === kind).map((e) => g.getEdgeAttribute(e, 'label'))

  it('concept の線にだけ label が付き、lang で変わる', () => {
    const ja = buildMapGraph(sky, [], [], 'ja')
    const en = buildMapGraph(sky, [], [], 'en')
    expect(labels(ja, 'concept')).toEqual(['変える'])
    expect(labels(en, 'concept')).toEqual(['changes'])
    expect(labels(ja, 'member').every((l) => l === undefined)).toBe(true)
    expect(labels(ja, 'dataset').every((l) => l === undefined)).toBe(true)
  })

  it('label は形ではない（signature は言語で変わらない）', () => {
    expect(mapSignature(buildMapGraph(sky, [], [], 'ja'))).toBe(mapSignature(buildMapGraph(sky, [], [], 'en')))
  })

  it('関係の語', () => {
    expect(relationLabel('isPartOf', 'ja')).toBe('一部')
    expect(relationLabel('isA', 'en')).toBe('kind of')
    expect(relationLabel('zzz', 'ja')).toBeUndefined()
  })

  it('syncMapAttributes は線の label も写す（ja で配置 → en を sync）', () => {
    const laid = buildMapGraph(sky, [], [], 'ja')
    layoutMap(laid)
    syncMapAttributes(laid, buildMapGraph(sky, [], [], 'en'))
    expect(labels(laid, 'concept')).toEqual(['changes'])
  })
})
