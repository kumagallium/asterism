import { useEffect, useState } from 'react'
import { navCollapsedNow, sheetMaxWidth, useNavCollapsed } from './navState'

// 画面の幅の判定。会話が下からのシートになる境（ホームはナビが開いていれば 1100px 以下・畳んでいれば 980px 以下、
// テーマ一覧などの 375px 級は 720px 以下）。

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

/** 375px 級の画面か（`@media (max-width: 720px)` と同じ境）。 */
export function useNarrow(): boolean {
  return useMatches('(max-width: 720px)')
}

/**
 * ホームの会話をシートにする画面か。360px の会話の横に星図が 600px 以上残らない幅では、星図が主・会話は下からのシート。
 * 境はナビの状態で変わる（`navState.ts` の 1 か所）。CSS は `.sky-ws.is-sheet` でこの判定を受ける。
 */
export function useSheet(): boolean {
  return useMatches(`(max-width: ${sheetMaxWidth(useNavCollapsed())}px)`)
}

/** いま会話をシートにする幅か（最初の開閉の初期値に使う）。 */
export function sheetNow(): boolean {
  return read(`(max-width: ${sheetMaxWidth(navCollapsedNow())}px)`)
}
