// 「知りたいことから作る」（テーマ・3 段）の純関数。描画（ThemeWizard）から切り離してあるので、
// 段の進め方・閉じた JSON の検証・つながりの検査・確定の body を vitest でそのまま固定できる。
// LLM は使わない（AI の下書きは api が作り、ここは検証と整形だけ）。
//
// 設計の芯（design.md §15・PR 6b 契約）:
//   - 3 段: ① 書く → ② 下書きをたしかめる（知識の図＋文＋答え方）→ ③ 置く。
//   - AI は閉じた JSON だけ。機械の完全一致（標準用語・既にある用語）が AI の use に勝つ。
//   - 参照は key（同名の種類と項目を区別する）。「そう／違う」は問わない — 下書きは初期未確定のまま、
//     人は ✕ で外し、「N 件を置く」で一括して決める（来歴に AI 下書き・日付・理由が残る）。
//   - 確定は「問いの対象に関係でつながらない新概念」を 422 で拒む。②で先にその印を出し、送らない。
//   - 関係は閉集合 9 述語（一種・同じ・量・一部・下げる・上げる・変える）。標準の述語を使う。
import { normalizeName } from '../decide/decide'
import { QUANTITY_KIND_NS, SV_NS } from '../lineChoice'
import { isValidSlug, slugFromText } from '../vocabQuestion'

export type Step = 1 | 2 | 3
export const LAST_STEP: Step = 3

export type TermKind = 'class' | 'property'
export type RelKind = 'sub' | 'eq' | 'quantity' | 'part' | 'lowers' | 'raises' | 'affects'
export const REL_KINDS: readonly RelKind[] = ['sub', 'eq', 'quantity', 'part', 'lowers', 'raises', 'affects']
export type RelationProblem = 'same' | 'mixed' | 'cycle' | 'quantity_target' | 'target_not_property'
export type AnswerMode = 'knowledge' | 'data' | 'memo'
export type MemoCode = 'op' | 'ai' | 'none' | 'human'

/** 機械が見つけた「既にあるもの」（完全一致）。 */
export interface TermMatch {
  kind: 'existing' | 'standard' | 'quantity'
  /** existing＝slug・standard／quantity＝curie。 */
  ref: string
  label: string
  matched_by: 'label' | 'alias'
}

/** ②の図の概念 1 つ。`key` は行の識別子で、関係・答え方はこれで結ぶ。 */
export interface ConceptRow {
  key: string
  label_ja: string
  label_en: string
  aliases: string[]
  kind: TermKind
  /** `new` ／ `existing:<slug>` ／ `standard:<curie>`。 */
  use: string
  /** ID（必ず入る。英語名が無ければ機械が `t<n>` を仮置き・③で直せる）。 */
  slug: string
  reason: string
  match: TermMatch | null
  by: 'ai' | 'human'
  /** `use` を決めたのは誰か。`human`＝人が触った（機械の照合で上書きしない）。 */
  use_by?: 'machine' | 'human'
}

/** 関係 1 本。`to` は概念の key か標準用語の curie。`problem` があれば確定から外す。 */
export interface RelationRow {
  from: string
  rel: RelKind
  to: string
  reason: string
  problem: RelationProblem | null
  by: 'ai' | 'human'
}

/** 問い 1 つの答え方。`target` / `kind` / `property` は概念の key。 */
export interface AnswerRow {
  question: string
  mode: AnswerMode
  /** knowledge: factors|parts|kinds・data: count|range|top。 */
  op: string
  target: string
  /** factors の向き: lowers|raises|affects。 */
  direction: string
  kind: string
  property: string
  value: string
  memo: MemoCode | null
  wanted_data: string
  reason: string
  by: 'ai' | 'human'
}

export interface StandardInfo {
  label: string
  kind: string
  /** api が返す標準の語の IRI（無ければ `STD_NAMESPACES` の表で組む。古い下書きのための予備）。 */
  iri?: string
}

/** POST /api/themes/draft の返り値（機械の後処理まで済んだ行）。 */
export interface DraftResponse {
  theme_label: string
  concepts: ConceptRow[]
  relations: RelationRow[]
  answers: AnswerRow[]
  suggested_questions: string[]
  standards: Record<string, StandardInfo>
  dropped: { concepts: unknown[]; relations: unknown[] }
}

/** 作業場の会話の保存（7a）。人の発言と、適用した差分だけ。AI の散文は保存しない。 */
export interface DraftChat {
  messages: { role: 'user'; content: string; at: string }[]
  proposals: { proposal_id: string; ops: Record<string, unknown>[]; at: string; by: 'ai' | 'human' }[]
}

