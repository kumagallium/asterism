// 押した点の中身（`GET /api/chat/contents`）の読み取り（PR 11k）。
// key と sky の signature（手番のあとに sky が読み直されたら読み直す）で fetch する。同じ組の返りは短く cache（Map・上限 50）。
import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchContents, type ContentsKey, type ContentsLoad } from './chatApi'

export type ContentsState = { status: 'idle' } | { status: 'loading' } | { status: 'error' } | { status: 'ok'; data: ContentsLoad; refreshing?: boolean }

export const CACHE_MAX = 50

/** 上限を超えたら古い順に捨てて入れる。 */
export function putCache<V>(m: Map<string, V>, id: string, v: V, max = CACHE_MAX): void {
  while (m.size >= max) m.delete(m.keys().next().value as string)
  m.set(id, v)
}

export const keyString = (key: ContentsKey): string => JSON.stringify(key)

export function useContents(key: ContentsKey | null, lang: 'ja' | 'en', signature: string | number): { state: ContentsState; retry: () => void } {
  const cache = useRef<Map<string, ContentsLoad>>(new Map())
  // 返りは「どの組の返りか」と一緒に持つ。いまの組と違えば loading（effect の中で同期に setState しない）
  const [done, setDone] = useState<{ id: string; ks: string; state: ContentsState } | null>(null)
  const [retryTick, setRetryTick] = useState(0)
  const ks = key ? keyString(key) : null
  const id = ks ? `${lang}|${signature}|${ks}` : null

  useEffect(() => {
    if (!id || !key || !ks) return
    const ctl = new AbortController()
    const hit = cache.current.get(id)
    const run = hit ? Promise.resolve(hit) : fetchContents(key, lang, ctl.signal)
    run
      .then((data) => {
        if (ctl.signal.aborted) return
        if (!hit) putCache(cache.current, id, data)
        setDone({ id, ks, state: { status: 'ok', data } })
      })
      .catch(() => !ctl.signal.aborted && setDone({ id, ks, state: { status: 'error' } }))
    return () => ctl.abort()
    // key は id から決まる（オブジェクトの同一性では読み直さない）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, retryTick])

  const retry = useCallback(() => {
    setDone(null)
    setRetryTick((n) => n + 1)
  }, [])
  return { state: pickState(done, id, ks), retry }
}

/** 同じ key で signature だけ変わったとき（sky の読み直し）は、前の表を残したまま refreshing にする。 */
export function pickState(done: { id: string; ks: string; state: ContentsState } | null, id: string | null, ks: string | null): ContentsState {
  if (!id) return { status: 'idle' }
  if (done && done.id === id) return done.state
  if (done && done.ks === ks && done.state.status === 'ok') return { status: 'ok', data: done.state.data, refreshing: true }
  return { status: 'loading' }
}
