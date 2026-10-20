// 「知りたいことから作る」（テーマ・6 段）の純関数。描画（ThemeWizard）から切り離してあるので、
// 段の進める条件・slug の導出・閉じた JSON の検証・実行文の材料・ID 一覧・確定の body を vitest で
// そのまま固定できる。LLM は使わない（AI の下書きは api が作り、ここは検証と整形だけ）。
//
// 設計の芯（design.md §3・PR 4 契約）:
//   - AI は閉じた JSON だけ。機械の完全一致（標準用語・既にある用語）が AI の use に勝つ。
//   - 用語はテーマの持ち物ではない（テーマを外しても用語・関係・問いは残る）。
//   - 初期選択なし: 関係は「そう／違う」を人が選ぶまで未決。数え方が決まらない問いは「メモの問い」。
//   - ID は⑥で見せる（意味を先に・ID を後に）。slug の導出は vocabQuestion.slugFromText と同じ。
import { isValidSlug, slugFromText } from '../vocabQuestion'

export type Step = 1 | 2 | 3 | 4 | 5 | 6
export const LAST_STEP: Step = 6

export type TermKind = 'class' | 'property'
export type RelationKind = 'sub' | 'eq' | 'quantity'
export type RelationProblem = 'same' | 'mixed' | 'cycle' | 'quantity_target'
export type MemoCode = 'op' | 'ai' | 'none' | 'human'

/** 数え方の閉集合。先頭 3 つが今の宣言ツールで答えられる（残りは「メモの問い」へ）。 */
export const SELECTION_OPS = ['count', 'range', 'top', 'filter', 'group', 'trend', 'join'] as const
export type SelectionOp = (typeof SELECTION_OPS)[number]
export const FITTING_OPS: readonly string[] = ['count', 'range', 'top']

/** 機械が見つけた「既にあるもの」（完全一致）。 */
export interface TermMatch {
  kind: 'existing' | 'standard' | 'quantity'
  /** existing＝slug・standard／quantity＝curie。 */
  ref: string
  label: string
  matched_by: 'label' | 'alias'
}

/** ③の 1 行（用語 1 つ）。`key` は行の識別子で、関係・問いはこれで結ぶ。 */
export interface TermRow {
  key: string
  label_ja: string
  label_en: string
  aliases: string[]
  kind: TermKind
  /** `new` ／ `existing:<slug>` ／ `standard:<curie>`。 */
  use: string
  slug: string
  reason: string
  match: TermMatch | null
  by: 'ai' | 'human'
  /** `use` を決めたのは誰か。`human`＝人が select を触った（機械の照合で上書きしない）。 */
  use_by?: 'machine' | 'human'
}

/** ④の 1 行（関係 1 つ）。`object` は行の key か標準用語の curie。`ok` は人の答え（null＝未決）。 */
export interface RelationRow {
  subject: string
  object: string
  relation: RelationKind
  problem: RelationProblem | null
  by: 'ai' | 'human'
  ok: boolean | null
}

/** ⑤の 1 行（問い 1 つの「数え方 × 種類 × 項目」）。`kind` / `property` は行の key。 */
export interface SelectionRow {
  question: string
  op: SelectionOp | ''
  kind: string
  property: string
  value: string
  fits: boolean
  memo: MemoCode | null
  reason: string
  by: 'ai' | 'human'
}

export interface GroupSuggestion {
  label_ja: string
  /** まとめられる側の行の key。 */
  children: string[]
}

export interface StandardInfo {
  label: string
  kind: string
}

/** POST /api/themes/draft の返り値（機械の後処理まで済んだ行と、閉じた JSON そのもの）。 */
export interface DraftResponse {
  ai: Record<string, unknown>
  terms: TermRow[]
  relations: RelationRow[]
  selections: SelectionRow[]
  suggested_questions: string[]
  standards: Record<string, StandardInfo>
  groups: GroupSuggestion[]
}

