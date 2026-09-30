import { describe, expect, it } from 'vitest'
import type { CardToolResult } from './cardsApi'
import { valueFromCountResult } from './SetPage'

// 架空の分野（図書館の貸出）で確かめる。宣言ツールでは item のキーと
// ItemSpec.var が異なりうる — 行の参照はキーで行う（var は使わない）。

function toolResult(over: Partial<CardToolResult>): CardToolResult {
  return {
    tool: 'set_count',
    count: 0,
    items: [],
    truncated: false,
    sparql: '',
    output_kind: 'quantity',
    item: {},
    materials: [],
    shareable: null,
    ...over,
  }
}

describe('valueFromCountResult', () => {
  it('reads the value via the item key, not ItemSpec.var, when they differ', () => {
    const result = toolResult({
      item: {
        checkout_total: { var: 'checkoutTotal', role: 'value', number: true },
      },
      items: [{ checkout_total: 42 }],
    })
    expect(valueFromCountResult(result)).toBe(42)
  })

  it('returns null when no value role is declared', () => {
    const result = toolResult({ item: {}, items: [{ x: 1 }] })
    expect(valueFromCountResult(result)).toBeNull()
  })
})
