// ⑥「ためす」の「自分の問い」— 閉じた選択から問いを組む純関数
// （ADR upper-structure-shared-terms §2.3「形は閉じた選択で作る」・契約 handoff §5.3）。
//
// 問いの形は「種類／項目／集計（件数・範囲・上位の値）」の選択だけ。題は人が書く。
// SPARQL は api が決定論のテンプレートに流し込む（LLM なし・自由記述の SPARQL なし）。
// ここは画面の選択肢を作る・選択が組み上がっているかを見る・api に送る形にする、だけを持つ。
import type {
  QuestionDraft,
  QuestionOp,
  QuestionSelection,
  TrialQueries,
  UpperItem,
  UpperQuestionsReport,
} from '../api'
import type { DatasetRules, RuleProperty } from '../galleryApi'

export const QUESTION_OPS: readonly QuestionOp[] = ['count', 'range', 'top']

/** 画面で選べる種類 1 つ。 */
export interface KindChoice {
  iri: string
  label: string
}

/** 画面で選べる項目 1 つ。`kinds` はその項目を持つ種類の IRI（絞り込みの候補を出すため）。 */
export interface PropertyChoice {
  iri: string
  label: string
  kinds: string[]
}

export interface QuestionChoices {
  kinds: KindChoice[]
  properties: PropertyChoice[]
}

