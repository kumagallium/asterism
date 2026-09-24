// class_schema（1 件の種類のスキーマ）→ 絞り込みフォームの項目、を決定論で
// 組む純関数（決定 8「絞り込みのフォーム」: LLM ゼロ。「1 行書く→条件に直す」は
// Phase 2）。分野の項目を固定で書かない — 何を出すかはすべて
// `schema.properties[].kind` から決まる。
//
// quantity → 「より大きい／より小さい／範囲」の対象になる。
// category → 「次のどれか（複数）」の対象になる（実際の選択肢＝distinct 値は
//   set_breakdown の上位 12 から取るので、ここでは対象の property を返すだけ）。
// identifier / text / link → Phase 2（「含む」）。ここでは出さない。
//
// 並べ方は quantity から選ぶ（+ 昇降）。
//
// ファイル名について（deviations）: 契約メモ §6.3 は `setForm.ts`（このファイル）
// と `SetForm.tsx`（フォームのコンポーネント）を別ファイルとして挙げているが、
// 同じディレクトリに大文字小文字だけが違う 2 ファイルを置くと macOS/Windows の
// 大文字小文字を区別しないファイルシステム上で `tsc -b` が TS1149/TS1261
// （"differs ... only in casing"）で止まる（Linux の CI では気づけない）。
// この純関数側だけ `setFormFields.ts` に改名して回避した。エクスポートする
// 名前・シグネチャは変えていない。

import type { SchemaProperty } from './cardsApi'

/** setFormFields.ts が読む最小の class_schema の形（`cardsApi.ClassSchema` の
 *  部分集合）。この関数のテストが `cardsApi.ts` の fetch 実装に依存しなくて
 *  済むよう、必要なフィールドだけを独立して持つ（構造的に部分集合なので
 *  `ClassSchema` をそのまま渡せる）。 */
export interface ClassSchemaLike {
  class_iri: string
  label: string
  properties: SchemaProperty[]
}

export type SetFilterOp = 'gt' | 'lt' | 'between'

export interface QuantityFilterField {
  kind: 'quantity'
  property: string
  label: string
  unit: string | null
  ops: readonly SetFilterOp[]
}

export interface CategoryFilterField {
  kind: 'category'
  property: string
  label: string
}

export type SetFilterField = QuantityFilterField | CategoryFilterField

export interface SetOrderOption {
  property: string
  label: string
  unit: string | null
}

export interface SetFormSpec {
  filters: SetFilterField[]
  orderOptions: SetOrderOption[]
  /** 「絞り込む」が既定で使う件数（引き継ぎ書 §5.5・set_members の既定 20）。 */
  defaultLimit: number
  /** SetForm の件数入力の上限（契約メモ §6.3「上限 20」。api 自体の上限は 200
   *  だが、フォームの選べる範囲はこれより狭い）。 */
  maxLimit: number
}

const QUANTITY_OPS: readonly SetFilterOp[] = ['gt', 'lt', 'between']

export const SET_FORM_DEFAULT_LIMIT = 20
export const SET_FORM_MAX_LIMIT = 20

/**
 * class_schema の `properties[]` から、絞り込みフォームの項目を決定論で組む。
 * 同じ schema には常に同じ `SetFormSpec` を返す（副作用なし・入力を書き換え
 * ない）。
 */
// ---------------------------------------------------------------------------
// PR E 契約メモ §4「条件を変える」— 分類の値が 12 を超えるときは検索欄つきの
// 一覧にし、上位 12 だけ見せて「さらに表示」。ここは「何を見せるか」の決定論
// だけを持つ純関数 — 実際の選択肢の取得（set_breakdown 呼び出し）は
// SetForm.tsx の責任。
// ---------------------------------------------------------------------------

/** SetForm.tsx が見せる分類の選択肢の上限（超えたら「さらに表示」）。 */
export const CATEGORY_VISIBLE_LIMIT = 12

export interface CategoryOptionsView {
  /** 実際に描画する選択肢。 */
  visible: string[]
  /** 「さらに表示」を出すべきか（検索中・showAll のときは出さない）。 */
  hasMore: boolean
  /** 検索後（検索が無ければ全件）の件数。 */
  total: number
}

/**
 * 分類の選択肢（`values`）から、実際に見せる範囲を決定論で決める。`search` が
 * あれば大小文字を無視した部分一致でまず絞り込み、その後 `showAll` が立って
 * いない限り上位 `CATEGORY_VISIBLE_LIMIT` 件だけを見せる。検索中は既に絞り込ま
 * れているので「さらに表示」は出さない（`hasMore` は立てない）。同じ入力には
 * 常に同じ結果（副作用なし・入力を書き換えない）。
 */
export function categoryOptionsView(
  values: string[],
  opts: { search?: string; showAll?: boolean } = {},
): CategoryOptionsView {
  const query = (opts.search ?? '').trim().toLowerCase()
  const filtered = query ? values.filter((v) => v.toLowerCase().includes(query)) : values
  const capped = !query && !opts.showAll
  return {
    visible: capped ? filtered.slice(0, CATEGORY_VISIBLE_LIMIT) : filtered,
    hasMore: capped && filtered.length > CATEGORY_VISIBLE_LIMIT,
    total: filtered.length,
  }
}

export function buildSetFormSpec(schema: ClassSchemaLike): SetFormSpec {
  const filters: SetFilterField[] = []
  const orderOptions: SetOrderOption[] = []
  for (const prop of schema.properties) {
    if (prop.kind === 'quantity') {
      filters.push({
        kind: 'quantity',
        property: prop.iri,
        label: prop.label,
        unit: prop.unit ?? null,
        ops: QUANTITY_OPS,
      })
      orderOptions.push({ property: prop.iri, label: prop.label, unit: prop.unit ?? null })
    } else if (prop.kind === 'category') {
      filters.push({ kind: 'category', property: prop.iri, label: prop.label })
    }
    // identifier / text / link: Phase 2（「含む」）— ここでは出さない。
  }
  return { filters, orderOptions, defaultLimit: SET_FORM_DEFAULT_LIMIT, maxLimit: SET_FORM_MAX_LIMIT }
}
