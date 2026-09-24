// class_schema →「グラフを足す」フォームの候補・見せ方ごとの必須項目・
// 決定論のカード組み立てを行う純関数群（契約メモ §1-1〜§1-2・分担 §2
// ui-form）。`setFormFields.ts`（絞り込みフォーム）と対の存在 — こちらは
// 「新しいカードを 1 枚組む」ための候補づくりを担当する。LLM は呼ばない。
//
// i18n はこのファイルでは行わない（react-i18next への依存を持ち込まず、
// テストを純粋にする — `setTitle.ts`/`cardTitle.ts` と同じ流儀）。呼び出し側
// の `t()` を受け取るだけ・キーは必ず `cards:newcard.*`（namespace 込みの
// フルキー）で書く — このファイルには `useTranslation` が無いので、bare な
// キーは i18n 参照チェック（`check-i18n-refs.mjs`）が既定 namespace `common`
// で解決しようとして「未定義」判定になる。

import type { CardSpec, MeasureAgg, MeasureCardParams, MeasureShape, MeasureWhereClause, SchemaProperty } from './cardsApi'

export type Translate = (key: string, options?: Record<string, unknown>) => string

// ---------------------------------------------------------------------------
// ①見せ方
// ---------------------------------------------------------------------------

/** 契約メモ §1-2 の表の掲載順（NewCardForm のタイルの並びもこの順）。 */
export const MEASURE_SHAPES: readonly MeasureShape[] = ['series', 'ranked', 'breakdown', 'pairs', 'quantity', 'facts']

export function shapes(): readonly MeasureShape[] {
  return MEASURE_SHAPES
}

/** ②「数字 1 つ」だけの集計（class_schema からの候補ではない固定 5 択）。 */
export const MEASURE_AGGS: readonly MeasureAgg[] = ['avg', 'max', 'min', 'sum', 'count']

// ---------------------------------------------------------------------------
// ②で聞く項目（契約メモ §1-2 の表そのもの）
// ---------------------------------------------------------------------------

/** ②の項目の役割。表示の言葉は §3 の `newcard.axis_x`/`axis_y`/`item`/
 *  `category`/`items`。 */
export type MeasureFieldRole = 'x' | 'y' | 'item' | 'category' | 'items'

export interface MeasureField {
  role: MeasureFieldRole
  /** facts の「項目（複数可）」だけ true。 */
  multiple: boolean
}

/** 見せ方 → ②で聞く項目（契約メモ §1-2 の表）。 */
export function fieldsFor(shape: MeasureShape): MeasureField[] {
  switch (shape) {
    case 'series':
      return [
        { role: 'x', multiple: false },
        { role: 'y', multiple: false },
      ]
    case 'pairs':
      return [
        { role: 'x', multiple: false },
        { role: 'y', multiple: false },
      ]
    case 'ranked':
    case 'quantity':
      return [{ role: 'item', multiple: false }]
    case 'breakdown':
      return [{ role: 'category', multiple: false }]
    case 'facts':
      return [{ role: 'items', multiple: true }]
  }
}

/** 「数字 1 つ」だけ、②に集計の選択も要る（固定 5 択なので fieldsFor には
 *  出さず、NewCardForm が別枠で聞く）。 */
export function needsAgg(shape: MeasureShape): boolean {
  return shape === 'quantity'
}

// ---------------------------------------------------------------------------
// 候補（class_schema → kind で絞った一覧）
// ---------------------------------------------------------------------------

/** class_schema からこの UI が読む最小の形（`setFormFields.ts` の
 *  `ClassSchemaLike` と同じ理由 — `cardsApi.ts` の fetch 実装に依存させない）。 */
export interface MeasureSchemaLike {
  class_iri: string
  label: string
  properties: SchemaProperty[]
}

export interface MeasureCandidate {
  property: string
  label: string
  unit: string | null
}

function toCandidate(p: SchemaProperty): MeasureCandidate {
  return { property: p.iri, label: p.label, unit: p.unit ?? null }
}

/** 値が 1 つしかない quantity（`distinct_count === 1`）は、推移・比べる・
 *  散らばりでは変化を描けないので候補から外す（契約メモ §1-3「1 件自身の
 *  quantity（値が 1 つ）は『数字 1 つ』『表』でのみ選べる」）。`distinct_count`
 *  が不明（null/undefined）のものは判定できないので残す（安全側）。 */
function quantityProps(schema: MeasureSchemaLike, opts: { allowSingleValue: boolean }): SchemaProperty[] {
  return schema.properties.filter((p) => {
    if (p.kind !== 'quantity') return false
    if (opts.allowSingleValue) return true
    return p.distinct_count == null || p.distinct_count > 1
  })
}

