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
import { llmHeaders, type LlmCredentials } from '../settings/store'
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

/** §3.3 材料（PR D の D1-materials でフル形になった — `kind` は
 *  'own' | 'open' | 'unknown'）。 */
export interface CardMaterial {
  kind: string
  dataset_id: string
  dataset_label: string
  snapshot: string | null
  license: string | null
  redistributable: boolean | null
  count: number
}

/** §3 共通の戻り値: `run_query_tool` と同じ形 + `output_kind`・`item`・`materials`。
 *  `graph`/`found` は `subject_flow`（output_kind: 'flow'）だけが持つ。
 *
 *  PR D（D1-materials）で `materials` がフル形（`kind`/`dataset_label`/
 *  `redistributable` を持つ）になり、`shareable_reasons` が新設された —
 *  ただし `tool: 'subject_flow'` のレスポンスだけは従来どおり
 *  `{dataset_id, snapshot, graph}` の別形・`shareable` は null 固定・
 *  `shareable_reasons` キー自体が無い（D1-materials の報告どおり）ので任意。 */
export interface CardToolResult {
  tool: string
  count: number
  items: Row[]
  truncated: boolean
  sparql: string
  output_kind: OutputKind
  item: Record<string, ItemSpec>
  materials: CardMaterial[]
  /** materials が空、または subject_flow のときは null。 */
  shareable: boolean | null
  /** `shareable` が false の理由（固定語彙・固定順 — 契約メモ §2）。
   *  `subject_flow` のレスポンスには無い。 */
  shareable_reasons?: string[]
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

/** PR F16 §1.4:「同じものとして束ねたもの」ハブの 1 メンバー（`resolveSubject`
 *  の `hub.members` 1 件）。 */
export interface SubjectHubMember {
  iri: string
  label: string
  dataset_id: string | null
  dataset_label: string | null
  class_label: string | null
}

/** PR F16 §1.4: 主語がハブ自身のときの詳細（`resolveSubject` の `hub`）。 */
export interface SubjectHubInfo {
  perspective_id: string
  name: string
  members: SubjectHubMember[]
}

/** PR F16 §1.4: 主語がハブのメンバー（または親がメンバー）のときの帯用情報
 *  （`resolveSubject` の `hub_of`）。 */
export interface SubjectHubOf {
  iri: string
  label: string
  perspective_name: string
  member_count: number
  dataset_labels: string[]
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
  /** PR F16 §1.4: 主語がハブ自身なら true。api がまだ返さない間は任意として
   *  読む（無ければ false 相当）。 */
  is_hub?: boolean
  /** PR F16 §1.4: `is_hub` のときだけ。 */
  hub?: SubjectHubInfo | null
  /** PR F16 §1.4: 主語がハブのメンバー（または親がメンバー）のときだけ。 */
  hub_of?: SubjectHubOf | null
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

/** `dataset_id` を渡すとそのデータセットの version graph に限定する（契約メモ
 *  §3.2・データセットのページの「1 件を開く」）。`class_iri` を渡すとその種類に
 *  限定する（契約メモ contract_pr_f9.md §2.2・追加画面）。`q` は空でもよい
 *  （その種類の名前順の先頭 `limit` 件を返す一覧表示用）。無指定は今までどおり
 *  全体から。 */
export async function searchSubjects(
  q: string,
  limit = 20,
  datasetId?: string,
  classIri?: string,
): Promise<SubjectSearchItem[]> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (datasetId) params.set('dataset_id', datasetId)
  if (classIri) params.set('class_iri', classIri)
  const res = await fetch(`/api/subjects/search?${params.toString()}`)
  if (!res.ok) await throwApiError(res, 'subject search')
  const data = (await res.json()) as { items?: SubjectSearchItem[] }
  return data.items ?? []
}

/** `searchSubjects` の続き取り（offset）つき版（契約メモ contract_pr_f10.md
 *  §1.1・§2）。追加画面の「もっと見る」専用 — 既存の呼び出し元
 *  （`DatasetPage.tsx`）は `items` だけを使うので `searchSubjects` の形は
 *  変えず、こちらを新設した。`total`（`limit`/`offset` に関係ない件数）と
 *  `totalIsLowerBound`（サーバ側の走査上限に当たって `total` が下限になって
 *  いるときだけ true）を返す。 */
export interface SubjectSearchPage {
  items: SubjectSearchItem[]
  total: number
  offset: number
  limit: number
  totalIsLowerBound: boolean
}

export async function searchSubjectsPage(
  q: string,
  limit: number,
  offset: number,
  classIri?: string,
  datasetId?: string,
): Promise<SubjectSearchPage> {
  const params = new URLSearchParams({ q, limit: String(limit), offset: String(offset) })
  if (datasetId) params.set('dataset_id', datasetId)
  if (classIri) params.set('class_iri', classIri)
  const res = await fetch(`/api/subjects/search?${params.toString()}`)
  if (!res.ok) await throwApiError(res, 'subject search')
  const data = (await res.json()) as {
    items?: SubjectSearchItem[]
    total?: number
    offset?: number
    limit?: number
    total_is_lower_bound?: boolean
  }
  return {
    items: data.items ?? [],
    total: data.total ?? 0,
    offset: data.offset ?? offset,
    limit: data.limit ?? limit,
    totalIsLowerBound: data.total_is_lower_bound ?? false,
  }
}

// ---------------------------------------------------------------------------
// PR F9 §2.1: 種類の一覧（追加画面の左・契約メモ contract_pr_f9.md §3）
// ---------------------------------------------------------------------------

/** `GET /api/classes` の 1 種類分（契約メモ contract_pr_f9.md §2.1）。 */
export interface ClassEntry {
  class_iri: string
  label: string
  count: number
  dataset_id: string
  dataset_label: string
  is_demo: boolean
  properties: number
  with_label: number
  with_unit: number
  /** PR F16 §1.2: 共有ハブ（同じものの 1 つのページ）としての種類なら true。 */
  is_hub?: boolean
  /** PR F16 §1.2: `is_hub` のときの perspective id（レールの印の判定にだけ使う。
   *  人向け文言には出さない — K4）。 */
  hub_perspective_id?: string
}

/** 件数の多い順→名前順（api 側で確定した並びをそのまま返す）。 */
export async function listClasses(): Promise<ClassEntry[]> {
  const res = await fetch('/api/classes')
  if (!res.ok) await throwApiError(res, 'classes list')
  const body = (await res.json()) as { classes?: ClassEntry[] }
  return body.classes ?? []
}

/** 既定カード 1 件の並び項目（契約メモ §3.4）。 */
export interface CardRef {
  card_id: string
  title: string
  tool: string
  params: Record<string, unknown>
  output_kind: OutputKind
  /** PR F13 §1: AI が書いた見せ方があるときだけ（`CardTile.tsx`/`CardDetail.tsx`
   *  の `renderableCustomView` が読む形と揃えてある）。 */
  view?: CardView
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
  /** データセットのページのパンくず用（契約メモ §3.3・データセットの表示名）。
   *  api がまだ返さない間は `undefined` のまま。 */
  dataset_label?: string | null
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
  /** 契約メモ §3.3。データセットの表示名（subjectStore.ts の埋め戻し・
   *  データセットのページのパンくずで使う）。api がまだ返さない間は
   *  `undefined` のまま。 */
  dataset_label?: string | null
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
  /** 種類（class）の IRI（契約メモ contract_pr_f9.md §1-2・左レールの木は
   *  データセットでなくこれでグループ化する）。individual は `resolveSubject`
   *  の `class_iri`、set は `spec.class` から。既存の保存済み項目には無いことが
   *  あり、subjectStore.ts が読み込み時に 1 回だけ埋め戻す。無ければレールの
   *  「その他」節。 */
  class_iri?: string
  /** kind === 'set' のときの絞り込み仕様。 */
  spec?: SetSpec
  /** 親のデータセット（契約メモ §2.1・左レールの木の枝分け）。既存の保存済み
   *  項目には無いことがある — subjectStore.ts が読み込み時に 1 回だけ埋め戻す
   *  （`resolveSubject`/`classSchema` から）。無ければレールの「その他」節。 */
  dataset_id?: string
  /** データセットの表示名（人向け・K4: dataset_id そのものは出さない）。 */
  dataset_label?: string
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

// ---------------------------------------------------------------------------
// PR D §1: ライセンスを書く（`ingest/src/asterism/licenses.KNOWN_LICENSES` と
// 同じ SPDX 識別子一覧・`PUT /api/datasets/{id}/license`）
// ---------------------------------------------------------------------------

/** `asterism.licenses.KNOWN_LICENSES`（ingest/src/asterism/licenses.py）の
 *  キーをそのまま写した一覧。「ライセンスを書く」の入力の datalist 候補に
 *  使うだけ — 再配布可否の判定そのものはサーバ（`licenses.redistributable`）
 *  がする。並び順もサーバの辞書定義順（再配布可 12 件・不可 6 件）のまま。 */
export const KNOWN_LICENSE_IDS: readonly string[] = [
  'CC0-1.0',
  'CC-BY-4.0',
  'CC-BY-3.0',
  'CC-BY-SA-4.0',
  'CC-BY-SA-3.0',
  'ODbL-1.0',
  'ODC-By-1.0',
  'PDDL-1.0',
  'MIT',
  'Apache-2.0',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'CC-BY-NC-4.0',
  'CC-BY-ND-4.0',
  'CC-BY-NC-SA-4.0',
  'CC-BY-NC-ND-4.0',
  'proprietary',
  'all-rights-reserved',
]

export interface DatasetLicenseResult {
  dataset_id: string
  license: string | null
  redistributable: boolean | null
}

/** `PUT /api/datasets/{dataset_id}/license`（契約メモ §1・書き込みトークン
 *  必須）。`license: null` はライセンスの消去。 */
export async function putDatasetLicense(
  datasetId: string,
  license: string | null,
): Promise<DatasetLicenseResult> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/license`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ license }),
  })
  if (!res.ok) await throwApiError(res, 'dataset license')
  return (await res.json()) as DatasetLicenseResult
}

// ---------------------------------------------------------------------------
// PR F2 §2.1・§2.2・§3.1・§3.4: 左レールの木・データセットのページ
// ---------------------------------------------------------------------------

/** `GET /api/datasets` の一覧（契約メモ §3.4）。カタログの重い形
 *  （`galleryApi.ts` の `CatalogDataset` — mermaid・alignment 込み）とは別の、
 *  レールの木を組むためだけの薄い形。`origin`/`is_demo`/`stage` は api がまだ
 *  返さない間はここで安全側に倒す（`unknown`/`false`/`ingested|promoted` から
 *  推定）。 */
export interface CardsDatasetSummary {
  id: string
  name: string
  origin: 'own' | 'open' | 'unknown'
  is_demo: boolean
  stage: 'design' | 'ingested' | 'promoted'
}

function toDatasetOrigin(value: unknown): 'own' | 'open' | 'unknown' {
  return value === 'own' || value === 'open' ? value : 'unknown'
}

function toDatasetStage(raw: Record<string, unknown>): 'design' | 'ingested' | 'promoted' {
  const stage = raw.stage
  if (stage === 'design' || stage === 'ingested' || stage === 'promoted') return stage
  // api がまだ `stage` を返さない間の後方互換（`ingested`/`promoted` 既存フラグから）。
  if (raw.promoted === true) return 'promoted'
  if (raw.ingested === true) return 'ingested'
  return 'design'
}

export async function listDatasets(): Promise<CardsDatasetSummary[]> {
  const res = await fetch('/api/datasets')
  if (!res.ok) await throwApiError(res, 'datasets list')
  const body = (await res.json()) as { datasets?: Record<string, unknown>[] }
  const list = Array.isArray(body.datasets) ? body.datasets : []
  return list.map((raw) => ({
    id: String(raw.id ?? ''),
    name: typeof raw.name === 'string' && raw.name ? raw.name : String(raw.id ?? ''),
    origin: toDatasetOrigin(raw.origin),
    is_demo: raw.is_demo === true,
    stage: toDatasetStage(raw),
  }))
}

/** `GET /api/datasets/{id}/summary`（契約メモ §3.1）の 1 種類分の内訳。 */
export interface DatasetSummaryClass {
  class_iri: string
  label: string
  count: number
  properties: number
  with_label: number
  with_unit: number
}

/** データセットのページ（`DatasetPage.tsx`・ui-page 担当）が読む形。 */
export interface DatasetSummary {
  dataset_id: string
  label: string
  origin: 'own' | 'open' | 'unknown'
  stage: string
  license: string | null
  snapshot: string | null
  source_note: string | null
  classes: DatasetSummaryClass[]
  is_demo: boolean
}

export async function datasetSummary(datasetId: string): Promise<DatasetSummary> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/summary`, {
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'dataset summary')
  return (await res.json()) as DatasetSummary
}

// ---------------------------------------------------------------------------
// PR D §3: エージェント束の書き出し（`POST /api/subjects/export`）
// ---------------------------------------------------------------------------

export type ExportShareMode = 'full' | 'shareable'
export type ExportLang = 'ja' | 'en'

/** body の `cards[]`（契約メモ §3）。`CardRef` は `title`/`output_kind` も
 *  持つが、api が読むのはこの 3 つだけ — 呼び出し側は `CardRef` をそのまま
 *  渡してよい（余分なフィールドは JSON.stringify で自然に落ちない為、
 *  呼び出し側で詰め替える）。 */
export interface ExportCardRef {
  card_id: string
  tool: string
  params: Record<string, unknown>
}

/** `share: 'shareable'` で配れるカードが 1 枚も無いときの 409（契約メモ §3）。
 *  `reasons` は `materials.shareable_reasons` と同じ固定語彙。 */
export class NotShareableExportError extends Error {
  readonly reasons: string[]
  constructor(reasons: string[]) {
    super('subjects export: not shareable')
    this.name = 'NotShareableExportError'
    this.reasons = reasons
  }
}

export interface ExportedAgentBundle {
  blob: Blob
  filename: string
}

/** `Content-Disposition: attachment; filename="<slug>-agent.zip"` からファイル
 *  名を取り出す（引用符の有無どちらでも）。取れなければ既定名に倒す。 */
function filenameFromContentDisposition(header: string | null): string {
  if (!header) return 'agent.zip'
  const match = /filename\*?=(?:UTF-8''|")?([^";\n]+)"?/i.exec(header)
  return match ? decodeURIComponent(match[1]) : 'agent.zip'
}

