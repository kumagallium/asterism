// 関係の目録（ui 側）。組み込み（生成物 builtinCatalog.ts）に、起動後に読む `GET /api/relations` を重ねる。
// design.md §23.13・ADR docs/architecture/relation-catalog.md。
//
// - 読む前・失敗時は組み込みを返す（今は API と組み込みが同じなので、画面は変わらない）。
// - 利用者が関係を足す段（R3）で、API の側にだけある関係が画面に出る土台になる。
// - **module 直下で目録を読まない**（i18n のシングルトンと同じ罠: 読み込み順で未初期化の値を掴む）。
//   表示に使う所は、関数の中で `getCatalog()`（React の中なら `useCatalog()`）を呼ぶ。
// - 再描画: 目録が変わると `useCatalog()` を呼ぶ部品（今は HandForm・GraphPlaceSide の 2 つ）だけが引き直す。
//   `getCatalog()` を直に呼ぶ所は、目録が変わっても次の再描画まで古い結果を持つ。今は API と組み込みが同じで
//   「同じ内容なら通知しない」ので画面には出ない。API にだけある関係が来る R3 で、使う部品を広げる（ADR 参照）。
import { useSyncExternalStore } from 'react'
import { BUILTIN_CATALOG } from './builtinCatalog'
import { exerciseCatalog } from './relationFns'

type Lang = { readonly ja: string; readonly en: string }

/** 下書き側の関係 1 つ（`GET /api/relations` の `relations[]`）。 */
export interface CatalogRelation {
  readonly name: string
  readonly label: Lang
  readonly sentence: Lang
  readonly predicates: readonly { readonly name: string; readonly iri: string; readonly when: readonly [string, string] | null }[]
  readonly symmetric_draft: boolean
  readonly symmetric_pair: boolean
  readonly experiment: boolean
  readonly builtin: boolean
  /** 足した関係だけ。使うのをやめた物（新しくは選べない）。 */
  readonly retired?: boolean
}

/** 確定側の述語 1 つ（`predicates[]`）。 */
export interface CatalogPredicate {
  readonly name: string
  readonly iri: string
  readonly shapes: { readonly from: readonly string[]; readonly to: readonly string[] }
  readonly same_shape: boolean
  readonly roles: { readonly from: readonly string[]; readonly to: readonly string[] } | null
  readonly no_self: boolean
  readonly subsumption: boolean
  readonly symmetric: boolean
  readonly knowledge: boolean
  readonly own: {
    readonly label: Lang
    readonly definition: string
    readonly searched: readonly string[]
    readonly date: string
  } | null
}

export interface RelationCatalog {
  readonly relations: readonly CatalogRelation[]
  readonly predicates: readonly CatalogPredicate[]
}

/** 組み込みの目録（生成物）。 */
export const BUILTIN: RelationCatalog = BUILTIN_CATALOG

// 型（ユニオン）は生成物の定数から導く。API にだけある関係は実行時の値で、型には入らない。
type BuiltinRelation = (typeof BUILTIN_CATALOG.relations)[number]
type BuiltinPredicate = (typeof BUILTIN_CATALOG.predicates)[number]
/** 下書き側の関係名（sub・eq・…・has_property）。 */
export type RelationName = BuiltinRelation['name']
/** 確定側の述語名（subClassOf・…・hasProperty）。 */
export type PredicateName = BuiltinPredicate['name']
/** 知識の関係（人が確定する述語）。 */
export type KnowledgePredicateName = Extract<BuiltinPredicate, { readonly knowledge: true }>['name']

// ---- 重ねる ----------------------------------------------------------------------------------

/** 組み込みに API の目録を重ねる: 同じ名前は API の値・API にだけある名前は後ろに足す。並びは組み込みが先。
 *  API の返りに同名が 2 つあれば後のを捨てる（重複を作らない）。 */
