import { describe, expect, it } from 'vitest'
import { crossingCount } from './edgeRoutingTestUtil'
import type { Alignment, CrosswalkPerspective } from './crosswalkApi'
import type { DatasetRules, RuleMap } from './galleryApi'
import {
  hubCountsOf,
  layoutKindOverview,
  routeAround,
  routedLine,
  ROUTE_MARGIN,
  sizeScale,
  compactCount,
  countInside,
  INSIDE_R,
  type OverviewInput,
} from './kindOverview'

// 架空の分野: 果樹園（orchard）と気象観測（weather）。
const NS = 'https://example.org/orchard#'
const rmap = (id: string, over: Partial<RuleMap> = {}): RuleMap => ({
  id,
  subject: { template: `o:${id}/{k}`, classes: [`o:${id}`], class_iris: [`${NS}${id}`] },
  properties: [],
  ...over,
})
const rules = (maps: RuleMap[], labels: Record<string, string> = {}): DatasetRules => ({
  maps,
  prefixes: {},
  warnings: [],
  labels,
})
const ds = (id: string, name: string, r: DatasetRules) => ({ id: `live-${id}`, apiId: id, name, rules: r })

const apple = ds('a1', '果樹園', rules([rmap('Tree'), rmap('Fruit')], { [`${NS}Tree`]: '木', [`${NS}Fruit`]: '実' }))
apple.rules.maps[0].properties.push({
  predicate: 'o:hasFruit',
  predicate_iri: `${NS}hasFruit`,
  label: '実',
  target_map: 'Fruit',
} as never)
const weather = ds('w1', '気象', rules([rmap('Station')]))
const third = ds('t1', '土壌', rules([rmap('Plot')]))

const base = (over: Partial<OverviewInput> = {}): OverviewInput => ({
  datasets: [apple, weather],
  unnamedHub: '名前のないつながり',
  ...over,
})

const hubPerspective = (participants: { dataset_id: string; subject_class?: string }[]): CrosswalkPerspective =>
  ({
    perspective_id: 'p1',
    display_name: '産地',
    config: {
      min_datasets: 2,
      concepts: [
        {
          name: 'origin',
          concept_label: '産地',
          class_iri: `${NS}Origin`,
          participants: participants.map((p) => ({ ...p, label: 'x' })),
        },
      ],
    },
    dataset: null,
  }) as CrosswalkPerspective

describe('sizeScale — 表示している中の最小〜最大で、面積が対数に比例', () => {
  it('最小→rMin・最大→rMax', () => {
    const f = sizeScale([10, 1000, 100000], 16, 52)
    expect(f(10)).toBe(16)
    expect(f(100000)).toBe(52)
  })
  it('中間は √t（面積が対数に比例）', () => {
    const f = sizeScale([0, 99, 9999], 16, 52) // 0 件は min に入れない
    const g = sizeScale([99, 9999], 16, 52)
    const t = (Math.log10(1000) - Math.log10(100)) / (Math.log10(10000) - Math.log10(100))
    expect(g(999)).toBe(Math.round(16 + 36 * Math.sqrt(t)))
    expect(f(undefined)).toBe(16)
  })
  it('全部同じ・1 つだけなら件数のある丸は中間', () => {
    const mid = Math.round(16 + 36 * Math.SQRT1_2)
    expect(sizeScale([500], 16, 52)(500)).toBe(mid)
    expect(sizeScale([500, 500, undefined], 16, 52)(500)).toBe(mid)
  })
  it('件数なし・0 件は rMin', () => {
    const f = sizeScale([undefined, 10, 1000], 16, 52)
    expect(f(undefined)).toBe(16)
    expect(f(0)).toBe(16)
    expect(sizeScale([], 16, 52)(undefined)).toBe(16)
  })
  it('頭打ちしない: 10・1 万・100 万で全部違い、単調に増える', () => {
    const f = sizeScale([10, 1e4, 1e6], 16, 52)
    const rs = [f(10), f(1e4), f(1e6)]
    expect(new Set(rs).size).toBe(3)
    expect(rs[0]).toBeLessThan(rs[1])
    expect(rs[1]).toBeLessThan(rs[2])
  })
  it('同じ入力は同じ結果（決定論）', () => {
    expect(sizeScale([3, 30, 300], 16, 52)(30)).toBe(sizeScale([300, 3, 30], 16, 52)(30))
  })
})

