import { describe, expect, it } from 'vitest'
import type { Alignment } from './crosswalkApi'
import type { DatasetRules, RuleMap } from './galleryApi'
import {
  layoutKindOverview,
  overviewStats,
  pickEndOfOverview,
  R_MAX,
  R_MIN,
  type OverviewInput,
} from './kindOverview'
import { focusOverview, layoutDatasetOverview } from './kindOverviewScale'
import { SV_NS } from './lineChoice'
import type { SharedTerm } from './vocabApi'

/** 共有のことばの帯（ADR upper-structure-shared-terms.md §2.5.1 2）: 標準の帯の上・決定論・
 *  データセット 0 件でも描く・上位への線は向きあり（≡ は両向き）・丸の大きさは子の件数の合計。 */

const NS = 'https://example.org/xrd#'
const STD = 'https://schema.org/Thing' // 既知の語彙
const sv = (slug: string) => `${SV_NS}${slug}`

const rmap = (id: string): RuleMap => ({
  id,
  subject: { template: `x:${id}/{k}`, classes: [`x:${id}`], class_iris: [`${NS}${id}`] },
  properties: [],
})
const rules = (maps: RuleMap[]): DatasetRules => ({ maps, prefixes: {}, warnings: [], labels: {} })
const ds = (id: string, name: string, maps: string[]) => ({
  id: `live-${id}`,
  apiId: id,
  name,
  rules: rules(maps.map(rmap)),
})
const xrd1 = ds('x1', 'XRD 実験', ['Record', 'Peak'])
const xrd2 = ds('x2', 'XRD 参照', ['Card'])

const term = (slug: string, over: Partial<SharedTerm> = {}): SharedTerm => ({
  iri: sv(slug),
  slug,
  kind: 'class',
  label: slug,
  label_en: null,
  comment: null,
  created_at: '2026-10-09T00:00:00Z',
  wired: true,
  answering_datasets: 2,
  narrower: [],
  standards: [],
  cqs: [],
  ...over,
})
const al = (source: string, target: string, relation: string): Alignment => ({
  alignment_iri: `a:${source}>${target}`,
  source,
  target,
  relation,
  from_perspective: '',
  to_perspective: '',
  at: '',
})

const base = (over: Partial<OverviewInput> = {}): OverviewInput => ({
  datasets: [xrd1, xrd2],
  unnamedHub: '名前のないつながり',
  classCountsByDataset: {
    'live-x1': { [`${NS}Record`]: 1000, [`${NS}Peak`]: 50 },
    'live-x2': { [`${NS}Card`]: 300 },
  },
  ...over,
})

describe('共有のことばの帯 — 配置は決定論', () => {
  const input = base({
    sharedTerms: [term('diffraction_point'), term('peak'), term('zeta')],
    alignments: [
      al(`${NS}Record`, sv('diffraction_point'), 'subClassOf'),
      al(`${NS}Card`, sv('diffraction_point'), 'subClassOf'),
      al(`${NS}Peak`, sv('peak'), 'equivalentClass'),
      al(sv('diffraction_point'), STD, 'equivalentClass'),
    ],
  })

  it('同じ入力は同じ座標（丸も帯も線も）', () => {
    const a = layoutKindOverview(input)
    const b = layoutKindOverview(structuredClone(input))
    expect(b.shared).toEqual(a.shared)
    expect(b.sharedBand).toEqual(a.sharedBand)
    expect(b.edges).toEqual(a.edges)
  })

  it('入力の語の順を変えても同じ並び（並びは線の重心と名前で決まる）', () => {
    const a = layoutKindOverview(input)
    const rev = layoutKindOverview({ ...input, sharedTerms: [...input.sharedTerms!].reverse() })
    expect(rev.shared!.map((s) => s.id)).toEqual(a.shared!.map((s) => s.id))
  })

  it('共有のことばの帯は標準の帯の上・本体（枠）の下', () => {
    const l = layoutKindOverview(input)
    expect(l.sharedBand).not.toBeNull()
    expect(l.band).not.toBeNull()
    const bodyBottom = Math.max(...l.frames.map((f) => f.y + f.h))
    expect(l.sharedBand!.y).toBeGreaterThan(bodyBottom)
    expect(l.sharedBand!.y + l.sharedBand!.h).toBeLessThanOrEqual(l.band!.y)
    for (const s of l.shared!) {
      expect(s.y - s.r).toBeGreaterThanOrEqual(l.sharedBand!.y)
      expect(s.y + s.r).toBeLessThanOrEqual(l.sharedBand!.y + l.sharedBand!.h)
    }
  })

  it('孤立の語（まだ線になっていない）も帯に出て、札のもとの orphan が立つ', () => {
    const l = layoutKindOverview(
      base({ sharedTerms: [term('lonely', { wired: false, answering_datasets: 0 })] }),
    )
    expect(l.shared!).toHaveLength(1)
    expect(l.shared![0].orphan).toBe(true)
  })
})

