import { describe, expect, it } from 'vitest'
import type { Alignment } from './crosswalkApi'
import { layoutDatasetOverview } from './kindOverviewScale'
import { mkDs, NS } from './overviewScaleFixture'

const al = (relation: string): Alignment => ({
  alignment_iri: relation,
  source: `${NS}d0K0`,
  target: `${NS}d1K0`,
  relation,
  from_perspective: '',
  to_perspective: '',
  at: '',
})
const edgesOf = (relation: string) =>
  layoutDatasetOverview({ datasets: [mkDs(0, 1), mkDs(1, 1)], alignments: [al(relation)], unnamedHub: 'x' }, { width: 900 }).edges

describe('layoutDatasetOverview — 対応の線の読み方', () => {
  it('同じ（exactMatch）は向きのない対応線・⊂ は向きのある線・競合する（向きなし）は描かない', () => {
    expect(edgesOf('exactMatch').filter((e) => e.kind === 'alignment')).toHaveLength(1)
    expect(edgesOf('subClassOf').filter((e) => e.kind === 'upper')).toHaveLength(1)
    const competes = edgesOf('competesWith')
    expect(competes.filter((e) => e.kind === 'alignment' || e.kind === 'upper')).toHaveLength(0)
  })
})
