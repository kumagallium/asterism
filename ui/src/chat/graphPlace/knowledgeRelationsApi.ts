// 取り込んだ知識の台帳（語の組ごとの関係）を読む・確認する口（PR 14e-1・契約 §1.2）。
// 読む: GET /api/knowledge-imports と GET /api/knowledge-copies/{source}/relations?state=…（状態ごとに読んで束ねる）。
// 書く: POST …/relations/review（確認・取り消し・違う・戻す）と …/relations/assign（候補に関係を付ける）。
// 認証は authHeaders()。共通の apiFetch は無いので chatApi.ts と同じ作法で書く。
import { authHeaders } from '../../authToken'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** 取り込み元 1 件（`GET /api/knowledge-imports`）。 */
export interface ImportSource {
  source: string
  title: string
  datasetId: string | null
  copiedAt: string | null
  running: boolean
}

export interface TermCard {
  id: string
  name: string
  kind: string | null
  role: string | null
  /** ストアに置いた語の IRI（無ければ null＝まだ台帳だけの語）。 */
  termIri: string | null
}

/** 確認済みの関係を正本に置いた結果（行の `store`）。`reason` は置けなかった理由の記号。 */
export interface StoreOutcome {
  ok: boolean
  reason?: string
}

/** 確認・付ける操作の返りの `store`（置いた数・外した数・置けなかった件・残り）。 */
export interface StoreReport {
  placed: number
  removed: number
  failed: { key: string; reason: string }[]
  remaining: number
  /** 置く段そのものが落ちた（台帳の確認は通っている）。 */
  error: boolean
}

export interface EvidenceItem {
  id: string
  title: string
  sourceUrl: string | null
}

/** 台帳の行 1 本。`state` は api の状態（relation＝関係・none＝候補・dropped＝見せない）。 */
export interface LedgerRow {
  source: string
  /** 操作の鍵（確認・「違う」・戻す）。AI の行＝組の鍵、取り込み元の行＝`o:<from>|<rel>|<to>`（行ごとに一意）。 */
  key: string | null
  /** 組の鍵（2 語の id を文字列順に `|` でつないだもの）。「関係を付ける」はこれを渡す。a＝先の語。 */
  pair: string
  /** 候補の組にまとめた取り込み元の言い分（食い違い）の文。 */
  originSentence?: string | null
  state: 'relation' | 'none' | 'dropped'
  rel: string | null
  from: TermCard
  to: TermCard
  sentence: string | null
  evidence: EvidenceItem[]
  context: { id: string; name: string }[]
  tentative: boolean
  note: string | null
  reason: string | null
  by: 'ai' | 'origin' | 'user' | null
  confirmed: boolean
  /** 人が「違う」とした（見せない・戻せる）。 */
  rejected: boolean
  /** 確認済みで結果がある件だけ。無ければ null。 */
  store: StoreOutcome | null
}

export interface LedgerSummary {
  shown: number
  terms: number
  candidates: number
}

/** 台帳 1 つ分（取り込み元 1 件の行と件数）。 */
export interface Ledger {
  source: ImportSource
  rows: LedgerRow[]
  summary: LedgerSummary
}

export class KnowledgeApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Obj = Record<string, unknown>
const isObj = (x: unknown): x is Obj => !!x && typeof x === 'object' && !Array.isArray(x)
const str = (x: unknown): string | null => (typeof x === 'string' && x !== '' ? x : null)
const num = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) ? x : 0)

async function failure(res: Response): Promise<KnowledgeApiError> {
  let detail = ''
  try {
    const b: unknown = await res.json()
    if (isObj(b) && typeof b.detail === 'string') detail = b.detail
  } catch {
    // 本文が無い・JSON でない
  }
  return new KnowledgeApiError(res.status, detail || `HTTP ${res.status}`)
}

async function get(path: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) throw await failure(res)
  return res.json()
}

