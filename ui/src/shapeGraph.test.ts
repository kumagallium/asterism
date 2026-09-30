import { describe, expect, it } from 'vitest'
import {
  boxWidthFor,
  edgeEnds,
  edgeLabels,
  labelRect,
  labelSize,
  layout,
  nodeHeight,
  NODE_W,
  pointOnEdge,
  rowsOf,
  rulesShape,
  skeletonShape,
  type Shape,
} from './shapeGraph'
import type { MappingSkeleton, SkeletonMap } from './api'
import type { DatasetRules, RuleMap } from './galleryApi'

/** ④で予告した形が⑤で実線になる、が図の意味。だから確かめるのは
 *  「④と⑤が同じ設計に対して同じ形を出すか」と、「ルールにこう書いてあれば
 *  線が引かれる／引かれない」の 2 つ。 */

const NS = 'https://example.org/xrd/ontology#'

// ── ⑤: 保存済みの取り込みルールから ────────────────────────────────────────

const rmap = (id: string, over: Partial<RuleMap> = {}): RuleMap => ({
  id,
  subject: { template: `xrdr:${id}/{No}`, classes: [`xrd:${id}`], class_iris: [`${NS}${id}`] },
  properties: [],
  ...over,
})
const rules = (maps: RuleMap[], labels: Record<string, string> = {}): DatasetRules => ({
  maps,
  prefixes: {},
  warnings: [],
  labels,
})
const link = (predicate: string, over: Record<string, unknown>) => ({
  predicate: `xrd:${predicate}`,
  predicate_iri: `${NS}${predicate}`,
  ...over,
})

describe('rulesShape', () => {
  it('names a box by the class label, falling back to the class then the map', () => {
    const shape = rulesShape(rules([rmap('Peak'), rmap('Crystal')], { [`${NS}Peak`]: 'ピーク' }))
    expect(shape.nodes.map((n) => n.label)).toEqual(['ピーク', 'Crystal'])
  })

  it('paints every box the same — ⑤ has nothing for colour to say', () => {
    // 取り込みルールからは 1 件のカードか行の種類かが読めない。分からないことを
    // 塗り分けると、④の色（人が決めたこと）と意味が食い違う。
    const card = rmap('Card', { subject: { template: 'xrdr:card', classes: ['xrd:Card'] } })
    const shape = rulesShape(rules([card, rmap('Peak')]))
    expect(shape.nodes.map((n) => n.tone)).toEqual(['record', 'record'])
  })

  it('links by template, by join, and by a shared constant IRI', () => {
    const crystal = rmap('Crystal', {
      subject: { template: 'xrdr:Crystal/{No}', classes: ['xrd:Crystal'] },
    })
    const byTemplate = rmap('Peak', {
      properties: [link('ofCrystal', { kind: 'template', template: 'xrdr:Crystal/{No}', label: '結晶' })],
    })
    expect(rulesShape(rules([byTemplate, crystal])).edges).toEqual([
      { from: 'Peak', to: 'Crystal', label: '結晶' },
    ])

    const byJoin = rmap('Peak', {
      properties: [link('ofCrystal', { kind: 'join', parent_map: 'Crystal' })],
    })
    // ラベルが無ければプレディケートの局所名を線の名前にする。
    expect(rulesShape(rules([byJoin, rmap('Crystal')])).edges[0].label).toBe('ofCrystal')

    const doc = rmap('Doc', {
      subject: { constant: 'https://example.org/doc/1', constant_is_iri: true },
    })
    const byConstant = rmap('Peak', {
      properties: [
        {
          predicate: 'prov:wasDerivedFrom',
          predicate_iri: 'http://www.w3.org/ns/prov#wasDerivedFrom',
          kind: 'constant',
          constant: 'https://example.org/doc/1',
          constant_is_iri: true,
        },
      ],
    })
    expect(rulesShape(rules([byConstant, doc])).edges[0]).toMatchObject({
      from: 'Peak',
      to: 'Doc',
    })
  })

  it('never turns a value into a line, and never draws the same pair twice', () => {
    const crystal = rmap('Crystal', {
      subject: { template: 'xrdr:Crystal/{No}', classes: ['xrd:Crystal'] },
    })
    const peak = rmap('Peak', {
      properties: [
        // ただの値。形ではないので線にしない。
        link('dSpacing', { kind: 'reference', reference: 'd' }),
        link('ofCrystal', { kind: 'template', template: 'xrdr:Crystal/{No}' }),
        link('alsoCrystal', { kind: 'template', template: 'xrdr:Crystal/{No}' }),
      ],
    })
    expect(rulesShape(rules([peak, crystal])).edges).toHaveLength(1)
  })
})