/** `POST /api/subjects/export` → zip の Blob（契約メモ §3）。呼び出し側が
 *  `<a download>` 相当で保存する（ここでは保存しない）。 */
export async function exportSubjectAgent(
  subject: CardRunSubject,
  cards: ExportCardRef[],
  share: ExportShareMode,
  lang: ExportLang,
): Promise<ExportedAgentBundle> {
  const res = await fetch('/api/subjects/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ subject, cards, share, lang }),
  })
  if (!res.ok) {
    if (res.status === 409) {
      const body = await res.text().catch(() => '')
      let reasons: string[] = []
      try {
        const parsed = JSON.parse(body) as { detail?: { reasons?: unknown } }
        if (Array.isArray(parsed.detail?.reasons)) {
          reasons = parsed.detail.reasons.filter((r): r is string => typeof r === 'string')
        }
      } catch {
        // 本文が JSON でなければ理由なしで扱う — ダイアログは汎用文言に倒れる。
      }
      throw new NotShareableExportError(reasons)
    }
    await throwApiError(res, 'subjects export')
  }
  const filename = filenameFromContentDisposition(res.headers.get('content-disposition'))
  const blob = await res.blob()
  return { blob, filename }
}

// ---------------------------------------------------------------------------
// PR F4 §1-2〜§1-5: グラフを足す（set_measure の型・「この 1 件」条件・保存）
// ---------------------------------------------------------------------------

