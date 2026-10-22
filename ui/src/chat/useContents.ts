// 押した点の中身（`GET /api/chat/contents`）の読み取り（PR 11k）。
// key と sky の signature（手番のあとに sky が読み直されたら読み直す）で fetch する。同じ組の返りは短く cache（Map・上限 50）。
import { useEffect, useRef, useState } from 'react'
import { fetchContents, type ContentsKey, type ContentsLoad } from './chatApi'

export type ContentsState = { status: 'idle' } | { status: 'loading' } | { status: 'error' } | { status: 'ok'; data: ContentsLoad }

export const CACHE_MAX = 50

/** 上限を超えたら古い順に捨てて入れる。 */
export function putCache<V>(m: Map<string, V>, id: string, v: V, max = CACHE_MAX): void {
  while (m.size >= max) m.delete(m.keys().next().value as string)
  m.set(id, v)
}

export const keyString = (key: ContentsKey): string => JSON.stringify(key)

export function useContents(key: ContentsKey | null, lang: 'ja' | 'en', signature: string | number): ContentsState {
  const cache = useRef<Map<string, ContentsLoad>>(new Map())
  // 返りは「どの組の返りか」と一緒に持つ。いまの組と違えば loading（effect の中で同期に setState しない）
  const [done, setDone] = useState<{ id: string; state: ContentsState } | null>(null)
  const ks = key ? keyString(key) : null
  const id = ks ? `${lang}|${signature}|${ks}` : null

  useEffect(() => {
    if (!id || !key) return
    const ctl = new AbortController()
    const hit = cache.current.get(id)
    const run = hit ? Promise.resolve(hit) : fetchContents(key, lang, ctl.signal)
    run
      .then((data) => {
        if (ctl.signal.aborted) return
        if (!hit) putCache(cache.current, id, data)
        setDone({ id, state: { status: 'ok', data } })
      })
      .catch(() => !ctl.signal.aborted && setDone({ id, state: { status: 'error' } }))
    return () => ctl.abort()
    // key は id から決まる（オブジェクトの同一性では読み直さない）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (!id) return { status: 'idle' }
  return done && done.id === id ? done.state : { status: 'loading' }
}
