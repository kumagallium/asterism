import { describe, expect, it } from 'vitest'
import { SAMPLE_SKY } from './sample'
import { EMPTY_SKY, type SkyData } from './skyApi'
import { makeSky } from './skyFixture'
import {
  chooseMode,
  hash01,
  kindId,
  layoutSky,
  MID_TOP0,
  neighborhood,
  nodeIdsForIris,
  orderedDatasets,
  PAD,
  placeLanes,
  STAR_MAX_DATASETS,
  STAR_MAX_KINDS,
  termId,
  type KindNode,
  type SkyLayout,
} from './skyLayout'

const positions = (l: SkyLayout) =>
  [...l.nodes.values()].map((n) => `${n.id}@${n.x.toFixed(3)},${n.y.toFixed(3)}`).sort()

describe('hash01', () => {
  it('同じ入力は同じ値・0 以上 1 未満', () => {
    expect(hash01('abc', 'angle')).toBe(hash01('abc', 'angle'))
    expect(hash01('abc', 'angle')).not.toBe(hash01('abc', 'radius'))
    for (const s of ['', 'a', '種類', 'https://example.org/x#Y']) {
      const v = hash01(s, 'x')
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThan(1)
    }
  })
})

describe('chooseMode — 星のままにする上限', () => {
  it('閾値は 種類 60・データセット 8', () => {
    expect(STAR_MAX_KINDS).toBe(60)
    expect(STAR_MAX_DATASETS).toBe(8)
  })
  it('ちょうどは星・1 つ超えたら丸（両側）', () => {
    expect(chooseMode(makeSky(8, 60))).toBe('stars')
    expect(chooseMode(makeSky(9, 20))).toBe('circles')
    expect(chooseMode(makeSky(3, 61))).toBe('circles')
    expect(chooseMode(EMPTY_SKY)).toBe('stars')
  })
})

describe('layoutSky — 決定論', () => {
  it('同じデータ・同じ幅は同じ絵', () => {
    const a = layoutSky(makeSky(5, 24), { width: 1000 })
    const b = layoutSky(makeSky(5, 24), { width: 1000 })
    expect(positions(a)).toEqual(positions(b))
    expect(a.height).toBe(b.height)
  })
  it('入力の並びに依らない（データセットは作成順に並べ直す）', () => {
    const sky = makeSky(5, 24)
    const shuffled: SkyData = { ...sky, datasets: [...sky.datasets].reverse() }
    expect(positions(layoutSky(shuffled, { width: 1000 }))).toEqual(positions(layoutSky(sky, { width: 1000 })))
  })
  it('作成の古い順（同時刻は id）に並べる', () => {
    const sky = makeSky(3, 6)
    const d = orderedDatasets({ ...sky, datasets: [sky.datasets[2], sky.datasets[0], sky.datasets[1]] })
    expect(d.map((x) => x.id)).toEqual(['d000', 'd001', 'd002'])
  })
})

describe('layoutSky — 増分配置', () => {
  it('新しいデータセットを末尾に足しても、既存の星は動かない', () => {
    const sky = makeSky(6, 30)
    const before = layoutSky(sky, { width: 1000 })
    const extra = makeSky(7, 35).datasets[6]
    const grown: SkyData = {
      ...sky,
      datasets: [...sky.datasets, { ...extra, id: 'dNEW', created_at: '2027-01-01T00:00:00Z' }],
    }
    const after = layoutSky(grown, { width: 1000 })
    let checked = 0
    for (const n of before.nodes.values()) {
      if (n.type !== 'kind') continue
      const m = after.nodes.get(n.id) as KindNode
      expect(m).toBeDefined()
      expect(m.x).toBeCloseTo(n.x, 6)
      // 用語の帯の段数が増えると基準 midTop が動くので、基準からの相対で比べる
      expect(m.y - after.bands.midTop).toBeCloseTo(n.y - before.bands.midTop, 6)
      checked++
    }
    expect(checked).toBe(30)
    // 星座の箱も同じ
    for (const c of before.clusters) {
      const c2 = after.clusters.find((x) => x.datasetId === c.datasetId)!
      expect(c2.cx).toBeCloseTo(c.cx, 6)
      expect(c2.cy - after.bands.midTop).toBeCloseTo(c.cy - before.bands.midTop, 6)
    }
  })
})