// ── ④: 骨格から ──────────────────────────────────────────────────────────

const smap = (name: string, template: string): SkeletonMap => ({
  name,
  source: 'xrd.txt',
  subject: { template, classes: [`xrd:${name}`] },
})
const skel = (maps: SkeletonMap[]): MappingSkeleton => ({ version: 1, prefixes: {}, maps })

describe('skeletonShape', () => {
  it('draws a solid line where one ID embeds another, child above parent', () => {
    const s = skeletonShape(skel([smap('Peak', 'r:peak/{No}/{hkl}'), smap('Crystal', 'r:crystal/{No}')]), {
      label: (m) => m.name,
      edgeLabel: 'ID に含む',
    })
    expect(s.edges).toEqual([{ from: 'Peak', to: 'Crystal', label: 'ID に含む' }])
  })

  it('adds the pending line only where no real line already runs', () => {
    const maps = [smap('Peak', 'r:peak/{No}/{hkl}'), smap('Crystal', 'r:crystal/{No}'), smap('SpaceGroup', 'r:sg/{sg}')]
    const s = skeletonShape(skel(maps), {
      label: (m) => m.name,
      edgeLabel: 'ID に含む',
      // Crystal→Peak は既に実線が引かれている向きなので、予告は足さない。
      pendingEdges: [
        ['Crystal', 'SpaceGroup'],
        ['Crystal', 'Peak'],
        ['Crystal', 'Missing'],
      ],
      pendingLabel: 'このあと',
    })
    expect(s.edges.filter((e) => e.pending)).toEqual([
      { from: 'Crystal', to: 'SpaceGroup', label: 'このあと', pending: true },
    ])
  })

  it('lets the caller paint a box by its role', () => {
    const s = skeletonShape(skel([smap('Card', 'r:card'), smap('Peak', 'r:peak/{No}')]), {
      label: (m) => m.name,
      tone: (m) => (m.name === 'Card' ? 'whole' : 'value'),
    })
    expect(s.nodes.map((n) => n.tone)).toEqual(['whole', 'value'])
  })
})

// ── 並べかた ─────────────────────────────────────────────────────────────

describe('layout', () => {
  const shape = (nodes: string[], edges: [string, string][]): Shape => ({
    nodes: nodes.map((id) => ({ id, label: id, tone: 'record' as const })),
    edges: edges.map(([from, to]) => ({ from, to })),
  })

  it('puts a source above its target and centres each row', () => {
    const pos = layout(shape(['A', 'B'], [['A', 'B']]))
    expect(pos.get('A')!.y).toBeLessThan(pos.get('B')!.y)
    expect(pos.get('A')!.x).toBe(pos.get('B')!.x)
  })

  const mid = (pos: Map<string, { x: number; y: number }>, id: string) => pos.get(id)!.x + NODE_W / 2

  it('puts two children of one parent side by side, even with perRow 1', () => {
    for (const perRow of [undefined, 1]) {
      const pos = layout(shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]), { perRow })
      expect(pos.get('a')!.y).toBe(pos.get('b')!.y)
      expect(pos.get('a')!.x).not.toBe(pos.get('b')!.x)
      expect(pos.get('P')!.y).toBeLessThan(pos.get('a')!.y)
      expect(mid(pos, 'P')).toBeCloseTo((mid(pos, 'a') + mid(pos, 'b')) / 2)
    }
  })

  it('keeps three children of one parent on one row, whatever perRow is', () => {
    for (const perRow of [1, 2]) {
      const pos = layout(shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']]), { perRow })
      expect(new Set(['a', 'b', 'c'].map((id) => pos.get(id)!.y)).size).toBe(1)
      expect(pos.get('P')!.x).toBe(pos.get('b')!.x)
    }
  })

  it('stacks a chain A → B → C in one column', () => {
    const pos = layout(shape(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']]))
    expect(pos.get('A')!.x).toBe(pos.get('B')!.x)
    expect(pos.get('B')!.x).toBe(pos.get('C')!.x)
    expect(pos.get('A')!.y).toBeLessThan(pos.get('B')!.y)
    expect(pos.get('B')!.y).toBeLessThan(pos.get('C')!.y)
  })

  it('wraps boxes with no lines by perRow', () => {
    const pos = layout(shape(['X', 'Y', 'Z'], []), { perRow: 2 })
    expect(pos.get('X')!.y).toBe(pos.get('Y')!.y)
    expect(pos.get('Z')!.y).toBeGreaterThan(pos.get('X')!.y)
  })

  it('puts boxes with no lines below the linked ones and wraps only those', () => {
    const pos = layout(
      shape(['P', 'a', 'b', 'X', 'Y', 'Z'], [['P', 'a'], ['P', 'b']]),
      { perRow: 2 },
    )
    for (const id of ['X', 'Y', 'Z']) expect(pos.get(id)!.y).toBeGreaterThan(pos.get('a')!.y)
    expect(pos.get('X')!.y).toBe(pos.get('Y')!.y)
    expect(pos.get('Z')!.y).toBeGreaterThan(pos.get('X')!.y)
  })

  it('never routes a line behind a box that is not one of its ends', () => {
    const cases: Shape[] = [
      shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]),
      shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']]),
      shape(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']]),
      shape(['A', 'B', 'a', 'b'], [['A', 'a'], ['A', 'b'], ['B', 'a'], ['B', 'b']]),
    ]
    for (const s of cases) {
      for (const perRow of [1, 2]) {
        const pos = layout(s, { perRow })
        for (const e of s.edges) {
          const pa = pos.get(e.from)!
          const pb = pos.get(e.to)!
          const from = { x: pa.x + NODE_W / 2, y: pa.y + nodeHeight(s.nodes[0]) }
          const to = { x: pb.x + NODE_W / 2, y: pb.y }
          for (let k = 0; k <= 200; k++) {
            const p = pointOnEdge(from, to, k / 200)
            for (const n of s.nodes) {
              if (n.id === e.from || n.id === e.to) continue
              const q = pos.get(n.id)!
              const inside =
                p.x > q.x && p.x < q.x + NODE_W && p.y > q.y && p.y < q.y + nodeHeight(n)
              expect(inside).toBe(false)
            }
          }
        }
      }
    }
  })

  it('terminates on a cycle instead of recursing forever', () => {
    const pos = layout(shape(['A', 'B'], [['A', 'B'], ['B', 'A']]))
    expect(pos.size).toBe(2)
    expect([...pos.values()].every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true)
  })

  it('is deterministic — the same design lays out identically', () => {
    const s = shape(['A', 'B', 'C'], [['A', 'B'], ['A', 'C']])
    expect([...layout(s).entries()]).toEqual([...layout(s).entries()])
  })
})

