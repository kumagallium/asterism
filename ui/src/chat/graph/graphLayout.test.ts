import { describe, expect, it } from 'vitest'
import { EMPTY_SKY, type DraftSky, type SkyData, type SkyTerm } from '../../sky/skyApi'
import { PAD, kindId, termId, type KindNode, type SkyLayout, type SkyNode, type TermNode } from '../../sky/skyLayout'
import { selectionOfEdge, selectionOfNode } from '../chatState'
import { CARD_GAP, GRAPH_HEIGHT_MIN, geomPoints, layerDepths, layoutGraph, packCenters } from './graphLayout'
import { DATASETS, DRAFTS, THERMO, tIri } from './graphFixture'

const cardsOf = (l: SkyLayout): (SkyNode & { w: number; h: number })[] =>
  [...l.nodes.values()].filter((n): n is SkyNode & { w: number; h: number } => n.type === 'term' || n.type === 'draft')
const term = (l: SkyLayout, slug: string): TermNode => l.nodes.get(termId(tIri(slug))) as TermNode
const WIDTHS = [375, 640, 1280, 1440]

const mini = (terms: SkyTerm[], edges: SkyData['edges'] = []): SkyData => ({ ...EMPTY_SKY, terms, edges })
const bare = (slug: string, label = slug): SkyTerm => ({
  iri: tIri(slug),
  slug,
  label,
  label_en: label,
  kind: 'class',
  members: [],
  standard: null,
  answering: 0,
  questions: [],
})
const part = (from: string, to: string) => ({ from: tIri(from), to: tIri(to), relation: 'isPartOf' as const, reason: null })

describe('段の割り当て（一種・一部だけが段を作る）', () => {
  it('to（全体）が上・from（一部）が下。最長の道', () => {
    const d = layerDepths(['a', 'b', 'c', 'd'], [
      { lower: 'b', upper: 'a' },
      { lower: 'c', upper: 'b' },
      { lower: 'c', upper: 'a' },
    ])
    expect(d.get('a')).toBe(0)
    expect(d.get('b')).toBe(1)
    expect(d.get('c')).toBe(2)
    expect(d.has('d')).toBe(false) // 線のない札は段を持たない
  })
  it('輪になる線でも止まる', () => {
    const d = layerDepths(['a', 'b'], [
      { lower: 'a', upper: 'b' },
      { lower: 'b', upper: 'a' },
    ])
    expect(d.size).toBe(2)
  })
  it('図では全体の札が上、一部の札が下', () => {
    const l = layoutGraph(THERMO, { width: 1280, lang: 'ja' })
    expect(term(l, 'material').y).toBeLessThan(term(l, 'paper').y)
    expect(term(l, 'material').y).toBeLessThan(term(l, 'measurement').y)
    expect(term(l, 'paper').y).toBe(term(l, 'measurement').y)
  })
  it('下げる・上げる・変える は段を作らない（同じ段の横）', () => {
    const l = layoutGraph(THERMO, { width: 1440, lang: 'ja' })
    expect(term(l, 'thermal').y).toBe(term(l, 'zt').y)
    expect(term(l, 'seebeck').y).toBe(term(l, 'zt').y)
    expect(term(l, 'doping').y).toBe(term(l, 'carrier').y)
  })
  it('提案の一種・一部は段を作る（既にある用語の下に提案の札）', () => {
    const l = layoutGraph(THERMO, { width: 1280, lang: 'ja', drafts: DRAFTS })
    const a = l.nodes.get('draft|dr1|a') as SkyNode
    const b = l.nodes.get('draft|dr1|b') as SkyNode
    expect(a.type).toBe('draft')
    expect(a.y).toBeGreaterThan(term(l, 'material').y)
    expect(b.y).toBeGreaterThan(a.y)
    // existing: は札にしない
    expect(l.nodes.has('draft|dr1|c')).toBe(false)
  })
})

