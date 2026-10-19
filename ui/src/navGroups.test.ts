import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from './navGroups'

// 左ナビは 5 つ・順序固定（UI 再設計 PR 1）。navGroups.ts は純データなので描画抜きで検証できる。

describe('NAV_ITEMS', () => {
  it('ホーム / 知りたいこと(vocab) / データ(gallery) / 見る(cards) / 質問する(ask)（この順）', () => {
    expect(NAV_ITEMS.map((it) => it.id)).toEqual(['home', 'vocab', 'gallery', 'cards', 'ask'])
  })

  it('全項目（5 つ）がちょうど 1 度だけ出る', () => {
    const ids = NAV_ITEMS.map((it) => it.id)
    expect(ids).toHaveLength(5)
    expect(new Set(ids).size).toBe(5)
  })

  it('つながり・全体グラフ・かんたんウィザード・アクティビティは本線に出ない', () => {
    const ids: string[] = NAV_ITEMS.map((it) => it.id)
    for (const hidden of ['crosswalk', 'network', 'map', 'workbench', 'decisions', 'sparql']) {
      expect(ids).not.toContain(hidden)
    }
  })

  it('各項目に icon コンポーネントが紐づく', () => {
    for (const item of NAV_ITEMS) {
      expect(typeof item.icon).toBe('function')
    }
  })
})
