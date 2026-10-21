// ③「意味をつける」の表に出す、会話で受けた「列 ↔ 用語」の結びつきの印（契約 pr7c C1）。
// 7a の apply（`bind_column`）は `upper.json` に `property:<map>/<列>` の項目として書く。公開で線になる。
// 画面は `fetchDatasetUpper` で読み戻し、その列の行の下に「結ぶ: 粒径 ≡ 粒径（用語）」と出す。
import type { UpperItem } from '../api'
import { isPropertyRef, PROPERTY_REF } from './kindFit'

export interface ColumnBinding {
  /** 項目の参照（`property:<map>/<列>`）。同じ見出しの列が複数の map にあれば全部。「外す」で api に送る。 */
  subjects: string[]
  /** 列の見出し。 */
  column: string
  /** 当てはめ先の用語の IRI。 */
  term: string
  /** `equivalentProperty`（同じもの）／ `subPropertyOf`（の一種）。 */
  relation: 'equivalentProperty' | 'subPropertyOf'
  /** 会話で受けた（来歴がある）か。表で直に受けたものは false。 */
  fromChat: boolean
  /** 公開で線になった（`applied_at` がある）。 */
  applied: boolean
  /** 公開で線にできなかった理由（未消費で `skipped` がある）。無ければ null。 */
  skipped: string | null
}

/** 表の 1 行の「名前が一致する候補」（行の列の見出しと、いま出している用語の IRI）。 */
export interface FitFound {
  column: string
  terms: readonly string[]
}

export function sameFound(prev: FitFound | undefined, terms: readonly string[]): boolean {
  return (prev?.terms ?? []).join('\u0000') === terms.join('\u0000')
}

/** 最初のカード「名前が一致する候補 N 件」の N。（列, 用語）の組で重複を除いて数える —
 *  同じ見出しの列が複数のファイルにあっても、同じ用語への候補は 1 件。 */
export function countFitPairs(rows: readonly FitFound[]): number {
  const seen = new Set<string>()
  for (const r of rows) for (const term of r.terms) seen.add(`${r.column}\u0000${term}`)
  return seen.size
}

/** `property:<map>/<列>` → 列の見出し（形が違えば null）。map 名に `/` は入らない。 */
export function columnOfRef(subject: string): string | null {
  if (!isPropertyRef(subject)) return null
  const rest = subject.slice(PROPERTY_REF.length)
  const at = rest.indexOf('/')
  return at > 0 && at < rest.length - 1 ? rest.slice(at + 1) : null
}

/** `upper.json` の項目 → 列ごとの結びつき（項目の当てはめだけ。種類の当てはめは別の画面）。 */
export function columnBindings(items: readonly UpperItem[]): ColumnBinding[] {
  const out = new Map<string, ColumnBinding>()
  for (const it of items) {
    const column = columnOfRef(it.subject)
    if (column === null) continue
    if (it.relation !== 'equivalentProperty' && it.relation !== 'subPropertyOf') continue
    const k = `${column}\u0000${it.term}\u0000${it.relation}`
    const seen = out.get(k)
    if (seen) {
      if (!seen.subjects.includes(it.subject)) seen.subjects.push(it.subject)
      continue
    }
    const skipped = it.applied_at ? null : typeof it.skipped === 'string' && it.skipped ? it.skipped : null
    out.set(k, {
      subjects: [it.subject],
      column,
      term: it.term,
      relation: it.relation,
      fromChat: typeof (it as { drafted_by?: unknown }).drafted_by === 'string',
      applied: !!it.applied_at,
      skipped,
    })
  }
  return [...out.values()]
}

/**
 * ある行の下に出す印。その行の表の fit（FitSuggestion が「受けました」と言っているもの）と同じ語は
 * 出さない（同じことを 2 度言わない）。列の見出しが同じなら、どのファイルの行にも付く（api の subject は
 * ファイル名でなく map 名を持つので、見出しで引く）。
 */
export function bindingsForColumn(
  all: readonly ColumnBinding[],
  column: string,
  acceptedTerm?: string | null,
): ColumnBinding[] {
  return all.filter((b) => b.column === column && b.term !== (acceptedTerm ?? null))
}
