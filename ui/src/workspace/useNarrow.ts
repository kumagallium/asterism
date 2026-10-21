import { useEffect, useState } from 'react'

// 画面の幅の判定。会話が下からのシートになる境（ホームは 980px 以下・テーマ一覧などの 375px 級は 720px 以下）。

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
 * ホームの会話をシートにする画面か（`sky.css` の `@media (max-width: 980px)` と同じ境）。
 * 360px の会話の横に星図が 600px 以上残らない幅では、星図が主・会話は下からのシート。
 */
export function useSheet(): boolean {
  return useMatches('(max-width: 980px)')
}