function categoryProps(schema: MeasureSchemaLike): SchemaProperty[] {
  return schema.properties.filter((p) => p.kind === 'category')
}

/** 数値 datatype の局所名（`ingest/src/asterism/class_schema.py` の
 *  `_NUMERIC_LOCAL_NAMES` と同じ集合 — import できない別言語なので同じ集合を
 *  ここに持つ）。 */
const NUMERIC_DATATYPE_LOCAL_NAMES = new Set(['double', 'float', 'decimal', 'integer', 'int', 'long'])

function isNumericDatatype(datatype: string | null | undefined): boolean {
  if (!datatype) return false
  return NUMERIC_DATATYPE_LOCAL_NAMES.has(localName(datatype).trim().toLowerCase())
}

/** この性質が横軸（x）に使える「座標」か（`measure_spec.py` の
 *  `is_coordinate` と同じ判定）: kind が quantity、または kind が identifier
 *  で datatype が数値。年・日付・連番のような座標は主語テンプレートの一部
 *  （identifier）としてモデル化されるのが一般的な形なので、横軸だけはこれも
 *  受ける — y・item・category の対象は従来どおり quantity/category のみ。 */
function isCoordinate(p: SchemaProperty): boolean {
  if (p.kind === 'quantity') return true
  if (p.kind === 'identifier') return isNumericDatatype(p.datatype)
  return false
}

/** 横軸候補（座標＝quantity または数値 identifier）。値が 1 つしかない
 *  quantity は除く（`quantityProps` と同じ理由・§1-3）。identifier は
 *  distinct が 1 でも「キーの一部の座標」なので除かない（そもそも 1 件の
 *  記録には現れない値ではなく、主語を作る列 — 値が 1 種類しかないことは
 *  通常無い）。 */
function coordinateProps(schema: MeasureSchemaLike): SchemaProperty[] {
  return schema.properties.filter((p) => {
    if (!isCoordinate(p)) return false
    if (p.kind === 'quantity') return p.distinct_count == null || p.distinct_count > 1
    return true
  })
}

/** 表（facts）の項目候補: 見せ方の妥当性表（`measure_spec.py` の
 *  `_FIELD_KINDS["facts"]["items"] = ("quantity", "category")`）と揃え、
 *  quantity/category だけを対象にする — text/identifier/link は表の外
 *  （text は自由記述で列として並べる意味が薄く、link は生の IRI のままでは
 *  人に見せられない・K4 相当）。 */
function factProps(schema: MeasureSchemaLike): SchemaProperty[] {
  return schema.properties.filter((p) => p.kind === 'quantity' || p.kind === 'category')
}

/** 横軸（推移）の並べ替え: 時間らしい quantity → 単位なし → distinct が多い、
 *  の優先順（契約メモ §1-2「候補は distinct が多く単位なし／時間の kind を
 *  優先」）。`ingest/src/asterism/measure_spec.py` の `x_candidates`/
 *  `_is_time_like` と同じ判定にする — 「時間らしい」は `label` + IRI の
 *  局所名に year/date/time/年/日 のどれかを含むかどうか（分野固有の量の
 *  名前ではなく、座標の一般名）。 */
const TIME_LIKE = /year|date|time|年|日/i

/** `iri.rsplit('#',1)[-1].rsplit('/',1)[-1] or iri`（measure_spec.py の
 *  `_local_name` と同じ規則）。 */
function localName(iri: string): string {
  const afterHash = iri.includes('#') ? iri.slice(iri.lastIndexOf('#') + 1) : iri
  const afterSlash = afterHash.includes('/') ? afterHash.slice(afterHash.lastIndexOf('/') + 1) : afterHash
  // `xsd:integer` のような CURIE 形（class_schema はこの形で datatype を返す）
  // も末尾だけにする — `measure_spec.py` の `_local_name` と同じ規則。
  const afterColon =
    !afterSlash.includes('://') && afterSlash.includes(':')
      ? afterSlash.slice(afterSlash.lastIndexOf(':') + 1)
      : afterSlash
  return afterColon || iri
}

function isTimeLike(p: SchemaProperty): boolean {
  return TIME_LIKE.test(`${p.label ?? ''} ${localName(p.iri)}`)
}

/** 横軸の並び順（強い順）: 数値 identifier（キーの一部の座標）→ 時間らしい
 *  → 単位なし → distinct が多い。`measure_spec.py` の `x_candidates` の
 *  `_sort_key` と同じ規則（identifier を quantity より先に）。 */
