// 1 枚の知識の図の描画（canvas 2D・PR 11f）。配置は `graphLayout.ts`（描画は何も決めない）。
//
// 紙の地（格子・帯・地平線は無し）に、札（概念）・札の直下に吊る星（データ）・最下段の名前のない星・線と小さな印の札を描く。
// 薄い（alpha 0.55）＝提案、濃い＝確認済み。外す提案は accent の枠＋打ち消し線。点線は使わない。
// 部品（パレット・輪・件数の書式・当たり判定）は `sky/skyDraw.ts` から流用する。
import {
  BORN_GROW_MS,
  BORN_RING_MS,
  draftPillText,
  withAlpha,
  type DrawState,
  type SkyLabels,
  type SkyPalette,
} from '../../sky/skyDraw'
import { draftEdgeId, type DraftEdgeInfo, type DraftNode, type HubNode, type KindNode, type SkyEdge, type SkyLayout, type SkyNode, type TermNode } from '../../sky/skyLayout'
import { geomPoints } from './graphLayout'

type Ctx = CanvasRenderingContext2D

export interface GraphLabels extends SkyLabels {
  /** 最下段の見出し（概念に結ばれていないデータ）。 */
  noName: string
}

export interface GraphDrawState extends DrawState {
  /** 外す提案の用語の札（layout.nodes の id）。accent の枠と打ち消し線で描く。 */
  removeNodeIds?: ReadonlySet<string>
}

const THIN_ALPHA = 0.55

function roundRect(ctx: Ctx, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function star4(ctx: Ctx, x: number, y: number, r: number): void {
  ctx.beginPath()
  for (let i = 0; i < 8; i++) {
    const a = (Math.PI / 4) * i - Math.PI / 2
    const rr = i % 2 === 0 ? r : r * 0.42
    const px = x + Math.cos(a) * rr
    const py = y + Math.sin(a) * rr
    if (i === 0) ctx.moveTo(px, py)
    else ctx.lineTo(px, py)
  }
  ctx.closePath()
}

const easeOutBack = (t: number): number => {
  const c1 = 1.70158
  const c3 = c1 + 1
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2)
}

/** 幅に収まるよう「…」で省く。 */
function fitText(ctx: Ctx, text: string, maxW: number): string {
  if (ctx.measureText(text).width <= maxW) return text
  const chars = [...text]
  let lo = 0
  let hi = chars.length
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2)
    if (ctx.measureText(chars.slice(0, mid).join('') + '…').width <= maxW) lo = mid
    else hi = mid - 1
  }
  return chars.slice(0, Math.max(1, lo)).join('') + '…'
}

/** 小さな字の縁取り（紙の色）。下に線が通っても字が読める。 */
function halo(ctx: Ctx, text: string, x: number, y: number, paper: string): void {
  ctx.save()
  ctx.lineWidth = 3
  ctx.lineJoin = 'round'
  ctx.strokeStyle = withAlpha(paper, 0.9)
  ctx.strokeText(text, x, y)
  ctx.restore()
}

function arrowHead(ctx: Ctx, x: number, y: number, ang: number, len = 8): void {
  ctx.beginPath()
  ctx.moveTo(x, y)
  ctx.lineTo(x - len * Math.cos(ang - 0.4), y - len * Math.sin(ang - 0.4))
  ctx.lineTo(x - len * Math.cos(ang + 0.4), y - len * Math.sin(ang + 0.4))
  ctx.closePath()
  ctx.fill()
}

/** 提案の線の濃さ。図に入れたものは濃く、提案は薄く、付け替えで離れる元の線はさらに薄い。選んだ線は濃い。 */
function draftLineAlpha(d: DraftEdgeInfo, selected: boolean): number {
  if (selected) return 0.95
  switch (d.state) {
    case 'solid':
      return 0.85
    case 'retarget':
      return 0.6
    case 'moved':
      return 0.2
    case 'remove':
      return 0.3
    default:
      return 0.42
  }
}

function strokePath(ctx: Ctx, pts: { x: number; y: number }[]): void {
  ctx.beginPath()
  ctx.moveTo(pts[0].x, pts[0].y)
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y)
  ctx.stroke()
}

function endAngle(pts: { x: number; y: number }[]): number {
  const a = pts[pts.length - 2]
  const z = pts[pts.length - 1]
  return Math.atan2(z.y - a.y, z.x - a.x)
}

function startAngle(pts: { x: number; y: number }[]): number {
  return Math.atan2(pts[0].y - pts[1].y, pts[0].x - pts[1].x)
}