/** ①見せ方＝出口の型（契約メモ §1-2 の表・6 種）。`OutputKind` から来歴専用の
 *  `flow` を除いたもの — `set_measure` は来歴を返さない。`params.shape` と
 *  戻り値の `output_kind` は同じ語彙。 */
export type MeasureShape = Exclude<OutputKind, 'flow'>

/** ②「数字 1 つ」だけが持つ集計（class_schema からの候補ではない固定 5 択・
 *  契約メモ §3 の newcard.agg_*）。 */
export type MeasureAgg = 'avg' | 'max' | 'min' | 'sum' | 'count'

/** ③条件の「この 1 件」形（契約メモ §1-3）:「?p = この 1 件」を where に足す。
 *  既存の値条件（`op`/`value` を持つ {@link SetWhereClause}）とは別の形なので、
 *  そちらを書き換えず独立の型として持つ（ingest 側 `normalize_set_spec` への
 *  この形の追加は tool 担当の範囲・`ingest/src/asterism/subjects.py`）。 */
export interface MeasureLinkClause {
  /** この 1 件を目的語に持つ関係の述語（`linkingKinds` の候補から選ぶ）。 */
  property: string
  /** 指す先 = いまの 1 件の IRI。 */
  iri: string
}

/** PR F14 §1.2: 2 段の where（`child_child`/`sibling_child` 形）。`via` は
 *  1 段だけ（サーバ側 `_normalize_clause` と同じ制約）。 */