export function mergeCatalog(base: RelationCatalog, remote: RelationCatalog): RelationCatalog {
  const merge = <T extends { readonly name: string }>(a: readonly T[], b: readonly T[]): T[] => {
    const over = new Map<string, T>()
    for (const x of b) if (!over.has(x.name)) over.set(x.name, x)
    const out = a.map((x) => over.get(x.name) ?? x)
    const have = new Set(a.map((x) => x.name))
    for (const x of over.values()) if (!have.has(x.name)) out.push(x)
    return out
  }
  return { relations: merge(base.relations, remote.relations), predicates: merge(base.predicates, remote.predicates) }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isStrs = (v: unknown): boolean => Array.isArray(v) && v.every((x) => typeof x === 'string')
const isLang = (v: unknown): boolean => isObj(v) && typeof v.ja === 'string' && typeof v.en === 'string'
const isEnds = (v: unknown): boolean => isObj(v) && isStrs(v.from) && isStrs(v.to)

function isRelationShape(r: unknown): boolean {
  if (!isObj(r)) return false
  return (
    typeof r.name === 'string' &&
    isLang(r.label) &&
    isLang(r.sentence) &&
    Array.isArray(r.predicates) &&
    r.predicates.every(
      (p) => isObj(p) && typeof p.name === 'string' && typeof p.iri === 'string' && (p.when === null || (Array.isArray(p.when) && p.when.length === 2 && isStrs(p.when))),
    ) &&
    typeof r.symmetric_draft === 'boolean' &&
    typeof r.symmetric_pair === 'boolean' &&
    typeof r.experiment === 'boolean'
  )
}

function isPredicateShape(p: unknown): boolean {
  if (!isObj(p)) return false
  return (
    typeof p.name === 'string' &&
    typeof p.iri === 'string' &&
    isEnds(p.shapes) &&
    typeof p.same_shape === 'boolean' &&
    (p.roles === null || isEnds(p.roles)) &&
    typeof p.no_self === 'boolean' &&
    typeof p.subsumption === 'boolean' &&
    typeof p.symmetric === 'boolean' &&
    typeof p.knowledge === 'boolean'
  )
}

/** 足した関係は、表示名・文・述語の名前が片方の言語だけのことがある（人が今の画面の言語でだけ書く）。
 *  もう片方は、あるほうで埋めて形をそろえる（ui は両方の言語の欄を読む）。形が違う物はそのまま返す（形の検査で捨てる）。 */
export function fillLangs(v: unknown): unknown {
  if (!isObj(v) || !Array.isArray(v.relations)) return v
  const fill = (x: unknown): unknown => {
    if (!isObj(x)) return x
    const ja = typeof x.ja === 'string' ? x.ja : undefined
    const en = typeof x.en === 'string' ? x.en : undefined
    if (ja === undefined && en === undefined) return x
    return { ...x, ja: ja ?? en, en: en ?? ja }
  }
  const relations = v.relations.map((r) => (isObj(r) && r.builtin === false ? { ...r, label: fill(r.label), sentence: fill(r.sentence) } : r))
  const predicates = Array.isArray(v.predicates)
    ? v.predicates.map((p) => (isObj(p) && isObj(p.own) ? { ...p, own: { ...p.own, label: fill(p.own.label) } } : p))
    : v.predicates
  return { ...v, relations, predicates }
}

/** API の返りが目録の形か（ui が読む欄の型まで見る。形が違えば捨てて組み込みのまま）。 */
export function isCatalogShape(v: unknown): v is RelationCatalog {
  if (!isObj(v)) return false
  return Array.isArray(v.relations) && v.relations.every(isRelationShape) && Array.isArray(v.predicates) && v.predicates.every(isPredicateShape)
}

// ---- store（useSyncExternalStore） ------------------------------------------------------------

let current: RelationCatalog = BUILTIN
let started = false
const listeners = new Set<() => void>()

/** いまの目録（読む前・失敗時は組み込み）。同期で使える。 */
export function getCatalog(): RelationCatalog {
  return current
}

export function subscribeCatalog(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** React の中で使う。API の目録が読めて内容が変わったときだけ再描画される。 */
export function useCatalog(): RelationCatalog {
  return useSyncExternalStore(subscribeCatalog, getCatalog, getCatalog)
}

/**
 * 起動後に `GET /api/relations` を読んで重ねる。失敗（古いサーバ・通信の失敗・形の違い・重ねた結果で関数が落ちる）は
 * 黙って組み込みのまま。サイドカーの起動が遅い場合のため、失敗したら `retryMs` 後に 1 回だけ読み直す。
 * 重ねた結果が今と同じ内容なら、store は変えない（通知しない）。
 */
export async function loadRelationCatalog(fetchImpl: typeof fetch = fetch, retryMs = 3000): Promise<void> {
  if (started) return
  started = true
  if (await tryLoad(fetchImpl)) return
  await new Promise((r) => setTimeout(r, retryMs))
  await tryLoad(fetchImpl)
}

/** 関係を足した・直した・やめた・消したあとに、目録を読み直す（読み込み済みかどうかに関わらず 1 回）。失敗は黙って今のまま。 */
export async function reloadRelationCatalog(fetchImpl: typeof fetch = fetch): Promise<void> {
  started = true
  await tryLoad(fetchImpl)
}

/** 1 回読んで重ねる。成功（重ねた or 同じ内容）なら true。 */
async function tryLoad(fetchImpl: typeof fetch): Promise<boolean> {
  try {
    const res = await fetchImpl('/api/relations')
    if (!res.ok) return false
    const body = fillLangs(await res.json())
    if (!isCatalogShape(body)) return false
    const next = mergeCatalog(BUILTIN, body)
    exerciseCatalog(next) // 安全網: 画面が呼ぶ主な関数を試し呼びし、落ちるなら捨てる
    if (JSON.stringify(next) !== JSON.stringify(current)) {
      current = next
      for (const fn of [...listeners]) fn()
    }
    return true
  } catch {
    return false
  }
}

/** テスト用: 読む前の状態に戻す。 */
export function resetCatalogForTest(): void {
  current = BUILTIN
  started = false
  listeners.clear()
}
