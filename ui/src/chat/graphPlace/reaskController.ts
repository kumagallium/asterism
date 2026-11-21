// 判定し直しを始めて見守る手続き（R4）。hook（useReask）から外に出した純粋な部分で、依存（API・ジョブの購読・時計）を
// 差し込めるので、押下・二重押し・取り消し・失敗・時間切れを試験で固定できる。
import type { JobHandle } from '../../api'
import type { LlmCredentials } from '../../settings/store'
import { normalizeReaskResult, reaskFailureOf, type ReaskStart } from './reaskApi'
import type { ReaskAction } from './reaskState'

/** ジョブから何も届かない時間の上限（ミリ秒）。心拍は 15 秒おきに届くので、これを超えたら見失ったとして失敗にする。 */
export const STALL_MS = 90_000

export interface ReaskDeps {
  start: (name: string, lang: 'ja' | 'en', creds: LlmCredentials | null) => Promise<ReaskStart>
  subscribe: (
    jobId: string,
    h: {
      onRunning: (d: Record<string, unknown>) => void
      onDone: (r: unknown) => void
      onCancelled: () => void
      onError: (m: string) => void
      onPulse: () => void
    },
  ) => JobHandle
  setTimer: (fn: () => void, ms: number) => unknown
  clearTimer: (t: unknown) => void
}

export interface ReaskLatest {
  lang: 'ja' | 'en'
  getCreds: () => LlmCredentials | null
  /** 終わった（成功・取り消し・失敗）たびに。 */
  onSettled: () => void
}

export interface ReaskControllerOpts extends ReaskLatest {
  dispatch: (a: ReaskAction) => void
  deps: ReaskDeps
  stallMs?: number
}

export interface ReaskController {
  /** 確かめの「はい」。走っている間・準備中の押下は無視する（二重に押しても始めるのは 1 回）。 */
  yes: (name: string) => void
  cancel: () => void
  /** 最新の言語・AI の座標・読み直しに差し替える。 */
  update: (p: ReaskLatest) => void
  /** 開発時の二重マウント（破棄 → 作り直し）で使えるように戻す。 */
  revive: () => void
  dispose: () => void
}

export function createReaskController(o: ReaskControllerOpts): ReaskController {
  let latest: ReaskLatest = { lang: o.lang, getCreds: o.getCreds, onSettled: o.onSettled }
  let active = false
  let disposed = false
  let handle: JobHandle | null = null
  let timer: unknown = null
  const stall = o.stallMs ?? STALL_MS
  const clear = () => {
    if (timer !== null) o.deps.clearTimer(timer)
    timer = null
  }
  const finish = () => {
    clear()
    handle?.close()
    handle = null
    active = false
    latest.onSettled()
  }
  const arm = () => {
    clear()
    timer = o.deps.setTimer(() => {
      timer = null
      if (!active || disposed) return
      o.dispatch({ type: 'failed', kind: 'other' }) // ジョブを見失った（心拍も来ない）
      finish()
    }, stall)
  }
  return {
    yes(name) {
      if (active || disposed) return
      active = true
      o.dispatch({ type: 'starting' })
      const creds = latest.getCreds()
      if (!creds) {
        active = false
        o.dispatch({ type: 'failed', kind: 'aiUnset' })
        return
      }
      void o.deps.start(name, latest.lang, creds).then(
        (st) => {
          if (disposed) return
          o.dispatch({ type: 'started' })
          arm()
          handle = o.deps.subscribe(st.jobId, {
            onPulse: arm,
            onRunning: (d) => o.dispatch({ type: 'progress', index: d.source_index, total: d.source_total, done: d.done, pairs: d.total }),
            onDone: (r) => {
              const result = normalizeReaskResult(r)
              if (result) o.dispatch({ type: 'finished', result: { ...result, skipped: { ...st.skipped, ...result.skipped } } })
              else o.dispatch({ type: 'failed', kind: 'other' })
              finish()
            },
            onCancelled: () => {
              o.dispatch({ type: 'failed', kind: 'cancelled' })
              finish()
            },
            onError: () => {
              o.dispatch({ type: 'failed', kind: 'other' }) // ジョブが見つからない・接続が切れた
              finish()
            },
          })
        },
        (e: unknown) => {
          if (disposed) return
          const kind = reaskFailureOf(e)
          active = false
          o.dispatch({ type: 'failed', kind })
          if (kind === 'nothing') latest.onSettled()
        },
      )
    },
    cancel() {
      void handle?.cancel().catch(() => undefined) // 止まったかはストリームの cancelled で分かる
    },
    update(p) {
      latest = p
    },
    revive() {
      disposed = false
    },
    dispose() {
      disposed = true
      clear()
      handle?.close()
      handle = null
    },
  }
}