export function drawGraph(
  ctx: Ctx,
  layout: SkyLayout,
  pal: SkyPalette,
  labels: GraphLabels,
  st: GraphDrawState,
  visible: ReadonlySet<string>,
): void {
  const W = layout.width
  const H = layout.height
  const A = st.alpha ?? 1
  ctx.clearRect(0, 0, W, H)
  ctx.fillStyle = pal.paper
  ctx.fillRect(0, 0, W, H)

  const focus = st.focusId
  const near = st.near
  const removed = st.removeNodeIds
  const dimmed = (id: string): boolean => !!near && !near.has(id)
  const thinOf = (id: string): number => {
    const n = layout.nodes.get(id)
    if (!n) return 1
    if (n.type === 'kind' || n.type === 'dataset') return n.proposed ? THIN_ALPHA : 1
    if (n.type === 'draft') return n.state === 'thin' || n.proposed || n.state === 'remove' ? THIN_ALPHA : 1
    if (n.type === 'term') return removed?.has(id) ? THIN_ALPHA : 1
    return 1
  }
  const alphaOf = (id: string): number => (dimmed(id) ? 0.22 : 1) * thinOf(id)
  const scaleOf = (id: string): number => {
    const t0 = st.born.get(id)
    if (t0 === undefined || st.reducedMotion) return 1
    const t = Math.min(1, (st.now - t0) / BORN_GROW_MS)
    return Math.max(0.05, easeOutBack(t))
  }
  const node = (id: string): SkyNode | undefined => layout.nodes.get(id)
  const quietK = st.reducedMotion ? 0.5 : 1

  // 最下段の見出しと、データセットの名前（星座の名前は最下段だけ）
  if (layout.clusters.length > 0) {
    ctx.save()
    ctx.globalAlpha = A
    ctx.font = `600 11px ${pal.font}`
    ctx.fillStyle = pal.bandText
    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    ctx.fillText(labels.noName, 28, layout.bands.midTop)
    ctx.restore()
    for (const cl of layout.clusters) {
      const inCluster = cl.nodeIds.some((id) => !dimmed(id))
      ctx.save()
      ctx.globalAlpha = (near && !inCluster ? 0.25 : 1) * (cl.nodeIds.length ? thinOf(cl.nodeIds[0]) : 1) * A
      ctx.font = `600 11px ${pal.font}`
      ctx.fillStyle = pal.body
      ctx.textAlign = 'left'
      ctx.textBaseline = 'top'
      ctx.fillText(fitText(ctx, cl.label, cl.labelMaxW), cl.cx - cl.w / 2, cl.cy - cl.h / 2)
      ctx.restore()
    }
  }

  // 星 → 札（短い線。同じものは矢じり無し）
  for (const e of layout.edges) {
    if (e.kind !== 'member' || !e.geom) continue
    const a = node(e.from)
    const c = node(e.to)
    if (!a || !c) continue
    const pts = geomPoints(e.geom)
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.to), alphaOf(e.from)) * 0.9 * A
    ctx.strokeStyle = pal.activity
    ctx.fillStyle = pal.activity
    ctx.lineWidth = near && near.has(e.from) && near.has(e.to) && focus ? 1.8 : 1
    strokePath(ctx, pts)
    if (!e.eq) arrowHead(ctx, pts[pts.length - 1].x, pts[pts.length - 1].y, endAngle(pts), 5)
    ctx.restore()
  }

  // 知識の線（一部＝細い線＋矢じり、下げる／上げる／変える＝線＋矢じり）。札の下に敷く
  for (const e of layout.edges) {
    if (e.kind !== 'concept' || !e.geom) continue
    const pts = geomPoints(e.geom)
    const part = e.relation === 'isPartOf'
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * (part ? 0.75 : 0.9) * quietK * A
    ctx.strokeStyle = part ? pal.body : pal.activity
    ctx.fillStyle = ctx.strokeStyle
    ctx.lineWidth = near && near.has(e.from) && near.has(e.to) && focus ? 1.8 : part ? 1 : 1.4
    strokePath(ctx, pts)
    arrowHead(ctx, pts[pts.length - 1].x, pts[pts.length - 1].y, endAngle(pts))
    ctx.restore()
  }

  // 提案の線（薄い実線。点線は使わない）
  for (const e of layout.edges) {
    if (e.kind !== 'draft' || !e.draft || !e.geom) continue
    const d = e.draft
    const pts = geomPoints(e.geom)
    const part = d.rel === 'part' || d.rel === 'sub'
    const on = st.selectedEdge === draftEdgeId(d)
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * draftLineAlpha(d, on) * A
    ctx.strokeStyle = d.problem ? pal.accent : part ? pal.body : pal.activity
    ctx.fillStyle = ctx.strokeStyle
    ctx.lineWidth = on ? 2.4 : d.state === 'solid' ? 1.4 : 1.2
    strokePath(ctx, pts)
    arrowHead(ctx, pts[pts.length - 1].x, pts[pts.length - 1].y, endAngle(pts))
    if (d.rel === 'eq') arrowHead(ctx, pts[0].x, pts[0].y, startAngle(pts))
    ctx.restore()
  }

  // 星（札の直下・最下段）
  ctx.textAlign = 'center'
  for (const n of layout.nodes.values()) {
    if (n.type !== 'kind') continue
    drawStar(ctx, n, pal, labels, focus === n.id, alphaOf(n.id) * A, scaleOf(n.id), st, visible.has(n.id) || focus === n.id)
  }

  // 視点（つながり）
  for (const n of layout.nodes.values()) {
    if (n.type !== 'hub') continue
    drawHub(ctx, n, pal, focus === n.id, alphaOf(n.id) * A, scaleOf(n.id))
  }

  // 札（概念）
  for (const n of layout.nodes.values()) {
    if (n.type === 'term') drawCard(ctx, n, pal, labels, focus === n.id, alphaOf(n.id) * A, scaleOf(n.id), st, removed?.has(n.id) === true)
    else if (n.type === 'draft') drawCard(ctx, n, pal, labels, focus === n.id, alphaOf(n.id) * A, scaleOf(n.id), st, false)
  }

  // 線の小さな札（線の中ほど。紙の地で抜く）
  for (const e of layout.edges) {
    if ((e.kind !== 'concept' && e.kind !== 'draft') || !e.geom) continue
    drawPill(ctx, e, pal, labels, st, Math.min(alphaOf(e.from), alphaOf(e.to)) * A, quietK)
  }
  ctx.textAlign = 'start'
}

