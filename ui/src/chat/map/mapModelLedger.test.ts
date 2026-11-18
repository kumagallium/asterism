import Graph from 'graphology'
import { describe, expect, it } from 'vitest'
import { EMPTY_SKY } from '../../sky/skyApi'
import type { DraftSky, SkyData } from '../../sky/skyApi'
import { draftNodeId } from '../../sky/skyLayout'
import { buildMapGraph, layoutMap, mapSignature, syncMapAttributes, toneOf, type MapNodeAttrs } from './mapModel'

const draft = (over: { confirmed?: boolean } = {}): DraftSky => ({
  draft_id: 'ledger:s',
  theme_label: '台帳',
  concepts: [
    { key: 'm', label: '小麦粉', kind: 'class', use: 'new', state: 'solid', role: 'material', confirmed: true } as DraftSky['concepts'][number],
    { key: 'p', label: 'かたさ', kind: 'property', use: 'new', state: 'solid', role: null, confirmed: over.confirmed ?? false } as DraftSky['concepts'][number],
    { key: 't', label: 'こねる', kind: 'class', use: 'new', state: 'solid', role: 'method', confirmed: false } as DraftSky['concepts'][number],
    { key: 'c', label: '粘り', kind: 'class', use: 'new', state: 'solid', role: null, confirmed: false } as DraftSky['concepts'][number],
  ],
  relations: [
    { from: 't', to: 'p', rel: 'affects', state: 'solid', confirmed: over.confirmed ?? false } as DraftSky['relations'][number],
    { from: 'm', to: 'c', rel: 'sub', state: 'thin', confirmed: false } as DraftSky['relations'][number],
    { from: 'm', to: 't', rel: 'candidate', state: 'thin', candidate: true } as DraftSky['relations'][number],
  ],
  questions: [],
})
const sky: SkyData = { ...EMPTY_SKY, datasets: [{ id: 'world', label: '世界', created_at: '2026-01-01', kinds: [{ iri: 'urn:k:a', label: '国', count: 3, links: [], standard: null }] }] }
const attrs = (g: Graph, id: string) => g.getNodeAttributes(id) as unknown as MapNodeAttrs

describe('台帳の枝（buildMapGraph）', () => {
  it('語は役割で色分け（材料 m・物性 p・手法 t・それ以外 c）。確認済みの関係が無い語は薄い印', () => {
    expect(toneOf('material')).toBe('m')
    const g = buildMapGraph(sky, [draft()], [], 'ja')
    expect(attrs(g, draftNodeId('ledger:s', 'm'))).toMatchObject({ tone: 'm', ledger: true })
    expect(attrs(g, draftNodeId('ledger:s', 'p'))).toMatchObject({ tone: 'p', unconfirmed: true })
    expect(attrs(g, draftNodeId('ledger:s', 't')).tone).toBe('t')
    expect(attrs(g, draftNodeId('ledger:s', 'c')).tone).toBe('c')
    expect(attrs(g, draftNodeId('ledger:s', 'm')).unconfirmed).toBeUndefined()
  })
  it('線: 確認済み＝薄くない・未確認＝薄い・候補＝名前の無い薄い線。名前は関係の短い名前', () => {
    const g = buildMapGraph(sky, [draft({ confirmed: true })], [], 'ja')
    const edges = g.edges().map((e) => g.getEdgeAttributes(e) as Record<string, unknown>)
    const fixed = edges.find((e) => e.relation === 'affects')!
    const un = edges.find((e) => e.relation === 'sub')!
    const cand = edges.find((e) => e.candidate === true)!
    expect(fixed).toMatchObject({ ledger: true, label: '変える' })
    expect(fixed.thin).toBeUndefined()
    expect(un).toMatchObject({ thin: true, unconfirmed: true })
    expect(cand).toMatchObject({ thin: true, candidate: true })
    expect(cand.label).toBeUndefined()
  })
  it('確認しても図の形（署名）は変わらない＝カメラが戻らない。属性だけ写る', () => {
    const a = buildMapGraph(sky, [draft()], [], 'ja')
    const b = buildMapGraph(sky, [draft({ confirmed: true })], [], 'ja')
    expect(mapSignature(b)).toBe(mapSignature(a))
    layoutMap(a)
    syncMapAttributes(a, b)
    const e = a.edges().find((x) => a.getEdgeAttribute(x, 'relation') === 'affects')!
    expect(a.getEdgeAttribute(e, 'thin')).toBeUndefined()
  })
})

