import { describe, expect, it, vi } from 'vitest'
import type { NetworkNode, NetworkNodeKind, NetworkResponse } from './networkApi'
import {
  BUNDLE_R,
  ENTITY_R,
  VALUE_R,
  assignKindRoles,
  focusedKindRoles,
  nodeAppearance,
  edgeAppearance,
  legendKindRows,
  visibleLegendRows,
  REST_ROLE,
  buildNetworkGraph,
  connectedParts,
  focusCamera,
  hashPosition,
  layoutIterations,
  layoutNetwork,
  loadLaidOutNetwork,
  neighborhoodOf,
  searchNodes,
  KIND_COLORS,
  kindDisplayName,
  roleCss,
  roleOf,
} from './networkModel'

// 架空の分野（星・観測所）の作り物。
const NS = 'https://example.org/sky#'
const node = (id: string, over: Partial<NetworkNode> = {}): NetworkNode => ({
  id,
  kind: 'entity',
  label: id,
  class_iri: `${NS}Star`,
  class_label: '星',
  dataset_id: 'd1',
  count: 1,
  degree: 1,
  set_spec: null,
  ...over,
})

function fixture(): NetworkResponse {
  const nodes: NetworkNode[] = [
    node('s1', { degree: 3 }),
    node('s2', { degree: 2, label: 'Vega' }),
    node('o1', { class_iri: `${NS}Obs`, class_label: '観測所', degree: 2 }),
    node('v:region:north', { kind: 'value', class_iri: null, class_label: null, label: '地域: 北', degree: 3 }),
    node('b1', { kind: 'bundle', count: 3001, degree: 1, label: 'Record 3,001 件' }),
    node('h1', { kind: 'hub', class_iri: `${NS}Hub`, class_label: '共通', degree: 2 }),
  ]
  return {
    nodes,
    edges: [
      { source: 's1', target: 'v:region:north', label: '地域' },
      { source: 's2', target: 'v:region:north', label: '地域' },
      { source: 'o1', target: 'v:region:north', label: '地域' },
      { source: 's1', target: 'o1', label: '観測' },
      { source: 'b1', target: 'h1', label: '' },
      { source: 'h1', target: 's2', label: '' },
      // 落とされる: 重複・自分への線・存在しない点
      { source: 'o1', target: 's1', label: '観測' },
      { source: 's1', target: 's1', label: '' },
      { source: 's1', target: 'nope', label: '' },
    ],
    kinds: [
      { class_iri: `${NS}Star`, class_label: '星', count: 3003 },
      { class_iri: `${NS}Obs`, class_label: '観測所', count: 1 },
    ],
    stats: { entities: 3004, nodes: 6, edges: 6, values: 1, bundles: 1 },
    truncated: false,
  }
}

/** 1 種類だけ渡したときの好みの席（ぶつからない）。 */
// 固定値（seatOf と別に直書き）。変わったら既存ユーザの色が変わる
const GOLDEN: [string, string][] = [
  ['https://example.org/sky#Star', 'kind-7'],
  ['https://example.org/sky#Planet', 'kind-0'],
  ['https://example.org/ns#Class1', 'kind-4'],
]
const seatOf = (iri: string) => assignKindRoles([{ class_iri: iri }]).get(iri) as string
/** 好みの席が互いに違う IRI を n 個（決定論で探す）。 */
function distinctSeatIris(n: number, prefix = 'k'): string[] {
  const out: string[] = []
  const seats = new Set<string>()
  for (let i = 0; out.length < n && i < 10000; i++) {
    const iri = `${prefix}${i}`
    if (seats.has(seatOf(iri))) continue
    seats.add(seatOf(iri))
    out.push(iri)
  }
  return out
}
/** 好みの席が同じ 2 つの IRI（決定論で探す）。 */
function collidingPair(): [string, string] {
  const bySeat = new Map<string, string>()
  for (let i = 0; i < 10000; i++) {
    const iri = `x${i}`
    const seat = seatOf(iri)
    const other = bySeat.get(seat)
    if (other) return [other, iri]
    bySeat.set(seat, iri)
  }
  throw new Error('no collision')
}
const kd = (iris: string[]) => iris.map((class_iri) => ({ class_iri }))

