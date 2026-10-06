import { describe, expect, it } from 'vitest'
import type { NeighborGroup, NeighborsResult, NeighborItem } from './cardsApi'
import {
  MAX_BOXES,
  buildNeighborGraph,
  collapseNode,
  columnLayout,
  initialState,
  openBundle,
  openNode,
} from './neighborGraph'

// 題材は気象観測（分野語を書かない）。中心 = 観測所 S1。

const S1 = 'https://example.org/station/1'
const R = (n: number) => `https://example.org/reading/${n}`
const P = (n: number) => `https://example.org/region/${n}`
const item = (iri: string, label: string, over: Partial<NeighborItem> = {}): NeighborItem => ({
  iri,
  label,
  class_iri: null,
  is_hub: false,
  ...over,
})
const group = (over: Partial<NeighborGroup>): NeighborGroup => ({
  key: 'k',
  direction: 'out',
  predicate_iri: 'https://example.org/p',
  predicate_label: '線',
  class_iri: null,
  class_label: null,
  count: 0,
  items: [],
  set_spec: null,
  ...over,
})
const result = (iri: string, label: string, groups: NeighborGroup[], truncated = false): NeighborsResult => ({
  iri,
  found: true,
  center: { iri, label, class_iri: null, class_label: '観測所', is_hub: false },
  groups,
  truncated,
})

const centerResult = result(S1, '観測所 1', [
  group({
    key: `out|located|region`,
    direction: 'out',
    predicate_label: '所在',
    class_label: '地域',
    count: 1,
    items: [item(P(1), '北地域')],
  }),
  group({
    key: `in|of|reading`,
    direction: 'in',
    predicate_label: '観測所',
    class_iri: 'https://example.org/Reading',
    class_label: '観測値',
    count: 40,
    items: [],
    set_spec: {
      class: 'https://example.org/Reading',
      where: [{ property: 'https://example.org/of', iri: S1 }],
      order_by: null,
      limit: 20,
      source_scope: 'all',
    } as never,
  }),
  group({
    key: `in|manages|staff`,
    direction: 'in',
    predicate_label: '担当',
    class_label: '担当者',
    count: 2,
    items: [item('https://example.org/staff/1', '担当者 A'), item('https://example.org/staff/2', '担当者 B', { is_hub: true })],
  }),
])

describe('buildNeighborGraph: 列と向き', () => {
  const view = buildNeighborGraph(initialState(centerResult))

  it('中心は列 0、出る線は +1、入る線は −1', () => {
    expect(view.columns.get(S1)).toBe(0)
    expect(view.columns.get(P(1))).toBe(1)
    expect(view.columns.get('https://example.org/staff/1')).toBe(-1)
  })

  it('線の向きは主語 → 目的語のまま（入る線は隣から中心へ）', () => {
    const out = view.graph.edges.find((e) => e.to === P(1))
    expect(out).toEqual({ from: S1, to: P(1), label: '所在' })
    const inn = view.graph.edges.find((e) => e.from === 'https://example.org/staff/1')
    expect(inn).toEqual({ from: 'https://example.org/staff/1', to: S1, label: '担当' })
  })

  it('多い群は束の箱 1 つ（名前に件数）。少ない群は 1 件ずつの箱', () => {
    const bundle = view.graph.nodes.find((n) => n.kind === 'bundle')!
    expect(bundle.label).toBe('観測値 40 件')
    expect(view.columns.get(bundle.id)).toBe(-1)
    expect(view.graph.nodes.filter((n) => n.kind === 'bundle')).toHaveLength(1)
    expect(view.graph.nodes.map((n) => n.id)).toContain(P(1))
  })

  it('ハブは hub の種類・中心には印', () => {
    expect(view.graph.nodes.find((n) => n.id === 'https://example.org/staff/2')!.kind).toBe('hub')
    expect(view.graph.nodes.find((n) => n.id === S1)!.center).toBe(true)
  })

  it('束の名前は種類が無ければ線の名前', () => {
    const r = result(S1, 'x', [
      group({ key: 'k1', predicate_label: '参照元', count: 9, direction: 'in' }),
    ])
    const v = buildNeighborGraph(initialState(r))
    expect(v.graph.nodes.find((n) => n.kind === 'bundle')!.label).toBe('参照元 9 件')
  })

  it('決定論: 同じ入力は同じ図', () => {
    expect(buildNeighborGraph(initialState(centerResult))).toEqual(view)
  })
})

