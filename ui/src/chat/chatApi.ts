// 話（チャット）の api クライアント（api/src/asterism_api/chat_routes.py・契約 pr11a-contract.md §A〜§F）。
//
// 書き込みの鍵は authHeaders()、AI の座標は llmHeaders()（11b が使う。この PR のサーバは AI を呼ばない）。
// 失敗は themes と同じ ThemesApiError（HTTP status 付き）。受け取りは形を検査し、壊れていれば 502。
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import { type LlmCredentials, llmHeaders } from '../settings/store'
import { EMPTY_SKY, type DraftSky, type SkyData, type SkyDataset } from '../sky/skyApi'
import { ThemesApiError } from '../themes/themesApi'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// ── 形（契約 §A） ───────────────────────────────────────────────

/** 返事の注記 1 件。`kind` は閉集合だが、11c で増えるので文字列のまま受ける。 */
export type NoteKind = 'ai_unset' | 'ai_failed' | 'dropped' | 'no_question' | 'commit_error' | 'info'

export interface NoteChoice {
  label: string
  options: { value: string; label: string }[]
  default: string
  /** 人が決めた値（決めたあと）。 */
  chosen?: string
}

export interface Note {
  kind: NoteKind | string
  text: string
  choice: NoteChoice | null
}

/** 図への差分 1 件（`add_concept` / `add_relation` …）。形はサーバの検査が正で、UI は読まない。 */
export type GraphOp = { op: string } & Record<string, unknown>

export interface Reply {
  text: string
  ops: GraphOp[]
  sentences: string[]
  /** 次に聞けること（チップ）。11b から。 */
  questions: string[]
  /** 11c: 列と用語の完全一致。 */
  bindings: Binding[]
  /** 機械の手番（公開後の名前の一致）。 */
  suggestions: Suggestion[]
  notes: Note[]
  /** Ask の答え（`state: none`・出典つき）。 */
  answer: ReplyAnswer | null
  /** 「見せて」の表。 */
  table: ReplyTable | null
}

/** 表のセル。`iri` があれば図の点（押すと濃く）。 */
export interface TableCell {
  text: string
  iri?: string
}

/** 「見せて」の表（`reply.table`）。 */
export interface ReplyTable {
  kind: { iri: string; label: string }
  columns: { iri: string; label: string }[]
  rows: { iri: string; label: string; cells: Record<string, TableCell> }[]
  total: number
  truncated: boolean
}

/** Ask の答え（`reply.answer`）。 */
export interface ReplyAnswer {
  text: string
  citations: { iri: string; label: string; dataset_id: string; dataset_label: string }[]
  notes: string[]
  warnings: string[]
  verified_tools: string[]
  /** 検証していない問い合わせで答えた。 */
  unverified_sparql: boolean
  /** false なら答えが出なかった（無ければ true）。 */
  answered: boolean
}

/** 列と用語の一致 1 件。`decided` は既定（結ぶ／結ばない）で、✓ のときに人の選びを送る。 */
export type Decided = 'bind' | 'skip'
export interface Binding {
  column: string
  label: string
  term: string
  term_label: string
  relation: string
  /** 同名の列を見分ける主語（無ければ空）。 */
  subject: string
  decided: Decided
}
export interface Suggestion {
  subject: string
  term: string
  relation: string
}
export interface Attachment {
  dataset_id: string
  name: string
  rows: number
  kinds: { iri: string; label: string; count: number }[]
  id_column: string
}

export type TurnState = 'none' | 'running' | 'proposed' | 'confirmed' | 'reverted'

export interface CommitRecord {
  theme_id: string
  minted: string[]
  lines: number
  questions: string[]
  batch: string
  at: string
  /** ✓ で外した件数（無ければ 0 件）。 */
  removed?: { lines: number; terms: number }
  /** 外さずに残したもの（slug と理由）。 */
  kept?: { slug: string; reason: string }[]
}

export interface Turn {
  id: string
  at: string
  user: { text: string; attachments: { name: string; size: number }[] } | null
  /** 添えた表（取り込み済み）。 */
  attachment: Attachment | null
  /** 走っているジョブ（`state: running` のとき）。 */
  job_id: string | null
  reply: Reply
  batch: string
  state: TurnState
  before: unknown | null
  commit: CommitRecord | null
  by: 'ai' | 'human' | 'machine'
}