describe('段の中の並び（重心）', () => {
  // 親 A・B（入力順は B が先）。子 a は A の下、b は B の下
  const sky = mini(
    [bare('B'), bare('A'), bare('b'), bare('a')],
    [part('a', 'A'), part('b', 'B')],
  )
  it('子は親の真下の側に並ぶ（親の左右と子の左右が同じ向き）', () => {
    for (const w of [640, 1280]) {
      const l = layoutGraph(sky, { width: w })
      const x = (s: string): number => term(l, s).x
      expect(Math.sign(x('a') - x('b'))).toBe(Math.sign(x('A') - x('B')))
    }
  })
  it('子は親の中心に寄る', () => {
    const l = layoutGraph(sky, { width: 1280 })
    expect(Math.abs(term(l, 'a').x - term(l, 'A').x)).toBeLessThan(40)
    expect(Math.abs(term(l, 'b').x - term(l, 'B').x)).toBeLessThan(40)
  })
  it('packCenters は並びを保ち、札どうしを gap 以上あけ、枠の内側に収める', () => {
    const xs = packCenters([10, 12, 14], [60, 60, 60], [14, 14], 28, 400)
    expect(xs[1] - xs[0]).toBeGreaterThanOrEqual(74 - 1e-6)
    expect(xs[2] - xs[1]).toBeGreaterThanOrEqual(74 - 1e-6)
    expect(xs[0] - 30).toBeGreaterThanOrEqual(28 - 1e-6)
    const right = packCenters([390, 395, 400], [60, 60, 60], [14, 14], 28, 400)
    expect(right[2] + 30).toBeLessThanOrEqual(400 + 1e-6)
  })
})

describe('折り返し・はみ出し・重なり', () => {
  const big = (n: number): SkyData =>
    mini(Array.from({ length: n }, (_, i) => bare(`t${String(i).padStart(2, '0')}`, `概念の名前その${i}`)))
  it('幅に収まらない段は折り返す（375 は 2 列まで・1440 は 1 行）', () => {
    const narrow = layoutGraph(big(8), { width: 375 })
    const wide = layoutGraph(big(8), { width: 1440 })
    const rowsOf = (l: SkyLayout): number => new Set(cardsOf(l).map((n) => n.y)).size
    expect(rowsOf(wide)).toBe(1)
    expect(rowsOf(narrow)).toBeGreaterThan(1)
    // 375 でも 1 行に 2 枚は入る
    const perRow = new Map<number, number>()
    for (const n of cardsOf(narrow)) perRow.set(n.y, (perRow.get(n.y) ?? 0) + 1)
    expect(Math.max(...perRow.values())).toBe(2)
  })
  for (const w of WIDTHS) {
    it(`幅 ${w}: 札・星は横にはみ出さない／札どうしは ${CARD_GAP}px 以上あく`, () => {
      for (const sky of [THERMO, big(20)]) {
        const l = layoutGraph(sky, { width: w, lang: 'ja', drafts: sky === THERMO ? DRAFTS : [] })
        const cs = cardsOf(l)
        for (const n of cs) {
          expect(n.x - n.w / 2).toBeGreaterThanOrEqual(PAD - 1e-6)
          expect(n.x + n.w / 2).toBeLessThanOrEqual(l.width - PAD + 1e-6)
        }
        for (const n of l.nodes.values()) {
          if (n.type !== 'kind') continue
          expect(n.x - n.r).toBeGreaterThanOrEqual(PAD - 1e-6)
          expect(n.x + n.r).toBeLessThanOrEqual(l.width - PAD + 1e-6)
        }
        for (let i = 0; i < cs.length; i++) {
          for (let j = i + 1; j < cs.length; j++) {
            const a = cs[i]
            const b = cs[j]
            const dx = Math.abs(a.x - b.x) - (a.w + b.w) / 2
            const dy = Math.abs(a.y - b.y) - (a.h + b.h) / 2
            expect(Math.max(dx, dy)).toBeGreaterThanOrEqual(CARD_GAP - 1e-6)
          }
        }
        // 札の高さ・図の高さ
        expect(l.height).toBeGreaterThanOrEqual(GRAPH_HEIGHT_MIN)
        for (const n of l.nodes.values()) expect(n.y).toBeLessThan(l.height)
      }
    })
  }
  it('星どうしも重ならない', () => {
    for (const w of WIDTHS) {
      const l = layoutGraph(THERMO, { width: w, lang: 'ja' })
      const ks = [...l.nodes.values()].filter((n): n is KindNode => n.type === 'kind')
      for (let i = 0; i < ks.length; i++) {
        for (let j = i + 1; j < ks.length; j++) {
          expect(Math.hypot(ks[i].x - ks[j].x, ks[i].y - ks[j].y)).toBeGreaterThan(ks[i].r + ks[j].r)
        }
      }
    }
  })
})

