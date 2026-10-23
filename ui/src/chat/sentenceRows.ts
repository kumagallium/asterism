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
  /** 未承認の行: 下書きの鍵と、その提案の手番。 */
  draft?: { draftId: string; from: string; rel: string; to: string; turnId: string | null }
}

const SENTENCE = {
  ja: {
    sub: '{s} は {o} の一種',
    eq: '{s} は {o} と同じもの',
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
  return tpl ? tpl.replace('{s}', s).replace('{o}', o) : `${s} — ${rel} → ${o}`
}

/** 点の 1 枚の文 → 行。下書きの鍵（from/rel/to）を持つ文は未承認の行。 */
export function rowsOfPoint(p: Point): SentenceRow[] {
  return p.sentences
    .filter((s) => s.text !== '')
    .map((s) =>
      s.from !== undefined && s.rel !== undefined && s.to !== undefined && s.draft_id !== undefined
        ? { text: s.text, approved: false, draft: { draftId: s.draft_id, from: s.from, rel: s.rel, to: s.to, turnId: s.turn_id } }
        : { text: s.text, approved: true, source: s.source, relation: s.relation, target: s.target },
    )
}

/** この話の下書き全体 → 未承認の行（外す提案・付け替えで離れる線は除く）。 */
export function rowsOfDraft(d: DraftSky | null, names: ReadonlyMap<string, string>, turnId: string | null, lang: 'ja' | 'en'): SentenceRow[] {
  if (!d) return []
  const name = (k: string) => names.get(k) ?? k
  return d.relations
    .filter((r) => r.state !== 'remove' && r.state !== 'moved')
    .map((r) => ({
      text: relSentence(r.rel, name(r.from), name(r.to), lang),
      approved: false,
      draft: { draftId: d.draft_id, from: r.from, rel: r.rel, to: r.to, turnId },
    }))
}

export type SubTab = 'sentences' | 'data'
export const SUBTAB_KEY = 'chat.subtab'

/** 既定: 覚えた選びがあればそれ。無ければ、データの種類・データセットを押したら「データ」・それ以外（用語・提案の札・何も押さない）は「文」。 */
export function subTabOf(stored: string | null, pickedId: string | null): SubTab {
  if (stored === 'sentences' || stored === 'data') return stored
  const head = pickedId?.split('|')[0]
  return head === 'k' || head === 'd' ? 'data' : 'sentences'
}

export const readSubTab = (): string | null => {
  try {
    return localStorage.getItem(SUBTAB_KEY)
  } catch {
    return null
  }
}
export const writeSubTab = (v: SubTab): void => {
  try {
    localStorage.setItem(SUBTAB_KEY, v)
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}
