// 他の画面から相談ドロワーを開く合図。空欄を見つけた「その場」から相談へ行ける
// ようにするための一方向の受け渡しで、AI チャット → 空欄（既存の一括反映）の
// 逆方向にあたる。自動送信はしない — 文面を入れて開くだけで、送るのは人間
// （相談は LLM を呼ぶ。押したつもりのない課金を作らない）。
//
// 別ファイルなのは react-refresh の制約（コンポーネントファイルはコンポーネント
// 以外を export できない）。モジュール寿命なので、リロードで消えるのは許容。
type Listener = (prefill: string) => void

const listeners = new Set<Listener>()

/** ドロワーを開き、入力欄に文面を入れる（送信はしない）。 */
export function requestConsult(prefill: string): void {
  for (const fn of listeners) fn(prefill)
}

export function onConsultRequested(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}