/** まだ「提案（薄い）」の行（サーバが下書きと一緒に持つ）。関係は `[from, rel, to]`。無ければ「覚えていない」。 */
export interface DraftPending {
  concepts: string[]
  relations: string[][]
}

/** 保存する下書き（GET/PUT /api/themes/drafts/{id}）。 */
export interface ThemeDraft {
  id: string
  label: string
  step: Step
  /** 既にあるテーマの id（「問いを足す」: 置くと、そのテーマに問いが加わる）。 */
  extend?: string
  questions: string[]
  concepts: ConceptRow[]
  relations: RelationRow[]
  answers: AnswerRow[]
  suggested_questions: string[]
  standards: Record<string, StandardInfo>
  /** 作業場の会話（サーバが持つ。PUT では送らず、適用の返り値で更新される）。 */
  chat?: DraftChat
  /** 提案（薄い）のまま残る行（PUT で送り、GET で返る。無ければ覚えていない）。 */
  pending?: DraftPending
  updated_at: string
}

// ---- 問いの入力（①） -------------------------------------------------------------

/** 1 行 1 問のテキスト → 問いの配列（前後の空白を整え、空・重複を除く）。 */
export function parseQuestionLines(text: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const line of text.split('\n')) {
    const q = line.replace(/\s+/g, ' ').trim()
    const key = q.normalize('NFKC').toLowerCase()
    if (!q || seen.has(key)) continue
    seen.add(key)
    out.push(q)
  }
  return out
}

/** AI の案の chip を押して問いを足す（すでにあれば何もしない）。 */
export function addQuestion(questions: string[], q: string): string[] {
  return parseQuestionLines([...questions, q].join('\n'))
}

/** テーマ名が空のとき（AI 未設定・AI が名前を返さなかったとき）の名前: 最初の問いの先頭 20 字。 */
export function defaultLabel(questions: string[]): string {
  return (questions[0] ?? '').slice(0, 20).trimEnd()
}

/** ①の例チップ（押すと問いの欄へ足す）。 */
export const EXAMPLE_QUESTIONS_JA = [
  'ZT が最も高い試料の組成は？',
  '熱伝導率を下げる要素になり得るのは？',
  'にんじんを使うレシピは？',
] as const
export const EXAMPLE_QUESTIONS_EN = [
  'Which sample composition has the highest ZT?',
  'What can lower thermal conductivity?',
  'Which recipes use carrots?',
] as const

// ---- 閉じた JSON の検証（api の返り値を信じすぎない） ------------------------------

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const isStr = (v: unknown): v is string => typeof v === 'string'
const str = (v: unknown): string => (isStr(v) ? v : '')
const isRel = (v: unknown): v is RelKind => REL_KINDS.includes(v as RelKind)

function conceptOf(c: Record<string, unknown>, by: 'ai' | 'human'): ConceptRow | null {
  if (!isStr(c.key) || !isStr(c.label_ja)) return null
  if (c.kind !== 'class' && c.kind !== 'property') return null
  return {
    key: c.key,
    label_ja: c.label_ja,
    label_en: str(c.label_en),
    aliases: Array.isArray(c.aliases) ? c.aliases.filter(isStr) : [],
    kind: c.kind,
    use: isStr(c.use) && c.use ? c.use : 'new',
    slug: str(c.slug),
    reason: str(c.reason),
    match: isObj(c.match) && isStr(c.match.ref) ? (c.match as unknown as TermMatch) : null,
    by: c.by === 'human' ? 'human' : by,
    ...(c.use_by === 'human' || c.use_by === 'machine' ? { use_by: c.use_by } : {}),
  }
}

function relationOf(r: Record<string, unknown>): RelationRow | null {
  if (!isStr(r.from) || !isStr(r.to) || !isRel(r.rel)) return null
  return {
    from: r.from,
    rel: r.rel,
    to: r.to,
    reason: str(r.reason),
    problem: isStr(r.problem) && r.problem ? (r.problem as RelationProblem) : null,
    by: r.by === 'human' ? 'human' : 'ai',
  }
}

function answerOf(a: Record<string, unknown>): AnswerRow | null {
  if (!isStr(a.question)) return null
  const mode: AnswerMode = a.mode === 'knowledge' || a.mode === 'data' ? a.mode : 'memo'
  return {
    question: a.question,
    mode,
    op: str(a.op),
    target: str(a.target),
    direction: str(a.direction),
    kind: str(a.kind),
    property: str(a.property),
    value: str(a.value),
    memo: isStr(a.memo) && a.memo ? (a.memo as MemoCode) : mode === 'memo' ? 'none' : null,
    wanted_data: str(a.wanted_data),
    reason: str(a.reason),
    by: a.by === 'human' ? 'human' : 'ai',
  }
}