describe('layoutMap と stretchToAspect', () => {
  const finite = (g: Graph) => g.everyNode((_i, a) => Number.isFinite(a.x) && Number.isFinite(a.y))
  const extent = (g: Graph) => {
    const xs = g.mapNodes((_i, a) => a.x as number)
    const ys = g.mapNodes((_i, a) => a.y as number)
    return { w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) }
  }
  const sameXY = (a: Graph, b: Graph) => a.everyNode((id, at) => at.x === b.getNodeAttribute(id, 'x') && at.y === b.getNodeAttribute(id, 'y'))
  it('台帳の語が無いときは枠の縦横比に依らず従来と同じ配置', () => {
    const a = buildMapGraph(sky, [], [], 'ja')
    const b = buildMapGraph(sky, [], [], 'ja')
    layoutMap(a)
    layoutMap(b, 0.4)
    expect(sameXY(a, b)).toBe(true)
  })
  it('台帳の語があるとき: NaN が無く、重ならず、枠の縦横比に合う（横長・縦長）', () => {
    for (const aspect of [1.6, 0.5]) {
      const g = buildMapGraph(sky, [draft()], [], 'ja')
      layoutMap(g, aspect)
      expect(finite(g)).toBe(true)
      const pts = g.mapNodes((_i, a) => `${(a.x as number).toFixed(3)},${(a.y as number).toFixed(3)}`)
      expect(new Set(pts).size).toBe(pts.length)
      const { w, h } = extent(g)
      expect(w / h).toBeCloseTo(aspect, 0)
    }
  })
  it('台帳だけの図でも枠いっぱいに伸ばす。同じ入力なら何度でも同じ', () => {
    const a = buildMapGraph(EMPTY_SKY, [draft()], [], 'ja')
    const b = buildMapGraph(EMPTY_SKY, [draft()], [], 'ja')
    layoutMap(a, 1.5)
    layoutMap(b, 1.5)
    layoutMap(b, 1.5)
    expect(sameXY(a, b)).toBe(true)
    expect(finite(a)).toBe(true)
    const { w, h } = extent(a)
    expect(w / h).toBeCloseTo(1.5, 0)
  })
  it('データセット側の点が無い図（語と関係だけ）は枠いっぱいに伸ばす。データセットがある図は従来のまま', () => {
    const a = buildMapGraph(EMPTY_SKY, [draft()], [], 'ja')
    layoutMap(a, 1.6)
    const { w, h } = extent(a)
    expect(w / h).toBeCloseTo(1.6, 0)
    const withDs = buildMapGraph(sky, [], [], 'ja')
    const same = buildMapGraph(sky, [], [], 'ja')
    layoutMap(withDs, 1.6)
    layoutMap(same, 0.3)
    expect(sameXY(withDs, same)).toBe(true)
  })
})

describe('矢じり（向きのある関係の線）', () => {
  const dir = (g: Graph) => g.mapEdges((e, a) => ({ rel: (a.relation ?? a.kind) as string, directed: a.directed === true, src: g.source(e), tgt: g.target(e) }))
  const ledgerDraft = (): DraftSky => ({
    draft_id: 'ledger:s',
    theme_label: '台帳',
    concepts: ['a', 'b', 'c', 'd', 'e'].map((k) => ({ key: k, label: k, kind: 'class' as const, use: 'new', state: 'solid' as const })),
    relations: [
      { from: 'a', to: 'b', rel: 'lowers', state: 'solid' },
      { from: 'b', to: 'c', rel: 'sub', state: 'thin' },
      { from: 'c', to: 'd', rel: 'competes', state: 'thin' },
      { from: 'd', to: 'e', rel: 'eq', state: 'thin' },
      { from: 'a', to: 'e', rel: 'candidate', state: 'thin', candidate: true } as DraftSky['relations'][number],
    ],
    questions: [],
  })
  it('台帳: 向きのある関係（下げる・一種）だけに付き、from → to の向きで張る。競合する・同じ・候補には付けない', () => {
    const g = buildMapGraph(EMPTY_SKY, [ledgerDraft()], [], 'ja')
    const by = Object.fromEntries(dir(g).map((e) => [e.rel, e]))
    expect(by.lowers).toMatchObject({ directed: true, src: draftNodeId('ledger:s', 'a'), tgt: draftNodeId('ledger:s', 'b') })
    expect(by.sub).toMatchObject({ directed: true, src: draftNodeId('ledger:s', 'b'), tgt: draftNodeId('ledger:s', 'c') })
    expect(by.competes.directed).toBe(false)
    expect(by.eq.directed).toBe(false)
    expect(by.candidate.directed).toBe(false)
  })
  it('ストアの確定した関係の線: 向きのある関係（一種）は from → to で矢じり。競合する・データセットと種類を結ぶ線・用語と種類の線には付けない', () => {
    const mk = (slug: string, members: SkyData['terms'][number]['members'] = []) => ({ iri: `urn:t:${slug}`, slug, label: slug, label_en: slug, kind: 'class' as const, members, standard: null, answering: 0, questions: [] })
    const s: SkyData = {
      ...sky,
      terms: [mk('x', [{ iri: 'urn:k:a', dataset_id: 'ds1', relation: 'subClassOf' }]), mk('y'), mk('z')],
      edges: [
        { from: 'urn:t:x', to: 'urn:t:y', relation: 'subClassOf' },
        { from: 'urn:t:y', to: 'urn:t:z', relation: 'competesWith' },
      ] as unknown as SkyData['edges'],
      datasets: [{ id: 'ds1', label: 'データ1', created_at: '2026-01-01', kinds: [{ iri: 'urn:k:a', label: '材料', count: 3, links: [], standard: null }] }],
    }
    const g = buildMapGraph(s, [], [], 'ja')
    const e = g.mapEdges((id, a) => ({ kind: a.kind as string, rel: a.relation as string | undefined, directed: a.directed === true, src: g.source(id), tgt: g.target(id) }))
    const sub = e.find((x) => x.kind === 'concept' && x.rel === 'subClassOf')!
    expect(sub).toMatchObject({ directed: true, src: 't|urn:t:x', tgt: 't|urn:t:y' })
    expect(e.find((x) => x.rel === 'competesWith')!.directed).toBe(false)
    expect(e.filter((x) => x.kind === 'dataset' || x.kind === 'member').every((x) => !x.directed)).toBe(true)
  })
})