describe('layoutSky — モックと同じ寸法（1 行・幅 1000）', () => {
  const l = layoutSky(SAMPLE_SKY, { width: 1000 })
  it('帯の位置が参考実装（H=620）と同じ', () => {
    expect(l.bands.midTop).toBe(MID_TOP0)
    expect(l.bands.midBot).toBe(446)
    expect(l.height).toBe(620)
    const std = [...l.nodes.values()].find((n) => n.type === 'std')!
    expect(std.y).toBe(577)
    const hub = [...l.nodes.values()].find((n) => n.type === 'hub')!
    expect(hub.y).toBe(482)
    const term = [...l.nodes.values()].find((n) => n.type === 'term')!
    expect(term.y).toBe(99)
  })
  it('星は星座の箱の中に収まる', () => {
    for (const c of l.clusters) {
      for (const id of c.nodeIds) {
        const n = l.nodes.get(id)!
        expect(Math.abs(n.x - c.cx)).toBeLessThanOrEqual(c.w / 2 + 1e-6)
        expect(Math.abs(n.y - c.cy)).toBeLessThanOrEqual(c.h / 2 + 1e-6)
      }
    }
  })
  it('用語は入っている星の重心の上に置かれる（近いものは押し広げる）', () => {
    const t = l.nodes.get(termId('sample:recipe'))!
    const members = [kindId('menu', 'sample:menu-recipe'), kindId('pantry', 'sample:pantry-recipe')].map((i) => l.nodes.get(i)!)
    const cx = members.reduce((s, n) => s + n.x, 0) / members.length
    expect(Math.abs(t.x - cx)).toBeLessThan(80)
  })
  it('星の無い用語は member の線を持たない', () => {
    const t = l.nodes.get(termId('sample:cooking-method'))
    expect(t?.type).toBe('term')
    expect(l.edges.some((e) => e.kind === 'member' && e.to === termId('sample:cooking-method'))).toBe(false)
  })
})

describe('layoutSky — 空・端の場合', () => {
  it('データ 0 でも帯は潰れない（高さ 620・データの帯 260）', () => {
    const l = layoutSky(EMPTY_SKY, { width: 900 })
    expect(l.height).toBe(620)
    expect(l.bands.midBot - l.bands.midTop).toBe(260)
    expect(l.nodes.size).toBe(0)
  })
  it('種類が 0 のデータセットは箱だけ出る', () => {
    const sky: SkyData = {
      ...EMPTY_SKY,
      datasets: [{ id: 'a', label: 'A', created_at: '2026-01-01T00:00:00Z', kinds: [] }],
    }
    const l = layoutSky(sky, { width: 900 })
    expect(l.clusters).toHaveLength(1)
    expect(l.nodes.size).toBe(0)
  })
  it('幅の下限は 320', () => {
    expect(layoutSky(makeSky(2, 4), { width: 100 }).width).toBe(320)
  })
  it('標準用語が無い IRI を指す種類は標準用語の線を引かない', () => {
    const sky = makeSky(2, 4)
    sky.datasets[0].kinds[0].standard = 'https://example.org/none'
    const l = layoutSky(sky, { width: 900 })
    expect(l.edges.some((e) => e.kind === 'kindStd' && e.to === 's|https://example.org/none')).toBe(false)
  })
  it('英語の画面は用語の英語名で置く', () => {
    const l = layoutSky(makeSky(2, 6), { width: 900, lang: 'en' })
    const t = [...l.nodes.values()].find((n) => n.type === 'term')!
    expect(t.label.startsWith('Term ')).toBe(true)
  })
})

describe('layoutSky — 丸モード', () => {
  const sky = makeSky(12, 36)
  const l = layoutSky(sky, { width: 1000 })
  it('データセットが丸になる（星は出さない）', () => {
    expect(l.mode).toBe('circles')
    expect([...l.nodes.values()].some((n) => n.type === 'kind')).toBe(false)
    expect([...l.nodes.values()].filter((n) => n.type === 'dataset')).toHaveLength(12)
    expect(l.clusters).toHaveLength(0)
  })
  it('用語は丸につながる（同じ丸に 2 本は引かない）', () => {
    const per = new Map<string, number>()
    for (const e of l.edges) if (e.kind === 'member') per.set(`${e.from}>${e.to}`, (per.get(`${e.from}>${e.to}`) ?? 0) + 1)
    expect([...per.values()].every((v) => v === 1)).toBe(true)
    expect(per.size).toBeGreaterThan(0)
  })
  it('新しいデータセットを足しても既存の丸は動かない', () => {
    const extra = makeSky(13, 39).datasets[12]
    const grown: SkyData = { ...sky, datasets: [...sky.datasets, { ...extra, id: 'dNEW', created_at: '2027-01-01T00:00:00Z' }] }
    const after = layoutSky(grown, { width: 1000 })
    for (const n of l.nodes.values()) {
      if (n.type !== 'dataset') continue
      const m = after.nodes.get(n.id)!
      expect(m.x).toBeCloseTo(n.x, 6)
      expect(m.y - after.bands.midTop).toBeCloseTo(n.y - l.bands.midTop, 6)
    }
  })
})