/** 手番のあとで変わる話の欄。 */
export interface SessionBrief {
  id: string
  title: string
  theme_id: string | null
  draft_id: string | null
  updated_at: string
}

export interface Session extends SessionBrief {
  created_at: string
  turns: Turn[]
}

/** 一覧の 1 行。`turns` は手番の数、`proposed` は未確認の返事の数。 */
export interface SessionSummary extends SessionBrief {
  created_at: string
  turns: number
  proposed: number
}

export type SkyDraft = DraftSky & { session_id?: string }

export interface TurnResponse {
  turn: Turn
  session: SessionBrief
  draft: SkyDraft | null
  job_id?: string | null
}

export interface AttachResponse {
  turn: Turn
  session: SessionBrief
  /** 無いとき（`unsupported` の手番）は購読しない。 */
  job_id: string | null
}

export interface ConfirmResponse {
  turn: Turn
  session: SessionBrief
  commit?: CommitRecord
  confirmed?: string[]
  /** 公開のあとの機械の手番（名前の一致）。 */
  followup?: Turn | null
}

/** ✓ で送る、結ぶ／結ばないの選び。 */
export interface BindingDecision {
  column: string
  subject?: string
  decided: Decided
}

/** 確認済みの手番を戻した結果（`undone`）。 */
export interface Undone {
  lines: number
  terms: number
  questions: number
  kept: { slug: string; reason: string }[]
  failed?: { what: string; reason: string }[]
}

export interface RevertResponse {
  turn: Turn
  session: SessionBrief
  draft: SkyDraft | null
  reverted: string[]
  /** 確認済みの手番を戻したときだけ。 */
  undone?: Undone
}

/** 戻す途中で失敗した（409）。何が戻せなかったかを `undone.failed` に持つ。 */
export class RevertFailedError extends ThemesApiError {
  undone: Undone
  constructor(message: string, detail: string, undone: Undone) {
    super(message, 409, detail)
    this.undone = undone
  }
}

/** 鍵が無い（401/503）。話を保存できない。 */
export const isKeyMissing = (e: unknown): boolean => e instanceof ThemesApiError && (e.status === 401 || e.status === 503)

// ── 検査 ────────────────────────────────────────────────────────

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d)
const strOrNull = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
const STATES: readonly TurnState[] = ['none', 'running', 'proposed', 'confirmed', 'reverted']

function badShape(): ThemesApiError {
  return new ThemesApiError(i18n.t('chat:error.badShape'), 502, '')
}

function note(v: unknown): Note | null {
  if (!isObj(v) || typeof v.kind !== 'string') return null
  let choice: NoteChoice | null = null
  if (isObj(v.choice) && Array.isArray(v.choice.options)) {
    choice = {
      label: str(v.choice.label),
      default: str(v.choice.default),
      ...(typeof v.choice.chosen === 'string' ? { chosen: v.choice.chosen } : {}),
      options: v.choice.options
        .filter(isObj)
        .map((o) => ({ value: str(o.value), label: str(o.label) })),
    }
  }
  return { kind: v.kind, text: str(v.text), choice }
}

function binding(o: Record<string, unknown>): Binding {
  return {
    column: str(o.column),
    label: str(o.label),
    term: str(o.term),
    term_label: str(o.term_label),
    relation: str(o.relation, 'eq'),
    subject: str(o.subject),
    decided: o.decided === 'skip' ? 'skip' : 'bind',
  }
}

function attachmentOf(v: unknown): Attachment | null {
  if (!isObj(v)) return null
  return {
    dataset_id: str(v.dataset_id),
    name: str(v.name),
    rows: typeof v.rows === 'number' ? v.rows : 0,
    kinds: Array.isArray(v.kinds)
      ? v.kinds.filter(isObj).map((k) => ({ iri: str(k.iri), label: str(k.label), count: typeof k.count === 'number' ? k.count : 0 }))
      : [],
    id_column: str(v.id_column),
  }
}

