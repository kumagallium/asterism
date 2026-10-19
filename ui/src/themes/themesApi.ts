// 「知りたいこと（テーマ）」の api クライアント（api/src/asterism_api/themes_routes.py）。
//
// decideApi.ts と同じ流儀: 同じ /api プロキシ、書き込みだけ authHeaders()、AI を呼ぶものだけ
// llmHeaders()。失敗は ThemesApiError（HTTP status 付き）で投げる — 画面が 503（AI が未設定・
// 手入力へ）/ 409（ID が重なる）/ 422（置けない組み合わせ）を言い分けられるように。
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import { type LlmCredentials, llmHeaders } from '../settings/store'
import type { ThemeDraft, DraftResponse, SelectionRow, TermRow, RelationRow } from './themeDraft'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export class ThemesApiError extends Error {
  status: number
  /** サーバの detail そのまま（無ければ空）。 */
  detail: string
  constructor(message: string, status: number, detail: string) {
    super(message)
    this.name = 'ThemesApiError'
    this.status = status
    this.detail = detail
  }
}

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

/** 一覧の 1 問（答えるデータセット数と実行文つき）。 */
export interface ThemeQuestion {
  tool_name: string
  title: string
  answering_datasets: number
  /** 機械が組む実行文（空＝まだ組めない）。 */
  shape: string
  terms: string[]
}

export interface ThemeTerm {
  slug: string
  label: string
  kind: 'class' | 'property'
  answering_datasets: number
  wired: boolean
}

export interface MemoQuestion {
  id: string
  title: string
  reason: string
}

export interface ThemeSummary {
  id: string
  label: string
  created_at: string
  questions: ThemeQuestion[]
  /** 答えられる（1 データセット以上が答える）問いの数。 */
  answerable: number
  terms: ThemeTerm[]
  memo_questions: MemoQuestion[]
}

export interface DraftSummary {
  id: string
  label: string
  step: number
  updated_at: string
}

export interface ThemesListing {
  total_datasets: number
  themes: ThemeSummary[]
  drafts: DraftSummary[]
  /** どのテーマにも入っていない共有の問い。 */
  unassigned_questions: ThemeQuestion[]
}

/** 移行で作る最初のテーマ（id=first）の題はサーバが日本語で保存する。既定の題のままなら
 * 表示側で UI 言語に合わせる（利用者が付け直した題はそのまま）。 */
export const FIRST_THEME_ID = 'first'
export const FIRST_THEME_DEFAULT_LABEL = 'はじめの問い'
export function themeLabel(theme: { id: string; label: string }, t: (key: string) => string): string {
  return theme.id === FIRST_THEME_ID && theme.label === FIRST_THEME_DEFAULT_LABEL
    ? t('themes:firstThemeLabel')
    : theme.label
}

export async function listThemes(lang: string): Promise<ThemesListing> {
  const res = await fetch(`${API_BASE}/api/themes?lang=${encodeURIComponent(lang)}`)
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.list'))
  return (await res.json()) as ThemesListing
}

/** 問いから AI が下書きを作る。503＝AI が未設定（画面は手入力へ）・502＝答えが決まった形にならない。 */
export async function draftWithAi(
  questions: string[],
  lang: string,
  creds: LlmCredentials | null,
): Promise<DraftResponse> {
  const res = await fetch(`${API_BASE}/api/themes/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...llmHeaders(creds), ...authHeaders() },
    body: JSON.stringify({ questions, lang }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.draft'))
  return (await res.json()) as DraftResponse
}

/** 閉じた選択の実行文（機械が組む）。足りなければ空文字。 */
export async function describeSelections(
  selections: { op: string; kind: string; property: string; value: string }[],
  lang: string,
): Promise<string[]> {
  const res = await fetch(`${API_BASE}/api/themes/describe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selections, lang }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.describe'))
  return ((await res.json()) as { sentences: string[] }).sentences
}

export async function createDraft(
  label: string,
  questions: string[],
  extend?: string,
): Promise<ThemeDraft> {
  const res = await fetch(`${API_BASE}/api/themes/drafts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ label, questions, ...(extend ? { extend } : {}) }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.saveDraft'))
  return (await res.json()) as ThemeDraft
}

export async function getDraft(id: string): Promise<ThemeDraft> {
  const res = await fetch(`${API_BASE}/api/themes/drafts/${encodeURIComponent(id)}`)
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.openDraft'))
  return (await res.json()) as ThemeDraft
}

export async function saveDraft(draft: ThemeDraft): Promise<ThemeDraft> {
  const { id, label, step, extend, questions, ai, terms, relations, selections } = draft
  const res = await fetch(`${API_BASE}/api/themes/drafts/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ label, step, extend, questions, ai, terms, relations, selections }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.saveDraft'))
  return (await res.json()) as ThemeDraft
}

export async function deleteDraft(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/themes/drafts/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.deleteDraft'))
}

/** 確定するときに送る下書きの最終形（themeDraft.buildCommitBody が作る）。 */
export interface CommitBody {
  label: string
  /** 既にあるテーマの id（あればそのテーマに問いを足す）。 */
  extend?: string
  lang: string
  ai_drafted: boolean
  questions: string[]
  terms: TermRow[]
  relations: Pick<RelationRow, 'subject' | 'object' | 'relation'>[]
  selections: SelectionRow[]
}

export interface CommitResult {
  theme: { id: string; label: string }
  minted: string[]
  lines: number
  questions: string[]
  memo: number
}

/** 用語・関係・問い・テーマを 1 操作で置く。409＝ID が重なる・422＝置けない組み合わせ。 */
export async function commitTheme(id: string, body: CommitBody): Promise<CommitResult> {
  const res = await fetch(`${API_BASE}/api/themes/${encodeURIComponent(id)}/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.commit'))
  return (await res.json()) as CommitResult
}

/** 作った問い（共有の問いの名前）をテーマに足す。 */
export async function addQuestionsToTheme(
  themeId: string,
  toolNames: string[],
  selections: Record<string, { op: string; kind?: string; property?: string; value?: string }> = {},
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/themes/${encodeURIComponent(themeId)}/questions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ tool_names: toolNames, selections }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.addQuestions'))
}

/** テーマを外す。用語・関係・問いは残る。 */
export async function deleteTheme(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/themes/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.deleteTheme'))
}

/** 型紙（CSV の見出し行だけ）の取得先。 */
export function templateUrl(id: string): string {
  return `${API_BASE}/api/themes/${encodeURIComponent(id)}/template.csv.zip`
}
