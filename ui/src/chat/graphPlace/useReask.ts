// 判定し直しを始めて見守る hook（R4）。状態は reaskState の reducer。ジョブは取り込みと同じ subscribeJob で見る。
// 終わる（成功・取り消し・失敗）たびに `onSettled` を呼ぶ（台帳・言えなかった種類・足した関係を読み直す）。
import { useCallback, useEffect, useReducer, useRef } from 'react'
import { subscribeJob, type JobHandle } from '../../api'
import type { LlmCredentials } from '../../settings/store'
import { normalizeReaskResult, reaskFailureOf, startReask } from './reaskApi'
import { idle, reaskReducer, type ReaskState } from './reaskState'

export interface ReaskApi {
  state: ReaskState
  /** 確かめの 1 行を開く（N 組）。 */
  ask: (name: string, n: number) => void
  /** 確かめで「はい」。 */
  yes: () => void
  /** 確かめで「やめておく」。 */
  no: () => void
  cancel: () => void
  dismiss: () => void
}

export function useReask(lang: 'ja' | 'en', getCreds: (() => LlmCredentials | null) | undefined, onSettled: () => void): ReaskApi {
  const [state, dispatch] = useReducer(reaskReducer, idle)
  const handle = useRef<JobHandle | null>(null)
  const live = useRef(true)
  const settled = useRef(onSettled)
  useEffect(() => {
    settled.current = onSettled
  }, [onSettled])
  useEffect(() => {
    live.current = true
    return () => {
      live.current = false
      handle.current?.close()
    }
  }, [])
  const ask = useCallback((name: string, n: number) => dispatch({ type: 'ask', name, n }), [])
  const no = useCallback(() => dispatch({ type: 'no' }), [])
  const dismiss = useCallback(() => dispatch({ type: 'dismiss' }), [])
  const cancel = useCallback(() => {
    void handle.current?.cancel().catch(() => undefined) // 止まったかはストリームの cancelled で分かる
  }, [])
  const yes = useCallback(() => {
    if (state.phase !== 'confirm') return
    const name = state.name
    const creds = getCreds?.() ?? null
    dispatch({ type: 'starting' })
    if (!creds) {
      dispatch({ type: 'failed', kind: 'aiUnset' })
      return
    }
    void startReask(name, lang, creds).then(
      (start) => {
        if (!live.current) return
        dispatch({ type: 'started' })
        const end = () => {
          handle.current?.close()
          handle.current = null
          settled.current()
        }
        handle.current = subscribeJob<unknown>(start.jobId, {
          onRunning: (d) => dispatch({ type: 'progress', index: d.source_index, total: d.source_total, done: d.done, pairs: d.total }),
          onDone: (r) => {
            const result = normalizeReaskResult(r)
            if (result) dispatch({ type: 'finished', result: { ...result, skipped: { ...start.skipped, ...result.skipped } } })
            else dispatch({ type: 'failed', kind: 'other' })
            end()
          },
          onCancelled: () => {
            dispatch({ type: 'failed', kind: 'cancelled' })
            end()
          },
          onError: () => {
            dispatch({ type: 'failed', kind: 'other' })
            end()
          },
        })
      },
      (e: unknown) => {
        if (!live.current) return
        dispatch({ type: 'failed', kind: reaskFailureOf(e) })
        if (reaskFailureOf(e) === 'nothing') settled.current()
      },
    )
  }, [state, lang, getCreds])
  return { state, ask, yes, no, cancel, dismiss }
}