async function post(path: string, body: unknown): Promise<unknown> {
  const res = await fetch(`${API_BASE}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) })
  if (!res.ok) throw await failure(res)
  return res.json()
}

const term = (x: unknown): TermCard | null => {
  if (!isObj(x) || typeof x.id !== 'string') return null
  return { id: x.id, name: str(x.name) ?? x.id, kind: str(x.kind), role: str(x.role), termIri: str(x.term_iri) }
}

const storeOutcome = (x: unknown): StoreOutcome | null => {
  if (!isObj(x) || typeof x.ok !== 'boolean') return null
  const reason = str(x.reason)
  return reason ? { ok: x.ok, reason } : { ok: x.ok }
}

/** 返りの `store` を正す。無い（古い裏側）なら null。 */
export function normalizeStoreReport(x: unknown): StoreReport | null {
  if (!isObj(x)) return null
  return {
    placed: num(x.placed),
    removed: num(x.removed),
    failed: Array.isArray(x.failed) ? x.failed.filter(isObj).map((f) => ({ key: String(f.key ?? ''), reason: String(f.reason ?? '') })) : [],
    remaining: num(x.remaining),
    error: x.error === true,
  }
}

/** 行 1 つを正す。形が壊れた行・両端の語が無い行は null。 */
export function normalizeRow(source: string, x: unknown, fallbackState: LedgerRow['state'] | null): LedgerRow | null {
  if (!isObj(x)) return null
  const from = term(x.from)
  const to = term(x.to)
  if (!from || !to) return null
  const state = x.state === 'relation' || x.state === 'none' || x.state === 'dropped' ? x.state : fallbackState
  if (!state) return null // failed など、見せない状態
  const evidence: EvidenceItem[] = Array.isArray(x.evidence)
    ? x.evidence.filter(isObj).map((e) => ({ id: String(e.id ?? ''), title: str(e.title) ?? '', sourceUrl: str(e.source_url) }))
    : []
  const context = Array.isArray(x.context) ? x.context.filter(isObj).map((c) => ({ id: String(c.id ?? ''), name: str(c.name) ?? '' })) : []
  const by = x.by === 'ai' || x.by === 'origin' || x.by === 'user' ? x.by : null
  const reason = str(x.reason)
  return {
    source,
    key: str(x.key),
    pair: str(x.pair) ?? [from.id, to.id].sort().join('|'),
    state,
    rel: str(x.rel),
    from,
    to,
    sentence: str(x.sentence),
    evidence,
    context,
    tentative: x.tentative === true,
    note: str(x.note),
    reason,
    by,
    // 今の api は confirmed に台帳の dict を入れる（無ければ null）。新しい api は bool。どちらも真偽に直す
    confirmed: !!x.confirmed,
    rejected: x.rejected === true || (state === 'dropped' && reason === 'rejected'),
    store: storeOutcome(x.store),
  }
}

export async function fetchImports(): Promise<ImportSource[]> {
  const body = await get('/api/knowledge-imports')
  if (!isObj(body) || !Array.isArray(body.sources)) throw new KnowledgeApiError(0, 'bad shape')
  return body.sources
    .filter(isObj)
    .filter((s) => typeof s.source === 'string')
    .map((s) => ({
      source: s.source as string,
      title: str(s.title) ?? (s.source as string),
      datasetId: str(s.dataset_id),
      copiedAt: str(s.copied_at),
      running: s.running === true,
    }))
}

/** 取り込み元の行の AI の判定（`ai`）→ 台帳の状態。見解が同じ（agrees）行は api が出さない。 */
const ORIGIN_STATE: Record<string, LedgerRow['state']> = { unseen: 'relation', disputes: 'none', rejected: 'dropped' }

/** 台帳の行を `state=all` の 1 回で読む（分けて読むと、書き込みと重なって行が二重・欠落する）。
 *  `relations`（AI の行）と `origin`（取り込み元の関係）を束ね、見せない状態（failed など）は捨てる。 */
export async function fetchLedger(info: ImportSource, lang: 'ja' | 'en'): Promise<Ledger> {
  const body = await get(`/api/knowledge-copies/${encodeURIComponent(info.source)}/relations?state=all&lang=${lang}`)
  if (!isObj(body)) throw new KnowledgeApiError(0, 'bad shape')
  const rows: LedgerRow[] = []
  for (const x of Array.isArray(body.relations) ? body.relations : []) {
    const r = normalizeRow(info.source, x, null)
    if (r) rows.push(r)
  }
  for (const x of Array.isArray(body.origin) ? body.origin : []) {
    if (!isObj(x)) continue
    const state = ORIGIN_STATE[String(x.ai ?? '')] ?? (x.rejected === true ? 'dropped' : null)
    const r = state ? normalizeRow(info.source, { ...x, state }, null) : null
    if (r) rows.push(r)
  }
  const sum = isObj(body.summary) ? body.summary : {}
  return { source: info, rows, summary: { shown: num(sum.shown), terms: num(sum.terms), candidates: num(sum.candidates) } }
}

export type ReviewAction = 'confirm' | 'unconfirm' | 'reject' | 'restore'
export const REVIEW_MAX = 50

export interface ReviewResult {
  changed: string[]
  skipped: { key: string; reason: string }[]
  /** 正本に置いた結果（古い裏側なら無し）。 */
  store?: StoreReport | null
}

/** 確認・確認の取り消し・違う・戻す。1 回に 1〜50 件（超える分は呼び手が分ける。`reviewInChunks`）。 */
export async function postReview(source: string, action: ReviewAction, keys: string[]): Promise<ReviewResult> {
  const body = await post(`/api/knowledge-copies/${encodeURIComponent(source)}/relations/review`, { action, keys })
  const o = isObj(body) ? body : {}
  return {
    changed: Array.isArray(o.changed) ? o.changed.map(String) : [],
    skipped: Array.isArray(o.skipped) ? o.skipped.filter(isObj).map((s) => ({ key: String(s.key ?? ''), reason: String(s.reason ?? '') })) : [],
    store: normalizeStoreReport(o.store),
  }
}

/** 分けて送った返りの `store` を足し合わせる。 */
export function mergeStore(a: StoreReport | null, b: StoreReport | null): StoreReport | null {
  if (!a) return b
  if (!b) return a
  return { placed: a.placed + b.placed, removed: a.removed + b.removed, failed: [...a.failed, ...b.failed], remaining: b.remaining, error: a.error || b.error }
}

/** 50 件を超えるときは分けて送る。1 回でも失敗したらそこで投げる（済んだ分は台帳に残る）。 */
export async function reviewInChunks(source: string, action: ReviewAction, keys: string[]): Promise<ReviewResult> {
  const out: ReviewResult = { changed: [], skipped: [], store: null }
  for (let i = 0; i < keys.length; i += REVIEW_MAX) {
    const r = await postReview(source, action, keys.slice(i, i + REVIEW_MAX))
    out.changed.push(...r.changed)
    out.skipped.push(...r.skipped)
    out.store = mergeStore(out.store ?? null, r.store ?? null)
  }
  return out
}

/** 候補に関係を付ける。`subject` は組のどちらが主語か（a＝先・b＝後）。検査に落ちたら 422（理由は message）。 */
export async function postAssign(source: string, key: string, rel: string, subject: 'a' | 'b', lang?: 'ja' | 'en'): Promise<StoreReport | null> {
  const body = await post(`/api/knowledge-copies/${encodeURIComponent(source)}/relations/assign`, { key, rel, subject, ...(lang ? { lang } : {}) })
  return normalizeStoreReport(isObj(body) ? body.store : null)
}