function reply(v: unknown): Reply | null {
  if (!isObj(v)) return null
  return {
    text: str(v.text),
    ops: Array.isArray(v.ops) ? (v.ops.filter((o) => isObj(o) && typeof o.op === 'string') as GraphOp[]) : [],
    sentences: strs(v.sentences),
    questions: strs(v.questions),
    bindings: Array.isArray(v.bindings) ? v.bindings.filter(isObj).map(binding).filter((b) => b.column !== '') : [],
    suggestions: Array.isArray(v.suggestions)
      ? v.suggestions.filter(isObj).map((x) => ({ subject: str(x.subject), term: str(x.term), relation: str(x.relation) }))
      : [],
    notes: Array.isArray(v.notes) ? v.notes.map(note).filter((n): n is Note => n !== null) : [],
    answer: answerOf(v.answer),
    table: tableOf(v.table),
  }
}

/** 壊れた表は無いものとして扱う（描かない）。 */
export function tableOf(v: unknown): ReplyTable | null {
  if (!isObj(v) || !isObj(v.kind) || !Array.isArray(v.columns) || !Array.isArray(v.rows)) return null
  const columns = v.columns.filter(isObj).filter((c) => typeof c.iri === 'string').map((c) => ({ iri: str(c.iri), label: str(c.label, str(c.iri)) }))
  const rows = v.rows
    .filter(isObj)
    .filter((r) => typeof r.iri === 'string')
    .map((r) => {
      const cells: Record<string, TableCell> = {}
      if (isObj(r.cells))
        for (const [k, x] of Object.entries(r.cells)) {
          if (typeof x === 'string') cells[k] = { text: x }
          else if (isObj(x)) cells[k] = typeof x.iri === 'string' && x.iri ? { text: str(x.text), iri: x.iri } : { text: str(x.text) }
        }
      return { iri: str(r.iri), label: str(r.label, str(r.iri)), cells }
    })
  const total = typeof v.total === 'number' ? v.total : rows.length
  return { kind: { iri: str(v.kind.iri), label: str(v.kind.label) }, columns, rows, total, truncated: v.truncated === true }
}

/** 壊れた答えは無いものとして扱う（描かない）。 */
export function answerOf(v: unknown): ReplyAnswer | null {
  if (!isObj(v) || typeof v.text !== 'string') return null
  const lines = (x: unknown): string[] =>
    Array.isArray(x) ? x.map((m) => (typeof m === 'string' ? m : isObj(m) ? str(m.message, str(m.text)) : '')).filter((m) => m !== '') : []
  return {
    text: v.text,
    citations: Array.isArray(v.citations)
      ? v.citations
          .filter(isObj)
          .filter((c) => typeof c.iri === 'string')
          .map((c) => ({ iri: str(c.iri), label: str(c.label), dataset_id: str(c.dataset_id), dataset_label: str(c.dataset_label) }))
      : [],
    notes: lines(v.notes),
    warnings: lines(v.warnings),
    verified_tools: strs(v.verified_tools),
    unverified_sparql: v.unverified_sparql === true,
    answered: v.answered !== false,
  }
}

function commitOf(c: unknown): CommitRecord | null {
  if (!isObj(c)) return null
  return {
    theme_id: str(c.theme_id),
    minted: strs(c.minted),
    lines: typeof c.lines === 'number' ? c.lines : 0,
    questions: strs(c.questions),
    batch: str(c.batch),
    at: str(c.at),
    removed: removedOf(c.removed),
    kept: Array.isArray(c.kept)
      ? c.kept.filter(isObj).map((k) => ({ slug: str(k.slug), reason: str(k.reason) })).filter((k) => k.slug !== '')
      : [],
  }
}

/** 件数か配列のどちらで来ても件数にする。 */
function removedOf(v: unknown): { lines: number; terms: number } {
  const n = (x: unknown): number => (typeof x === 'number' ? x : Array.isArray(x) ? x.length : 0)
  return isObj(v) ? { lines: n(v.lines), terms: n(v.terms) } : { lines: 0, terms: 0 }
}