describe('compactCount — 件数の短い形と、丸の中／下', () => {
  it('ja は万、en は K/M', () => {
    expect(compactCount(1200000, 'ja')).toBe('120万')
    expect(compactCount(34000, 'ja')).toBe('3.4万')
    expect(compactCount(9820, 'ja')).toBe('9820')
    expect(compactCount(1200000, 'en')).toBe('1.2M')
    expect(compactCount(34000, 'en')).toBe('34K')
  })
  it('有効数字 3 桁で揃える（「1978.3万」のような形にしない）', () => {
    expect(compactCount(19783000, 'ja')).toBe('1980万')
    expect(compactCount(189992093, 'ja')).toBe('1.9億')
    expect(compactCount(19783000, 'en')).toBe('19.8M')
  })
  it('半径 22 以上は丸の中・未満は下', () => {
    expect(countInside(22)).toBe(true)
    expect(countInside(21)).toBe(false)
    expect(INSIDE_R).toBe(22)
  })
})

describe('layoutKindOverview — 丸と枠', () => {
  it('同じデータセットの同じ種類（同じ ID の作り方・種類名）は 1 つの丸に畳む', () => {
    const dup = ds('d1', 'ダブり', rules([rmap('Leaf'), rmap('Leaf2', { subject: rmap('Leaf').subject })]))
    const l = layoutKindOverview(base({ datasets: [dup] }))
    expect(l.circles).toHaveLength(1)
  })
  it('丸は件数の多い順・1 行に最大 4 つ', () => {
    const maps = ['A', 'B', 'C', 'D', 'E'].map((x) => rmap(x))
    const d = ds('m1', '多い', rules(maps))
    const counts = Object.fromEntries(maps.map((m, i) => [m.subject.class_iris![0], (i + 1) * 10]))
    const l = layoutKindOverview(base({ datasets: [d], classCountsByDataset: { 'live-m1': counts } }))
    expect(l.circles.map((c) => c.label)).toEqual(['E', 'D', 'C', 'B', 'A'])
    expect(new Set(l.circles.map((c) => c.y)).size).toBe(2)
    expect(l.circles.filter((c) => c.y === l.circles[0].y)).toHaveLength(4)
  })
  it('件数はデータセット単位。取れなければ全体の件数に落ちる', () => {
    const iri = `${NS}Station`
    const per = layoutKindOverview(
      base({ datasets: [weather], classCounts: { [iri]: 99 }, classCountsByDataset: { 'live-w1': { [iri]: 7 } } }),
    )
    expect(per.circles[0].count).toBe(7)
    const all = layoutKindOverview(base({ datasets: [weather], classCounts: { [iri]: 99 } }))
    expect(all.circles[0].count).toBe(99)
    expect(layoutKindOverview(base({ datasets: [weather] })).circles[0].count).toBeUndefined()
  })
  it('名前は既存の読み順（labels → classes の末尾 → map id）', () => {
    const l = layoutKindOverview(base({ datasets: [apple] }))
    expect(l.circles.map((c) => c.label).sort()).toEqual(['実', '木'])
  })
  it('種類どうしの線はデータセットの中だけ', () => {
    const l = layoutKindOverview(base({ datasets: [apple, weather] }))
    const links = l.edges.filter((e) => e.kind === 'link')
    expect(links).toHaveLength(1)
    expect(links[0].from).toBe('live-a1::Tree')
    expect(links[0].to).toBe('live-a1::Fruit')
  })
  it('データセットは入力順に左右交互・列は 3 つ', () => {
    const l = layoutKindOverview(base({ datasets: [apple, weather, third] }))
    const f = (id: string) => l.frames.find((x) => x.id === id)!
    expect(f('live-a1').x).toBe(0)
    expect(f('live-w1').x).toBeGreaterThan(f('live-a1').x + f('live-a1').w)
    expect(f('live-t1').x).toBe(0)
    expect(f('live-t1').y).toBeGreaterThan(f('live-a1').y)
  })
  it('決定論: 同じ入力は同じ結果', () => {
    const i = base({ datasets: [apple, weather, third] })
    expect(layoutKindOverview(i)).toEqual(layoutKindOverview(i))
  })
  it('枠の中の丸は枠からはみ出さない', () => {
    const l = layoutKindOverview(base({ datasets: [apple] }))
    const fr = l.frames[0]
    for (const c of l.circles) {
      expect(c.x - c.r).toBeGreaterThanOrEqual(fr.x)
      expect(c.x + c.r).toBeLessThanOrEqual(fr.x + fr.w)
      expect(c.y + c.r).toBeLessThanOrEqual(fr.y + fr.h)
    }
  })
})

