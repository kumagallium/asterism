// 「決めたこと」画面の純関数（絞り込み・1 行 1 文・hash）。描画（DecisionsView）から切り離して
// vitest で固定する。LLM は使わない。
import type { Alignment } from '../crosswalkApi'
import { relationKey } from '../lineChoice'
import { endLabel } from '../vocabQuestion'
import type { Translate } from './decide'

/** 絞り込みの pill。`perspective`（つながり同士）だけは、一覧ではなく対応づけの画面を出す。 */
export const DECISION_FILTERS = [
  'all',
  'sub',
  'same',
  'quantity',
  'standard',
  'perspective',
  'broken',
] as const
export type DecisionFilter = (typeof DECISION_FILTERS)[number]

const SUB = new Set(['subClassOf', 'subPropertyOf'])
const SAME = new Set(['equivalentClass', 'equivalentProperty'])
const KNOWN = new Set([...SUB, ...SAME, 'hasQuantityKind'])

/** 1 行の絞り込みの判定。`perspective` は一覧では使わない（常に false）。 */
export function matchesFilter(a: Alignment, filter: DecisionFilter): boolean {
  const rel = relationKey(a.relation)
  switch (filter) {
    case 'all':
      return true
    case 'sub':
      return SUB.has(rel)
    case 'same':
      return SAME.has(rel)
    case 'quantity':
      return rel === 'hasQuantityKind'
    case 'standard':
      return a.source_kind === 'standard' || a.target_kind === 'standard'
    case 'broken':
      return !!a.broken
    default:
      return false
  }
}

/** 絞り込みごとの件数（pill に添える）。 */
export function filterCounts(list: readonly Alignment[]): Record<DecisionFilter, number> {
  const out = Object.fromEntries(DECISION_FILTERS.map((f) => [f, 0])) as Record<DecisionFilter, number>
  for (const f of DECISION_FILTERS) out[f] = list.filter((a) => matchesFilter(a, f)).length
  return out
}

/** 新しい順（日付が無い行は後ろ）。同じ日付は元の順（api は古い順なので、後に決めたものが先）。 */
export function newestFirst(list: readonly Alignment[]): Alignment[] {
  return list
    .map((a, i) => ({ a, i }))
    .sort((x, y) => (y.a.at || '').localeCompare(x.a.at || '') || y.i - x.i)
    .map((x) => x.a)
}

/** 端の表示名。データセットの端は「名前（データセット名）」。 */
export function endName(
  a: Alignment,
  side: 'source' | 'target',
  datasetNames: Record<string, string>,
): string {
  const iri = side === 'source' ? a.source : a.target
  const kind = side === 'source' ? a.source_kind : a.target_kind
  const label = side === 'source' ? a.source_label : a.target_label
  const dataset = side === 'source' ? a.source_dataset : a.target_dataset
  const name = endLabel(iri, kind, label)
  const dsName = dataset ? (datasetNames[dataset] ?? dataset) : ''
  return kind === 'dataset' && dsName ? `${name}（${dsName}）` : name
}

/** 1 行 1 文: 「『Sample（Starrydata）』は『試料』の一種」。閉集合に無い関係は関係名のまま挟む。 */
export function decisionSentence(
  a: Alignment,
  datasetNames: Record<string, string>,
  t: Translate,
): string {
  const subject = endName(a, 'source', datasetNames)
  const target = endName(a, 'target', datasetNames)
  const rel = relationKey(a.relation)
  if (!KNOWN.has(rel)) return t('decisions:sentence.other', { subject, target, relation: rel })
  return t(`decide:sentence.${rel}`, { subject, target })
}

/** 共有の用語（=問いを持つ側）に掛かる問いの数。用語が端に無い・問いが無ければ 0。 */
export function questionCount(
  a: Alignment,
  terms: readonly { iri: string; cqs?: readonly unknown[] }[],
): number {
  const ends = new Set([a.source, a.target])
  return terms.filter((term) => ends.has(term.iri)).reduce((n, term) => n + (term.cqs?.length ?? 0), 0)
}

/** 1 つの種類・用語に掛かる行か（`#/decisions?about=<IRI>`）。 */
export function involves(a: Alignment, iri: string): boolean {
  return a.source === iri || a.target === iri
}

/** `#/decisions?filter=perspective&about=<IRI>` の読み書き（クエリは `?` の後ろ）。
 *  `about` はその IRI に掛かる決めたことだけに絞る（星の横の「決めたこと N 件 →」の着地先）。 */
export function parseDecisionsQuery(query: string): { filter: DecisionFilter; about?: string } {
  const params = new URLSearchParams(query)
  const filter = params.get('filter')
  const about = params.get('about')
  return {
    filter: (DECISION_FILTERS as readonly string[]).includes(filter ?? '')
      ? (filter as DecisionFilter)
      : 'all',
    ...(about ? { about } : {}),
  }
}

export function decisionsQuery(filter: DecisionFilter, about?: string): string {
  const params = new URLSearchParams()
  if (filter !== 'all') params.set('filter', filter)
  if (about) params.set('about', about)
  const q = params.toString()
  return q ? `?${q}` : ''
}

/** 「決めたこと」への hash（絞り込みつき）。 */
export function decisionsHash(filter: DecisionFilter = 'all', about?: string): string {
  return `#/decisions${decisionsQuery(filter, about)}`
}
