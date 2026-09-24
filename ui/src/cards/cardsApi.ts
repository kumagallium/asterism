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

/** `dataset_id` を渡すとそのデータセットの version graph に限定する（契約メモ
 *  §3.2・データセットのページの「1 件を開く」）。無指定は今までどおり全体から。 */
export async function searchSubjects(
  q: string,
  limit = 20,
  datasetId?: string,
): Promise<SubjectSearchItem[]> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (datasetId) params.set('dataset_id', datasetId)
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
