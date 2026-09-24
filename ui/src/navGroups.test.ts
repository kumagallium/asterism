import { describe, expect, it } from 'vitest'
import { NAV_GROUPS } from './navGroups'

// 契約メモ contract_pr_f7.md §1: 見出し 2 つ・順序・所属・全 Tab が 1 度だけ出る・
// workbench はここに出ない。navGroups.ts は純データなので描画抜きで検証できる。

describe('NAV_GROUPS', () => {
  it('見出しは build → use の順に 2 つだけ', () => {
    expect(NAV_GROUPS.map((g) => g.key)).toEqual(['build', 'use'])
  })

  it('build＝ホーム/データ/つながり/共通の言葉/アクティビティ（この順）', () => {
    expect(NAV_GROUPS[0].items.map((it) => it.id)).toEqual([
      'home',
      'gallery',
      'crosswalk',
      'vocab',
      'jobs',
    ])
  })

  it('use＝ワークスペース(cards)/質問する(ask)（この順）', () => {
    expect(NAV_GROUPS[1].items.map((it) => it.id)).toEqual(['cards', 'ask'])
  })

  it('全項目（7 つ）がちょうど 1 度だけ出る', () => {
    const ids = NAV_GROUPS.flatMap((g) => g.items.map((it) => it.id))
    expect(ids).toHaveLength(7)
    expect(new Set(ids).size).toBe(7)
  })

  it('workbench（かんたんウィザード）はどの見出しにも出ない', () => {
    const ids = NAV_GROUPS.flatMap((g) => g.items.map((it) => it.id))
    expect(ids).not.toContain('workbench')
  })

  it('各項目に icon コンポーネントが紐づく', () => {
    for (const g of NAV_GROUPS) {
      for (const item of g.items) {
        expect(typeof item.icon).toBe('function')
      }
    }
  })
})
