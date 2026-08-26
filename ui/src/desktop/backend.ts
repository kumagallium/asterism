// バックエンドの死活監視（デスクトップ版のみ・updater.ts と同じ流儀）。
//
// SPA を配信しているのはローカルの Asterism バックエンドそのものなので、それが
// 死ぬと API が全滅する。真因（バックエンド不在）が画面に出ないと「読み込み直す」
// を押しても永久に直らない — これに気づかせ、1 クリックで直す手段を用意する。
//
// 自動再起動はしない（死因が分からないまま再起動を繰り返すと、失敗にすら気づけず
// ログも流れてしまう）。あくまで「気づかせて、押したら直す」までに留める。
//
// isTauri() が false（ブラウザ/web 配備）では何もしない。Tauri の JS パッケージは
// restartBackend() の中でだけ dynamic import する（web バンドルに静的に入らない）。
//
// 状態遷移の純粋関数（nextBackendState）は backendState.ts に切り出してある
// （Tauri/DOM に依存しないユニットテスト対象）。

import { nextBackendState, type BackendState } from './backendState'
import { isTauri } from './updater'

export type { BackendState }

const HEALTH_URL = '/health'
/** 正常時の確認間隔。 */
const OK_INTERVAL_MS = 10_000
/** 1 回失敗した直後、次を確かめるまでの間隔（瞬間的な取りこぼしを疑ってすぐ再確認）。 */
const RECHECK_DELAY_MS = 2_000
/** down の間、復帰していないか確かめる間隔。 */
const DOWN_INTERVAL_MS = 3_000

let state: BackendState = 'ok'
let consecutiveFailures = 0
let watching = false

const listeners = new Set<(s: BackendState) => void>()

function setState(next: BackendState): void {
  if (state === next) return
  state = next
  for (const listener of listeners) listener(state)
}

/** 現在の死活状態を購読する。返り値は解除関数。 */
export function subscribeBackendState(listener: (s: BackendState) => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * 現在の死活状態を読む（updater.ts の pendingUpdate() と対称）。
 * `setState` は同値なら通知しないので、モジュール側が既に down のときに
 * バナーが（再）マウントされると、購読だけでは次に down→down が来ず
 * 永久に気づけない。マウント時の初期値をこれで読むことでその穴を防ぐ。
 */
export function getBackendState(): BackendState {
  return state
}

/** `/health` を叩く。応答があれば（ステータスは何でもよい）生存とみなす。 */
async function probe(): Promise<boolean> {
  try {
    await fetch(HEALTH_URL, { cache: 'no-store' })
    return true
  } catch {
    return false
  }
}

async function tick(): Promise<void> {
  const ok = await probe()
  const result = nextBackendState(state, consecutiveFailures, ok)
  consecutiveFailures = result.consecutiveFailures
  setState(result.state)

  // down 中は 3 秒間隔で復帰待ち。ok 中に失敗した直後だけ 2 秒後にすぐ再確認
  // （瞬間的な取りこぼしでバナーを出さないため）。それ以外は通常の 10 秒間隔。
  const delay = state === 'down' ? DOWN_INTERVAL_MS : !ok ? RECHECK_DELAY_MS : OK_INTERVAL_MS
  setTimeout(() => void tick(), delay)
}

/** 監視を開始する（多重起動しない）。`main.tsx` から 1 回呼ぶ。 */
export function startBackendWatch(): void {
  if (!isTauri()) return
  if (watching) return
  watching = true
  void tick()
}

/**
 * Tauri command `restart_backend` を呼ぶ。成功すれば新しいバックエンドのポート番号、
 * 失敗すれば Rust 側の Err 文字列で reject する。
 */
export async function restartBackend(): Promise<number> {
  const { invoke } = await import('@tauri-apps/api/core')
  return await invoke<number>('restart_backend')
}
