// 公開済みの用語の札・知識の線に重ねる「外す提案」の印（PR 11b）。
// sky/skyLayout・skyDraw の既存の関数は変えず、drawSky のあとに ChartPane が重ね描きする（drawDraftOverlay と同じ流儀）。
import type { DraftSky } from '../sky/skyApi'
import { withAlpha, type SkyPalette } from '../sky/skyDraw'
import { conceptEdgeGeom, conceptPillPos, type SkyEdge, type SkyLayout, type TermNode } from '../sky/skyLayout'

type Ctx = CanvasRenderingContext2D

export interface RemovalOverlay {
  /** 外す提案の用語の札（layout.nodes の id）。 */
  nodeIds: string[]
  /** 外す提案の知識の線（layout.edges の要素）。 */
  edges: SkyEdge[]
}

/** サーバの関係名（part など）も、図の関係名（isPartOf など）も同じ線として読む。 */
const REL: Record<string, string> = { part: 'isPartOf' }
const rel = (r: string): string => REL[r] ?? r

/** `proposed[].removals` に当たる、公開済みの用語の札と知識の線。無ければ空。 */
export function removalOverlay(layout: SkyLayout, proposed: readonly Pick<DraftSky, 'removals'>[]): RemovalOverlay {
  const terms = new Set<string>()
  const lines = new Set<string>()
  for (const d of proposed) {
    for (const t of d.removals?.terms ?? []) terms.add(t)
    for (const l of d.removals?.lines ?? []) lines.add(`${l.from}|${rel(l.relation)}|${l.to}`)
  }
  const nodeIds: string[] = []
  if (terms.size > 0) for (const n of layout.nodes.values()) if (n.type === 'term' && terms.has(n.iri)) nodeIds.push(n.id)
  const edges: SkyEdge[] = []
  if (lines.size > 0) {
    for (const e of layout.edges) {
      if (e.kind !== 'concept' || !e.relation) continue
      const a = layout.nodes.get(e.from)
      const b = layout.nodes.get(e.to)
      if (a?.type !== 'term' || b?.type !== 'term') continue
      if (lines.has(`${a.iri}|${e.relation}|${b.iri}`)) edges.push(e)
    }
  }
  return { nodeIds, edges }
}

function roundRect(ctx: Ctx, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function fit(ctx: Ctx, text: string, maxW: number): string {
  if (ctx.measureText(text).width <= maxW) return text
  const chars = [...text]
  let n = chars.length
  while (n > 1 && ctx.measureText(chars.slice(0, n).join('') + '…').width > maxW) n--
  return chars.slice(0, n).join('') + '…'
}

/** 外す提案を重ねる。札は薄く・accent の枠・打ち消し線・「外す提案」。線は薄く・小さな「×」。drawSky のあとに呼ぶ。 */
export function drawRemovals(ctx: Ctx, layout: SkyLayout, pal: SkyPalette, ov: RemovalOverlay, mark: string): void {
  if (ov.nodeIds.length === 0 && ov.edges.length === 0) return
  ctx.save()
  for (const e of ov.edges) {
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    // 下の線を消してから、薄い accent の線を引き直す
    ctx.globalAlpha = 1
    ctx.strokeStyle = pal.paper
    ctx.lineWidth = 3.5
    ctx.beginPath()
    ctx.moveTo(g.x1, g.y1)
    ctx.quadraticCurveTo(g.cx, g.cy, g.x2, g.y2)
    ctx.stroke()
    ctx.globalAlpha = 0.5
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 1.4
    ctx.beginPath()
    ctx.moveTo(g.x1, g.y1)
    ctx.quadraticCurveTo(g.cx, g.cy, g.x2, g.y2)
    ctx.stroke()
    ctx.font = `600 11px ${pal.font}`
    const w = ctx.measureText('×').width + 10
    const pos = e.pill !== undefined ? e.pill : conceptPillPos(layout, g, w)
    if (!pos) continue
    roundRect(ctx, pos.x - w / 2, pos.y - 8, w, 16, 8)
    ctx.globalAlpha = 1
    ctx.fillStyle = pal.surface
    ctx.fill()
    ctx.globalAlpha = 0.5
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.globalAlpha = 0.8
    ctx.fillStyle = pal.accent
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText('×', pos.x, pos.y + 0.5)
  }
  for (const id of ov.nodeIds) {
    const n = layout.nodes.get(id) as TermNode | undefined
    if (!n || n.type !== 'term') continue
    ctx.font = `600 12px ${pal.font}`
    const text = fit(ctx, n.label, n.w - 26)
    const tw = ctx.measureText(text).width
    const w = Math.min(n.w, tw + 26)
    const x = n.x - w / 2
    const y = n.y - n.h / 2
    // 下の札を消してから、薄い札を描く
    ctx.globalAlpha = 1
    ctx.fillStyle = pal.paper
    roundRect(ctx, x - 2, y - 2, w + 4, n.h + 4, 16)
    ctx.fill()
    ctx.globalAlpha = 0.55
    roundRect(ctx, x, y, w, n.h, 14)
    ctx.fillStyle = pal.activitySoft
    ctx.fill()
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 1.4
    ctx.stroke()
    ctx.fillStyle = pal.activity
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, n.x, n.y + 0.5)
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(n.x - tw / 2, n.y + 0.5)
    ctx.lineTo(n.x + tw / 2, n.y + 0.5)
    ctx.stroke()
    ctx.globalAlpha = 1
    ctx.font = `500 9px ${pal.mono}`
    ctx.fillStyle = withAlpha(pal.accent, 1)
    ctx.textBaseline = 'bottom'
    ctx.fillText(mark, n.x, y - 2)
  }
  ctx.restore()
}