function xAxisRank(p: SchemaProperty): readonly [number, number, number, number] {
  return [p.kind === 'identifier' ? 1 : 0, isTimeLike(p) ? 1 : 0, p.unit ? 0 : 1, p.distinct_count ?? 0] as const
}

function sortForXAxis(props: SchemaProperty[]): SchemaProperty[] {
  return [...props].sort((a, b) => {
    const ra = xAxisRank(a)
    const rb = xAxisRank(b)
    for (let i = 0; i < ra.length; i++) {
      if (ra[i] !== rb[i]) return rb[i] - ra[i]
    }
    return a.iri.localeCompare(b.iri)
  })
}

export interface MeasureCandidateSet {
  x: MeasureCandidate[]
  y: MeasureCandidate[]
  item: MeasureCandidate[]
  category: MeasureCandidate[]
  items: MeasureCandidate[]
}

const EMPTY_CANDIDATES: MeasureCandidateSet = { x: [], y: [], item: [], category: [], items: [] }

/** class_schema → ①で選んだ見せ方に応じた候補（kind で絞る・契約メモ §1-2）。
 *  同じ入力には常に同じ結果（副作用なし）。使わない役割のキーは空配列。 */
export function candidates(schema: MeasureSchemaLike, shape: MeasureShape): MeasureCandidateSet {
  switch (shape) {
    case 'series': {
      const q = quantityProps(schema, { allowSingleValue: false })
      const x = sortForXAxis(coordinateProps(schema)).map(toCandidate)
      return { ...EMPTY_CANDIDATES, x, y: q.map(toCandidate) }
    }
    case 'pairs': {
      // 散らばりは横軸・縦軸に優先順を付けない（推移だけが時間らしさで並べ替
      // える）— 座標の集合を広げるだけで、既存の「x と y は同じ並び」という
      // 挙動は変えない。
      const x = coordinateProps(schema).map(toCandidate)
      return { ...EMPTY_CANDIDATES, x, y: quantityProps(schema, { allowSingleValue: false }).map(toCandidate) }
    }
    case 'ranked':
      return { ...EMPTY_CANDIDATES, item: quantityProps(schema, { allowSingleValue: false }).map(toCandidate) }
    case 'quantity':
      return { ...EMPTY_CANDIDATES, item: quantityProps(schema, { allowSingleValue: true }).map(toCandidate) }
    case 'breakdown':
      return { ...EMPTY_CANDIDATES, category: categoryProps(schema).map(toCandidate) }
    case 'facts':
      return { ...EMPTY_CANDIDATES, items: factProps(schema).map(toCandidate) }
  }
}

// ---------------------------------------------------------------------------
// カードの組み立て（buildMeasureCard・cardId・titleFor）
// ---------------------------------------------------------------------------

export interface MeasureCardInput {
  classIri: string
  shape: MeasureShape
  where: MeasureWhereClause[]
  /** 推移・散らばりの横軸（property IRI）。 */
  x?: string
  /** 推移・散らばりの縦軸（property IRI）。 */
  y?: string
  /** 比べる（ranked）・数字 1 つ（quantity）の「項目」（property IRI）。 */
  item?: string
  /** 内訳の分類（property IRI）。 */
  category?: string
  /** 表の項目（複数可・property IRI の配列）。 */
  items?: string[]
  /** 数字 1 つの集計。 */
  agg?: MeasureAgg
}

/** ②〜③の選択 → `set_measure` の params（`measure_spec.py` の
 *  `validate_measure` と同じキー: series/pairs は `x`/`y`、ranked/quantity は
 *  `item`、breakdown は `category`、facts は `items`）。 */
export function buildMeasureCard(input: MeasureCardInput): { tool: 'set_measure'; params: MeasureCardParams; output_kind: MeasureShape } {
  const params: MeasureCardParams = {
    class: input.classIri,
    where: input.where,
    shape: input.shape,
  }
  if (input.x !== undefined) params.x = input.x
  if (input.y !== undefined) params.y = input.y
  if (input.item !== undefined) params.item = input.item
  if (input.category !== undefined) params.category = input.category
  if (input.items !== undefined && input.items.length > 0) params.items = input.items
  if (input.agg !== undefined) params.agg = input.agg
  // 「比べる」= 多い順（契約メモ §1-2「{item} が多い順」）— 並び順を選ばせる
  // 項目は表に無いので固定で降順にする。
  if (input.shape === 'ranked') params.order = 'desc'
  return { tool: 'set_measure', params, output_kind: input.shape }
}

