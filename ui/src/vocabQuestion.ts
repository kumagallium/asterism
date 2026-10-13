import { knownVocabForIri, localName as _localName } from './vocab'
// 「ことば」画面の純関数（ADR upper-structure-shared-terms.md §2.1・§2.3・§2.5.1）。
// 描画から切り離してあるので、既定の問い・標準語を先に出す分岐・孤立の判定・hash の読み書きを
// vitest でそのまま固定できる。LLM は使わない（すべて決定論）。
import type { Alignment, AlignmentScope } from './crosswalkApi'
import type { GroundCandidate } from './groundingApi'
import type { CqInput, CqOp, MintTermInput, SharedTerm, SharedTermKind } from './vocabApi'

/** slug の形（ADR §2.1: `^[a-z][a-z0-9_]*$`）。 */
export const SLUG_RE = /^[a-z][a-z0-9_]*$/

/** 孤立（まだ線になっていない）語がこれを超えたら一覧の上に注意を出す（上限ではない・§2.1）。 */
export const ISOLATED_NOTICE_OVER = 20

/** ground_terms の完全一致の点。これ未満は「標準の語がある」とは言わない（§2.1）。 */
export const EXACT_SCORE = 100

export function isValidSlug(s: string): boolean {
  return SLUG_RE.test(s)
}

/**
 * 英語 label や標準語の名前から slug の既定値を作る。camelCase は語に割り、ASCII 以外は
 * 落とす。形に合わなければ空（人が入れる）。日本語 label からは作らない（決定論に作れない）。
 */
export function slugFromText(text: string): string {
  const ascii = text
    .normalize('NFKD')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  if (!ascii) return ''
  const slug = /^[0-9]/.test(ascii) ? `x_${ascii}` : ascii
  return isValidSlug(slug) ? slug : ''
}

/** 先に作れた候補を採る（英語 label → 列名 → 標準語の名前の順に渡す）。 */
export function suggestSlug(...sources: (string | undefined | null)[]): string {
  for (const s of sources) {
    const slug = slugFromText((s ?? '').trim())
    if (slug) return slug
  }
  return ''
}

/** 問いの op は種類の形で決まる（種類＝件数、項目＝値）。閉じた選択（§2.3）。 */
export function cqOpFor(kind: SharedTermKind): CqOp {
  return kind === 'class' ? 'count' : 'values'
}

type TLabel = (key: string, opts: { label: string }) => string

/** 既定の問い（§2.3 の表）。label が空なら空文字（まだ語が決まっていない）。 */
export function defaultCqTitle(kind: SharedTermKind, label: string, t: TLabel): string {
  const l = label.trim()
  if (!l) return ''
  return t(`vocab:question.defaultCq.${kind}`, { label: l })
}

/**
 * 保存前に確認する問いの初期値。題（問いが先の入口で書いた 1 文）があればそれ、無ければ既定の
 * テンプレート。人が直すまでの「見せかけの既定」で、保存されるのは人が確認した文だけ。
 */
export function initialCqTitle(
  question: string,
  kind: SharedTermKind,
  label: string,
  t: TLabel,
): string {
  return question.trim() || defaultCqTitle(kind, label, t)
}

/**
 * 標準の語を先に出すかどうか。完全一致（score 100）の最初の 1 件だけを返す。部分一致は出さない
 * （「標準の語がある」と言い切れるのは完全一致だけ・§2.1）。
 */
export function pickExactStandard(cands: GroundCandidate[]): GroundCandidate | null {
  return cands.find((c) => c.score >= EXACT_SCORE) ?? null
}

export interface MintDraft {
  labelJa: string
  labelEn: string
  slug: string
  kind: SharedTermKind
  comment: string
  /** 人が確認した問いの文（空は落とす）。 */
  cqTitles: string[]
  /** 標準の語はあったが使わない、と人が決めたときだけ。 */
  declined?: { iri: string; reason: string } | null
}

export type MintProblem = 'label' | 'slug' | 'cq' | 'reason'

/** フォームの入力を api の body にする。足りないものは問題名で返す（空欄のまま送らない）。 */
export function buildMintInput(d: MintDraft): { input: MintTermInput } | { problem: MintProblem } {
  if (!d.labelJa.trim()) return { problem: 'label' }
  if (!isValidSlug(d.slug)) return { problem: 'slug' }
  const op = cqOpFor(d.kind)
  const cqs: CqInput[] = d.cqTitles
    .map((s) => s.trim())
    .filter(Boolean)
    .map((title) => ({ title, op }))
  if (cqs.length === 0) return { problem: 'cq' }
  const input: MintTermInput = { slug: d.slug, kind: d.kind, label_ja: d.labelJa.trim(), cqs }
  if (d.labelEn.trim()) input.label_en = d.labelEn.trim()
  if (d.comment.trim()) input.comment = d.comment.trim()
  if (d.declined) {
    if (!d.declined.reason.trim()) return { problem: 'reason' }
    input.declined_standard = { iri: d.declined.iri, reason: d.declined.reason.trim() }
  }
  return { input }
}

