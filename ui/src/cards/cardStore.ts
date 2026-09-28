// 足したカード（CardSpec[]）の読み書き（契約メモ §1-5）。`subjectStore.ts` と
// 同じ流儀 — 永続化先はサーバが決める（ADR app-data-on-disk.md D1）:
// `/api/appdata/info` が単一ユーザーを名乗れば `/api/appdata/cards` に
// PUT/DELETE、そうでなければ（またはまだ 404 の間は）
// `localStorage['asterism.cards.extra']`。ロード直後は同期的に localStorage を
// 読んで即描画し（ちらつき防止）、appdata が使えると分かった時点でサーバの
// 内容に差し替える — `subjectStore.ts` の bootstrap と同じ形。
//
// `card_id`（params の決定論ハッシュ・`measureCardFields.ts` の `cardId`）は
// そのまま appdata の保存キーとして使える（`SubjectItem.thread_id` のような
// 別採番の uuid4 は要らない — IRI/set_id と違って card_id は最初から
// ファイル名として安全な形）。

import { useMemo, useSyncExternalStore } from 'react'
import { initAppData } from '../appdata'
import { deleteAppDataCard, fetchAppDataCards, normalizeCardView, putAppDataCard } from './cardsApi'
import type { CardSpec } from './cardsApi'

// ui-page（SubjectPage.tsx/SetPage.tsx）は `CardSpec` をこのストアの入口
// （`./cardStore`）から import する — 正本は `cardsApi.ts` なのでここでは
// 型だけ再輸出する。
export type { CardSpec } from './cardsApi'

const STORAGE_KEY = 'asterism.cards.extra'

// ---- 純関数（テスト容易性のため export はするが、専用テストファイルは
// このファイルの担当範囲に含まれていない — measureCardFields.test.ts が対象） ---

/** `subjectKey` に属するカードだけを、作った順（`created_at` 昇順・ISO 8601
 *  なので辞書順 = 時系列順）で返す。既定カードの「後ろ」に並べるのは
 *  呼び出し側（SubjectPage/SetPage・ui-page）の責任 — ここでは足した
 *  カードどうしの順番だけを決める。 */
export function cardsForSubject(items: CardSpec[], subjectKey: string): CardSpec[] {
  return items.filter((c) => c.subject_key === subjectKey).sort((a, b) => a.created_at.localeCompare(b.created_at))
}

/** 追加（純粋）。同じ `card_id` が既にあれば置き換える — 同じ params からは
 *  常に同じ card_id なので、二重追加は自然に潰れる。 */
export function addCardItem(items: CardSpec[], item: CardSpec): CardSpec[] {
  return [...items.filter((i) => i.card_id !== item.card_id), item]
}

/** 削除（純粋）。`subjectKey` も合わせて見る — card_id は params の決定論
 *  ハッシュだけで作られるので理論上は主語を跨いで一意とは限らない
 *  （実務上は where にその主語の条件が必ず含まれるので衝突しない想定だが、
 *  「消す」操作は関係ない主語のカードを巻き込まないよう安全側に倒す）。 */
export function removeCardItem(items: CardSpec[], subjectKey: string, cardId: string): CardSpec[] {
  return items.filter((i) => !(i.card_id === cardId && i.subject_key === subjectKey))
}

/** PR F13 §1 実装 (3): `item.view` の形を検証し、違えば `view` だけを落として
 *  既定ビューへ安全側に倒す（カード自体は残す — 他のフィールドはこのファイルの
 *  従来どおり検証しない）。 */
function sanitizeCardView(item: CardSpec): CardSpec {
  if (item.view === undefined) return item
  const view = normalizeCardView(item.view)
  if (view) return view === item.view ? item : { ...item, view }
  const rest: CardSpec = { ...item }
  delete rest.view
  return rest
}

/** localStorage の生の値 → CardSpec[]（純粋）。無い／壊れている／形が違う
 *  場合は空配列に倒す（`subjectStore.ts` の `parseStoredSubjects` と同じ
 *  流儀）。 */
export function parseStoredCards(raw: string | null): CardSpec[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw) as { v?: number; items?: unknown }
    return Array.isArray(parsed.items) ? (parsed.items as CardSpec[]).map(sanitizeCardView) : []
  } catch {
    return []
  }
}

export function serializeCards(items: CardSpec[]): string {
  return JSON.stringify({ v: 1, items })
}

// ---- 永続化つき状態（React の外の単一ストア。subjectStore.ts と同じ形） -------

let items: CardSpec[] = load()
let serverMode = false
const listeners = new Set<() => void>()

function emit(): void {
  for (const l of listeners) l()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): CardSpec[] {
  return items
}

/** 1 主語（`subject_key`）ぶんの「足したカード」を購読する。全件ストアが
 *  変わらない限り同じ配列を返す（`useSyncExternalStore` はスナップショットの
 *  参照同一性を要求するため、絞り込み結果は `useMemo` で安定させる）。 */
export function useCards(subjectKey: string): CardSpec[] {
  const all = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return useMemo(() => cardsForSubject(all, subjectKey), [all, subjectKey])
}

/** React の外から一度だけ読む（購読しない）。 */
export function getAllCards(): CardSpec[] {
  return items
}

/** 全主語ぶんの「足したカード」を購読する（PR F6・`viewpoints.ts` の
 *  `viewpointsFrom` の入力）。`useCards` と違い絞り込まないので、全件ストア
 *  のスナップショットをそのまま返せる（`useMemo` は不要）。 */
export function useAllCards(): CardSpec[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

function load(): CardSpec[] {
  if (typeof localStorage === 'undefined') return []
  try {
    return parseStoredCards(localStorage.getItem(STORAGE_KEY))
  } catch {
    return []
  }
}

function saveLocal(): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY, serializeCards(items))
  } catch {
    /* private mode 等 — 書けないなら諦める（メモリ上の状態はそのまま使える） */
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (serverMode || e.key !== STORAGE_KEY) return
    items = load()
    emit()
  })
  void bootstrap()
}

async function bootstrap(): Promise<void> {
  try {
    const info = await initAppData()
    if (!info.singleUser) return
    const serverItems = await fetchAppDataCards()
    serverMode = true
    items = serverItems.map(sanitizeCardView)
    emit()
  } catch {
    // `/api/appdata/cards` がまだ無い（404）／単一ユーザーでない — localStorage
    // のまま運用する（subjects と同じ流儀）。
  }
}

// ---- ミューテーション（コンポーネントから呼ぶ） -------------------------------

/** 追加して永続化する。同じ `card_id` の既存項目は置き換える。 */
export function addCard(item: CardSpec): void {
  items = addCardItem(items, item)
  emit()
  if (serverMode) void putAppDataCard(item.card_id, item)
  else saveLocal()
}

export function removeCard(subjectKey: string, cardId: string): void {
  items = removeCardItem(items, subjectKey, cardId)
  emit()
  if (serverMode) void deleteAppDataCard(cardId)
  else saveLocal()
}