// ---- cardId: params の決定論ハッシュ ----------------------------------------
//
// 規則（サーバと同じ規則にする契約 — 契約メモ §1-5）: params を key 順で正規化
// した JSON（sort_keys・区切りに空白なし・非 ASCII は \uXXXX にエスケープ —
// Python の `json.dumps(params, sort_keys=True, separators=(",", ":"),
// ensure_ascii=True)` と揃える）を SHA-256 し、先頭 16 桁の hex に
// `"card-"` を付ける。ブラウザには同期的な SHA-256 が無い（`crypto.subtle` は
// 非同期）ため、依存を増やさず決定論のためだけの純 JS 実装を下に持つ。
//
// deviation: tool 担当の `measure_spec.py`（この PR の時点で未実装）が実際の
// card_id をどう切るかはまだ確認できていない。「JSON を key 順で正規化して
// sha-256 の先頭 16 桁など」という契約メモの記述どおりに実装したが、桁数
// （16）は契約メモの「など」の例をそのまま採用したもの — 統合時に一致を
// 確認する。

/** value を再帰的に正規化する: オブジェクトはキーをソートし、配列は要素の
 *  順序を保ったまま中身だけ正規化する（Python の `sort_keys=True` と同じ —
 *  配列の並びは意味を持つので触らない）。 */
function canonicalizeValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalizeValue)
  if (value !== null && typeof value === 'object') {
    const obj = value as Record<string, unknown>
    const sorted: Record<string, unknown> = {}
    for (const key of Object.keys(obj).sort()) sorted[key] = canonicalizeValue(obj[key])
    return sorted
  }
  return value
}

/** `JSON.stringify` は既定で区切りに空白を入れない（Python の
 *  `separators=(",", ":")` と一致）が、非 ASCII 文字はエスケープせずそのまま
 *  出す — Python の `ensure_ascii=True` に合わせて \u0080 以上を \uXXXX に
 *  変換する（文字列の外に出ることはない: JSON.stringify が生成する非 ASCII は
 *  常に文字列リテラルの中身だけ）。 */