describe('星の吊り・名前のない星・標準の語', () => {
  const l = layoutGraph(THERMO, { width: 1280, lang: 'ja' })
  const star = (ds: string, name: string): KindNode => l.nodes.get(kindId(ds, `https://example.org/g#${ds}/${name}`)) as KindNode

  it('概念に結ばれた星は、その札の直下（札の下・中心の x がそろう／複数は横に）', () => {
    const t = term(l, 'thermal')
    const s = star('nims', 'Thermal')
    expect(s.x).toBe(t.x)
    expect(s.y).toBeGreaterThan(t.y + t.h / 2)
    expect(s.y - (t.y + t.h / 2)).toBeLessThan(40)
    const m = term(l, 'material')
    const s1 = star('sd', 'Sample')
    const s2 = star('mp', 'Material')
    expect(s1.y).toBe(s2.y)
    expect(Math.abs(s1.x - s2.x)).toBeGreaterThanOrEqual(22)
    expect((s1.x + s2.x) / 2).toBeCloseTo(m.x, 5)
  })
  it('星の大きさは件数の対数（多いほど大きい）', () => {
    expect(star('sd', 'Curve').r).toBeGreaterThan(star('sd', 'Paper').r)
    expect(star('sd', 'Paper').r).toBeGreaterThan(star('nims', 'Misc').r)
  })
  it('結ばれていない星は最下段（札・吊りの星より下）にデータセットの順に左から', () => {
    const lonely = ['Author'].map((n) => star('sd', n)).concat([star('nims', 'Misc'), star('price', 'Price')])
    const lowest = Math.max(...cardsOf(l).map((n) => n.y), ...[...l.nodes.values()].filter((n): n is KindNode => n.type === 'kind' && !lonely.includes(n)).map((n) => n.y))
    for (const s of lonely) expect(s.y).toBeGreaterThan(lowest)
    expect(lonely[0].x).toBeLessThan(lonely[1].x)
    expect(lonely[1].x).toBeLessThan(lonely[2].x)
    expect(l.clusters.map((c) => c.datasetId)).toEqual(['sd', 'nims', 'price'])
  })
  it('未公開のデータセットの星は薄い（proposed）', () => {
    expect(star('price', 'Price').proposed).toBe(true)
    expect(star('sd', 'Curve').proposed).toBeUndefined()
  })
  it('proposedDatasets は星として足す（同じ id は足さない）', () => {
    const sky: SkyData = { ...THERMO, datasets: DATASETS.slice(0, 3) }
    const withP = layoutGraph(sky, { width: 1280, proposedDatasets: [DATASETS[3], DATASETS[0]] })
    expect(withP.nodes.has(kindId('price', 'https://example.org/g#price/Price'))).toBe(true)
    expect([...withP.nodes.values()].filter((n) => n.type === 'kind').length).toBe(DATASETS.reduce((s, d) => s + d.kinds.length, 0))
  })
  it('標準の語は札を作らず、札の中の 1 行（stdId・stdLabel）', () => {
    expect([...l.nodes.values()].some((n) => n.type === 'std')).toBe(false)
    const t = term(l, 'thermal')
    expect(t.stdId).toBe('s|https://example.org/g#std/ThermalConductivity')
    expect(t.stdLabel).toBe('qudt:ThermalConductivity')
    expect(t.h).toBeGreaterThan(term(l, 'paper').h)
    expect(term(l, 'paper').stdLabel).toBeUndefined()
  })
  it('丸モードにしない（星が 60 を超えても星のまま）', () => {
    const many: SkyData = {
      ...EMPTY_SKY,
      datasets: Array.from({ length: 12 }, (_, d) => ({
        id: `d${d}`,
        label: `データ${d}`,
        created_at: `2026-01-${String(d + 1).padStart(2, '0')}T00:00:00Z`,
        kinds: Array.from({ length: 10 }, (_, k) => ({ iri: `x:${d}/${k}`, label: `種${d}-${k}`, count: 10 + k, links: [], standard: null })),
      })),
    }
    const g = layoutGraph(many, { width: 640 })
    expect(g.mode).toBe('stars')
    expect([...g.nodes.values()].filter((n) => n.type === 'kind').length).toBe(120)
    expect([...g.nodes.values()].some((n) => n.type === 'dataset')).toBe(false)
    for (const n of g.nodes.values()) if (n.type === 'kind') expect(n.x + n.r).toBeLessThanOrEqual(g.width - PAD + 1e-6)
  })
  it('概念が 0 のときは名前のない星だけ', () => {
    const g = layoutGraph({ ...EMPTY_SKY, datasets: DATASETS }, { width: 1280 })
    expect(cardsOf(g)).toHaveLength(0)
    expect([...g.nodes.values()].every((n) => n.type === 'kind')).toBe(true)
    expect(g.clusters).toHaveLength(4)
  })
  it('何も無いときは下限の高さ', () => {
    const g = layoutGraph(EMPTY_SKY, { width: 800 })
    expect(g.nodes.size).toBe(0)
    expect(g.height).toBe(GRAPH_HEIGHT_MIN)
  })
  it('視点（ハブ）は最下段の右に小さな札', () => {
    const g = layoutGraph({ ...THERMO, hubs: [{ id: 'h1', label: '材料の視点', members: ['https://example.org/g#sd/Sample'] }] }, { width: 1280 })
    const hub = g.nodes.get('h|h1')
    expect(hub?.type).toBe('hub')
    expect(hub && hub.x).toBeGreaterThan(900)
    expect(hub && hub.y).toBeGreaterThan(Math.max(...[...g.nodes.values()].filter((n) => n.type === 'kind').map((n) => n.y)))
  })
})