export function normalizeTurn(v: unknown): Turn | null {
  if (!isObj(v) || typeof v.id !== 'string') return null
  const r = reply(v.reply)
  if (!r) return null
  const state = STATES.includes(v.state as TurnState) ? (v.state as TurnState) : null
  if (!state) return null
  const user = isObj(v.user)
    ? {
        text: str(v.user.text),
        attachments: Array.isArray(v.user.attachments)
          ? v.user.attachments.filter(isObj).map((a) => ({ name: str(a.name), size: typeof a.size === 'number' ? a.size : 0 }))
          : [],
      }
    : null
  const by = v.by === 'ai' || v.by === 'machine' ? v.by : 'human'
  return {
    id: v.id,
    at: str(v.at),
    user,
    reply: r,
    batch: str(v.batch),
    state,
    before: v.before ?? null,
    commit: commitOf(v.commit),
    by,
    attachment: attachmentOf(v.attachment),
    job_id: strOrNull(v.job_id),
  }
}

function brief(v: unknown): SessionBrief | null {
  if (!isObj(v) || typeof v.id !== 'string') return null
  return {
    id: v.id,
    title: str(v.title),
    theme_id: strOrNull(v.theme_id),
    draft_id: strOrNull(v.draft_id),
    updated_at: str(v.updated_at),
  }
}

export function normalizeSession(v: unknown): Session | null {
  const b = brief(v)
  if (!b || !isObj(v) || !Array.isArray(v.turns)) return null
  const turns: Turn[] = []
  for (const t of v.turns) {
    const n = normalizeTurn(t)
    if (!n) return null
    turns.push(n)
  }
  return { ...b, created_at: str(v.created_at), turns }
}

function summary(v: unknown): SessionSummary | null {
  const b = brief(v)
  if (!b || !isObj(v)) return null
  const turns = Array.isArray(v.turns) ? v.turns.length : typeof v.turns === 'number' ? v.turns : 0
  return { ...b, created_at: str(v.created_at), turns, proposed: typeof v.proposed === 'number' ? v.proposed : 0 }
}

function removalsOf(v: unknown): DraftSky['removals'] {
  if (!isObj(v)) return { terms: [], lines: [] }
  const lines = Array.isArray(v.lines)
    ? v.lines.filter(isObj).map((l) => ({ from: str(l.from), to: str(l.to), relation: str(l.relation) })).filter((l) => l.from && l.to)
    : []
  return { terms: strs(v.terms), lines }
}

function draftSky(v: unknown): SkyDraft | null {
  if (!isObj(v) || typeof v.draft_id !== 'string' || !Array.isArray(v.concepts)) return null
  return {
    ...(v as unknown as DraftSky),
    relations: Array.isArray(v.relations) ? (v.relations as DraftSky['relations']) : [],
    questions: strs(v.questions),
    removals: removalsOf(v.removals),
    session_id: typeof v.session_id === 'string' ? v.session_id : undefined,
  }
}

// ── 通信 ────────────────────────────────────────────────────────

async function asError(res: Response, op: string): Promise<ThemesApiError> {
  const text = await res.text().catch(() => '')
  let detail = text
  try {
    const j = JSON.parse(text) as { detail?: unknown }
    if (j && typeof j.detail === 'string') detail = j.detail
  } catch {
    /* JSON ではない — 生の文字列のまま */
  }
  return new ThemesApiError(
    i18n.t('themes:error.failed', { op, status: res.status, detail: detail ? `: ${detail}` : '' }),
    res.status,
    detail,
  )
}

async function call(path: string, init: RequestInit, op: string): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) throw await asError(res, i18n.t(`chat:error.ops.${op}`))
  return res
}

const json = (extra: Record<string, string> = {}): Record<string, string> => ({
  'Content-Type': 'application/json',
  ...authHeaders(),
  ...extra,
})

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await call('/api/chat/sessions', { headers: authHeaders() }, 'list')
  const body: unknown = await res.json()
  if (!isObj(body) || !Array.isArray(body.sessions)) throw badShape()
  const out: SessionSummary[] = []
  for (const s of body.sessions) {
    const n = summary(s)
    if (!n) throw badShape()
    out.push(n)
  }
  return out
}

export async function createSession(title = ''): Promise<Session> {
  const res = await call('/api/chat/sessions', { method: 'POST', headers: json(), body: JSON.stringify({ title }) }, 'create')
  const s = normalizeSession(await res.json())
  if (!s) throw badShape()
  return s
}