describe('openNode / collapseNode', () => {
  const regionResult = result(P(1), '北地域', [
    group({
      key: 'in|located|station',
      direction: 'in',
      predicate_label: '所在',
      class_label: '観測所',
      count: 2,
      items: [item(S1, '観測所 1'), item('https://example.org/station/2', '観測所 2')],
    }),
    group({
      key: 'out|partOf|country',
      direction: 'out',
      predicate_label: '属する',
      class_label: '国',
      count: 1,
      items: [item('https://example.org/country/1', '国 1')],
    }),
  ])

  it('ひらくと親の列を基準に足す。すでにある箱は動かさず線だけ足す', () => {
    const s0 = initialState(centerResult)
    const { state, blocked } = openNode(s0, P(1), regionResult)
    expect(blocked).toBe(false)
    const v = buildNeighborGraph(state)
    expect(v.columns.get(S1)).toBe(0)
    expect(v.columns.get('https://example.org/station/2')).toBe(0) // P1(1) + (-1)
    expect(v.columns.get('https://example.org/country/1')).toBe(2)
    expect(v.graph.nodes.filter((n) => n.id === S1)).toHaveLength(1)
    // 中心への線は元のまま 1 本（S1 → P1 は所在）
    expect(v.graph.edges.filter((e) => e.from === S1 && e.to === P(1))).toHaveLength(1)
    expect(v.graph.edges.filter((e) => e.from === 'https://example.org/station/2' && e.to === P(1))).toHaveLength(1)
  })

  it('同じ箱を 2 度足さない（2 回ひらいても同じ）', () => {
    const s1 = openNode(initialState(centerResult), P(1), regionResult).state
    const s2 = openNode(s1, P(1), regionResult).state
    expect(buildNeighborGraph(s2)).toEqual(buildNeighborGraph(s1))
  })

  it('たたむと足した箱と線が消える。中心と他からつながる箱は残る', () => {
    const s1 = openNode(initialState(centerResult), P(1), regionResult).state
    const s2 = collapseNode(s1, P(1))
    expect(buildNeighborGraph(s2)).toEqual(buildNeighborGraph(initialState(centerResult)))
  })

  it('他の節からもつながっている箱は、たたんでも残る', () => {
    const staff = 'https://example.org/staff/1'
    const other = result(staff, '担当者 A', [
      group({
        key: 'out|x|station',
        direction: 'out',
        predicate_label: '担当観測所',
        class_label: '観測所',
        count: 1,
        items: [item('https://example.org/station/2', '観測所 2')],
      }),
    ])
    let s = openNode(initialState(centerResult), staff, other).state
    s = openNode(s, P(1), regionResult).state // station/2 は P1 からも staff/1 からもつながる
    const collapsed = buildNeighborGraph(collapseNode(s, P(1)))
    const ids = collapsed.graph.nodes.map((n) => n.id)
    expect(ids).toContain('https://example.org/station/2')
    expect(ids).not.toContain('https://example.org/country/1')
  })

  it('親をたたむと、孫の節（ひらいていた）も一緒に消える', () => {
    const country = result('https://example.org/country/1', '国 1', [
      group({ key: 'out|a|b', predicate_label: '首都', class_label: '都市', count: 1, items: [item('https://example.org/city/1', '都市 1')] }),
    ])
    let s = openNode(initialState(centerResult), P(1), regionResult).state
    s = openNode(s, 'https://example.org/country/1', country).state
    expect(buildNeighborGraph(s).graph.nodes.map((n) => n.id)).toContain('https://example.org/city/1')
    s = collapseNode(s, P(1))
    expect(buildNeighborGraph(s).graph.nodes.map((n) => n.id)).not.toContain('https://example.org/city/1')
    expect(s.openOrder).toEqual([S1])
  })

  it('中心はたためない', () => {
    const s = initialState(centerResult)
    expect(collapseNode(s, S1)).toBe(s)
  })

  it(`箱が ${MAX_BOXES} を超えるひらき方は止める（状態は変えない）`, () => {
    const many = result(
      P(1),
      '北地域',
      Array.from({ length: 6 }, (_, g) =>
        group({
          key: `out|p${g}|c`,
          predicate_label: `線${g}`,
          count: 6,
          items: Array.from({ length: 6 }, (_, i) => item(`https://example.org/x/${g}/${i}`, `x${g}-${i}`)),
        }),
      ),
    )
    let s = initialState(centerResult)
    s = openNode(s, P(1), many).state // 3+... 
    expect(buildNeighborGraph(s).graph.nodes.length).toBeLessThanOrEqual(MAX_BOXES)
    // さらに別の節で足していくと、いずれ止まる
    let blockedSeen = false
    for (let k = 0; k < 6 && !blockedSeen; k++) {
      const id = `https://example.org/x/${k}/0`
      const more = result(
        id,
        'x',
        Array.from({ length: 6 }, (_, g) =>
          group({
            key: `out|q${g}|c`,
            predicate_label: `q${g}`,
            count: 6,
            items: Array.from({ length: 6 }, (_, i) => item(`https://example.org/y/${k}/${g}/${i}`, `y${i}`)),
          }),
        ),
      )
      const r = openNode(s, id, more)
      if (r.blocked) {
        blockedSeen = true
        expect(r.state).toBe(s)
      } else {
        s = r.state
        expect(buildNeighborGraph(s).graph.nodes.length).toBeLessThanOrEqual(MAX_BOXES)
      }
    }
    expect(blockedSeen).toBe(true)
  })
})

