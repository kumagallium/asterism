// 判定し直しを始めて見守る hook（R4）。状態は reaskState の reducer、手続きは reaskController（試験で固定）。
// 終わる（成功・取り消し・失敗）たびに `onSettled` を呼ぶ（台帳・言えなかった種類・足した関係を読み直す）。
import { useCallback, useEffect, useReducer, useState } from 'react'
import { subscribeJob } from '../../api'
import type { LlmCredentials } from '../../settings/store'
import { startReask } from './reaskApi'
import { createReaskController, type ReaskDeps } from './reaskController'
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

const realDeps: ReaskDeps = {
  start: startReask,
  subscribe: (jobId, h) => subscribeJob<unknown>(jobId, h),
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
}

export function useReask(lang: 'ja' | 'en', getCreds: (() => LlmCredentials | null) | undefined, onSettled: () => void): ReaskApi {
  const [state, dispatch] = useReducer(reaskReducer, idle)
  const [ctl] = useState(() =>
    createReaskController({ lang, getCreds: () => getCreds?.() ?? null, onSettled, dispatch, deps: realDeps }),
  )
  // 手続きは最新の言語・AI の座標・読み直しを使う
  useEffect(() => {
    ctl.update({ lang, getCreds: () => getCreds?.() ?? null, onSettled })
  })
  useEffect(() => {
    ctl.revive()
    return () => ctl.dispose()
  }, [ctl])
  const ask = useCallback((name: string, n: number) => dispatch({ type: 'ask', name, n }), [])
  const no = useCallback(() => dispatch({ type: 'no' }), [])
  const dismiss = useCallback(() => dispatch({ type: 'dismiss' }), [])
  const yes = useCallback(() => {
    if (state.phase === 'confirm') ctl.yes(state.name)
  }, [state, ctl])
  return { state, ask, yes, no, cancel: ctl.cancel, dismiss }
}
