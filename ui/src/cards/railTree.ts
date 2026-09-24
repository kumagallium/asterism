// 左レールの木を組む純関数（契約メモ §2.1・§5: ui-rail 担当）。入力は
// `listDatasets()`（`cardsApi.ts`）と `useSubjects()`（`subjectStore.ts`）の結果
// だけ — fetch もレンダリングもしない（`railTree.test.ts` がここを検証する）。
//
// 節の割り当て:
//   - データセットは `origin` で振り分ける（own/unknown → 自分のデータ、
//     open → オープンデータ）。
//   - 主語（1 件・条件で集めた一覧）はその `dataset_id` が一致するデータセット
//     の子になる。`dataset_id` が無い、または既知のどのデータセットとも一致
//     しない主語は「その他」節に落ちる（subjectStore.ts の埋め戻しが 1 回だけ
//     試みるが、失敗した主語はここで拾われる）。
//   - 見本の印はデータセット行にだけ出す（`is_demo`）。子には出さない。

import type { CardsDatasetSummary } from './cardsApi'
import type { SubjectItem } from './cardsApi'

export interface RailChild {
  subjectKey: string
  label: string
  kind: 'individual' | 'set'
  /** 個体は class_label、絞り込みは持たない（呼び出し側が固定語「一覧」を出す）。 */
  kindLabel: string | null
  /** 個体の 3 状態（凡例の点の色）。絞り込みは常に `null`（呼び出し側は
   *  `kind === 'set'` を先に見る）。 */
  match: SubjectItem['match']
}

/** データセットの取り込み状況（契約メモ §2.1 の行右の小さな状態）。
 *  `null` は公開済み（状態は出さない）。 */
export type RailDatasetState = 'draft' | 'ingesting' | null

export interface RailDatasetNode {
  datasetId: string
  label: string
  origin: 'own' | 'open' | 'unknown'
  isSample: boolean
  state: RailDatasetState
  expanded: boolean
  children: RailChild[]
}

export interface RailTree {
  own: RailDatasetNode[]
  open: RailDatasetNode[]
  /** dataset_id が無い、または既知のデータセットと一致しない主語（契約メモ
   *  §2.1・rail.other_section）。 */
  other: RailChild[]
}

function toChild(item: SubjectItem): RailChild {
  return {
    subjectKey: item.subject_key,
    label: item.label ?? '',
    kind: item.kind,
    kindLabel: item.kind === 'set' ? null : item.class_label,
    match: item.kind === 'set' ? null : item.match,
  }
}

function toState(stage: CardsDatasetSummary['stage']): RailDatasetState {
  if (stage === 'design') return 'draft'
  if (stage === 'ingested') return 'ingesting'
  return null
}

/** 新しい方が上（`subjectStore.ts` の `sortSubjects` と同じ並び）。純粋に
 *  ここだけで並べる — グループが既にデータセット単位に割れているため
 *  own/open の順位づけは不要。 */
function byCreatedDesc(items: SubjectItem[]): SubjectItem[] {
  return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

export interface BuildRailTreeInput {
  datasets: CardsDatasetSummary[]
  subjects: SubjectItem[]
  /** いま開いているページのデータセット（あれば）。展開の既定を決める
   *  （契約メモ §2.1: 4 つ以上は現在地だけ展開）。 */
  currentDatasetId?: string | null
  /** localStorage に控えた「人が明示的に開閉した」上書き（try/catch は
   *  呼び出し側の責務 — ここは純粋なマップとして受け取るだけ）。 */
  expandedOverrides?: Record<string, boolean>
}

/** 左レールの木を組む（契約メモ §2.1）。 */
export function buildRailTree({
  datasets,
  subjects,
  currentDatasetId,
  expandedOverrides,
}: BuildRailTreeInput): RailTree {
  const known = new Set(datasets.map((d) => d.id))
  const byDataset = new Map<string, SubjectItem[]>()
  const other: RailChild[] = []

  for (const s of subjects) {
    if (s.dataset_id && known.has(s.dataset_id)) {
      const list = byDataset.get(s.dataset_id)
      if (list) list.push(s)
      else byDataset.set(s.dataset_id, [s])
    } else {
      other.push(toChild(s))
    }
  }

  // 展開の既定（契約メモ §2.1）: データセットが 3 つ以下なら全部展開。
  // 4 つ以上なら「いま開いているページのデータセット」だけ展開。
  const expandAllByDefault = datasets.length <= 3

  const own: RailDatasetNode[] = []
  const open: RailDatasetNode[] = []

  for (const d of datasets) {
    const children = byCreatedDesc(byDataset.get(d.id) ?? []).map(toChild)
    const defaultExpanded = expandAllByDefault || d.id === currentDatasetId
    const expanded = expandedOverrides?.[d.id] ?? defaultExpanded
    const node: RailDatasetNode = {
      datasetId: d.id,
      label: d.name,
      origin: d.origin,
      isSample: d.is_demo,
      state: toState(d.stage),
      expanded,
      children,
    }
    if (d.origin === 'open') open.push(node)
    else own.push(node)
  }

  return { own, open, other }
}