describe('束', () => {
  const sampleBundle = result(S1, '観測所 1', [
    group({
      key: 'out|r|c',
      direction: 'out',
      predicate_label: '記録',
      class_label: '記録',
      count: 15,
      items: Array.from({ length: 12 }, (_, i) => item(R(i), `記録 ${i}`)),
      set_spec: null,
    }),
  ])

  it('set_spec の無い束は「中を見る」で sample を箱にし、残り件数の束を残す', () => {
    const s0 = initialState(sampleBundle)
    const bundle = [...buildNeighborGraph(s0).meta.values()].find((m) => m.kind === 'bundle')!
    expect(bundle.bundle!.group.set_spec).toBeNull()
    const { state, blocked } = openBundle(s0, bundle.id)
    expect(blocked).toBe(false)
    const v = buildNeighborGraph(state)
    expect(v.graph.nodes.filter((n) => n.kind === 'entity' && !n.center)).toHaveLength(12)
    const rest = [...v.meta.values()].find((m) => m.kind === 'bundle')!
    expect(rest.bundle!.remaining).toBe(3)
    expect(rest.opened).toBe(true)
  })

  it('全部が見えたら束は消える', () => {
    const full = result(S1, 'x', [
      group({
        key: 'out|r|c',
        predicate_label: '記録',
        count: 7,
        items: Array.from({ length: 7 }, (_, i) => item(R(i), `記録 ${i}`)),
      }),
    ])
    const s0 = initialState(full)
    const b = [...buildNeighborGraph(s0).meta.values()].find((m) => m.kind === 'bundle')!
    const v = buildNeighborGraph(openBundle(s0, b.id).state)
    expect([...v.meta.values()].some((m) => m.kind === 'bundle')).toBe(false)
  })

  it('たたむと束を開いたことも忘れる', () => {
    const regionOpen = result(P(1), 'r', [
      group({ key: 'out|r|c', predicate_label: '記録', count: 8, items: Array.from({ length: 8 }, (_, i) => item(R(i), `記録 ${i}`)) }),
    ])
    let s = openNode(initialState(centerResult), P(1), regionOpen).state
    const b = [...buildNeighborGraph(s).meta.values()].find((m) => m.kind === 'bundle' && m.bundle!.ownerIri === P(1))!
    s = openBundle(s, b.id).state
    s = collapseNode(s, P(1))
    expect(s.openedBundles).toEqual([])
  })
})

describe('truncated', () => {
  it('ひらいた節のどれかが切れていれば truncated を伝える', () => {
    expect(buildNeighborGraph(initialState(result(S1, 'x', [], true))).truncated).toBe(true)
    expect(buildNeighborGraph(initialState(result(S1, 'x', [], false))).truncated).toBe(false)
  })
})

describe('columnLayout', () => {
  it('列は左から右へ・列の中は足した順（決定論）', () => {
    const v = buildNeighborGraph(initialState(centerResult))
    const { positions, height } = columnLayout(v.columns)(v.graph)
    expect(positions.get('https://example.org/staff/1')!.x).toBeLessThan(positions.get(S1)!.x)
    expect(positions.get(S1)!.x).toBeLessThan(positions.get(P(1))!.x)
    expect(positions.get('https://example.org/staff/1')!.y).toBeLessThan(positions.get('https://example.org/staff/2')!.y)
    expect(height).toBeGreaterThan(0)
    expect(columnLayout(v.columns)(v.graph)).toEqual({ positions, height })
  })
})
