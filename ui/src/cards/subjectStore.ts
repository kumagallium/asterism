// 私の一覧（SubjectItem[]）の読み書き（契約メモ §6.1）。
//
// 永続化先はサーバが決める（`ui/src/appdata.ts` と同じ規律 — ADR
// app-data-on-disk.md D1）: `/api/appdata/info` が単一ユーザーを名乗れば
// `/api/appdata/subjects` に PUT/DELETE、そうでなければ（またはまだ 404 の間は）
// `localStorage['asterism.cards.subjects']`。ロード直後は同期的に localStorage を
// 読んで即描画し（ちらつき防止）、appdata が使えると分かった時点でサーバの内容に
// 差し替える — `threadStore.ts` の bootstrap と同じ形。
//
// 純関数（`addSubject`/`removeSubject`/`sortSubjects`）はテスト対象
// （`subjectStore.test.ts`）。ミューテーション API（`*AndPersist`）は永続化つきの
// 薄いラッパで、こちらはテストしない。`backfillDatasetIds`（appdata への書き戻しを
// 伴う移行ロジック）は例外的に export し、`cardsApi.ts` の `resolveSubject`/
// `classSchema` をモックしてテストする（埋め戻しの成否とフォールバックが対象）。

import { useSyncExternalStore } from 'react'
import { initAppData } from '../appdata'
import {
  classSchema,
  deleteAppDataSubject,
  fetchAppDataSubjects,
  putAppDataSubject,
  resolveSubject,
  type SubjectItem,
} from './cardsApi'

const STORAGE_KEY = 'asterism.cards.subjects'

// ---- 純関数（テスト対象） ---------------------------------------------------

/** own → open の順、各グループの中は created_at 降順（新しい方が上）。
 *  ISO 8601 文字列前提（辞書順 = 時系列順）。 */
export function sortSubjects(items: SubjectItem[]): SubjectItem[] {
  const rank = (source: SubjectItem['source']): number => (source === 'own' ? 0 : 1)
  return [...items].sort((a, b) => {
    const bySource = rank(a.source) - rank(b.source)
    if (bySource !== 0) return bySource
    return b.created_at.localeCompare(a.created_at)
  })
}

/** 追加（純粋）。同じ `subject_key` が既にあれば置き換える（重複させない）。
 *  並びは呼び出し側が {@link sortSubjects} で作る — この関数は差し込みだけ。 */
export function addSubject(items: SubjectItem[], item: SubjectItem): SubjectItem[] {
  return [...items.filter((i) => i.subject_key !== item.subject_key), item]
}

/** 削除（純粋）。`id` に一致するものを 1 件取り除く。 */
export function removeSubject(items: SubjectItem[], id: string): SubjectItem[] {
  return items.filter((i) => i.id !== id)
}

// ---- 新規 id ------------------------------------------------------------------

/** appdata の thread_id と同じ規律（uuid4）— サーバ側のバリデータが要求する形。 */
export function newSubjectId(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c?.randomUUID) return c.randomUUID()
  // crypto.randomUUID が無い環境向けの保険（appdata 保存は使えないが localStorage
  // 運用は壊れない）。
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

// ---- 永続化つき状態（React の外の単一ストア。threadStore.ts と同じ形） -------

let items: SubjectItem[] = load()
let loaded = false
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

function getSnapshot(): SubjectItem[] {
  return items
}

function getLoadedSnapshot(): boolean {
  return loaded
}

export function useSubjects(): SubjectItem[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

/** appdata かどうかの判定が終わったか（サーバの内容で置き換わる前に一覧が空に
 *  見えて「まだありません」が一瞬出る、を避けたい呼び出し側向け）。 */
export function useSubjectsLoaded(): boolean {
  return useSyncExternalStore(subscribe, getLoadedSnapshot, getLoadedSnapshot)
}

/** React の外から一度だけ読む（購読しない）。 */
export function getAllSubjects(): SubjectItem[] {
  return items
}

/** localStorage の生の値 → SubjectItem[]（純粋・テスト対象）。無い／壊れている／
 *  形が違う場合は空配列に倒す — これが「フォールバック」の中身。 */
export function parseStoredSubjects(raw: string | null): SubjectItem[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw) as { v?: number; items?: unknown }
    return Array.isArray(parsed.items) ? (parsed.items as SubjectItem[]) : []
  } catch {
    return []
  }
}

/** SubjectItem[] → localStorage に積む文字列（純粋・{@link parseStoredSubjects}
 *  と対の往復）。 */
export function serializeSubjects(items: SubjectItem[]): string {
  return JSON.stringify({ v: 1, items })
}

function load(): SubjectItem[] {
  if (typeof localStorage === 'undefined') return []
  try {
    return parseStoredSubjects(localStorage.getItem(STORAGE_KEY))
  } catch {
    return []
  }
}

function saveLocal(): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY, serializeSubjects(items))
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
  void bootstrap().then(() => backfillDatasetIds())
}

async function bootstrap(): Promise<void> {
  try {
    const info = await initAppData()
    if (!info.singleUser) return
    const serverItems = await fetchAppDataSubjects()
    serverMode = true
    items = serverItems
  } catch {
    // `/api/appdata/subjects` がまだ無い（404）／単一ユーザーでない — localStorage
    // のまま運用する（§5: 「単一ユーザーでないサーバでは 404 のまま → ui は
    // localStorage に落とす」）。
  } finally {
    loaded = true
    emit()
  }
}

