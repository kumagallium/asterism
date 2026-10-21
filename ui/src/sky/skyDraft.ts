// 作業中の下書き（テーマ）を、星図の上の帯に描く「提案の札・提案の線」の形にする（純関数）。
//   CanvasModel（workspaceState.buildCanvas＝本物と提案の重ね）→ DraftSky
//   ・薄い（まだ図に入れていない）概念・関係 = thin ／ 図に入れた = solid ／ 外す提案 = remove
//   ・付け替えの新しい線 = retarget ／ 付け替えで離れる元の線 = moved
// 開いていない下書き（`GET /api/themes/drafts/sky`）は state を持たない＝全部 thin（提案）として描く。
import { type ThemeDraft, conceptName } from '../themes/themeDraft'
import type { CanvasModel } from '../workspace/workspaceState'
import type { DraftLineState, DraftSky, DraftState, SkyTerm } from './skyApi'
import type { SkyNode } from './skyLayout'

/** 開いている下書きに id がまだ無いあいだ（保存の前）の札の持ち主。 */
export const NEW_DRAFT_ID = 'new'

export function draftSkyOf(draft: ThemeDraft, model: CanvasModel, lang: string): DraftSky {
  const conceptState = (g: 'add' | 'remove' | undefined): DraftState => (g === 'add' ? 'thin' : g === 'remove' ? 'remove' : 'solid')
  const lineState = (g: 'add' | 'retarget' | 'moved' | 'remove' | undefined): DraftLineState => (g ? (g === 'add' ? 'thin' : g) : 'solid')
  return {
    draft_id: draft.id || NEW_DRAFT_ID,
    theme_label: draft.label,
    questions: draft.questions,
    concepts: model.concepts.map((c) => ({
      key: c.row.key,
      label: conceptName(c.row, lang),
      kind: c.row.kind,
      use: c.row.use,
      state: conceptState(c.ghost),
      ...(c.proposed ? { proposed: true } : {}),
    })),
    relations: model.relations.map((r) => ({
      from: r.row.from,
      to: r.row.to,
      rel: r.row.rel,
      state: lineState(r.ghost),
      index: r.draftIndex,
      problem: r.row.problem !== null,
    })),
  }
}

/**
 * 星図の点（札）を、開いている下書きの概念の key にする（線の端を落とした先）。
 * 提案の札は自分の key・既にある用語の札は、下書きがその用語を使っている概念の key。返事を待つ提案の札・
 * ほかの下書きの札・星などは null。
 */
export function retargetKeyOf(
  node: SkyNode | undefined,
  draft: ThemeDraft | null,
  terms: readonly Pick<SkyTerm, 'iri' | 'slug'>[],
  draftId: string,
): string | null {
  if (!node || !draft) return null
  if (node.type === 'draft') {
    if (node.draftId !== draftId || node.proposed) return null
    return draft.concepts.some((c) => c.key === node.conceptKey) ? node.conceptKey : null
  }
  if (node.type === 'term') {
    const slug = terms.find((t) => t.iri === node.iri)?.slug
    if (!slug) return null
    return draft.concepts.find((c) => c.use === `existing:${slug}`)?.key ?? null
  }
  return null
}

/** 開いている下書きの関係の ID（`from|rel|to`＝relId）を、星図の線の ID（`draftId|from|rel|to`）にする。 */
export const skyEdgeId = (draftId: string, rel: string): string => `${draftId}|${rel}`

/** 星図の線の ID から、開いている下書きの関係の ID を取り出す。ほかの下書きの線・下書きが無いときは null。 */
export function relOfSkyEdge(edgeId: string | null, openDraftId: string): string | null {
  if (!edgeId || !openDraftId) return null
  const head = `${openDraftId}|`
  return edgeId.startsWith(head) ? edgeId.slice(head.length) : null
}

/**
 * 選んだ線（星図の線の ID）の、開いている下書きの関係の添字（無ければ -1）。ほかの下書きの線は -1 —
 * 開いていない下書きの線を、開いている下書きの関係として直さない。
 */
export function edgeIndexOf(draft: ThemeDraft | null, edgeId: string | null, openDraftId: string): number {
  const rel = relOfSkyEdge(edgeId, openDraftId)
  if (!draft || rel === null) return -1
  return draft.relations.findIndex((r) => `${r.from}|${r.rel}|${r.to}` === rel)
}