function listOf<T>(raw: unknown, f: (o: Record<string, unknown>) => T | null): T[] | null {
  if (!Array.isArray(raw)) return null
  const out: T[] = []
  for (const o of raw) {
    if (!isObj(o)) return null
    const v = f(o)
    if (v === null) return null
    out.push(v)
  }
  return out
}

function standardsOf(raw: unknown): Record<string, StandardInfo> {
  if (!isObj(raw)) return {}
  const out: Record<string, StandardInfo> = {}
  for (const [k, v] of Object.entries(raw)) {
    if (!isObj(v)) continue
    out[k] = { label: str(v.label), kind: str(v.kind), ...(isStr(v.iri) && v.iri ? { iri: v.iri } : {}) }
  }
  return out
}

/** api の返り値が、画面の読める形か。形が合わなければ `null`（画面は手入力へ戻る）。 */
export function validateDraftResponse(raw: unknown): DraftResponse | null {
  if (!isObj(raw)) return null
  const concepts = listOf(raw.concepts, (c) => conceptOf(c, 'ai'))
  const relations = listOf(raw.relations, relationOf)
  const answers = listOf(raw.answers, answerOf)
  if (!concepts || !relations || !answers) return null
  const dropped = isObj(raw.dropped) ? raw.dropped : {}
  return {
    theme_label: str(raw.theme_label),
    concepts,
    relations,
    answers,
    suggested_questions: Array.isArray(raw.suggested_questions) ? raw.suggested_questions.filter(isStr) : [],
    standards: standardsOf(raw.standards),
    dropped: {
      concepts: Array.isArray(dropped.concepts) ? dropped.concepts : [],
      relations: Array.isArray(dropped.relations) ? dropped.relations : [],
    },
  }
}

function chatOf(raw: unknown): DraftChat | undefined {
  if (!isObj(raw)) return undefined
  const messages = (Array.isArray(raw.messages) ? raw.messages : [])
    .filter(isObj)
    .filter((m) => isStr(m.content) && m.content.trim() !== '')
    .map((m) => ({ role: 'user' as const, content: str(m.content), at: str(m.at) }))
  const proposals = (Array.isArray(raw.proposals) ? raw.proposals : [])
    .filter(isObj)
    .filter((p) => isStr(p.proposal_id) && Array.isArray(p.ops))
    .map((p) => ({
      proposal_id: str(p.proposal_id),
      ops: (p.ops as unknown[]).filter(isObj),
      at: str(p.at),
      by: p.by === 'human' ? ('human' as const) : ('ai' as const),
    }))
  return messages.length > 0 || proposals.length > 0 ? { messages, proposals } : undefined
}

function pendingOfRaw(raw: unknown): DraftPending | undefined {
  if (!isObj(raw)) return undefined
  return {
    concepts: (Array.isArray(raw.concepts) ? raw.concepts : []).filter(isStr),
    relations: (Array.isArray(raw.relations) ? raw.relations : []).flatMap((r) =>
      Array.isArray(r) && r.length === 3 && r.every(isStr) ? [r as string[]] : [],
    ),
  }
}

/** GET/PUT が返す保存済み下書きを、画面の形に整える（壊れた行は落とす）。 */
export function normalizeDraft(raw: unknown): ThemeDraft | null {
  if (!isObj(raw) || !isStr(raw.id)) return null
  const step = raw.step === 2 || raw.step === 3 ? raw.step : 1
  const concepts = (Array.isArray(raw.concepts) ? raw.concepts : [])
    .filter(isObj)
    .map((c) => conceptOf(c, 'ai'))
    .filter((c): c is ConceptRow => c !== null)
  const relations = (Array.isArray(raw.relations) ? raw.relations : [])
    .filter(isObj)
    .map(relationOf)
    .filter((r): r is RelationRow => r !== null)
  const answers = (Array.isArray(raw.answers) ? raw.answers : [])
    .filter(isObj)
    .map(answerOf)
    .filter((a): a is AnswerRow => a !== null)
  const questions = Array.isArray(raw.questions) ? raw.questions.filter(isStr) : []
  return {
    id: raw.id,
    label: str(raw.label),
    step,
    ...(isStr(raw.extend) && raw.extend ? { extend: raw.extend } : {}),
    questions,
    concepts,
    relations,
    answers: syncAnswers(answers, questions),
    suggested_questions: Array.isArray(raw.suggested_questions) ? raw.suggested_questions.filter(isStr) : [],
    standards: standardsOf(raw.standards),
    ...(chatOf(raw.chat) ? { chat: chatOf(raw.chat) } : {}),
    ...(pendingOfRaw(raw.pending) ? { pending: pendingOfRaw(raw.pending) } : {}),
    updated_at: str(raw.updated_at),
  }
}

