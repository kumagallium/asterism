import { describe, expect, it } from 'vitest'
import { makeSky } from '../sky/skyFixture'
import { layoutSky, termId } from '../sky/skyLayout'
import { removalOverlay } from './chartOverlay'

const base = makeSky(3, 12)
const [a, b, c] = base.terms
const layout = layoutSky(
  {
    ...base,
    edges: [
      { from: a.iri, to: b.iri, relation: 'lowers', reason: null },
      { from: b.iri, to: c.iri, relation: 'isPartOf', reason: null },
    ],
  },
  { width: 1000 },
)
const draft = (removals?: { terms: string[]; lines: { from: string; to: string; relation: string }[] }) => ({ removals })

describe('removalOverlay', () => {
  it('用語の IRI と線を引く', () => {
    const ov = removalOverlay(layout, [draft({ terms: [a.iri], lines: [{ from: b.iri, to: c.iri, relation: 'isPartOf' }] })])
    expect(ov.nodeIds).toEqual([termId(a.iri)])
    expect(ov.edges.map((e) => [e.from, e.to, e.relation])).toEqual([[termId(b.iri), termId(c.iri), 'isPartOf']])
  })
  it('向きと関係が違う線は引かない', () => {
    const ov = removalOverlay(layout, [draft({ terms: [], lines: [{ from: c.iri, to: b.iri, relation: 'isPartOf' }, { from: a.iri, to: b.iri, relation: 'raises' }] })])
    expect(ov.edges).toEqual([])
  })
  it('無ければ空', () => {
    expect(removalOverlay(layout, [draft(undefined)])).toEqual({ nodeIds: [], edges: [] })
    expect(removalOverlay(layout, [])).toEqual({ nodeIds: [], edges: [] })
  })
})