describe('assignKindRoles', () => {
  it('先頭 8 種類に色、9 種類目から rest。8 種類以下なら全部違う色', () => {
    const kinds = Array.from({ length: 10 }, (_, i) => ({ class_iri: `k${i}` }))
    const roles = assignKindRoles(kinds)
    const colors = kinds.slice(0, 8).map((k) => roles.get(k.class_iri))
    expect(new Set(colors).size).toBe(8)
    for (const c of colors) expect(c).toMatch(/^kind-[0-7]$/)
    expect(roles.get('k8')).toBe('rest')
    expect(roles.get('k9')).toBe('rest')
    const few = assignKindRoles(kd(['a', 'b', 'c']))
    expect(new Set(few.values()).size).toBe(3)
  })
  it('同じ入力で同じ割り当て（決定論）', () => {
    const kinds = [{ class_iri: 'b' }, { class_iri: 'a' }]
    expect([...assignKindRoles(kinds)]).toEqual([...assignKindRoles(kinds)])
  })
  it('席は順位でなくハッシュ: 1 位の種類が kind-0 とは限らず、好みの席と一致する', () => {
    const firsts = new Set(
      Array.from({ length: 30 }, (_, i) => assignKindRoles([{ class_iri: `x${i}` }]).get(`x${i}`)),
    )
    expect(firsts.size).toBeGreaterThan(1)
    const iris = distinctSeatIris(3)
    const roles = assignKindRoles(kd(iris))
    iris.forEach((iri) => expect(roles.get(iri)).toBe(seatOf(iri)))
  })
  it('下位に種類が増えても、上位の種類の色は変わらない（上位に席のぶつかりを含む）', () => {
    const [p, q] = collidingPair()
    const rest = Array.from({ length: 20 }, (_, i) => `u${i}`).filter((x) => x !== p && x !== q)
    const top = [p, q, ...rest.slice(0, 3)]
    const before = assignKindRoles(kd(top))
    const after = assignKindRoles(kd([...top, ...rest.slice(3)]))
    for (const iri of top) expect(after.get(iri)).toBe(before.get(iri))
    expect(before.get(p)).not.toBe(before.get(q))
  })
  it('順位が違う 2 つの kinds でも、ぶつからない種類は同じ色（好みの席）', () => {
    const [a, b, c, d] = distinctSeatIris(4)
    const r1 = assignKindRoles(kd([a, b, c]))
    const r2 = assignKindRoles(kd([d, c, b, a]))
    for (const iri of [a, b, c]) expect(r1.get(iri)).toBe(r2.get(iri))
    expect(r1.get(a)).toBe(seatOf(a))
  })
  it('好みの席がぶつかったら次の空き席（+1 して % 8）', () => {
    const [p, q] = collidingPair()
    const roles = assignKindRoles(kd([p, q]))
    const seat = Number(seatOf(p).replace('kind-', ''))
    expect(roles.get(p)).toBe(`kind-${seat}`)
    expect(roles.get(q)).toBe(`kind-${(seat + 1) % 8}`)
    const iris = Array.from({ length: 12 }, (_, i) => `c${i}`)
    const all = assignKindRoles(kd(iris))
    expect(new Set(iris.slice(0, 8).map((i) => all.get(i))).size).toBe(8)
  })
  it('重複する IRI は 1 つとして数え、席も消費しない', () => {
    const roles = assignKindRoles(kd(['a', 'a', 'b']))
    expect(roles.size).toBe(2)
    expect(roles.get('a')).toBe(seatOf('a'))
    expect(roles.get('b')).toBe(assignKindRoles(kd(['a', 'b'])).get('b'))
  })
  it('番号違い・大文字小文字違いの IRI が必ずぶつかることはない', () => {
    const seats = new Set<string>()
    for (let i = 0; i < 9; i++) seats.add(seatOf(`https://example.org/ns#Class${i}`))
    expect(seats.size).toBeGreaterThan(4)
    expect(seatOf('https://example.org/ns#Star') === seatOf('https://example.org/ns#star') &&
      seatOf('https://example.org/ns#Class1') === seatOf('https://example.org/ns#Class9')).toBe(false)
  })
})

