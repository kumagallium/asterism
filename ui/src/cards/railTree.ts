// 左レールの木を組む純関数（契約メモ contract_pr_f9.md §1-2・§5: ui-rail 担当）。
// 入力は `listDatasets()`（`cardsApi.ts`）と `useSubjects()`（`subjectStore.ts`）の
// 結果だけ — fetch もレンダリングもしない（`railTree.test.ts` がここを検証する）。
//
// 節の割り当て（旧: データセットごと own/open → 新: 種類ごと・区切り無し）:
//   - 主語（1 件・条件で集めた一覧）は `class_iri` でグループ化する
//     （データセットではない。オープンデータ／自分のデータの区切りも無い）。
//   - グループの並びは名前順（`class_label` の決定論の辞書順・localeCompare は
//     使わない — ICU の版で結果が揺れるため）。
//   - `class_iri` が無い主語は「その他」節に落ちる（subjectStore.ts の埋め戻しが
//     1 回だけ試みるが、失敗した主語はここで拾われる）。
//   - 見本の印は、見本のデータセット（`is_demo`）由来の主語（子）にだけ付ける
//     （契約メモ §1-2: グループの見出し行には出さない）。

import type { CardsDatasetSummary, SubjectItem } from './cardsApi'

export interface RailChild {
  subjectKey: string
  label: string
  kind: 'individual' | 'set'
  /** 個体の 3 状態（凡例の点の色）。絞り込みは常に `null`（呼び出し側は
   *  `kind === 'set'` を先に見る）。 */
  match: SubjectItem['match']
  /** 見本のデータセット（`is_demo`）由来か（契約メモ §1-2）。 */
  isSample: boolean
}

export interface RailKindNode {
  classIri: string
  label: string
  children: RailChild[]
}

export interface RailTree {
  kinds: RailKindNode[]
  /** `class_iri` が無い主語（契約メモ §1-2）。 */
  other: RailChild[]
}

/** 新しい方が上（`subjectStore.ts` の `sortSubjects` と同じ並び）。 */
function byCreatedDesc(items: SubjectItem[]): SubjectItem[] {
  return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

function toChild(item: SubjectItem, sampleDatasets: Set<string>): RailChild {
  return {
    subjectKey: item.subject_key,
    label: item.label ?? '',
    kind: item.kind,
    match: item.kind === 'set' ? null : item.match,
    isSample: item.dataset_id != null && sampleDatasets.has(item.dataset_id),
  }
}

export interface BuildRailTreeInput {
  datasets: CardsDatasetSummary[]
  subjects: SubjectItem[]
}

/** 左レールの木を組む（契約メモ §1-2）。 */
export function buildRailTree({ datasets, subjects }: BuildRailTreeInput): RailTree {
  const sampleDatasets = new Set(datasets.filter((d) => d.is_demo).map((d) => d.id))

  const groups = new Map<string, { label: string; items: SubjectItem[] }>()
  const otherItems: SubjectItem[] = []

  for (const s of subjects) {
    if (!s.class_iri) {
      otherItems.push(s)
      continue
    }
    const existing = groups.get(s.class_iri)
    if (existing) {
      existing.items.push(s)
      if (!existing.label && s.class_label) existing.label = s.class_label
    } else {
      groups.set(s.class_iri, { label: s.class_label ?? '', items: [s] })
    }
  }

  const kinds: RailKindNode[] = [...groups.entries()]
    .map(([classIri, g]) => ({
      classIri,
      label: g.label,
      children: byCreatedDesc(g.items).map((i) => toChild(i, sampleDatasets)),
    }))
    .sort((a, b) => (a.label < b.label ? -1 : a.label > b.label ? 1 : 0))

  const other = byCreatedDesc(otherItems).map((i) => toChild(i, sampleDatasets))

  return { kinds, other }
}
