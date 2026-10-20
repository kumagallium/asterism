// 星図の描画（canvas 2D）。参考実装 `docs/design/sky-chart-mock/skychart.js` の `draw()` の移植。
//
// 紙の地・薄い格子・星座の線・星（大きさ＝件数）・用語の札（青）・つながり（4 芒星）・
// 標準用語（琥珀の地平線）・hover の濃淡・生まれた輪。夜空の暗色は使わない（forest 単一テーマ）。
// 色は CSS 変数から（`readSkyPalette`）。配置は `skyLayout.ts`（描画は何も決めない）。
import {
  approxLabelWidth,
  conceptEdgeGeom,
  conceptPillPos,
  type DatasetNode,
  type KindNode,
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
  /** 下書きの札の印（枠だけの札の上）。 */
  draft: string
  /** 知識の線の小さな札（「一部」）。下げる／上げる／変えるは記号（↓ ↑ ↕）。 */
  partOf: string
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
  const alphaOf = (id: string): number => (dimmed(id) ? 0.22 : 1)
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
    ctx.globalAlpha = (near && !inCluster ? 0.25 : 1) * A
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
    ctx.globalAlpha = (near && !(near.has(e.from) || near.has(e.to)) ? 0.25 : 1) * A
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
      ctx.fillText(labels.property, n.x, y - 2)
    }
    const std = n.stdId ? layout.nodes.get(n.stdId) : undefined
    if (empty) {
      ctx.font = `500 10px ${pal.font}`
      ctx.fillStyle = pal.faint
      ctx.textBaseline = 'top'
      ctx.fillText(labels.empty, n.x, y + h + 3)
    } else if (std && std.type === 'std') {
      ctx.font = `500 9.5px ${pal.mono}`
      ctx.fillStyle = pal.accent
      ctx.textBaseline = 'top'
      ctx.fillText(fitText(ctx, `≡ ${std.prefix}:${std.label}`, n.w + 8), n.x, y + h + 3)
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
    const pos = conceptPillPos(layout, g, w)
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

  // 下書きの札（上の帯）: 枠だけ（塗らない）＋「下書き」の印。置いたが星なしの札（薄塗り）とは別の符号。
  for (const n of layout.nodes.values()) {
    if (n.type !== 'draft') continue
    const sc = scaleOf(n.id)
    ctx.save()
    ctx.globalAlpha = alphaOf(n.id) * A
    ctx.font = `600 12px ${pal.font}`
    const text = fitText(ctx, n.label, n.w - 26)
    const tw = ctx.measureText(text).width
    const w = Math.min(n.w, tw + 26) * sc
    const h = n.h * sc
    const x = n.x - w / 2
    const y = n.y - h / 2
    roundRect(ctx, x, y, w, h, 14)
    ctx.fillStyle = pal.paper
    ctx.fill()
    ctx.strokeStyle = pal.activity
    ctx.lineWidth = focus === n.id ? 2.5 : 1.4
    ctx.stroke()
    ctx.fillStyle = pal.activity
    ctx.textBaseline = 'middle'
    ctx.fillText(text, n.x, n.y + 0.5)
    // 「下書き」の印（札の上）。項目の札は、印の右に「項目」を添える
    ctx.font = `500 9px ${pal.mono}`
    ctx.fillStyle = pal.accent
    ctx.textBaseline = 'bottom'
    ctx.fillText(n.termKind === 'property' ? `${labels.draft} · ${labels.property}` : labels.draft, n.x, y - 2)
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