describe('席の固定値（色が黙って変わらないように）', () => {
  it('代表的な IRI の席は直書きの値と一致する', () => {
    for (const [iri, seat] of GOLDEN) expect(seatOf(iri)).toBe(seat)
  })
})

describe('focusedKindRoles', () => {
  const iris = distinctSeatIris(5)
  const kinds = kd(iris)
  it('空なら既定と同じ', () => {
    expect([...focusedKindRoles(kinds, new Set())]).toEqual([...assignKindRoles(kinds)])
  })
  it('選んだ種類だけ色が付き、ほかは Map に無い。色は既定と同じ', () => {
    const roles = focusedKindRoles(kinds, new Set([iris[1], iris[3]]))
    expect([...roles.keys()].sort()).toEqual([iris[1], iris[3]].sort())
    expect(roles.get(iris[1])).toBe(assignKindRoles(kinds).get(iris[1]))
    expect(roles.get(iris[3])).toBe(assignKindRoles(kinds).get(iris[3]))
    expect(roles.get(iris[0])).toBeUndefined()
  })
})

describe('nodeAppearance / edgeAppearance', () => {
  const roles = new Map([['A', 'kind-1']])
  const base = { roles, sel: null, isSel: false, isNear: false }
  const focusA = new Set(['A'])
  it('選びも絞り込みも無ければ全部普通', () => {
    const ap = nodeAppearance({ ...base, nodeKind: 'entity', kindKey: 'B', focused: new Set() })
    expect(ap).toMatchObject({ role: REST_ROLE, faded: false, hideLabel: false, zIndex: 0 })
    expect(nodeAppearance({ ...base, nodeKind: 'entity', kindKey: 'A', focused: new Set() }).role).toBe('kind-1')
  })
  it('値・ハブは固定の役割で、絞り込みでも薄くならない', () => {
    for (const [k, role] of [['value', 'value'], ['hub', 'hub']] as const) {
      const ap = nodeAppearance({ ...base, nodeKind: k, kindKey: null, focused: focusA })
      expect(ap.role).toBe(role)
      expect(ap.faded).toBe(false)
    }
  })
  it('絞り込みの外の件と束は薄く名前なし。中なら普通', () => {
    for (const k of ['entity', 'bundle'] as const) {
      expect(nodeAppearance({ ...base, nodeKind: k, kindKey: 'B', focused: focusA })).toMatchObject({
        faded: true,
        hideLabel: true,
      })
      expect(nodeAppearance({ ...base, nodeKind: k, kindKey: 'A', focused: focusA }).faded).toBe(false)
    }
  })
  it('選んだ点とその相手は絞り込みの外でも薄くならない。ほかは薄い', () => {
    const o = { ...base, nodeKind: 'entity' as const, kindKey: 'B', focused: focusA, sel: 's' }
    expect(nodeAppearance({ ...o, isSel: true })).toMatchObject({ faded: false, forceLabel: true, highlighted: true, zIndex: 2 })
    expect(nodeAppearance({ ...o, isNear: true })).toMatchObject({ faded: false, zIndex: 1 })
    expect(nodeAppearance(o)).toMatchObject({ faded: true, hideLabel: true })
  })
  type End = { nodeKind: NetworkNodeKind; kindKey: string | null }
  const inA: End = { nodeKind: 'entity', kindKey: 'A' }
  const inB: End = { nodeKind: 'entity', kindKey: 'B' }
  const val: End = { nodeKind: 'value', kindKey: null }
  const bundleA: End = { nodeKind: 'bundle', kindKey: 'A' }
  const noKind: End = { nodeKind: 'entity', kindKey: null }
  const e = (sel: string | null, s: End, t: End, focused: ReadonlySet<string>) =>
    edgeAppearance({ sel, source: 's', target: 't', sourceNode: s, targetNode: t, focused })
  it('線: 選びがあれば今まで通り、無ければ絞り込みの種類に触れない線を薄く', () => {
    expect(e('s', inB, inB, focusA)).toMatchObject({ on: true, faded: false })
    expect(e('x', inA, inA, focusA).faded).toBe(true)
    expect(e(null, inB, val, focusA).faded).toBe(true)
    expect(e(null, inA, val, focusA).faded).toBe(false)
    expect(e(null, inB, inB, new Set()).faded).toBe(false)
  })
  it('線: 選んだ点が target 側でも「今と同じ」', () => {
    expect(e('t', inB, inB, focusA)).toMatchObject({ on: true, faded: false })
  })
  it('選択中: 値・ハブも（相手でなければ）薄くなり、role は roles から引く', () => {
    const o = { ...base, kindKey: null, focused: focusA, sel: 's' }
    for (const k of ['value', 'hub'] as const) {
      expect(nodeAppearance({ ...o, nodeKind: k }).faded).toBe(true)
      expect(nodeAppearance({ ...o, nodeKind: k, isNear: true }).faded).toBe(false)
    }
    expect(nodeAppearance({ ...o, nodeKind: 'entity', kindKey: 'A' }).role).toBe('kind-1')
    expect(nodeAppearance({ ...o, nodeKind: 'entity', kindKey: 'A', isNear: true }).role).toBe('kind-1')
    expect(nodeAppearance({ ...o, nodeKind: 'entity', kindKey: 'B' }).role).toBe(REST_ROLE)
  })
  it('束は絞り込みの種類として数える。kindKey が null の件は絞り込み中は薄い', () => {
    expect(e(null, bundleA, val, focusA).faded).toBe(false)
    expect(e(null, noKind, val, focusA).faded).toBe(true)
    expect(e(null, noKind, noKind, focusA).faded).toBe(true)
    expect(nodeAppearance({ ...base, nodeKind: 'entity', kindKey: null, focused: focusA })).toMatchObject({
      faded: true,
      hideLabel: true,
    })
    expect(nodeAppearance({ ...base, nodeKind: 'entity', kindKey: null, focused: new Set() }).faded).toBe(false)
  })
})

