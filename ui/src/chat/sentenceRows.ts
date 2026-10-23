// 「文」のタブの行（PR 11o）。点の 1 枚の文と、この話の下書き全体から、画面に出す 1 行ずつを組む。純関数。
// 言い方は api の graph_diff.REL_SENTENCE と同じ（「A は B の一種」）。
import type { Point } from './chatApi'
import type { DraftSky } from '../sky/skyApi'

export interface SentenceRow {
  /** 画面に出す文。 */
  text: string
  /** 承認済み（ストアにある）なら true。薄い行（この話の未承認の提案）は false。 */
  approved: boolean
  /** 承認済みの行: 線の両端（IRI）と関係名。 */
  source?: string
  relation?: string
  target?: string
  /** 下書きの鍵（未承認の行と、この話の種の行）と、その提案の手番。 */
  draft?: { draftId: string; from: string; rel: string; to: string; turnId: string | null }
}

const SENTENCE = {
  ja: {
    sub: '{s} は {o} の一種',
    eq: '{s} と {o} は同じもの',
    quantity: '{s} の量は {o}',
    part: '{s} は {o} の一部',
    lowers: '{s} は {o} を下げる',
    raises: '{s} は {o} を上げる',
    affects: '{s} は {o} を変える',
  },
  en: {
    sub: '{s} is a kind of {o}',
    eq: '{s} is the same as {o}',
    quantity: 'The quantity of {s} is {o}',
    part: '{s} is a part of {o}',
    lowers: '{s} lowers {o}',
    raises: '{s} raises {o}',
    affects: '{s} changes {o}',
  },
} as const

/** 下書きの関係 1 本を文にする（知らない関係名は「s —rel→ o」）。 */
export function relSentence(rel: string, s: string, o: string, lang: 'ja' | 'en'): string {
  const tpl = (SENTENCE[lang] as Record<string, string>)[rel]
  return tpl ? tpl.replace('{s}', s).replace('{o}', o) : `${s} - ${o}`
}

/** 点の 1 枚の文 → 行。下書きの鍵（from/rel/to/draft_id）を持つ文は、種（from_seed）でなければ未承認の行。 */
export function rowsOfPoint(p: Point): SentenceRow[] {
  return p.sentences
    .filter((s) => s.text !== '')
    .map((s): SentenceRow => {
      const hasKey = s.from !== undefined && s.rel !== undefined && s.to !== undefined && s.draft_id !== undefined
      const draft = hasKey ? { draftId: s.draft_id as string, from: s.from as string, rel: s.rel as string, to: s.to as string, turnId: s.turn_id } : undefined
      if (hasKey && !s.from_seed) return { text: s.text, approved: false, draft }
      return { text: s.text, approved: true, source: s.source, relation: s.relation, target: s.target, ...(draft ? { draft } : {}) }
    })
}

/** この話の下書き全体 → 未承認の行（外す提案・付け替えで離れる線は除く）。 */
export function rowsOfDraft(d: DraftSky | null, names: ReadonlyMap<string, string>, turnId: string | null, lang: 'ja' | 'en'): SentenceRow[] {
  if (!d) return []
  // 名前が引けない標準の語（curie）は ':' の右だけ
  const name = (k: string) => names.get(k) ?? (k.includes(':') ? k.slice(k.indexOf(':') + 1) : k)
  return d.relations
    .filter((r) => r.state !== 'remove' && r.state !== 'moved')
    .map((r) => ({
      text: relSentence(r.rel, name(r.from), name(r.to), lang),
      // この話の承認済みの種（from_seed）は濃い行。IRI が無いので外すは出さない
      approved: r.from_seed === true,
      draft: { draftId: d.draft_id, from: r.from, rel: r.rel, to: r.to, turnId },
    }))
}

export type SubTab = 'sentences' | 'data'
export const SUBTAB_KEY = 'chat.subtab'

/** 選んだ点の種類: 用語・提案・視点・線＝term／データの種類・データセット＝data／何もなし＝none。 */
export type PointGroup = 'term' | 'data' | 'none'
export function pointGroupOf(pickedId: string | null): PointGroup {
  if (!pickedId) return 'none'
  const head = pickedId.split('|')[0]
  return head === 'k' || head === 'd' ? 'data' : 'term'
}

/** 手で選んだタブ。覚えるのは「同じ種類の点を選んでいるあいだ」だけ。 */
export interface SubTabChoice {
  tab: SubTab
  group: PointGroup
}

/** 既定: 覚えた選びが今の点の種類のものならそれ。無ければ、データの種類・データセット＝「データ」・それ以外＝「文」。 */
export function subTabOf(stored: SubTabChoice | null, pickedId: string | null): SubTab {
  const group = pointGroupOf(pickedId)
  if (stored && stored.group === group) return stored.tab
  return group === 'data' ? 'data' : 'sentences'
}

export const readSubTab = (): SubTabChoice | null => {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(SUBTAB_KEY) ?? 'null')
    if (v && typeof v === 'object') {
      const { tab, group } = v as Record<string, unknown>
      if ((tab === 'sentences' || tab === 'data') && (group === 'term' || group === 'data' || group === 'none')) return { tab, group }
    }
  } catch {
    /* 読めなければ既定 */
  }
  return null
}
export const writeSubTab = (v: SubTabChoice): void => {
  try {
    localStorage.setItem(SUBTAB_KEY, JSON.stringify(v))
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}

/** 行の鍵（光らせた行を覚える）。 */
export const rowKey = (r: SentenceRow): string => (r.draft ? `d|${r.draft.from}|${r.draft.rel}|${r.draft.to}` : `a|${r.source ?? ''}|${r.relation ?? ''}|${r.target ?? ''}`)

/** 外すを出せるか: 未承認の行（AI を呼ばない手番）か、IRI を持つ承認済みの行。 */
export const canRemoveRow = (r: SentenceRow): boolean =>
  (!r.approved && !!r.draft) || (r.approved && /^https?:/.test(r.source ?? '') && !!r.relation && /^https?:/.test(r.target ?? ''))

export interface ScopeLike {
  node: string
  line?: { from: string; rel: string; to: string }
  name: string
}

/** 範囲の行が読み直した行から消えていたら、点だけの範囲に戻す（札の文も点の名前に）。点の名前が無ければ範囲を外す（null）。 */
export function shrinkScope<T extends ScopeLike>(scope: T | null, rows: readonly SentenceRow[], pointName: string): T | null {
  if (!scope?.line) return scope
  const { from, rel, to } = scope.line
  const alive = rows.some((r) => (r.draft && r.draft.from === from && r.draft.rel === rel && r.draft.to === to) || (r.source === from && r.relation === rel && r.target === to))
  if (alive) return scope
  const { line: _line, ...rest } = scope
  void _line
  if (!pointName) return null
  return { ...rest, name: pointName } as T
}
