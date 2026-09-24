// カード 1 件の「見せ方」（Presentation）の手元保存（契約メモ §1.5）。
// サーバ・appdata には書かない — localStorage `asterism.cardView.<card_id>`
// のみ（try/catch。読めない／壊れているときは既定 = `undefined` に倒れる）。
// `subjectStore.ts` の永続化と同じ流儀（純関数は export してテスト、実行時の
// 読み書きは `typeof localStorage` を確かめてから）。
import { useCallback, useState } from 'react'
import type { Presentation } from './presentation'

function keyFor(cardId: string): string {
  return `asterism.cardView.${cardId}`
}

/** localStorage の生の値 → Presentation（純粋・テスト対象）。無い／壊れている／
 *  形が違う（オブジェクトでない）場合は既定（`undefined`）に倒す。 */
export function parseStoredPresentation(raw: string | null): Presentation | undefined {
  if (!raw) return undefined
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return undefined
    return parsed as Presentation
  } catch {
    return undefined
  }
}

/** Presentation → localStorage に積む文字列（純粋・{@link parseStoredPresentation}
 *  と対の往復）。 */
export function serializePresentation(presentation: Presentation): string {
  return JSON.stringify(presentation)
}

/** 実行時の読み出し。`localStorage` が無い環境（テストランタイム・private
 *  mode 等）では常に既定（`undefined`）。 */
export function readCardPresentation(cardId: string): Presentation | undefined {
  if (typeof localStorage === 'undefined') return undefined
  try {
    return parseStoredPresentation(localStorage.getItem(keyFor(cardId)))
  } catch {
    return undefined
  }
}

function writeCardPresentation(cardId: string, presentation: Presentation): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(keyFor(cardId), serializePresentation(presentation))
  } catch {
    /* private mode 等 — 書けないなら諦める（描画は既定に倒れるだけ）。 */
  }
}

function clearCardPresentation(cardId: string): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.removeItem(keyFor(cardId))
  } catch {
    /* 同上。 */
  }
}

export interface CardPresentationHook {
  presentation: Presentation | undefined
  setPresentation: (next: Presentation) => void
  reset: () => void
}

/** 1 カードぶんの「見せ方」の読み書き。閲覧者の手元だけに保存する
 *  （契約メモ §1.5）。`presentation` が `undefined` のときは呼び出し側が
 *  `viewFor(tool, rows, undefined)`（＝既定ビュー）を使う。 */
export function useCardPresentation(cardId: string): CardPresentationHook {
  const [presentation, setPresentationState] = useState<Presentation | undefined>(() => readCardPresentation(cardId))
  // card_id が変わったら、その card の保存値を読み直す（`CardDetail.tsx` の
  // `tabFor` と同じ「prop が変わったら state を調整する」パターン — effect
  // を使わない）。
  const [cardFor, setCardFor] = useState(cardId)
  if (cardFor !== cardId) {
    setCardFor(cardId)
    setPresentationState(readCardPresentation(cardId))
  }

  const setPresentation = useCallback(
    (next: Presentation) => {
      writeCardPresentation(cardId, next)
      setPresentationState(next)
    },
    [cardId],
  )

  const reset = useCallback(() => {
    clearCardPresentation(cardId)
    setPresentationState(undefined)
  }, [cardId])

  return { presentation, setPresentation, reset }
}
