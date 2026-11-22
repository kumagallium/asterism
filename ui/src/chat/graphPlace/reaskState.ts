// 判定し直しの画面の状態（純関数の reducer・R4）。ジョブの手続きは hook（useReask）に置き、ここは遷移だけ。
import type { ReaskFailKind, ReaskResult } from './reaskApi'

export type ReaskState =
  | { phase: 'idle' }
  | { phase: 'confirm'; name: string; n: number }
  | { phase: 'starting'; name: string }
  | { phase: 'running'; name: string; index: number | null; total: number | null; done: number | null; pairs: number | null }
  | { phase: 'done'; name: string; result: ReaskResult }
  | { phase: 'failed'; name: string; kind: ReaskFailKind }

export type ReaskAction =
  | { type: 'ask'; name: string; n: number }
  | { type: 'no' }
  | { type: 'starting' }
  | { type: 'started' }
  | { type: 'progress'; index?: unknown; total?: unknown; done?: unknown; pairs?: unknown }
  | { type: 'finished'; result: ReaskResult }
  | { type: 'failed'; kind: ReaskFailKind }
  | { type: 'dismiss' }

export const idle: ReaskState = { phase: 'idle' }
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null)
export const busyPhase = (s: ReaskState): boolean => s.phase === 'starting' || s.phase === 'running'

export function reaskReducer(state: ReaskState, a: ReaskAction): ReaskState {
  switch (a.type) {
    case 'ask':
      // 走っている間は 2 本目の確かめを開かない
      return busyPhase(state) ? state : { phase: 'confirm', name: a.name, n: a.n }
    case 'no':
      return state.phase === 'confirm' ? idle : state
    case 'starting':
      return state.phase === 'confirm' ? { phase: 'starting', name: state.name } : state
    case 'started':
      return state.phase === 'starting' ? { phase: 'running', name: state.name, index: null, total: null, done: null, pairs: null } : state
    case 'progress':
      if (state.phase !== 'running') return state
      return {
        ...state,
        index: num(a.index) ?? state.index,
        total: num(a.total) ?? state.total,
        done: num(a.done) ?? state.done,
        pairs: num(a.pairs) ?? state.pairs,
      }
    case 'finished':
      return busyPhase(state) ? { phase: 'done', name: (state as { name: string }).name, result: a.result } : state
    case 'failed':
      return busyPhase(state) ? { phase: 'failed', name: (state as { name: string }).name, kind: a.kind } : state
    case 'dismiss':
      return busyPhase(state) ? state : idle
  }
}