/** AI の答えを下書きに取り込む（①→②）。 */
export function applyDraftResponse(draft: ThemeDraft, res: DraftResponse): ThemeDraft {
  return {
    ...draft,
    label: draft.label.trim() ? draft.label : res.theme_label || defaultLabel(draft.questions),
    concepts: res.concepts,
    relations: res.relations,
    answers: syncAnswers(res.answers, draft.questions),
    suggested_questions: res.suggested_questions,
    standards: res.standards,
  }
}

export function blankAnswer(question: string): AnswerRow {
  return {
    question,
    mode: 'memo',
    op: '',
    target: '',
    direction: '',
    kind: '',
    property: '',
    value: '',
    memo: 'none',
    wanted_data: '',
    reason: '',
    by: 'human',
  }
}

/** 答え方の行を問いの並びに合わせる（消えた問いの行は落とし、増えた問いは「まだ」の行を足す）。 */
export function syncAnswers(answers: AnswerRow[], questions: string[]): AnswerRow[] {
  const by = new Map(answers.map((a) => [normalizeName(a.question), a]))
  return questions.map((q) => by.get(normalizeName(q)) ?? blankAnswer(q))
}

/** AI を使わず手で書く（①→②）。問いごとの「まだ」の行だけ作り、概念は②で足す。 */
export function startManual(draft: ThemeDraft): ThemeDraft {
  return {
    ...draft,
    label: draft.label.trim() ? draft.label : defaultLabel(draft.questions),
    answers: syncAnswers(draft.answers, draft.questions),
  }
}

// ---- 概念・関係の編集（②） ---------------------------------------------------------

/** 概念の行で、新しく作るものか（`new` ／ 標準の語と結んで作る）。既にある用語を使う行は作らない。 */
export function conceptMints(row: Pick<ConceptRow, 'use'>): boolean {
  return !row.use.startsWith('existing:')
}

/** 手で足した行の key（AI の `c<n>` とぶつからない）。 */
export function nextHandKey(rows: { key: string }[]): string {
  let n = 1
  const keys = new Set(rows.map((r) => r.key))
  while (keys.has(`h${n}`)) n++
  return `h${n}`
}

export function newConceptRow(rows: ConceptRow[], label: string, kind: TermKind): ConceptRow {
  return {
    key: nextHandKey(rows),
    label_ja: label.trim(),
    label_en: '',
    aliases: [],
    kind,
    use: 'new',
    slug: '',
    reason: '',
    match: null,
    by: 'human',
  }
}

/** 同名（正規化して一致）の概念が既にあるか。手で足すときの重複を止める。 */
export function hasConceptNamed(rows: ConceptRow[], label: string): boolean {
  const n = normalizeName(label)
  return n !== '' && rows.some((r) => normalizeName(r.label_ja) === n || normalizeName(r.label_en) === n)
}

export function addConcept(draft: ThemeDraft, label: string, kind: TermKind): ThemeDraft {
  if (!label.trim() || hasConceptNamed(draft.concepts, label)) return draft
  return { ...draft, concepts: [...draft.concepts, newConceptRow(draft.concepts, label, kind)] }
}

