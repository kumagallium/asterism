import { describe, expect, it } from 'vitest'
import {
  arrange,
  boxWidthFor,
  edgeEnds,
  edgeLabels,
  edgePath,
  hasSideBySide,
  isPlainEdge,
  labelRect,
  labelSize,
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

describe('rulesShape — 同じ種類を共有する map は 1 つの箱（K63）', () => {
  it('別々のファイルの受け口が同じ ID の作り方と種類名を持てば、箱は 1 つで線は両方から来る', () => {
    const comp = (id: string, source: string): RuleMap => ({
      id,
      source,
      subject: {
        template: 'xrdr:composition/{composition}',
        classes: ['xrd:Composition'],
        class_iris: [`${NS}Composition`],
      },
      properties: [],
    })
    const tpl = 'xrdr:composition/{composition}'
    const shape = rulesShape(
      rules([
        rmap('Record', {
          source: 'curves.csv',
          properties: [link('hasComposition', { template: tpl })] as RuleMap['properties'],
        }),
        comp('composition', 'curves.csv'),
        rmap('Record2', {
          source: 'samples.csv',
          properties: [link('hasComposition', { template: tpl })] as RuleMap['properties'],
        }),
        comp('composition2', 'samples.csv'),
      ]),
    )
    expect(shape.nodes.map((n) => n.id)).toEqual(['Record', 'composition', 'Record2'])
    expect(shape.edges.map((e) => [e.from, e.to])).toEqual([
      ['Record', 'composition'],
      ['Record2', 'composition'],
    ])
  })
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

  it('links where the api names the target, even when the subject is a function', () => {
    // 変換つきの主語は RML では関数になり、テンプレートの文字列を持たない。
    // 行き先は api が付ける（target_map）。
    const country = rmap('Country', {
      subject: { kind: 'function', function: 'template', classes: ['xrd:Country'] },
    })
    const obs = rmap('Observation', {
      properties: [
        link('ofCountry', { kind: 'function', function: 'template', target_map: 'Country', label: '国' }),
        link('year', { kind: 'reference', reference: 'year', label: '年' }),
      ],
    })
    const shape = rulesShape(rules([obs, country]), { withFields: true })
    expect(shape.edges).toEqual([{ from: 'Observation', to: 'Country', label: '国' }])
    // 線になった項目は箱の中に重ねて書かない
    expect(shape.nodes.find((n) => n.id === 'Observation')?.fields?.map((f) => f.name)).toEqual(['年'])
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

describe('arrange', () => {
  const shape = (nodes: string[], edges: [string, string][]): Shape => ({
    nodes: nodes.map((id) => ({ id, label: id, tone: 'record' as const })),
    edges: edges.map(([from, to]) => ({ from, to })),
  })

  it('puts a source above its target and centres each row', () => {
    const pos = arrange(shape(['A', 'B'], [['A', 'B']])).pos
    expect(pos.get('A')!.y).toBeLessThan(pos.get('B')!.y)
    expect(pos.get('A')!.x).toBe(pos.get('B')!.x)
  })

  const mid = (pos: Map<string, { x: number; y: number }>, id: string) => pos.get(id)!.x + NODE_W / 2

  it('puts two children of one parent side by side, even with perRow 1', () => {
    for (const perRow of [undefined, 1]) {
      const pos = arrange(shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]), { perRow }).pos
      expect(pos.get('a')!.y).toBe(pos.get('b')!.y)
      expect(pos.get('a')!.x).not.toBe(pos.get('b')!.x)
      expect(pos.get('P')!.y).toBeLessThan(pos.get('a')!.y)
      expect(mid(pos, 'P')).toBeCloseTo((mid(pos, 'a') + mid(pos, 'b')) / 2)
    }
  })

  it('keeps three children of one parent on one row, whatever perRow is', () => {
    for (const perRow of [1, 2]) {
      const pos = arrange(shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']]), { perRow }).pos
      expect(new Set(['a', 'b', 'c'].map((id) => pos.get(id)!.y)).size).toBe(1)
      expect(pos.get('P')!.x).toBe(pos.get('b')!.x)
    }
  })

  it('stacks a chain A → B → C in one column', () => {
    const pos = arrange(shape(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']])).pos
    expect(pos.get('A')!.x).toBe(pos.get('B')!.x)
    expect(pos.get('B')!.x).toBe(pos.get('C')!.x)
    expect(pos.get('A')!.y).toBeLessThan(pos.get('B')!.y)
    expect(pos.get('B')!.y).toBeLessThan(pos.get('C')!.y)
  })

  it('wraps boxes with no lines by perRow', () => {
    const pos = arrange(shape(['X', 'Y', 'Z'], []), { perRow: 2 }).pos
    expect(pos.get('X')!.y).toBe(pos.get('Y')!.y)
    expect(pos.get('Z')!.y).toBeGreaterThan(pos.get('X')!.y)
  })

  it('puts boxes with no lines below the linked ones and wraps only those', () => {
    const pos = arrange(
      shape(['P', 'a', 'b', 'X', 'Y', 'Z'], [['P', 'a'], ['P', 'b']]),
      { perRow: 2 },
    ).pos
    for (const id of ['X', 'Y', 'Z']) expect(pos.get(id)!.y).toBeGreaterThan(pos.get('a')!.y)
    expect(pos.get('X')!.y).toBe(pos.get('Y')!.y)
    expect(pos.get('Z')!.y).toBeGreaterThan(pos.get('X')!.y)
  })

  /** 線が、両端でない箱の中を通る回数（線を細かく刻んで数える）。`clear` は
   *  箱のまわりに取る余裕。 */
  const behind = (
    s: Shape,
    opts: { perRow?: number; nodeWidth?: number; heightOf?: (n: Shape['nodes'][number]) => number } = {},
    clear = 0,
  ): number => {
    const nodeWidth = opts.nodeWidth ?? NODE_W
    const heightOf = opts.heightOf ?? ((n: Shape['nodes'][number]) => nodeHeight(n))
    const { pos, routes } = arrange(s, opts)
    let hits = 0
    edgeEnds(s, pos, { ...opts, routes }).forEach((end, i) => {
      for (let k = 0; k <= 400; k++) {
        const p = pointOnEdge(end!.from, end!.to, k / 400, end!.route)
        for (const n of s.nodes) {
          if (n.id === s.edges[i].from || n.id === s.edges[i].to) continue
          const q = pos.get(n.id)!
          if (
            p.x > q.x - clear &&
            p.x < q.x + nodeWidth + clear &&
            p.y > q.y - clear &&
            p.y < q.y + heightOf(n) + clear
          )
            hits++
        }
      }
    })
    return hits
  }

  // 深さをまたぐ線。R → b は、途中の段の a の裏を通ってはいけない。
  const triangle = shape(['R', 'a', 'b'], [['R', 'a'], ['a', 'b'], ['R', 'b']])
  // 途中の段のまん中の箱（x）の裏を通ると、x → c の線に読める。
  const pastSiblings = shape(
    ['R', 'a', 'x', 'y', 'c'],
    [['R', 'a'], ['R', 'x'], ['R', 'y'], ['a', 'c'], ['R', 'c']],
  )
  // ID の入れ子が 4 段（A ⊃ B ⊃ C ⊃ D）。線は 6 本で、3 本が段をまたぐ。
  const nested = shape(
    ['A', 'B', 'C', 'D'],
    [['A', 'B'], ['A', 'C'], ['A', 'D'], ['B', 'C'], ['B', 'D'], ['C', 'D']],
  )

  it('never routes a line behind a box that is not one of its ends', () => {
    const cases: Shape[] = [
      shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]),
      shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']]),
      shape(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']]),
      shape(['A', 'B', 'a', 'b'], [['A', 'a'], ['A', 'b'], ['B', 'a'], ['B', 'b']]),
      triangle,
      pastSiblings,
      nested,
      // 行の種類が結晶と空間群を指し、3 つとも同じ文書を指す。
      shape(
        ['Peak', 'Crystal', 'SpaceGroup', 'Doc'],
        [['Peak', 'Crystal'], ['Peak', 'SpaceGroup'], ['Peak', 'Doc'], ['Crystal', 'Doc'], ['SpaceGroup', 'Doc']],
      ),
      // 行き先が図のまん中に無い（段をまたぐ線が斜めに降りる）。
      shape(
        ['R', 'a', 'b', 'c', 'p', 'q'],
        [['R', 'a'], ['R', 'b'], ['R', 'c'], ['a', 'p'], ['c', 'q'], ['R', 'p'], ['R', 'q']],
      ),
    ]
    for (const s of cases) {
      for (const perRow of [1, 2]) {
        for (const nodeWidth of [112, NODE_W, 248]) {
          // 線は、箱のすき間（22）の半分より箱に近づかない。
          expect(behind(s, { perRow, nodeWidth }, 10)).toBe(0)
        }
      }
    }
  })

  /** 箱ごとに項目の数を決めた図（項目を開いた箱は高い）。 */
  const withFields = (s: Shape, counts: number[]): Shape => ({
    ...s,
    nodes: s.nodes.map((n, i) => ({
      ...n,
      fields: Array.from({ length: counts[i] ?? 0 }, (_, k) => ({ name: `f${k}` })),
    })),
  })

  it('keeps a line off the boxes it passes, however tall they are', () => {
    // 席は段の上端から下端まで続く。
    for (const s of [triangle, pastSiblings, nested]) {
      expect(behind(withFields(s, [0, 4, 8, 12, 16]), {}, 10)).toBe(0)
    }
  })

  it('drops straight to the foot of its row before it bends, so a taller neighbour is not in the way', () => {
    // 同じ段に高さの違う箱が並ぶ。低い箱から出た線が、自分の箱の下辺から曲がり
    // 始めると、隣の高い箱の裏を通る。
    const uneven: [Shape, number[]][] = [
      // 段 0 = [P（低い）・Q（高い）]。P → b は Q の下へ向かう。
      [shape(['P', 'Q', 'a', 'b'], [['P', 'a'], ['P', 'b'], ['Q', 'a'], ['Q', 'b']]), [0, 12]],
      // 段をまたぐ線が、出どころの隣の高い箱の下を通って席へ向かう。
      [
        shape(
          ['n0', 'n1', 'n3', 'n4', 'n5', 'n7', 'n8'],
          [['n3', 'n5'], ['n0', 'n5'], ['n5', 'n7'], ['n4', 'n8'], ['n0', 'n3'], ['n1', 'n7']],
        ),
        [1, 9],
      ],
      // 席が入って箱がずれると、段をまたがない線が隣の高い箱の下に入る。
      [
        shape(['n0', 'n1', 'n2', 'n3', 'n4'], [['n0', 'n2'], ['n2', 'n3'], ['n0', 'n3'], ['n1', 'n2']]),
        [0, 10, 0, 4],
      ],
    ]
    for (const [s, counts] of uneven) {
      for (const perRow of [1, 2, 3]) {
        for (const nodeWidth of [112, NODE_W, 248]) {
          expect(behind(withFields(s, counts), { perRow, nodeWidth }, 10)).toBe(0)
        }
      }
    }
  })

  it('bends at once when its box is the tallest of the row', () => {
    // 降りるのは、段の下端が自分の箱の下辺より下にあるときだけ。
    const s = withFields(shape(['P', 'Q', 'a'], [['P', 'a'], ['Q', 'a']]), [0, 12])
    const { pos, routes } = arrange(s)
    const [short, tall] = edgeEnds(s, pos, { routes })
    expect(isPlainEdge(short!.from, short!.to, short!.route)).toBe(false)
    expect(isPlainEdge(tall!.from, tall!.to, tall!.route)).toBe(true)
    // 低い箱の線は、まず真下へ。段の下端は、高い箱の線の出どころと同じ高さ。
    const foot = pointOnEdge(short!.from, short!.to, 0.25, short!.route)
    expect(foot.x).toBe(short!.from.x)
    expect(short!.route.drop).toBe(tall!.from.y)
  })

  it('seats a line that skips a row beside the box it would have hidden behind', () => {
    const { pos, routes, width } = arrange(triangle)
    // 途中の段は「a・席」。R と b は図のまん中のまま。
    expect(routes.map((r) => r.via.length)).toEqual([0, 0, 1])
    const [seat] = routes[2].via
    expect(pos.get('a')!.x).toBe(0)
    // 席は箱からすき間（22）を空け、線は席（幅 24）のまん中を通る。
    expect(seat.x).toBe(NODE_W + 22 + 12)
    expect(width).toBe(NODE_W + 22 + 24)
    expect(mid(pos, 'R')).toBe(width / 2)
    expect(mid(pos, 'b')).toBe(width / 2)
    // 席は途中の段の上端から下端まで。
    expect(seat.top).toBe(pos.get('a')!.y)
    expect(seat.bottom).toBe(pos.get('a')!.y + nodeHeight(triangle.nodes[1]))
  })

  it('takes the gap nearest to the straight line, and the right one on a tie', () => {
    // R と c は図のまん中。まっすぐ結ぶと x の箱を通る — 左右のすき間は同じ近さ。
    const { pos, routes } = arrange(pastSiblings)
    const [seat] = routes[4].via
    // x と y のあいだ。線は、2 つの箱のちょうどまん中を通る。
    expect(seat.x).toBe((pos.get('x')!.x + NODE_W + pos.get('y')!.x) / 2)
    // 箱の並びは入力順のまま。
    expect(pos.get('a')!.x).toBeLessThan(pos.get('x')!.x)
    expect(pos.get('x')!.x).toBeLessThan(pos.get('y')!.x)
  })

  it('takes the gap the straight line passes, not the middle of the figure', () => {
    // R は図のまん中、p は左・q は右。まっすぐ結ぶと、R → p は a と b のあいだ、
    // R → q は b と c のあいだを通る。
    const s = shape(
      ['R', 'a', 'b', 'c', 'p', 'q'],
      [['R', 'a'], ['R', 'b'], ['R', 'c'], ['a', 'p'], ['c', 'q'], ['R', 'p'], ['R', 'q']],
    )
    const { pos, routes } = arrange(s)
    const between = (x: number, left: string, right: string) => {
      expect(x).toBeGreaterThan(pos.get(left)!.x + NODE_W)
      expect(x).toBeLessThan(pos.get(right)!.x)
    }
    between(routes[5].via[0].x, 'a', 'b')
    between(routes[6].via[0].x, 'b', 'c')
  })

  it('orders two seats in one gap by where the straight lines pass', () => {
    // P → t4 と Q → t2 は、どちらも a の右のすき間を通る。P は Q の左にあるが、
    // まっすぐ結んだ線は P → t4 のほうが右を通る。
    const s = shape(
      ['P', 'Q', 'a', 't0', 't1', 't2', 't3', 't4'],
      [
        ['P', 'a'], ['Q', 'a'],
        ['a', 't0'], ['a', 't1'], ['a', 't2'], ['a', 't3'], ['a', 't4'],
        ['P', 't4'], ['Q', 't2'],
      ],
    )
    const { pos, routes } = arrange(s)
    const [p4] = routes[7].via
    const [q2] = routes[8].via
    expect(q2.x).toBeGreaterThan(pos.get('a')!.x + NODE_W)
    expect(p4.x).toBeGreaterThan(q2.x)
  })

  it('gives every skipping line a seat of its own in every row it passes', () => {
    const { routes } = arrange(nested)
    // A → C は 1 段、A → D は 2 段、B → D は 1 段をまたぐ。
    expect(routes.map((r) => r.via.length)).toEqual([0, 1, 2, 0, 1, 0])
    const seats = routes.flatMap((r) => r.via)
    seats.forEach((p, i) =>
      seats.slice(i + 1).forEach((q) => {
        if (p.top === q.top) expect(Math.abs(p.x - q.x)).toBeGreaterThanOrEqual(24)
      }),
    )
  })

  it('orders seats in one gap so that lines cross as little as they can', () => {
    // 線の入力順を変えても同じ絵: 遠くまで行く線（A → D）が外側を、まっすぐ降りる。
    const forward = nested
    const backward = shape(
      ['A', 'B', 'C', 'D'],
      [['C', 'D'], ['B', 'D'], ['B', 'C'], ['A', 'D'], ['A', 'C'], ['A', 'B']],
    )
    for (const [s, ad, ac, bd] of [
      [forward, 2, 1, 4],
      [backward, 3, 4, 1],
    ] as [Shape, number, number, number][]) {
      const via = arrange(s).routes.map((r) => r.via)
      expect(via[ad]).toHaveLength(2)
      expect(via[ad][0].x).toBe(via[ad][1].x)
      expect(via[ac][0].x).toBeLessThan(via[ad][0].x)
      expect(via[bd][0].x).toBeLessThan(via[ad][1].x)
    }
  })

  /** 席を取る前の並べ方（K51 まで）の式の写し。`arrange` と同じコードを通さずに、
   *  「席の無い図は変わらない」を確かめるため。 */
  const before = (s: Shape, perRow: number, nodeWidth: number) => {
    const rows = rowsOf(s, perRow)
    const widest = Math.max(1, ...rows.map((r) => r.length))
    const canvasW = widest * nodeWidth + (widest - 1) * 22
    const pos: [string, { x: number; y: number }][] = []
    let top = 0
    for (const row of rows) {
      const left = (canvasW - (row.length * nodeWidth + (row.length - 1) * 22)) / 2
      row.forEach((id, i) => pos.push([id, { x: left + i * (nodeWidth + 22), y: top }]))
      top += Math.max(...row.map((id) => nodeHeight(s.nodes.find((n) => n.id === id)!))) + 68
    }
    return pos
  }

  it('leaves a figure with no skipping line exactly as it was', () => {
    const cases: Shape[] = [
      shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]),
      shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']]),
      shape(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']]),
      shape(['A', 'B', 'a', 'b'], [['A', 'a'], ['A', 'b'], ['B', 'a'], ['B', 'b']]),
      shape(['X', 'P', 'a', 'Y', 'b', 'Z'], [['P', 'a'], ['P', 'b']]),
      shape(['A', 'B'], [['A', 'B'], ['B', 'A']]),
      withFields(shape(['P', 'Q', 'a', 'b'], [['P', 'a'], ['P', 'b'], ['Q', 'a'], ['Q', 'b']]), [3, 7, 1]),
      shape([], []),
    ]
    for (const s of cases) {
      for (const perRow of [1, 2, 3]) {
        // 箱の幅は小数でも（端数まで）同じ。
        for (const nodeWidth of [NODE_W, 153, 153.3]) {
          const a = arrange(s, { perRow, nodeWidth })
          expect([...a.pos.entries()]).toEqual(before(s, perRow, nodeWidth))
          expect(a.routes.every((r) => r.via.length === 0)).toBe(true)
        }
      }
    }
    // 式の写しが、実機で測った座標と合っていること（2026-09-30・親 1・子 2・幅 132）。
    expect(before(shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]), 2, 132)).toEqual([
      ['P', { x: 77, y: 0 }],
      ['a', { x: 0, y: 114 }],
      ['b', { x: 154, y: 114 }],
    ])
  })

  it('draws the very same line as before when every box of a row is as tall as the others', () => {
    for (const s of [
      shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]),
      shape(['A', 'B', 'a', 'b'], [['A', 'a'], ['A', 'b'], ['B', 'a'], ['B', 'b']]),
      withFields(shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]), [6, 2, 2]),
    ]) {
      const { pos, routes } = arrange(s)
      for (const end of edgeEnds(s, pos, { routes })) {
        expect(isPlainEdge(end!.from, end!.to, end!.route)).toBe(true)
      }
    }
  })

  it('counts a seat as something that stands side by side', () => {
    expect(hasSideBySide(shape(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']]), 1)).toBe(false)
    expect(hasSideBySide(shape(['P', 'a', 'b'], [['P', 'a'], ['P', 'b']]), 1)).toBe(true)
    expect(hasSideBySide(triangle, 1)).toBe(true)
  })

  it('terminates on a cycle instead of recursing forever', () => {
    const pos = arrange(shape(['A', 'B'], [['A', 'B'], ['B', 'A']])).pos
    expect(pos.size).toBe(2)
    expect([...pos.values()].every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true)
  })

  it('is deterministic — the same design lays out identically', () => {
    const s = shape(['A', 'B', 'C'], [['A', 'B'], ['A', 'C']])
    expect([...arrange(s).pos.entries()]).toEqual([...arrange(s).pos.entries()])
    for (const t of [triangle, pastSiblings, nested]) {
      const a = arrange(t)
      const b = arrange(t)
      expect([...a.pos.entries()]).toEqual([...b.pos.entries()])
      expect(a.routes).toEqual(b.routes)
    }
  })
})

describe('pointOnEdge / edgePath', () => {
  const from = { x: 100, y: 49 }
  const to = { x: 100, y: 225 }
  const route = { via: [{ x: 190, top: 114, bottom: 160 }] }

  it('is the plain bezier when the line passes no seat', () => {
    expect(pointOnEdge(from, { x: 40, y: 117 }, 0.5)).toEqual({ x: 70, y: 83 })
    expect(edgePath(from, { x: 40, y: 117 })).toBe('M100,49 C100,83 40,83 40,117')
  })

  it('starts and ends where the line does, and passes through the seat', () => {
    expect(pointOnEdge(from, to, 0, route)).toEqual(from)
    expect(pointOnEdge(from, to, 1, route)).toEqual(to)
    // まん中の高さは席の中。席の中では、線はまっすぐ降りる。
    expect(pointOnEdge(from, to, 0.5, route)).toEqual({ x: 190, y: 137 })
    expect(edgePath(from, to, route)).toBe(
      'M100,49 C100,81.5 190,81.5 190,114 L190,160 C190,192.5 100,192.5 100,225',
    )
  })

  it('goes straight down to the foot of the row first, when the row is taller than its box', () => {
    // 段の下端は 120。線は 49 → 120 をまっすぐ降りてから曲がる。
    const low = { drop: 120, via: [] }
    expect(edgePath(from, { x: 40, y: 200 }, low)).toBe('M100,49 L100,120 C100,160 40,160 40,200')
    expect(isPlainEdge(from, { x: 40, y: 200 }, low)).toBe(false)
    // 自分の箱がいちばん高い（段の下端 = 箱の下辺）なら、今までと同じ線。
    expect(isPlainEdge(from, { x: 40, y: 200 }, { drop: 49, via: [] })).toBe(true)
    expect(edgePath(from, { x: 40, y: 117 }, { drop: 49, via: [] })).toBe(
      edgePath(from, { x: 40, y: 117 }),
    )
    // 上へ戻る線は降りない。
    expect(isPlainEdge(from, { x: 40, y: 10 }, low)).toBe(true)
  })

  it('never jumps — a small step along the line is a small move', () => {
    const long = { drop: 70, via: route.via }
    let prev = pointOnEdge(from, to, 0, long)
    for (let k = 1; k <= 400; k++) {
      const p = pointOnEdge(from, to, k / 400, long)
      expect(Math.hypot(p.x - prev.x, p.y - prev.y)).toBeLessThan(3)
      expect(p.y).toBeGreaterThanOrEqual(prev.y)
      prev = p
    }
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

  it('fits the longest English word on one line — a word never breaks in two', () => {
    // 「composition」11 字 = 93.5。半分に割ると「compositio / n」と語の途中で折れた
    // （実機 2026-10-01）。語 1 つ ＋ 余白 36 ＋ 畳むボタン 26 = 156。
    expect(boxWidthFor(kinds(['Record', 'composition']), 132, 176)).toBe(156)
    // 語の境目（空白）では割れるので、今までどおり 2 行の幅（1 行に並べた 232 にはしない）。
    // 23 字 = 195.5。半分の 98 ＋ 1 字 13 ＋ 余白 36 = 147。いちばん長い語は 42.5。
    expect(boxWidthFor(kinds(['aaaaa bbbbb ccccc ddddd'], false), 100, 300)).toBe(147)
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
    const { pos, routes } = arrange(s, { nodeWidth })
    const ls = edgeLabels(s, pos, { nodeWidth, routes })
    const ends = edgeEnds(s, pos, { nodeWidth, routes })
    const labels: R[] = []
    const at: number[] = []
    let covered = 0
    s.edges.forEach((_, i) => {
      const l = ls[i]
      const end = ends[i]
      if (!l || !end) return
      const rect = labelRect(end.from, end.to, l, end.route)
      labels.push(rect)
      at.push(l.at)
      // ほかの線の上に乗っていないか（線を細かく刻んで確かめる）。
      ends.forEach((other, j) => {
        if (j === i || !other) return
        for (let k = 1; k < 100; k++) {
          const p = pointOnEdge(other.from, other.to, k / 100, other.route)
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
    expect(edgeLabels(s, arrange(s, { nodeWidth: 112 }).pos, { nodeWidth: 112 }).map((l) => l?.at)).toEqual([
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
      const pos = arrange(s, { nodeWidth }).pos
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

  it('puts the name of a line that skips a row in its seat', () => {
    const s = named(['R', 'a', 'b'], [
      ['R', 'a', '食材の名前'],
      ['a', 'b', '店の名前'],
      ['R', 'b', '取り込んだ日時'],
    ])
    for (const nodeWidth of [132, NODE_W, 248]) {
      const r = rectsOf(s, nodeWidth)
      expectClear(r, 3)
      expect(r.covered).toBe(0)
      expect(r.at).toEqual([0.5, 0.5, 0.5])
    }
  })

  it('keeps that name off the box beside it, however tall the box is', () => {
    // 項目を開いた箱。名前を段の間へ逃がせなくても、席の幅が名前を入れる。
    const s: Shape = {
      nodes: ['Peak', 'Crystal', 'Doc'].map((id, i) => ({
        id,
        label: id,
        tone: 'record' as const,
        fields: Array.from({ length: i === 1 ? 14 : 2 }, (_, k) => ({ name: `f${k}` })),
      })),
      edges: [
        { from: 'Peak', to: 'Crystal', label: 'ofCrystal' },
        { from: 'Peak', to: 'Doc', label: 'wasDerivedFrom' },
        // 同じ名前は 2 度出さない。
        { from: 'Crystal', to: 'Doc', label: 'wasDerivedFrom' },
      ],
    }
    for (const nodeWidth of [132, 248]) {
      const r = rectsOf(s, nodeWidth)
      expectClear(r, 2)
      expect(r.covered).toBe(0)
    }
  })

  it('gives a nameless skipping line the narrowest seat, and a named one room for its name', () => {
    const bare = arrange(named(['R', 'a', 'b'], [['R', 'a', 'x'], ['a', 'b', 'x'], ['R', 'b', 'x']]))
    const said = arrange(
      named(['R', 'a', 'b'], [['R', 'a', 'x'], ['a', 'b', 'y'], ['R', 'b', '取り込んだ日時']]),
    )
    // 箱（128）＋すき間（22）＋席。名前の無い席は 24、名前のある席は名前＋余白。
    expect(bare.width).toBe(128 + 22 + 24)
    expect(said.width).toBe(128 + 22 + Math.max(24, labelSize('取り込んだ日時').w + 8))
    // 席は箱より広くしない。
    const long = arrange(named(['R', 'a', 'b'], [['R', 'a'], ['a', 'b'], ['R', 'b', 'あ'.repeat(40)]]))
    expect(long.width).toBe(128 + 22 + 128)
  })

  it('puts the name in the middle of a chain', () => {
    const s = named(['A', 'B', 'C'], [['A', 'B'], ['B', 'C']])
    const ls = edgeLabels(s, arrange(s).pos)
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
    const ls = edgeLabels(s, arrange(s).pos)
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
      const pos = arrange(s, { nodeWidth }).pos
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
