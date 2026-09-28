// 「1 件／絞り込み × カード」の api 薄いラッパ（契約メモ contract_pr_c.md §3.4・
// §4.3・§5、+ §2 は下の notes 参照）。個別 fetch + authHeaders() + throwApiError
// （api.ts の流儀）。ここでは判断をしない — サーバの戻り値の形をそのまま
// TypeScript の型に写すだけ。
//
// SubjectKey・SetSpec・SubjectItem・ShapeMatch は契約メモ §1・§5.4・§5.5・§5.6
// （引き継ぎ書 §5.4〜§5.6）の形をそのまま持つ。api 側（C1）の実装が揃うまでは
// fetch が失敗するだけで、このファイル自体のビルドは通る。
//
// 関数名は c2-shell の当初案から、並行実装（CardDetail/CardTile/SetForm/
// SubjectPage/SetPage/PlaceView）が既に './cardsApi' から import していた名前
// （`runCard`・`defaultCardsForSubject`・`defaultCardsForSet`・`classSchema`・
// `createStaging`・`inspectPlace`・`commitPlace`）に合わせてある — 5+ ファイルを
// 編集せずに済む側を優先した（詳細は notes 参照）。`classSchema`（契約メモ §2）は
// 本来 C1-schema 担当だが、SetForm.tsx がここからの import 前提で書かれていたため
// 薄いラッパだけ足した。

import { throwApiError } from '../api'
import { authHeaders } from '../authToken'
import type { ItemSpec, OutputKind, Row } from './viewSpec'

// ---------------------------------------------------------------------------
// §1 主語の鍵（SubjectKey）／§5.5 絞り込み仕様（SetSpec）
// ---------------------------------------------------------------------------

/** `where[].at` / `order_by.at`（値どうしの対応づけ）。Phase 1 は受け付けない
 *  （送ると 400）— ADR 残課題としての逃げ道の印。 */
export interface SetWhereAt {
  property: string
  value: unknown
  tolerance?: number
}

/** `op` ごとの `value` の形: gt/lt/eq = 数値、between = `{min,max}`、in = 文字列の
 *  配列（分類の複数選択）。並行実装（cardTypes.ts）が固定した解釈に合わせてある
 *  — 契約メモ自体は `value: …` としか書いていない。 */
export type SetWhereValue = number | string | string[] | { min: number; max: number }

export interface SetWhereClause {
  property: string
  op: 'gt' | 'lt' | 'eq' | 'between' | 'in'
  value: SetWhereValue
  /** Phase 1 は 400。 */
  at?: SetWhereAt
}

export interface SetOrderBy {
  property: string
  dir: 'desc' | 'asc'
  /** Phase 1 は 400。 */
  at?: SetWhereAt
}

/** 絞り込みの仕様（契約メモ §5.5）。IRI を持たない。`set_id` はサーバの
 *  `set_id_of(spec)` が決める決定論ハッシュ — ui では計算しない。 */
export interface SetSpec {
  class: string
  where: SetWhereClause[]
  order_by: SetOrderBy | null
  limit: number
  source_scope: 'all' | 'own' | 'open'
}

/** 1 件／絞り込みの主語の鍵（契約メモ §1）。保存・ルーティングで使う完全形
 *  （set は set_id も持つ）。 */
export type SubjectKey =
  | { kind: 'individual'; iri: string }
  | { kind: 'set'; set_id: string; spec: SetSpec }

/** `POST /api/cards/run` の `subject`（契約メモ §3.4）。set は `spec` だけで足りる
 *  （ツール実行に set_id は要らない）が、{@link SubjectKey}（set_id 必須）をそのまま
 *  渡す呼び出しも多いため `set_id` は任意にしてある — 呼び出し側は set_id 有り／
 *  無しのどちらのオブジェクトリテラルも渡せる。 */
export type CardRunSubject =
  | { kind: 'individual'; iri: string }
  | { kind: 'set'; set_id?: string; spec: SetSpec }

/** 契約メモ §1 の subject_key 文字列表現: `i:<iri>` | `s:<set_id>`。
 *  URL では `encodeURIComponent` して使う（呼び出し側の責務）。 */
export function subjectKeyToString(key: SubjectKey): string {
  return key.kind === 'individual' ? `i:${key.iri}` : `s:${key.set_id}`
}

// ---------------------------------------------------------------------------
// §3.4 カード実行・主語の解決／検索／既定カード
// ---------------------------------------------------------------------------

/** §3.3 材料（PR D まで license/own/shareable は空のまま・形だけ）。 */
export interface CardMaterial {
  dataset_id: string
  snapshot: string | null
  kind: string
  license: string | null
  count: number
}

/** §3 共通の戻り値: `run_query_tool` と同じ形 + `output_kind`・`item`・`materials`。
 *  `graph`/`found` は `subject_flow`（output_kind: 'flow'）だけが持つ。 */