describe('layoutKindOverview — ハブ', () => {
  const xw = [hubPerspective([{ dataset_id: 'a1', subject_class: `${NS}Tree` }, { dataset_id: 'w1' }])]
  it('ハブは概念ごとに 1 つ。名前は概念のラベル。大きさはハブのグラフの件数', () => {
    const l = layoutKindOverview(base({ crosswalks: xw, hubCounts: { [`${NS}Origin`]: 1000 } }))
    expect(l.hubs).toHaveLength(1)
    expect(l.hubs[0].label).toBe('産地')
    expect(l.hubs[0].r).toBe(sizeScale([1000], 16, 52)(1000))
    expect(l.hubs[0].count).toBe(1000)
  })
  it('件数が取れなければ最小', () => {
    const l = layoutKindOverview(base({ crosswalks: xw }))
    expect(l.hubs[0].r).toBe(16)
    expect(l.hubs[0].count).toBeUndefined()
  })
  it('subject_class が合う種類の丸から線を引き、無い参加者は枠から引く', () => {
    const l = layoutKindOverview(base({ crosswalks: xw }))
    const hubEdges = l.edges.filter((e) => e.kind === 'hub')
    expect(hubEdges.map((e) => e.from).sort()).toEqual(['live-a1::Tree', 'live-w1'])
  })
  it('登録 id が合わない参加者（描いていないデータセット）は線にしない', () => {
    const l = layoutKindOverview(base({ crosswalks: [hubPerspective([{ dataset_id: 'zzz' }])] }))
    expect(l.edges.filter((e) => e.kind === 'hub')).toHaveLength(0)
    expect(l.hubs).toHaveLength(1)
  })
  it('ハブは真ん中の列', () => {
    const l = layoutKindOverview(base({ crosswalks: xw }))
    const left = l.frames.find((f) => f.id === 'live-a1')!
    const right = l.frames.find((f) => f.id === 'live-w1')!
    expect(l.hubs[0].x - l.hubs[0].r).toBeGreaterThanOrEqual(left.x + left.w)
    expect(l.hubs[0].x + l.hubs[0].r).toBeLessThanOrEqual(right.x)
  })
  it('名前が無いハブは差し替えの語', () => {
    const p = hubPerspective([])
    p.config!.concepts[0].concept_label = undefined
    p.config!.concepts[0].name = 'shared_value_1'
    p.display_name = undefined
    const l = layoutKindOverview(base({ crosswalks: [p] }))
    expect(l.hubs[0].label).toBe('名前のないつながり')
  })
  it('hubCountsOf: 件数が切れたときは空（途中の件数を事実として描かない）', () => {
    expect(
      hubCountsOf({
        truncated: true,
        graphs: [{ graph: 'g2', dataset_id: null, hub: true, kinds: [{ class_iri: 'y', count: 3 }] }],
      }),
    ).toEqual({})
  })
  it('hubCountsOf: ハブのグラフだけ集める', () => {
    expect(
      hubCountsOf({
        truncated: false,
        graphs: [
          { graph: 'g1', dataset_id: 'a1', hub: false, kinds: [{ class_iri: 'x', count: 5 }] },
          { graph: 'g2', dataset_id: null, hub: true, kinds: [{ class_iri: 'y', count: 3 }] },
          { graph: 'g3', dataset_id: null, hub: true, kinds: [{ class_iri: 'y', count: 4 }] },
        ],
      }),
    ).toEqual({ y: 7 })
  })
})

describe('layoutKindOverview — 標準のことばと対応', () => {
  const std = 'https://schema.org/Place'
  const withStd = ds('s1', '場所', rules([rmap('Spot', { subject: { ...rmap('Spot').subject, class_iris: [std] } })]))
  it('既知の語彙の種類は下の帯の標準のことばへ（項目の標準語は描かない）', () => {
    const l = layoutKindOverview(base({ datasets: [withStd], standardNames: { [std]: '場所' } }))
    expect(l.stds).toHaveLength(1)
    expect(l.stds[0].label).toBe('場所')
    expect(l.edges.filter((x) => x.kind === 'standard')).toHaveLength(1)
    const circle = l.circles[0]
    expect(l.stds[0].y).toBeGreaterThan(circle.y + circle.r)
    expect(l.band!.y).toBeLessThan(l.stds[0].y)
  })
  it('標準が無ければ帯も出ない', () => {
    expect(layoutKindOverview(base()).band).toBeNull()
  })
  it('対応: 種類どうし・種類と標準の間だけ。項目どうしの対応は描かない', () => {
    const al = (source: string, target: string): Alignment => ({
      alignment_iri: `${source}>${target}`,
      source,
      target,
      relation: 'exactMatch',
      from_perspective: '',
      to_perspective: '',
      at: '',
    })
    const l = layoutKindOverview(
      base({
        datasets: [apple, weather],
        alignments: [
          al(`${NS}Tree`, `${NS}Station`),
          al(`${NS}hasFruit`, 'https://schema.org/name'),
          al(`${NS}Station`, 'https://schema.org/Place'),
        ],
      }),
    )
    const a = l.edges.filter((x) => x.kind === 'alignment')
    expect(a).toHaveLength(2)
    expect(a.every((x) => x.both)).toBe(true)
    expect(l.stds.map((s) => s.id)).toEqual(['https://schema.org/Place'])
  })

  it('対応: 同じ種類 IRI を複数のデータセットが名乗るときは全ての丸へ張る', () => {
    const stationA = ds('sa', 'A社', rules([rmap('Station')]))
    const stationB = ds('sb', 'B社', rules([rmap('Station')]))
    const l = layoutKindOverview(
      base({
        datasets: [stationA, stationB, apple],
        alignments: [
          {
            alignment_iri: 'x',
            source: `${NS}Station`,
            target: `${NS}Tree`,
            relation: 'exactMatch',
            from_perspective: '',
            to_perspective: '',
            at: '',
          },
        ],
      }),
    )
    const a = l.edges.filter((x) => x.kind === 'alignment')
    expect(a).toHaveLength(2)
  })
})