describe('legendKindRows', () => {
  const kinds = [
    { class_iri: 'https://e.org/ns#StarCatalog', class_label: null, count: 3000 },
    { class_iri: 'https://e.org/ns#Obs', class_label: '観測所', count: 12 },
    { class_iri: 'https://e.org/ns#Obs', class_label: '観測所', count: 12 },
  ]
  it('順番・名前・件数・押した状態・重複なし', () => {
    const rows = legendKindRows(kinds, assignKindRoles(kinds), new Set(['https://e.org/ns#Obs']))
    expect(rows.map((r) => r.name)).toEqual(['Star Catalog', '観測所'])
    expect(rows.map((r) => r.count)).toEqual([3000, 12])
    expect(rows.map((r) => r.pressed)).toEqual([false, true])
  })
  it('絞り込み中は選ばれていない種類は灰', () => {
    const f = new Set(['https://e.org/ns#Obs'])
    const rows = legendKindRows(kinds, focusedKindRoles(kinds, f), f)
    expect(rows[0].role).toBe(REST_ROLE)
    expect(rows[1].role).toMatch(/^kind-/)
  })
})

describe('buildNetworkGraph', () => {
  const g = buildNetworkGraph(fixture())
  it('点と線を変換し、重複・自分・欠けた相手の線を落とす', () => {
    expect(g.order).toBe(6)
    expect(g.size).toBe(6)
  })
  it('色の役割: 件は種類、値・ハブは固定、束は中身の種類', () => {
    const roles = assignKindRoles(fixture().kinds)
    expect(g.getNodeAttribute('s1', 'role')).toBe(roles.get(`${NS}Star`))
    expect(g.getNodeAttribute('o1', 'role')).toBe(roles.get(`${NS}Obs`))
    expect(g.getNodeAttribute('v:region:north', 'role')).toBe('value')
    expect(g.getNodeAttribute('h1', 'role')).toBe('hub')
    expect(g.getNodeAttribute('b1', 'role')).toBe(roles.get(`${NS}Star`))
  })
  it('絞り込みの鍵 kindKey: 件・束は group_iri（無ければ class_iri）、値・ハブは null', () => {
    const resp = fixture()
    resp.nodes = resp.nodes.map((n) => (n.id === 's2' ? { ...n, group_iri: `${NS}Body` } : n))
    const gg = buildNetworkGraph(resp)
    expect(gg.getNodeAttribute('s1', 'kindKey')).toBe(`${NS}Star`)
    expect(gg.getNodeAttribute('s2', 'kindKey')).toBe(`${NS}Body`)
    expect(gg.getNodeAttribute('b1', 'kindKey')).toBe(`${NS}Star`)
    expect(gg.getNodeAttribute('v:region:north', 'kindKey')).toBeNull()
    expect(gg.getNodeAttribute('h1', 'kindKey')).toBeNull()
  })
  it('大きさ: 役割ごとの範囲に収まる（件は小さく・値は中・束は大きく）', () => {
    const size = (id: string) => g.getNodeAttribute(id, 'size') as number
    g.forEachNode((id, a) => {
      const [lo, hi] =
        a.nodeKind === 'bundle' ? BUNDLE_R : a.nodeKind === 'value' ? VALUE_R : ENTITY_R
      expect(size(id)).toBeGreaterThanOrEqual(lo)
      expect(size(id)).toBeLessThanOrEqual(hi)
    })
    // 束は件の点より大きい（束の件数が多くても件の点を大きくしない）
    expect(size('b1')).toBeGreaterThan(size('s1'))
    expect(size('s1')).toBeGreaterThan(size('s2') - 1)
  })
  it('初期位置は id のハッシュそのもの', () => {
    expect(g.getNodeAttribute('s1', 'x')).toBe(hashPosition('s1').x)
  })
})

