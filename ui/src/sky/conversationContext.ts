// 左の会話は 1 つの欄で、送り先が 3 つの道に分かれる（UI 再設計 PR 8c・契約 pr8c §2）。どの道かは人が選ぶのでなく、
// いま画面にあるものから**決まる**。欄の上の「いま: …」の 1 行は、その道を言う。
//
//   データを置いている（右にかんたんが出ている） → 段で決める
//       ③ 意味をつける で、設計ができている → 'bind'    列の結びつき（/api/consult/graph screen=dataset・bind_column）
//       それ以外                          → 'consult'  設計の相談（/api/design/consult・列の意味・種類名・ID の提案）
//   星図 → 下書きが開いている             → 'draft'    問いの下書きの相談（screen=theme）
//          星を選んでいる                  → 'star'     その星の相談（screen=home）
//          どちらでもない                  → 'ask'      一言は問いとして読む（下書きを始める）
//   星を選んでいても、人が「問いを書く」（チップ・欄の上の切替）を選んでいれば 'ask'（問い）。星の既定は 'star'。
//
// 'bind' の段でも、人が「設計の相談」を選べる（ボタンを押す・「空欄の意味を AI に相談して埋める」）。選びは**その段のあいだだけ**
// 効く（段が変わると既定に戻る）。
import type { HostedState, StageNo } from '../kantan/hostedStage'

export type DataDest = 'bind' | 'consult'
export type ConversationPath = DataDest | 'draft' | 'star' | 'ask'

/** 人が選んだ送り先と、選んだときの段。 */
export interface DestPick {
  recipe: StageNo
  dest: DataDest
}

/** 段が変わったら、人の選びは消す（「その段のあいだだけ」。戻ってきても前の選びを蘇らせない）。 */
export function keepPick(pick: DestPick | null, recipe: StageNo | null): DestPick | null {
  return pick && pick.recipe === recipe ? pick : null
}

/** データを置いているあいだの送り先。 */
export function dataDest(h: Pick<HostedState, 'recipe' | 'bindReady'>, pick: DestPick | null): DataDest {
  const bindable = h.recipe === 3 && h.bindReady
  if (!bindable) return 'consult'
  if (pick && pick.recipe === h.recipe) return pick.dest
  return 'bind'
}

/** 送り先を切り替えられる段か（③で設計ができているときだけ。ほかは 1 つしかない）。 */
export function canSwitch(h: Pick<HostedState, 'recipe' | 'bindReady'>): boolean {
  return h.recipe === 3 && h.bindReady
}

export function conversationPath(o: {
  adding: boolean
  hosted: Pick<HostedState, 'recipe' | 'bindReady'>
  pick: DestPick | null
  draftOpen: boolean
  starSelected: boolean
  /** 人が「問いを書く」を選んでいる（星を選んでいても、一言は問いとして読む）。 */
  writing?: boolean
}): ConversationPath {
  if (o.adding) return dataDest(o.hosted, o.pick)
  if (o.draftOpen) return 'draft'
  if (o.starSelected && !o.writing) return 'star'
  return 'ask'
}

/** 段の名前の丸数字（①〜⑦）。 */
export function stageMark(n: StageNo): string {
  return ['①', '②', '③', '④', '⑤', '⑥', '⑦'][n - 1]
}
