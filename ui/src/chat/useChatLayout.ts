// 3 領域の出し分け（PR 11a）。境の数字はここ 1 か所（CSS には class で渡す）。
//   1100px 以下: 左（話の一覧）は既定でたたむ（たたむ状態は localStorage に覚える）
//    980px 以下: 中央と右をタブにする。左は ≡ で左から出るシート
import { useCallback, useEffect, useState } from 'react'

export const TABS_MAX = 980
export const FOLD_MAX = 1100
export const LIST_FOLDED_KEY = 'asterism.chat.listFolded'

const read = (query: string): boolean =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false

function useMatches(query: string): boolean {
  const [on, setOn] = useState(() => read(query))
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(query)
    const change = () => setOn(mq.matches)
    change()
    mq.addEventListener?.('change', change)
    return () => mq.removeEventListener?.('change', change)
  }, [query])
  return on
}

/** 覚えているたたみ具合（無ければ null＝画面の幅で決める）。 */
export function readListFolded(): boolean | null {
  try {
    const raw = localStorage.getItem(LIST_FOLDED_KEY)
    return raw === null ? null : raw === '1'
  } catch {
    return null
  }
}

/** たたむかどうか: 覚えた値が先、無ければ 1100px 以下でたたむ。 */
export const listFolded = (stored: boolean | null, small: boolean): boolean => stored ?? small

export interface ChatLayout {
  /** 中央と右をタブにする幅か。 */
  tabs: boolean
  /** 左をたたんでいるか（タブの幅では常にシートなので、ここでは false）。 */
  folded: boolean
  /** 左のシートが開いているか（タブの幅だけ）。 */
  sheetOpen: boolean
  /** ≡ を押した。 */
  toggleList: () => void
  closeSheet: () => void
}

export function useChatLayout(): ChatLayout {
  const tabs = useMatches(`(max-width: ${TABS_MAX}px)`)
  const small = useMatches(`(max-width: ${FOLD_MAX}px)`)
  const [stored, setStored] = useState<boolean | null>(readListFolded)
  const [sheet, setSheet] = useState(false)
  const folded = listFolded(stored, small)
  const toggleList = useCallback(() => {
    if (tabs) {
      setSheet((o) => !o)
      return
    }
    const next = !folded
    setStored(next)
    try {
      localStorage.setItem(LIST_FOLDED_KEY, next ? '1' : '0')
    } catch {
      /* 保存できなくても今回の表示には効く */
    }
  }, [tabs, folded])
  const closeSheet = useCallback(() => setSheet(false), [])
  return { tabs, folded: tabs ? false : folded, sheetOpen: tabs && sheet, toggleList, closeSheet }
}