describe('rowsOf', () => {
  const shape = (nodes: string[], edges: [string, string][]): Shape => ({
    nodes: nodes.map((id) => ({ id, label: id, tone: 'record' as const })),
    edges: edges.map(([from, to]) => ({ from, to })),
  })

  it('gives one row per depth and never wraps a row that has lines', () => {
    const s = shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']])
    expect(rowsOf(s, 1)).toEqual([['P'], ['a', 'b', 'c']])
  })

  it('wraps only the boxes with no lines, below the rest', () => {
    const s = shape(['X', 'P', 'a', 'Y', 'b', 'Z'], [['P', 'a'], ['P', 'b']])
    expect(rowsOf(s, 2)).toEqual([['P'], ['a', 'b'], ['X', 'Y'], ['Z']])
  })

  it('ignores a line to a box that is not there, and a line back to itself', () => {
    const s = shape(['A', 'B'], [['A', 'Missing'], ['B', 'B']])
    expect(rowsOf(s, 2)).toEqual([['A', 'B']])
  })
})

describe('boxWidthFor', () => {
  const kinds = (labels: string[], withFields = true): Shape => ({
    nodes: labels.map((label, i) => ({
      id: `n${i}`,
      label,
      tone: 'record' as const,
      fields: withFields ? [{ name: 'x' }] : undefined,
    })),
    edges: [],
  })

  it('stays at the narrow width while every name fits on one or two lines', () => {
    expect(boxWidthFor(kinds(['値段の記録', '食材名', '店名']), 132, 176)).toBe(132)
  })

  it('widens until the longest name fits on two lines', () => {
    // 12 字 = 156。半分の 78 ＋ 1 字 13 ＋ 余白 36 ＋ 畳むボタン 26 = 153。
    expect(boxWidthFor(kinds(['値段の記録', '仕入れた食材の正式な名前']), 132, 176)).toBe(153)
    // 項目の無い箱には畳むボタンが無い。
    expect(boxWidthFor(kinds(['仕入れた食材の正式な名前'], false), 100, 176)).toBe(127)
  })

  it('never goes past the width the caller asked for', () => {
    expect(boxWidthFor(kinds(['あ'.repeat(40)]), 132, 176)).toBe(176)
  })
})