export interface MeasureLinkViaClause {
  property: string
  via: { property: string; iri: string }
}

/** `set_measure` の `where` は既存の値条件（{@link SetWhereClause} と同じ形）と
 *  {@link MeasureLinkClause}（「この 1 件」）・{@link MeasureLinkViaClause}
 *  （2 段の「この 1 件」）のどれかを受ける。 */
export type MeasureWhereClause = SetWhereClause | MeasureLinkClause | MeasureLinkViaClause

/** `set_measure` の params（契約メモ §1-4・`ingest/src/asterism/measure_spec.py`
 *  の `validate_measure` と揃えてある）。②の「項目」（比べる=ranked・数字 1 つ
 *  =quantity）は `item` キー（`measure_spec.py` 側は `x` もフォールバックとして
 *  受けるが、こちらが正）。 */
export interface MeasureCardParams {
  class: string
  where: MeasureWhereClause[]
  shape: MeasureShape
  x?: string
  y?: string
  item?: string
  category?: string
  items?: string[]
  agg?: MeasureAgg
  order?: 'asc' | 'desc'
  // `CardRef.params`/`runCard` の params は汎用の `Record<string, unknown>`
  // （組み込み/宣言のあらゆるツールを受ける契約）。`CardSpec.params` を
  // そのまま `CardRef.params` に代入できるよう（`cardSpecToCardRef` —
  // ui-page 側）、既知のフィールドに加えてインデックスシグネチャも持たせる。
  [key: string]: unknown
}