describe('placeLanes — 帯の札は重ならない', () => {
  it('同じ位置に集まっても段が増えて重ならず、枠内に収まる', () => {
    const items = Array.from({ length: 30 }, (_, i) => ({ id: `i${i}`, x: 500, w: 120 }))
    const { placed, lanes } = placeLanes(items, 1000)
    expect(lanes).toBeGreaterThan(1)
    const byLane = new Map<number, { l: number; r: number }[]>()
    for (const p of placed) {
      expect(p.x - 60).toBeGreaterThanOrEqual(PAD - 1e-6)
      expect(p.x + 60).toBeLessThanOrEqual(1000 - PAD + 1e-6)
      const arr = byLane.get(p.lane) ?? []
      for (const o of arr) expect(p.x - 60 >= o.r || p.x + 60 <= o.l).toBe(true)
      arr.push({ l: p.x - 60, r: p.x + 60 })
      byLane.set(p.lane, arr)
    }
  })
  it('空いていれば 1 段のまま置きたい位置に置く', () => {
    const { placed, lanes } = placeLanes(
      [
        { id: 'a', x: 200, w: 100 },
        { id: 'b', x: 600, w: 100 },
      ],
      1000,
    )
    expect(lanes).toBe(1)
    expect(placed.map((p) => p.x)).toEqual([200, 600])
  })
  it('入力の順に依らない', () => {
    const items = [
      { id: 'a', x: 300, w: 120 },
      { id: 'b', x: 310, w: 120 },
      { id: 'c', x: 305, w: 120 },
    ]
    const x = placeLanes(items, 900)
    const y = placeLanes([...items].reverse(), 900)
    const key = (r: typeof x) => r.placed.map((p) => `${p.id}:${p.x}:${p.lane}`).sort()
    expect(key(x)).toEqual(key(y))
  })
})

describe('neighborhood / nodeIdsForIris', () => {
  const l = layoutSky(SAMPLE_SKY, { width: 1000 })
  it('星: 自分・星座の中のつながり・入っている用語・その標準用語', () => {
    const near = neighborhood(l, kindId('samples', 'sample:sample'))
    expect(near.has(kindId('samples', 'sample:project'))).toBe(true)
    expect(near.has(termId('sample:sample'))).toBe(true)
    expect(near.has(termId('sample:composition'))).toBe(true)
    expect(near.has('s|sample:cmso-material')).toBe(true)
    expect(near.has(kindId('papers', 'sample:paper'))).toBe(false)
  })
  it('用語: 入っている星と標準用語', () => {
    const near = neighborhood(l, termId('sample:recipe'))
    expect(near.has(kindId('menu', 'sample:menu-recipe'))).toBe(true)
    expect(near.has(kindId('pantry', 'sample:pantry-recipe'))).toBe(true)
    expect(near.size).toBe(3)
  })
  it('標準用語: 結ばれている用語と種類', () => {
    const near = neighborhood(l, 's|sample:qudt-qk')
    expect(near.has(kindId('curves', 'sample:property'))).toBe(true)
  })
  it('ハブ: 参加している星', () => {
    const near = neighborhood(l, 'h|composition')
    expect(near.has(kindId('samples', 'sample:sample'))).toBe(true)
    expect(near.has(kindId('pantry', 'sample:pantry-amount'))).toBe(true)
  })
  it('無い id は空', () => {
    expect(neighborhood(l, 'nope').size).toBe(0)
    expect(neighborhood(l, null).size).toBe(0)
  })
  it('IRI から点の id を引く（種類・用語。丸モードではその種類を持つ丸）', () => {
    expect(nodeIdsForIris(l, ['sample:pantry-recipe']).sort()).toEqual([kindId('pantry', 'sample:pantry-recipe')])
    expect(nodeIdsForIris(l, ['sample:composition'])).toEqual([termId('sample:composition')])
    const sky = makeSky(12, 36)
    const c = layoutSky(sky, { width: 1000 })
    const iri = sky.datasets[3].kinds[0].iri
    expect(nodeIdsForIris(c, [iri])).toContain('d|d003')
  })
})