const LOCAL = /[^/#]+$/

function localOf(iri: string): string {
  return LOCAL.exec(iri)?.[0] ?? iri
}

/** 値を持つ項目か（列から読んだ値・変換した値）。IRI を作る行や別の種類へのつながり、
 *  固定値は「範囲」「上位の値」の対象にならない。 */
function isValueProperty(p: RuleProperty): boolean {
  if (!p.predicate_iri) return false
  if (p.kind !== 'reference' && p.kind !== 'function') return false
  return p.term_type !== 'IRI'
}

/** 設計の規則（GET /rules）→ ⑥で選べる種類と項目。表示名は model.yaml の label →
 *  設計の label → IRI の最後の語、の順。同じ IRI は 1 つにまとめる（出てきた順）。 */
export function questionChoices(rules: DatasetRules | null): QuestionChoices {
  if (!rules) return { kinds: [], properties: [] }
  const labelOf = (iri: string, own?: string) => rules.labels?.[iri] || own || localOf(iri)
  const kinds = new Map<string, KindChoice>()
  const properties = new Map<string, PropertyChoice>()
  for (const map of rules.maps ?? []) {
    const kindIris = map.subject?.class_iris ?? []
    for (const iri of kindIris) {
      if (!kinds.has(iri)) kinds.set(iri, { iri, label: labelOf(iri) })
    }
    for (const p of map.properties ?? []) {
      if (!isValueProperty(p)) continue
      const have = properties.get(p.predicate_iri)
      if (have) {
        for (const k of kindIris) if (!have.kinds.includes(k)) have.kinds.push(k)
      } else {
        properties.set(p.predicate_iri, {
          iri: p.predicate_iri,
          label: labelOf(p.predicate_iri, p.label),
          kinds: [...kindIris],
        })
      }
    }
  }
  return { kinds: [...kinds.values()], properties: [...properties.values()] }
}

/** 選択が組み上がっているか。`count` は種類が要り、`range` / `top` は項目が要る
 *  （種類は絞り込みで任意）。api の `selection_error` と同じ規則。 */
export function selectionReady(sel: QuestionSelection): boolean {
  if (!QUESTION_OPS.includes(sel.op)) return false
  if (sel.op === 'count') return !!sel.kind_iri
  return !!sel.property_iri
}

/** 画面の選択 → api に送る形。op に要らない側は送らない（`count` に項目、など）。 */
export function selectionOf(
  op: QuestionOp,
  kindIri: string,
  propertyIri: string,
): QuestionSelection {
  if (op === 'count') return { op, kind_iri: kindIri }
  const sel: QuestionSelection = { op, property_iri: propertyIri }
  if (kindIri) sel.kind_iri = kindIri
  return sel
}

/** 題が書かれていて選択も組み上がっているときだけ保存できる。 */
export function canSaveQuestion(title: string, sel: QuestionSelection): boolean {
  return title.trim() !== '' && selectionReady(sel)
}

/** 下書きの問い 1 件。id は題と選択から決まる文字列ではなく、足した順に振る
 *  （同じ選択で題違いの問いを足せるように）。 */
export function newQuestion(
  title: string,
  sel: QuestionSelection,
  existing: readonly QuestionDraft[],
): QuestionDraft {
  const taken = new Set(existing.map((q) => q.id))
  let n = existing.length + 1
  while (taken.has(`q${n}`)) n += 1
  const q: QuestionDraft = { id: `q${n}`, title: title.trim(), op: sel.op }
  if (sel.kind_iri) q.kind_iri = sel.kind_iri
  if (sel.property_iri) q.property_iri = sel.property_iri
  return q
}

export function selectionOfQuestion(q: QuestionDraft): QuestionSelection {
  const sel: QuestionSelection = { op: q.op }
  if (q.kind_iri) sel.kind_iri = q.kind_iri
  if (q.property_iri) sel.property_iri = q.property_iri
  return sel
}

/** 同じ選択か（答えを引き直すかどうかの鍵）。 */
export function selectionKey(sel: QuestionSelection): string {
  return [sel.op, sel.kind_iri ?? '', sel.property_iri ?? ''].join('\u0000')
}

/** 「ことばへ写す」の行き先の側。`count` は種類の語、`range` / `top` は項目の語。 */
export function mapSide(op: QuestionOp): 'classes' | 'properties' {
  return op === 'count' ? 'classes' : 'properties'
}

/** 問いの IRI を上位の対応表（GET /api/vocab/upper）で畳んだ先。畳めなければ（自分自身の
 *  まま）null — 上位に共有の語が無いので、鋳造フォームへ案内する。 */
export function upperTarget(
  q: QuestionDraft,
  upper: { classes: Record<string, string>; properties: Record<string, string> },
): string | null {
  const side = mapSide(q.op)
  const iri = side === 'classes' ? q.kind_iri : q.property_iri
  if (!iri) return null
  const mapped = upper[side][iri]
  return mapped && mapped !== iri ? mapped : null
}

/** 問いの答え（/trial-queries/run）に答えが入っているか。 */
export function hasAnswer(res: TrialQueries): boolean {
  return res.available && (res.classes.length > 0 || !!res.range || !!res.top)
}

/** 公開ダイアログの「線 N 本・問い M 件を書きます」。N = 未消費で、書けないと分かって
 *  いない線（`skipped` 済みは数えない）、M = 検査で落ちていない問いの数。どちらも 0 なら
 *  null（出さない）。 */
export function publishWriteCounts(
  upper: readonly UpperItem[],
  questions: readonly QuestionDraft[],
): { lines: number; questions: number } | null {
  const lines = upper.filter((u) => !u.applied_at && !u.skipped).length
  const m = questions.filter((q) => !q.lint_error).length
  return lines === 0 && m === 0 ? null : { lines, questions: m }
}

/** 線の IRI／`property:<map>/<列>` → 画面に出す短い名前。 */
export function shortRef(ref: string): string {
  if (ref.startsWith('property:')) return ref.slice('property:'.length)
  const i = Math.max(ref.lastIndexOf('#'), ref.lastIndexOf('/'))
  return i >= 0 && i < ref.length - 1 ? ref.slice(i + 1) : ref
}

/** 書けなかった線の理由のうち、api が決まった言い方で返すもの（画面が言い換える）。 */
export type SkipReasonKind = 'predicateMissing' | 'subjectMissing' | 'other'

export function skipReasonKind(reason: string): SkipReasonKind {
  if (reason === 'predicate missing') return 'predicateMissing'
  if (reason === 'subject missing') return 'subjectMissing'
  return 'other'
}

/** 公開の応答（`upper_questions`）→ 画面が言うこと。書けた件数と、書けなかったもの
 *  （線の理由・検査で落ちた問い・警告）。何も言うことが無ければ `empty`。 */
export interface UpperQuestionsNotes {
  applied: number
  skipped: { subject: string; term: string; reason: string }[]
  written: number
  lintErrors: { id: string; errors: string[] }[]
  warnings: string[]
  empty: boolean
}

export function describeUpperQuestions(
  report: UpperQuestionsReport | null | undefined,
): UpperQuestionsNotes {
  const applied = Math.max(0, Number(report?.upper?.applied ?? 0) || 0)
  const skipped = (report?.upper?.skipped ?? []).filter((x) => x && x.reason)
  const written = (report?.questions?.written ?? []).length
  const lintErrors = (report?.questions?.lint_errors ?? []).map((e) => ({
    id: e.id,
    errors: e.errors ?? [],
  }))
  const warnings = (report?.warnings ?? []).filter((w) => w.trim() !== '')
  return {
    applied,
    skipped,
    written,
    lintErrors,
    warnings,
    empty:
      applied === 0 &&
      skipped.length === 0 &&
      written === 0 &&
      lintErrors.length === 0 &&
      warnings.length === 0,
  }
}
