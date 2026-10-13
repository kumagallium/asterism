// 「線を引く」の**選び方**と、共有のことばに掛かる線の読み方（純関数・決定論）。
// ADR upper-structure-shared-terms.md §2.2・§2.5.1 3。画面（LineForm・地図）はこれを呼ぶだけ。
//
// 関係は閉集合（api の ALIGN_RELATIONS）から、両端の kind で絞る:
//   種類どうし → equivalentClass / subClassOf
//   項目どうし → equivalentProperty / subPropertyOf
//   項目 → 量の種類 → hasQuantityKind
//   種類と項目の混在 → 無し（api は 422）
import type { Alignment } from './crosswalkApi'
import type { SharedTerm } from './vocabApi'

/** 共有のことば（sv:）の名前空間。 */
export const SV_NS = 'https://kumagallium.github.io/asterism/vocab/shared#'
/** 量の種類（QUDT）の名前空間。ここに入る語だけが hasQuantityKind の行き先になる。 */
export const QUANTITY_KIND_NS = 'http://qudt.org/vocab/quantitykind/'

/** 丸の素性: データセットの種類 / 共有のことば / 標準のことば。 */
export type EndRole = 'dataset' | 'shared' | 'standard'
/** 語の kind。quantity = 量の種類（種類の一種）、unknown = 見分けがつかない標準の語。 */
export type TermKind = 'class' | 'property' | 'quantity' | 'unknown'

/** 地図で選んだ丸 1 つ。 */
export interface PickEnd {
  /** 地図の節 id（選択の鍵）。 */
  id: string
  iri: string
  label: string
  role: EndRole
  termKind: TermKind
}

export const RELATION_ORDER = [
  'equivalentClass',
  'subClassOf',
  'equivalentProperty',
  'subPropertyOf',
  'hasQuantityKind',
] as const
export type AlignRelation = (typeof RELATION_ORDER)[number]

/** 標準の語の kind を IRI から推す（量の種類だけ分かる。他は呼び手が知っていれば渡す）。 */
export function standardTermKind(iri: string, known?: 'class' | 'property'): TermKind {
  if (iri.startsWith(QUANTITY_KIND_NS)) return 'quantity'
  return known ?? 'unknown'
}