/** 保存された 1 枚の「足したカード」（契約メモ §1-5・ADR O19 CardSpec）。
 *  `card_id` は params の決定論ハッシュ（`measureCardFields.ts` の `cardId`）
 *  — 同じ params からは常に同じ `card_id`。 */
export interface CardSpec {
  card_id: string
  subject_key: string
  // PR F13 穴埋め: 既定カード（`defaultCardsForSubject`/`defaultCardsForSet`）を
  // 元にした view 提案の「足す」も同じ `CardSpec` として保存する必要があり、
  // 既定カードの `tool` は `set_measure` に限らない（宣言ツール名・
  // `subject_facts` 等の組み込みもありうる — `CardRef.tool` と同じ `string`）。
  tool: string
  params: MeasureCardParams
  title: string
  output_kind: MeasureShape
  created_at: string
  /** PR F13 §1 決定 3・4: AI が Vega-Lite／表仕様／Mermaid で「書いた」見せ方が
   *  乗っているときだけ（`presentation` — F3・この作業ツリーにはまだ無い — と
   *  同居する想定の場所）。既定ビューのカードには無い。 */
  view?: CardView
}

// ---------------------------------------------------------------------------
// PR F13 §1: AI が書いた見せ方（Vega-Lite／表仕様／Mermaid）
//
// ワイヤ形（サーバの `<proposal>` の `view` — `converse_prompt.py` の
// `_validate_view_proposal` が実際に返す形）と保存形（`CardSpec.view`）は
// 別の型: ワイヤ形は `custom` を持たない（「AI が書いた」の印は保存する瞬間に
// client が必ず立てるものなので、サーバの応答自体には無い）。mermaid は
// `spec`（object）ではなく `text`（ソーステキストの string）— サーバ実装
// （`converse_prompt.py`）と揃えた。
// ---------------------------------------------------------------------------

export type CardViewLang = 'vega-lite' | 'table' | 'mermaid'

/** サーバの `<proposal>` の `view`（契約メモ §1-2 のワイヤ形）。データは
 *  含まない — vega-lite の `spec.data.values` は描画の直前に rows を差し込む
 *  だけで、AI の JSON 自体には書かせない（サーバの許可リスト検証＝
 *  `view_spec_check.py`・api 担当が別途検証する）。 */
export interface ConverseProposalView {
  lang: CardViewLang
  /** vega-lite・table のとき。 */
  spec?: Record<string, unknown>
  /** mermaid のとき（flowchart のソーステキスト）。 */
  text?: string
  /** このページに並んでいる、元にしたカードの `card_id`。 */
  source_card_id: string
}

/** `CardSpec.view`（保存形。契約メモ §1 決定 3・4）。`custom: true` は
 *  固定 — 「足す」で保存する瞬間に必ず立てる（サーバの応答自体には無い —
 *  {@link ConverseProposalView} 参照）。この形の view は必ず「AI が書いた」
 *  印を持つ（既定ビューには `view` 自体が無い）。 */
export interface CardView extends ConverseProposalView {
  custom: true
}

const CARD_VIEW_LANGS = new Set<string>(['vega-lite', 'table', 'mermaid'])

