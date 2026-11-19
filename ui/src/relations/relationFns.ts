// 関係の目録から導く、ui の関係についての判定・表示名・候補（純関数）。
// 以前は lineChoice / skyLayout / mapModel / sentenceRows / rowFns / themeDraft / decisionsModel に直書きされていた。
// 結果は寄せる前と同じ（relationsBaseline.test.ts が golden で固定）。
// 目録は呼ぶたびに `getCatalog()` で読む（module 直下で読まない・catalog.ts の注意）。
import { type CatalogPredicate, type CatalogRelation, getCatalog, type RelationCatalog } from './catalog'

type Lang = 'ja' | 'en'
/** 端の種別（量の種類 = quantity・見分けられない = unknown は画面側の言い方）。 */
export type EndKind = 'class' | 'property' | 'quantity' | 'unknown'

interface Index {
  relations: Map<string, CatalogRelation>
  predicates: Map<string, CatalogPredicate>
  /** 述語名 → その述語を持つ関係。 */
  owner: Map<string, CatalogRelation>
}

const cache = new WeakMap<RelationCatalog, Index>()
function index(c: RelationCatalog): Index {
  let ix = cache.get(c)
  if (!ix) {
    const owner = new Map<string, CatalogRelation>()
    for (const r of c.relations) for (const p of r.predicates) if (!owner.has(p.name)) owner.set(p.name, r)
    ix = { relations: new Map(c.relations.map((r) => [r.name, r])), predicates: new Map(c.predicates.map((p) => [p.name, p])), owner }
    cache.set(c, ix)
  }
  return ix
}

// ---- 名前 ------------------------------------------------------------------------------------

/** 下書き側の関係名（目録の並び）。 */
export const relationNames = (c: RelationCatalog = getCatalog()): string[] => c.relations.map((r) => r.name)
export const isRelationName = (v: unknown, c: RelationCatalog = getCatalog()): v is string => typeof v === 'string' && index(c).relations.has(v)
/** 確定側の述語名（目録の並び）。 */
export const predicateNames = (c: RelationCatalog = getCatalog()): string[] => c.predicates.map((p) => p.name)
/** 人が確定する「知識の関係」の述語（目録の並び）。 */
export const knowledgePredicateNames = (c: RelationCatalog = getCatalog()): string[] => c.predicates.filter((p) => p.knowledge).map((p) => p.name)
export const isKnowledgePredicate = (name: string, c: RelationCatalog = getCatalog()): boolean => index(c).predicates.get(name)?.knowledge === true

// ---- 向き ------------------------------------------------------------------------------------

/** 向きが無い述語か（確定側。競合する）。 */
export const isSymmetricPredicate = (name: string, c: RelationCatalog = getCatalog()): boolean => index(c).predicates.get(name)?.symmetric === true
/** 台帳・提案で向きが無い関係か（下書き側の名前。同じ・競合する）。 */
export const isSymmetricPairRelation = (rel: string, c: RelationCatalog = getCatalog()): boolean => index(c).relations.get(rel)?.symmetric_pair === true
/** 下書きで向きが無い関係か（下書き側の名前。競合する）。 */
export const isSymmetricDraftRelation = (rel: string, c: RelationCatalog = getCatalog()): boolean => index(c).relations.get(rel)?.symmetric_draft === true

/** 向きのある述語か（一種・量の種類・知識の関係）。同じ（≡）・競合する・目録に無い名前は向きなし。 */
export function isDirectedPredicate(name: string, c: RelationCatalog = getCatalog()): boolean {
  const ix = index(c)
  const p = ix.predicates.get(name)
  if (!p || p.symmetric) return false
  return ix.owner.get(name)?.symmetric_pair !== true
}

/** 線が段をつくる関係か（下書き側の名前。一種・一部）。 */
export const isHierarchyRelation = (rel: string, c: RelationCatalog = getCatalog()): boolean =>
  index(c).relations.get(rel)?.predicates.some((p) => index(c).predicates.get(p.name)?.subsumption === true) === true

// ---- 表示名・文 ------------------------------------------------------------------------------

/** 下書き側の関係名の短い表示名。知らない名前はそのまま。 */
export const draftRelationLabel = (rel: string, lang: Lang, c: RelationCatalog = getCatalog()): string => index(c).relations.get(rel)?.label[lang] ?? rel

/** 確定側の述語（と `isA`・`measures`）の短い表示名。引けなければ undefined。 */
export function predicateLabel(key: string, lang: Lang, c: RelationCatalog = getCatalog()): string | undefined {
  const ix = index(c)
  if (key === 'isA') return ix.relations.get('sub')?.label[lang]
  if (key === 'measures') return ix.relations.get('measures')?.label[lang]
  if (!ix.predicates.get(key)?.knowledge) return undefined
  return ix.owner.get(key)?.label[lang]
}

/** 下書きの関係 1 本を文にする。知らない関係名は undefined（呼び手が代わりの形にする）。 */
export function relationSentence(rel: string, s: string, o: string, lang: Lang, c: RelationCatalog = getCatalog()): string | undefined {
  const tpl = index(c).relations.get(rel)?.sentence[lang]
  return tpl === undefined ? undefined : tpl.replace('{s}', s).replace('{o}', o)
}

// ---- 下書きの関係 → 確定側の述語 --------------------------------------------------------------

