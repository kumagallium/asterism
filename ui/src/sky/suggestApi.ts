// 公開のあとの「結べそうな線」（api/src/asterism_api/sky_suggestions_routes.py・契約 pr7c B1）。
// 機械が名前の完全一致で数えた候補を、人が見て決める。AI は呼ばない。
//   GET  /api/sky/suggestions          write token（無い・読めないときは空 — 星図は止めない）
//   POST /api/sky/suggestions/apply    write token。{items, batch} → 候補にあるものだけ線（drafted_by=machine）
//   POST /api/sky/suggestions/dismiss  write token。外して次から出さない
import { throwApiError } from '../api'
import { authHeaders } from '../authToken'

export interface Suggestion {
  /** 種類（星）・項目の IRI。 */
  subject: string
  subject_label: string
  /** 用語の IRI。 */
  term: string
  term_label: string
  /** 種類は equivalentClass・項目は equivalentProperty。 */
  relation: string
  dataset_id: string
  dataset_label: string
  version: string
  at: string
}

/** apply・dismiss に送る 1 件（サーバは「いまの候補にあるか」を自分で確かめる）。 */
export type SuggestionKey = Pick<Suggestion, 'subject' | 'term' | 'relation'>

export const suggestionKey = (s: SuggestionKey): string => `${s.subject}\u0000${s.term}\u0000${s.relation}`

const isStr = (v: unknown): v is string => typeof v === 'string'

/** 返りを読める形に（形の悪い行は落とす）。 */
export function readSuggestions(raw: unknown): Suggestion[] {
  const rows = (raw as { suggestions?: unknown } | null)?.suggestions
  if (!Array.isArray(rows)) return []
  const out: Suggestion[] = []
  for (const r of rows as Record<string, unknown>[]) {
    if (!r || !isStr(r.subject) || !isStr(r.term) || !isStr(r.relation)) continue
    out.push({
      subject: r.subject,
      subject_label: isStr(r.subject_label) ? r.subject_label : r.subject,
      term: r.term,
      term_label: isStr(r.term_label) ? r.term_label : r.term,
      relation: r.relation,
      dataset_id: isStr(r.dataset_id) ? r.dataset_id : '',
      dataset_label: isStr(r.dataset_label) ? r.dataset_label : '',
      version: isStr(r.version) ? r.version : '',
      at: isStr(r.at) ? r.at : '',
    })
  }
  return out
}

/** 候補の一覧。鍵が無い・古いサーバ・読めないときは空（札を出さないだけ）。 */
export async function fetchSuggestions(): Promise<Suggestion[]> {
  try {
    const res = await fetch('/api/sky/suggestions', { headers: authHeaders() })
    if (!res.ok) return []
    return readSuggestions(await res.json())
  } catch {
    return []
  }
}

export interface ApplySuggestionsResult {
  applied: number
  dropped: { index: number; reason: string }[]
  batch: string
}

const keyOf = (s: SuggestionKey): SuggestionKey => ({ subject: s.subject, term: s.term, relation: s.relation })

/** 「N 件をまとめて確定」「1 件ずつ」。候補にあるものだけが線になる（drafted_by=machine）。 */
export async function applySuggestions(items: SuggestionKey[], batch: string): Promise<ApplySuggestionsResult> {
  const res = await fetch('/api/sky/suggestions/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ items: items.map(keyOf), batch }),
  })
  if (!res.ok) await throwApiError(res, 'suggestions')
  const j = (await res.json()) as Partial<ApplySuggestionsResult>
  return {
    applied: typeof j.applied === 'number' ? j.applied : 0,
    dropped: Array.isArray(j.dropped) ? j.dropped : [],
    batch: isStr(j.batch) ? j.batch : batch,
  }
}

/** 「違う」。却下を保存して次から出さない。返りは新しく保存した行数。 */
export async function dismissSuggestions(items: SuggestionKey[]): Promise<number> {
  const res = await fetch('/api/sky/suggestions/dismiss', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ items: items.map(keyOf) }),
  })
  if (!res.ok) await throwApiError(res, 'suggestions')
  const j = (await res.json()) as { dismissed?: unknown }
  return typeof j.dismissed === 'number' ? j.dismissed : 0
}
