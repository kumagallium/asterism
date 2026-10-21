// 話（チャット）の api クライアント（api/src/asterism_api/chat_routes.py・契約 pr11a-contract.md §A〜§F）。
//
// 書き込みの鍵は authHeaders()、AI の座標は llmHeaders()（11b が使う。この PR のサーバは AI を呼ばない）。
// 失敗は themes と同じ ThemesApiError（HTTP status 付き）。受け取りは形を検査し、壊れていれば 502。
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import { type LlmCredentials, llmHeaders } from '../settings/store'
import { EMPTY_SKY, type DraftSky, type SkyData } from '../sky/skyApi'
import { ThemesApiError } from '../themes/themesApi'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// ── 形（契約 §A） ───────────────────────────────────────────────

/** 返事の注記 1 件。`kind` は閉集合だが、11c で増えるので文字列のまま受ける。 */
export type NoteKind = 'ai_unset' | 'ai_failed' | 'dropped' | 'no_question' | 'commit_error' | 'info'

export interface NoteChoice {
  label: string
  options: { value: string; label: string }[]
  default: string
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
  /** 11c。 */
  bindings: unknown[]
  notes: Note[]
  /** 11d。 */
  answer: unknown | null
  table: unknown | null
}

export type TurnState = 'none' | 'proposed' | 'confirmed' | 'reverted'

export interface CommitRecord {
  theme_id: string
  minted: string[]
  lines: number
  questions: string[]
  batch: string
  at: string
}

export interface Turn {
  id: string
  at: string
  user: { text: string; attachments: unknown[] } | null
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
}

export interface ConfirmResponse {
  turn: Turn
  session: SessionBrief
  commit?: CommitRecord
  confirmed?: string[]
}

export interface RevertResponse {
  turn: Turn
  session: SessionBrief
  draft: SkyDraft | null
  reverted: string[]
}

// ── 検査 ────────────────────────────────────────────────────────

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d)
const strOrNull = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
const STATES: readonly TurnState[] = ['none', 'proposed', 'confirmed', 'reverted']

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
      options: v.choice.options
        .filter(isObj)
        .map((o) => ({ value: str(o.value), label: str(o.label) })),
    }
  }
  return { kind: v.kind, text: str(v.text), choice }
}

function reply(v: unknown): Reply | null {
  if (!isObj(v)) return null
  return {
    text: str(v.text),
    ops: Array.isArray(v.ops) ? (v.ops.filter((o) => isObj(o) && typeof o.op === 'string') as GraphOp[]) : [],
    sentences: strs(v.sentences),
    questions: strs(v.questions),
    bindings: Array.isArray(v.bindings) ? v.bindings : [],
    notes: Array.isArray(v.notes) ? v.notes.map(note).filter((n): n is Note => n !== null) : [],
    answer: v.answer ?? null,
    table: v.table ?? null,
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
  }
}

export function normalizeTurn(v: unknown): Turn | null {
  if (!isObj(v) || typeof v.id !== 'string') return null
  const r = reply(v.reply)
  if (!r) return null
  const state = STATES.includes(v.state as TurnState) ? (v.state as TurnState) : null
  if (!state) return null
  const user = isObj(v.user)
    ? { text: str(v.user.text), attachments: Array.isArray(v.user.attachments) ? v.user.attachments : [] }
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

function draftSky(v: unknown): SkyDraft | null {
  if (!isObj(v) || typeof v.draft_id !== 'string' || !Array.isArray(v.concepts)) return null
  return {
    ...(v as unknown as DraftSky),
    relations: Array.isArray(v.relations) ? (v.relations as DraftSky['relations']) : [],
    questions: strs(v.questions),
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

export interface TurnInput {
  text?: string
  ops?: GraphOp[]
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
  return { turn, session, draft: draftSky(body.draft) }
}

export async function confirmTurn(id: string, turnId: string, lang: 'ja' | 'en'): Promise<ConfirmResponse> {
  const res = await call(
    `/api/chat/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/confirm`,
    { method: 'POST', headers: json(), body: JSON.stringify({ lang }) },
    'confirm',
  )
  const body: unknown = await res.json()
  const turn = isObj(body) ? normalizeTurn(body.turn) : null
  const session = isObj(body) ? brief(body.session) : null
  if (!turn || !session || !isObj(body)) throw badShape()
  return { turn, session, commit: commitOf(body.commit) ?? undefined, confirmed: strs(body.confirmed) }
}

export async function revertTurn(id: string, turnId: string): Promise<RevertResponse> {
  const res = await call(
    `/api/chat/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/revert`,
    { method: 'POST', headers: json(), body: '{}' },
    'revert',
  )
  const body: unknown = await res.json()
  const turn = isObj(body) ? normalizeTurn(body.turn) : null
  const session = isObj(body) ? brief(body.session) : null
  if (!turn || !session || !isObj(body)) throw badShape()
  return { turn, session, draft: draftSky(body.draft), reverted: strs(body.reverted) }
}

/**
 * 星図。`GET /api/sky` は無認証のまま、鍵が付いていれば未確認の下書き（`proposed`）も返る。
 * （`sky/skyApi.ts` の `getSky` は鍵を付けないので、ここに別に持つ。）
 */
export async function getSkyWithProposed(lang: 'ja' | 'en'): Promise<{ sky: SkyData; proposed: SkyDraft[] }> {
  const res = await fetch(`${API_BASE}/api/sky?lang=${lang}`, { headers: authHeaders() })
  if (!res.ok) throw await asError(res, i18n.t('chat:error.ops.sky'))
  const body = (await res.json()) as Partial<SkyData> & { proposed?: unknown }
  const proposed = Array.isArray(body.proposed)
    ? body.proposed.map(draftSky).filter((d): d is SkyDraft => d !== null)
    : []
  const rest: Partial<SkyData> & { proposed?: unknown } = { ...body }
  delete rest.proposed
  return {
    sky: { ...EMPTY_SKY, ...rest, facts: { ...EMPTY_SKY.facts, ...(body.facts ?? {}) } },
    proposed,
  }
}
