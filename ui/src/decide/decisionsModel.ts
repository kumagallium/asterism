// 「決めたこと」画面の純関数（絞り込み・1 行 1 文・hash）。描画（DecisionsView）から切り離して
// vitest で固定する。LLM は使わない。
import type { Alignment } from '../crosswalkApi'
import { KNOWLEDGE_RELATIONS, relationKey } from '../lineChoice'
import { endLabel } from '../vocabQuestion'
import type { Translate } from './decide'

/** 絞り込みの pill。`perspective`（つながり同士）だけは、一覧ではなく対応づけの画面を出す。 */
export const DECISION_FILTERS = [
  'all',
  'sub',
  'same',
  'quantity',
  'knowledge',
  'standard',
  'perspective',
  'broken',
] as const
export type DecisionFilter = (typeof DECISION_FILTERS)[number]

const SUB = new Set(['subClassOf', 'subPropertyOf'])
const SAME = new Set(['equivalentClass', 'equivalentProperty'])
const KNOWLEDGE = new Set<string>(KNOWLEDGE_RELATIONS)
const KNOWN = new Set([...SUB, ...SAME, 'hasQuantityKind', ...KNOWLEDGE])

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
    case 'knowledge':
      return KNOWLEDGE.has(rel)
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

/** 1 行 1 文: 「『Sample（Starrydata）』は『試料』の一種」。9 関係（一種・同じ・量・一部・下げる・上げる・変える）は
 *  文にする。閉集合に無い関係だけ関係名のまま挟む（生の `lowers` は出さない）。 */
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

/** 決めたことの並び（新しい順）の 1 項目: 1 本の線か、同じ「まとめて確定」で決めた線の束。 */
export type DecisionEntry =
  | { kind: 'one'; a: Alignment }
  | { kind: 'batch'; batch: string; items: Alignment[]; at: string; draftedBy: string }

/**
 * 同じ「まとめて確定」（`batch`）の線を 1 つに畳む。2 本以上あるものだけ束にする（1 本は 1 行のまま）。
 * 束の位置は、束の中でいちばん新しい線のところ（並びは入力のまま — 新しい順に渡せば新しい順）。
 */
export function groupByBatch(list: readonly Alignment[]): DecisionEntry[] {
  const size = new Map<string, number>()
  for (const a of list) if (a.batch) size.set(a.batch, (size.get(a.batch) ?? 0) + 1)
  const out: DecisionEntry[] = []
  const at = new Map<string, Extract<DecisionEntry, { kind: 'batch' }>>()
  for (const a of list) {
    if (!a.batch || (size.get(a.batch) ?? 0) < 2) {
      out.push({ kind: 'one', a })
      continue
    }
    let g = at.get(a.batch)
    if (!g) {
      g = { kind: 'batch', batch: a.batch, items: [], at: a.at || '', draftedBy: a.drafted_by ?? 'human' }
      at.set(a.batch, g)
      out.push(g)
    }
    g.items.push(a)
  }
  return out
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