export async function getSession(id: string): Promise<Session> {
  const res = await call(`/api/chat/sessions/${encodeURIComponent(id)}`, { headers: authHeaders() }, 'read')
  const s = normalizeSession(await res.json())
  if (!s) throw badShape()
  return s
}

export async function renameSession(id: string, title: string): Promise<void> {
  await call(
    `/api/chat/sessions/${encodeURIComponent(id)}`,
    { method: 'PATCH', headers: json(), body: JSON.stringify({ title }) },
    'rename',
  )
}

export async function deleteSession(id: string): Promise<void> {
  await call(`/api/chat/sessions/${encodeURIComponent(id)}`, { method: 'DELETE', headers: authHeaders() }, 'delete')
}

/** 既にある問いの答え方を人が決める（`set_answer`）。欄は下書きの概念の key。 */
export interface SetAnswerBody {
  question: string
  mode: 'knowledge' | 'data'
  op: string
  target?: string
  direction?: string
  kind?: string
  property?: string
  value?: string
  match?: string
  property2?: string
  kind2?: string
}

export interface TurnInput {
  text?: string
  ops?: GraphOp[]
  set_answer?: SetAnswerBody
  /** 返事の中の小さな選択（ラジオ 1 組）。 */
  choice?: { turn_id: string; note_index: number; value: string }
  /** 確認済みの点（用語か線）を外す提案。 */
  remove?: { iri: string } | { from: string; relation: string; to: string }
  lang?: 'ja' | 'en'
}

/** 手番を送る。AI の座標は今から付ける（11b が使う）。 */
export async function postTurn(id: string, input: TurnInput, creds: LlmCredentials | null): Promise<TurnResponse> {
  const res = await call(
    `/api/chat/sessions/${encodeURIComponent(id)}/turns`,
    { method: 'POST', headers: json(llmHeaders(creds)), body: JSON.stringify(input) },
    'turn',
  )
  const body: unknown = await res.json()
  const turn = isObj(body) ? normalizeTurn(body.turn) : null
  const session = isObj(body) ? brief(body.session) : null
  if (!turn || !session || !isObj(body)) throw badShape()
  return { turn, session, draft: draftSky(body.draft), job_id: strOrNull(body.job_id) }
}

/** 表を添える（multipart）。201 で手番（running）とジョブの id が返る。AI 未設定は 503。 */
export async function postAttach(id: string, files: File[], lang: 'ja' | 'en', creds: LlmCredentials | null): Promise<AttachResponse> {
  const form = new FormData()
  for (const f of files) form.append('files', f, f.name)
  form.append('lang', lang)
  const res = await call(
    `/api/chat/sessions/${encodeURIComponent(id)}/attach`,
    { method: 'POST', headers: { ...authHeaders(), ...llmHeaders(creds) }, body: form },
    'attach',
  )
  const body: unknown = await res.json()
  const turn = isObj(body) ? normalizeTurn(body.turn) : null
  const session = isObj(body) ? brief(body.session) : null
  if (!turn || !session || !isObj(body)) throw badShape()
  return { turn, session, job_id: strOrNull(body.job_id) }
}

/** AI が設定されていない（attach の 503）。鍵が無い 503 とは `detail` で見分ける。 */
export const isAiUnset = (e: unknown): boolean => e instanceof ThemesApiError && e.status === 503 && e.detail.includes('AI')

export async function confirmTurn(
  id: string,
  turnId: string,
  lang: 'ja' | 'en',
  bindings?: BindingDecision[],
  creds: LlmCredentials | null = null,
): Promise<ConfirmResponse> {
  const res = await call(
    `/api/chat/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/confirm`,
    { method: 'POST', headers: json(llmHeaders(creds)), body: JSON.stringify(bindings ? { lang, bindings } : { lang }) },
    'confirm',
  )
  const body: unknown = await res.json()
  const turn = isObj(body) ? normalizeTurn(body.turn) : null
  const session = isObj(body) ? brief(body.session) : null
  if (!turn || !session || !isObj(body)) throw badShape()
  return {
    turn,
    session,
    commit: commitOf(body.commit) ?? undefined,
    confirmed: strs(body.confirmed),
    followup: normalizeTurn(body.followup),
  }
}