export interface CardToolResult {
  tool: string
  count: number
  items: Row[]
  truncated: boolean
  sparql: string
  output_kind: OutputKind
  item: Record<string, ItemSpec>
  materials: CardMaterial[]
  /** PR D まで常に null（配れる判定は Step 6）。 */
  shareable: boolean | null
  /** `subject_flow` だけ: `prov_graph.graph` をそのまま。 */
  graph?: { nodes: unknown[]; edges: unknown[] }
  /** `subject_flow` だけ: 辺が 0 なら false（UI はカードを出さない）。 */
  found?: boolean
}

export async function runCard(
  subject: CardRunSubject,
  tool: string,
  params?: Record<string, unknown>,
): Promise<CardToolResult> {
  const res = await fetch('/api/cards/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ subject, tool, params: params ?? {} }),
  })
  if (!res.ok) await throwApiError(res, 'cards run')
  return (await res.json()) as CardToolResult
}

export interface SubjectResolveResult {
  iri: string
  found: boolean
  label: string | null
  class_iri: string | null
  class_label: string | null
  dataset_id: string | null
  /** registry の表示名（meta.name）。人に見せるのはこちら（K4: id は見せない）。 */
  dataset_label?: string | null
  snapshot: string | null
}

export async function resolveSubject(iri: string): Promise<SubjectResolveResult> {
  const res = await fetch(`/api/subjects/resolve?iri=${encodeURIComponent(iri)}`)
  if (!res.ok) await throwApiError(res, 'subject resolve')
  return (await res.json()) as SubjectResolveResult
}

export interface SubjectSearchItem {
  iri: string
  label: string
  class_iri: string | null
  class_label: string | null
  dataset_id: string | null
}

export async function searchSubjects(q: string, limit = 20): Promise<SubjectSearchItem[]> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  const res = await fetch(`/api/subjects/search?${params.toString()}`)
  if (!res.ok) await throwApiError(res, 'subject search')
  const data = (await res.json()) as { items?: SubjectSearchItem[] }
  return data.items ?? []
}

/** 既定カード 1 件の並び項目（契約メモ §3.4）。 */
export interface CardRef {
  card_id: string
  title: string
  tool: string
  params: Record<string, unknown>
  output_kind: OutputKind
}

/** `GET /api/subjects/default-cards?iri=…` → 既定カードの並び（裸の配列）。 */
export async function defaultCardsForSubject(iri: string): Promise<CardRef[]> {
  const res = await fetch(`/api/subjects/default-cards?iri=${encodeURIComponent(iri)}`)
  if (!res.ok) await throwApiError(res, 'subject default cards')
  return (await res.json()) as CardRef[]
}

/** `GET /api/sets/default-cards?spec=<JSON>` → 絞り込みの既定カード（裸の配列）。 */
export async function defaultCardsForSet(spec: SetSpec): Promise<CardRef[]> {
  const url = `/api/sets/default-cards?spec=${encodeURIComponent(JSON.stringify(spec))}`
  const res = await fetch(url)
  if (!res.ok) await throwApiError(res, 'set default cards')
  return (await res.json()) as CardRef[]
}

export interface SetResolveClause {
  property_label: string
  op: string
  value: SetWhereValue
  unit?: string | null
}

export interface SetResolveResult {
  set_id: string
  /** 正規化済みの spec（サーバが where の順序等を揃えたもの）。 */
  spec: SetSpec
  /** i18n は ui 側でやる — api は構造だけ返す。 */
  title: { class_label: string; clauses: SetResolveClause[] }
}

export async function resolveSet(spec: SetSpec): Promise<SetResolveResult> {
  const res = await fetch('/api/sets/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ spec }),
  })
  if (!res.ok) await throwApiError(res, 'set resolve')
  return (await res.json()) as SetResolveResult
}

// ---------------------------------------------------------------------------
// §2 1 件の種類のスキーマ（本来 C1-schema の担当だが、SetForm.tsx が
// `classSchema` をここから import する前提で書かれているため、c2-shell が
// 薄いラッパだけをここに足す — c1-schema の実装と route 名が食い違えば統合段で
// 直す）。
// ---------------------------------------------------------------------------

export type SchemaPropertyKind = 'quantity' | 'category' | 'text' | 'link' | 'identifier'

export interface SchemaProperty {
  iri: string
  label: string
  kind: SchemaPropertyKind
  datatype?: string | null
  unit?: string | null
  quantity_kind?: string | null
  column?: string | null
  distinct_count?: number | null
}

export interface ClassSchema {
  class_iri: string
  label: string
  dataset_id: string | null
  snapshot: string | null
  properties: SchemaProperty[]
  tools: unknown[]
}

/** `GET /api/classes/schema?class_iri=…`。無ければ 404 → null（例外にしない —
 *  「まだ形が無い」は正常系）。 */
export async function classSchema(classIri: string): Promise<ClassSchema | null> {
  const res = await fetch(`/api/classes/schema?class_iri=${encodeURIComponent(classIri)}`)
  if (res.status === 404) return null
  if (!res.ok) await throwApiError(res, 'class schema')
  return (await res.json()) as ClassSchema
}

// ---------------------------------------------------------------------------
// §4.1 形の一致（ShapeMatch）／§4.3「データを置く」
// ---------------------------------------------------------------------------

