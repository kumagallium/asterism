// 作業場の会話の api クライアント（api/src/asterism_api/consult_graph_routes.py・7a）。
//
// `POST /api/consult/graph`（認証不要・AI を呼ぶ → llmHeaders）: 人の一言 → 差分の提案
// `POST /api/consult/graph/apply`（write token）: 「そう」→ 検査に通して当てる・theme は返った draft で図を更新
// `POST /api/consult/graph/dismiss`（write token）: 「違う」「✕」→ 却下を保存（次の提案に出ない）
// 失敗は ThemesApiError（HTTP status 付き）。503＝AI 未設定・404＝下書き無し・502＝差分が 2 回不正。
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import { type LlmCredentials, llmHeaders } from '../settings/store'
import { type ThemeDraft, normalizeDraft } from './themeDraft'
import { ThemesApiError } from './themesApi'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** 閉じた差分 1 操作（AI も人も同じ形）。`op` 以外の欄は api が検査する。 */
export type GraphOp = { op: string } & Record<string, unknown>

export interface GraphContext {
  draft_id?: string
  dataset_id?: string
}

export interface GraphMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface GraphProposal {
  reply: string
  ops: GraphOp[]
  /** サーバが ops と同じ順で組んだ 1 操作 1 文。UI は組まない。 */
  sentences: string[]
  proposal_id: string
}

/** 落とされた操作。`index` は送った ops の添字。 */
export interface GraphDropped {
  index: number
  op: string
  reason: string
  ref: string
}

export interface GraphApplyResult {
  applied: number
  dropped: GraphDropped[]
  unlinked: string[]
  /** theme は当たれば返る（図と文はこれで更新する）。 */
  draft: ThemeDraft | null
  /** 同じ proposal_id の二重適用（何もしなかった）。 */
  duplicate: boolean
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isStr = (v: unknown): v is string => typeof v === 'string'

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

function opsOf(raw: unknown): GraphOp[] | null {
  if (!Array.isArray(raw)) return null
  const out: GraphOp[] = []
  for (const o of raw) {
    if (!isObj(o) || !isStr(o.op)) return null
    out.push(o as GraphOp)
  }
  return out
}

/** 提案の返りを、画面が読める形に（形が違えば null）。sentences は ops と同じ長さに揃える。 */
export function validateProposal(raw: unknown): GraphProposal | null {
  if (!isObj(raw) || !isStr(raw.proposal_id)) return null
  const ops = opsOf(raw.ops)
  if (!ops) return null
  const sentences = Array.isArray(raw.sentences) ? raw.sentences.map((s) => (isStr(s) ? s : '')) : []
  while (sentences.length < ops.length) sentences.push('')
  return {
    reply: isStr(raw.reply) ? raw.reply : '',
    ops,
    sentences: sentences.slice(0, ops.length),
    proposal_id: raw.proposal_id,
  }
}

export function validateApply(raw: unknown): GraphApplyResult | null {
  if (!isObj(raw) || typeof raw.applied !== 'number') return null
  const dropped: GraphDropped[] = (Array.isArray(raw.dropped) ? raw.dropped : [])
    .filter(isObj)
    .map((d) => ({
      index: typeof d.index === 'number' ? d.index : -1,
      op: isStr(d.op) ? d.op : '',
      reason: isStr(d.reason) ? d.reason : '',
      ref: isStr(d.ref) ? d.ref : '',
    }))
  return {
    applied: raw.applied,
    dropped,
    unlinked: (Array.isArray(raw.unlinked) ? raw.unlinked : []).filter(isStr),
    draft: raw.draft !== undefined ? normalizeDraft(raw.draft) : null,
    duplicate: raw.duplicate === true,
  }
}

/** 人の一言 → 差分の提案。何も書かない。`messages` の最後は人の発言（新しい 12 件まで）。 */
export async function proposeGraph(
  screen: 'theme',
  context: GraphContext,
  messages: GraphMessage[],
  lang: string,
  creds: LlmCredentials | null,
): Promise<GraphProposal> {
  const res = await fetch(`${API_BASE}/api/consult/graph`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...llmHeaders(creds), ...authHeaders() },
    body: JSON.stringify({ screen, context, messages: messages.slice(-12), lang }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.consult'))
  const body = validateProposal(await res.json())
  if (!body) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return body
}

export interface ApplyArgs {
  context: GraphContext
  ops: GraphOp[]
  proposalId: string
  draftedBy: 'ai' | 'human'
  /** この提案に至った人の発言（前の適用から今まで）。 */
  messages?: string[]
  batch?: string
}

/** 「そう」。選んだ ops（と対応する sentences）だけを送れる。 */
export async function applyGraph(a: ApplyArgs): Promise<GraphApplyResult> {
  const res = await fetch(`${API_BASE}/api/consult/graph/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      screen: 'theme',
      context: a.context,
      ops: a.ops,
      proposal_id: a.proposalId,
      drafted_by: a.draftedBy,
      ...(a.batch ? { batch: a.batch } : {}),
      ...(a.messages && a.messages.length > 0
        ? { messages: a.messages.slice(-12).map((content) => ({ role: 'user', content })) }
        : {}),
    }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.consultApply'))
  const body = validateApply(await res.json())
  if (!body) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return body
}

/** 「違う」「✕」。同じ ops をそのまま送る。返りは却下した件数。 */
export async function dismissGraph(context: GraphContext, ops: GraphOp[]): Promise<number> {
  const res = await fetch(`${API_BASE}/api/consult/graph/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ screen: 'theme', context, ops }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.consultDismiss'))
  const j = (await res.json()) as { dismissed?: unknown }
  return typeof j.dismissed === 'number' ? j.dismissed : 0
}
