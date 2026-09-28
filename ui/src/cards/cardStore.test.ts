// PR F18 契約メモ §1.1: `replaceCardItem`（純関数・差し替えの 3 態）と
// `replaceCard`（永続化つき）の検証。

import { describe, expect, it } from 'vitest'
import { addCard, addCardItem, getAllCards, replaceCard, replaceCardItem } from './cardStore'
import type { CardSpec } from './cardsApi'

function uniq(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2)}`
}

function card(overrides: Partial<CardSpec> & Pick<CardSpec, 'card_id'>): CardSpec {
  return {
    subject_key: 'subject-1',
    tool: 'set_measure',
    params: { class: 'class-x', where: [], shape: 'facts' },
    title: overrides.card_id,
    output_kind: 'facts',
    created_at: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
}

describe('replaceCardItem — 位置と created_at を保つ 3 態', () => {
  it('態 1: old が見つかり、new の id が他と被らない → 同じ位置・元の created_at のまま差し替わる', () => {
    const before = [
      card({ card_id: 'a', created_at: '2026-01-01T00:00:00.000Z' }),
      card({ card_id: 'b', created_at: '2026-01-02T00:00:00.000Z' }),
      card({ card_id: 'c', created_at: '2026-01-03T00:00:00.000Z' }),
    ]
    const replacement = card({ card_id: 'b', title: '棒グラフに', created_at: '2099-01-01T00:00:00.000Z' })
    const after = replaceCardItem(before, 'b', replacement)
    expect(after.map((c) => c.card_id)).toEqual(['a', 'b', 'c'])
    expect(after[1].title).toBe('棒グラフに')
    // created_at は元のカード（差し替えられる前）のものを保つ。
    expect(after[1].created_at).toBe('2026-01-02T00:00:00.000Z')
  })

  it('態 1（id 自体が変わる場合）: old の位置のまま、new の id・元の created_at で差し替わる', () => {
    const before = [
      card({ card_id: 'a', created_at: '2026-01-01T00:00:00.000Z' }),
      card({ card_id: 'b', created_at: '2026-01-02T00:00:00.000Z' }),
    ]
    const replacement = card({ card_id: 'new-id', created_at: '2099-01-01T00:00:00.000Z' })
    const after = replaceCardItem(before, 'b', replacement)
    expect(after.map((c) => c.card_id)).toEqual(['a', 'new-id'])
    expect(after[1].created_at).toBe('2026-01-02T00:00:00.000Z')
  })

  it('態 2: new の id が別の既存カードと被る → そのカードは触らず残し、old だけを消す', () => {
    const existing = card({ card_id: 'c', title: '元からあったカード', created_at: '2026-01-03T00:00:00.000Z' })
    const before = [card({ card_id: 'a' }), card({ card_id: 'b' }), existing]
    const replacement = card({ card_id: 'c', title: '別の内容のはずが同じ id に着地' })
    const after = replaceCardItem(before, 'b', replacement)
    expect(after.map((c) => c.card_id)).toEqual(['a', 'c'])
    // 既存カードは中身も参照もそのまま（差し替え内容で上書きされない）。
    expect(after.find((c) => c.card_id === 'c')).toBe(existing)
  })

  it('態 3: old が見当たらない → 末尾に足す', () => {
    const before = [card({ card_id: 'a' })]
    const replacement = card({ card_id: 'z' })
    const after = replaceCardItem(before, 'does-not-exist', replacement)
    expect(after.map((c) => c.card_id)).toEqual(['a', 'z'])
  })

  it('態 3 は addCardItem と同じ規則（同じ id が既にあれば置き換える）', () => {
    const before = [card({ card_id: 'a', title: '旧' })]
    const replacement = card({ card_id: 'a', title: '新' })
    expect(replaceCardItem(before, 'does-not-exist', replacement)).toEqual(addCardItem(before, replacement))
  })

  it('新旧の id が同じときは態 1（自分自身との衝突を態 2 と誤判定しない）', () => {
    const before = [card({ card_id: 'a' }), card({ card_id: 'b', created_at: '2026-01-02T00:00:00.000Z' })]
    const replacement = card({ card_id: 'b', title: '同じ id のまま中身だけ変える' })
    const after = replaceCardItem(before, 'b', replacement)
    expect(after.map((c) => c.card_id)).toEqual(['a', 'b'])
    expect(after[1].title).toBe('同じ id のまま中身だけ変える')
    expect(after[1].created_at).toBe('2026-01-02T00:00:00.000Z')
  })
})

describe('replaceCard — 永続化つき（localStorage が無い環境ではメモリのみ）', () => {
  it('カードストアの状態を差し替えて購読者に伝える（addCard → replaceCard → getAllCards）', () => {
    const subject = uniq('subject')
    const oldId = uniq('card')
    const newId = uniq('card')
    addCard(card({ card_id: oldId, subject_key: subject, title: '元のカード' }))
    replaceCard(subject, oldId, card({ card_id: newId, subject_key: subject, title: '差し替えたカード' }))
    const mine = getAllCards().filter((c) => c.subject_key === subject)
    expect(mine.map((c) => c.card_id)).toEqual([newId])
    expect(mine[0].title).toBe('差し替えたカード')
  })
})