/** `class_iri` を「まだ試していない」(undefined) と区別するための、
 *  「確認済み・rdf:type が無く恒久的に class_iri が無い」印（空文字）。
 *  個体の `resolveSubject` 呼び出しが例外を投げずに完了し、かつ `class_iri: null`
 *  が返ってきた場合に使う。空文字は `railTree.ts` の `!s.class_iri` 判定では
 *  undefined と同じ「その他」節に落ちるが、{@link backfillDatasetIds} の対象
 *  フィルタ（`=== undefined`）からは外れるため、起動のたびに同じ個体を
 *  再フェッチし続ける非収束を防げる（checker 指摘: rdf:type を持たない個体は
 *  `pick_class_iri` が常に None を返すため、undefined のまま据え置くと収束しない）。 */
const CLASS_IRI_UNRESOLVABLE = ''

/** 既存の保存済み項目には `dataset_id`/`class_iri` が無いことがある（契約メモ
 * contract_pr_f9.md §1-2）。読み込み直後に 1 回だけ、無いものだけを埋め戻す
 * （個体は `resolveSubject` の `dataset_id`/`class_iri`/`class_label`、絞り込みは
 * `spec.class`（＝`class_iri` そのもの）と `classSchema(spec.class)` の
 * `dataset_id`/`dataset_label`/`label` から）。追加の fetch は増やさない —
 * 既存の `dataset_id` 埋め戻しと同じ 1 回の呼び出しで両方を埋める。失敗した
 * 項目は諦める — `class_iri` が無いままレールの「その他」節に出るだけで、
 * 致命的にはしない（ただし通信エラー等で呼び出し自体が例外を投げた場合のみ
 * 次回また対象になる。呼び出しが成功したのに rdf:type が無い個体は
 * {@link CLASS_IRI_UNRESOLVABLE} を書き戻し、二度と再フェッチしない）。 */
export async function backfillDatasetIds(): Promise<void> {
  const targets = items.filter((i) => i.dataset_id === undefined || i.class_iri === undefined)
  for (const target of targets) {
    try {
      let datasetId: string | null | undefined
      let datasetLabel: string | null | undefined
      let classIri: string | undefined
      let classLabel: string | null | undefined
      if (target.kind === 'individual') {
        const resolved = await resolveSubject(target.id)
        datasetId = resolved.dataset_id
        datasetLabel = resolved.dataset_label
        // 呼び出しは成功した — `class_iri: null`（rdf:type 無し）は「まだ試して
        // いない」(undefined) と取り違えない。
        classIri = resolved.class_iri ?? CLASS_IRI_UNRESOLVABLE
        classLabel = resolved.class_label
      } else if (target.spec) {
        classIri = target.spec.class
        const schema = await classSchema(target.spec.class)
        datasetId = schema?.dataset_id
        datasetLabel = schema?.dataset_label
        classLabel = schema?.label
      }
      if (!datasetId && !classIri) continue
      const updated: SubjectItem = {
        ...target,
        dataset_id: datasetId ?? target.dataset_id,
        dataset_label: (datasetLabel ?? target.dataset_label) ?? undefined,
        // classIri は今回試みたなら（個体・絞り込みどちらの分岐でも）確定値
        // （実 IRI か {@link CLASS_IRI_UNRESOLVABLE}）を持つ — undefined
        // に巻き戻さない。分岐に入らなかった（想定外の形の項目）ときだけ
        // 既存値を保つ。
        class_iri: classIri !== undefined ? classIri : target.class_iri,
        class_label: classLabel ?? target.class_label,
      }
      items = items.map((i) => (i.subject_key === target.subject_key ? updated : i))
      if (serverMode && updated.thread_id) void putAppDataSubject(updated.thread_id, updated)
      else if (!serverMode) saveLocal()
    } catch {
      // best-effort: この項目はこの回だけ「その他」節に出る（通信エラー等は
      // 次回また対象になる）。
    }
  }
  emit()
}

// ---- ミューテーション（コンポーネントから呼ぶ） -------------------------------

/** 追加して永続化する。同じ `subject_key` の既存項目は置き換える。appdata 運用
 *  では保存キー（`thread_id`・uuid4）が無ければここで採番する — `id`
 *  （IRI/set_id）はファイル名として使えないため（cardsApi.ts の SubjectItem 参照）。 */
export function addSubjectAndPersist(item: SubjectItem): void {
  const toStore = serverMode && !item.thread_id ? { ...item, thread_id: newSubjectId() } : item
  items = addSubject(items, toStore)
  emit()
  if (serverMode && toStore.thread_id) void putAppDataSubject(toStore.thread_id, toStore)
  else if (!serverMode) saveLocal()
}

export function removeSubjectAndPersist(id: string): void {
  const existing = items.find((i) => i.id === id)
  items = removeSubject(items, id)
  emit()
  if (!existing) return
  if (serverMode && existing.thread_id) void deleteAppDataSubject(existing.thread_id)
  else if (!serverMode) saveLocal()
}