function drawStar(
  ctx: Ctx,
  n: KindNode,
  pal: SkyPalette,
  labels: GraphLabels,
  focused: boolean,
  alpha: number,
  sc: number,
  st: GraphDrawState,
  showLabel: boolean,
): void {
  const r = n.r * sc
  ctx.save()
  ctx.globalAlpha = alpha
  const t0 = st.born.get(n.id)
  if (t0 !== undefined && !st.reducedMotion) {
    const t = Math.min(1, (st.now - t0) / BORN_RING_MS)
    if (t < 1) {
      ctx.strokeStyle = withAlpha(pal.entity, 0.5 * (1 - t))
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(n.x, n.y, r + 6 + 26 * t, 0, Math.PI * 2)
      ctx.stroke()
    }
  }
  ctx.fillStyle = pal.entity
  ctx.beginPath()
  ctx.arc(n.x, n.y, r, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = pal.surface
  ctx.lineWidth = 1.5
  ctx.stroke()
  if (focused) {
    ctx.strokeStyle = pal.entity
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2)
    ctx.stroke()
  }
  if (showLabel) {
    // 名前は星の下 3px、件数はその下。重なる星の名前は呼び手が出さない（hover・選択中は出す）
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.font = `500 11px ${pal.font}`
    ctx.fillStyle = pal.ink
    ctx.lineWidth = 3
    ctx.lineJoin = 'round'
    ctx.strokeStyle = withAlpha(pal.paper, 0.9)
    ctx.strokeText(n.label, n.x, n.y + r + 3)
    ctx.fillText(n.label, n.x, n.y + r + 3)
    if (n.count > 0) {
      const s = labels.count(n.count)
      ctx.font = `500 10px ${pal.mono}`
      ctx.fillStyle = pal.faint
      ctx.strokeText(s, n.x, n.y + r + 16)
      ctx.fillText(s, n.x, n.y + r + 16)
    }
  }
  ctx.restore()
}

function drawHub(ctx: Ctx, n: HubNode, pal: SkyPalette, focused: boolean, alpha: number, sc: number): void {
  ctx.save()
  ctx.globalAlpha = alpha
  ctx.fillStyle = pal.activity
  star4(ctx, n.x, n.y, 11 * sc)
  ctx.fill()
  ctx.strokeStyle = pal.surface
  ctx.lineWidth = 1.5
  ctx.stroke()
  if (focused) {
    ctx.strokeStyle = pal.activity
    ctx.lineWidth = 2
    star4(ctx, n.x, n.y, 15 * sc)
    ctx.stroke()
  }
  ctx.font = `500 11px ${pal.font}`
  ctx.fillStyle = pal.activity
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  halo(ctx, n.label, n.x, n.y + 14, pal.paper)
  ctx.fillText(n.label, n.x, n.y + 14)
  ctx.restore()
}