describe('hashPosition', () => {
  it('同じ id は同じ位置、別の id は別の位置、円盤の中', () => {
    expect(hashPosition('a')).toEqual(hashPosition('a'))
    expect(hashPosition('a')).not.toEqual(hashPosition('b'))
    for (const id of ['a', 'b', 'https://example.org/x#1']) {
      const p = hashPosition(id)
      expect(Math.hypot(p.x, p.y)).toBeLessThanOrEqual(100.0001)
    }
  })
})

describe('layoutNetwork', () => {
  it('同じ入力で同じ配置（固定回数・決定論）', () => {
    const a = buildNetworkGraph(fixture())
    const b = buildNetworkGraph(fixture())
    layoutNetwork(a)
    layoutNetwork(b)
    a.forEachNode((id, attrs) => {
      expect(attrs.x).toBe(b.getNodeAttribute(id, 'x'))
      expect(attrs.y).toBe(b.getNodeAttribute(id, 'y'))
      expect(Number.isFinite(attrs.x)).toBe(true)
    })
    // 初期位置から動いている
    expect(a.getNodeAttribute('s1', 'x')).not.toBe(hashPosition('s1').x)
  })
  it('500 点以上（Barnes-Hut）でも同じ配置', () => {
    const big = (): NetworkResponse => {
      const nodes = Array.from({ length: 520 }, (_, i) => node(`n${i}`, { degree: 2 }))
      const edges = nodes.map((n, i) => ({ source: n.id, target: `n${(i + 1) % 520}`, label: '' }))
      return { ...fixture(), nodes, edges }
    }
    const a = buildNetworkGraph(big())
    const b = buildNetworkGraph(big())
    layoutNetwork(a)
    layoutNetwork(b)
    expect(a.getNodeAttribute('n7', 'x')).toBe(b.getNodeAttribute('n7', 'x'))
  })
  it('空のグラフでも落ちない', () => {
    expect(() => layoutNetwork(buildNetworkGraph({ ...fixture(), nodes: [], edges: [] }))).not.toThrow()
  })
  it('繰り返し回数は 120〜500、点が多いほど少ない', () => {
    expect(layoutIterations(10)).toBe(500)
    expect(layoutIterations(776)).toBe(400)
    expect(layoutIterations(100000)).toBe(120)
  })
})