/** 下書きの関係 → 確定側の述語名。条件つきの 2 つ（一種・同じ＝主語の種別／測る＝相手の種別）は 1 つ目の条件に当たればそれ、
 *  当たらなければ 2 つ目。目録に無い関係名はそのまま返す（今までの動き）。 */
export function predicateOfRelation(rel: string, subjectKind: string, objectKind?: string, c: RelationCatalog = getCatalog()): string {
  const rules = index(c).relations.get(rel)?.predicates
  if (!rules || rules.length === 0) return rel
  if (rules.length === 1) return rules[0].name
  const when = rules[0].when
  const value = when?.[0] === 'from_kind' ? subjectKind : objectKind
  return value === when?.[1] ? rules[0].name : rules[rules.length - 1].name
}

/** 下書きの関係 → 知識の線の述語名。知識の関係でないもの（一種・同じ・量）は undefined。`measures` だけは相手の種別で決まり、
 *  種別が分からなければ undefined（表示名は下書きの名前「測る」で出す）。星図と地図で同じ割り方を使う。 */
export function knowledgePredicateOf(rel: string, toKind: 'class' | 'property' | undefined, c: RelationCatalog = getCatalog()): string | undefined {
  const ix = index(c)
  const rules = ix.relations.get(rel)?.predicates
  if (!rules || rules.length === 0 || !rules.every((p) => ix.predicates.get(p.name)?.knowledge)) return undefined
  if (rules.length === 1) return rules[0].name
  const when = rules[0].when
  if (when?.[0] !== 'to_kind' || toKind === undefined) return undefined
  return toKind === when[1] ? rules[0].name : rules[rules.length - 1].name
}

/** 候補に付ける関係の選択肢（下書き側の名前）。量（量の種類の語を選ぶ別の口）は出さず、同じ（向きが無く、最後の逃げ道）は末尾。 */
export function assignableRelations(c: RelationCatalog = getCatalog()): string[] {
  const ix = index(c)
  const quantity = (r: CatalogRelation) => r.predicates.every((p) => ix.predicates.get(p.name)?.shapes.to.length === 0)
  const rows = c.relations.filter((r) => !quantity(r))
  const last = (r: CatalogRelation) => r.symmetric_pair && !r.experiment
  return [...rows.filter((r) => !last(r)), ...rows.filter(last)].map((r) => r.name)
}

// ---- 端の種別から引ける関係 -------------------------------------------------------------------

const classy = (k: EndKind): boolean => k === 'class' || k === 'quantity'

function shapeHas(set: readonly string[], kind: EndKind): boolean {
  return kind === 'unknown' || set.length === 0 || (kind !== 'quantity' && set.includes(kind))
}

/** 知識の関係の述語のうち、`source → target` の端の種別で引けるもの。並びは「一部・変化（下げる…）→ 種類へ → 項目へ」。
 *  量の種類の端は、同じ種別を求める述語（一部）にだけ種類として数える。見分けられない端は分かっている側に合わせる。 */
export function knowledgePredicatesFor(source: EndKind, target: EndKind, c: RelationCatalog = getCatalog()): string[] {
  const group = (p: CatalogPredicate): number => (p.roles === null ? 0 : p.shapes.to.includes('class') ? 1 : 2)
  const ok = (p: CatalogPredicate): boolean => {
    if (p.same_shape) {
      if (source === 'unknown' && target === 'unknown') return false
      if (source === 'unknown' || target === 'unknown') return true
      return classy(source) === classy(target)
    }
    return shapeHas(p.shapes.from, source) && shapeHas(p.shapes.to, target)
  }
  return c.predicates
    .filter((p) => p.knowledge && ok(p))
    .map((p, i) => ({ p, i }))
    .sort((a, b) => group(a.p) - group(b.p) || a.i - b.i)
    .map((x) => x.p.name)
}

/** 標準の関係（知識でない述語）のうち、`source → target` で引けるもの。種類どうし・項目どうし・項目 → 量の種類。 */
export function standardPredicatesFor(source: EndKind, target: EndKind, c: RelationCatalog = getCatalog()): string[] {
  const std = c.predicates.filter((p) => !p.knowledge)
  const only = (a: readonly string[], k: string) => a.length === 1 && a[0] === k
  const classFamily = std.filter((p) => only(p.shapes.from, 'class') && only(p.shapes.to, 'class')).map((p) => p.name)
  const propFamily = std.filter((p) => only(p.shapes.from, 'property') && only(p.shapes.to, 'property')).map((p) => p.name)
  const quantity = std.filter((p) => only(p.shapes.from, 'property') && p.shapes.to.length === 0).map((p) => p.name)
  if (source === 'property' && target === 'quantity') return quantity
  if (source === 'property' && target === 'unknown') return [...propFamily, ...quantity]
  if (classy(source) && classy(target)) return classFamily
  if (source === 'property' && target === 'property') return propFamily
  // 片方が見分けられないときは、分かっている方の種別に合わせる（混在の拒否は api が最後の砦）。
  if (source === 'unknown' && target === 'unknown') return [...classFamily, ...propFamily]
  if (source === 'unknown') return classy(target) ? classFamily : propFamily
  if (target === 'unknown') return classy(source) ? classFamily : propFamily
  return [] // 種類と項目の混在・項目 → 項目以外
}
