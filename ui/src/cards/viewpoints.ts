// 「観点」（viewpoint）— カードの作り方から条件（`where`）を除いたもの。契約
// メモ contract_pr_f6.md §1。純関数のみ（store アクセスなし・LLM なし）。
//
// 観点は保存しない — appdata の `cards`（`cardStore.ts`）から毎回、決定論に
// 派生させる（カードを全部消せば観点も消える・契約メモ §1-1）。id は F4 の
// `cardId`（`measureCardFields.ts`）と同じ規則（params を key 順で正規化した
// JSON の sha-256 先頭 16 桁）— `where` を除く点だけが違うので、同じ
// `canonicalJson`/`sha256Hex` をそのまま再利用する。

import type { CardSpec } from './cardsApi'
import type { LinkingKind, MeasureCardParams, MeasureShape, MeasureWhereClause, SetSpec } from './cardsApi'
import { canonicalJson, sha256Hex, type Translate } from './measureCardFields'

export type { Translate } from './measureCardFields'

// ---------------------------------------------------------------------------
// 観点の id（契約メモ §1-1）
// ---------------------------------------------------------------------------

/** `params` から `where` キーだけを取り除いた新しいオブジェクト（`where` は
 *  必須キーなので `delete` ではなく詰め替えで作る）。 */
function omitWhere(params: MeasureCardParams): MeasureCardParams {
  const rest: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (key !== 'where') rest[key] = value
  }
  return rest as MeasureCardParams
}

/** `params` から `where` を除いた正規化 JSON のハッシュ。`measureCardFields.ts`
 *  の `cardId` と同じ規則（sha-256 先頭 16 桁の hex）だが、カードの id
 *  （`card-` 接頭辞）と混同しないよう `vp-` を付ける。 */
export function viewpointId(params: MeasureCardParams): string {
  return `vp-${sha256Hex(canonicalJson(omitWhere(params))).slice(0, 16)}`
}

// ---------------------------------------------------------------------------
// 観点の派生（契約メモ §1-1）
// ---------------------------------------------------------------------------

export interface Viewpoint {
  id: string
  /** 観点の対象の種類（`params.class`）。 */
  class: string
  shape: MeasureShape
  /** カードの作り方から `where` を除いたもの（`paramsForPage` がこれに
   *  ページごとの条件を足して `set_measure` の params を組み立てる）。 */
  params: MeasureCardParams
  /** カードの題名そのまま（契約メモ §1-5「チップは題名そのもの」）。 */
  title: string
  /** この観点が使われているページの数（`subject_key` の distinct 数）。 */
  usedOn: number
}

interface ViewpointGroup {
  class: string
  shape: MeasureShape
  params: MeasureCardParams
  subjectKeys: Set<string>
  /** 題名は「最小の card_id を持つカード」のものを採用する — カード配列の
   *  並び順（appdata/localStorage の読み込み順）に依存しない決定論のため。 */
  minCardId: string
  title: string
}

/** 全カード（`useAllCards()` の結果）→ 観点の集合（契約メモ §1-1）。同じ
 *  観点（`where` 以外が同じ）は 1 つにまとまる。順は使用数の多い順→題名順
 *  →id 順（決定論・入力の並び順に依存しない）。 */
export function viewpointsFrom(cards: CardSpec[]): Viewpoint[] {
  const groups = new Map<string, ViewpointGroup>()
  for (const card of cards) {
    const id = viewpointId(card.params)
    let group = groups.get(id)
    if (!group) {
      group = {
        class: card.params.class,
        shape: card.output_kind,
        params: omitWhere(card.params),
        subjectKeys: new Set(),
        minCardId: card.card_id,
        title: card.title,
      }
      groups.set(id, group)
    }
    group.subjectKeys.add(card.subject_key)
    if (card.card_id < group.minCardId) {
      group.minCardId = card.card_id
      group.title = card.title
    }
  }
  const viewpoints: Viewpoint[] = [...groups.entries()].map(([id, g]) => ({
    id,
    class: g.class,
    shape: g.shape,
    params: g.params,
    title: g.title,
    usedOn: g.subjectKeys.size,
  }))
  viewpoints.sort((a, b) => b.usedOn - a.usedOn || a.title.localeCompare(b.title) || a.id.localeCompare(b.id))
  return viewpoints
}

// ---------------------------------------------------------------------------
// どのページに出すか（契約メモ §1-3）
// ---------------------------------------------------------------------------

/** `applicableViewpoints`/`paramsForPage` に渡すページの形。呼び出し側
 *  （`ViewpointStrip.tsx`）がページの種類ごとに埋める — 一覧のページは
 *  `classIri`（=spec.class）・`where`・`sourceScope`、1 件のページは
 *  `classIri`（この 1 件自身の種類・わかっていれば）・`linkingKinds`（この
 *  1 件を指す種類の候補・`cardsApi.linkingKinds` の結果そのまま）・`iri`。 */
