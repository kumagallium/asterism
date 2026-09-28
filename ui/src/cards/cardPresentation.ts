// カード 1 件の「見せ方」（Presentation）の手元保存（契約メモ §1.5）。
// サーバ・appdata には書かない — localStorage `asterism.cardView.<card_id>`
// のみ（try/catch。読めない／壊れているときは既定 = `undefined` に倒れる）。
// `subjectStore.ts` の永続化と同じ流儀（純関数は export してテスト、実行時の
// 読み書きは `typeof localStorage` を確かめてから）。
//
// 同じカードのタイルと詳細、会話のパネル（並んで開いたまま — ADR O61）が同時に
// 画面にあるので、書き込みは購読者に知らせる（`useSyncExternalStore`）。
// localStorage が使えない環境でも、その場の切替は効くように手元の控え
// （`session`）を先に見る。
import { useCallback, useMemo, useSyncExternalStore } from 'react'
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

// このタブの中での最新の値（card_id → 生の文字列。`null` は「消した」）。
// localStorage に書けない環境（private mode 等）でも切替がその場で効くように、
// 読むときはここを先に見る。
const session = new Map<string, string | null>()
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function emit(): void {
  for (const listener of [...listeners]) listener()
}

function readRaw(cardId: string): string | null {
  if (session.has(cardId)) return session.get(cardId) ?? null
  if (typeof localStorage === 'undefined') return null
  try {
    return localStorage.getItem(keyFor(cardId))
  } catch {
    return null
  }
}

/** 実行時の読み出し。`localStorage` が無い環境（テストランタイム・private
 *  mode 等）では、このタブで選んだものが無いかぎり既定（`undefined`）。 */
export function readCardPresentation(cardId: string): Presentation | undefined {
  return parseStoredPresentation(readRaw(cardId))
}

/** 手元の選択を書く（同じカードを描いている全ての部品に届く）。 */
export function writeCardPresentation(cardId: string, presentation: Presentation): void {
  const raw = serializePresentation(presentation)
  session.set(cardId, raw)
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(keyFor(cardId), raw)
    } catch {
      /* private mode 等 — 書けなくても、このタブの中では `session` が効く。 */
    }
  }
  emit()
}

/** 手元の選択を消す（＝カードに保存された見せ方、無ければ既定に戻る）。
 *  会話でカードの見せ方を決め直したとき（差し替え）にも呼ぶ — 古い手元の
 *  選択が残っていると、決め直した見せ方が画面に出ない。 */
export function clearCardPresentation(cardId: string): void {
  session.set(cardId, null)
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.removeItem(keyFor(cardId))
    } catch {
      /* 同上。 */
    }
  }
  emit()
}

export interface CardPresentationHook {
  presentation: Presentation | undefined
  setPresentation: (next: Presentation) => void
  reset: () => void
}

/** 1 カードぶんの「見せ方」の読み書き。閲覧者の手元だけに保存する
 *  （契約メモ §1.5）。`presentation` が `undefined` のときは呼び出し側が
 *  カードに保存された見せ方 → 既定の順に倒す（`effectivePresentation`）。 */
export function useCardPresentation(cardId: string): CardPresentationHook {
  // スナップショットは生の文字列（プリミティブ）— 同じ値なら再描画しない。
  const raw = useSyncExternalStore(
    subscribe,
    () => readRaw(cardId),
    () => null,
  )
  const presentation = useMemo(() => parseStoredPresentation(raw), [raw])

  const setPresentation = useCallback((next: Presentation) => writeCardPresentation(cardId, next), [cardId])
  const reset = useCallback(() => clearCardPresentation(cardId), [cardId])

  return { presentation, setPresentation, reset }
}
