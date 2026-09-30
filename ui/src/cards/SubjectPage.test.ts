import { describe, expect, it } from 'vitest'
import type { CardRef, CardToolResult } from './cardsApi'
import { appendAddedCards, sourceLabelsFrom } from './SubjectPage'

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

// 契約メモ PR F4 §1-5: 既定カードの後ろに、足したカードを並べる。
function cardRef(over: Partial<CardRef>): CardRef {
  return { card_id: 'c1', title: 't', tool: 'subject_facts', params: {}, output_kind: 'facts', ...over }
}

describe('appendAddedCards', () => {
  it('places added cards after the default ones, in order', () => {
    const defaults = [cardRef({ card_id: 'd1' }), cardRef({ card_id: 'd2' })]
    const added = [cardRef({ card_id: 'a1' }), cardRef({ card_id: 'a2' })]
    expect(appendAddedCards(defaults, added).map((c) => c.card_id)).toEqual(['d1', 'd2', 'a1', 'a2'])
  })

  it('drops an added card whose id collides with a default one (default wins)', () => {
    const defaults = [cardRef({ card_id: 'd1', title: 'default' })]
    const added = [cardRef({ card_id: 'd1', title: 'added' }), cardRef({ card_id: 'a1' })]
    const result = appendAddedCards(defaults, added)
    expect(result.map((c) => c.card_id)).toEqual(['d1', 'a1'])
    expect(result[0].title).toBe('default')
  })
})
