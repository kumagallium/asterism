import { describe, expect, it } from 'vitest'
import { SAMPLE_SKY } from './sample'
import { EMPTY_SKY, type DraftSky, type SkyData, type SkyLine } from './skyApi'
import { makeSky } from './skyFixture'
import { KNOW_PITCH } from './skyKnowledge'
import {
  chooseMode,
  conceptEdgeGeom,
  conceptEdgePointAt,
  conceptPillPos,
  draftCardConcepts,
  draftEdgeId,
  draftNodeId,
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
  type DraftNode,
  type KindNode,
  type TermNode,
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

describe('提案の札（上の帯・薄い塗り）', () => {
  const thermal: DraftSky = {
    draft_id: 'thermo',
    theme_label: '熱電材料',
    concepts: [
      { key: 'c1', label: '熱伝導率', kind: 'property', use: 'standard:quantitykind:ThermalConductivity' },
      { key: 'c2', label: '格子熱伝導率', kind: 'property', use: 'new' },
      { key: 'c4', label: '粒界散乱', kind: 'class', use: 'new' },
      { key: 'c7', label: '試料', kind: 'class', use: 'existing:sample' },
    ],
    relations: [{ from: 'c2', to: 'c1', rel: 'part' }],
    questions: ['熱伝導率を下げる要素になり得るのは？'],
  }

  it('既にある語は札にしない（新しく作る語・標準の語と結ぶ語だけ）', () => {
    expect(draftCardConcepts(thermal).map((c) => c.key)).toEqual(['c1', 'c2', 'c4'])
  })

  it('データが 0 件でも描く（上の帯に重ならずに並び、図の中に収まる）', () => {
    const l = layoutSky(EMPTY_SKY, { width: 960, drafts: [thermal] })
    const cards = [...l.nodes.values()].filter((n): n is DraftNode => n.type === 'draft')
    expect(cards.map((n) => n.id).sort()).toEqual(
      ['c1', 'c2', 'c4'].map((k) => draftNodeId('thermo', k)).sort(),
    )
    for (const n of cards) {
      expect(n.themeLabel).toBe('熱電材料')
      expect(n.conceptCount).toBe(3)
      expect(n.x - n.w / 2).toBeGreaterThanOrEqual(0)
      expect(n.x + n.w / 2).toBeLessThanOrEqual(l.width)
      expect(n.y).toBeLessThan(l.bands.midTop)
    }
    for (const a of cards) {
      for (const b of cards) {
        if (a.id === b.id || a.lane !== b.lane) continue
        expect(Math.abs(a.x - b.x)).toBeGreaterThanOrEqual((a.w + b.w) / 2)
      }
    }
  })

  it('下書きが無ければ今までと同じ配置（見本・本物の札を動かさない）', () => {
    const sky = makeSky(3, 10)
    expect(positions(layoutSky(sky, { width: 960, drafts: [] }))).toEqual(positions(layoutSky(sky, { width: 960 })))
  })

  it('決定論で、本物の用語の札とも重ならない', () => {
    const sky = makeSky(3, 12)
    const a = layoutSky(sky, { width: 960, drafts: [thermal] })
    expect(positions(layoutSky(sky, { width: 960, drafts: [thermal] }))).toEqual(positions(a))
    const band = [...a.nodes.values()].filter((n) => n.type === 'term' || n.type === 'draft') as (
      | DraftNode
      | { type: 'term'; id: string; x: number; w: number; lane: number }
    )[]
    for (const p of band) {
      for (const q of band) {
        if (p.id === q.id || p.lane !== q.lane) continue
        expect(Math.abs(p.x - q.x)).toBeGreaterThanOrEqual((p.w + q.w) / 2)
      }
    }
  })
})

describe('提案の線（薄い線・図に入れると濃くなる）', () => {
  const lowers: DraftSky = {
    draft_id: 'thermo',
    theme_label: '熱電材料',
    concepts: [
      { key: 'c1', label: '熱伝導率', kind: 'property', use: 'new', state: 'solid' },
      { key: 'c2', label: '格子熱伝導率', kind: 'property', use: 'new', state: 'solid' },
      { key: 'c4', label: '粒界散乱', kind: 'class', use: 'new', state: 'thin' },
      { key: 'c5', label: '点欠陥散乱', kind: 'class', use: 'new', state: 'remove' },
    ],
    relations: [
      { from: 'c2', to: 'c1', rel: 'part', state: 'solid', index: 0 },
      { from: 'c4', to: 'c2', rel: 'lowers', state: 'thin', index: 1 },
      { from: 'c5', to: 'c1', rel: 'lowers', state: 'thin', index: 2 },
    ],
    questions: [],
  }
  const cardsOf = (l: SkyLayout) => [...l.nodes.values()].filter((n): n is DraftNode => n.type === 'draft')
  const draftEdges = (l: SkyLayout) => l.edges.filter((e) => e.kind === 'draft')

  it('札は状態（提案・図に入れた・外す提案）を持ち、状態の無い札（開いていない下書き）は提案', () => {
    const l = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    const st = Object.fromEntries(cardsOf(l).map((n) => [n.conceptKey, n.state]))
    expect(st).toEqual({ c1: 'solid', c2: 'solid', c4: 'thin', c5: 'remove' })
    const bare = layoutSky(EMPTY_SKY, { width: 960, drafts: [{ ...thermalBare }] })
    expect(cardsOf(bare).every((n) => n.state === 'thin' && !n.proposed)).toBe(true)
  })

  it('両端の札がある関係は kind=draft の線になり、道すじ（geom）と関係・状態・添字を持つ', () => {
    const l = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    const es = draftEdges(l)
    expect(es).toHaveLength(3)
    for (const e of es) {
      expect(e.geom).toBeDefined()
      expect(e.draft?.draftId).toBe('thermo')
    }
    expect(es.map((e) => [e.draft?.fromKey, e.draft?.rel, e.draft?.toKey, e.draft?.state, e.draft?.index])).toEqual([
      ['c2', 'part', 'c1', 'solid', 0],
      ['c4', 'lowers', 'c2', 'thin', 1],
      ['c5', 'lowers', 'c1', 'thin', 2],
    ])
    expect(draftEdgeId(es[1].draft!)).toBe('thermo|c4|lowers|c2')
  })

  it('原因・一部は結果・全体の下の段に並ぶ（線 from → to は from が下）', () => {
    const l = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    const y = (k: string) => l.nodes.get(draftNodeId('thermo', k))!.y
    expect(y('c2')).toBeGreaterThan(y('c1'))
    expect(y('c4')).toBeGreaterThan(y('c2'))
    expect(y('c5')).toBeGreaterThan(y('c1'))
  })

  it('標準の語（curie）・無い概念の端の線は足さない', () => {
    const d: DraftSky = {
      ...lowers,
      relations: [
        { from: 'c1', to: 'quantitykind:ThermalConductivity', rel: 'quantity', state: 'solid', index: 0 },
        { from: 'c1', to: 'zzz', rel: 'lowers', state: 'solid', index: 1 },
      ],
    }
    expect(draftEdges(layoutSky(EMPTY_SKY, { width: 960, drafts: [d] }))).toHaveLength(0)
  })

  it('既にある用語の札（existing:<slug>）の端は、その用語の札につながる', () => {
    const base = makeSky(3, 12)
    const t = base.terms[0]
    const d: DraftSky = {
      draft_id: 'x',
      theme_label: 'x',
      concepts: [
        { key: 'a', label: '新しい因子', kind: 'class', use: 'new', state: 'thin' },
        { key: 'b', label: t.label, kind: t.kind, use: `existing:${t.slug}`, state: 'solid' },
      ],
      relations: [{ from: 'a', to: 'b', rel: 'lowers', state: 'thin', index: 0 }],
      questions: [],
    }
    const l = layoutSky(base, { width: 1000, drafts: [d] })
    const e = draftEdges(l)
    expect(e).toHaveLength(1)
    expect(e[0].from).toBe(draftNodeId('x', 'a'))
    expect(e[0].to).toBe(termId(t.iri))
    // 既にある語の札は増えない
    expect(cardsOf(l).map((n) => n.conceptKey)).toEqual(['a'])
  })

  it('既にある用語どうしの提案の線が、本物の線と同じ関係なら薄い線は二重に引かない（違う関係・別の向きは引く）', () => {
    const base = makeSky(3, 12)
    const [a, b] = base.terms
    const sky: SkyData = { ...base, edges: [{ from: a.iri, to: b.iri, relation: 'lowers', reason: null }] }
    const mk = (rel: string, from = 'a', to = 'b', state: 'thin' | 'solid' = 'thin'): DraftSky => ({
      draft_id: 'x',
      theme_label: 'x',
      concepts: [
        { key: 'a', label: a.label, kind: a.kind, use: `existing:${a.slug}`, state: 'solid' },
        { key: 'b', label: b.label, kind: b.kind, use: `existing:${b.slug}`, state: 'solid' },
      ],
      relations: [{ from, to, rel, state, index: 0 }],
      questions: [],
    })
    expect(draftEdges(layoutSky(sky, { width: 1000, drafts: [mk('lowers')] }))).toHaveLength(0)
    expect(draftEdges(layoutSky(sky, { width: 1000, drafts: [mk('raises')] }))).toHaveLength(1)
    expect(draftEdges(layoutSky(sky, { width: 1000, drafts: [mk('lowers', 'b', 'a')] }))).toHaveLength(1)
  })

  it('図に入れた（solid）線は、本物の線と同じ関係でも引く（本物の線はそのまま残る・星図から選べる）', () => {
    const base = makeSky(3, 12)
    const [a, b] = base.terms
    const sky: SkyData = { ...base, edges: [{ from: a.iri, to: b.iri, relation: 'lowers', reason: null }] }
    const d: DraftSky = {
      draft_id: 'x',
      theme_label: 'x',
      concepts: [
        { key: 'a', label: a.label, kind: a.kind, use: `existing:${a.slug}`, state: 'solid' },
        { key: 'b', label: b.label, kind: b.kind, use: `existing:${b.slug}`, state: 'solid' },
      ],
      relations: [{ from: 'a', to: 'b', rel: 'lowers', state: 'solid', index: 0 }],
      questions: [],
    }
    const l = layoutSky(sky, { width: 1000, drafts: [d] })
    expect(draftEdges(l)).toHaveLength(1)
    expect(l.edges.filter((e) => e.kind === 'concept')).toHaveLength(1)
  })

  it('別の下書きの同じ線は別の線（先に並んだ下書きが、後ろの下書きの線を消さない）', () => {
    const base = makeSky(3, 12)
    const [a, b] = base.terms
    const mk = (id: string): DraftSky => ({
      draft_id: id,
      theme_label: id,
      concepts: [
        { key: 'a', label: a.label, kind: a.kind, use: `existing:${a.slug}`, state: 'solid' },
        { key: 'b', label: b.label, kind: b.kind, use: `existing:${b.slug}`, state: 'solid' },
      ],
      relations: [{ from: 'a', to: 'b', rel: 'lowers', state: 'thin', index: 0 }],
      questions: [],
    })
    const es = draftEdges(layoutSky(base, { width: 1000, drafts: [mk('other'), mk('open')] }))
    expect(es.map((e) => draftEdgeId(e.draft!).split('|')[0]).sort()).toEqual(['open', 'other'])
  })

  it('付け替えの新しい線（retarget）は札の位置を動かさない・離れる元の線（moved）は形に残る', () => {
    const plain = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    const moved: DraftSky = {
      ...lowers,
      relations: [
        ...lowers.relations.slice(0, 1),
        { from: 'c4', to: 'c2', rel: 'lowers', state: 'moved', index: 1 },
        { from: 'c4', to: 'c1', rel: 'lowers', state: 'retarget', index: null },
        lowers.relations[2],
      ],
    }
    const l = layoutSky(EMPTY_SKY, { width: 960, drafts: [moved] })
    expect(positions(l)).toEqual(positions(plain))
    const states = draftEdges(l).map((e) => e.draft?.state)
    expect(states).toEqual(['solid', 'moved', 'retarget', 'thin'])
    // 提案の線（index が無い）は押しても直せない
    expect(draftEdges(l)[2].draft?.index).toBeNull()
  })

  it('決定論（同じ入力は同じ道すじ）で、関係が無ければ線を足さない', () => {
    const a = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    const b = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    expect(draftEdges(a).map((e) => e.geom)).toEqual(draftEdges(b).map((e) => e.geom))
    expect(draftEdges(layoutSky(EMPTY_SKY, { width: 960, drafts: [{ ...lowers, relations: [] }] }))).toHaveLength(0)
  })

  it('conceptEdgeGeom は提案の札どうし（と用語の札）の線に形を返す', () => {
    const l = layoutSky(EMPTY_SKY, { width: 960, drafts: [lowers] })
    for (const e of draftEdges(l)) expect(conceptEdgeGeom(l, e)).not.toBeNull()
  })
})

const thermalBare: DraftSky = {
  draft_id: 'bare',
  theme_label: 'x',
  concepts: [{ key: 'c1', label: '熱伝導率', kind: 'property', use: 'new' }],
  relations: [],
  questions: [],
}

describe('layoutSky — 語どうしの知識の線（一部・下げる・上げる・変える）', () => {
  const base = makeSky(3, 12)
  const [a, b, c] = base.terms
  const withEdges = (edges: SkyData['edges']): SkyData => ({ ...base, edges })

  it('線は kind=concept・relation つきで足される（線が無ければ今までの配置のまま）', () => {
    const none = layoutSky(withEdges([]), { width: 1000 })
    const old = { ...base } as Partial<SkyData>
    delete old.edges
    expect(positions(layoutSky(old as SkyData, { width: 1000 }))).toEqual(positions(none))
    const l = layoutSky(
      withEdges([
        { from: a.iri, to: b.iri, relation: 'lowers', reason: null },
        { from: b.iri, to: c.iri, relation: 'isPartOf', reason: '粒の中' },
      ]),
      { width: 1000 },
    )
    // 星・星座は動かない（上の帯の札と、その下の標準用語だけが知識の段に従う）
    const notTerm = (x: SkyLayout) => positions(x).filter((k) => !k.startsWith('t|') && !k.startsWith('s|'))
    expect(notTerm(l).length).toBeGreaterThan(0)
    const dy = l.bands.midTop - none.bands.midTop
    expect(notTerm(l)).toEqual(
      notTerm(none).map((k) => {
        const m = /^(.*)@([-\d.]+),([-\d.]+)$/.exec(k)!
        return `${m[1]}@${m[2]},${(Number(m[3]) + dy).toFixed(3)}`
      }),
    )
    const cs = l.edges.filter((e) => e.kind === 'concept')
    expect(cs.map((e) => [e.from, e.to, e.relation])).toEqual([
      [termId(a.iri), termId(b.iri), 'lowers'],
      [termId(b.iri), termId(c.iri), 'isPartOf'],
    ])
    // 他の線は 1 本も減らない
    expect(l.edges.filter((e) => e.kind !== 'concept')).toEqual(none.edges.filter((e) => e.kind !== 'concept'))
  })

  it('札の無い語・自分自身・重複の線は足さない', () => {
    const l = layoutSky(
      withEdges([
        { from: a.iri, to: 'urn:none', relation: 'raises', reason: null },
        { from: a.iri, to: a.iri, relation: 'affects', reason: null },
        { from: a.iri, to: b.iri, relation: 'raises', reason: null },
        { from: a.iri, to: b.iri, relation: 'raises', reason: null },
      ]),
      { width: 1000 },
    )
    expect(l.edges.filter((e) => e.kind === 'concept')).toHaveLength(1)
  })

  it('edges が無い古いサーバの形でも動く', () => {
    const old = { ...base } as Partial<SkyData>
    delete old.edges
    expect(layoutSky(old as SkyData, { width: 1000 }).edges.some((e) => e.kind === 'concept')).toBe(false)
  })

  it('線の形は札の縁から縁へ・決定論・逆向きの線は別の側に曲がる', () => {
    const l = layoutSky(
      withEdges([
        { from: a.iri, to: b.iri, relation: 'lowers', reason: null },
        { from: b.iri, to: a.iri, relation: 'raises', reason: null },
      ]),
      { width: 1000 },
    )
    const [e1, e2] = l.edges.filter((e) => e.kind === 'concept')
    const g1 = conceptEdgeGeom(l, e1)!
    const g1b = conceptEdgeGeom(l, e1)!
    expect(g1).toEqual(g1b)
    const na = l.nodes.get(termId(a.iri))!
    const nb = l.nodes.get(termId(b.iri))!
    // 始点は a の札の外側（中心でない）・終点は b の札の縁
    expect(Math.abs(g1.x1 - na.x) > 1 || Math.abs(g1.y1 - na.y) > 1).toBe(true)
    expect(Math.abs(g1.x2 - nb.x) > 1 || Math.abs(g1.y2 - nb.y) > 1).toBe(true)
    const g2 = conceptEdgeGeom(l, e2)!
    expect(Math.sign(g1.cy - (g1.y1 + g1.y2) / 2) + Math.sign(g2.cy - (g2.y1 + g2.y2) / 2)).toBe(0)
    // 用語の札でないものは null
    expect(conceptEdgeGeom(l, { kind: 'concept', from: 'k|x|y', to: termId(a.iri) })).toBeNull()
  })

  it('線でつながる札は近傍に入る（選ぶとつながる語が濃く残る）', () => {
    const l = layoutSky(withEdges([{ from: a.iri, to: b.iri, relation: 'lowers', reason: null }]), { width: 1000 })
    expect(neighborhood(l, termId(b.iri)).has(termId(a.iri))).toBe(true)
  })

  it('小さな札は用語の札に重ならない所に置く（札の下の小さな字は、置けるなら避ける。置けなければ出さない）', () => {
    const l = layoutSky(withEdges([{ from: a.iri, to: b.iri, relation: 'lowers', reason: null }]), { width: 1000 })
    const e = l.edges.find((x) => x.kind === 'concept')!
    const g = conceptEdgeGeom(l, e)!
    const pos = conceptPillPos(l, g, 20)
    if (pos) {
      for (const n of l.nodes.values()) {
        if (n.type !== 'term') continue
        const hit = Math.abs(pos.x - n.x) < (n.w + 20) / 2 && Math.abs(pos.y - n.y) < (n.h + 16) / 2
        expect(hit).toBe(false)
      }
    }
    // 線の全体が札に覆われる（2 つの札が同じ段で、札の幅がとても広い）なら null
    const nb = l.nodes.get(e.to)!
    nb.y = (l.nodes.get(e.from)! as typeof nb).y
    const g2 = conceptEdgeGeom(l, { ...e, geom: undefined })!
    expect(conceptPillPos(l, g2, 5000)).toBeNull()
    expect(conceptPillPos(l, g, 20)).toEqual(pos) // 決定論
  })
})


// ── 上の帯を知識の段で並べる ────────────────────────────────────────
describe('layoutSky — 上の帯の知識の段（結果が上・原因が下）', () => {
  const mk = (slug: string, label: string, kind: 'class' | 'property' = 'class') =>
    ({
      iri: `https://example.org/k#${slug}`,
      slug,
      label,
      label_en: slug,
      kind,
      members: [],
      standard: null,
      answering: 0,
      questions: [],
    }) as SkyData['terms'][number]
  const T = {
    cond: mk('cond', '熱伝導率', 'property'),
    lat: mk('lat', '格子熱伝導率', 'property'),
    ele: mk('ele', '電子熱伝導率', 'property'),
    gb: mk('gb', '粒界散乱'),
    pd: mk('pd', '点欠陥散乱'),
    car: mk('car', 'キャリア濃度', 'property'),
    smp: mk('smp', '試料'),
  }
  const iri = (k: keyof typeof T): string => T[k].iri
  const line = (from: keyof typeof T, to: keyof typeof T, relation: SkyLine['relation']): SkyLine => ({
    from: iri(from),
    to: iri(to),
    relation,
    reason: null,
  })
  const thermal = (): SkyData => ({
    ...makeSky(1, 3),
    terms: Object.values(T),
    hubs: [],
    edges: [
      line('lat', 'cond', 'isPartOf'),
      line('ele', 'cond', 'isPartOf'),
      line('gb', 'lat', 'lowers'),
      line('pd', 'lat', 'lowers'),
      line('car', 'ele', 'raises'),
    ],
  })
  const nodeOf = (l: SkyLayout, k: keyof typeof T) => l.nodes.get(termId(iri(k))) as TermNode
  const rectOf = (n: TermNode, pad = 0) => ({ l: n.x - n.w / 2 - pad, r: n.x + n.w / 2 + pad, t: n.y - n.h / 2 - pad, b: n.y + n.h / 2 + pad })
  const inside = (p: { x: number; y: number }, r: { l: number; r: number; t: number; b: number }) =>
    p.x > r.l && p.x < r.r && p.y > r.t && p.y < r.b
  /** 札の下の小さな字（まだ星がない）の矩形。 */
  const capOf = (n: TermNode) => ({ l: n.x - 30, r: n.x + 30, t: n.y + n.h / 2, b: n.y + n.h / 2 + 16 })

  it('原因は結果より下の段・全体は上の段（熱伝導率の例）', () => {
    const l = layoutSky(thermal(), { width: 1000 })
    const y = (k: keyof typeof T) => nodeOf(l, k).y
    expect(y('cond')).toBeLessThan(y('lat'))
    expect(y('cond')).toBeLessThan(y('ele'))
    expect(y('lat')).toBeLessThan(y('gb'))
    expect(y('lat')).toBeLessThan(y('pd'))
    expect(y('ele')).toBeLessThan(y('car'))
    expect(y('gb')).toBe(y('pd'))
    // 線でつながらない札（試料）は段に入らず、従来どおり段詰め
    expect(nodeOf(l, 'smp').lane).toBeLessThan(1000)
    // 結果の真下に原因: 全体は 2 つの一部のあいだ・原因は結果の近く
    expect(nodeOf(l, 'cond').x).toBeGreaterThan(Math.min(nodeOf(l, 'lat').x, nodeOf(l, 'ele').x))
    expect(nodeOf(l, 'cond').x).toBeLessThan(Math.max(nodeOf(l, 'lat').x, nodeOf(l, 'ele').x))
    const near = (a: keyof typeof T, b: keyof typeof T) => Math.abs(nodeOf(l, a).x - nodeOf(l, b).x)
    expect(near('gb', 'lat')).toBeLessThan(near('gb', 'ele'))
    expect(near('car', 'ele')).toBeLessThan(near('car', 'lat'))
  })

  it('上の帯の高さは段の数で増え、データの帯はその分だけ下がる・決定論', () => {
    const none = layoutSky({ ...thermal(), edges: [] }, { width: 1000 })
    const l = layoutSky(thermal(), { width: 1000 })
    expect(l.bands.midTop).toBeGreaterThan(none.bands.midTop)
    expect(l.bands.midTop).toBe(MID_TOP0 + 2 * KNOW_PITCH)
    expect(l.bands.namesLanes).toBeGreaterThan(none.bands.namesLanes)
    expect(l.height).toBeGreaterThanOrEqual(none.height)
    expect(positions(layoutSky(thermal(), { width: 1000 }))).toEqual(positions(l))
  })

  it('線は札を横切らない（札・札の下の字）・小さな札は札・字・ほかの小さな札に重ならない', () => {
    for (const width of [1440, 1000, 375]) {
      const l = layoutSky(thermal(), { width })
      const cards = [...l.nodes.values()].filter((n): n is TermNode => n.type === 'term')
      const pills: { l: number; r: number; t: number; b: number }[] = []
      const cs = l.edges.filter((e) => e.kind === 'concept')
      expect(cs).toHaveLength(5)
      for (const e of cs) {
        const g = conceptEdgeGeom(l, e)!
        for (let i = 1; i < 30; i++) {
          const p = conceptEdgePointAt(g, i / 30)
          for (const n of cards) {
            if (n.id === e.from || n.id === e.to) continue
            expect(inside(p, rectOf(n, 1))).toBe(false)
            expect(inside(p, capOf(n))).toBe(false)
          }
        }
        if (!e.pill) continue
        const w = e.relation === 'isPartOf' ? 36 : 24
        const r = { l: e.pill.x - w / 2, r: e.pill.x + w / 2, t: e.pill.y - 8, b: e.pill.y + 8 }
        const ov = (a: typeof r, b: typeof r) => a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t
        for (const n of cards) {
          expect(ov(r, rectOf(n))).toBe(false)
          expect(ov(r, capOf(n))).toBe(false)
        }
        for (const q of pills) expect(ov(r, q)).toBe(false)
        pills.push(r)
      }
      // この例では 5 本とも小さな札を置ける
      expect(pills).toHaveLength(5)
    }
  })

  it('375px でも札は重ならず、枠の中に収まる（段の中で折り返す）', () => {
    const l = layoutSky(thermal(), { width: 375 })
    const cards = [...l.nodes.values()].filter((n): n is TermNode => n.type === 'term')
    for (const n of cards) {
      expect(n.x - n.w / 2).toBeGreaterThanOrEqual(0)
      expect(n.x + n.w / 2).toBeLessThanOrEqual(375)
    }
    for (const a of cards) {
      for (const b of cards) {
        if (a.id >= b.id) continue
        const ov = Math.abs(a.x - b.x) < (a.w + b.w) / 2 && Math.abs(a.y - b.y) < (a.h + b.h) / 2
        expect(ov).toBe(false)
      }
    }
    // 結果が上・原因が下は幅が狭くても保つ
    expect(nodeOf(l, 'cond').y).toBeLessThan(nodeOf(l, 'lat').y)
    expect(nodeOf(l, 'lat').y).toBeLessThan(nodeOf(l, 'gb').y)
    expect(nodeOf(l, 'ele').y).toBeLessThan(nodeOf(l, 'car').y)
  })

  it('段をまたぐ長い線は、あいだの札を避けて曲がる', () => {
    const sky: SkyData = {
      ...thermal(),
      edges: [line('lat', 'cond', 'isPartOf'), line('gb', 'lat', 'lowers'), line('gb', 'cond', 'affects')],
    }
    const l = layoutSky(sky, { width: 1000 })
    const e = l.edges.find((x) => x.kind === 'concept' && x.relation === 'affects')!
    const g = conceptEdgeGeom(l, e)!
    for (let i = 1; i < 30; i++) {
      const p = conceptEdgePointAt(g, i / 30)
      expect(inside(p, rectOf(nodeOf(l, 'lat'), 1))).toBe(false)
    }
  })

  it('輪になる線（a→b と b→a）でも段が決まり、札は重ならない', () => {
    const sky: SkyData = { ...thermal(), edges: [line('gb', 'lat', 'lowers'), line('lat', 'gb', 'raises')] }
    const l = layoutSky(sky, { width: 1000 })
    expect(nodeOf(l, 'lat').y).not.toBe(nodeOf(l, 'gb').y)
    expect(l.edges.filter((e) => e.kind === 'concept')).toHaveLength(2)
  })

  it('札が 20 を超える成分は、超えた分を段詰めに戻す（崩れない）', () => {
    const many = Array.from({ length: 26 }, (_, i) => mk(`n${String(i).padStart(2, '0')}`, `語${i}`))
    const sky: SkyData = {
      ...makeSky(1, 3),
      terms: many,
      hubs: [],
      edges: many.slice(1).map((t, i) => ({ from: t.iri, to: many[Math.floor(i / 2)].iri, relation: 'lowers' as const, reason: null })),
    }
    for (const width of [1000, 375]) {
      const l = layoutSky(sky, { width })
      const cards = [...l.nodes.values()].filter((n): n is TermNode => n.type === 'term')
      expect(cards.filter((n) => n.lane >= 1000)).toHaveLength(20)
      expect(cards.filter((n) => n.lane < 1000)).toHaveLength(6)
      for (const a of cards) {
        expect(a.x - a.w / 2).toBeGreaterThanOrEqual(-0.5)
        expect(a.x + a.w / 2).toBeLessThanOrEqual(width + 0.5)
        for (const b of cards) {
          if (a.id >= b.id) continue
          expect(Math.abs(a.x - b.x) < (a.w + b.w) / 2 && Math.abs(a.y - b.y) < (a.h + b.h) / 2).toBe(false)
        }
      }
    }
  })

  it('標準用語（下の帯）は札の動きに従い、線の無い札と標準用語の線は残る', () => {
    const l = layoutSky(thermal(), { width: 1000 })
    expect(l.edges.filter((e) => e.kind === 'termStd')).toHaveLength(0)
    expect(l.height).toBeGreaterThanOrEqual(620)
  })
})

describe('layoutSky — 未公開（proposed）のデータセット', () => {
  it('proposed が星・丸に通り、並びは公開済みの後ろ', () => {
    const base = makeSky(2, 3)
    const thin = { id: 'zz-thin', label: '価格表', created_at: '', kinds: [{ iri: 'urn:k', label: '値段の記録', count: 5, links: [], standard: null }], proposed: true }
    const withThin = { ...base, datasets: [...base.datasets, thin] }
    const l = layoutSky(withThin, { width: 1000 })
    const star = [...l.nodes.values()].find((n) => n.type === 'kind' && n.datasetId === 'zz-thin')
    expect(star && star.type === 'kind' && star.proposed).toBe(true)
    const plain = [...l.nodes.values()].filter((n) => n.type === 'kind' && n.datasetId !== 'zz-thin')
    expect(plain.length).toBeGreaterThan(0)
    expect(plain.every((n) => n.type === 'kind' && !n.proposed)).toBe(true)
    // 薄い星があっても、公開済みの並びは変わらない
    const before = layoutSky(base, { width: 1000 })
    for (const n of before.nodes.values()) if (n.type === 'kind') expect(l.nodes.get(n.id)?.x).toBeCloseTo(n.x, 5)
    const circles = layoutSky(withThin, { width: 1000, mode: 'circles' })
    const ds = [...circles.nodes.values()].find((n) => n.type === 'dataset' && n.datasetId === 'zz-thin')
    expect(ds && ds.type === 'dataset' && ds.proposed).toBe(true)
  })
})
