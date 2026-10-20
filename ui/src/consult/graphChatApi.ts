// 作業場の会話の api クライアント（home・dataset の画面用）。`POST /api/consult/graph{,/apply,/dismiss}`
// （api/src/asterism_api/consult_graph_routes.py）。
//
// `themes/consultGraphApi.ts`（7b）は screen=theme に固定している。ここは screen を引数にして、
// 返りの検査（validateProposal・validateApply）と型は 7b のものをそのまま使う。置き場を分けたのは、
// 7b の作業場（themes/）を触らずに home・dataset へ広げるため。
//   home    — propose は認証不要（AI の鍵はブラウザから）・apply／dismiss は write token
//   dataset — 3 つとも write token（列の見出しと型を AI に渡すため）
// 失敗は ThemesApiError（status 付き）。503＝AI 未設定・404＝データセットが無い・422＝形が違う。
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import { type LlmCredentials, llmHeaders } from '../settings/store'
import {
  type GraphApplyResult,
  type GraphContext,
  type GraphMessage,
  type GraphOp,
  type GraphProposal,
  validateApply,
  validateProposal,
} from '../themes/consultGraphApi'
import { ThemesApiError } from '../themes/themesApi'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export type ChatScreen = 'home' | 'dataset'

/** home の文脈に足せる欄（api の `context.focus_kind` — 星図で選んでいる種類の IRI）。 */
export type WorkspaceContext = GraphContext & { focus_kind?: string }

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

/** 人の一言 → 差分の提案。何も書かない。`messages` の最後は人の発言（新しい 12 件まで）。 */
export async function proposeOnScreen(
  screen: ChatScreen,
  context: WorkspaceContext,
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

export interface ApplyOnScreenArgs {
  screen: ChatScreen
  context: WorkspaceContext
  ops: GraphOp[]
  proposalId: string
  /** 「まとめて確定」の ID（来歴）。無ければサーバが付ける。 */
  batch?: string
}

/** home の適用で書いた・外した線の端（星図を取り直して、この点に輪を出すため）。 */
export interface AppliedLine {
  action: 'add' | 'remove'
  source: string
  target: string
  relation: string
}

export type ApplyOnScreenResult = GraphApplyResult & { lines: AppliedLine[] }

function linesOf(raw: unknown): AppliedLine[] {
  const rows = (raw as { lines?: unknown } | null)?.lines
  if (!Array.isArray(rows)) return []
  const out: AppliedLine[] = []
  for (const r of rows as Record<string, unknown>[]) {
    if (r && typeof r.source === 'string' && typeof r.target === 'string') {
      out.push({
        action: r.action === 'remove' ? 'remove' : 'add',
        source: r.source,
        target: r.target,
        relation: typeof r.relation === 'string' ? r.relation : '',
      })
    }
  }
  return out
}

/** 「そう」。選んだ ops だけを送れる。来歴は AI の提案を人が受けた（drafted_by=ai）。 */
export async function applyOnScreen(a: ApplyOnScreenArgs): Promise<ApplyOnScreenResult> {
  const res = await fetch(`${API_BASE}/api/consult/graph/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      screen: a.screen,
      context: a.context,
      ops: a.ops,
      proposal_id: a.proposalId,
      drafted_by: 'ai',
      ...(a.batch ? { batch: a.batch } : {}),
    }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.consultApply'))
  const raw: unknown = await res.json()
  const body = validateApply(raw)
  if (!body) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return { ...body, lines: linesOf(raw) }
}

/** 「違う」。同じ ops をそのまま送る（却下を保存して次の提案に出さない）。返りは却下した件数。 */
export async function dismissOnScreen(
  screen: ChatScreen,
  context: WorkspaceContext,
  ops: GraphOp[],
): Promise<number> {
  const res = await fetch(`${API_BASE}/api/consult/graph/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ screen, context, ops }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.consultDismiss'))
  const j = (await res.json()) as { dismissed?: unknown }
  return typeof j.dismissed === 'number' ? j.dismissed : 0
}
