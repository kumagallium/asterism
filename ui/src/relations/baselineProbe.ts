// 関係の直書きを目録に寄せる前後で、結果が同じことを固定するための「測る関数」。
// 寄せる前のコードで golden（relationsBaseline.golden.json）を作り、寄せた後のコードが同じ値を返すことを見る。
import { decisionSentence, filterCounts, matchesFilter, DECISION_FILTERS } from '../decide/decisionsModel'
import {
  isDirectedRelation,
  isEquivalenceRelation,
  isKnowledgeRelation,
  KNOWLEDGE_RELATIONS,
  knowledgeRelationsFor,
  RELATION_ORDER,
  relationsFor,
  relationSymbol,
  type TermKind,
} from '../lineChoice'
import { assignRels, isSymmetricRel } from '../chat/graphPlace/rowFns'
import { draftRelationLabel, relationLabel } from '../chat/map/mapModel'
import { relSentence } from '../chat/sentenceRows'
import { realRelationOf } from '../sky/skyLayout'
import { isHierarchy, REL_KINDS, relationPredicate, type RelKind } from '../themes/themeDraft'
import type { Alignment } from '../crosswalkApi'

const PREDICATES = [...RELATION_ORDER]
const NAMES = [...REL_KINDS]
const EXTRA = ['', 'unknown', 'competes_with', 'isA', 'measures', 'http://purl.obolibrary.org/obo/RO_0002212', 'obo:RO_0002213']
const KINDS: TermKind[] = ['class', 'property', 'quantity', 'unknown']
const TO_KINDS = ['class', 'property', undefined] as const

export function probe(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  out.RELATION_ORDER = PREDICATES
  out.KNOWLEDGE_RELATIONS = [...KNOWLEDGE_RELATIONS]
  out.REL_KINDS = NAMES
  out.ASSIGN_RELS = assignRels()
  const preds = [...PREDICATES, ...EXTRA, null, undefined]
  out.predicateFns = preds.map((p) => ({
    p: p ?? String(p),
    directed: isDirectedRelation(p),
    equivalence: isEquivalenceRelation(p),
    knowledge: isKnowledgeRelation(p),
    symbol: relationSymbol(p),
  }))
  out.knowledgeRelationsFor = KINDS.flatMap((s) => KINDS.map((t) => ({ s, t, r: knowledgeRelationsFor(s, t) })))
  out.relationsFor = KINDS.flatMap((s) => KINDS.map((t) => ({ s, t, r: relationsFor(s, t) })))
  const rels = [...NAMES, ...EXTRA]
  out.realRelationOf = rels.flatMap((r) => TO_KINDS.map((k) => ({ r, k: k ?? 'undef', v: realRelationOf(r, k) ?? null })))
  out.relSentence = (['ja', 'en'] as const).flatMap((l) => rels.map((r) => ({ l, r, v: relSentence(r, 'S', 'O', l) })))
  out.draftRelationLabel = (['ja', 'en'] as const).flatMap((l) => rels.map((r) => ({ l, r, v: draftRelationLabel(r, l) })))
  out.relationLabel = (['ja', 'en'] as const).flatMap((l) => [...PREDICATES, ...EXTRA].map((r) => ({ l, r, v: relationLabel(r, l) ?? null })))
  out.symmetricRel = rels.map((r) => ({ r, v: isSymmetricRel(r) }))
  out.relationPredicate = NAMES.flatMap((r) =>
    (['class', 'property'] as const).flatMap((s) =>
      ([undefined, 'class', 'property'] as const).map((o) => ({ r, s, o: o ?? 'undef', v: relationPredicate(r as RelKind, s, o) })),
    ),
  )
  out.isHierarchy = NAMES.map((r) => ({ r, v: isHierarchy(r as RelKind) }))
  const alignments = preds.filter((p): p is string => typeof p === 'string').map((p) => ({ relation: p, source: 'a', target: 'b', source_kind: 'dataset', target_kind: 'dataset' }) as unknown as Alignment)
  out.matchesFilter = alignments.map((a) => ({ r: a.relation, v: DECISION_FILTERS.map((f) => matchesFilter(a, f)) }))
  out.filterCounts = filterCounts(alignments)
  const t = (key: string, vars?: Record<string, unknown>) => `${key}|${JSON.stringify(vars ?? {})}`
  out.decisionSentence = alignments.map((a) => ({ r: a.relation, v: decisionSentence(a, {}, t) }))
  return out
}