describe('neighborhoodOf / searchNodes', () => {
  const g = buildNetworkGraph(fixture())
  it('載せた点と相手', () => {
    expect([...neighborhoodOf(g, 'v:region:north')].sort()).toEqual(['o1', 's1', 's2', 'v:region:north'])
    expect(neighborhoodOf(g, null).size).toBe(0)
    expect(neighborhoodOf(g, 'ない').size).toBe(0)
  })
  it('名前で探す: 大文字小文字を無視・前方一致が先', () => {
    expect(searchNodes(g, 'vega')).toEqual(['s2'])
    expect(searchNodes(g, '')).toEqual([])
    expect(searchNodes(g, 'S')[0]).toBe('s1')
  })
})

describe('loadLaidOutNetwork（API をモック）', () => {
  it('取得して配置まで進め、include_prov をそのまま渡す', async () => {
    const fetcher = vi.fn(async () => fixture())
    const r = await loadLaidOutNetwork(fetcher, true)
    expect(fetcher).toHaveBeenCalledWith(true)
    expect(r.graph.order).toBe(6)
    expect(r.response.stats.nodes).toBe(6)
  })
})

describe('種類の色', () => {
  const lab = (hex: string): [number, number, number] => {
    const lin = (i: number) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    }
    const [r, g, b] = [lin(1), lin(3), lin(5)]
    const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    const y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    const f = (v: number) => (v > 0.008856 ? Math.cbrt(v) : 7.787 * v + 16 / 116)
    return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))]
  }
  const dist = (a: string, b: string) => Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]))
  // index.css の --link / --muted / --faint（ハブ・値・そのほか）
  const fixed = ['#356794', '#54695b', '#728579']
  it('8 色が互いに、またハブ・値・灰とも見分けられる距離（Lab 25 以上）にある', () => {
    expect(KIND_COLORS).toHaveLength(8)
    const all = [...KIND_COLORS, ...fixed]
    for (let i = 0; i < KIND_COLORS.length; i++)
      for (let j = i + 1; j < all.length; j++) expect(dist(all[i], all[j])).toBeGreaterThanOrEqual(25)
  })
  it('凡例の色: 種類は固定色、ほかは CSS 変数', () => {
    expect(roleCss('kind-3')).toBe(KIND_COLORS[3])
    expect(roleCss('hub')).toBe('var(--link)')
  })
})

describe('kindDisplayName', () => {
  it('名前があればそのまま、無ければ IRI のローカル名を読みくだす', () => {
    expect(kindDisplayName('星', 'https://example.org/x#Star')).toBe('星')
    expect(kindDisplayName(null, 'https://example.org/x#StarCatalog')).toBe('Star Catalog')
    expect(kindDisplayName('', 'https://example.org/ns/dark_matter')).toBe('dark matter')
  })
})

describe('上位構造の色の鍵・束の名前・壊れた名前', () => {
  it('group_iri があればそれで色を決める（上位の種類が同じなら同じ色）', () => {
    const roles = assignKindRoles([{ class_iri: `${NS}Upper` }])
    const up = roles.get(`${NS}Upper`)
    expect(up).toMatch(/^kind-/)
    expect(roleOf(node('a', { class_iri: `${NS}A`, group_iri: `${NS}Upper` }), roles)).toBe(up)
    expect(roleOf(node('b', { class_iri: `${NS}B`, group_iri: `${NS}Upper` }), roles)).toBe(up)
  })
  it('group_iri が無い古いサーバでは class_iri で塗る', () => {
    const roles = assignKindRoles([{ class_iri: `${NS}A` }])
    expect(roleOf(node('a', { class_iri: `${NS}A` }), roles)).toBe(roles.get(`${NS}A`))
  })
  it('束の名前は渡した作り方で組み立てる', () => {
    const resp = {
      nodes: [node('b1', { kind: 'bundle', class_label: '観測', label: '観測', count: 3001, degree: 1 }), node('c1', { degree: 1 })],
      edges: [{ source: 'b1', target: 'c1', label: 'x' }],
      kinds: [],
      stats: { entities: 3002, nodes: 2, edges: 1, values: 0, bundles: 1 },
      truncated: false,
    } as unknown as NetworkResponse
    const g = buildNetworkGraph(resp, (name, count) => `${name}:${count}`)
    expect(g.getNodeAttribute('b1', 'label')).toBe('観測:3001')
  })
  it('壊れた % 符号の名前でも落ちない', () => {
    expect(kindDisplayName(null, 'http://x/a%ZZ')).toBe('a%ZZ')
  })
})