/** 関係（短い名前でも IRI でも CURIE でも）の最後の名前。 */
export function relationKey(relation: string | null | undefined): string {
  const r = relation ?? ''
  return r.split(/[#/:]/).filter(Boolean).pop() ?? r
}

/** 向きのある関係か（⊂ と量の種類）。それ以外（≡・未知の対応）は両向きとして描く。 */
export function isDirectedRelation(relation: string | null | undefined): boolean {
  const k = relationKey(relation)
  return k === 'subClassOf' || k === 'subPropertyOf' || k === 'hasQuantityKind'
}

/** 関係の記号（title・一覧用）。 */
export function relationSymbol(relation: string | null | undefined): string {
  const k = relationKey(relation)
  if (k === 'subClassOf' || k === 'subPropertyOf') return '⊂'
  if (k === 'hasQuantityKind') return '→'
  return '≡'
}

const isClassy = (k: TermKind) => k === 'class' || k === 'quantity'

/** `source → target` の向きで引ける関係（閉集合から kind で絞る）。空なら引けない。 */
export function relationsFor(source: TermKind, target: TermKind): AlignRelation[] {
  if (source === 'property' && target === 'quantity') return ['hasQuantityKind']
  if (source === 'property' && target === 'unknown')
    return ['equivalentProperty', 'subPropertyOf', 'hasQuantityKind']
  const classFamily: AlignRelation[] = ['equivalentClass', 'subClassOf']
  const propFamily: AlignRelation[] = ['equivalentProperty', 'subPropertyOf']
  if (isClassy(source) && isClassy(target)) return classFamily
  if (source === 'property' && target === 'property') return propFamily
  // 片方が見分けられないときは、分かっている方の kind に合わせる（混在の拒否は api が最後の砦）。
  if (source === 'unknown' && target === 'unknown') return [...classFamily, ...propFamily]
  if (source === 'unknown') return isClassy(target) ? classFamily : propFamily
  if (target === 'unknown') return isClassy(source) ? classFamily : propFamily
  return [] // 種類と項目の混在・項目 → 項目以外
}

const ROLE_RANK: Record<EndRole, number> = { dataset: 0, shared: 1, standard: 2 }

/** 既定の向き: 狭い方 → 広い方（データセット → 共有 → 標準）。項目 → 量の種類は固定。
 *  同じ素性どうしは選んだ順。 */
export function orientPicks(a: PickEnd, b: PickEnd): { source: PickEnd; target: PickEnd } {
  if (a.termKind === 'property' && b.termKind === 'quantity') return { source: a, target: b }
  if (b.termKind === 'property' && a.termKind === 'quantity') return { source: b, target: a }
  return ROLE_RANK[b.role] < ROLE_RANK[a.role] ? { source: b, target: a } : { source: a, target: b }
}

export interface LineChoice {
  source: PickEnd
  target: PickEnd
  /** この向きで引ける関係（空 = 引けない）。 */
  relations: AlignRelation[]
  /** 向きを入れ替えても引けるか。 */
  canSwap: boolean
}

/** 2 つ選んだときの、向きと関係の候補。 */
export function lineChoice(a: PickEnd, b: PickEnd): LineChoice {
  const { source, target } = orientPicks(a, b)
  const relations = relationsFor(source.termKind, target.termKind)
  return {
    source,
    target,
    relations,
    canSwap: relationsFor(target.termKind, source.termKind).length > 0,
  }
}

// ── 共有のことばに掛かる線 ──

/** 共有のことばを片端に持つ 1 本の線（向きは source → target）。 */
export interface SharedLine {
  from: string
  to: string
  /** 関係（短い名前）。分からないもの（narrower の旧来の吊るし）は subClassOf/subPropertyOf とみなす。 */
  relation: string
}

/**
 * 共有のことばに掛かる線の一覧。alignments（関係つき）を正とし、語の narrower / standards
 * （GET /api/vocab/shared が返す、その語へ結ばれた側・標準語）のうち alignments に無い対を足す。
 * 入力順を保つ。同じ（from, to, 関係の種別）は 1 本。
 */
export function sharedLines(terms: SharedTerm[], alignments: Alignment[]): SharedLine[] {
  const shared = new Map(terms.map((t) => [t.iri, t]))
  const out: SharedLine[] = []
  const seen = new Set<string>()
  const pairs = new Set<string>()
  const push = (from: string, to: string, relation: string) => {
    const key = `${from}\u0000${to}\u0000${relationKey(relation)}`
    if (seen.has(key)) return
    seen.add(key)
    out.push({ from, to, relation: relationKey(relation) })
  }
  for (const a of alignments) {
    if (!shared.has(a.source) && !shared.has(a.target)) continue
    pairs.add([a.source, a.target].sort().join('\u0000'))
    push(a.source, a.target, a.relation)
  }
  for (const t of terms) {
    const sub = t.kind === 'property' ? 'subPropertyOf' : 'subClassOf'
    for (const n of t.narrower ?? []) {
      if (!pairs.has([n.iri, t.iri].sort().join('\u0000'))) push(n.iri, t.iri, sub)
    }
    for (const s of t.standards ?? []) {
      if (!pairs.has([t.iri, s.iri].sort().join('\u0000'))) push(t.iri, s.iri, s.relation || 'equivalentClass')
    }
  }
  return out
}

/** 失敗の HTTP status（`.status` を持つ例外か、`align` の文言 "(HTTP 409)" から）。取れなければ null。 */
export function httpStatusOf(err: unknown): number | null {
  const e = err as { status?: unknown; message?: unknown } | null
  if (typeof e?.status === 'number') return e.status
  const m = typeof e?.message === 'string' ? e.message.match(/HTTP (\d{3})/) : null
  return m ? Number(m[1]) : null
}
