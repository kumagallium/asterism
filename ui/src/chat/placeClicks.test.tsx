// 押したときに渡る値の検査（PR 14d-1・契約 §9）。DOM の環境は無いので、hook を 1 つ（文言）だけ持つ部品を
// 関数として呼び、返った要素の onClick を直に押す。壊した実装（別の id・別の場所を渡す）で落ちる。
import type { ReactElement, ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'ja' } }),
}))

import { PLACES, placeRouteOf, sameView } from './chatRoute'
import { ImportRowList } from './ImportPlace'
import type { ImportRow } from './importRows'
import { NavRail } from './NavRail'
import { PlaceTabbar } from './PlaceTabbar'

type El = ReactElement<Record<string, unknown>>
const isEl = (n: unknown): n is El => !!n && typeof n === 'object' && 'props' in (n as object)

/** 要素の木から、`data-testid` が一致する要素を集める（関数部品の中までは入らない）。 */
function byTestId(node: ReactNode, id: string, out: El[] = []): El[] {
  if (Array.isArray(node)) for (const c of node) byTestId(c, id, out)
  else if (isEl(node)) {
    if (node.props['data-testid'] === id) out.push(node)
    byTestId(node.props.children as ReactNode, id, out)
  }
  return out
}
const press = (el: El) => (el.props.onClick as () => void)()

const row = (over: Partial<ImportRow>): ImportRow => ({ id: 'd1', name: '価格表', published: true, paper: false, kinds: 1, rows: 10, created: null, ...over })

describe('押すと渡る値', () => {
  it('取り込みの一覧: 行を押すと、その行のデータセットの id が渡る', () => {
    const onOpen = vi.fn()
    const tree = ImportRowList({ rows: [row({ id: 'prices-1' }), row({ id: 'world', name: '世界の国' })], lang: 'ja', onOpen })
    const rows = byTestId(tree, 'import-dataset-row')
    expect(rows).toHaveLength(2)
    press(rows[1])
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith('world')
    press(rows[0])
    expect(onOpen).toHaveBeenLastCalledWith('prices-1')
  })

  it('左のナビ: 項目を押すと、その場所が渡る（広い形・たたんだ形）', () => {
    for (const folded of [false, true]) {
      const onPlace = vi.fn()
      const tree = NavRail({ place: 'chat', onPlace, folded, showToggle: true, sheet: false, onToggle: () => {}, onOpenSettings: () => {}, children: null })
      for (const p of PLACES) {
        const [btn] = byTestId(tree, `nav-${p}`)
        press(btn)
        expect(onPlace).toHaveBeenLastCalledWith(p)
      }
      expect(onPlace).toHaveBeenCalledTimes(3)
    }
  })

  it('下のタブ: タブを押すと、その場所が渡る', () => {
    const onPlace = vi.fn()
    const tree = PlaceTabbar({ place: 'import', onPlace })
    for (const p of PLACES) {
      press(byTestId(tree, `place-tab-${p}`)[0])
      expect(onPlace).toHaveBeenLastCalledWith(p)
    }
  })
})

describe('今の場所のルート（設定を閉じたときに戻る先）', () => {
  const chat = { kind: 'session', id: 'cabc12345x' } as const
  it('チャットは今のチャット・取り込みはかんたんの出し分けつき・知識グラフ・決まる前は #/', () => {
    expect(placeRouteOf('chat', chat, false)).toEqual(chat)
    expect(placeRouteOf('chat', { kind: 'new' }, true)).toEqual({ kind: 'new' })
    expect(placeRouteOf('import', chat, false)).toEqual({ kind: 'import' })
    expect(placeRouteOf('import', chat, true)).toEqual({ kind: 'import', add: true })
    expect(placeRouteOf('graph', chat, true)).toEqual({ kind: 'graph' })
    expect(placeRouteOf(null, chat, false)).toEqual({ kind: 'root' })
  })
  it('同じチャットかどうか', () => {
    expect(sameView({ kind: 'new' }, { kind: 'new' })).toBe(true)
    expect(sameView(chat, { kind: 'session', id: 'cabc12345x' })).toBe(true)
    expect(sameView(chat, { kind: 'session', id: 'cother12345' })).toBe(false)
    expect(sameView(chat, { kind: 'new' })).toBe(false)
    expect(sameView({ kind: 'new' }, chat)).toBe(false)
  })
})
