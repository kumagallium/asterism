// 「ことば」（共有のことば＝上位構造）の api クライアント
// （ADR upper-structure-shared-terms.md §2.1・§2.5・契約 handoff §5/§6）。
//
// crosswalkApi.ts と同じ流儀: 同じ /api プロキシ、書き込みだけ authHeaders()、エラー文は
// i18n（ここは vocab 名前空間の error.*）。LLM は呼ばない（決定論）。
// 失敗は VocabApiError（HTTP status 付き）で投げる — 画面が 409（同 slug・線が残る）/
// 422（問いが空・形が不正）を言い分けられるように。
import { authHeaders } from './authToken'
import i18n from './i18n'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** 語の種類: 種類（class）か項目（property）。 */
export type SharedTermKind = 'class' | 'property'

/** 共有語の「下に掛かる」もの（この語へ結ばれた側）。 */
export interface SharedTermNarrower {
  iri: string
  kind: 'dataset' | 'perspective'
  dataset_id: string | null
  label: string
}

/** 標準語との線（relation は閉じた集合の 1 つ）。 */
export interface SharedTermStandard {
  iri: string
  relation: string
}

/** 問い（CQ）。語が答えられるものの名札で、答えるデータセットの数つき。 */
export interface SharedTermCq {
  tool_name: string
  title: string
  answering_datasets: number
}

/** 鋳造済みの共有語 1 つ（GET /api/vocab/shared の要素）。 */
export interface SharedTerm {
  iri: string
  slug: string
  kind: SharedTermKind
  label: string
  label_en: string | null
  comment: string | null
  created_at: string
  /** まだ線が 1 本も無い（＝誰も答えない）語は false。 */
  wired: boolean
  answering_datasets: number
  narrower: SharedTermNarrower[]
  standards: SharedTermStandard[]
  cqs: SharedTermCq[]
}

/** 問いのテンプレ。`op` は閉じた選択（種類＝count・項目＝values）。 */
export type CqOp = 'count' | 'values'

/** 作る問い 1 つ（鋳造時・追加時に送る）。 */
export interface CqInput {
  title: string
  op: CqOp
}

/** 標準の語はあったが使わなかった、という来歴（語の意味は変えない）。 */
export interface DeclinedStandard {
  iri: string
  reason: string
}

/** 鋳造の入力。問い（cqs）は 1 本以上が要る（空は 422）。 */
export interface MintTermInput {
  slug: string
  kind: SharedTermKind
  label_ja: string
  label_en?: string
  comment?: string
  declined_standard?: DeclinedStandard
  cqs: CqInput[]
}

/** 列を当てはめられる語の候補（完全一致のみ・表示専用）。 */
export interface FitCandidate {
  /** 候補の語の IRI。 */
  term: string
  kind: 'standard' | 'shared' | 'dataset'
  label: string
  matched_by: 'label' | 'column'
}

/** GET /api/vocab/upper — 各 IRI を、線だけから決めた最上位の共有語へ畳む対応表。 */
export interface UpperMap {
  classes: Record<string, string>
  properties: Record<string, string>
  at: string
}

/** HTTP status を持つエラー。 `status` で 409 / 422 などを言い分ける。 */
export class VocabApiError extends Error {
  status: number
  /** サーバの detail そのまま（文言の組み立て用。無ければ空）。 */
  detail: string
  constructor(message: string, status: number, detail: string) {
    super(message)
    this.name = 'VocabApiError'
    this.status = status
    this.detail = detail
  }
}

async function asError(res: Response, op: string): Promise<VocabApiError> {
  const text = await res.text().catch(() => '')
  let detail = text
  try {
    const j = JSON.parse(text) as { detail?: unknown }
    if (j && typeof j.detail === 'string') detail = j.detail
  } catch {
    /* JSON ではない — 生の文字列のまま */
  }
  return new VocabApiError(
    i18n.t('vocab:error.failed', {
      op,
      status: res.status,
      detail: detail ? `: ${detail}` : '',
    }),
    res.status,
    detail,
  )
}

/** 鋳造済みの共有語の一覧。 */
export async function listTerms(): Promise<SharedTerm[]> {
  const res = await fetch(`${API_BASE}/api/vocab/shared`)
  if (!res.ok) throw await asError(res, i18n.t('vocab:error.ops.list'))
  return ((await res.json()) as { terms?: SharedTerm[] }).terms ?? []
}

/** 語を 1 つ作る（問いを添えて 1 操作）。409＝同じ slug・422＝問いが空／形が不正。 */
export async function mintTerm(input: MintTermInput): Promise<SharedTerm> {
  const res = await fetch(`${API_BASE}/api/vocab/shared`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw await asError(res, i18n.t('vocab:error.ops.mint'))
  return ((await res.json()) as { term: SharedTerm }).term
}

/** 語を外す。409＝線が残っている（先に線を外す）。 */
export async function removeTerm(slug: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/vocab/shared/${encodeURIComponent(slug)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await asError(res, i18n.t('vocab:error.ops.remove'))
}

/** 既存の語に問いを 1 本足す。返り値は足した問いの名前と題。 */
export async function addCq(
  slug: string,
  cq: CqInput,
): Promise<{ term: string; cq: { tool_name: string; title: string } }> {
  const res = await fetch(`${API_BASE}/api/vocab/shared/${encodeURIComponent(slug)}/cq`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(cq),
  })
  if (!res.ok) throw await asError(res, i18n.t('vocab:error.ops.addCq'))
  return (await res.json()) as { term: string; cq: { tool_name: string; title: string } }
}

/** 上位の対応表（全体グラフが読む）。 */
export async function getUpper(): Promise<UpperMap> {
  const res = await fetch(`${API_BASE}/api/vocab/upper`)
  if (!res.ok) throw await asError(res, i18n.t('vocab:error.ops.upper'))
  return (await res.json()) as UpperMap
}

/** この列を当てはめられる語の候補（完全一致のみ）。label か column のどちらかは要る。 */
export async function fit(label: string, column = ''): Promise<FitCandidate[]> {
  const qs = new URLSearchParams({ label, column })
  const res = await fetch(`${API_BASE}/api/vocab/fit?${qs.toString()}`)
  if (!res.ok) throw await asError(res, i18n.t('vocab:error.ops.fit'))
  return ((await res.json()) as { candidates?: FitCandidate[] }).candidates ?? []
}
