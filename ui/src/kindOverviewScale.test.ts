import { describe, expect, it } from 'vitest'
import type { Alignment, CrosswalkPerspective } from './crosswalkApi'
import type { DatasetRules, RuleMap } from './galleryApi'
import { layoutKindOverview, type OverviewInput } from './kindOverview'
import { crossingCount } from './edgeRoutingTestUtil'
import {
  chooseLevelByCounts,
  chooseOverviewLevel,
  collectHubs,
  countCrossings,
  edgeClassName,
  focusOverview,
  FOCUS_MAX_DATASETS,
  FOCUS_MAX_HUBS,
  KIND_LEVEL_MAX_DATASETS,
  KIND_LEVEL_MAX_KINDS,
  layoutDatasetOverview,
} from './kindOverviewScale'

// 架空の分野の作り物。規模の試験にも同じ作り方を使う。
const NS = 'https://example.org/scale#'
const rmap = (id: string): RuleMap => ({
  id,
  subject: { template: `o:${id}/{k}`, classes: [`o:${id}`], class_iris: [`${NS}${id}`] },
  properties: [],
})
const mkDs = (i: number, kinds: number) => ({
  id: `live-d${i}`,
  apiId: `d${i}`,
  name: `データセット${i}`,
  rules: {
    maps: Array.from({ length: kinds }, (_, k) => rmap(`d${i}K${k}`)),
    prefixes: {},
    warnings: [],
    labels: {},
  } as DatasetRules,
})