/** `raw` が {@link ConverseProposalView} の形をしているかを見る共通の下請け
 *  （`custom` の有無は見ない — 呼び出し側がワイヤ形／保存形のどちらを期待
 *  するかで分ける）。`spec`/`text` の中身（許可リストの検証）はここでは
 *  見ない — それはサーバ側 `view_spec_check.py` の仕事で、ここは伝送・保存の
 *  形だけを見る。 */
function parseProposalViewShape(raw: unknown): ConverseProposalView | undefined {
  if (raw === null || raw === undefined || typeof raw !== 'object') return undefined
  const r = raw as Record<string, unknown>
  if (typeof r.lang !== 'string' || !CARD_VIEW_LANGS.has(r.lang)) return undefined
  if (typeof r.source_card_id !== 'string' || !r.source_card_id) return undefined
  const lang = r.lang as CardViewLang
  if (lang === 'mermaid') {
    if (typeof r.text !== 'string') return undefined
    return { lang, text: r.text, source_card_id: r.source_card_id }
  }
  if (r.spec === null || typeof r.spec !== 'object') return undefined
  return { lang, spec: r.spec as Record<string, unknown>, source_card_id: r.source_card_id }
}

/** サーバの `<proposal>` から来た `view` を検証する（`custom` は見ない・
 *  持っていても無視する）。形が違えば `undefined`（提案全体を落とす —
 *  呼び出し側 `normalizeConverseProposal` 参照）。 */
export function normalizeConverseProposalView(raw: unknown): ConverseProposalView | undefined {
  return parseProposalViewShape(raw)
}

/** 保存／appdata から来た値が {@link CardView}（`custom: true` 込みの保存形）
 *  の形をしているかを検証する。形が違えば `undefined`（呼び出し側は view
 *  なしとして扱う＝既定ビューへ安全側に倒す — 契約メモ §1 実装 (3)「違えば
 *  捨てる」）。 */
export function normalizeCardView(raw: unknown): CardView | undefined {
  if (raw === null || typeof raw !== 'object' || (raw as Record<string, unknown>).custom !== true) return undefined
  const base = parseProposalViewShape(raw)
  return base ? { ...base, custom: true } : undefined
}

/** PR F14 §1.1: `child_child`/`sibling_child` の 2 段目（via）。 */
export interface LinkingKindVia {
  property: string
  property_label: string
  class_label: string
}

/** `GET /api/subjects/linking-kinds?iri=…` の 1 候補（契約メモ §1-3・PR F14
 *  §1.1・`ingest/src/asterism/subject_tools.py` の `linking_kinds` と揃えて
 *  ある）。この IRI から届く範囲（近傍）の種類と述語（来歴のクラスは api 側で
 *  除外済み）。従来の `direct`（この 1 件を直接指す）に加え、`child_child`・
 *  `sibling`・`sibling_child` の 3 形が増える（PR F14）。`where` 以外は
 *  api がまだ返さない実装途中でも安全に読めるよう任意にしてある。 */
export interface LinkingKind {
  class_iri: string
  class_label: string
  property: string
  property_label: string
  count: number
  /** 段数（1=direct、2=child_child/sibling、3=sibling_child）。 */
  hops?: 1 | 2 | 3
  path_kind?: 'direct' | 'child_child' | 'sibling' | 'sibling_child'
  /** PR F16: ハブのページの direct 行で、その種類が属するデータセットの名前。 */
  class_dataset_label?: string | null
  /** `where` が指す IRI（direct/child_child はこの 1 件、sibling 系は親）。 */
  anchor_iri?: string
  anchor_label?: string | null
  /** 親の種類の名前（sibling 系だけ。direct 系は null）。 */
  anchor_class_label?: string | null
  /** この 1 件 → 親 の述語（sibling 系だけ。direct 系は null）。 */
  anchor_property?: string | null
  anchor_property_label?: string | null
  /** 2 段目（child_child/sibling_child だけ）。 */
  via?: LinkingKindVia | null
  /** PR F16 §1.3: ハブ（同じものの 1 つのページ）から見たときだけ — どの
   *  メンバー（ハブを指す実体）を経由してこの候補に届くか。ハブでない主語では
   *  無い。 */
  via_member?: { iri: string; label: string; dataset_id: string; dataset_label: string }
  /** 完成形の条件。消費側（NewCardForm/viewpoints）はこれをそのまま
   *  `set_measure` の `where` に使う — 自前で組み立てない（PR F14 §1.3）。 */
  where: MeasureWhereClause[]
}

