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

import type { CardsDatasetSummary, ClassEntry, SubjectItem } from './cardsApi'

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
  /** PR F16 §1.5: この種類が共有ハブ（同じものの 1 つのページ）なら true
   *  （`ClassEntry.is_hub` から。見出しに小さな印「つながり」を出す判定にだけ
   *  使う）。 */
  isHub: boolean
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
  /** PR F16 §1.5: 種類の一覧（`listClasses()`）。`is_hub` を見出しの印付けに、
   *  `label` を見出しの名前（いまの表示名）に使う — 省略時（api がまだ
   *  返さない・未取得）は全て印なし・名前は主語に保存した写し。 */
  classes?: ClassEntry[]
}

/** 左レールの木を組む（契約メモ §1-2）。 */
export function buildRailTree({ datasets, subjects, classes }: BuildRailTreeInput): RailTree {
  const sampleDatasets = new Set(datasets.filter((d) => d.is_demo).map((d) => d.id))
  const hubClasses = new Set((classes ?? []).filter((c) => c.is_hub).map((c) => c.class_iri))
  // 種類の見出しは、主語に保存した写し（追加したときの名前）ではなく、いまの
  // 表示名を使う — 保存した写しは、あとで種類の名前が変わっても古いまま残る
  // （実機: つながりの種類が、表示名に直したあとも古い名前で出ていた）。
  // 種類の一覧がまだ無い・その種類が載っていないときだけ、写しに落とす。
  // 同じ種類の行が複数あるとき（複数のデータセットが同じ種類を持つ）は、先に
  // 出た行を使う — 種類の一覧は件数の多い順なので、主な方の名前になる。
  const currentLabels = new Map<string, string>()
  for (const c of classes ?? []) {
    if (c.label && !currentLabels.has(c.class_iri)) currentLabels.set(c.class_iri, c.label)
  }

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
      label: currentLabels.get(classIri) ?? g.label,
      children: byCreatedDesc(g.items).map((i) => toChild(i, sampleDatasets)),
      isHub: hubClasses.has(classIri),
    }))
    .sort((a, b) => (a.label < b.label ? -1 : a.label > b.label ? 1 : 0))

  const other = byCreatedDesc(otherItems).map((i) => toChild(i, sampleDatasets))

  return { kinds, other }
}