describe('routeAround — 線が別の丸の裏を通らないように曲げる', () => {
  const p = { x: 0, y: 0 }
  const q = { x: 236, y: 0 }
  const mid = { x: 118, y: 0, r: 30 }
  const curvePoints = (c: { cx: number; cy: number }) =>
    Array.from({ length: 25 }, (_, i) => {
      const t = i / 24
      return {
        x: (1 - t) * (1 - t) * p.x + 2 * t * (1 - t) * c.cx + t * t * q.x,
        y: (1 - t) * (1 - t) * p.y + 2 * t * (1 - t) * c.cy + t * t * q.y,
      }
    })
  it('障害物なし・離れた障害物は null（直線のまま）', () => {
    expect(routeAround(p, q, [])).toBeNull()
    expect(routeAround(p, q, [{ x: 118, y: 80, r: 30 }])).toBeNull()
  })
  it('報告の形（3 つの丸が一列）: 真ん中の丸から r + 余白以上離れる曲線を返す', () => {
    const c = routeAround(p, q, [mid])!
    expect(c).not.toBeNull()
    for (const pt of curvePoints(c)) expect(Math.hypot(pt.x - mid.x, pt.y - mid.y)).toBeGreaterThanOrEqual(mid.r + ROUTE_MARGIN)
  })
  it('決定論: 同じ入力は同じ制御点', () => {
    expect(routeAround(p, q, [mid])).toEqual(routeAround(p, q, [mid]))
  })
  it('向き: 最初に試す ＋ は進行方向の左（画面で上）。左が塞がれていれば右へ', () => {
    const c = routeAround(p, q, [{ ...mid, r: 8 }])!
    expect(c.cy).toBeLessThan(0)
    const blocked = routeAround(p, q, [mid, { x: 118, y: -40, r: 30 }])!
    expect(blocked.cy).toBeGreaterThan(0)
  })
  it('余白 6 は効いている: 余白なしなら通る丸（r=40 ・線から 34）でも、余白ありは頂点 56（k=4）まで曲げる', () => {
    // 余白 0 なら曲線は半径 40 の外（頂点 42 = k=3）でよいが、余白 6 では 46 以上が要る（頂点 56 = k=4・制御点は 2 倍の 112）
    const o = { x: 118, y: 0, r: 40 }
    const c = routeAround(p, q, [o])!
    expect(ROUTE_MARGIN).toBe(6)
    expect(c).toEqual({ cx: 118, cy: -112 })
    // 線分と丸の距離判定にも余白が効く（距離 34 は r=30 の外だが r+6=36 の内）
    expect(routeAround(p, q, [{ x: 118, y: 34, r: 30 }])).not.toBeNull()
    expect(routeAround(p, q, [{ x: 118, y: 36, r: 30 }])).toBeNull()
  })
  it('どうしても離れきらないときは余白が最大のものを採る（同点は試した順で先）', () => {
    const wall = [{ x: 118, y: 0, r: 30 }, ...Array.from({ length: 9 }, (_, i) => ({ x: 118, y: (i - 4) * 60, r: 200 }))]
    const c = routeAround(p, q, wall)!
    // 独立に全候補（+1, -1, +2, -2, … ×14）の余白を数える
    const need = wall.map((o) => ({ ...o, r: o.r + ROUTE_MARGIN }))
    const clearOf = (cy: number) => {
      let w = Infinity
      for (let i = 0; i <= 24; i++) {
        const t = i / 24
        const x = (1 - t) * (1 - t) * p.x + 2 * t * (1 - t) * 118 + t * t * q.x
        const y = (1 - t) * (1 - t) * p.y + 2 * t * (1 - t) * cy + t * t * q.y
        for (const o of need) w = Math.min(w, Math.hypot(x - o.x, y - o.y) - o.r)
      }
      return w
    }
    const cands: number[] = []
    for (let k = 1; k <= 8; k++) for (const sg of [1, -1]) cands.push(-sg * k * 14 * 2) // ＋ = 画面の上（cy が負）
    const clears = cands.map(clearOf)
    const max = Math.max(...clears)
    expect(max).toBeLessThan(0) // 離れきらない壁である
    expect(Math.min(...clears)).toBeLessThan(max) // 候補によって余白が違う（最大を選ぶ意味がある）
    expect(clearOf(c.cy)).toBe(max)
    // 同点は先に試した方: 壁は上下対称なので ＋ と − が同点 → 先の ＋（cy が負）
    expect(c.cy).toBe(cands[clears.indexOf(max)])
    expect(clears.filter((v) => v === max).length).toBeGreaterThan(1)
    expect(c.cy).toBeLessThan(0)
  })
})

