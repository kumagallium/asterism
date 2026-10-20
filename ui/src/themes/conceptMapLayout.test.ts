import { describe, expect, it } from 'vitest'
import { layoutConceptMap, MAP_NODE_H, type MapNodePos } from './conceptMapLayout'

// 熱伝導率の例（スタブ AI と同じ形）: 格子・電子は熱伝導率の一部。粒界散乱・点欠陥は格子を下げ、
// キャリア濃度は電子を上げ、試料は熱伝導率を変える。
const nodes = [
  { key: 'c1', label: '熱伝導率' },
  { key: 'c2', label: '格子熱伝導率' },
  { key: 'c3', label: '電子熱伝導率' },
  { key: 'c4', label: '粒界散乱' },
  { key: 'c5', label: '点欠陥散乱' },
  { key: 'c6', label: 'キャリア濃度' },
  { key: 'c7', label: '試料' },
]
const rels = [
  { index: 0, from: 'c2', to: 'c1', rel: 'part' as const },
  { index: 1, from: 'c3', to: 'c1', rel: 'part' as const },
  { index: 2, from: 'c4', to: 'c2', rel: 'lowers' as const },
  { index: 3, from: 'c5', to: 'c2', rel: 'lowers' as const },
  { index: 4, from: 'c6', to: 'c3', rel: 'raises' as const },
  { index: 5, from: 'c7', to: 'c1', rel: 'affects' as const },
]

const overlaps = (a: MapNodePos, b: MapNodePos) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

