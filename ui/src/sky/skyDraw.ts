// 星図の描画（canvas 2D）。参考実装 `docs/design/sky-chart-mock/skychart.js` の `draw()` の移植。
//
// 紙の地・薄い格子・星座の線・星（大きさ＝件数）・用語の札（青）・つながり（4 芒星）・
// 標準用語（琥珀の地平線）・hover の濃淡・生まれた輪。夜空の暗色は使わない（forest 単一テーマ）。
// 色は CSS 変数から（`readSkyPalette`）。配置は `skyLayout.ts`（描画は何も決めない）。
import {
  approxLabelWidth,
  conceptEdgeGeom,
  conceptPillPos,
  draftEdgeId,
  type DatasetNode,
  type DraftEdgeInfo,
  type DraftNode,
  type KindNode,
  type SkyEdge,
  type SkyLayout,
  type SkyNode,
} from './skyLayout'

export interface SkyPalette {
  paper: string
  grid: string
  bandText: string
  ink: string
  body: string
  entity: string
  entitySoft: string
  activity: string
  activitySoft: string
  accent: string
  accentSoft: string
  surface: string
  faint: string
  font: string
  mono: string
}

const FALLBACK: SkyPalette = {
  paper: '#f7f9f4',
  grid: '#dde6da',
  bandText: '#728579',
  ink: '#16241a',
  body: '#33453a',
  entity: '#3a7a4c',
  entitySoft: '#e5f0e6',
  activity: '#356794',
  activitySoft: '#e4eef6',
  accent: '#b3722b',
  accentSoft: '#f6ecd9',
  surface: '#ffffff',
  faint: '#728579',
  font: "'Hanken Grotesk','Zen Kaku Gothic New','Noto Sans JP',system-ui,sans-serif",
  mono: "'IBM Plex Mono',ui-monospace,Menlo,monospace",
}

/** CSS 変数を色に解決する（canvas は CSS 変数を読めない）。`NetworkView.readPalette` と同じ流儀。 */
export function readSkyPalette(): SkyPalette {
  if (typeof document === 'undefined') return FALLBACK
  const cs = getComputedStyle(document.documentElement)
  const v = (name: string, fb: string): string => cs.getPropertyValue(name).trim() || fb
  return {
    paper: v('--paper', FALLBACK.paper),
    grid: v('--border', FALLBACK.grid),
    bandText: v('--faint', FALLBACK.bandText),
    ink: v('--fg', FALLBACK.ink),
    body: v('--body', FALLBACK.body),
    entity: v('--entity', FALLBACK.entity),
    entitySoft: v('--entity-soft', FALLBACK.entitySoft),
    activity: v('--activity', FALLBACK.activity),
    activitySoft: v('--activity-soft', FALLBACK.activitySoft),
    accent: v('--accent', FALLBACK.accent),
    accentSoft: v('--accent-soft', FALLBACK.accentSoft),
    surface: v('--surface', FALLBACK.surface),
    faint: v('--faint', FALLBACK.faint),
    font: v('--font-ui', FALLBACK.font),
    mono: v('--font-mono', FALLBACK.mono),
  }
}

export interface SkyLabels {
  names: string
  mid: string
  midCircles: string
  std: string
  /** 星の無い用語の札の下。 */
  empty: string
  /** 項目（property）の用語の札の上。 */
  property: string
  /** 提案の札の印（薄い札の上）。 */
  draft: string
  /** 外す提案の札の印。 */
  draftRemove: string
  /** 知識の線の小さな札（「一部」）。下げる／上げる／変えるは記号（↓ ↑ ↕）。 */
  partOf: string
  /** 提案の線の小さな札（一種・量）。同じもの・付け替え・外すは記号。 */
  relSub: string
  relQuantity: string
  /** 件数の単位つき文字列（`46,200 件`）。 */
  count: (n: number) => string
  /** データセットの丸の下（`3 種類`）。 */
  kinds: (n: number) => string
}

export interface DrawState {
  /** hover があればそれ、無ければ選択中。 */
  focusId: string | null
  /** 濃く残す点。null なら濃淡なし。 */
  near: ReadonlySet<string> | null
  /** 生まれたばかりの点（id → 開始時刻 ms）。 */
  born: ReadonlyMap<string, number>
  now: number
  /** 静かな表示（動かさない）。 */
  reducedMotion: boolean
  /** 全体の濃さ（見本を薄く重ねるとき）。 */
  alpha?: number
  /** 選んでいる提案の線（`from|rel|to`）。太く描く。 */
  selectedEdge?: string | null
}

// ── 小道具 ───────────────────────────────────────────────────────
type Ctx = CanvasRenderingContext2D

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