describe('データセットが 0 件でも帯は描く', () => {
  it('共有のことばだけで帯と丸が出る（枠・種類の丸は 0）', () => {
    const l = layoutKindOverview({
      datasets: [],
      unnamedHub: 'x',
      sharedTerms: [term('a'), term('b', { wired: false })],
    })
    expect(l.frames).toHaveLength(0)
    expect(l.circles).toHaveLength(0)
    expect(l.shared).toHaveLength(2)
    expect(l.sharedBand).toEqual({ x: 0, y: 0, w: l.width, h: expect.any(Number) })
    expect(l.height).toBe(l.sharedBand!.h)
    for (const s of l.shared!) {
      expect(s.x).toBeGreaterThan(0)
      expect(s.x).toBeLessThan(l.width)
    }
    expect(overviewStats(l).shared).toBe(2)
  })

  it('共有のことばも無ければ何も描かない（従来どおり）', () => {
    const l = layoutKindOverview({ datasets: [], unnamedHub: 'x' })
    expect(l.shared).toEqual([])
    expect(l.sharedBand).toBeNull()
    expect(l.band).toBeNull()
  })

  it('標準の語への ≡ だけでも、標準の帯が共有の帯の下に出る', () => {
    const l = layoutKindOverview({
      datasets: [],
      unnamedHub: 'x',
      sharedTerms: [term('a', { standards: [{ iri: STD, relation: 'equivalentClass' }] })],
    })
    expect(l.stds.map((s) => s.id)).toEqual([STD])
    expect(l.sharedBand!.y + l.sharedBand!.h).toBeLessThanOrEqual(l.band!.y)
    const e = l.edges.find((x) => x.to === STD)!
    expect(e.kind).toBe('upper')
    expect(e.both).toBe(true) // ≡ は両向き
  })
})

describe('上位への線（upper）は向きあり', () => {
  const l = layoutKindOverview(
    base({
      sharedTerms: [term('dp'), term('pk')],
      alignments: [
        al(`${NS}Record`, sv('dp'), 'subClassOf'),
        al(`${NS}Peak`, sv('pk'), 'equivalentClass'),
        al(sv('dp'), STD, 'subClassOf'),
      ],
    }),
  )
  const circleId = (name: string) => l.circles.find((c) => c.classIri === `${NS}${name}`)!.id

  it('種類の丸 → 共有のことばは ⊂ の向き（from=種類・to=共有・両向きでない）', () => {
    const e = l.edges.find((x) => x.kind === 'upper' && x.from === circleId('Record'))!
    expect(e.to).toBe(sv('dp'))
    expect(e.both).toBeUndefined()
    expect(e.relation).toBe('subClassOf')
  })

  it('≡ は両向き', () => {
    const e = l.edges.find((x) => x.kind === 'upper' && x.from === circleId('Peak'))!
    expect(e.both).toBe(true)
  })

  it('共有のことば → 標準の語も向きあり（⊂ の場合）', () => {
    const e = l.edges.find((x) => x.kind === 'upper' && x.from === sv('dp') && x.to === STD)!
    expect(e.both).toBeUndefined()
  })

  it('共有のことばを片端に持つ線は、向きのない対応（alignment）にも二重に出ない', () => {
    expect(l.edges.filter((x) => x.kind === 'alignment')).toHaveLength(0)
  })
})

describe('共有のことばの丸の大きさ = 子の件数の合計（派生値）', () => {
  it('掛かる種類の件数の合計。大きい方が大きい丸・件数のある子が無ければ最小', () => {
    const l = layoutKindOverview(
      base({
        sharedTerms: [term('big'), term('small'), term('none')],
        alignments: [
          al(`${NS}Record`, sv('big'), 'subClassOf'), // 1000
          al(`${NS}Card`, sv('big'), 'subClassOf'), // 300 → 計 1300
          al(`${NS}Peak`, sv('small'), 'subClassOf'), // 50
        ],
      }),
    )
    const by = (slug: string) => l.shared!.find((s) => s.slug === slug)!
    expect(by('big').count).toBe(1300)
    expect(by('small').count).toBe(50)
    expect(by('none').count).toBeUndefined()
    expect(by('big').kids).toBe(2)
    expect(by('big').r).toBe(R_MAX)
    expect(by('small').r).toBe(R_MIN)
    expect(by('none').r).toBe(R_MIN)
  })

  it('下の共有のことばが抱える種類も子の合計に入る（同じ丸は 1 回）', () => {
    const l = layoutKindOverview(
      base({
        sharedTerms: [term('top'), term('mid')],
        alignments: [
          al(`${NS}Record`, sv('mid'), 'subClassOf'),
          al(sv('mid'), sv('top'), 'subClassOf'),
          al(`${NS}Record`, sv('top'), 'subClassOf'), // 直接も掛かる — 二重に数えない
        ],
      }),
    )
    expect(l.shared!.find((s) => s.slug === 'top')!.count).toBe(1000)
    // 共有語どうしの線も描く（向きあり）
    const e = l.edges.find((x) => x.from === sv('mid') && x.to === sv('top'))!
    expect(e.kind).toBe('upper')
    expect(e.both).toBeUndefined()
  })

  it('循環があっても止まる', () => {
    const l = layoutKindOverview(
      base({
        sharedTerms: [term('p'), term('q')],
        alignments: [al(sv('p'), sv('q'), 'equivalentClass'), al(sv('q'), sv('p'), 'equivalentClass')],
      }),
    )
    expect(l.shared).toHaveLength(2)
  })
})