/** 保存する下書き。`ai` は閉じた JSON に、機械が足した standards / suggested_questions / groups を添える。 */
export interface ThemeDraft {
  id: string
  label: string
  step: Step
  /** 既にあるテーマの id（「問いを足す」: 置くと、そのテーマに問いが加わる）。 */
  extend?: string
  questions: string[]
  ai: (Record<string, unknown> & Partial<Pick<DraftResponse, 'standards' | 'suggested_questions' | 'groups'>>) | null
  terms: TermRow[]
  relations: RelationRow[]
  selections: SelectionRow[]
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

// ---- 閉じた JSON の検証（api の返り値を信じすぎない） ------------------------------

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const isStr = (v: unknown): v is string => typeof v === 'string'

/**
 * api の返り値が、画面の読める形か。形が合わなければ `null`（画面は手入力へ戻る）。
 * 列挙（kind・relation・op）を外れた行は、AI の答えとして受けない。
 */
export function validateDraftResponse(raw: unknown): DraftResponse | null {
  if (!isObj(raw) || !Array.isArray(raw.terms) || !Array.isArray(raw.relations)) return null
  if (!Array.isArray(raw.selections)) return null
  const terms: TermRow[] = []
  for (const t of raw.terms) {
    if (!isObj(t) || !isStr(t.key) || !isStr(t.label_ja) || !isStr(t.use) || !isStr(t.slug)) return null
    if (t.kind !== 'class' && t.kind !== 'property') return null
    terms.push({
      key: t.key,
      label_ja: t.label_ja,
      label_en: isStr(t.label_en) ? t.label_en : '',
      aliases: Array.isArray(t.aliases) ? t.aliases.filter(isStr) : [],
      kind: t.kind,
      use: t.use,
      slug: t.slug,
      reason: isStr(t.reason) ? t.reason : '',
      match: isObj(t.match) && isStr(t.match.ref) ? (t.match as unknown as TermMatch) : null,
      by: 'ai',
    })
  }
  const relations: RelationRow[] = []
  for (const r of raw.relations) {
    if (!isObj(r) || !isStr(r.subject) || !isStr(r.object)) return null
    if (r.relation !== 'sub' && r.relation !== 'eq' && r.relation !== 'quantity') return null
    relations.push({
      subject: r.subject,
      object: r.object,
      relation: r.relation,
      problem: isStr(r.problem) ? (r.problem as RelationProblem) : null,
      by: 'ai',
      ok: null,
    })
  }
  const selections: SelectionRow[] = []
  for (const s of raw.selections) {
    if (!isObj(s) || !isStr(s.question)) return null
    const op = isStr(s.op) && (SELECTION_OPS as readonly string[]).includes(s.op) ? (s.op as SelectionOp) : ''
    selections.push({
      question: s.question,
      op,
      kind: isStr(s.kind) ? s.kind : '',
      property: isStr(s.property) ? s.property : '',
      value: isStr(s.value) ? s.value : '',
      fits: s.fits === true && op !== '' && FITTING_OPS.includes(op),
      memo: op === '' && s.memo == null ? 'none' : ((isStr(s.memo) ? s.memo : null) as MemoCode | null),
      reason: isStr(s.reason) ? s.reason : '',
      by: 'ai',
    })
  }
  return {
    ai: isObj(raw.ai) ? raw.ai : {},
    terms,
    relations,
    selections,
    suggested_questions: Array.isArray(raw.suggested_questions) ? raw.suggested_questions.filter(isStr) : [],
    standards: isObj(raw.standards) ? (raw.standards as Record<string, StandardInfo>) : {},
    groups: Array.isArray(raw.groups)
      ? (raw.groups.filter(
          (g) => isObj(g) && isStr(g.label_ja) && Array.isArray(g.children),
        ) as unknown as GroupSuggestion[])
      : [],
  }
}

/** AI の答えを下書きに取り込む（②→③）。関係は未決（`ok: null`）、機械が止めたものは最初から「違う」。 */
export function applyDraftResponse(draft: ThemeDraft, res: DraftResponse): ThemeDraft {
  return {
    ...draft,
    ai: { ...res.ai, standards: res.standards, suggested_questions: res.suggested_questions, groups: res.groups },
    terms: res.terms,
    relations: res.relations.map((r) => ({ ...r, ok: r.problem ? false : null })),
    selections: res.selections,
  }
}

/** AI を使わず手で書く（②→③）。問いごとの行だけ作り、用語は③で足す。 */
export function startManual(draft: ThemeDraft): ThemeDraft {
  return {
    ...draft,
    ai: null,
    terms: [],
    relations: [],
    selections: draft.questions.map(
      (question): SelectionRow => ({
        question,
        op: '',
        kind: '',
        property: '',
        value: '',
        fits: false,
        memo: 'none',
        reason: '',
        by: 'human',
      }),
    ),
  }
}

/** ①で問いを直したとき、⑤の行を問いの並びに合わせる（消えた問いの行は落とし、増えた問いは空の行を足す）。 */
export function syncSelections(selections: SelectionRow[], questions: string[]): SelectionRow[] {
  const by = new Map(selections.map((s) => [s.question, s]))
  return questions.map(
    (q) =>
      by.get(q) ?? {
        question: q,
        op: '',
        kind: '',
        property: '',
        value: '',
        fits: false,
        memo: 'none',
        reason: '',
        by: 'human',
      },
  )
}

export function draftStandards(draft: ThemeDraft): Record<string, StandardInfo> {
  return draft.ai?.standards ?? {}
}
export function draftSuggestions(draft: ThemeDraft): string[] {
  return draft.ai?.suggested_questions ?? []
}
export function draftGroups(draft: ThemeDraft): GroupSuggestion[] {
  return draft.ai?.groups ?? []
}

// ---- ③ 用語 ----------------------------------------------------------------------

/** この行は新しい用語を作るか（`new` ／ 標準の語と結んで作る）。既にある用語を使う行は作らない。 */
export function termMints(row: Pick<TermRow, 'use'>): boolean {
  return !row.use.startsWith('existing:')
}

export function nextKey(rows: { key: string }[]): string {
  let n = rows.length + 1
  const keys = new Set(rows.map((r) => r.key))
  while (keys.has(`t${n}`)) n++
  return `t${n}`
}

export function newTermRow(rows: TermRow[], kind: TermKind): TermRow {
  return {
    key: nextKey(rows),
    label_ja: '',
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

/** 「どうする」の選べるもの。機械が完全一致を見つけた行だけ、その候補が増える。 */
export type UseOption = { value: string; kind: 'new' | 'existing' | 'standard' | 'quantity' }
export function termUseOptions(row: TermRow): UseOption[] {
  const out: UseOption[] = [{ value: 'new', kind: 'new' }]
  if (row.match?.kind === 'existing') out.push({ value: `existing:${row.match.ref}`, kind: 'existing' })
  if (row.match?.kind === 'standard') out.push({ value: `standard:${row.match.ref}`, kind: 'standard' })
  if (row.match?.kind === 'quantity') out.push({ value: `standard:${row.match.ref}`, kind: 'quantity' })
  return out
}

/** 日本語・英語名から決まる ID の既定（英語名 → 別名の順。作れなければ空・人が⑥で入れる）。 */
export function suggestRowSlug(row: Pick<TermRow, 'label_en' | 'aliases'>, taken: Iterable<string>): string {
  const base = slugFromText(row.label_en) || row.aliases.map(slugFromText).find(Boolean) || ''
  if (!base) return ''
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}_${n}`)) n++
  return `${base}_${n}`
}

export type RowProblem = 'label' | 'slug' | 'duplicate' | 'taken'

/** 行ごとの問題（空なら置ける）。`existingSlugs`＝すでに作ってある用語の ID。 */
export function rowProblems(rows: TermRow[], existingSlugs: ReadonlySet<string>): Record<string, RowProblem> {
  const out: Record<string, RowProblem> = {}
  const seen = new Map<string, string>()
  for (const r of rows) {
    if (!r.label_ja.trim()) {
      out[r.key] = 'label'
      continue
    }
    if (!termMints(r)) continue
    if (!isValidSlug(r.slug)) out[r.key] = 'slug'
    else if (existingSlugs.has(r.slug)) out[r.key] = 'taken'
    else if (seen.has(r.slug)) out[r.key] = 'duplicate'
    seen.set(r.slug, r.key)
  }
  return out
}

/** 日本語名の変更に合わせて、関係・問いが結ぶ key は変わらない（key で結んであるため）。 */
export function renameRow(rows: TermRow[], key: string, patch: Partial<TermRow>): TermRow[] {
  return rows.map((r) => (r.key === key ? { ...r, ...patch } : r))
}

/** どうするを変える。既にある用語を使うなら、その用語の ID に寄せる。 */
export function chooseUse(rows: TermRow[], key: string, use: string): TermRow[] {
  return rows.map((r) => {
    if (r.key !== key) return r
    if (use.startsWith('existing:')) return { ...r, use, slug: use.slice('existing:'.length), use_by: 'human' }
    return { ...r, use, use_by: 'human' }
  })
}

/** 照合に出す行の中身（名前・別名・種類）。これが変わったら照合し直す。 */
export function matchSignature(row: Pick<TermRow, 'label_ja' | 'label_en' | 'aliases' | 'kind'>): string {
  return JSON.stringify([row.label_ja.trim(), row.label_en.trim(), row.aliases, row.kind])
}

/** `POST /api/themes/match` の 1 行分の答え（機械の照合と勧め）。 */
export interface MatchResult {
  key: string
  match: TermMatch | null
  use: string
}

/**
 * 機械の照合を行に反映する（純関数）。`sent`＝照合に出した時点の行の中身（matchSignature）。
 * いま行の中身がそれと違えば（打ち直された）その行は反映しない。`match` は常に機械の答え。
 * `use` は人が触っていない行だけ機械の勧めに置き換える。人が選んだ `use` は、その候補が
 * まだ選べる（照合に残っている）かぎり上書きしない・選べなくなったら勧めに戻す。
 */
export function applyMatches(
  rows: TermRow[],
  results: MatchResult[],
  sent: Record<string, string>,
  existingSlugs: Iterable<string> = [],
): TermRow[] {
  const byKey = new Map(results.map((m) => [m.key, m]))
  const taken = new Set(existingSlugs)
  return rows.map((row) => {
    const res = byKey.get(row.key)
    if (!res || sent[row.key] !== matchSignature(row)) return row
    const next: TermRow = { ...row, match: res.match }
    const stillOffered = termUseOptions(next).some((o) => o.value === row.use)
    if (row.use_by === 'human' && stillOffered) return next
    next.use = res.use
    next.use_by = 'machine'
    if (res.use.startsWith('existing:')) {
      next.slug = res.use.slice('existing:'.length)
    } else if (row.use.startsWith('existing:')) {
      // 既にある用語から外れた: その用語の ID を引きずらない
      const others = rows.filter((r) => r.key !== row.key).map((r) => r.slug)
      next.slug = suggestRowSlug(row, [...others, ...taken])
    }
    return next
  })
}

/** 行を外す（その行を使う関係・問いの選択も外す）。 */
export function removeRow(draft: ThemeDraft, key: string): ThemeDraft {
  return {
    ...draft,
    terms: draft.terms.filter((r) => r.key !== key),
    relations: draft.relations.filter((r) => r.subject !== key && r.object !== key),
    selections: draft.selections.map((s) => ({
      ...s,
      kind: s.kind === key ? '' : s.kind,
      property: s.property === key ? '' : s.property,
    })),
  }
}

// ---- ④ 関係 ----------------------------------------------------------------------

/** この関係を置けるか: 主語が新しく作る用語で、機械が止めていない。 */
export function relationApplies(rel: RelationRow, rows: TermRow[]): boolean {
  const subject = rows.find((r) => r.key === rel.subject)
  return !!subject && termMints(subject) && rel.problem === null
}

/** 標準の語と同じ／量の種類と結ぶ、③で選んだ行の関係（人の「そう／違う」は要らない）。 */
export function implicitRelations(rows: TermRow[]): { subject: string; curie: string; quantity: boolean }[] {
  return rows
    .filter((r) => r.use.startsWith('standard:'))
    .map((r) => ({
      subject: r.key,
      curie: r.use.slice('standard:'.length),
      quantity: r.match?.kind === 'quantity' || r.use.startsWith('standard:quantitykind:'),
    }))
}

/** ④を進めてよいか: 置ける関係は、すべて「そう／違う」を選んである（初期選択なし）。 */
export function relationsReady(rels: RelationRow[], rows: TermRow[]): boolean {
  return rels.every((r) => !relationApplies(r, rows) || r.ok !== null)
}

export function setRelation(rels: RelationRow[], index: number, ok: boolean): RelationRow[] {
  return rels.map((r, i) => (i === index ? { ...r, ok } : r))
}

/** 「そう」と答えた、置く関係。 */
export function acceptedRelations(rels: RelationRow[], rows: TermRow[]): RelationRow[] {
  return rels.filter((r) => r.ok === true && relationApplies(r, rows))
}

/** まとめ名の chip を押した: 新しい種類の行と、まとめられる側の関係（そう）を足す。 */
export function addGroup(draft: ThemeDraft, group: GroupSuggestion): ThemeDraft {
  const row: TermRow = {
    ...newTermRow(draft.terms, 'class'),
    label_ja: group.label_ja,
    by: 'human',
  }
  const taken = draft.terms.map((r) => r.slug)
  row.slug = suggestRowSlug(row, taken)
  return {
    ...draft,
    terms: [...draft.terms, row],
    relations: [
      ...draft.relations,
      ...group.children.map(
        (child): RelationRow => ({ subject: child, object: row.key, relation: 'sub', problem: null, by: 'human', ok: true }),
      ),
    ],
  }
}

/** まとめ名の提案が出せるか: 同じ親を要る用語が 2 つ以上（api が数える）で、まだ足していない。 */
export function visibleGroups(groups: GroupSuggestion[], rows: TermRow[]): GroupSuggestion[] {
  return groups.filter(
    (g) =>
      g.children.filter((c) => rows.some((r) => r.key === c)).length >= 2 &&
      !rows.some((r) => r.label_ja === g.label_ja),
  )
}

export interface StructureLine {
  depth: number
  /** 行の key か標準用語の curie。 */
  ref: string
  label: string
  tag: 'new' | 'existing' | 'standard'
  /** この用語に付く「同じもの」「量」の関係（標準用語への線）。 */
  notes: { relation: 'eq' | 'quantity'; curie: string }[]
}

/** 「このテーマの上位構造」（字下げ）: 受けた関係だけから、親 → 子の順に並べる。 */
export function upperStructure(draft: ThemeDraft): StructureLine[] {
  const rows = draft.terms
  const label = (ref: string): string => rows.find((r) => r.key === ref)?.label_ja ?? ref
  const tagOf = (ref: string): StructureLine['tag'] => {
    const row = rows.find((r) => r.key === ref)
    if (!row) return 'standard'
    return termMints(row) ? 'new' : 'existing'
  }
  const subs = acceptedRelations(draft.relations, rows).filter((r) => r.relation === 'sub')
  const children = new Map<string, string[]>()
  const hasParent = new Set<string>()
  for (const r of subs) {
    children.set(r.object, [...(children.get(r.object) ?? []), r.subject])
    hasParent.add(r.subject)
  }
  const notesOf = (key: string): StructureLine['notes'] => [
    ...acceptedRelations(draft.relations, rows)
      .filter((r) => r.subject === key && r.relation !== 'sub')
      .map((r) => ({ relation: r.relation as 'eq' | 'quantity', curie: r.object })),
    ...implicitRelations(rows)
      .filter((r) => r.subject === key)
      .map((r) => ({ relation: (r.quantity ? 'quantity' : 'eq') as 'eq' | 'quantity', curie: r.curie })),
  ]
  const out: StructureLine[] = []
  const walk = (ref: string, depth: number, seen: Set<string>) => {
    if (seen.has(ref)) return
    seen.add(ref)
    out.push({ depth, ref, label: label(ref), tag: tagOf(ref), notes: notesOf(ref) })
    for (const c of [...(children.get(ref) ?? [])].sort((a, b) => label(a).localeCompare(label(b), 'ja'))) {
      walk(c, depth + 1, seen)
    }
  }
  const involved = new Set<string>()
  for (const r of subs) {
    involved.add(r.subject)
    involved.add(r.object)
  }
  for (const row of rows) if (notesOf(row.key).length > 0) involved.add(row.key)
  const roots = [...involved].filter((k) => !hasParent.has(k))
  const seen = new Set<string>()
  // 木になっているもの（子を持つ親）を先に、標準用語との関係だけを持つ行を後に。
  const byTree = (a: string, b: string) =>
    Number(children.has(b)) - Number(children.has(a)) || label(a).localeCompare(label(b), 'ja')
  for (const root of roots.sort(byTree)) walk(root, 0, seen)
  return out
}

// ---- ⑤ ためす --------------------------------------------------------------------

export const needsKind = (op: string): boolean => ['count', 'filter', 'group', 'trend', 'join'].includes(op)
export const needsProperty = (op: string): boolean => ['range', 'top', 'filter', 'group', 'trend', 'join'].includes(op)
export const needsValue = (op: string): boolean => op === 'filter'
export const kindOptional = (op: string): boolean => op === 'range' || op === 'top'

/** 数え方・種類・項目が、その数え方に足りているか。 */
export function selectionComplete(s: SelectionRow): boolean {
  if (s.op === '') return false
  if (needsKind(s.op) && !s.kind) return false
  if (needsProperty(s.op) && !s.property) return false
  if (needsValue(s.op) && !s.value.trim()) return false
  return true
}

/** 公開できる型か（今の宣言ツールで答えられる数え方）。 */
export function selectionFits(s: SelectionRow): boolean {
  return selectionComplete(s) && FITTING_OPS.includes(s.op)
}

/** 型に合わない・決まっていない問いは「メモの問い」（公開しない）。 */
export function isMemo(s: SelectionRow): boolean {
  return s.memo !== null || !selectionFits(s)
}

export function memoReason(s: SelectionRow): MemoCode {
  if (s.memo) return s.memo
  if (s.op === '' || !selectionComplete(s)) return 'none'
  return FITTING_OPS.includes(s.op) ? 'ai' : 'op'
}

/** ⑤を進めてよいか: すべての問いが、型に合うか、メモとして残すと決めてある。 */
export function selectionsReady(selections: SelectionRow[]): boolean {
  return selections.every((s) => s.memo !== null || selectionComplete(s))
}

/** 数え方を選び直した行。型に合うなら「メモ」の印を外し、合わなければ機械の印にする。 */
export function setSelection(s: SelectionRow, patch: Partial<SelectionRow>): SelectionRow {
  const next: SelectionRow = { ...s, ...patch, by: 'human' }
  if (patch.memo === undefined) {
    next.memo = selectionComplete(next) && !FITTING_OPS.includes(next.op) ? 'op' : null
  }
  next.fits = selectionFits(next) && next.memo === null
  return next
}

/** 実行文を組むための材料（api の describe に渡す）。表示名は行の日本語／英語名。 */
export function selectionPayload(
  s: SelectionRow,
  rows: TermRow[],
  lang: string,
): { op: string; kind: string; property: string; value: string } {
  const name = (key: string): string => {
    const r = rows.find((x) => x.key === key)
    if (!r) return ''
    return lang.startsWith('en') ? r.label_en || r.label_ja : r.label_ja
  }
  return { op: s.op, kind: name(s.kind), property: name(s.property), value: s.value }
}

/**
 * 「いまは N データセットが答える」。新しく作る用語はまだ関係が無いので 0、既にある用語を使うなら
 * その用語を答えるデータセットの数。宿す用語（件数＝種類・範囲／上位の値＝項目）で数える。
 * `answering`＝既にある用語の slug → 答えるデータセット数。
 */
export function answeringNow(s: SelectionRow, rows: TermRow[], answering: Record<string, number>): number {
  const hostKey = s.op === 'count' ? s.kind : s.op === 'range' || s.op === 'top' ? s.property : ''
  const host = rows.find((r) => r.key === hostKey)
  if (!host || termMints(host)) return 0
  return answering[host.slug] ?? 0
}

// ---- ⑥ 置く ----------------------------------------------------------------------

export interface IdLine {
  key: string
  label: string
  slug: string
}

/** 「保存する用語と ID」（新しく作るものだけ）。ID はここで変えられる。 */
export function idList(rows: TermRow[]): IdLine[] {
  return rows.filter(termMints).map((r) => ({ key: r.key, label: r.label_ja, slug: r.slug }))
}

export interface PlaceSummary {
  terms: number
  lines: number
  published: number
  memo: number
}

export function placeSummary(draft: ThemeDraft): PlaceSummary {
  const lines =
    acceptedRelations(draft.relations, draft.terms).length + implicitRelations(draft.terms).length
  const memo = draft.selections.filter(isMemo).length
  return {
    terms: draft.terms.filter(termMints).length,
    lines,
    published: draft.selections.length - memo,
    memo,
  }
}

/** 確定の body（人が直した最終形）。置けない関係・「違う」と答えた関係は送らない。 */
export function buildCommitBody(draft: ThemeDraft, lang: string) {
  return {
    label: draft.label.trim(),
    ...(draft.extend ? { extend: draft.extend } : {}),
    lang,
    ai_drafted: draft.ai !== null,
    questions: draft.questions,
    terms: draft.terms,
    relations: acceptedRelations(draft.relations, draft.terms).map(({ subject, object, relation }) => ({
      subject,
      object,
      relation,
    })),
    selections: draft.selections.map((s) => ({
      ...s,
      // 型に合わない問いは、機械の印（memo）を付けて送る（サーバも同じ判断をする）。
      memo: isMemo(s) ? memoReason(s) : null,
    })),
  }
}

// ---- 段の進める条件 ----------------------------------------------------------------

export interface StepContext {
  /** すでに作ってある用語の ID。 */
  existingSlugs: ReadonlySet<string>
}

/** その段から次へ進めるか（「次へ」のボタンの有効・無効）。 */
export function canAdvance(draft: ThemeDraft, step: Step, ctx: StepContext): boolean {
  switch (step) {
    case 1:
      return draft.label.trim() !== '' && draft.questions.length > 0
    case 2:
      // AI に読ませた、または「手で書く」を選んだ（⑤の行が問いの数だけある）。
      return draft.selections.length === draft.questions.length && draft.selections.length > 0
    case 3: {
      const problems = rowProblems(draft.terms, ctx.existingSlugs)
      // ID は⑥で直せる。ここでは名前が空の行だけを止める（ID の形は⑥の「置く」が止める）。
      return Object.values(problems).every((p) => p !== 'label')
    }
    case 4:
      return relationsReady(draft.relations, draft.terms)
    case 5:
      return selectionsReady(draft.selections)
    case 6:
      return Object.keys(rowProblems(draft.terms, ctx.existingSlugs)).length === 0
  }
}

/** ⑥の「置く」を押せない理由（無ければ null）。 */
export function placeBlocker(draft: ThemeDraft, ctx: StepContext): RowProblem | null {
  const problems = Object.values(rowProblems(draft.terms, ctx.existingSlugs))
  return problems[0] ?? null
}

export function nextStep(step: Step): Step {
  return step >= LAST_STEP ? LAST_STEP : ((step + 1) as Step)
}
export function prevStep(step: Step): Step {
  return step <= 1 ? 1 : ((step - 1) as Step)
}
