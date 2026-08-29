import { describe, expect, it } from 'vitest'
import { layout, rulesShape, skeletonShape, type Shape } from './shapeGraph'
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

  it('wraps a row that would put three boxes side by side', () => {
    const pos = layout(shape(['P', 'a', 'b', 'c'], [['P', 'a'], ['P', 'b'], ['P', 'c']]))
    const ys = ['a', 'b', 'c'].map((id) => pos.get(id)!.y)
    // 3 つ目は折り返して次の行へ（細い列で 3 並びは読めない）。
    expect(new Set(ys).size).toBe(2)
    expect(pos.get('a')!.y).toBe(pos.get('b')!.y)
    expect(pos.get('c')!.y).toBeGreaterThan(pos.get('a')!.y)
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