/** 日本語・英語名から決まる ID の既定（英語名 → 別名の順。作れなければ空）。 */
export function suggestRowSlug(row: Pick<ConceptRow, 'label_en' | 'aliases'>, taken: Iterable<string>): string {
  const base = slugFromText(row.label_en) || row.aliases.map(slugFromText).find(Boolean) || ''
  if (!base) return ''
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}_${n}`)) n++
  return `${base}_${n}`
}

/** 概念を外す: その概念を使う関係を外し、その概念を対象にしていた答え方は「まだ」に戻す。 */
export function removeConcept(draft: ThemeDraft, key: string): ThemeDraft {
  return {
    ...draft,
    concepts: draft.concepts.filter((c) => c.key !== key),
    relations: draft.relations.filter((r) => r.from !== key && r.to !== key),
    answers: draft.answers.map((a) =>
      a.target === key || a.kind === key || a.property === key
        ? { ...blankAnswer(a.question), wanted_data: a.wanted_data, by: 'human' as const }
        : a,
    ),
  }
}

export function removeRelation(draft: ThemeDraft, index: number): ThemeDraft {
  return { ...draft, relations: draft.relations.filter((_, i) => i !== index) }
}

/** 照合に出す行の中身（名前・別名・種類）。これが変わったら照合し直す。 */
export function matchSignature(row: Pick<ConceptRow, 'label_ja' | 'label_en' | 'aliases' | 'kind'>): string {
  return JSON.stringify([row.label_ja.trim(), row.label_en.trim(), row.aliases, row.kind])
}

/** `POST /api/themes/match` の 1 行分の答え。 */
export interface MatchResult {
  key: string
  match: TermMatch | null
  use: string
  slug: string
}

/**
 * 機械の照合を手で足した行に反映する（純関数）。`sent`＝照合に出した時点の行の中身
 * （matchSignature）。いま行の中身がそれと違えば（打ち直された）その行は反映しない。
 * `use` は人が触っていない行だけ機械の勧めに置き換える。ID（slug）は、空か機械が付けたもの
 * （`t<n>`）、または既にある用語から外れた行だけ置き換える（人が打った ID は触らない）。
 */
export function applyMatches(
  rows: ConceptRow[],
  results: MatchResult[],
  sent: Record<string, string>,
): ConceptRow[] {
  const byKey = new Map(results.map((m) => [m.key, m]))
  return rows.map((row) => {
    const res = byKey.get(row.key)
    if (!res || sent[row.key] !== matchSignature(row)) return row
    const next: ConceptRow = { ...row, match: res.match }
    if (row.use_by !== 'human') {
      next.use = res.use
      next.use_by = 'machine'
    }
    if (next.use.startsWith('existing:')) next.slug = next.use.slice('existing:'.length)
    else if (!row.slug || /^t\d+$/.test(row.slug) || row.use.startsWith('existing:')) next.slug = res.slug
    return next
  })
}

// ---- つながり（問いの対象から関係でたどれるか） -------------------------------------------

/** 確定に送ってよい関係（機械が止めたものは送らない）。 */
export function usableRelations(draft: ThemeDraft): RelationRow[] {
  return draft.relations.filter((r) => r.problem === null)
}

/** 問いの対象（答え方が決まっている問いの target / kind / property）。 */
export function answerRoots(draft: ThemeDraft): Set<string> {
  const keys = new Set(draft.concepts.map((c) => c.key))
  const out = new Set<string>()
  for (const a of draft.answers) {
    if (!answerComplete(a)) continue
    for (const k of [a.target, a.kind, a.property]) if (k && keys.has(k)) out.add(k)
  }
  return out
}

/**
 * 問いの対象から、関係で（向きを無視して）たどれる概念の key。標準の語と結んだ 2 つの概念も
 * つながる（api も「標準の語と同じ」の線を図に入れて数える）。
 */
export function reachableKeys(draft: ThemeDraft): Set<string> {
  const adj = new Map<string, Set<string>>()
  const link = (a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, new Set())
    if (!adj.has(b)) adj.set(b, new Set())
    adj.get(a)!.add(b)
    adj.get(b)!.add(a)
  }
  for (const r of usableRelations(draft)) link(r.from, r.to)
  for (const c of draft.concepts) if (c.use.startsWith('standard:')) link(c.key, c.use.slice('standard:'.length))
  const seen = new Set<string>()
  const stack = [...answerRoots(draft)]
  while (stack.length > 0) {
    const k = stack.pop()!
    if (seen.has(k)) continue
    seen.add(k)
    for (const n of adj.get(k) ?? []) if (!seen.has(n)) stack.push(n)
  }
  // 標準の語の端（curie）は、たどる途中の中継点。概念の key だけを返す
  const keys = new Set(draft.concepts.map((c) => c.key))
  return new Set([...seen].filter((k) => keys.has(k)))
}

/** 問いにつながっていない概念の key（図に「問いにつながっていません」の印を出す・確定から外す）。 */
export function unlinkedKeys(draft: ThemeDraft): Set<string> {
  const reach = reachableKeys(draft)
  return new Set(draft.concepts.filter((c) => !reach.has(c.key)).map((c) => c.key))
}

/**
 * 確定に送る概念（問いの対象につながるもの）。問いを足す（`extend`）ときは、既に置いたテーマの線を
 * 画面は持たないので、ここで外さず全部送る（つながっているかは api が保存済みの線も見て判定し、
 * だめなら 422 で理由を返す）。印（`unlinkedKeys`）だけは出す。
 */
export function sentConcepts(draft: ThemeDraft): ConceptRow[] {
  if (draft.extend) return draft.concepts
  const reach = reachableKeys(draft)
  return draft.concepts.filter((c) => reach.has(c.key))
}

/** 確定に送る関係（機械が止めていない・両端が送る概念か標準の語）。 */
export function sentRelations(draft: ThemeDraft): RelationRow[] {
  const keys = new Set(sentConcepts(draft).map((c) => c.key))
  const all = new Set(draft.concepts.map((c) => c.key))
  return usableRelations(draft).filter((r) => keys.has(r.from) && (keys.has(r.to) || !all.has(r.to)))
}

export interface ConfirmCounts {
  /** 新しく作る概念。 */
  concepts: number
  relations: number
  /** 「N 件を置く」の N（概念＋関係）。 */
  total: number
}

export function confirmCounts(draft: ThemeDraft): ConfirmCounts {
  const concepts = sentConcepts(draft).filter(conceptMints).length
  const relations = sentRelations(draft).length
  return { concepts, relations, total: concepts + relations }
}

// ---- 答え方（②の上の行） ------------------------------------------------------------

/** 答え方の型。knowledge の 5 つ（向き 3 つ・一部・一種）、data の 3 つ、メモ。 */
export const ANSWER_WAYS = ['lowers', 'raises', 'affects', 'parts', 'kinds', 'count', 'range', 'top', 'memo'] as const
export type AnswerWay = (typeof ANSWER_WAYS)[number]

/** いまの答え方の型（不完全でも op から読む）。 */
export function wayOf(a: AnswerRow): AnswerWay {
  if (a.mode === 'knowledge') {
    if (a.op === 'parts') return 'parts'
    if (a.op === 'kinds') return 'kinds'
    if (a.direction === 'raises') return 'raises'
    if (a.direction === 'affects') return 'affects'
    return 'lowers'
  }
  if (a.mode === 'data') {
    if (a.op === 'range') return 'range'
    if (a.op === 'top') return 'top'
    return 'count'
  }
  return 'memo'
}

/** その型が選ぶ概念: target（knowledge）・kind（件数・範囲・最大）・property（範囲・最大）。 */
export function wayNeeds(way: AnswerWay): { target: boolean; kind: boolean; property: boolean; kindOptional: boolean } {
  return {
    target: ['lowers', 'raises', 'affects', 'parts', 'kinds'].includes(way),
    kind: way === 'count' || way === 'range' || way === 'top',
    property: way === 'range' || way === 'top',
    kindOptional: way === 'range' || way === 'top',
  }
}

/** 型を選び直す。 */
export function setWay(a: AnswerRow, way: AnswerWay): AnswerRow {
  const base = { ...a, by: 'human' as const, value: '', reason: a.reason }
  switch (way) {
    case 'lowers':
    case 'raises':
    case 'affects':
      return { ...base, mode: 'knowledge', op: 'factors', direction: way, kind: '', property: '', memo: null }
    case 'parts':
    case 'kinds':
      return { ...base, mode: 'knowledge', op: way, direction: '', kind: '', property: '', memo: null }
    case 'count':
      return { ...base, mode: 'data', op: 'count', direction: '', target: '', property: '', memo: null }
    case 'range':
    case 'top':
      return { ...base, mode: 'data', op: way, direction: '', target: '', memo: null }
    case 'memo':
      return { ...base, mode: 'memo', op: '', direction: '', target: '', kind: '', property: '', memo: 'human' }
  }
}

/** 概念の選択を変える。 */
export function setAnswerTarget(
  a: AnswerRow,
  patch: Partial<Pick<AnswerRow, 'target' | 'kind' | 'property'>>,
): AnswerRow {
  return { ...a, ...patch, by: 'human' }
}

/** 答え方が決まっているか（knowledge: 対象・data: 種類か項目）。 */
export function answerComplete(a: AnswerRow): boolean {
  if (a.mode === 'knowledge') {
    if (!a.target) return false
    if (a.op === 'factors') return ['lowers', 'raises', 'affects'].includes(a.direction)
    return a.op === 'parts' || a.op === 'kinds'
  }
  if (a.mode === 'data') {
    if (a.op === 'count') return !!a.kind
    if (a.op === 'range' || a.op === 'top') return !!a.property
    return false
  }
  return false
}

/**
 * 知識の問いに、いま確定したら何件で答えられるかの見積もり（api の `knowledge.py` と同じ規則・
 * 閉包は 1 段）: `factors(X, 下げる)` ＝ X を直接下げる線 ∪ X の一部を下げる線。「変える」は
 * 下げる・上げる・変えるの 3 述語すべて。`parts` は一部の線・`kinds` は一種の線。
 */
export function knowledgeEstimate(draft: ThemeDraft, a: AnswerRow): number {
  if (a.mode !== 'knowledge' || !answerComplete(a)) return 0
  const rels = sentRelations(draft)
  if (a.op === 'parts') return rels.filter((r) => r.rel === 'part' && r.to === a.target).length
  if (a.op === 'kinds') return rels.filter((r) => r.rel === 'sub' && r.to === a.target).length
  const accept: RelKind[] = a.direction === 'affects' ? ['lowers', 'raises', 'affects'] : [a.direction as RelKind]
  const via = new Set([a.target, ...rels.filter((r) => r.rel === 'part' && r.to === a.target).map((r) => r.from)])
  const seen = new Set<string>()
  for (const r of rels) if (accept.includes(r.rel) && via.has(r.to)) seen.add(`${r.from}|${r.rel}|${r.to}`)
  return seen.size
}

/** 答え方が土台にする概念（知識は target・件数は kind・範囲と最大は property）。 */
export function answerHost(a: AnswerRow): string {
  if (a.mode === 'knowledge') return a.target
  if (a.mode === 'data') return a.op === 'count' ? a.kind : a.property
  return ''
}

/** 「知識」「データ」「まだ」（決まっていない・メモ）。 */
export function answerState(a: AnswerRow): 'knowledge' | 'data' | 'memo' {
  if (a.mode === 'memo' || !answerComplete(a)) return 'memo'
  return a.mode
}

/** 型ごとに選べる概念（件数の種類・範囲の項目・下げる系の対象は項目）。 */
export function conceptsFor(draft: ThemeDraft, way: AnswerWay, slot: 'target' | 'kind' | 'property'): ConceptRow[] {
  const want: TermKind | null =
    slot === 'kind'
      ? 'class'
      : slot === 'property'
        ? 'property'
        : way === 'lowers' || way === 'raises' || way === 'affects'
          ? 'property'
          : null
  return draft.concepts.filter((c) => want === null || c.kind === want)
}

// ---- 述語・標準の語の IRI（✕ の保存に使う） ---------------------------------------------

/** 下書きの関係 → alignment の述語名（api の ALIGN_RELATIONS のキー）。主語の種別で変わる。 */
export function relationPredicate(rel: RelKind, subjectKind: TermKind): string {
  switch (rel) {
    case 'sub':
      return subjectKind === 'class' ? 'subClassOf' : 'subPropertyOf'
    case 'eq':
      return subjectKind === 'class' ? 'equivalentClass' : 'equivalentProperty'
    case 'quantity':
      return 'hasQuantityKind'
    case 'part':
      return 'isPartOf'
    default:
      return rel
  }
}

/** 標準語のプレフィックス → 名前空間（known_vocabs.yaml と QUDT の量の種類）。 */
const STD_NAMESPACES: Record<string, string> = {
  quantitykind: QUANTITY_KIND_NS,
  cmso: 'http://purls.helmholtz-metadaten.de/cmso/',
  qudt: 'http://qudt.org/schema/qudt/',
  emmo: 'https://w3id.org/emmo#',
  schema: 'https://schema.org/',
  prov: 'http://www.w3.org/ns/prov#',
  dcterms: 'http://purl.org/dc/terms/',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  foaf: 'http://xmlns.com/foaf/0.1/',
  dcat: 'http://www.w3.org/ns/dcat#',
  sosa: 'http://www.w3.org/ns/sosa/',
  bibo: 'http://purl.org/ontology/bibo/',
}

export function standardIri(curie: string, standards?: Record<string, StandardInfo>): string | null {
  const fromApi = standards?.[curie]?.iri
  if (fromApi) return fromApi
  const i = curie.indexOf(':')
  const ns = i > 0 ? STD_NAMESPACES[curie.slice(0, i)] : undefined
  return ns && i < curie.length - 1 ? ns + curie.slice(i + 1) : null
}

/** 概念の却下の鍵: `concept:<正規化した日本語名>`（api の `concept_subject` と同じ規則）。 */
export function conceptDismissKey(label_ja: string): string {
  return `concept:${normalizeName(label_ja)}`
}

/**
 * ✕ が関係の端に使う鍵。既にある用語は IRI・標準の語は IRI・まだ置いていない概念は
 * `concept:<名前>`（api の `concept_end_key` と同じ規則 — 次の下書きで同じ関係を出さない）。
 * `ref` は関係の端そのもの（概念の key か標準の語の curie）。
 */
export function dismissEndKey(
  row: ConceptRow | undefined,
  ref: string,
  standards?: Record<string, StandardInfo>,
): string {
  if (!row) return standardIri(ref, standards) ?? ref
  if (row.use.startsWith('existing:') && row.slug) return `${SV_NS}${row.slug}`
  const m = row.match
  if (m && (m.kind === 'standard' || m.kind === 'quantity')) {
    const iri = standardIri(m.ref, standards)
    if (iri) return iri
  }
  return conceptDismissKey(row.label_ja)
}

// ---- ③ 置く ----------------------------------------------------------------------

export type RowProblem = 'label' | 'slug' | 'duplicate' | 'taken'

/** 行ごとの問題（空なら置ける）。`existingSlugs`＝すでに作ってある用語の ID。 */
export function rowProblems(rows: ConceptRow[], existingSlugs: ReadonlySet<string>): Record<string, RowProblem> {
  const out: Record<string, RowProblem> = {}
  const seen = new Map<string, string>()
  for (const r of rows) {
    if (!r.label_ja.trim()) {
      out[r.key] = 'label'
      continue
    }
    if (!conceptMints(r)) continue
    if (!isValidSlug(r.slug)) out[r.key] = 'slug'
    else if (existingSlugs.has(r.slug)) out[r.key] = 'taken'
    else if (seen.has(r.slug)) out[r.key] = 'duplicate'
    seen.set(r.slug, r.key)
  }
  return out
}

export interface IdLine {
  key: string
  label: string
  slug: string
}

/** 「保存する概念と ID」（新しく作るものだけ）。ID はここで変えられる。 */
export function idList(draft: ThemeDraft): IdLine[] {
  return sentConcepts(draft)
    .filter(conceptMints)
    .map((r) => ({ key: r.key, label: r.label_ja, slug: r.slug }))
}

/** 「置く」を押せない理由（無ければ null）。③が見せる行（送る概念）だけを検べる。 */
export function placeBlocker(draft: ThemeDraft, existingSlugs: ReadonlySet<string>): RowProblem | null {
  const problems = Object.values(rowProblems(sentConcepts(draft), existingSlugs))
  return problems[0] ?? null
}

/** 確定の body（人が直した最終形）。問いにつながらない概念・機械が止めた関係は送らない。 */
export function buildCommitBody(draft: ThemeDraft, lang: string) {
  const concepts = sentConcepts(draft)
  const keys = new Set(concepts.map((c) => c.key))
  const relations = sentRelations(draft)
  return {
    label: draft.label.trim() || defaultLabel(draft.questions),
    ...(draft.extend ? { extend: draft.extend } : {}),
    lang,
    ai_drafted: concepts.some((c) => c.by === 'ai') || relations.some((r) => r.by === 'ai'),
    questions: draft.questions,
    concepts: concepts.map((c) => ({
      key: c.key,
      label_ja: c.label_ja,
      label_en: c.label_en,
      aliases: c.aliases,
      kind: c.kind,
      use: c.use,
      slug: conceptMints(c) ? c.slug : '',
      by: c.by,
    })),
    relations: relations.map((r) => ({ from: r.from, rel: r.rel, to: r.to, reason: r.reason, by: r.by })),
    answers: draft.answers.map((a) => {
      const ok = answerComplete(a) && [a.target, a.kind, a.property].every((k) => !k || keys.has(k))
      // 決まっていない答え方は、メモの問いとして送る（問いは捨てない）
      return ok ? { ...a, memo: null } : { ...a, mode: 'memo' as const, memo: a.memo ?? 'none' }
    }),
  }
}

// ---- 段 ----------------------------------------------------------------------------

/** ①→②: 問いが 1 つ以上ある（テーマ名は任意）。 */
export function canStart(questions: string[]): boolean {
  return questions.length > 0
}

export function nextStep(step: Step): Step {
  return step >= LAST_STEP ? LAST_STEP : ((step + 1) as Step)
}
export function prevStep(step: Step): Step {
  return step <= 1 ? 1 : ((step - 1) as Step)
}

/** 図の線の種類。段をつくる（一種・一部）か、同じ段の横の線か。 */
export function isHierarchy(rel: RelKind): boolean {
  return rel === 'sub' || rel === 'part'
}

/** 概念の表示名（言語に合わせる。英語で英語名が無ければ日本語名）。 */
export function conceptName(row: ConceptRow | undefined, lang: string): string {
  if (!row) return ''
  return lang.startsWith('en') ? row.label_en || row.label_ja : row.label_ja
}
