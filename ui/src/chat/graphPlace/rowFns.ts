// 右の一覧の行の純関数・定数（LedgerRows.tsx は部品だけを出す）。
import { relSentence } from '../sentenceRows'
import type { LedgerRow } from './knowledgeRelationsApi'

type Lang = 'ja' | 'en'

/** 行の文。api の文（`sentence`）が無ければ、下書き側の言い方で組む。 */
export function rowText(r: LedgerRow, lang: Lang): string {
  return r.sentence ?? (r.rel ? relSentence(r.rel, r.from.name, r.to.name, lang) : `${r.from.name} - ${r.to.name}`)
}

/** 行の識別（開閉の鍵）。取り込み元の関係には鍵が無いので、両端と関係で作る。 */
export const rowId = (r: LedgerRow): string => (r.key ? `${r.source}|${r.key}` : `${r.source}|o|${r.from.id}|${r.to.id}|${r.rel ?? ''}`)

/** 候補に付ける関係の選択肢（下書き側の名前）。向きの無い関係（同じ・競合する）は主語を選ばせない。 */
export const ASSIGN_RELS = ['sub', 'part', 'lowers', 'raises', 'affects', 'makes', 'measures', 'becomes', 'competes', 'has_property', 'eq'] as const
export const isSymmetricRel = (rel: string): boolean => rel === 'competes' || rel === 'eq'

export interface AssignForm {
  rel: string
  subject: 'a' | 'b'
}