describe('edgeLabels', () => {
  const named = (nodes: string[], edges: [string, string, string?][]): Shape => ({
    nodes: nodes.map((id) => ({ id, label: id, tone: 'record' as const })),
    edges: edges.map(([from, to, label]) => ({ from, to, label: label ?? `${from}-${to}` })),
  })
  type R = { x: number; y: number; w: number; h: number }
  const rectsOf = (
    s: Shape,
    nodeWidth = NODE_W,
  ): { labels: R[]; boxes: R[]; at: number[]; covered: number } => {
    const pos = layout(s, { nodeWidth })
    const ls = edgeLabels(s, pos, { nodeWidth })
    const ends = edgeEnds(s, pos, { nodeWidth })
    const labels: R[] = []
    const at: number[] = []
    let covered = 0
    s.edges.forEach((_, i) => {
      const l = ls[i]
      const end = ends[i]
      if (!l || !end) return
      const rect = labelRect(end.from, end.to, l)
      labels.push(rect)
      at.push(l.at)
      // ほかの線の上に乗っていないか（線を細かく刻んで確かめる）。
      ends.forEach((other, j) => {
        if (j === i || !other) return
        for (let k = 1; k < 100; k++) {
          const p = pointOnEdge(other.from, other.to, k / 100)
          if (p.x > rect.x && p.x < rect.x + rect.w && p.y > rect.y && p.y < rect.y + rect.h)
            covered++
        }
      })
    })
    const boxes = s.nodes.map((n) => ({ ...pos.get(n.id)!, w: nodeWidth, h: nodeHeight(n) }))
    return { labels, boxes, at, covered }
  }
  const hit = (a: R, b: R) =>
    Math.min(a.x + a.w, b.x + b.w) > Math.max(a.x, b.x) &&
    Math.min(a.y + a.h, b.y + b.h) > Math.max(a.y, b.y)
  const expectClear = ({ labels, boxes }: { labels: R[]; boxes: R[] }, count: number) => {
    expect(labels).toHaveLength(count)
    labels.forEach((l, i) => {
      boxes.forEach((b) => expect(hit(l, b)).toBe(false))
      labels.slice(i + 1).forEach((m) => expect(hit(l, m)).toBe(false))
    })
  }

  it('keeps names off each other and off every box (1 parent, 2 or 3 children)', () => {
    expectClear(rectsOf(named(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']])), 2)
    expectClear(rectsOf(named(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']])), 3)
  })

  it('leaves every name in the middle when nothing collides', () => {
    // 実機の例（値段の記録 → 食材名・店名）。細い列の箱の幅でも、まん中のまま。
    const s = named(['値段の記録', '食材名', '店名'], [
      ['値段の記録', '食材名', '食材名'],
      ['値段の記録', '店名', '店名'],
    ])
    expect(edgeLabels(s, layout(s, { nodeWidth: 112 }), { nodeWidth: 112 }).map((l) => l?.at)).toEqual([
      0.5, 0.5,
    ])
  })

  it('keeps 4 names apart in a 2 x 2 full crossing', () => {
    // 交わる 2 本の線はまん中が同じ点。まん中に固定すると必ず重なる。
    expectClear(
      rectsOf(named(['A', 'B', 'a', 'b'], [['A', 'a'], ['A', 'b'], ['B', 'a'], ['B', 'b']])),
      4,
    )
  })

  it('moves long names toward the boxes they point at, off the other line', () => {
    // 親の近くは線が集まる。そこに長い名前を置くと、どの線の名前か迷う（実機
    // 2026-09-30）。横に並べて重ならないなら、行き先の側へ寄せる。
    const r = rectsOf(
      named(['P', 'a', 'b'], [
        ['P', 'a', '仕入れた食材の正式な名前'],
        ['P', 'b', '値段を調べたお店の名前'],
      ]),
      176,
    )
    expectClear(r, 2)
    expect(r.covered).toBe(0)
    expect(r.at.every((at) => at > 0.5)).toBe(true)
  })

  it('never puts a name over the arrowhead of its own line', () => {
    const s = named(['P', 'a', 'b'], [
      ['P', 'a', '仕入れた食材の正式な名前'],
      ['P', 'b', '値段を調べたお店の名前'],
    ])
    for (const nodeWidth of [132, 176]) {
      const pos = layout(s, { nodeWidth })
      const ls = edgeLabels(s, pos, { nodeWidth })
      edgeEnds(s, pos, { nodeWidth }).forEach((end, i) => {
        const rect = labelRect(end!.from, end!.to, ls[i]!)
        // 矢じりは線の終わりの 7px。
        expect(rect.y + rect.h).toBeLessThanOrEqual(end!.to.y - 7)
      })
    }
  })

  it('keeps long names apart by using the band above and the band below', () => {
    // 1 本ずつ先着で決めると、1 本目がまん中を取って 2 本目の逃げ場が無くなる。
    const long = 'abcdefghijklmnopqrstuvwx'
    for (const nodeWidth of [112, NODE_W, 176]) {
      expectClear(
        rectsOf(named(['P', 'a', 'b'], [['P', 'a', long], ['P', 'b', `${long}!`]]), nodeWidth),
        2,
      )
    }
    expectClear(
      rectsOf(
        named(['P', 'a', 'b', 'c'], [
          ['P', 'a', '取り込んだ日時の記録'],
          ['P', 'b', '値段を調べた店の名前'],
          ['P', 'c', '食材がとれた産地の名前'],
        ]),
        176,
      ),
      3,
    )
  })

  it('never estimates a name smaller than it was measured on screen', () => {
    // 実機の実測（2026-09-30・11px）。小さく見積もると、重なりを見落とす。
    expect(labelSize('食材の名前').w).toBeGreaterThanOrEqual(59.8125)
    expect(labelSize('取り込んだ日時').w).toBeGreaterThanOrEqual(82.125)
    expect(labelSize('isPartOf').w).toBeGreaterThanOrEqual(45)
    expect(labelSize('x').h).toBe(22)
  })

  it('puts the name in the middle of a chain', () => {
    const s = named(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']])
    const ls = edgeLabels(s, layout(s))
    expect(ls.map((l) => l?.at)).toEqual([0.5, 0.5])
  })

  it('leaves out unnamed, pending and repeated names', () => {
    const s: Shape = {
      nodes: ['A', 'B', 'C', 'D'].map((id) => ({ id, label: id, tone: 'record' as const })),
      edges: [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'C', label: 'x', pending: true },
        { from: 'C', to: 'D', label: 'same' },
        { from: 'A', to: 'D', label: 'same' },
      ],
    }
    const ls = edgeLabels(s, layout(s))
    expect(ls[0]).toBeUndefined()
    expect(ls[1]).toBeUndefined()
    expect(ls[2]?.text).toBe('same')
    expect(ls[3]).toBeUndefined()
  })

  it('is deterministic, and stays quick when a figure has many lines', () => {
    // 兄弟 30 個は、手数の上限に届く（実測 2026-09-30: 14 個では届かない）。
    // 打ち切っても、答えは毎回同じで、どの線にも候補のどれかが入っている。
    const kids = Array.from({ length: 30 }, (_, i) => `k${i}`)
    const s = named(
      ['P', ...kids],
      kids.map((k) => ['P', k, `かなり長い線の名前その${k}`] as [string, string, string]),
    )
    for (const nodeWidth of [132, 176]) {
      const pos = layout(s, { nodeWidth })
      const t0 = performance.now()
      const a = edgeLabels(s, pos, { nodeWidth })
      expect(performance.now() - t0).toBeLessThan(500)
      expect(edgeLabels(s, pos, { nodeWidth })).toEqual(a)
      expect(a).toHaveLength(30)
      for (const l of a) expect([0.5, 0.35, 0.65, 0.25, 0.75]).toContain(l?.at)
    }
  })
})

describe('rulesShape: 実データの例（Phase 2・中身タブ）', () => {
  it('項目が読んでいる列の実値を (source, column) で引いて添える', () => {
    const r = rules([
      rmap('record', {
        source: 'a.csv',
        properties: [
          { predicate: 'xrd:mass', predicate_iri: `${NS}mass`, reference: 'atomic_mass' },
          { predicate: 'xrd:note', predicate_iri: `${NS}note`, reference: 'note' },
          // 列を読まない項目（定数など）には例が付かない
          { predicate: 'xrd:kind', predicate_iri: `${NS}kind`, constant: 'x' },
        ],
      }),
    ])
    const shape = rulesShape(r, {
      withFields: true,
      exampleOf: (source, column) =>
        source === 'a.csv' && column === 'atomic_mass' ? '1.008' : undefined,
    })
    const fields = shape.nodes[0].fields!
    expect(fields.map((f) => f.example)).toEqual(['1.008', undefined, undefined])
  })

  it('exampleOf を渡さなければ例は出ない（従来どおり）', () => {
    const r = rules([
      rmap('record', {
        source: 'a.csv',
        properties: [{ predicate: 'xrd:mass', predicate_iri: `${NS}mass`, reference: 'atomic_mass' }],
      }),
    ])
    expect(rulesShape(r, { withFields: true }).nodes[0].fields![0].example).toBeUndefined()
  })
})