function undoneOf(v: unknown): Undone | undefined {
  if (!isObj(v)) return undefined
  const n = (x: unknown): number => (typeof x === 'number' ? x : Array.isArray(x) ? x.length : 0)
  const failed = Array.isArray(v.failed) ? v.failed.filter(isObj).map((f) => ({ what: str(f.what), reason: str(f.reason) })) : []
  return {
    lines: n(v.lines),
    terms: n(v.terms),
    questions: n(v.questions),
    kept: Array.isArray(v.kept) ? v.kept.filter(isObj).map((k) => ({ slug: str(k.slug), reason: str(k.reason) })).filter((k) => k.slug !== '') : [],
    ...(failed.length > 0 ? { failed } : {}),
  }
}

export async function revertTurn(id: string, turnId: string, lang: 'ja' | 'en' = 'ja'): Promise<RevertResponse> {
  const res = await fetch(`${API_BASE}/api/chat/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/revert?lang=${lang}`, {
    method: 'POST',
    headers: json(),
    body: '{}',
  })
  if (!res.ok) {
    if (res.status === 409) {
      const raw = await res.clone().json().catch(() => null)
      const d = isObj(raw) ? (isObj(raw.detail) ? raw.detail : raw) : null
      const undone = d ? undoneOf(d.undone) : undefined
      if (undone?.failed) throw new RevertFailedError(i18n.t('themes:error.failed', { op: i18n.t('chat:error.ops.revert'), status: 409, detail: '' }), '', undone)
    }
    throw await asError(res, i18n.t('chat:error.ops.revert'))
  }
  const body: unknown = await res.json()
  const turn = isObj(body) ? normalizeTurn(body.turn) : null
  const session = isObj(body) ? brief(body.session) : null
  if (!turn || !session || !isObj(body)) throw badShape()
  const undone = undoneOf(body.undone)
  return { turn, session, draft: draftSky(body.draft), reverted: strs(body.reverted), ...(undone ? { undone } : {}) }
}

/**
 * 星図。`GET /api/sky` は無認証のまま、鍵が付いていれば未確認の下書き（`proposed`）も返る。
 * （`sky/skyApi.ts` の `getSky` は鍵を付けないので、ここに別に持つ。）
 */
export async function getSkyWithProposed(
  lang: 'ja' | 'en',
): Promise<{ sky: SkyData; proposed: SkyDraft[]; proposedDatasets: SkyDataset[] }> {
  const res = await fetch(`${API_BASE}/api/sky?lang=${lang}`, { headers: authHeaders() })
  if (!res.ok) throw await asError(res, i18n.t('chat:error.ops.sky'))
  const body = (await res.json()) as Partial<SkyData> & { proposed?: unknown }
  const proposed = Array.isArray(body.proposed)
    ? body.proposed.map(draftSky).filter((d): d is SkyDraft => d !== null)
    : []
  const proposedDatasets = proposedDatasetsOf((body as { proposed_datasets?: unknown }).proposed_datasets)
  const rest: Partial<SkyData> & { proposed?: unknown; proposed_datasets?: unknown } = { ...body }
  delete rest.proposed
  delete rest.proposed_datasets
  return {
    sky: { ...EMPTY_SKY, ...rest, facts: { ...EMPTY_SKY.facts, ...(body.facts ?? {}) } },
    proposed,
    proposedDatasets,
  }
}

/** 取り込み済みで未公開のデータセットを、薄く描く `SkyDataset` の形にする。 */
export function proposedDatasetsOf(v: unknown): SkyDataset[] {
  if (!Array.isArray(v)) return []
  return v
    .filter(isObj)
    .filter((d) => typeof d.id === 'string' && d.id !== '')
    .map((d) => ({
      id: str(d.id),
      label: str(d.label),
      created_at: '',
      kinds: (Array.isArray(d.kinds) ? d.kinds.filter(isObj) : [])
        .filter((k) => typeof k.iri === 'string')
        .map((k) => ({ iri: str(k.iri), label: str(k.label), count: typeof k.count === 'number' ? k.count : 0, links: [], standard: null })),
      proposed: true,
    }))
}

