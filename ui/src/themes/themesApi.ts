// 「知りたいこと（テーマ）」の api クライアント（api/src/asterism_api/themes_routes.py）。
//
// decideApi.ts と同じ流儀: 同じ /api プロキシ、書き込みだけ authHeaders()、AI を呼ぶものだけ
// llmHeaders()。失敗は ThemesApiError（HTTP status 付き）で投げる — 画面が 503（AI が未設定・
// 手入力へ）/ 409（ID が重なる）/ 422（置けない組み合わせ）を言い分けられるように。
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import { type LlmCredentials, llmHeaders } from '../settings/store'
import {
  type DraftPending,
  type DraftResponse,
  buildCommitBody,
  type MatchResult,
  type ThemeDraft,
  normalizeDraft,
  validateDraftResponse,
} from './themeDraft'

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
  /** `knowledge`（確定した関係の線から答える）／ `data`（宣言ツール）。古いサーバは省略。 */
  mode?: 'knowledge' | 'data'
  /** 知識の問いだけ: 確定した線から出る答えの件数。 */
  knowledge_count?: number
  /** 「欲しいデータ」1 行（無ければ空）。 */
  wanted_data?: string
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
  wanted_data?: string
}

export interface ThemeSummary {
  id: string
  label: string
  created_at: string
  questions: ThemeQuestion[]
  /** 答えられる（1 データセット以上が答える）問いの数。 */
  answerable: number
  /** 知識で答えられる件数の合計（知識の問いの knowledge_count の和）。 */
  knowledge_total?: number
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

/** 問いから AI が知識の図の下書きを作る。503＝AI が未設定（画面は手入力へ）・502＝答えが決まった形にならない。 */
export async function draftWithAi(
  questions: string[],
  label: string,
  lang: string,
  creds: LlmCredentials | null,
): Promise<DraftResponse> {
  const res = await fetch(`${API_BASE}/api/themes/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...llmHeaders(creds), ...authHeaders() },
    body: JSON.stringify({ questions, label, lang }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.draft'))
  const body = validateDraftResponse(await res.json())
  if (!body) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return body
}

/**
 * 手で書いた用語の行を、既にある用語・標準用語と完全一致で照合する（決定論・AI は呼ばない）。
 * 失敗は呼ぶ側が黙って「なし」のままにする。
 */
export async function matchTerms(
  terms: { key: string; label_ja: string; label_en: string; aliases: string[]; kind: string }[],
  /** 同じ下書きで既に使っている ID（仮置きの `t<n>` を重ねないため）。 */
  taken: string[] = [],
): Promise<MatchResult[]> {
  const res = await fetch(`${API_BASE}/api/themes/match`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, taken }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.match'))
  return ((await res.json()) as { terms: MatchResult[] }).terms
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
  const d = normalizeDraft(await res.json())
  if (!d) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return d
}

export async function getDraft(id: string): Promise<ThemeDraft> {
  const res = await fetch(`${API_BASE}/api/themes/drafts/${encodeURIComponent(id)}`, { headers: authHeaders() })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.openDraft'))
  const d = normalizeDraft(await res.json())
  if (!d) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return d
}

/**
 * 下書きを保存する。`pending` は提案（薄い）のまま残る行（送らなければサーバの控えを残す）・
 * `resetChat` は保存済みの会話を空にする（「もう一度読ませる」: 消えた行についての会話を戻さない）。
 */
export async function saveDraft(
  draft: ThemeDraft,
  extra: { pending?: DraftPending; resetChat?: boolean } = {},
): Promise<ThemeDraft> {
  const { id, label, step, extend, questions, concepts, relations, answers, suggested_questions, standards } = draft
  const res = await fetch(`${API_BASE}/api/themes/drafts/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      label,
      step,
      extend,
      questions,
      concepts,
      relations,
      answers,
      suggested_questions,
      standards,
      ...(extra.pending ? { pending: extra.pending } : {}),
      ...(extra.resetChat ? { reset_chat: true } : {}),
    }),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.saveDraft'))
  const d = normalizeDraft(await res.json())
  if (!d) throw new ThemesApiError(i18n.t('themes:read.badShape'), 502, '')
  return d
}

export async function deleteDraft(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/themes/drafts/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.deleteDraft'))
}

/** 確定するときに送る下書きの最終形（themeDraft.buildCommitBody が作る）。 */
export type CommitBody = ReturnType<typeof buildCommitBody>

export interface CommitResult {
  theme: { id: string; label: string }
  minted: string[]
  lines: number
  questions: string[]
  memo: number
  batch?: string
}

/** 概念・関係・問い・テーマを 1 操作で置く。409＝ID が重なる・422＝置けない組み合わせ（問いにつながらない概念など）。 */
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

/** テーマ単位の取り消しの結果。ほかのテーマ・データが使っている概念と関係は残る。 */
export interface RemovedTheme {
  removed_concepts: { slug: string; label: string }[]
  kept_concepts: { slug: string; label: string; reason: string }[]
  removed_relations: { from: string; relation: string; to: string }[]
  kept_relations: { from: string; relation: string; to: string }[]
}

/** テーマを外す。ほかのテーマの問い・データの線に使われていない概念と関係も消える。 */
export async function deleteTheme(id: string): Promise<RemovedTheme | null> {
  const res = await fetch(`${API_BASE}/api/themes/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.deleteTheme'))
  // 200 で結果を返す（古いサーバは 204）。読めなければ結果なし。
  return (await res.json().catch(() => null)) as RemovedTheme | null
}

/** 問いごとの答え（知識 N・データ N）。 */
export interface KnowledgeItem {
  iri: string
  label: string
  relation: string
  via: string
  via_label: string
  reason: string | null
  drafted_by: string
  at: string
  batch: string | null
}

export interface QuestionAnswer {
  tool_name: string
  title: string
  mode: 'knowledge' | 'data'
  shape: string
  knowledge: { count: number; items: KnowledgeItem[] } | null
  data: { answering: number; total_datasets: number } | null
  wanted_data: string
}

export async function getThemeAnswers(themeId: string, lang: string): Promise<QuestionAnswer[]> {
  const res = await fetch(
    `${API_BASE}/api/themes/${encodeURIComponent(themeId)}/answers?lang=${encodeURIComponent(lang)}`,
  )
  if (!res.ok) throw await asError(res, i18n.t('themes:error.ops.answers'))
  return ((await res.json()) as { answers?: QuestionAnswer[] }).answers ?? []
}

/** 型紙（CSV の見出し行だけ）の取得先。 */
export function templateUrl(id: string): string {
  return `${API_BASE}/api/themes/${encodeURIComponent(id)}/template.csv.zip`
}
