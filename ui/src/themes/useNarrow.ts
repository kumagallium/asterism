import { useEffect, useState } from 'react'

// 375px 級の画面か（themes.css の `@media (max-width: 720px)` と同じ境）。
// 会話が下からのシートになり、線の直しの欄は図の下の帯に出る画面。

const QUERY = '(max-width: 720px)'

const read = (): boolean =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(QUERY).matches : false

export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(read)
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(QUERY)
    const on = () => setNarrow(mq.matches)
    on()
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [])
  return narrow
}