export function withAlpha(hex: string, a: number): string {
  const m = hex.replace('#', '')
  if (!/^([0-9a-f]{3}|[0-9a-f]{6})$/i.test(m)) return hex
  const n = m.length === 3 ? m.split('').map((c) => c + c).join('') : m
  const r = parseInt(n.slice(0, 2), 16)
  const g = parseInt(n.slice(2, 4), 16)
  const b = parseInt(n.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${a})`
}

const easeOutBack = (t: number): number => {
  const c1 = 1.70158
  const c3 = c1 + 1
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2)
}

/** 生まれたての 1 度きりの動き: 札・星が 650ms で現れ、輪は 1400ms で消える。 */
export const BORN_GROW_MS = 650
export const BORN_RING_MS = 1400

export function bornAlive(born: ReadonlyMap<string, number>, now: number): boolean {
  for (const t0 of born.values()) if (now - t0 < BORN_RING_MS + 100) return true
  return false
}

/** 件数の短い表記。日本語は万、英語は k/M。 */
export function formatCount(n: number, lang: 'ja' | 'en' = 'ja'): string {
  if (lang === 'ja') {
    if (n >= 10000) return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)}万`
    return n.toLocaleString('en-US')
  }
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k`
  return n.toLocaleString('en-US')
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

// ── 星・丸のラベルの重なり（出さない判定） ─────────────────────────
interface Box {
  x0: number
  y0: number
  x1: number
  y1: number
}
const overlaps = (a: Box, b: Box, m = 2): boolean =>
  a.x0 < b.x1 + m && a.x1 > b.x0 - m && a.y0 < b.y1 + m && a.y1 > b.y0 - m

/** 星・丸の名前（＋件数の行）が占める箱。名前の幅は 11px × 文字数で近似する。 */
export function pointLabelBox(n: KindNode | DatasetNode): Box {
  const textW = approxLabelWidth(n.label)
  const sub = n.count > 0 || n.type === 'dataset'
  const w = Math.min(n.type === 'dataset' ? 112 : 190, Math.max(textW, sub ? 56 : 0))
  const y0 = n.y + n.r + 3
  return { x0: n.x - w / 2, x1: n.x + w / 2, y0, y1: y0 + (sub ? 28 : 14) }
}

/**
 * 名前を出す星・丸の id。重なるものは出さない（大きい＝件数の多いものを先に）。
 * 星どうしの丸・星座の名前にも被せない。hover・選択中の点は呼び手が足して出す。
 */
export function visiblePointLabels(layout: SkyLayout): Set<string> {
  const obstacles: { id: string | null; box: Box }[] = []
  const points: (KindNode | DatasetNode)[] = []
  for (const n of layout.nodes.values()) {
    if (n.type === 'kind' || n.type === 'dataset') {
      points.push(n)
      obstacles.push({
        id: n.id,
        box: { x0: n.x - n.r - 1, x1: n.x + n.r + 1, y0: n.y - n.r - 1, y1: n.y + n.r + 1 },
      })
    }
  }
  for (const c of layout.clusters) {
    const w = Math.min(c.labelMaxW, approxLabelWidth(c.label))
    const x0 = c.cx - c.w / 2 + 8
    obstacles.push({ id: null, box: { x0, x1: x0 + w, y0: c.cy - c.h / 2 - 9, y1: c.cy - c.h / 2 + 4 } })
  }
  points.sort((a, b) => b.count - a.count || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const placed: Box[] = []
  const out = new Set<string>()
  for (const p of points) {
    const box = pointLabelBox(p)
    // 自分の丸は除いて、ほかの丸・先に置いた名前・星座の名前と比べる。
    const hit =
      obstacles.some((o) => o.id !== p.id && overlaps(box, o.box, 0)) || placed.some((o) => overlaps(box, o))
    if (hit) continue
    placed.push(box)
    out.add(p.id)
  }
  return out
}

// ── 提案の札・線 ─────────────────────────────────────────────────
function arrowHead(ctx: Ctx, x: number, y: number, ang: number): void {
  ctx.beginPath()
  ctx.moveTo(x, y)
  ctx.lineTo(x - 8 * Math.cos(ang - 0.4), y - 8 * Math.sin(ang - 0.4))
  ctx.lineTo(x - 8 * Math.cos(ang + 0.4), y - 8 * Math.sin(ang + 0.4))
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

/** 提案の線の小さな札の字。 */
export function draftPillText(d: DraftEdgeInfo, labels: SkyLabels): string {
  if (d.state === 'retarget') return '→'
  if (d.state === 'remove') return '×'
  switch (d.rel) {
    case 'part':
      return labels.partOf
    case 'sub':
      return labels.relSub
    case 'quantity':
      return labels.relQuantity
    case 'eq':
      return '＝'
    case 'lowers':
      return '↓'
    case 'raises':
      return '↑'
    case 'affects':
      return '↕'
    default:
      return ''
  }
}

function drawDraftCard(
  ctx: Ctx,
  n: DraftNode,
  pal: SkyPalette,
  labels: SkyLabels,
  focused: boolean,
  alpha: number,
  sc: number,
): void {
  const thin = n.state === 'thin' || n.proposed
  ctx.save()
  ctx.globalAlpha = alpha * (thin ? 0.72 : n.state === 'remove' ? 0.55 : 1)
  ctx.font = `600 12px ${pal.font}`
  const text = fitText(ctx, n.label, n.w - 26)
  const tw = ctx.measureText(text).width
  const w = Math.min(n.w, tw + 26) * sc
  const h = n.h * sc
  const x = n.x - w / 2
  const y = n.y - h / 2
  roundRect(ctx, x, y, w, h, 14)
  ctx.fillStyle = thin ? withAlpha(pal.activitySoft, 0.55) : pal.activitySoft
  ctx.fill()
  ctx.strokeStyle = n.state === 'remove' ? pal.accent : pal.activity
  ctx.lineWidth = focused ? 2.5 : thin ? 1.1 : 1.6
  ctx.stroke()
  ctx.fillStyle = pal.activity
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, n.x, n.y + 0.5)
  if (n.state === 'remove') {
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(n.x - tw / 2, n.y + 0.5)
    ctx.lineTo(n.x + tw / 2, n.y + 0.5)
    ctx.stroke()
  }
  // 印（札の上）: 提案・外す提案。図に入れたものには付けない。項目の札は、印の右に「項目」を添える
  const mark = n.state === 'remove' ? labels.draftRemove : thin ? labels.draft : ''
  const caption = mark ? (n.termKind === 'property' ? `${mark} · ${labels.property}` : mark) : n.termKind === 'property' ? labels.property : ''
  if (caption) {
    ctx.font = `500 9px ${pal.mono}`
    ctx.fillStyle = mark ? pal.accent : pal.faint
    ctx.textBaseline = 'bottom'
    ctx.fillText(caption, n.x, y - 2)
  }
  ctx.restore()
}

// ── 提案の線の当たり判定・端の丸 ───────────────────────────────────
const HANDLE_R = 7

/** 線（2 次ベジェ）の点 p からの最短距離（折れ線で近似）。 */
function distToCurve(g: { x1: number; y1: number; cx: number; cy: number; x2: number; y2: number }, px: number, py: number): number {
  let best = Infinity
  let prev = { x: g.x1, y: g.y1 }
  for (let i = 1; i <= 24; i++) {
    const t = i / 24
    const u = 1 - t
    const q = { x: u * u * g.x1 + 2 * u * t * g.cx + t * t * g.x2, y: u * u * g.y1 + 2 * u * t * g.cy + t * t * g.y2 }
    const dx = q.x - prev.x
    const dy = q.y - prev.y
    const len2 = dx * dx + dy * dy || 1
    const k = Math.max(0, Math.min(1, ((px - prev.x) * dx + (py - prev.y) * dy) / len2))
    best = Math.min(best, Math.hypot(px - (prev.x + dx * k), py - (prev.y + dy * k)))
    prev = q
  }
  return best
}

/**
 * 押した所にある提案の線（直せる線だけ＝**開いている下書き**の関係）。小さな札の上か、線から 7px 以内。
 * 開いていない下書きの線は選べない（`openDraftId` が null なら、どの線も選べない）。
 */
export function hitDraftEdge(layout: SkyLayout, px: number, py: number, openDraftId: string | null): SkyEdge | null {
  if (openDraftId === null) return null
  let best: SkyEdge | null = null
  let bd = Infinity
  for (const e of layout.edges) {
    if (e.kind !== 'draft' || !e.draft || e.draft.index === null || e.draft.draftId !== openDraftId) continue
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    let d = distToCurve(g, px, py)
    if (e.pill && Math.abs(px - e.pill.x) <= 16 && Math.abs(py - e.pill.y) <= 9) d = Math.min(d, 0)
    if (d <= 7 && d < bd) {
      bd = d
      best = e
    }
  }
  return best
}

/** 押した所にある線（知識の線・提案の線）。小さな札の上か、線から 7px 以内。開いている下書きかどうかは問わない。 */
export function hitLineEdge(layout: SkyLayout, px: number, py: number): SkyEdge | null {
  let best: SkyEdge | null = null
  let bd = Infinity
  for (const e of layout.edges) {
    if (e.kind !== 'concept' && e.kind !== 'draft') continue
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    let d = distToCurve(g, px, py)
    if (e.pill && Math.abs(px - e.pill.x) <= 16 && Math.abs(py - e.pill.y) <= 9) d = Math.min(d, 0)
    if (d <= 7 && d < bd) {
      bd = d
      best = e
    }
  }
  return best
}

/** 線の端の丸（矢じりのそば）の位置。 */
export function draftHandlePos(layout: SkyLayout, e: SkyEdge): { x: number; y: number } | null {
  const g = conceptEdgeGeom(layout, e)
  return g ? { x: g.x2, y: g.y2 } : null
}

/** 押した所にある端の丸（持って別の札に落とす）。`edges` は丸を出している線。 */
export function hitHandle(layout: SkyLayout, edges: readonly SkyEdge[], px: number, py: number): SkyEdge | null {
  for (const e of edges) {
    const h = draftHandlePos(layout, e)
    if (h && Math.hypot(px - h.x, py - h.y) <= HANDLE_R + 5) return e
  }
  return null
}

export interface DraftOverlay {
  /** 端の丸を出す線（選んだ線・選んだ札の線）。 */
  handles: readonly SkyEdge[]
  /** 持っている最中: どの線を・いまの位置・落とし先の札。 */
  drag: { edge: SkyEdge; x: number; y: number; overId: string | null } | null
}

/** 端の丸と、持っている最中の線・落とし先の枠を重ねる。drawSky のあとに呼ぶ。 */
export function drawDraftOverlay(ctx: Ctx, layout: SkyLayout, pal: SkyPalette, ov: DraftOverlay): void {
  ctx.save()
  for (const e of ov.handles) {
    const h = draftHandlePos(layout, e)
    if (!h) continue
    const dragging = ov.drag?.edge === e
    const x = dragging ? ov.drag!.x : h.x
    const y = dragging ? ov.drag!.y : h.y
    ctx.beginPath()
    ctx.arc(x, y, HANDLE_R, 0, Math.PI * 2)
    ctx.fillStyle = pal.surface
    ctx.fill()
    ctx.strokeStyle = pal.accent
    ctx.lineWidth = 2
    ctx.stroke()
    ctx.beginPath()
    ctx.arc(x, y, 2.5, 0, Math.PI * 2)
    ctx.fillStyle = pal.accent
    ctx.fill()
  }
  const d = ov.drag
  if (d) {
    const from = layout.nodes.get(d.edge.from)
    if (from) {
      ctx.strokeStyle = pal.accent
      ctx.globalAlpha = 0.7
      ctx.lineWidth = 1.6
      ctx.beginPath()
      ctx.moveTo(from.x, from.y)
      ctx.lineTo(d.x, d.y)
      ctx.stroke()
    }
    const over = d.overId ? layout.nodes.get(d.overId) : undefined
    if (over && (over.type === 'draft' || over.type === 'term')) {
      ctx.globalAlpha = 1
      ctx.strokeStyle = pal.accent
      ctx.lineWidth = 2.5
      roundRect(ctx, over.x - over.w / 2 - 3, over.y - over.h / 2 - 3, over.w + 6, over.h + 6, 16)
      ctx.stroke()
    }
  }
  ctx.restore()
}

// ── 当たり判定 ───────────────────────────────────────────────────
export function hitTest(layout: SkyLayout, px: number, py: number): SkyNode | null {
  let best: SkyNode | null = null
  let bd = Infinity
  for (const n of layout.nodes.values()) {
    const dx = n.x - px
    const dy = n.y - py
    const d = Math.sqrt(dx * dx + dy * dy)
    const hit =
      n.type === 'kind' || n.type === 'dataset'
        ? d < n.r + 8
        : n.type === 'hub'
          ? d < 16
          : Math.abs(dx) <= n.w / 2 + 4 && Math.abs(dy) <= n.h / 2 + 4
    if (hit && d < bd) {
      bd = d
      best = n
    }
  }
  return best
}

// ── 描画 ─────────────────────────────────────────────────────────
export function drawSky(
  ctx: Ctx,
  layout: SkyLayout,
  pal: SkyPalette,
  labels: SkyLabels,
  st: DrawState,
  visible: ReadonlySet<string>,
): void {
  const W = layout.width
  const H = layout.height
  const A = st.alpha ?? 1
  ctx.clearRect(0, 0, W, H)
  ctx.fillStyle = pal.paper
  ctx.fillRect(0, 0, W, H)

  // 格子（星図の経線・緯線の気配）
  ctx.save()
  ctx.globalAlpha = A
  ctx.strokeStyle = withAlpha(pal.grid, 0.9)
  ctx.lineWidth = 1
  for (let i = 1; i < 5; i++) {
    const y = (H / 5) * i
    ctx.beginPath()
    ctx.moveTo(0, y + 14)
    ctx.quadraticCurveTo(W / 2, y - 14, W, y + 14)
    ctx.stroke()
  }
  for (let i = 1; i < 7; i++) {
    const x = (W / 7) * i
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.quadraticCurveTo(x + (i - 3.5) * 10, H / 2, x, H)
    ctx.stroke()
  }
  ctx.restore()

  // 帯の名前・地平線
  const b = layout.bands
  ctx.save()
  ctx.globalAlpha = A
  ctx.font = `600 11px ${pal.font}`
  ctx.fillStyle = pal.bandText
  ctx.textBaseline = 'middle'
  ctx.fillText(labels.names, 16, b.namesLabelY)
  ctx.fillText(layout.mode === 'circles' ? labels.midCircles : labels.mid, 16, b.midTop - 10)
  ctx.fillText(labels.std, 16, b.stdLabelY)
  ctx.strokeStyle = withAlpha(pal.accent, 0.35)
  ctx.lineWidth = 1.2
  ctx.beginPath()
  ctx.moveTo(0, b.horizonY)
  ctx.quadraticCurveTo(W / 2, b.horizonY - 14, W, b.horizonY)
  ctx.stroke()
  ctx.restore()

  const focus = st.focusId
  const near = st.near
  const dimmed = (id: string): boolean => !!near && !near.has(id)
  // 未公開（取り込み済み）のデータセットの星・丸は薄く（0.55）。星座の線・名前も同じ
  const thin = (id: string): number => {
    const n = layout.nodes.get(id)
    return n && (n.type === 'kind' || n.type === 'dataset') && n.proposed ? 0.55 : 1
  }
  const alphaOf = (id: string): number => (dimmed(id) ? 0.22 : 1) * thin(id)
  const scaleOf = (id: string): number => {
    const t0 = st.born.get(id)
    if (t0 === undefined || st.reducedMotion) return 1
    const t = Math.min(1, (st.now - t0) / BORN_GROW_MS)
    return Math.max(0.05, easeOutBack(t))
  }
  const node = (id: string): SkyNode | undefined => layout.nodes.get(id)

  // 星座の線（データの中のつながり）と枠の名前
  for (const cl of layout.clusters) {
    ctx.save()
    const inCluster = cl.nodeIds.some((id) => !dimmed(id))
    ctx.globalAlpha = (near && !inCluster ? 0.25 : 1) * (cl.nodeIds.length ? thin(cl.nodeIds[0]) : 1) * A
    ctx.font = `600 11px ${pal.font}`
    ctx.fillStyle = pal.body
    ctx.textBaseline = 'alphabetic'
    ctx.fillText(fitText(ctx, cl.label, cl.labelMaxW), cl.cx - cl.w / 2 + 8, cl.cy - cl.h / 2 + 2)
    ctx.restore()
  }
  ctx.save()
  ctx.strokeStyle = withAlpha(pal.entity, 0.45)
  ctx.lineWidth = 1
  for (const e of layout.edges) {
    if (e.kind !== 'link') continue
    const a = node(e.from)
    const c = node(e.to)
    if (!a || !c) continue
    ctx.globalAlpha = (near && !(near.has(e.from) || near.has(e.to)) ? 0.25 : 1) * thin(e.from) * A
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.lineTo(c.x, c.y)
    ctx.stroke()
  }
  ctx.restore()

  // 用語 ↔ 点・標準用語・ハブの線
  for (const e of layout.edges) {
    const a = node(e.from)
    const c = node(e.to)
    if (!a || !c) continue
    ctx.save()
    if (e.kind === 'member') {
      // 点 → 用語（矢じりは用語の側へ。同じものは矢じり無し）
      ctx.globalAlpha = Math.min(alphaOf(e.to), alphaOf(e.from)) * 0.9 * A
      ctx.strokeStyle = pal.activity
      ctx.lineWidth = near && near.has(e.from) && near.has(e.to) && focus ? 1.8 : 1
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.bezierCurveTo(a.x, a.y - 70, c.x, c.y + 80, c.x, c.y + 14)
      ctx.stroke()
      if (!e.eq) {
        ctx.fillStyle = pal.activity
        ctx.beginPath()
        ctx.moveTo(c.x, c.y + 13)
        ctx.lineTo(c.x - 4, c.y + 21)
        ctx.lineTo(c.x + 4, c.y + 21)
        ctx.closePath()
        ctx.fill()
      }
    } else if (e.kind === 'termStd') {
      ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * 0.35 * A
      ctx.strokeStyle = pal.accent
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(a.x + 10, a.y + 12)
      ctx.bezierCurveTo(a.x + 60, a.y + 160, c.x + 50, c.y - 160, c.x, c.y - 12)
      ctx.stroke()
    } else if (e.kind === 'kindStd') {
      ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * 0.7 * A
      ctx.strokeStyle = pal.accent
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.bezierCurveTo(a.x, a.y + 90, c.x, c.y - 90, c.x, c.y - 12)
      ctx.stroke()
    } else if (e.kind === 'hub') {
      ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * 0.9 * A
      ctx.strokeStyle = pal.activity
      ctx.lineWidth = 1.2
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(c.x, c.y)
      ctx.stroke()
    }
    ctx.restore()
  }

  // 語どうしの知識の線（一部＝細い線＋矢じり、下げる／上げる／変える＝線＋矢じり）。札の下に敷く。
  // 静かな表示では薄くする。小さな札（一部・↓ ↑ ↕）は用語の札のあとに重ねる。
  const quietK = st.reducedMotion ? 0.5 : 1
  for (const e of layout.edges) {
    if (e.kind !== 'concept') continue
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    const part = e.relation === 'isPartOf'
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * (part ? 0.75 : 0.9) * quietK * A
    ctx.strokeStyle = part ? pal.body : pal.activity
    ctx.fillStyle = ctx.strokeStyle
    ctx.lineWidth = near && near.has(e.from) && near.has(e.to) && focus ? 1.8 : part ? 1 : 1.4
    ctx.beginPath()
    ctx.moveTo(g.x1, g.y1)
    ctx.quadraticCurveTo(g.cx, g.cy, g.x2, g.y2)
    ctx.stroke()
    const ang = Math.atan2(g.y2 - g.cy, g.x2 - g.cx)
    ctx.beginPath()
    ctx.moveTo(g.x2, g.y2)
    ctx.lineTo(g.x2 - 8 * Math.cos(ang - 0.4), g.y2 - 8 * Math.sin(ang - 0.4))
    ctx.lineTo(g.x2 - 8 * Math.cos(ang + 0.4), g.y2 - 8 * Math.sin(ang + 0.4))
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  }

  // 提案の線（薄い実線）。確かめるまでは薄く、図に入れたものは濃く。付け替えで離れる元の線はさらに薄く。
  // 点線は使わない（利用者の好み）。小さな札は用語の札のあとに重ねる。
  for (const e of layout.edges) {
    if (e.kind !== 'draft' || !e.draft) continue
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    const d = e.draft
    const part = d.rel === 'part' || d.rel === 'sub'
    const on = st.selectedEdge === draftEdgeId(d)
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * draftLineAlpha(d, on) * A
    ctx.strokeStyle = d.problem ? pal.accent : part ? pal.body : pal.activity
    ctx.fillStyle = ctx.strokeStyle
    ctx.lineWidth = on ? 2.4 : d.state === 'solid' ? 1.4 : 1.2
    ctx.beginPath()
    ctx.moveTo(g.x1, g.y1)
    ctx.quadraticCurveTo(g.cx, g.cy, g.x2, g.y2)
    ctx.stroke()
    arrowHead(ctx, g.x2, g.y2, Math.atan2(g.y2 - g.cy, g.x2 - g.cx))
    if (d.rel === 'eq') arrowHead(ctx, g.x1, g.y1, Math.atan2(g.y1 - g.cy, g.x1 - g.cx))
    ctx.restore()
  }

  // 星（種類）・丸（データセット）
  ctx.textAlign = 'center'
  for (const n of layout.nodes.values()) {
    if (n.type !== 'kind' && n.type !== 'dataset') continue
    const sc = scaleOf(n.id)
    const r = n.r * sc
    ctx.save()
    ctx.globalAlpha = alphaOf(n.id) * A
    // 生まれたばかりの輪
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
    if (focus === n.id) {
      ctx.strokeStyle = pal.entity
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2)
      ctx.stroke()
    }
    // 名前: 重なるものは出さない（hover・選択中は出す）
    if (visible.has(n.id) || focus === n.id) {
      ctx.font = `500 11px ${pal.font}`
      ctx.fillStyle = pal.ink
      ctx.textBaseline = 'top'
      ctx.lineWidth = 3
      ctx.strokeStyle = withAlpha(pal.paper, 0.9)
      const name = n.type === 'dataset' ? fitText(ctx, n.label, 112) : n.label
      ctx.strokeText(name, n.x, n.y + r + 3)
      ctx.fillText(name, n.x, n.y + r + 3)
      ctx.font = `500 9.5px ${pal.mono}`
      ctx.fillStyle = pal.faint
      if (n.type === 'dataset') {
        const s = `${labels.kinds(n.kindCount)} · ${labels.count(n.count)}`
        ctx.strokeText(s, n.x, n.y + r + 16)
        ctx.fillText(s, n.x, n.y + r + 16)
      } else if (n.count > 0) {
        const s = labels.count(n.count)
        ctx.strokeText(s, n.x, n.y + r + 16)
        ctx.fillText(s, n.x, n.y + r + 16)
      }
    }
    ctx.restore()
  }

  // ハブ（つながり）
  for (const n of layout.nodes.values()) {
    if (n.type !== 'hub') continue
    const sc = scaleOf(n.id)
    ctx.save()
    ctx.globalAlpha = alphaOf(n.id) * A
    ctx.fillStyle = pal.activity
    star4(ctx, n.x, n.y, 11 * sc)
    ctx.fill()
    ctx.strokeStyle = pal.surface
    ctx.lineWidth = 1.5
    ctx.stroke()
    if (focus === n.id) {
      ctx.strokeStyle = pal.activity
      ctx.lineWidth = 2
      star4(ctx, n.x, n.y, 15 * sc)
      ctx.stroke()
    }
    ctx.font = `500 11px ${pal.font}`
    ctx.fillStyle = pal.activity
    ctx.textBaseline = 'top'
    ctx.lineWidth = 3
    ctx.strokeStyle = withAlpha(pal.paper, 0.9)
    ctx.strokeText(n.label, n.x, n.y + 14)
    ctx.fillText(n.label, n.x, n.y + 14)
    ctx.restore()
  }

  // 用語（上の帯）
  for (const n of layout.nodes.values()) {
    if (n.type !== 'term') continue
    const sc = scaleOf(n.id)
    const empty = n.memberIds.length === 0
    ctx.save()
    ctx.globalAlpha = alphaOf(n.id) * A
    ctx.font = `600 12px ${pal.font}`
    const text = fitText(ctx, n.label, n.w - 26)
    const tw = ctx.measureText(text).width
    const w = Math.min(n.w, tw + 26) * sc
    const h = n.h * sc
    const x = n.x - w / 2
    const y = n.y - h / 2
    const t0 = st.born.get(n.id)
    if (t0 !== undefined && !st.reducedMotion) {
      const t = Math.min(1, (st.now - t0) / BORN_RING_MS)
      if (t < 1) {
        ctx.strokeStyle = withAlpha(pal.activity, 0.5 * (1 - t))
        ctx.lineWidth = 2
        roundRect(ctx, x - 6 - 20 * t, y - 6 - 20 * t, w + 12 + 40 * t, h + 12 + 40 * t, 20)
        ctx.stroke()
      }
    }
    roundRect(ctx, x, y, w, h, 14)
    ctx.fillStyle = empty ? pal.activitySoft : pal.activity
    ctx.fill()
    ctx.strokeStyle = pal.activity
    ctx.lineWidth = focus === n.id ? 2.5 : 1.2
    ctx.stroke()
    ctx.fillStyle = empty ? pal.activity : pal.surface
    ctx.textBaseline = 'middle'
    ctx.fillText(text, n.x, n.y + 0.5)
    if (n.termKind === 'property') {
      ctx.font = `500 9px ${pal.mono}`
      ctx.fillStyle = pal.faint
      ctx.textBaseline = 'bottom'
      halo(ctx, labels.property, n.x, y - 2, pal.paper)
      ctx.fillText(labels.property, n.x, y - 2)
    }
    const std = n.stdId ? layout.nodes.get(n.stdId) : undefined
    if (empty) {
      ctx.font = `500 10px ${pal.font}`
      ctx.fillStyle = pal.faint
      ctx.textBaseline = 'top'
      halo(ctx, labels.empty, n.x, y + h + 3, pal.paper)
      ctx.fillText(labels.empty, n.x, y + h + 3)
    } else if (std && std.type === 'std') {
      ctx.font = `500 9.5px ${pal.mono}`
      ctx.fillStyle = pal.accent
      ctx.textBaseline = 'top'
      const cap = fitText(ctx, `≡ ${std.prefix}:${std.label}`, n.w + 8)
      halo(ctx, cap, n.x, y + h + 3, pal.paper)
      ctx.fillText(cap, n.x, y + h + 3)
    }
    ctx.restore()
  }

  // 知識の線の小さな札（線の中ほど）
  for (const e of layout.edges) {
    if (e.kind !== 'concept') continue
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    const text = e.relation === 'isPartOf' ? labels.partOf : e.relation === 'lowers' ? '↓' : e.relation === 'raises' ? '↑' : '↕'
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * quietK * A
    ctx.font = `600 ${e.relation === 'isPartOf' ? 9.5 : 11}px ${pal.font}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    const w = ctx.measureText(text).width + 10
    // 置き場所は layoutSky が決めている（他の小さな札・札・札の下の字を避けてある）。無い古い形だけ従来の探し方。
    const pos = e.pill !== undefined ? e.pill : conceptPillPos(layout, g, w)
    if (!pos) {
      ctx.restore()
      continue
    }
    roundRect(ctx, pos.x - w / 2, pos.y - 8, w, 16, 8)
    ctx.fillStyle = pal.surface
    ctx.fill()
    ctx.strokeStyle = e.relation === 'isPartOf' ? pal.body : pal.activity
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = e.relation === 'isPartOf' ? pal.body : pal.activity
    ctx.fillText(text, pos.x, pos.y + 0.5)
    ctx.restore()
  }

  // 提案の札（上の帯）: 提案は薄い塗り＋「提案」の印、図に入れたものは濃く（印なし）。外す提案は打ち消し。
  for (const n of layout.nodes.values()) {
    if (n.type !== 'draft') continue
    drawDraftCard(ctx, n, pal, labels, focus === n.id, alphaOf(n.id) * A, scaleOf(n.id))
  }

  // 提案の線の小さな札（線の中ほど）
  for (const e of layout.edges) {
    if (e.kind !== 'draft' || !e.draft) continue
    const g = conceptEdgeGeom(layout, e)
    if (!g) continue
    const d = e.draft
    const text = draftPillText(d, labels)
    if (!text) continue
    const part = d.rel === 'part' || d.rel === 'sub'
    ctx.save()
    ctx.globalAlpha = Math.min(alphaOf(e.from), alphaOf(e.to)) * draftLineAlpha(d, st.selectedEdge === draftEdgeId(d)) * A
    ctx.font = `600 ${text.length > 1 ? 9.5 : 11}px ${pal.font}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    const w = ctx.measureText(text).width + 10
    const pos = e.pill !== undefined ? e.pill : conceptPillPos(layout, g, w)
    if (!pos) {
      ctx.restore()
      continue
    }
    roundRect(ctx, pos.x - w / 2, pos.y - 8, w, 16, 8)
    ctx.fillStyle = pal.surface
    ctx.fill()
    ctx.strokeStyle = d.problem ? pal.accent : part ? pal.body : pal.activity
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = ctx.strokeStyle
    ctx.fillText(text, pos.x, pos.y + 0.5)
    ctx.restore()
  }

  // 標準用語（地平線）
  for (const n of layout.nodes.values()) {
    if (n.type !== 'std') continue
    ctx.save()
    ctx.globalAlpha = alphaOf(n.id) * A
    ctx.font = `600 11px ${pal.font}`
    const text = fitText(ctx, n.label, n.w - 22)
    const w = Math.min(n.w, ctx.measureText(text).width + 22)
    const h = n.h
    roundRect(ctx, n.x - w / 2, n.y - h / 2, w, h, 6)
    ctx.fillStyle = pal.accentSoft
    ctx.fill()
    ctx.strokeStyle = withAlpha(pal.accent, 0.7)
    ctx.lineWidth = focus === n.id ? 2 : 1
    ctx.stroke()
    ctx.fillStyle = pal.accent
    ctx.textBaseline = 'middle'
    ctx.fillText(text, n.x, n.y + 0.5)
    ctx.font = `500 9px ${pal.mono}`
    ctx.fillStyle = pal.faint
    ctx.textBaseline = 'top'
    ctx.fillText(n.prefix, n.x, n.y + h / 2 + 2)
    ctx.restore()
  }
  ctx.textAlign = 'start'
}

/**
 * 「結べそうな線」（公開のあとに機械が数えた名前の完全一致）を、薄い実線で重ねる。星 → 用語の札。
 * 確かめていないものの符号は 6b の下書きの札と同じ（薄い。点線は使わない — 利用者の好み）— 線になるのは人が決めたあと。
 * 選んでいる点があれば、その点につながる線だけを少し濃くする。
 */
export function drawSuggestions(
  ctx: CanvasRenderingContext2D,
  layout: SkyLayout,
  pal: SkyPalette,
  edges: readonly { from: string; to: string }[],
  focusId: string | null,
): void {
  if (edges.length === 0) return
  ctx.save()
  ctx.lineWidth = 1.2
  ctx.strokeStyle = pal.activity
  for (const e of edges) {
    const a = layout.nodes.get(e.from)
    const c = layout.nodes.get(e.to)
    if (!a || !c) continue
    const on = !!focusId && (e.from === focusId || e.to === focusId)
    ctx.globalAlpha = on ? 0.6 : 0.22
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.bezierCurveTo(a.x, a.y - 70, c.x, c.y + 80, c.x, c.y + 14)
    ctx.stroke()
  }
  ctx.restore()
}
