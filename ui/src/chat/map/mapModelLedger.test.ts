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
})