describe('focusCamera', () => {
  it('点と相手が全部入る広さ・真ん中に寄る・0.04〜1 に収める', () => {
    const c = focusCamera([
      { x: 0.2, y: 0.4 },
      { x: 0.6, y: 0.5 },
    ])
    expect(c.x).toBeCloseTo(0.4)
    expect(c.y).toBeCloseTo(0.45)
    expect(c.ratio).toBeCloseTo(0.52)
    expect(focusCamera([{ x: 0.3, y: 0.3 }]).ratio).toBe(0.04)
    expect(focusCamera([{ x: 0, y: 0 }, { x: 2, y: 0 }]).ratio).toBe(1)
    expect(focusCamera([])).toEqual({ x: 0.5, y: 0.5, ratio: 1 })
  })
})

describe('connectedParts / まとまりごとの配置', () => {
  // 大きなまとまり（星 1 つに 30 個）と小さなまとまり（3 点）。
  const twoParts = (): NetworkResponse => {
    const nodes: NetworkNode[] = [node('hubA'), node('x1'), node('x2'), node('x3')]
    const edges = [
      { source: 'x1', target: 'x2', label: 'p' },
      { source: 'x2', target: 'x3', label: 'p' },
    ]
    for (let i = 0; i < 30; i++) {
      nodes.push(node(`a${String(i).padStart(2, '0')}`))
      edges.push({ source: 'hubA', target: `a${String(i).padStart(2, '0')}`, label: 'p' })
    }
    return {
      nodes,
      edges,
      kinds: [],
      stats: { entities: nodes.length, nodes: nodes.length, edges: edges.length, values: 0, bundles: 0 },
      truncated: false,
    }
  }
  it('大きい順・中身は id 順', () => {
    const parts = connectedParts(buildNetworkGraph(twoParts()))
    expect(parts.map((p) => p.length)).toEqual([31, 3])
    expect(parts[1]).toEqual(['x1', 'x2', 'x3'])
  })
  it('まとまりどうしが重ならない（外接円が離れている）・決定論', () => {
    const a = buildNetworkGraph(twoParts())
    const b = buildNetworkGraph(twoParts())
    layoutNetwork(a)
    layoutNetwork(b)
    a.forEachNode((id, attr) => {
      expect(attr.x).toBe(b.getNodeAttribute(id, 'x'))
      expect(attr.y).toBe(b.getNodeAttribute(id, 'y'))
    })
    const box = (ids: string[]) => {
      const xs = ids.map((id) => a.getNodeAttribute(id, 'x') as number)
      const ys = ids.map((id) => a.getNodeAttribute(id, 'y') as number)
      return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) }
    }
    const [big, small] = connectedParts(a).map(box)
    const apart = big.x1 < small.x0 || small.x1 < big.x0 || big.y1 < small.y0 || small.y1 < big.y0
    expect(apart).toBe(true)
  })
})

describe('visibleLegendRows', () => {
  const rows = Array.from({ length: 15 }, (_, i) => ({
    key: `k${i}`,
    name: `種類${i}`,
    count: 15 - i,
    role: REST_ROLE,
    pressed: i === 13,
  }))
  it('畳むと先頭の行と、その後ろで押してある行だけ（押した種類が凡例から消えない）', () => {
    expect(visibleLegendRows(rows, false, 12).map((r) => r.key)).toEqual([
      ...Array.from({ length: 12 }, (_, i) => `k${i}`),
      'k13',
    ])
  })
  it('広げると全部', () => {
    expect(visibleLegendRows(rows, true, 12)).toHaveLength(15)
  })
})