// ── 点を押した 1 枚（`GET /api/chat/point`・契約 pr11d §A1） ───────────

export type PointKind = 'term' | 'kind' | 'std' | 'hub' | 'line' | 'draft'
const POINT_KINDS: readonly PointKind[] = ['term', 'kind', 'std', 'hub', 'line', 'draft']

export interface PointSentence {
  text: string
  relation: string
  source: string
  target: string
  drafted_by: string
  at: string
  batch: string
  reason: string
  session_id: string | null
  turn_id: string | null
}
export interface Point {
  iri: string
  kind: PointKind
  label: string
  term_kind: string
  sentences: PointSentence[]
  provenance: { drafted_by: string; at: string; theme_id: string | null; session_id: string | null; turn_id: string | null } | null
  questions: { title: string; mode: string; answering: number; knowledge: number }[]
  members: { iri: string; label: string; dataset_label: string }[]
  can_revert: boolean
  /** ストアにある（✓ 済みの用語・線・公開済みの種類・標準の語・視点）。提案の札・提案の線・薄い星は false。 */
  confirmed: boolean
  dataset_id?: string
  /** 標準の語（あれば）。 */
  standard: { iri: string; label: string; prefix: string } | null
  /** 薄い星（未公開のデータセットの種類）。api を呼ばずに作る小さな 1 枚。 */
  thin?: { dataset: string }
  revert: { session_id: string; turn_id: string } | null
}

/** 押した点（`iri`）か線（`from`・`relation`・`to`）。 */
export type PointQuery = { iri: string } | { from: string; relation: string; to: string }

export function normalizePoint(v: unknown): Point | null {
  if (!isObj(v) || !POINT_KINDS.includes(v.kind as PointKind)) return null
  const n = (x: unknown): number => (typeof x === 'number' ? x : 0)
  const prov = isObj(v.provenance)
    ? {
        drafted_by: str(v.provenance.drafted_by),
        at: str(v.provenance.at),
        theme_id: strOrNull(v.provenance.theme_id),
        session_id: strOrNull(v.provenance.session_id),
        turn_id: strOrNull(v.provenance.turn_id),
      }
    : null
  const rev = isObj(v.revert) && typeof v.revert.session_id === 'string' && typeof v.revert.turn_id === 'string' ? { session_id: v.revert.session_id, turn_id: v.revert.turn_id } : null
  return {
    iri: str(v.iri),
    kind: v.kind as PointKind,
    label: str(v.label),
    term_kind: str(v.term_kind),
    sentences: Array.isArray(v.sentences)
      ? v.sentences.filter(isObj).map((s) => ({
          text: str(s.text),
          relation: str(s.relation),
          source: str(s.source),
          target: str(s.target),
          drafted_by: str(s.drafted_by),
          at: str(s.at),
          batch: str(s.batch),
          reason: str(s.reason),
          session_id: strOrNull(s.session_id),
          turn_id: strOrNull(s.turn_id),
        }))
      : [],
    provenance: prov,
    questions: Array.isArray(v.questions)
      ? v.questions.filter(isObj).map((q) => ({ title: str(q.title), mode: str(q.mode), answering: n(q.answering), knowledge: n(q.knowledge) }))
      : [],
    members: Array.isArray(v.members)
      ? v.members.filter(isObj).map((m) => ({ iri: str(m.iri), label: str(m.label), dataset_label: str(m.dataset_label) }))
      : [],
    can_revert: v.can_revert === true,
    confirmed: v.confirmed === true,
    ...(typeof v.dataset_id === 'string' ? { dataset_id: v.dataset_id } : {}),
    standard: isObj(v.standard) && typeof v.standard.iri === 'string' ? { iri: v.standard.iri, label: str(v.standard.label), prefix: str(v.standard.prefix) } : null,
    revert: rev,
  }
}

export async function getPoint(query: PointQuery, lang: 'ja' | 'en' = 'ja'): Promise<Point> {
  const qs = new URLSearchParams({ ...(query as Record<string, string>), lang }).toString()
  const res = await call(`/api/chat/point?${qs}`, { headers: authHeaders() }, 'point')
  const p = normalizePoint(await res.json())
  if (!p) throw badShape()
  return p
}
