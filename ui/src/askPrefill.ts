// 他画面から Ask 画面の次の mount に one-shot で手渡す質問（かんたん S9 の
// 試し質問チップ）。AskView の lastAsk と同じモジュール寿命の受け渡しで、
// リロードで消えるのは許容。別ファイルなのは react-refresh の制約
// （コンポーネントファイルはコンポーネント以外を export できない）のため。
// 自動送信はしない（Ask はキー必須+LLM 課金 — 送るのは人間）。
let pending: string | null = null

export function prefillAskQuestion(question: string) {
  pending = question
}

/** One-shot consume — null when nothing was handed in. */
export function takeAskPrefill(): string | null {
  const q = pending
  pending = null
  return q
}
