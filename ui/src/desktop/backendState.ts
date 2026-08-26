// バックエンド死活の状態遷移だけを切り出した純粋関数。
// window/fetch/Tauri に一切依存しないので、node --test でそのまま検証できる
// （backend.ts は ./updater への拡張子なし import を持ち、bundler 専用の解決に
// 依存するため node の ESM ローダー単体では読み込めない — テスト対象をここに分ける）。

export type BackendState = 'ok' | 'down'

/** これだけ連続で失敗したら down にする。 */
export const DOWN_THRESHOLD = 2

/**
 * 1 回のプローブ結果から次の状態を決める。
 *
 * - ok（生きている）中の 1 回の失敗だけでは down にしない（DOWN_THRESHOLD 回連続で down）。
 * - down 中は、生き返る（probeOk）まで down のまま。生き返れば ok・カウントは 0 に戻る。
 */
export function nextBackendState(
  prev: BackendState,
  prevConsecutiveFailures: number,
  probeOk: boolean,
): { state: BackendState; consecutiveFailures: number } {
  if (probeOk) {
    return { state: 'ok', consecutiveFailures: 0 }
  }
  const failures = prevConsecutiveFailures + 1
  const nextState: BackendState = prev === 'down' || failures >= DOWN_THRESHOLD ? 'down' : 'ok'
  return { state: nextState, consecutiveFailures: failures }
}
