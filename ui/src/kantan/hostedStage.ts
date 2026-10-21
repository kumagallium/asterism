// かんたんウィザードを「ホームの右の領域」に置いたとき（UI 再設計 PR 8c）、左の会話が知っておくこと。
//   ・いまの段（RecipeCard の ①〜⑦）— ウィザードが押し出す（読み戻さない）。会話の送り先を段で決めるのに使う。
//   ・③の会話（DatasetChat）が mount されているか／外のボタンに呼ばれたか — DatasetChat が押し出す。
//   ・会話の置き場（左の欄の div）— 左の欄が置き、DatasetChat がそこへ portal する。
// ウィザードの状態機械は読まない・書かない（一方向）。別ファイルなのは react-refresh の制約。
import { createContext, useSyncExternalStore } from 'react'

export type StageNo = 1 | 2 | 3 | 4 | 5 | 6 | 7

export interface HostedState {
  /** いまの段。ウィザードが出ていないとき null。 */
  recipe: StageNo | null
  /** 公開まで終わった（⑦のできあがり）。 */
  done: boolean
  /** ③の会話（DatasetChat・設計ができているとき）が出ている。 */
  bindReady: boolean
  /** ③の会話を開く合図が来た回数（「空欄の意味を AI に相談して埋める」など）。 */
  bindAsks: number
}

const INITIAL: HostedState = { recipe: null, done: false, bindReady: false, bindAsks: 0 }
let state: HostedState = INITIAL
const listeners = new Set<() => void>()

function set(patch: Partial<HostedState>): void {
  const next = { ...state, ...patch }
  if ((Object.keys(patch) as (keyof HostedState)[]).every((k) => next[k] === state[k])) return
  state = next
  for (const fn of listeners) fn()
}

/** ウィザードが、いまの段を伝える。 */
export function setHostedStage(recipe: StageNo, done: boolean): void {
  set({ recipe, done })
}
/** ウィザードが外れた（unmount）。 */
export function clearHostedStage(): void {
  set({ recipe: null, done: false })
}
/** ③の会話が出た／消えた。 */
export function setBindReady(on: boolean): void {
  set({ bindReady: on })
}
/** ③の会話を開く合図（外のボタン）。 */
export function askBind(): void {
  set({ bindAsks: state.bindAsks + 1 })
}

export function getHostedState(): HostedState {
  return state
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function useHostedState(): HostedState {
  return useSyncExternalStore(subscribe, getHostedState, getHostedState)
}

/** 左の会話の「③の会話の置き場」。無ければ（ホームの外で開いたウィザード）従来どおり表の右に置く。 */
export const ChatSlotContext = createContext<HTMLElement | null>(null)