export async function linkingKinds(iri: string): Promise<LinkingKind[]> {
  const res = await fetch(`/api/subjects/linking-kinds?iri=${encodeURIComponent(iri)}`)
  if (!res.ok) await throwApiError(res, 'linking kinds')
  const data = (await res.json()) as { kinds?: LinkingKind[] }
  return data.kinds ?? []
}

// ---- appdata cards（namespace "cards"・subjects と同じ流儀 — 単一ユーザーで
// ないサーバでは 404 のまま。呼び出し側 cardStore.ts が localStorage に
// フォールバックする） ---------------------------------------------------------

export async function fetchAppDataCards(): Promise<CardSpec[]> {
  const res = await fetch('/api/appdata/cards', { headers: authHeaders() })
  if (!res.ok) await throwApiError(res, 'appdata cards')
  const data = (await res.json()) as { cards?: CardSpec[] }
  return data.cards ?? []
}

export async function putAppDataCard(cardId: string, item: CardSpec): Promise<void> {
  const res = await fetch(`/api/appdata/cards/${encodeURIComponent(cardId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(item),
  })
  if (!res.ok) await throwApiError(res, 'appdata card put')
}

export async function deleteAppDataCard(cardId: string): Promise<void> {
  const res = await fetch(`/api/appdata/cards/${encodeURIComponent(cardId)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok && res.status !== 404) await throwApiError(res, 'appdata card delete')
}

// ---------------------------------------------------------------------------
// PR F12 §1-3: ページの中で AI と会話しながら観点を作る（POST /api/cards/converse）
// ---------------------------------------------------------------------------

export interface ConverseMessage {
  role: 'user' | 'assistant'
  content: string
}

/** `converse` の `subject`（契約メモ §1-3）。`CardRunSubject` の 2 種に加え、
 *  種類のページ（`k:<class_iri>`）ぶんの `class` を持つ — こちらは会話と要約の
 *  ためだけの形で、`runCard`/`NewCardForm` にはそのまま渡さない（`class` は
 *  `set`（`where: []`）に変換してから渡す。`PageChatDrawer.tsx` 参照）。 */
export type ConverseSubject = CardRunSubject | { kind: 'class'; class_iri: string }

/** ドロワーが持つ「直前の提案」— 次の送信の `draft` としてそのままサーバへ渡す
 *  （契約メモ §1-3「直す」）。`presentation` は任意（F3 の見せ方切替と同じ語彙、
 *  例 `{ mark: 'bar' }`）。 */
export interface ConverseDraft {
  params: Record<string, unknown>
  presentation: Record<string, unknown> | null
}

/** ページの要約の 1 件の事実（契約メモ §1-3 の `page.facts`）。 */
export interface ConversePageFact {
  label: string
  value: string
}

/** ページの要約の 1 枚のカード（契約メモ §1-3 の `page.cards`）。`rows` は
 *  呼び出し側が間引く（`pageChatThreads.ts` の `summarizeCardRows`）。
 *
 *  `card_id` は任意（PR F13・missing 参照）: `kind: 'view'` の提案が
 *  `source_card_id` でこのページの 1 枚を指すには、AI がその id を読める
 *  必要がある — ui-page 側（`SubjectPage.tsx`/`SetPage.tsx`/`ClassPage.tsx`）が
 *  `PageChatSummary`/`summarizeCardForChat` にこの列を足して初めて機能する
 *  （現時点ではまだ渡っていないので、常に `undefined` のまま送られる）。 */
export interface ConversePageCard {
  card_id?: string
  title: string
  output_kind: string
  rows: Row[]
  /** PR F13 穴埋め: `kind: 'view'` の提案が `source_card_id` で指す元のカードを
   *  再実行するために要る（既定カード＝まだ「足す」を押していないカードは
   *  `cardStore` に無いため、ここに乗せて初めて解決できる — `PageChatDrawer.tsx`
   *  の `resolveViewSourceCard` 参照）。`params` は body が大きくなりすぎない
   *  よう、JSON で 2KB を超える場合は省く（`SubjectPage.tsx`/`SetPage.tsx` の
   *  `summarizeCardForChat` が判断する）。 */
  tool?: string
  params?: Record<string, unknown>
}

export interface ConversePageSummary {
  facts: ConversePageFact[]
  cards: ConversePageCard[]
}

/** AI の提案（契約メモ §1-3・PR F13 §1）。`params` はサーバが `validate_measure`
 *  を通したもの — `set_measure` の params と同じ形（`MeasureCardParams` 互換）。
 *  `kind` 省略時は `'measure'`（F12 までの観点の指定）。`'view'` は AI が
 *  Vega-Lite／表仕様／Mermaid を「書いた」見せ方（PR F13）— このときだけ
 *  {@link ConverseProposalView} を持つ（ワイヤ形・`custom` は無い）。 */
export interface ConverseProposal {
  params: Record<string, unknown>
  presentation: Record<string, unknown> | null
  output_kind: string
  title: string
  kind?: 'measure' | 'view'
  view?: ConverseProposalView
  /** PR F14 §1.4:「提案してから答える」の印。true のときだけ、この提案を
   *  足すと会話の質問に答えられる（サーバの返信文が「足すと答えられます」と
   *  案内している）。既定 false 相当（未指定は false として扱う）。 */
  answers?: boolean
}

export interface ConverseResponse {
  reply: string
  proposal: ConverseProposal | null
}

/** サーバが LLM を解決できないとき（キー未設定・consult と同じ 502 契約 —
 *  契約メモ §1-3・§4「キーが無いとき」）。呼び出し側（`PageChatDrawer.tsx`）は
 *  これを捕まえてフォームに倒す。 */
export class NoLlmKeyError extends Error {
  constructor() {
    super('converse: no llm key configured')
    this.name = 'NoLlmKeyError'
  }
}

/** {@link ConverseProposal} の伝送形チェック（`converse()` から呼ぶ・pageChat.test.ts
 *  から直接も呼ぶので export）。**`kind: 'view'` を最初に分岐する** — サーバの
 *  `converse_prompt.py` の `_validate_view_proposal` は `kind: 'view'` のとき
 *  `{kind, view}` だけを返し `params`/`output_kind`/`title` を持たない
 *  （PR F13 §1）。これらの必須チェックを先に置くと view 提案は毎回ここで
 *  `undefined` に当たって null に潰れ、「書く」機能が UI 上で一度も発火し
 *  なくなる（checker 指摘・修正前の実装バグ）。 */
export function normalizeConverseProposal(raw: unknown): ConverseProposal | null {
  if (raw === null || raw === undefined || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const presentation =
    r.presentation !== null && r.presentation !== undefined && typeof r.presentation === 'object'
      ? (r.presentation as Record<string, unknown>)
      : null
  if (r.kind === 'view') {
    const view = normalizeConverseProposalView(r.view)
    if (!view) return null
    // `params`/`output_kind`/`title` はサーバの view 応答に無い。ViewProposalPreview
    // は `proposal.view.source_card_id` から解決した既存カードの title/params/tool
    // を使うのでこれらは参照されない（未使用の穴埋め）。
    return { params: {}, presentation, output_kind: '', title: '', kind: 'view', view, answers: r.answers === true }
  }
  if (r.params === null || typeof r.params !== 'object') return null
  if (typeof r.output_kind !== 'string' || typeof r.title !== 'string') return null
  // PR F14 §1.5: `answers: true`（答えを出すための提案）はそのまま運ぶ（bool 以外は false）。
  return {
    params: r.params as Record<string, unknown>,
    presentation,
    output_kind: r.output_kind,
    title: r.title,
    kind: 'measure',
    answers: r.answers === true,
  }
}

/** `POST /api/cards/converse`（契約メモ §1-3）。ヘッダは consult と同じ
 *  `llmHeaders`（`X-API-Key`/`X-LLM-Provider`/`X-LLM-Model`/`X-LLM-Api-Base`）。
 *  502（キー未解決）は {@link NoLlmKeyError} で区別できるようにする（consult の
 *  `consult()` はここを区別しないが、こちらはドロワーがフォームに倒れる分岐に
 *  使う）。 */
export async function converse(
  body: {
    subject: ConverseSubject
    messages: ConverseMessage[]
    draft: ConverseDraft | null
    page: ConversePageSummary
    lang: 'ja' | 'en'
  },
  creds: LlmCredentials | null,
  signal?: AbortSignal,
): Promise<ConverseResponse> {
  const res = await fetch('/api/cards/converse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...llmHeaders(creds) },
    body: JSON.stringify(body),
    signal,
  })
  if (res.status === 502) throw new NoLlmKeyError()
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`converse failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  const data = (await res.json()) as { reply?: unknown; proposal?: unknown }
  return {
    reply: typeof data.reply === 'string' ? data.reply : '',
    proposal: normalizeConverseProposal(data.proposal),
  }
}
