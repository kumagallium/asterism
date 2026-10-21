// 左ナビの「たたむ」状態を、ナビの外（ホームの会話の境の判定）へ知らせる小さな共有。
// 会話を下からのシートにする境は、ナビの幅の分だけ動く（ナビが開いていると星図の幅が 192px 狭い）。
// 境の数字はここ 1 か所（CSS には書かない — 境は `.sky-ws.is-sheet` の class で CSS に渡す）。
import { useSyncExternalStore } from 'react'

/** ナビが開いているとき、この幅以下では会話をシートにする。 */
export const SHEET_MAX_NAV_OPEN = 1100
/** ナビを畳んでいるとき。 */
export const SHEET_MAX_NAV_FOLDED = 980

export const sheetMaxWidth = (navCollapsed: boolean): number => (navCollapsed ? SHEET_MAX_NAV_FOLDED : SHEET_MAX_NAV_OPEN)

let collapsed = false
const listeners = new Set<() => void>()

/** App が、ナビの状態が決まったとき・変わったときに呼ぶ。 */
export function publishNavCollapsed(next: boolean): void {
  if (collapsed === next) return
  collapsed = next
  for (const fn of listeners) fn()
}

export function navCollapsedNow(): boolean {
  return collapsed
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function useNavCollapsed(): boolean {
  return useSyncExternalStore(subscribe, navCollapsedNow, () => false)
}