describe('routedLine — 曲げたときの両端は制御点へ向かう縁', () => {
  const A = { type: 'circle' as const, x: 188, y: 94, r: 30 }
  const B = { type: 'circle' as const, x: 424, y: 94, r: 25 }
  it('障害物なし: 直線（制御点なし）で、両端は相手の中心へ向かう縁', () => {
    const l = routedLine(A, B, [])
    expect(l.cx).toBeUndefined()
    expect(l.cy).toBeUndefined()
    expect(l.x1).toBeCloseTo(218)
    expect(l.x2).toBeCloseTo(399)
  })
  it('障害物あり: 始点は A の縁・A の中心→制御点の向き、終点は B の縁・B の中心→制御点の向き', () => {
    const l = routedLine(A, B, [{ x: 306, y: 94, r: 28 }])
    expect(l.cx).toBeDefined()
    const cx = l.cx!
    const cy = l.cy!
    expect(Math.hypot(l.x1 - A.x, l.y1 - A.y)).toBeCloseTo(A.r)
    expect(Math.hypot(l.x2 - B.x, l.y2 - B.y)).toBeCloseTo(B.r)
    // 向きは制御点（相手の中心ではない）
    const dA = Math.hypot(cx - A.x, cy - A.y)
    expect((l.x1 - A.x) / A.r).toBeCloseTo((cx - A.x) / dA)
    expect((l.y1 - A.y) / A.r).toBeCloseTo((cy - A.y) / dA)
    const dB = Math.hypot(cx - B.x, cy - B.y)
    expect((l.x2 - B.x) / B.r).toBeCloseTo((cx - B.x) / dB)
    expect((l.y2 - B.y) / B.r).toBeCloseTo((cy - B.y) / dB)
    // 相手の中心向きなら y1 は 94 のまま。制御点向きなら 94 からずれる
    expect(Math.abs(l.y1 - 94)).toBeGreaterThan(1)
  })
})

describe('layoutKindOverview — 報告の形（xrd-cards）で線が別の丸を貫かない', () => {
  const X = 'https://example.org/xrd#'
  const m = (id: string, props: string[] = []) =>
    rmap(id, {
      subject: { template: `x:${id}/{k}`, classes: [`x:${id}`], class_iris: [`${X}${id}`] },
      properties: props.map((t) => ({ predicate: `x:has${t}`, predicate_iri: `${X}has${t}`, label: t, target_map: t }) as never),
    })
  const xrd = ds('x1', 'xrd-cards', rules([m('Card', ['ChemicalFormula', 'SpaceGroup', 'CrystalSystem']), m('ChemicalFormula'), m('SpaceGroup'), m('CrystalSystem'), m('Record')]))
  const counts = { [`${X}Card`]: 100000, [`${X}ChemicalFormula`]: 50000, [`${X}SpaceGroup`]: 20000, [`${X}CrystalSystem`]: 9000, [`${X}Record`]: 100 }
  it('どの線も端点でない丸の中を通らない（直線なら通る）', () => {
    const l = layoutKindOverview({ datasets: [xrd], classCounts: counts, unnamedHub: 'x' })
    expect(l.edges.filter((e) => e.kind === 'link')).toHaveLength(3)
    expect(crossingCount(l, false)).toBeGreaterThan(0)
    expect(crossingCount(l, true)).toBe(0)
    expect(l.edges.some((e) => e.cx != null)).toBe(true)
  })
})
