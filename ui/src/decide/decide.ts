// 「ひとこと決める」の純関数（文の組み立て・候補の並び・選べる関係・帰結の文）。
// 描画（DecidePanel）から切り離してあるので、vitest でそのまま固定できる。LLM は使わない。
//
// 関係は閉集合（api の ALIGN_RELATIONS）から、両端の kind で絞る（lineChoice.relationsFor）。
// 初期選択は無い — 関係は人が選んだときだけ決まる。
import { type AlignRelation, type EndRole, type PickEnd, relationsFor } from '../lineChoice'

export type Translate = (key: string, opts?: Record<string, unknown>) => string

/** api の帰結の試算（POST /api/vocab/decide/preview）。 */
export interface DecidePreview {
  valid: boolean
  /** `valid=false` の理由。 */
  reason: DecideReason | null
  questions: { tool_name: string; title: string; before: number; after: number }[]
  terms: { iri: string; label: string; before: number; after: number }[]
}
export type DecideReason = 'mixed' | 'cycle' | 'unminted' | 'same'

/** 3 つめの選択肢「当てはまるものは無い」（提案を外す）。 */
export const NONE = 'none'
export type DecideChoice = AlignRelation | typeof NONE

/** 候補の並びの優先度: 標準用語 → 既にある用語 → 他のデータセットの種類。 */
const ROLE_ORDER: Record<EndRole, number> = { standard: 0, shared: 1, dataset: 2 }

/** 名前の完全一致の判定に使う正規化（NFKC・小文字・空白をたたむ。api の normalize_label と同じ向き）。 */
export function normalizeName(s: string): string {
  return s.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim()
}

/**
 * 候補の並べ替え。結べないもの（種類と項目の混在・自分自身）は落とす。名前が完全一致するものを
 * 先頭へ、残りは 標準用語 → 既にある用語 → 他のデータセットの種類（同順位は名前順・安定）。
 */
export function orderCandidates(subject: PickEnd, candidates: readonly PickEnd[]): PickEnd[] {
  const own = normalizeName(subject.label)
  const seen = new Set<string>()
  const usable = candidates.filter((c) => {
    if (c.iri === subject.iri || seen.has(c.iri)) return false
    seen.add(c.iri)
    return relationsFor(subject.termKind, c.termKind).length > 0
  })
  const exact = (c: PickEnd) => own !== '' && normalizeName(c.label) === own
  return usable
    .map((c, i) => ({ c, i }))
    .sort((a, b) => {
      const ea = exact(a.c) ? 0 : 1
      const eb = exact(b.c) ? 0 : 1
      if (ea !== eb) return ea - eb
      const ra = ROLE_ORDER[a.c.role]
      const rb = ROLE_ORDER[b.c.role]
      if (ra !== rb) return ra - rb
      return a.c.label.localeCompare(b.c.label, 'ja') || a.i - b.i
    })
    .map((x) => x.c)
}

/** 文面での並び: 一種 → 同じもの → 量（いちばん多く選ばれる「一種です」を先頭に）。 */
const CHOICE_ORDER: readonly AlignRelation[] = [
  'subClassOf',
  'subPropertyOf',
  'equivalentClass',
  'equivalentProperty',
  'hasQuantityKind',
]

/** この組み合わせで選べる関係（空＝結べない）。`relationsFor` で絞り、一種 → 同じもの の順に並べる。 */
export function relationChoices(subject: PickEnd, target: PickEnd): AlignRelation[] {
  return [...relationsFor(subject.termKind, target.termKind)].sort(
    (a, b) => CHOICE_ORDER.indexOf(a) - CHOICE_ORDER.indexOf(b),
  )
}

/** 決めた 1 文（確認・記録に出す）。『Sample』は『試料』の一種 / 『A』の値は『B』と同じ / … */
export function sentence(
  subject: string,
  target: string,
  relation: AlignRelation,
  t: Translate,
): string {
  return t(`decide:sentence.${relation}`, { subject, target })
}

/** 選んだ関係の見出し（ラジオの文面）。 */
export function optionLabel(relation: AlignRelation | typeof NONE, t: Translate): string {
  return t(`decide:option.${relation}`)
}

/** 「〔subject〕は [候補 ▾]」の導入の文（種類・項目・量で言い方を変える）。 */
export function leadText(subject: PickEnd, target: PickEnd | undefined, t: Translate): string {
  const kind =
    subject.termKind === 'property'
      ? target?.termKind === 'quantity'
        ? 'quantity'
        : 'property'
      : 'class'
  return t(`decide:lead.${kind}`, { subject: subject.label })
}

/** 帰結の 1 行: 「問い『…』に答えるデータセット 0 → 1」。変わらないものは「（変わりません）」。 */
export function effectText(preview: DecidePreview, t: Translate): string {
  if (preview.questions.length === 0) return t('decide:effect.noQuestion')
  return preview.questions
    .map((q) =>
      q.before === q.after
        ? t('decide:effect.unchanged', { title: q.title, n: q.after })
        : t('decide:effect.changed', { title: q.title, before: q.before, after: q.after }),
    )
    .join(' / ')
}

/** 公開前の列・種類（まだ store に無い）の帰結の 1 行。数を断定せず、「増える見込み」と今の数だけを言う
 *  （すでに別の経路で答えているデータセットは増えないため）。 */
export function effectAfterPublish(
  questions: { title: string; answering_datasets: number }[],
  t: Translate,
): string {
  if (questions.length === 0) return t('decide:effect.publishNoQuestion')
  return questions
    .map((q) =>
      t('decide:effect.publish', {
        title: q.title,
        n: q.answering_datasets,
      }),
    )
    .join(' / ')
}

/** 結べない理由（日本語）。 */
export function reasonText(reason: DecideReason | null, t: Translate): string {
  return t(`decide:reason.${reason ?? 'unknown'}`)
}

/** 日付（ISO）の日の部分。空なら空。 */
export function dayOf(at: string): string {
  return at ? at.slice(0, 10) : ''
}

/** 試算の状態（`key` はそのときの「主語|結ぶ先|関係」）。 */
export type PreviewState =
  | { key: string; status: 'ok'; preview: DecidePreview }
  | { key: string; status: 'error' }

/** 試算の鍵。結ぶ先・関係が無い／「当てはまるものは無い」なら空（試算しない）。 */
export function previewKeyOf(
  subjectIri: string,
  targetIri: string | undefined,
  picked: DecideChoice | null,
): string {
  return targetIri && picked && picked !== NONE ? `${subjectIri}|${targetIri}|${picked}` : ''
}

/** 今の鍵に対する試算だけを採る（候補・関係を替えたあとの古い結果は捨てる）。 */
export function currentPreview(state: PreviewState | null, key: string): PreviewState | null {
  return state && key !== '' && state.key === key ? state : null
}

/** 「そう決める」を押せるか。結べない（valid=false）試算が今の選択に付いていれば止める。 */
export function decideGate(a: {
  busy: boolean
  hasTarget: boolean
  picked: DecideChoice | null
  current: PreviewState | null
}): { blocked: boolean; canDecide: boolean } {
  const blocked = a.current?.status === 'ok' && !a.current.preview.valid
  return { blocked, canDecide: !a.busy && a.hasTarget && a.picked !== null && !blocked }
}