describe('既存画面の波及 — データセットの種類どうしの ⊂ は向きありの upper', () => {
  const inputWith = (relation: string) =>
    base({ alignments: [al(`${NS}Peak`, `${NS}Card`, relation)] })

  it('種類まで: ⊂ は 向きなしの「対応」ではなく upper（向きあり）', () => {
    const l = layoutKindOverview(inputWith('subClassOf'))
    expect(l.edges.filter((e) => e.kind === 'alignment')).toHaveLength(0)
    const up = l.edges.filter((e) => e.kind === 'upper')
    expect(up).toHaveLength(1)
    expect(up[0].both).toBeUndefined()
    expect(up[0].from).toBe(l.circles.find((c) => c.classIri === `${NS}Peak`)!.id)
  })

  it('種類まで: ≡ は従来どおり向きのない対応（両向き）', () => {
    const l = layoutKindOverview(inputWith('equivalentClass'))
    const al1 = l.edges.filter((e) => e.kind === 'alignment')
    expect(al1).toHaveLength(1)
    expect(al1[0].both).toBe(true)
    expect(l.edges.filter((e) => e.kind === 'upper')).toHaveLength(0)
  })

  it('データセットごと: ⊂ は向きありの upper・≡ は対応', () => {
    const sub = layoutDatasetOverview(inputWith('subClassOf'))
    expect(sub.edges.filter((e) => e.kind === 'upper')).toHaveLength(1)
    expect(sub.edges.filter((e) => e.kind === 'upper')[0].both).toBeUndefined()
    expect(sub.edges.filter((e) => e.kind === 'alignment')).toHaveLength(0)
    const eq = layoutDatasetOverview(inputWith('equivalentClass'))
    expect(eq.edges.filter((e) => e.kind === 'alignment')[0].both).toBe(true)
  })
})

describe('「データセットごと」の段では共有のことばを描かない', () => {
  it('sharedTerms があっても丸・帯を作らない（統計も 0）', () => {
    const l = layoutDatasetOverview(
      base({ sharedTerms: [term('dp')], alignments: [al(`${NS}Record`, sv('dp'), 'subClassOf')] }),
    )
    expect(l.shared ?? []).toHaveLength(0)
    expect(l.sharedBand ?? null).toBeNull()
    expect(overviewStats(l).shared).toBe(0)
  })
})

describe('周りだけ開く — 描いている種類につながる共有のことばだけ', () => {
  it('つながらない共有語は出さない', () => {
    const input = base({
      sharedTerms: [term('linked'), term('stray')],
      alignments: [al(`${NS}Record`, sv('linked'), 'subClassOf')],
    })
    expect(layoutKindOverview(input).shared!.map((s) => s.slug).sort()).toEqual(['linked', 'stray'])
    const f = focusOverview(input, { type: 'dataset', id: 'live-x1' })!
    expect(f.input.onlyLinkedShared).toBe(true)
    expect(layoutKindOverview(f.input).shared!.map((s) => s.slug)).toEqual(['linked'])
  })
})

describe('pickEndOfOverview — 地図の丸から線の端へ', () => {
  const l = layoutKindOverview(
    base({
      sharedTerms: [term('dp'), term('mass', { kind: 'property' })],
      alignments: [al(sv('dp'), STD, 'equivalentClass')],
    }),
  )
  it('種類の丸 / 共有のことば / 標準の語 の素性と kind を返す', () => {
    const c = l.circles[0]
    expect(pickEndOfOverview(l, c.id)).toMatchObject({ role: 'dataset', termKind: 'class', iri: c.classIri })
    expect(pickEndOfOverview(l, sv('mass'))).toMatchObject({ role: 'shared', termKind: 'property' })
    expect(pickEndOfOverview(l, STD)).toMatchObject({ role: 'standard' })
  })
  it('枠・ハブ・データセットごとの丸は選べない', () => {
    expect(pickEndOfOverview(l, 'live-x1')).toBeNull()
    expect(pickEndOfOverview(layoutDatasetOverview(base()), 'live-x1')).toBeNull()
  })
})