/** 札（概念）。用語＝星があれば濃い塗り・無ければ薄い塗り。項目は角の小さい札。提案は薄く「提案」の印。 */
function drawCard(
  ctx: Ctx,
  n: TermNode | DraftNode,
  pal: SkyPalette,
  labels: GraphLabels,
  focused: boolean,
  alpha: number,
  sc: number,
  st: GraphDrawState,
  removing: boolean,
): void {
  const isDraft = n.type === 'draft'
  const draftThin = isDraft && (n.state === 'thin' || n.proposed)
  const draftRemove = isDraft && n.state === 'remove'
  const remove = removing || draftRemove
  const solid = !isDraft && n.memberIds.length > 0 && !remove
  ctx.save()
  ctx.globalAlpha = alpha
  ctx.font = `600 12px ${pal.font}`
  const inner = n.w - 16
  const text = fitText(ctx, n.label, inner)
  const tw = ctx.measureText(text).width
  const w = n.w * sc
  const h = n.h * sc
  const x = n.x - w / 2
  const y = n.y - h / 2
  const radius = n.termKind === 'property' ? 5 : 14
  const t0 = st.born.get(n.id)
  if (t0 !== undefined && !st.reducedMotion) {
    const t = Math.min(1, (st.now - t0) / BORN_RING_MS)
    if (t < 1) {
      ctx.strokeStyle = withAlpha(pal.activity, 0.5 * (1 - t))
      ctx.lineWidth = 2
      roundRect(ctx, x - 6 - 20 * t, y - 6 - 20 * t, w + 12 + 40 * t, h + 12 + 40 * t, radius + 6)
      ctx.stroke()
    }
  }
  roundRect(ctx, x, y, w, h, radius)
  ctx.fillStyle = solid ? pal.activity : draftThin ? withAlpha(pal.activitySoft, 0.55) : pal.activitySoft
  ctx.fill()
  ctx.strokeStyle = remove ? pal.accent : pal.activity
  ctx.lineWidth = focused ? 2.5 : remove ? 1.4 : draftThin ? 1.1 : 1.2
  ctx.stroke()
  // 名前（標準の語の 1 行があれば札の上半分）
  const std = !isDraft ? n.stdLabel : undefined
  const nameY = std ? n.y - 8 : n.y
  ctx.fillStyle = solid ? pal.surface : pal.activity
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, n.x, nameY + 0.5)
  if (remove) {
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(n.x - tw / 2, nameY + 0.5)
    ctx.lineTo(n.x + tw / 2, nameY + 0.5)
    ctx.stroke()
  }
  if (std) {
    // 札の中の 1 行「≡ prefix:label」（押すと標準の語の 1 枚）
    ctx.font = `500 12px ${pal.font}`
    ctx.fillStyle = solid ? pal.accentSoft : pal.accent
    ctx.fillText(fitText(ctx, `≡ ${std}`, inner), n.x, n.y + 8.5)
  }
  // 印（札の上）: 提案・外す提案。図に入れたものには付けない
  const mark = remove ? labels.draftRemove : draftThin ? labels.draft : ''
  if (mark) {
    ctx.font = `500 10px ${pal.mono}`
    ctx.fillStyle = pal.accent
    ctx.textBaseline = 'bottom'
    halo(ctx, mark, n.x, y - 2, pal.paper)
    ctx.fillText(mark, n.x, y - 2)
  }
  ctx.restore()
}

/** 線の小さな札（↓ ↑ ↕・一部・一種・量・＝・→・×）。線の上に紙の地で抜く。 */
function drawPill(ctx: Ctx, e: SkyEdge, pal: SkyPalette, labels: GraphLabels, st: GraphDrawState, alpha: number, quietK: number): void {
  const pos = e.pill
  if (!pos) return
  let text: string
  let color: string
  let a = alpha * quietK
  if (e.kind === 'concept') {
    text = e.relation === 'isPartOf' ? labels.partOf : e.relation === 'lowers' ? '↓' : e.relation === 'raises' ? '↑' : '↕'
    color = e.relation === 'isPartOf' ? pal.body : pal.activity
  } else if (e.draft) {
    text = draftPillText(e.draft, labels)
    const part = e.draft.rel === 'part' || e.draft.rel === 'sub'
    color = e.draft.problem ? pal.accent : part ? pal.body : pal.activity
    a = alpha * draftLineAlpha(e.draft, st.selectedEdge === draftEdgeId(e.draft))
  } else return
  if (!text) return
  ctx.save()
  ctx.globalAlpha = a
  ctx.font = `600 ${text.length > 1 ? 11 : 12}px ${pal.font}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  const w = Math.max(24, ctx.measureText(text).width + 12)
  roundRect(ctx, pos.x - w / 2, pos.y - 8, w, 16, 8)
  // 線を抜く地は薄くしない（線が透けて字に被らない）
  ctx.save()
  ctx.globalAlpha = 1
  ctx.fillStyle = pal.paper
  ctx.fill()
  ctx.restore()
  ctx.strokeStyle = color
  ctx.lineWidth = 1
  ctx.stroke()
  ctx.fillStyle = color
  ctx.fillText(text, pos.x, pos.y + 0.5)
  ctx.restore()
}