function escapeNonAscii(json: string): string {
  return json.replace(/[\u0080-￿]/g, (ch) => `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

/** Python の `json.dumps(value, sort_keys=True, separators=(",", ":"),
 *  ensure_ascii=True)` と同じ文字列を作る（キー順・空白なし・非 ASCII
 *  エスケープ）。数値の書式（int と float の違いなど）は JS と Python で
 *  一致しない場合がある — `params` に渡る数値は既にフォーム入力の
 *  `Number(...)` を通した素直な値なので実用上は揃うが、完全な保証ではない
 *  （既知の限界。他の場所の大整数/float の罠と同種）。 */
export function canonicalJson(value: unknown): string {
  return escapeNonAscii(JSON.stringify(canonicalizeValue(value)))
}

const SHA256_K: readonly number[] = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
  0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
  0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
  0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
  0xc67178f2,
]

function rightRotate(x: number, n: number): number {
  return (x >>> n) | (x << (32 - n))
}

/** 標準的な SHA-256（FIPS 180-4）の純 JS 実装。決定論・副作用なし・依存なし
 *  — `crypto.subtle.digest` は非同期なので `cardId` を同期関数にできず、
 *  ここでは同期実装を選んだ（新しい npm 依存は増やさない）。 */
export function sha256Hex(message: string): string {
  const bytes = new TextEncoder().encode(message)
  const bitLen = bytes.length * 8
  const paddedLen = (((bytes.length + 9 + 63) >> 6) << 6) >>> 0
  const padded = new Uint8Array(paddedLen)
  padded.set(bytes)
  padded[bytes.length] = 0x80
  const view = new DataView(padded.buffer)
  view.setUint32(paddedLen - 4, bitLen >>> 0, false)
  view.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false)

  let h0 = 0x6a09e667
  let h1 = 0xbb67ae85
  let h2 = 0x3c6ef372
  let h3 = 0xa54ff53a
  let h4 = 0x510e527f
  let h5 = 0x9b05688c
  let h6 = 0x1f83d9ab
  let h7 = 0x5be0cd19

  const w = new Uint32Array(64)
  for (let chunkStart = 0; chunkStart < paddedLen; chunkStart += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(chunkStart + i * 4, false)
    for (let i = 16; i < 64; i++) {
      const s0 = rightRotate(w[i - 15], 7) ^ rightRotate(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rightRotate(w[i - 2], 17) ^ rightRotate(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0
    }
    let a = h0
    let b = h1
    let c = h2
    let d = h3
    let e = h4
    let f = h5
    let g = h6
    let h = h7
    for (let i = 0; i < 64; i++) {
      const s1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25)
      const ch = (e & f) ^ (~e & g)
      const temp1 = (h + s1 + ch + SHA256_K[i] + w[i]) >>> 0
      const s0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22)
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const temp2 = (s0 + maj) >>> 0
      h = g
      g = f
      f = e
      e = (d + temp1) >>> 0
      d = c
      c = b
      b = a
      a = (temp1 + temp2) >>> 0
    }
    h0 = (h0 + a) >>> 0
    h1 = (h1 + b) >>> 0
    h2 = (h2 + c) >>> 0
    h3 = (h3 + d) >>> 0
    h4 = (h4 + e) >>> 0
    h5 = (h5 + f) >>> 0
    h6 = (h6 + g) >>> 0
    h7 = (h7 + h) >>> 0
  }
  return [h0, h1, h2, h3, h4, h5, h6, h7].map((h) => h.toString(16).padStart(8, '0')).join('')
}

/** `params` の決定論ハッシュ（上記コメント参照）: 同じ params には常に同じ
 *  `card_id`。 */
export function cardId(params: MeasureCardParams): string {
  return `card-${sha256Hex(canonicalJson(params)).slice(0, 16)}`
}

// ---------------------------------------------------------------------------
// 題名（契約メモ §1-2「既定の題名」・§3 の newcard.title_*）
// ---------------------------------------------------------------------------

const AGG_KEYS: Record<MeasureAgg, string> = {
  avg: 'cards:newcard.agg_avg',
  max: 'cards:newcard.agg_max',
  min: 'cards:newcard.agg_min',
  sum: 'cards:newcard.agg_sum',
  count: 'cards:newcard.agg_count',
}

const AGG_FALLBACK: Record<MeasureAgg, string> = {
  avg: '平均',
  max: '最大',
  min: '最小',
  sum: '合計',
  count: '件数',
}

/** ②で選んだ人向けラベル（class_schema の `label`。property IRI ではない —
 *  K4）。使わない役割は省略してよい。 */
export interface MeasureCardLabels {
  x?: string
  y?: string
  item?: string
  category?: string
  items?: string[]
  agg?: MeasureAgg
}

/** 見せ方 + 選んだ項目のラベル → 既定の題名（契約メモ §1-2 の表・§3 の
 *  `newcard.title_*`）。`t` は呼び出し側の i18next（このファイルは
 *  react-i18next に依存しない）。キーがまだ無い間の `defaultValue` は
 *  契約メモ §3 の ja 文言そのもの。 */
export function titleFor(shape: MeasureShape, labels: MeasureCardLabels, t: Translate): string {
  switch (shape) {
    case 'series':
      return t('cards:newcard.title_series', { y: labels.y ?? '', defaultValue: `${labels.y ?? ''}の推移` })
    case 'ranked':
      return t('cards:newcard.title_ranked', { item: labels.item ?? '', defaultValue: `${labels.item ?? ''}が多い順` })
    case 'breakdown':
      return t('cards:newcard.title_breakdown', {
        category: labels.category ?? '',
        defaultValue: `${labels.category ?? ''}ごとの件数`,
      })
    case 'pairs':
      return t('cards:newcard.title_pairs', {
        x: labels.x ?? '',
        y: labels.y ?? '',
        defaultValue: `${labels.x ?? ''}と${labels.y ?? ''}`,
      })
    case 'quantity': {
      const agg = labels.agg ? t(AGG_KEYS[labels.agg], { defaultValue: AGG_FALLBACK[labels.agg] }) : ''
      return t('cards:newcard.title_quantity', { item: labels.item ?? '', agg, defaultValue: `${labels.item ?? ''}の${agg}` })
    }
    case 'facts': {
      const items = (labels.items ?? []).join('・')
      return t('cards:newcard.title_facts', { items, defaultValue: items })
    }
  }
}

/** `buildMeasureCard` + `cardId` + `titleFor` をまとめ、`CardSpec`（保存形）を
 *  1 回で組み立てる。「作る」で `runCard` が成功した後に呼ぶ（`created_at` は
 *  呼び出し時刻）。 */
export function toCardSpec(
  input: MeasureCardInput,
  labels: MeasureCardLabels,
  subjectKey: string,
  t: Translate,
): CardSpec {
  const { params, output_kind } = buildMeasureCard(input)
  return {
    card_id: cardId(params),
    subject_key: subjectKey,
    tool: 'set_measure',
    params,
    title: titleFor(input.shape, labels, t),
    output_kind,
    created_at: new Date().toISOString(),
  }
}
