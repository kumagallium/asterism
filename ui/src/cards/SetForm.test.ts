import { describe, expect, it } from 'vitest'
import type { CardToolResult } from './cardsApi'
import { categoryValuesFrom } from './SetForm'

// 架空の分野（気象観測）で確かめる。宣言ツールでは item のキーと
// ItemSpec.var が異なりうる — 行の参照はキーで行う（var は使わない）。

function toolResult(over: Partial<CardToolResult>): CardToolResult {
  return {
    tool: 'set_breakdown',
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

describe('categoryValuesFrom', () => {
  it('collects distinct values via the item key, not ItemSpec.var, when they differ', () => {
    const result = toolResult({
      item: {
        observation_station: { var: 'observationStation', role: 'category' },
      },
      items: [
        { observation_station: '観測所A' },
        { observation_station: '観測所B' },
        { observation_station: '観測所A' },
      ],
    })
    expect(categoryValuesFrom(result)).toEqual(['観測所A', '観測所B'])
  })

  it('returns an empty list when no category role is declared', () => {
    const result = toolResult({ item: {}, items: [{ x: 'y' }] })
    expect(categoryValuesFrom(result)).toEqual([])
  })
})