/** 契約メモ §4.1・引き継ぎ書 §5.6。 */
export interface ShapeMatch {
  type_id: string | null
  dialect: string
  matched_columns: string[]
  unmatched_columns: string[]
  confidence: number
}

export interface PlaceInspectFile {
  name: string
  columns: string[]
  rows: number
}

export interface PlaceInspectResult {
  files: PlaceInspectFile[]
  match: ShapeMatch
  signature_label: string | null
  signature_dataset_id: string | null
}

/** `{staging_id}` か `{dataset_id}` のどちらか一方を渡す。 */
export type PlaceSource = { staging_id: string } | { dataset_id: string }

/** アップロードをサーバへ置く（既存 `POST /api/staging` — `ui/src/api.ts` の
 *  `stageSources` と同じ経路。PlaceView.tsx が期待する最小の戻り値だけに絞った
 *  薄いラッパ）。 */
export async function createStaging(
  files: File[],
): Promise<{ stagingId: string; sourceNames: string[] }> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  const res = await fetch('/api/staging', { method: 'POST', headers: authHeaders(), body: form })
  if (!res.ok) await throwApiError(res, 'staging')
  const body = (await res.json()) as { staging_id: string; sources?: string[] }
  return { stagingId: body.staging_id, sourceNames: body.sources ?? [] }
}

export async function inspectPlace(body: PlaceSource): Promise<PlaceInspectResult> {
  const res = await fetch('/api/place/inspect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwApiError(res, 'place inspect')
  return (await res.json()) as PlaceInspectResult
}

/** §4.2 1 件の照合（`SubjectItem` の 3 状態）。 */
export type SubjectRowMatch = 'linked' | 'ambiguous' | 'own_only'

export interface SubjectRowCandidate {
  iri: string
  label: string
  count: number
}

export interface PlaceSubjectRow {
  value: string
  rows: number
  match: SubjectRowMatch
  iri?: string
  candidates?: SubjectRowCandidate[]
}

export async function placeSubjects(
  body: PlaceSource & { type_id: string },
): Promise<{ items: PlaceSubjectRow[] }> {
  const res = await fetch('/api/place/subjects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwApiError(res, 'place subjects')
  const data = (await res.json()) as { items?: PlaceSubjectRow[] }
  return { items: data.items ?? [] }
}

/** 私の一覧の項目（契約メモ §5・引き継ぎ書 §5.4 + subject_key・spec?・created_at）。 */
export interface SubjectItem {
  kind: 'individual' | 'set'
  /** individual なら IRI、set なら set_id（意味のある id。引き継ぎ書 §5.4）。 */
  id: string
  label: string | null
  class_label: string | null
  source: 'own' | 'open'
  /** 既定カードの件数。未取得は null（rail は数字を出さない）。 */
  card_count: number | null
  /** individual のときだけ意味を持つ（place 由来の 3 状態）。 */
  match: SubjectRowMatch | null
  /** 契約メモ §1 の文字列表現: `i:<iri>` | `s:<set_id>`。 */
  subject_key: string
  /** kind === 'set' のときの絞り込み仕様。 */
  spec?: SetSpec
  created_at: string
  /** appdata 保存だけで使う uuid4（§5: 「1 ファイル = SubjectItem 1 つ、
   *  thread_id = uuid4」）。`id`（IRI/set_id）は appdata のファイル名として使えない
   *  ため別に持つ — localStorage 運用の間は未設定でよい（subjectStore.ts が
   *  appdata へ upgrade する瞬間に採番する）。C1 の実装がこの想定と違えば統合段で
   *  合わせる（notes 参照）。 */
  thread_id?: string
}

export interface PlaceCommitResult {
  dataset_id: string
  job_id: string
  subjects: SubjectItem[]
  set: { set_id: string; spec: SetSpec }
}

export async function commitPlace(body: {
  staging_id: string
  type_id: string
  choices: Record<string, string | null>
  name: string
}): Promise<PlaceCommitResult> {
  const res = await fetch('/api/place/commit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwApiError(res, 'place commit')
  return (await res.json()) as PlaceCommitResult
}

// ---------------------------------------------------------------------------
// §5 主語の一覧の保存（appdata の `subjects` namespace）
//
// 単一ユーザーでないサーバでは 404 のまま（§5）— 呼び出し側（subjectStore.ts）が
// localStorage にフォールバックする。ここでは 404 も throwApiError で投げるだけ。
// ---------------------------------------------------------------------------

export async function fetchAppDataSubjects(): Promise<SubjectItem[]> {
  const res = await fetch('/api/appdata/subjects', { headers: authHeaders() })
  if (!res.ok) await throwApiError(res, 'appdata subjects')
  const data = (await res.json()) as { subjects?: SubjectItem[] }
  return data.subjects ?? []
}

export async function putAppDataSubject(id: string, item: SubjectItem): Promise<void> {
  const res = await fetch(`/api/appdata/subjects/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(item),
  })
  if (!res.ok) await throwApiError(res, 'appdata subject put')
}

export async function deleteAppDataSubject(id: string): Promise<void> {
  const res = await fetch(`/api/appdata/subjects/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok && res.status !== 404) await throwApiError(res, 'appdata subject delete')
}
