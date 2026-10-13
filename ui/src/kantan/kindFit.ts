import { knownVocabForIri, localName } from '../vocab'
// ⑤「かたちをたしかめる」の種類の当てはめ提案（ADR upper-structure-shared-terms §2.5.2・§2.5.3）。
//
// 種類の名前（人が付けた表示名）が、既にある語・他データの種類と**完全一致**したときだけ、
// 「既にある『〇〇』の一種にしますか」と出す。決定論・表示のみ。受けても線は書かない —
// 受けた当てはめは `upper.json` に入り、線になるのは公開のときだけ（人が受けたものだけ）。
// ここは描画から切り離した純関数（候補の絞り込み・受けた当てはめと upper.json の往復）。
import type { ColumnMeaning, MappingSkeleton, SkeletonMap, UpperItem, UpperRelation } from '../api'
import { basename } from '../skeletonContainment'
import { getKindCounts } from '../galleryApi'
import { type FitCandidate, listTerms } from '../vocabApi'
import { columnKey } from './settledStore'

/** 種類に当てはめられる関係。≡ は人が選ぶ（既定は一種）。 */
export type KindRelation = Extract<UpperRelation, 'subClassOf' | 'equivalentClass'>

/** 受けた当てはめ 1 つ（種類 1 つにつき 1 つ）。 */
export interface KindFitPick {
  term: string
  relation: KindRelation
  /** 受けたときの語の名前（画面に出す。保存はしない）。 */
  label: string
}

const KIND_RELATIONS: readonly string[] = ['subClassOf', 'equivalentClass']

/** CURIE（`ds:Sample`）→ 完全な IRI。既に IRI ならそのまま。展開できなければ null。 */
export function expandCurie(curie: string, prefixes: Record<string, string>): string | null {
  if (/^https?:\/\//i.test(curie)) return curie
  const i = curie.indexOf(':')
  if (i <= 0) return null
  const base = prefixes[curie.slice(0, i)]
  return base ? base + curie.slice(i + 1) : null
}

/** この種類（map）が公開されたときの IRI。種類の名前がまだ無ければ null。 */
export function kindIriOf(map: SkeletonMap, skeleton: MappingSkeleton): string | null {
  const first = map.subject.classes?.[0]
  return first ? expandCurie(first, skeleton.prefixes ?? {}) : null
}

/** 提案のうち「種類」への当てはめだけ。`shared`（共有の語）と `dataset`（他データの
 *  種類）で、`term_kind` が class で IRI が種類のもの。標準の語・項目・この種類自身は出さない。 */
export function kindFitCandidates(
  candidates: FitCandidate[],
  classIris: ReadonlySet<string>,
  ownIri: string | null,
): FitCandidate[] {
  return candidates.filter(
    (c) =>
      (c.kind === 'shared' || c.kind === 'dataset') &&
      c.term_kind === 'class' &&
      classIris.has(c.term) &&
      c.term !== ownIri,
  )
}

/** 提案のうち「項目」への当てはめだけ（③）。種類（class）の候補は出さない — 列を
 *  種類に当てはめると意味が違ってしまう。標準の語も `term_kind` が property のものだけ。 */
export function itemFitCandidates(candidates: FitCandidate[]): FitCandidate[] {
  return candidates.filter((c) => c.term_kind === 'property')
}

/** 項目の当てはめの subject の接頭辞（`property:<map 名>/<列名>`）。公開のとき api が
 *  その map の mapping.yaml の列 → 述語で述語 IRI に解決する。 */
export const PROPERTY_REF = 'property:'

export function isPropertyRef(subject: string): boolean {
  return subject.startsWith(PROPERTY_REF)
}

/** その列を持つ map。列を明示で持たせた map（`owns`）が先、無ければそのファイルの最初の
 *  map。ファイルが合わなくても、骨格のファイルが 1 つだけで列のファイルも 1 つだけなら
 *  それ（名前の食い違いで黙って落とさない）。 */
function mapOfColumn(
  skeleton: MappingSkeleton,
  source: string,
  column: string,
  onlySource: boolean,
): SkeletonMap | undefined {
  const here = skeleton.maps.filter(
    (m) => basename(m.source ?? '') === basename(source) || onlySource,
  )
  return (
    here.find((m) => (m.owns ?? []).map(String).includes(column)) ??
    // 1 件ごとの行の種類は、その列を ID のテンプレートに持つことが多い（record/{card_no}/{food}）。
    // ファイルの先頭の種類（カード）に寄せると、公開時に「設計に見つからない」で線が書けなかった。
    here.find((m) => (m.subject?.template ?? '').includes(`{${column}}`)) ??
    here[0]
  )
}

/** ③で受けた項目の当てはめ（`ColumnMeaning.fit`）→ `upper.json` の項目。標準の語へは
 *  ≡（equivalentProperty）、共有の語・他データの項目へは一種（subPropertyOf）。「取り込まない」
 *  列・map が見つからない列は出さない。表示のみの提案を人が受けたものだけが入る。 */
export function itemFitsToUpper(
  meanings: readonly ColumnMeaning[],
  excluded: readonly string[],
  skeleton: MappingSkeleton,
): UpperItem[] {
  const meaningSources = new Set(meanings.filter((m) => m.fit).map((m) => basename(m.source)))
  const skeletonSources = new Set(skeleton.maps.map((m) => basename(m.source ?? '')))
  const onlySource = meaningSources.size === 1 && skeletonSources.size === 1
  const out: UpperItem[] = []
  const seen = new Set<string>()
  for (const m of meanings) {
    if (!m.fit || excluded.includes(columnKey(m.source, m.column))) continue
    const map = mapOfColumn(skeleton, m.source, m.column, onlySource)
    if (!map) continue
    const relation: UpperRelation = m.fit.kind === 'standard' ? 'equivalentProperty' : 'subPropertyOf'
    const subject = `${PROPERTY_REF}${map.name}/${m.column}`
    const key = `${subject}\u0000${m.fit.term}\u0000${relation}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ subject, term: m.fit.term, relation })
  }
  return out
}

/** 種類ごとの当てはめ → `upper.json` の項目。`other` は種類ではない項目（保管済みの項目の
 *  当てはめなど）— 触らずに持ち越す。種類の項目が重複しないよう、種類側を作り直す。
 *
 *  `itemFits`（{@link itemFitsToUpper}）を渡すと、項目の当てはめ（`property:` の subject）は
 *  それで置き換わる: 保管済みのうち今も受けているものはそのまま残し（公開で消費済みの印を
 *  保つ）、外したものは消える。`null`／省略 = 項目の当てはめは分からない（③の意味を読めて
 *  いない）ので、保管済みの `property:` の項目も触らずに持ち越す。 */
export function picksToUpper(
  picks: Record<string, KindFitPick>,
  skeleton: MappingSkeleton,
  other: readonly UpperItem[],
  itemFits?: readonly UpperItem[] | null,
): UpperItem[] {
  const tripleKey = (u: UpperItem) => `${u.subject}\u0000${u.term}\u0000${u.relation}`
  let carried = [...other]
  if (itemFits) {
    const wanted = new Set(itemFits.map(tripleKey))
    carried = carried.filter((u) => !isPropertyRef(u.subject) || wanted.has(tripleKey(u)))
  }
  const out: UpperItem[] = carried
  const seen = new Set(out.map(tripleKey))
  for (const map of skeleton.maps) {
    const pick = picks[map.name]
    const subject = kindIriOf(map, skeleton)
    if (!pick || !subject) continue
    const key = `${subject}\u0000${pick.term}\u0000${pick.relation}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ subject, term: pick.term, relation: pick.relation })
  }
  for (const item of itemFits ?? []) {
    if (seen.has(tripleKey(item))) continue
    seen.add(tripleKey(item))
    out.push({ subject: item.subject, term: item.term, relation: item.relation })
  }
  return out
}