describe('線', () => {
  it('星 → 札（member）・札 → 札（concept）・提案（draft）。標準の語の線は作らない', () => {
    const l = layoutGraph(THERMO, { width: 1280, drafts: DRAFTS })
    const kinds = new Set(l.edges.map((e) => e.kind))
    expect(kinds.has('member')).toBe(true)
    expect(kinds.has('concept')).toBe(true)
    expect(kinds.has('draft')).toBe(true)
    expect(kinds.has('termStd')).toBe(false)
    expect(kinds.has('kindStd')).toBe(false)
    expect(l.edges.filter((e) => e.kind === 'concept')).toHaveLength(7)
  })
  for (const w of WIDTHS) {
    it(`幅 ${w}: 線は札の縁から縁へ・直線か 1 回折れ・札を貫かない・小さな札は道すじの上`, () => {
      const l = layoutGraph(THERMO, { width: w, drafts: DRAFTS })
      for (const e of l.edges) {
        if (e.kind !== 'concept' && e.kind !== 'draft') continue
        expect(e.geom).toBeDefined()
        const pts = geomPoints(e.geom!)
        expect(pts.length).toBeLessThanOrEqual(3)
        const a = l.nodes.get(e.from) as SkyNode & { w: number; h: number }
        const b = l.nodes.get(e.to) as SkyNode & { w: number; h: number }
        const onEdge = (n: typeof a, p: { x: number; y: number }): boolean =>
          Math.abs(p.x - n.x) <= n.w / 2 + 0.5 && Math.abs(p.y - n.y) <= n.h / 2 + 0.5 && (Math.abs(Math.abs(p.x - n.x) - n.w / 2) < 0.5 || Math.abs(Math.abs(p.y - n.y) - n.h / 2) < 0.5)
        expect(onEdge(a, pts[0])).toBe(true)
        expect(onEdge(b, pts[pts.length - 1])).toBe(true)
        // 線は、両端以外の札を貫かない
        for (const c of cardsOf(l)) {
          if (c.id === e.from || c.id === e.to) continue
          for (let i = 1; i < pts.length; i++) {
            for (let t = 0; t <= 1; t += 0.02) {
              const x = pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t
              const y = pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t
              expect(Math.abs(x - c.x) < c.w / 2 && Math.abs(y - c.y) < c.h / 2).toBe(false)
            }
          }
        }
        // 小さな札は線の道すじの上
        if (e.pill) {
          const near = pts.slice(1).some((p, i) => {
            const q = pts[i]
            const dx = p.x - q.x
            const dy = p.y - q.y
            const k = Math.max(0, Math.min(1, ((e.pill!.x - q.x) * dx + (e.pill!.y - q.y) * dy) / (dx * dx + dy * dy || 1)))
            return Math.hypot(e.pill!.x - (q.x + dx * k), e.pill!.y - (q.y + dy * k)) < 1
          })
          expect(near).toBe(true)
        }
      }
    })
  }
  it('小さな札は線ごとに置ける（置けない線は無い）', () => {
    for (const w of WIDTHS) {
      const l = layoutGraph(THERMO, { width: w })
      expect(l.edges.filter((e) => e.kind === 'concept' && !e.pill)).toHaveLength(0)
    }
  })
  it('1,440 では 8 概念・7 関係・4 星座が 1 画面（高さ 480 以内）に収まる', () => {
    expect(layoutGraph(THERMO, { width: 1440 }).height).toBeLessThanOrEqual(480)
    expect(layoutGraph(THERMO, { width: 1280 }).height).toBeLessThanOrEqual(480)
  })
  it('薄い提案が本物と同じ関係なら二重に引かない／図に入れた線は引く', () => {
    const dup: DraftSky[] = [
      {
        draft_id: 'd2',
        theme_label: 't',
        concepts: [
          { key: 'x', label: '測定', kind: 'class', use: 'existing:measurement' },
          { key: 'y', label: '材料', kind: 'class', use: 'existing:material' },
        ],
        relations: [{ from: 'x', to: 'y', rel: 'part' }],
        questions: [],
      },
    ]
    expect(layoutGraph(THERMO, { width: 1280, drafts: dup }).edges.filter((e) => e.kind === 'draft')).toHaveLength(0)
    dup[0].relations[0].state = 'solid'
    expect(layoutGraph(THERMO, { width: 1280, drafts: dup }).edges.filter((e) => e.kind === 'draft')).toHaveLength(1)
  })
})