export interface ViewpointPage {
  kind: 'individual' | 'set'
  /** 一覧のページ: `spec.class`。1 件のページ: この 1 件自身の種類（rdf:type
   *  ・わかっていれば）。 */
  classIri?: string
  /** 1 件のページだけ: この 1 件を指す種類の候補（`linkingKinds` の結果その
   *  まま）。`applicableViewpoints` は `class_iri` の集合として読み、
   *  `paramsForPage` は `property`/`iri` も使う。 */
  linkingKinds?: LinkingKind[]
  /** 一覧のページだけ: このページの条件（`paramsForPage` が使う）。 */
  where?: MeasureWhereClause[]
  /** 一覧のページだけ: このページの `source_scope`。 */
  sourceScope?: SetSpec['source_scope']
  /** 1 件のページだけ: この 1 件の IRI（`paramsForPage` が使う）。 */
  iri?: string
  /** このページに既にある観点の id（`viewpointId` の値）— 出さない
   *  （契約メモ §1-2「このページに既にある観点は出さない」）。 */
  existingViewpointIds: string[]
}

/** 契約メモ §1-3 の判定。観点の `class` が、
 *  - 一覧のページ: その一覧の `spec.class` と同じ、または
 *  - 1 件のページ: その 1 件の種類と同じ（かつ「数字 1 つ」「表」だけ — 1 件
 *    自身の quantity は値が 1 つなので他の見せ方は描けない）、または
 *    `linkingKinds` の `class_iri` に含まれる（この 1 件を指す種類・見せ方の
 *    制限なし）
 *  のとき。既にこのページにある観点（`existingViewpointIds`）は除く。 */
export function applicableViewpoints(viewpoints: Viewpoint[], page: ViewpointPage): Viewpoint[] {
  const existing = new Set(page.existingViewpointIds)
  const linkingClasses = new Set((page.linkingKinds ?? []).map((k) => k.class_iri))
  return viewpoints.filter((v) => {
    if (existing.has(v.id)) return false
    if (page.kind === 'set') return v.class === page.classIri
    const ownClassMatch = v.class === page.classIri && (v.shape === 'quantity' || v.shape === 'facts')
    return ownClassMatch || linkingClasses.has(v.class)
  })
}

// ---------------------------------------------------------------------------
// 1 クリックで足す条件付け（契約メモ §1-4）
// ---------------------------------------------------------------------------

/** 観点にこのページの条件を付けた `set_measure` の params。一覧のページは
 *  `spec.where`（`source_scope` も同じ）、1 件のページは `linkingKinds` の
 *  該当する種類の `{ property, iri }`（候補が複数なら最初の 1 つ — 契約メモ
 *  §1-4）。条件が組めない（一覧なのに `where` が無い・1 件なのに `iri` が
 *  無い・1 件なのに `linkingKinds` に観点の class と一致する候補が無い）
 *  ときは `null`。最後のケースは、観点の class が「この 1 件自身の種類」と
 *  一致するとき（`applicableViewpoints` の own-class-match）に起こる —
 *  `linkingKinds` はこの 1 件を**指す**種類の一覧であり、1 件自身の種類は
 *  含まれないため、`{ property, iri }` を組む材料が無い。ここで無条件の
 *  params を返すと「この 1 件のカード」を装った全体集計になってしまう。 */
export function paramsForPage(viewpoint: Viewpoint, page: ViewpointPage): MeasureCardParams | null {
  if (page.kind === 'set') {
    if (page.where === undefined) return null
    const params: MeasureCardParams = { ...viewpoint.params, where: page.where }
    if (page.sourceScope) params.source_scope = page.sourceScope
    return params
  }
  if (page.iri === undefined) return null
  // `linkingKinds` は class_iri・property の辞書順で届く（サーバ側の並び —
  // `subject_tools.linking_kinds`）ので、同じ class の候補のうち最初に見つかる
  // ものが「最初の 1 つ」になる（決定論）。
  const candidate = (page.linkingKinds ?? []).find((k) => k.class_iri === viewpoint.class)
  if (!candidate) return null
  const where: MeasureWhereClause[] = [{ property: candidate.property, iri: page.iri }]
  return { ...viewpoint.params, where }
}

// ---------------------------------------------------------------------------
// 題名（契約メモ §1-5）
// ---------------------------------------------------------------------------

/** 観点の題名（カードの題名そのまま — 条件を除いても題名は変わらない。
 *  題名は property の label だけから組み立てられ、`where` に依存しないため）。
 *  `t` は呼び出し側と同じ i18next インスタンスを受け取るだけ（このファイルは
 *  react-i18next に依存しない — `titleFor` と同じ流儀）で、今のところ使わない
 *  （将来 i18n を挟む余地のための引数）。 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- `_t` は `titleFor` 系と API を揃えるためだけの引数（契約メモ §1-1 のシグネチャ）。
export function viewpointTitle(viewpoint: Viewpoint, _t: Translate): string {
  return viewpoint.title
}
