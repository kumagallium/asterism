// IME（日本語入力など）の「変換確定の Enter」を、送る Enter と見分ける（純関数）。
//
// ブラウザで確定 Enter のイベントの順と印が違う:
// - Chrome/Blink: keydown(keyCode=229, isComposing=true) → compositionend
// - WebKit（Safari・Tauri の WKWebView＝デスクトップ版）: compositionend → keydown(keyCode=13, isComposing=false)
//   確定の Enter が「普通の Enter」に見えるので、isComposing／keyCode 229 の検査だけでは素通りする。
//   compositionend からの経過時間で吸収する（Graphium の `lib/ime-enter.ts` と同じ考え方）。

export interface ImeKeySignals {
  /** onCompositionStart／End で追っている変換中の印（いちばん確実）。 */
  composingNow: boolean
  /** KeyboardEvent.isComposing。 */
  isComposing: boolean
  /** keydown の keyCode（変換中は 229 を返すブラウザ向け）。 */
  keyCode: number
  /** 直近の compositionend からの経過ミリ秒。 */
  msSinceCompositionEnd: number
}

/** WebKit で compositionend の直後に届く確定の keydown を吸収する時間の幅（ms）。
 *  人が確定したあと次の Enter を押すまでの間隔より十分短くする。 */
export const IME_CONFIRM_KEY_WINDOW_MS = 50

/** この keydown が変換の一部（確定の Enter など）で、送る・決めるとして扱ってはいけないか。 */
export function isImeKeyEvent(s: ImeKeySignals): boolean {
  if (s.composingNow || s.isComposing || s.keyCode === 229) return true
  return s.msSinceCompositionEnd < IME_CONFIRM_KEY_WINDOW_MS
}

export interface EnterSubmitGuard extends ImeKeySignals {
  isEnter: boolean
  shiftKey: boolean
}

/** Enter で送ってよいか（Shift+Enter は改行・変換中と確定の Enter は送らない）。 */
export function shouldSubmitOnEnter(g: EnterSubmitGuard): boolean {
  if (!g.isEnter || g.shiftKey) return false
  return !isImeKeyEvent(g)
}