describe('決定論・選択の部品', () => {
  it('同じ入力は同じ座標（入力の順に依らない並びの手がかりはハッシュ）', () => {
    for (const w of WIDTHS) {
      const a = layoutGraph(THERMO, { width: w, drafts: DRAFTS })
      const b = layoutGraph(structuredClone(THERMO), { width: w, drafts: structuredClone(DRAFTS) })
      expect(JSON.stringify([...a.nodes.values()])).toBe(JSON.stringify([...b.nodes.values()]))
      expect(JSON.stringify(a.edges)).toBe(JSON.stringify(b.edges))
      expect(a.height).toBe(b.height)
    }
  })
  it('chatState の選択がそのまま動く（札・星・提案の札・知識の線・提案の線）', () => {
    const l = layoutGraph(THERMO, { width: 1280, drafts: DRAFTS })
    expect(selectionOfNode(term(l, 'thermal'), l)).toMatchObject({ kind: 'term', iri: tIri('thermal') })
    const star = [...l.nodes.values()].find((n) => n.type === 'kind') as KindNode
    expect(selectionOfNode(star, l)).toMatchObject({ kind: 'kind', iri: star.iri })
    const thin = l.nodes.get(kindId('price', 'https://example.org/g#price/Price')) as KindNode
    expect(selectionOfNode(thin, l)).toMatchObject({ kind: 'kind', thin: expect.anything() })
    expect(selectionOfNode(l.nodes.get('draft|dr1|a') as SkyNode, l)).toMatchObject({ kind: 'draft', draftId: 'dr1', iri: 'a' })
    const concept = l.edges.find((e) => e.kind === 'concept') as (typeof l.edges)[number]
    expect(selectionOfEdge(l, concept)).toMatchObject({ kind: 'line' })
    const draft = l.edges.find((e) => e.kind === 'draft') as (typeof l.edges)[number]
    expect(selectionOfEdge(l, draft)).toMatchObject({ kind: 'line', draft: true })
  })
})
