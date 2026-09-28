import { describe, expect, it } from 'vitest'
import type { CardToolResult } from './cardsApi'
import { sourceLabelsFrom } from './SubjectPage'

// 架空の分野（図書館の貸出）で確かめる。宣言ツールでは item のキーと
// ItemSpec.var が異なりうる — 行の参照はキーで行う（var は使わない）。

function toolResult(over: Partial<CardToolResult>): CardToolResult {
  return {
    tool: 'subject_sources',
    count: 0,
    items: [],
    truncated: false,
    sparql: '',
    output_kind: 'breakdown',
    item: {},
    materials: [],
    shareable: null,
    ...over,
  }
}

describe('sourceLabelsFrom', () => {
  it('picks values via the item key, not ItemSpec.var, when they differ', () => {
    const result = toolResult({
      item: {
        source_dataset: { var: 'sourceDataset', role: 'category' },
      },
      items: [{ source_dataset: '貸出記録 2020' }, { source_dataset: '貸出記録 2021' }],
    })
    expect(sourceLabelsFrom(result)).toEqual(['貸出記録 2020', '貸出記録 2021'])
  })

  it('returns an empty list when no category role is declared', () => {
    const result = toolResult({ item: {}, items: [{ x: 'y' }] })
    expect(sourceLabelsFrom(result)).toEqual([])
  })
})
