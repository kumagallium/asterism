// かんたんウィザードを「ホームの右の領域」に置いたとき（UI 再設計 PR 8c）、左の会話が知っておくこと。
//   ・いまの段（RecipeCard の ①〜⑦）— ウィザードが押し出す（読み戻さない）。会話の送り先を段で決めるのに使う。
//   ・③の会話（DatasetChat）が mount されているか／外のボタンに呼ばれたか — DatasetChat が押し出す。
//   ・会話の置き場（左の欄の div）— 左の欄が置き、DatasetChat がそこへ portal する。
// ウィザードの状態機械は読まない・書かない（一方向）。別ファイルなのは react-refresh の制約。
import { createContext, useCallback, useSyncExternalStore } from 'react'

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

// 左の会話の入力欄の下書き。ホームの会話（GraphChat）とデータを置く間の会話（ConsultDrawer の埋め込み）は、
// 左の欄ごと差し替わる（mount し直す）— 未送信の文字をここに持ち上げて、差し替えても残し、両方の欄が同じ値を共有する。
let inputText = ''
const inputListeners = new Set<() => void>()

export function getSharedInput(): string {
  return inputText
}
export function setSharedInput(text: string): void {
  if (text === inputText) return
  inputText = text
  for (const fn of inputListeners) fn()
}
function subscribeInput(fn: () => void): () => void {
  inputListeners.add(fn)
  return () => {
    inputListeners.delete(fn)
  }
}
/** 左の会話の入力欄の下書き（`[値, 更新]`）。 */
export function useSharedInput(): [string, (text: string) => void] {
  const v = useSyncExternalStore(subscribeInput, getSharedInput, getSharedInput)
  const set = useCallback((text: string) => setSharedInput(text), [])
  return [v, set]
}

// ⑦完了のとき、右の領域の「閉じる」は ⑦の「星図で見る」と同じ道（公開した種類を灯して星図へ・手元のファイルを捨てる）を通る。
// 道の本体（kzDatasetId・種類の取得）はウィザードが持つので、ウィザードが⑦のあいだだけ口を置く。押し出すだけで読み戻さない。
let skyOpener: (() => void) | null = null
export function setHostedSkyOpener(fn: (() => void) | null): void {
  skyOpener = fn
}
/** ⑦完了の口があれば呼ぶ（呼べたら true）。無ければ呼び出し側が従来どおり閉じる。 */
export function openHostedSky(): boolean {
  if (!skyOpener) return false
  skyOpener()
  return true
}
