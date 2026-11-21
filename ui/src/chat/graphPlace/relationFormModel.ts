// 関係の入力欄の純関数（R3c・契約 §1）: 下書き・検査・送る形。画面の部品（RelationForm）はこれを呼ぶだけ。
import type { AddBody, AddedRelation, PatchBody } from './relationAddApi'
import type { UnstatedGroup } from './knowledgeRelationsApi'

type Lang = 'ja' | 'en'

export const LABEL_MAX = 40
export const DEFINITION_MAX = 200
/** 種類から開いたときに「例」として読むだけで並べる理由の数。 */
export const EXAMPLE_NOTES = 3

export interface RelationFormValues {
  label: string
  definition: string
  /** 向きが無い（A と B を入れ替えても同じ意味）。 */
  symmetric: boolean
  /** B は項目に限る（相手が性質・量のときだけ）。 */
  toPropertyOnly: boolean
  /** 同じ意味の標準の述語の IRI（空＝指さない）。 */
  exactMatch: string
}

export type FormProblem = 'labelEmpty' | 'labelLong' | 'definitionEmpty' | 'definitionLong' | 'exactBad'

/** 空の入力欄。種類から開いたときは種類の名前を表示名の下書きに入れる（説明は自動で入れない）。 */
export function draftFor(group: UnstatedGroup | null): RelationFormValues {
  return { label: group ? group.label.slice(0, LABEL_MAX) : '', definition: '', symmetric: false, toPropertyOnly: false, exactMatch: '' }
}

/** 足した関係を直す入力欄の初期値（向き・項目に限るは読むだけなので値に持つが送らない）。 */
export function valuesOf(r: AddedRelation, lang: Lang): RelationFormValues {
  return { label: r.label[lang] || r.label.ja || r.label.en, definition: r.definition[lang] || r.definition.ja || r.definition.en, symmetric: r.symmetric, toPropertyOnly: r.toPropertyOnly, exactMatch: r.exactMatch ?? '' }
}

/** 例として読むだけで並べる、その種類の例の組の AI の理由（重複を除いて 2〜3 件）。 */
export function exampleNotes(group: UnstatedGroup | null): string[] {
  if (!group) return []
  const seen = new Set<string>()
  const out: string[] = []
  for (const e of group.examples) {
    const n = e.note?.trim()
    if (!n || seen.has(n)) continue
    seen.add(n)
    out.push(n)
    if (out.length >= EXAMPLE_NOTES) break
  }
  return out
}

const chars = (s: string): number => [...s].length
const isHttpIri = (s: string): boolean => /^https?:\/\/[^\s<>"{}]+$/.test(s)

/** 送る前の検査（最後はサーバが検査する）。問題が無ければ空。 */
export function problemsOf(v: RelationFormValues): FormProblem[] {
  const out: FormProblem[] = []
  const label = v.label.trim()
  const def = v.definition.trim()
  if (!label) out.push('labelEmpty')
  else if (chars(label) > LABEL_MAX) out.push('labelLong')
  if (!def) out.push('definitionEmpty')
  else if (chars(def) > DEFINITION_MAX) out.push('definitionLong')
  if (v.exactMatch.trim() && !isHttpIri(v.exactMatch.trim())) out.push('exactBad')
  return out
}

/** 足す: 表示名・説明は今の画面の言語の欄だけに入れる（もう片方は送らず、サーバの既定に任せる）。 */
export function buildAdd(v: RelationFormValues, lang: Lang, unstatedKey?: string): AddBody {
  const exact = v.exactMatch.trim()
  return {
    label: { [lang]: v.label.trim() },
    definition: { [lang]: v.definition.trim() },
    symmetric: v.symmetric,
    to_property_only: v.toPropertyOnly,
    ...(exact ? { exact_match: exact } : {}),
    ...(unstatedKey ? { unstated_key: unstatedKey } : {}),
  }
}

/** 直す: 表示名・説明・同じ意味の標準の述語だけ（向き・項目に限るは送らない）。 */
export function buildPatch(v: RelationFormValues, lang: Lang): PatchBody {
  const exact = v.exactMatch.trim()
  return { label: { [lang]: v.label.trim() }, definition: { [lang]: v.definition.trim() }, exact_match: exact || null }
}