/** 種つきの擬似乱数（時刻・Math.random は使わない）。 */
function lcg(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

export function makeScale(nDs: number, nKinds: number, nHubs: number, perHub: number): OverviewInput {
  const rnd = lcg(42)
  const datasets = Array.from({ length: nDs }, (_, i) => mkDs(i, nKinds))
  const counts: Record<string, Record<string, number>> = {}
  for (const d of datasets) {
    counts[d.id] = {}
    for (const m of d.rules.maps) counts[d.id][m.subject.class_iris![0]] = Math.round(10 ** (1 + rnd() * 4))
  }
  const crosswalks: CrosswalkPerspective[] = Array.from({ length: nHubs }, (_, h) => {
    const picked = new Set<number>()
    while (picked.size < Math.min(perHub, nDs)) picked.add(Math.floor(rnd() * nDs))
    return {
      perspective_id: `p${h}`,
      display_name: `つながり${String(h).padStart(2, '0')}`,
      config: {
        min_datasets: 2,
        concepts: [
          {
            name: `c${h}`,
            concept_label: `概念${String(h).padStart(2, '0')}`,
            class_iri: `${NS}Hub${h}`,
            participants: [...picked].map((i) => ({
              dataset_id: `d${i}`,
              subject_class: `${NS}d${i}K${Math.floor(rnd() * nKinds)}`,
              label: 'x',
            })),
          },
        ],
      },
      dataset: null,
    } as unknown as CrosswalkPerspective
  })
  return { datasets, classCountsByDataset: counts, crosswalks, unnamedHub: '名前なし' }
}

describe('段の選び方', () => {
  it('閾値は 6 データセット・40 種類', () => {
    expect(KIND_LEVEL_MAX_DATASETS).toBe(6)
    expect(KIND_LEVEL_MAX_KINDS).toBe(40)
  })
  it('閾値ちょうどは種類まで・1 つ超えたらデータセットごと（両側）', () => {
    expect(chooseLevelByCounts(6, 40)).toBe('kind')
    expect(chooseLevelByCounts(7, 10)).toBe('dataset')
    expect(chooseLevelByCounts(3, 41)).toBe('dataset')
    expect(chooseLevelByCounts(0, 0)).toBe('kind')
  })
  it('入力から数える（種類は畳んだ後の数）', () => {
    expect(chooseOverviewLevel([mkDs(1, 8), mkDs(2, 8)])).toBe('kind')
    expect(chooseOverviewLevel([mkDs(1, 21), mkDs(2, 20)])).toBe('dataset')
    expect(chooseOverviewLevel(Array.from({ length: 7 }, (_, i) => mkDs(i, 1)))).toBe('dataset')
  })
})

describe('layoutDatasetOverview', () => {
  const input = makeScale(12, 3, 4, 4)
  it('決定論: 同じ入力は同じ座標', () => {
    expect(layoutDatasetOverview(input)).toEqual(layoutDatasetOverview(input))
  })
  it('データセット 1 つが丸 1 つ。大きさは件数の合計（上限 56）・種類の件数を持つ', () => {
    const l = layoutDatasetOverview(input)
    expect(l.level).toBe('dataset')
    expect(l.frames).toHaveLength(0)
    expect(l.circles).toHaveLength(12)
    for (const c of l.circles) {
      expect(c.kindCount).toBe(3)
      expect(c.r).toBeGreaterThanOrEqual(16)
      expect(c.r).toBeLessThanOrEqual(56)
    }
  })
  it('入力順に帯の上・下へ交互に割り振る（偶数番は上・奇数番は下）', () => {
    const l = layoutDatasetOverview(input)
    const band = l.hubBand!
    const c = (i: number) => l.circles.find((x) => x.id === `live-d${i}`)!
    for (let i = 0; i < 12; i++) {
      if (i % 2 === 0) expect(c(i).y).toBeLessThan(band.y)
      else expect(c(i).y).toBeGreaterThan(band.y + band.h)
    }
  })
  it('帯に近い行から詰める（先頭の行が帯に近い）', () => {
    const many = makeScale(60, 1, 2, 3)
    const l = layoutDatasetOverview(many, { width: 600 })
    const band = l.hubBand!
    const ys = l.circles.filter((c) => c.y < band.y).map((c) => c.y)
    expect(new Set(ys).size).toBeGreaterThan(1)
    // 上の段: 入力順の先頭（偶数番）の行ほど帯に近い（y が大きい）
    const first = l.circles.find((c) => c.id === 'live-d0')!
    const last = l.circles.find((c) => c.id === 'live-d58')!
    expect(first.y).toBeGreaterThan(last.y)
  })
  it('行は図の幅で折り返す（升の幅より狭ければ 1 列）', () => {
    const many = makeScale(40, 1, 1, 2)
    const l = layoutDatasetOverview(many, { width: 700 })
    const band = l.hubBand!
    const upRows = new Set(l.circles.filter((c) => c.y < band.y).map((c) => Math.round(c.y)))
    expect(upRows.size).toBeGreaterThan(1)
    for (const c of l.circles) expect(c.x - 56).toBeGreaterThanOrEqual(-1)
    const narrow = layoutDatasetOverview(many, { width: 100 })
    const xs = new Set(narrow.circles.map((c) => Math.round(c.x)))
    expect(xs.size).toBe(1)
  })
  it('つながりの無いデータセットは各段の最後（帯から遠い行）に回る', () => {
    const base = makeScale(8, 1, 1, 2)
    // つながるのは d0 と d1 だけにして、残りは無し
    base.crosswalks![0].config!.concepts![0].participants = [
      { dataset_id: 'd4', subject_class: `${NS}d4K0`, label: 'x' },
      { dataset_id: 'd5', subject_class: `${NS}d5K0`, label: 'x' },
    ] as never
    const l = layoutDatasetOverview(base, { width: 260 }) // 1 行 1 つ
    const band = l.hubBand!
    const up = l.circles.filter((c) => c.y < band.y).sort((a, b) => b.y - a.y) // 帯に近い順
    // 上の段は d0 d2 d4 d6。つながる d4 が先頭、無い d0 d2 d6 が後ろ（入力順）
    expect(up.map((c) => c.id)).toEqual(['live-d4', 'live-d0', 'live-d2', 'live-d6'])
  })
  it('線は データセット → ハブ を 1 本（何種類参加していても）・title 用に種類の数を持つ', () => {
    const one = makeScale(2, 3, 1, 2)
    one.crosswalks![0].config!.concepts![0].participants = [
      { dataset_id: 'd0', subject_class: `${NS}d0K0`, label: 'x' },
      { dataset_id: 'd0', subject_class: `${NS}d0K1`, label: 'x' },
      { dataset_id: 'd1', subject_class: `${NS}d1K0`, label: 'x' },
    ] as never
    const l = layoutDatasetOverview(one)
    const hubEdges = l.edges.filter((e) => e.kind === 'hub')
    expect(hubEdges).toHaveLength(2)
    expect(hubEdges.find((e) => e.from === 'live-d0')!.kinds).toBe(2)
    expect(hubEdges.find((e) => e.from === 'live-d1')!.kinds).toBe(1)
  })
  it('対応は両端のデータセットが違うものだけ・同じ組は 1 本の点線にまとめる', () => {
    const inp = makeScale(2, 3, 0, 0)
    const al = (source: string, target: string) => ({ source, target }) as unknown as Alignment
    inp.alignments = [
      al(`${NS}d0K0`, `${NS}d1K0`),
      al(`${NS}d0K1`, `${NS}d1K2`), // 同じ組 → まとめる
      al(`${NS}d1K1`, `${NS}d0K2`), // 逆向きも同じ組
      al(`${NS}d0K0`, `${NS}d0K1`), // 同じデータセットの中 → 描かない
    ]
    const l = layoutDatasetOverview(inp)
    const aligns = l.edges.filter((e) => e.kind === 'alignment')
    expect(aligns).toHaveLength(1)
    expect(aligns[0].both).toBe(true)
  })
  it('標準のことばは描かない・ハブが無ければ帯も出ない', () => {
    const l = layoutDatasetOverview(makeScale(8, 1, 0, 0))
    expect(l.stds).toHaveLength(0)
    expect(l.band).toBeNull()
    expect(l.hubBand).toBeNull()
  })
  it('交差の数は「重心で並べ替えない場合」より減る', () => {
    const inp = makeScale(50, 5, 15, 5)
    const before = countCrossings(layoutDatasetOverview(inp, { sweeps: 0 }).edges)
    const after = countCrossings(layoutDatasetOverview(inp).edges)
    expect(before).toBeGreaterThan(0)
    expect(after).toBeLessThan(before)
  })
  it('線は端点でない丸をよける（端点でない丸と交わる線: S 24 本 2→0・M 75 本 43→8・L 180 本 133→72）', () => {
    const measure = (nDs: number, nK: number, nH: number, per: number) => {
      const l = layoutDatasetOverview(makeScale(nDs, nK, nH, per))
      return { before: crossingCount(l, false), after: crossingCount(l, true), n: l.edges.length }
    }
    const S = measure(12, 3, 6, 4)
    const M = measure(50, 5, 15, 5)
    const L = measure(100, 20, 30, 6)
    expect(S.before).toBeGreaterThan(0)
    expect(S.after).toBe(0)
    expect(M.after).toBeLessThan(M.before)
    expect(L.after).toBeLessThan(L.before)
  })
  it('100 データセット・各 20 種類・ハブ 30 で高さは 1,500px 以内', () => {
    const l = layoutDatasetOverview(makeScale(100, 20, 30, 6))
    expect(l.height).toBeLessThanOrEqual(1500)
    expect(l.circles).toHaveLength(100)
    expect(l.hubs).toHaveLength(30)
  })
})

describe('countCrossings', () => {
  const e = (x1: number, y1: number, x2: number, y2: number, from: string, to: string) =>
    ({ from, to, kind: 'hub', x1, y1, x2, y2 }) as const
  it('交わる 2 本は 1 つ・離れた 2 本と端を共有する 2 本は 0', () => {
    expect(countCrossings([e(0, 0, 10, 10, 'a', 'b'), e(0, 10, 10, 0, 'c', 'd')])).toBe(1)
    expect(countCrossings([e(0, 0, 10, 0, 'a', 'b'), e(0, 5, 10, 5, 'c', 'd')])).toBe(0)
    expect(countCrossings([e(0, 0, 10, 10, 'a', 'b'), e(0, 10, 10, 0, 'a', 'd')])).toBe(0)
  })
})

describe('フォーカスの入力の絞り方', () => {
  // d0: K0,K1,K2 / d1: K0,K1,K2 / d2: K0,K1,K2。ハブ H1 = d0.K0 + d1.K1、ハブ H2 = d1.K2 + d2.K0
  const inp = makeScale(3, 3, 2, 2)
  inp.crosswalks![0].config!.concepts![0].participants = [
    { dataset_id: 'd0', subject_class: `${NS}d0K0`, label: 'x' },
    { dataset_id: 'd1', subject_class: `${NS}d1K1`, label: 'x' },
  ] as never
  inp.crosswalks![1].config!.concepts![0].participants = [
    { dataset_id: 'd1', subject_class: `${NS}d1K2`, label: 'x' },
    { dataset_id: 'd2', subject_class: `${NS}d2K0`, label: 'x' },
  ] as never
  const H1 = 'hub:p0:c0'
  it('collectHubs: 概念ごとに 1 つ・id は kind 図と同じ', () => {
    const hubs = collectHubs(inp)
    expect(hubs.map((h) => h.id)).toEqual([H1, 'hub:p1:c1'])
    expect(hubs[0].label).toBe('概念00')
    expect(hubs[0].parts.map((p) => p.datasetId)).toEqual(['live-d0', 'live-d1'])
  })
  it('データセットを押した: 自分の全部の種類・参加するハブ・他の参加者の参加している種類だけ', () => {
    const f = focusOverview(inp, { type: 'dataset', id: 'live-d0' })!
    expect(f.label).toBe('データセット0')
    expect(f.input.datasets.map((d) => d.id)).toEqual(['live-d0', 'live-d1'])
    expect(f.input.datasets[0].rules.maps).toHaveLength(3)
    expect(f.input.datasets[1].rules.maps.map((m) => m.id)).toEqual(['d1K1'])
    expect(f.input.crosswalks!.flatMap((p) => p.config!.concepts!.map((c) => c.name))).toEqual(['c0'])
    expect(f.input.omitted).toEqual({ 'live-d1': 2 })
  })
  it('ハブを押した: 参加する全部のデータセットの参加している種類だけ', () => {
    const f = focusOverview(inp, { type: 'hub', id: 'hub:p1:c1' })!
    expect(f.label).toBe('概念01')
    expect(f.input.datasets.map((d) => d.id)).toEqual(['live-d1', 'live-d2'])
    expect(f.input.datasets.map((d) => d.rules.maps.map((m) => m.id))).toEqual([['d1K2'], ['d2K0']])
    expect(f.input.omitted).toEqual({ 'live-d1': 2, 'live-d2': 2 })
  })
  it('絞った入力を配置に渡すと、枠に「ほか N 種類」の数が付く', () => {
    const f = focusOverview(inp, { type: 'dataset', id: 'live-d0' })!
    const l = layoutKindOverview(f.input)
    expect(l.frames.find((x) => x.id === 'live-d1')!.omitted).toBe(2)
    expect(l.frames.find((x) => x.id === 'live-d0')!.omitted).toBeUndefined()
    expect(l.hubs).toHaveLength(1)
  })
  it('参加者が多くても周りの図は窓に収まる（他は FOCUS_MAX_DATASETS 個・ハブは FOCUS_MAX_HUBS 個まで）', () => {
    const big = makeScale(100, 20, 30, 30)
    const d = focusOverview(big, { type: 'dataset', id: 'live-d0' })!
    expect(d.input.datasets.length).toBeLessThanOrEqual(FOCUS_MAX_DATASETS + 1)
    expect(d.input.crosswalks!.length).toBeLessThanOrEqual(FOCUS_MAX_HUBS)
    expect(d.hiddenHubs).toBeGreaterThanOrEqual(0)
    const dl = layoutKindOverview(d.input)
    expect(dl.height).toBeLessThan(2500)
    const h = focusOverview(big, { type: 'hub', id: 'hub:p0:c0' })!
    expect(h.input.datasets).toHaveLength(FOCUS_MAX_DATASETS)
    expect(h.hiddenDatasets).toBe(30 - FOCUS_MAX_DATASETS)
    expect(layoutKindOverview(h.input).height).toBeLessThan(2500)
  })
  it('省いた数は上限以内なら 0', () => {
    const f = focusOverview(inp, { type: 'dataset', id: 'live-d0' })!
    expect(f.hiddenDatasets).toBe(0)
    expect(f.hiddenHubs).toBe(0)
  })
  it('存在しない id は null', () => {
    expect(focusOverview(inp, { type: 'dataset', id: 'nope' })).toBeNull()
    expect(focusOverview(inp, { type: 'hub', id: 'hub:x:y' })).toBeNull()
  })
})

describe('線の強弱の className', () => {
  it('何も載せていなければ今の濃さ（強弱の class なし）', () => {
    expect(edgeClassName('hub', 'a', 'b', null)).toBe('kind-ov-edge kind-ov-edge--hub')
  })
  it('載せた丸につながる線は濃く・他は薄く', () => {
    expect(edgeClassName('hub', 'a', 'h', 'a')).toBe('kind-ov-edge kind-ov-edge--hub is-hot')
    expect(edgeClassName('hub', 'a', 'h', 'h')).toBe('kind-ov-edge kind-ov-edge--hub is-hot')
    expect(edgeClassName('link', 'x', 'y', 'a')).toBe('kind-ov-edge kind-ov-edge--link is-dim')
  })
})