/** 線が 1 本も無い（誰も答えない）語。 */
export function isolatedCount(terms: SharedTerm[]): number {
  return terms.filter((t) => !t.wired).length
}

/** 孤立の語が 20 を超えたら注意を出す（上限ではない）。 */
export function showIsolatedNotice(terms: SharedTerm[]): boolean {
  return isolatedCount(terms) > ISOLATED_NOTICE_OVER
}

// ---- 線の絞り込み ------------------------------------------------------------

/** 「線」の節の絞り込み。`all` は api に scope を送らない（全部）。 */
export type LineScope = 'all' | AlignmentScope

export const LINE_SCOPES: readonly LineScope[] = [
  'all',
  'perspective',
  'standard',
  'shared',
  'dataset',
]

/** 画面の絞り込みを api の引数にする（all＝省略）。 */
export function apiScope(scope: LineScope): AlignmentScope | undefined {
  return scope === 'all' ? undefined : scope
}

/** 切れている線を先頭に（気づけるように）。それ以外の順は api のまま（安定）。 */
export function orderLines(lines: Alignment[]): Alignment[] {
  const broken = lines.filter((a) => a.broken)
  return [...broken, ...lines.filter((a) => !a.broken)]
}

// ---- hash の読み書き ---------------------------------------------------------

export interface VocabHashParams {
  /** 持ち込まれた題（空＝無し）。 */
  q: string
  scope: LineScope | null
  dataset: string | null
}

/** `#/vocab?q=…&scope=…&dataset=…` を読む。`?` が無ければ全部空。 */
export function parseVocabHash(hash: string): VocabHashParams {
  const i = hash.indexOf('?')
  const params = new URLSearchParams(i === -1 ? '' : hash.slice(i + 1))
  const scope = params.get('scope')
  return {
    q: params.get('q') ?? '',
    scope: scope && (LINE_SCOPES as readonly string[]).includes(scope) ? (scope as LineScope) : null,
    dataset: params.get('dataset'),
  }
}

/** Ask などから「この問いに答えられるように、ことばをつなぐ」の行き先。題だけ運ぶ。 */
export function vocabQuestionHash(question: string): string {
  const q = question.trim()
  return q ? `#/vocab?q=${encodeURIComponent(q)}` : '#/vocab'
}

/** 線の絞り込みを開いた状態で「ことば」画面へ送る。`#/vocab?scope=…`。 */
export function vocabScopeHash(scope: LineScope): string {
  return scope === 'all' ? '#/vocab' : `#/vocab?scope=${scope}`
}

// ---- 節の再読み込み ----------------------------------------------------------

/** 「ことば」画面の版。語・問い・線のどれが変わっても 1 つ進め、4 つの節が読み直す。 */
export function bumpVersion(v: number): number {
  return v + 1
}

// ---- 問いの題 ----------------------------------------------------------------

/** 共有の語の問い（CQ）から、tool_name → 題 の引き表を作る（先に出た題を採る）。 */
export function cqTitleMap(terms: SharedTerm[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const term of terms) {
    for (const cq of term.cqs ?? []) {
      if (!(cq.tool_name in out)) out[cq.tool_name] = cq.title
    }
  }
  return out
}

/** 線の問いの見せ方。題が引ければ題、引けなければ tool_name のまま。 */
export function cqLabel(titles: Record<string, string> | undefined, toolName: string): string {
  return titles?.[toolName] || toolName
}

/** 線の端の名前。標準の語は語彙の接頭辞つき（`qudt:hasUnit`）、それ以外は表示名（サーバが
 *  返す rdfs:label）、無ければ IRI の末尾。内部の IRI の末尾（`Record_50577d`）だけでは、
 *  何の種類か読めなかった（実機 2026-10-09）。 */
export function endLabel(iri: string, kind?: string, label?: string): string {
  if (kind === 'standard') {
    const v = knownVocabForIri(iri)
    if (v) return `${v.prefix}${_localName(iri)}`
  }
  return (label ?? '').trim() || _localName(iri)
}