/** `upper.json` の項目 → 種類ごとの当てはめ（見直しの読み戻し）と、種類ではない残り。
 *  骨格の種類（IRI が一致する map）に当たる、種類の関係の項目だけが当てはめになる。 */
export function upperToPicks(
  items: readonly UpperItem[],
  skeleton: MappingSkeleton,
  labelOf: (iri: string) => string,
): { picks: Record<string, KindFitPick>; other: UpperItem[] } {
  const byIri = new Map<string, SkeletonMap>()
  for (const map of skeleton.maps) {
    const iri = kindIriOf(map, skeleton)
    if (iri && !byIri.has(iri)) byIri.set(iri, map)
  }
  const picks: Record<string, KindFitPick> = {}
  const other: UpperItem[] = []
  for (const item of items) {
    const map = KIND_RELATIONS.includes(item.relation) ? byIri.get(item.subject) : undefined
    if (map && !picks[map.name]) {
      picks[map.name] = {
        term: item.term,
        relation: item.relation as KindRelation,
        label: labelOf(item.term),
      }
    } else {
      other.push({ subject: item.subject, term: item.term, relation: item.relation })
    }
  }
  return { picks, other }
}

/** 候補が「種類」かどうかを判定する IRI の集合。共有の語（種類）と、他データの
 *  種類（公開済みで件数を持つもの）。候補 API は項目と種類を区別しないので、ここで引く。
 *  失敗した側は空として扱う（提案は補助 — 引けなければ出さないだけ）。 */
export async function loadClassIris(): Promise<Set<string>> {
  const out = new Set<string>()
  const [terms, counts] = await Promise.all([
    listTerms().catch(() => []),
    getKindCounts().catch(() => null),
  ])
  for (const term of terms) if (term.kind === 'class') out.add(term.iri)
  for (const g of counts?.graphs ?? []) for (const k of g.kinds) out.add(k.class_iri)
  return out
}

/** 提案の 1 文。標準の語は語彙の接頭辞つき（`qudt:hasUnit`）で言う — ラベルだけだと、
 *  別の語彙の同名の語（CMSO と QUDT の has unit）が同じ文で 2 つ並んだ（実機 2026-10-09）。 */
export function fitSentence(t: (key: string, opts?: Record<string, unknown>) => string, c: FitCandidate): string {
  if (c.kind === 'standard') {
    const v = knownVocabForIri(c.term)
    const label = v ? `${v.prefix}${localName(c.term)}` : c.label
    return t('kantan:meanings.fit.sameStandard', { label })
  }
  return t('kantan:meanings.fit.same', { label: c.label })
}
