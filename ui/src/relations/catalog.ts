// 関係の目録（ui 側）。組み込み（生成物 builtinCatalog.ts）に、起動後に読む `GET /api/relations` を重ねる。
// design.md §23.13・ADR docs/architecture/relation-catalog.md。
//
// - 読む前・失敗時は組み込みを返す（今は API と組み込みが同じなので、画面は変わらない）。
// - 利用者が関係を足す段（R3）で、API の側にだけある関係が画面に出る土台になる。
// - **module 直下で目録を読まない**（i18n のシングルトンと同じ罠: 読み込み順で未初期化の値を掴む）。
//   表示に使う所は、関数の中で `getCatalog()`（React の中なら `useCatalog()`）を呼ぶ。
import { useSyncExternalStore } from 'react'
import { BUILTIN_CATALOG } from './builtinCatalog'

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

/** 組み込みに API の目録を重ねる: 同じ名前は API の値・API にだけある名前は後ろに足す。並びは組み込みが先。 */
export function mergeCatalog(base: RelationCatalog, remote: RelationCatalog): RelationCatalog {
  const merge = <T extends { readonly name: string }>(a: readonly T[], b: readonly T[]): T[] => {
    const over = new Map(b.map((x) => [x.name, x]))
    const out = a.map((x) => over.get(x.name) ?? x)
    const have = new Set(a.map((x) => x.name))
    for (const x of b) if (!have.has(x.name)) out.push(x)
    return out
  }
  return { relations: merge(base.relations, remote.relations), predicates: merge(base.predicates, remote.predicates) }
}

/** API の返りが目録の形か（形が違えば捨てて組み込みのまま）。 */
export function isCatalogShape(v: unknown): v is RelationCatalog {
  if (typeof v !== 'object' || v === null) return false
  const { relations, predicates } = v as { relations?: unknown; predicates?: unknown }
  const named = (a: unknown): boolean => Array.isArray(a) && a.every((x) => typeof x === 'object' && x !== null && typeof (x as { name?: unknown }).name === 'string')
  const rels = (relations as { label?: unknown; sentence?: unknown; predicates?: unknown }[]) ?? []
  return (
    named(relations) &&
    named(predicates) &&
    rels.every((r) => typeof r.label === 'object' && r.label !== null && typeof r.sentence === 'object' && r.sentence !== null && Array.isArray(r.predicates))
  )
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
 * 起動後に `GET /api/relations` を 1 回読んで重ねる。失敗（古いサーバ・通信の失敗・形の違い）は黙って組み込みのまま。
 * 重ねた結果が今と同じ内容なら、store は変えない（通知しない）。
 */
export async function loadRelationCatalog(fetchImpl: typeof fetch = fetch): Promise<void> {
  if (started) return
  started = true
  try {
    const res = await fetchImpl('/api/relations')
    if (!res.ok) return
    const body: unknown = await res.json()
    if (!isCatalogShape(body)) return
    const next = mergeCatalog(BUILTIN, body)
    if (JSON.stringify(next) === JSON.stringify(current)) return
    current = next
    for (const fn of [...listeners]) fn()
  } catch {
    // 組み込みのまま
  }
}

/** テスト用: 読む前の状態に戻す。 */
export function resetCatalogForTest(): void {
  current = BUILTIN
  started = false
  listeners.clear()
}