describe('layoutConceptMap', () => {
  const m = layoutConceptMap(nodes, rels)

  it('puts 一部 on rows (part above whole) and causes beside what they change', () => {
    const row = (k: string) => m.byKey.get(k)!.row
    expect(row('c2')).toBe(0)
    expect(row('c3')).toBe(0)
    expect(row('c1')).toBe(1)
    // 下げる・上げる・変える は段を作らない: 相手と同じ段
    expect(row('c4')).toBe(row('c2'))
    expect(row('c5')).toBe(row('c2'))
    expect(row('c6')).toBe(row('c3'))
    expect(row('c7')).toBe(row('c1'))
    expect(m.rows.length).toBe(2)
  })

  it('reads causes left to effects right', () => {
    const x = (k: string) => m.byKey.get(k)!.x
    expect(x('c4')).toBeLessThan(x('c2'))
    expect(x('c5')).toBeLessThan(x('c2'))
    expect(x('c6')).toBeLessThan(x('c3'))
    // 試料 → 熱伝導率（試料は原因）
    expect(x('c7')).toBeLessThan(x('c1'))
  })

  it('never overlaps boxes, and stacks the causes of one concept in one column left of it', () => {
    for (const a of m.nodes) {
      for (const b of m.nodes) {
        if (a.key !== b.key) expect(overlaps(a, b)).toBe(false)
      }
    }
    const p = (k: string) => m.byKey.get(k)!
    // 粒界散乱と点欠陥散乱は、格子熱伝導率の左に同じ列で縦に積まれる
    expect(p('c4').x + p('c4').w / 2).toBeCloseTo(p('c5').x + p('c5').w / 2, 1)
    expect(Math.abs(p('c4').y - p('c5').y)).toBeGreaterThanOrEqual(MAP_NODE_H)
    expect(m.width).toBeGreaterThan(0)
    expect(m.height).toBeGreaterThanOrEqual(MAP_NODE_H)
  })

  it('draws one edge per relation, hierarchy across rows, side along a row', () => {
    expect(m.edges.map((e) => e.index)).toEqual([0, 1, 2, 3, 4, 5])
    expect(m.edges.filter((e) => e.kind === 'hier').map((e) => e.index)).toEqual([0, 1])
    for (const e of m.edges) {
      expect(e.path.startsWith('M')).toBe(true)
      expect(Number.isFinite(e.mid.x) && Number.isFinite(e.mid.y)).toBe(true)
    }
    // 横の線は、向かい合う辺どうしを横向きに結ぶ（原因の右辺 → 結果の左辺）
    const cause = m.byKey.get('c4')!
    const effect = m.byKey.get('c2')!
    const e = m.edges.find((x) => x.index === 2)!
    expect(e.path.startsWith(`M${Math.round((cause.x + cause.w) * 10) / 10},`)).toBe(true)
    expect(e.path.endsWith(`${Math.round(effect.x * 10) / 10},${Math.round((effect.y + effect.h / 2) * 10) / 10}`)).toBe(true)
  })

  it('is deterministic', () => {
    expect(layoutConceptMap(nodes, rels)).toEqual(m)
  })

  it('puts unrelated concepts at the bottom and keeps problem relations out of the rows', () => {
    const more = layoutConceptMap(
      [...nodes, { key: 'x', label: '単独' }],
      [...rels, { index: 6, from: 'c1', to: 'c2', rel: 'sub' as const, problem: true }],
    )
    expect(more.byKey.get('x')!.row).toBe(more.rows.length - 1)
    const bad = more.edges.find((e) => e.index === 6)!
    expect(bad.problem).toBe(true)
    expect(bad.kind).toBe('side')
    // 機械が止めた「一種」は段を作らない
    expect(more.byKey.get('c2')!.row).toBe(0)
  })

  it('handles eq as two-way and a map with no relations at all', () => {
    const eq = layoutConceptMap(
      [
        { key: 'a', label: 'A' },
        { key: 'b', label: 'B' },
      ],
      [{ index: 0, from: 'a', to: 'b', rel: 'eq' }],
    )
    expect(eq.edges[0].both).toBe(true)
    expect(eq.rows).toEqual([['a', 'b']])
    const none = layoutConceptMap(nodes, [])
    expect(none.rows.flat().sort()).toEqual(nodes.map((n) => n.key).sort())
    expect(layoutConceptMap([], [])).toMatchObject({ width: 32, height: 0 })
  })

  it('ignores relations whose ends are not on the map (standard terms)', () => {
    const r = layoutConceptMap(nodes, [{ index: 0, from: 'c1', to: 'quantitykind:Foo', rel: 'quantity' }])
    expect(r.edges).toEqual([])
  })

  it('draws a standard word as a small card at the end of a quantity line (the line matches the sentence)', () => {
    const r = layoutConceptMap(
      [...nodes, { key: 'quantitykind:ThermalConductivity', label: 'Thermal Conductivity', std: true }],
      [...rels, { index: 6, from: 'c1', to: 'quantitykind:ThermalConductivity', rel: 'quantity' }],
    )
    const std = r.byKey.get('quantitykind:ThermalConductivity')!
    expect(std.std).toBe(true)
    expect(r.byKey.get('c1')!.std).toBe(false)
    // 量の線は段を作らず、概念の右（結果の側）の同じ段に置く
    expect(std.row).toBe(r.byKey.get('c1')!.row)
    expect(std.x).toBeGreaterThan(r.byKey.get('c1')!.x)
    const e = r.edges.find((x) => x.index === 6)!
    expect(e.kind).toBe('side')
    expect(e.to).toBe('quantitykind:ThermalConductivity')
    for (const a of r.nodes) for (const b of r.nodes) if (a.key !== b.key) expect(overlaps(a, b)).toBe(false)
  })

  it('align: start puts every row at the left edge (for a map that scrolls sideways); the default centres them', () => {
    const centre = layoutConceptMap(nodes, rels)
    const start = layoutConceptMap(nodes, rels, { align: 'start' })
    expect(start.width).toBe(centre.width)
    const leftOf = (l: typeof centre, row: number) => Math.min(...l.nodes.filter((n) => n.row === row).map((n) => n.x))
    expect(leftOf(start, 0)).toBe(16)
    expect(leftOf(start, 1)).toBe(16)
    expect(leftOf(centre, 1)).toBeGreaterThan(16) // 狭い段は中央寄せ
    for (const a of start.nodes) for (const b of start.nodes) if (a.key !== b.key) expect(overlaps(a, b)).toBe(false)
  })

  it('maxWidth wraps a wide row into several rows: nothing is wider than the limit, nothing overlaps, 一部 still runs top to bottom', () => {
    const wide = layoutConceptMap(nodes, rels)
    const limit = 420
    expect(wide.width).toBeGreaterThan(limit)
    const w = layoutConceptMap(nodes, rels, { maxWidth: limit })
    expect(w.width).toBeLessThanOrEqual(limit)
    expect(w.rows.length).toBeGreaterThan(wide.rows.length)
    for (const a of w.nodes) for (const b of w.nodes) if (a.key !== b.key) expect(overlaps(a, b)).toBe(false)
    // 一種・一部の線は、子（from）が上・親（to）が下のまま
    for (const e of w.edges.filter((x) => x.kind === 'hier')) {
      expect(w.byKey.get(e.from)!.row).toBeLessThan(w.byKey.get(e.to)!.row)
    }
    // どの札も図の中に収まる
    for (const n of w.nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0)
      expect(n.x + n.w).toBeLessThanOrEqual(w.width)
    }
    // 折り返して離れた同じかたまりの線も、端はかならず相手の札についている
    for (const e of w.edges) expect(Number.isFinite(e.end.x + e.end.y)).toBe(true)
  })

  it('maxWidth wide enough changes nothing; without it nothing wraps', () => {
    const plain = layoutConceptMap(nodes, rels)
    const roomy = layoutConceptMap(nodes, rels, { maxWidth: plain.width + 50 })
    expect(roomy.rows).toEqual(plain.rows)
    expect(roomy.width).toBe(plain.width)
  })

  it('maxWidth packs loose cards by width instead of five per row', () => {
    const loose = Array.from({ length: 9 }, (_, i) => ({ key: `l${i}`, label: `概念${i}` }))
    expect(layoutConceptMap(loose, []).rows.map((r) => r.length)).toEqual([5, 4])
    const w = layoutConceptMap(loose, [], { maxWidth: 300 })
    expect(w.width).toBeLessThanOrEqual(300)
    expect(w.rows.length).toBeGreaterThan(2)
    expect(w.rows.flat().sort()).toEqual(loose.map((n) => n.key).sort())
  })

  it('a cluster that is wider than the limit by itself is split at a column boundary', () => {
    const ns = [
      { key: 'a', label: '原因の概念その一' },
      { key: 'b', label: '真ん中の概念' },
      { key: 'c', label: '結果の概念その一' },
    ]
    const rs = [
      { index: 0, from: 'a', to: 'b', rel: 'lowers' as const },
      { index: 1, from: 'b', to: 'c', rel: 'raises' as const },
    ]
    const flat = layoutConceptMap(ns, rs)
    expect(flat.rows.length).toBe(1)
    const w = layoutConceptMap(ns, rs, { maxWidth: 320 })
    expect(w.rows.length).toBeGreaterThan(1)
    expect(w.width).toBeLessThanOrEqual(320)
    expect(w.edges.length).toBe(2)
  })
})
