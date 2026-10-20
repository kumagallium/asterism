// 星図の配置（純関数）。参考実装 `docs/design/sky-chart-mock/skychart.js` の `layout()` の移植。
//
//   種類 = 星（大きさ＝件数の対数・面積比例）／データセット = 星座／用語 = 上の帯（札）／
//   標準用語 = 下の帯（地平線）／つながり（ハブ）= 星座のあいだの橋の段（4 芒星）。
//
// 守ること:
//   ・乱数も時刻も使わない。同じデータ・同じ幅は同じ絵（初期位置は名前のハッシュ）。
//   ・増分配置: データセットは作成の古い順に並べ、新しいものは末尾に足す。星座は「列の
//     升」に前から詰めるので、あとから足したデータセットが既存の星座を動かさない
//     （帯の札・ハブは重心に置くため、星が増えれば動いてよい）。
//   ・規模: 種類 ≤ 60 かつ データセット ≤ 8 は星のまま。超えたらデータセットを 1 つの丸に畳む。
//   ・帯の札は重ならない。横に並べきれなければ段（レーン）を足して帯を押し広げる。
import type { DraftSky, SkyData, SkyDataset, SkyHub, SkyKnowledgeRelation, SkyTerm } from './skyApi'

// ── 決定論のハッシュ（モックと同じ） ──────────────────────────────
export function fnv1a(str: string, seed?: number): number {
  let h = (seed === undefined ? 0x811c9dc5 : seed) >>> 0
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h >>> 0
}

export function hash01(str: string, salt: string): number {
  const h = fnv1a(`${str}|${salt}`)
  const m = (h ^ (h >>> 16)) >>> 0
  return (m % 100000) / 100000
}

// ── 寸法（px）。モックの 620px の高さ・1 行のときと同じ値になるように決めてある ──
/** 星のままにする上限（種類・データセット）。超えたらデータセットを 1 つの丸にする。 */
export const STAR_MAX_KINDS = 60
export const STAR_MAX_DATASETS = 8
/** 図の左右の余白。 */
export const PAD = 28
/** 星座・列のあいだ。 */
export const GAP = 26
/** 星座の高さの下限・星座の下の余白（行の高さ = 星座の高さ + 40）。 */
export const CLUSTER_H_MIN = 220
const ROW_PAD = 40
/** 星 1 つに見込む面積（星どうしが重ならない広さ）。星が多い星座ほど詰まるので広く見込む。 */
export function starArea(n: number): number {
  return 6500 + 5500 * Math.min(1, Math.max(0, (n - 8) / 32))
}
/** 星座の中の反発・引力を回す回数（モックは 70。星が多いときは足す）。 */
export function relaxIterations(n: number): number {
  return Math.min(300, 70 + Math.max(0, n - 12) * 6)
}
/** 帯の段の高さ。 */
export const LANE_H = 50
/** 丸モード（札が多い）の段の高さ。 */
export const LANE_H_COMPACT = 40
/** 帯の最上段の y（用語の札の中心）・データの帯の上端。モック（H=620）と同じ。 */
export const NAMES_Y0 = 99
export const MID_TOP0 = 186
/** データの帯の高さの下限（モックの 1 行ぶん。データが 0 件でも潰さない）。 */
export const MID_MIN = 260
/** ハブの段（星座の下）・標準用語の帯。 */
export const HUB_OFFSET = 36
export const HUB_LANE_H = 40
export const STD_OFFSET = 131
export const HEIGHT_MIN = 620
/** 札の幅の上限・高さ。 */
export const TERM_MAX_W = 170
export const TERM_H = 28
export const STD_MAX_W = 160
export const STD_H = 26
/** 丸モード: 列の幅・行の高さ。 */
export const CIRCLE_COL_W = 118
export const CIRCLE_ROW_H = 120

/** 星の名前の幅の近似（11px × 文字数）。星のラベルの重なりを決めるのにも使う。 */
export const LABEL_PX = 11
export function approxLabelWidth(text: string, px: number = LABEL_PX): number {
  return px * [...text].length
}

// ── 型 ───────────────────────────────────────────────────────────
export type SkyNodeType = 'kind' | 'dataset' | 'term' | 'std' | 'hub' | 'draft'

interface NodeBase {
  id: string
  x: number
  y: number
  label: string
}
export interface KindNode extends NodeBase {
  type: 'kind'
  iri: string
  datasetId: string
  count: number
  /** 半径（件数の対数・面積比例）。 */
  r: number
}
export interface DatasetNode extends NodeBase {
  type: 'dataset'
  datasetId: string
  count: number
  kindCount: number
  kindIris: string[]
  r: number
}
export interface TermNode extends NodeBase {
  type: 'term'
  iri: string
  termKind: 'class' | 'property'
  /** 札の幅・高さ。 */
  w: number
  h: number
  /** 入っている点（星または丸）の id。 */
  memberIds: string[]
  stdId: string | null
  /** 段（0 が星に近い側）。 */
  lane: number
}
export interface StdNode extends NodeBase {
  type: 'std'
  iri: string
  prefix: string
  w: number
  h: number
  lane: number
}
export interface HubNode extends NodeBase {
  type: 'hub'
  hubId: string
  memberIds: string[]
  w: number
  lane: number
}
/** 下書きの札（まだ確かめていない概念）。枠だけで描き、押すと「下書きの札」の欄が出る。 */
export interface DraftNode extends NodeBase {
  type: 'draft'
  draftId: string
  themeLabel: string
  conceptKey: string
  termKind: 'class' | 'property'
  /** この下書きの、札になる概念の数。 */
  conceptCount: number
  w: number
  h: number
  lane: number
}
export type SkyNode = KindNode | DatasetNode | TermNode | StdNode | HubNode | DraftNode

export type SkyEdgeKind = 'link' | 'member' | 'termStd' | 'kindStd' | 'hub' | 'concept'
export interface SkyEdge {
  kind: SkyEdgeKind
  /** member: 点（星・丸）→ 用語。termStd: 用語 → 標準用語。kindStd: 点 → 標準用語。hub: 点 → ハブ。link: 星 → 星。 */
  from: string
  to: string
  /** member だけ: 同じもの（矢じり無し）。 */
  eq?: boolean
  /** concept だけ（用語 → 用語の知識の線）: 一部・下げる・上げる・変える。`from` が主語。 */
  relation?: SkyKnowledgeRelation
}

export interface SkyCluster {
  datasetId: string
  label: string
  /** 箱の中心と大きさ。星はこの中に収まる。 */
  cx: number
  cy: number
  w: number
  h: number
  /** 名前を出してよい幅（隣の星座に食い込まない）。 */
  labelMaxW: number
  nodeIds: string[]
}

export interface SkyBands {
  /** 用語の帯: 段の数・最上段の y・帯の名前の y。 */
  namesLanes: number
  namesLabelY: number
  midTop: number
  midBot: number
  hubTop: number
  hubLanes: number
  stdLanes: number
  /** 地平線の y（曲線の両端）・標準用語の帯の名前の y。 */
  horizonY: number
  stdLabelY: number
}

export type SkyMode = 'stars' | 'circles'

export interface SkyLayout {
  width: number
  height: number
  mode: SkyMode
  bands: SkyBands
  nodes: Map<string, SkyNode>
  clusters: SkyCluster[]
  edges: SkyEdge[]
}

export interface LayoutOptions {
  width: number
  /** 用語の名前の言語。 */
  lang?: 'ja' | 'en'
  /** 高さの下限（既定 620）。 */
  minHeight?: number
  /** 星／丸の強制（試験用）。 */
  mode?: SkyMode | 'auto'
  /** 下書きの札（`GET /api/themes/drafts/sky`）。上の帯に、用語の札のあいだへ足す。 */
  drafts?: readonly DraftSky[]
}

// ── id ───────────────────────────────────────────────────────────
export const kindId = (datasetId: string, iri: string): string => `k|${datasetId}|${iri}`
export const datasetNodeId = (datasetId: string): string => `d|${datasetId}`
export const termId = (iri: string): string => `t|${iri}`
export const stdId = (iri: string): string => `s|${iri}`
export const hubNodeId = (id: string): string => `h|${id}`
export const draftNodeId = (draftId: string, key: string): string => `draft|${draftId}|${key}`

/** 下書きの概念のうち札にするもの（新しく作る語・標準の語と結んで作る語）。既にある語は札を足さない。 */
export function draftCardConcepts(d: DraftSky): DraftSky['concepts'] {
  return d.concepts.filter((c) => !c.use.startsWith('existing:'))
}

/** 用語の表示名（言語に合わせる。無ければ日本語名・slug）。 */
export function termLabel(t: SkyTerm, lang: 'ja' | 'en' = 'ja'): string {
  if (lang === 'en' && t.label_en) return t.label_en
  return t.label || t.label_en || t.slug
}

/** 作成の古い順（同時刻は id）。新しいデータセットは常に末尾。 */
export function orderedDatasets(sky: SkyData): SkyDataset[] {
  return [...sky.datasets].sort((a, b) =>
    a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0,
  )
}

export function chooseMode(sky: SkyData): SkyMode {
  const kinds = sky.datasets.reduce((s, d) => s + d.kinds.length, 0)
  return kinds <= STAR_MAX_KINDS && sky.datasets.length <= STAR_MAX_DATASETS ? 'stars' : 'circles'
}

const isEq = (rel: string): boolean => rel === 'equivalentClass' || rel === 'equivalentProperty'

// ── 帯の段詰め ───────────────────────────────────────────────────
interface LaneItem {
  id: string
  /** 置きたい x（メンバーの重心。無ければハッシュ）。 */
  x: number
  w: number
}
interface LanePlaced {
  id: string
  x: number
  lane: number
}

/** 段を 1 つ増やす代わりに、置きたい位置からずらしてよい距離（px）。 */
const LANE_PENALTY = 160

/**
 * 横に並べる帯の札を段に詰める。x の小さい順に、置きたい位置に一番近い空きへ置く。
 * 段を増やすより、いまある段で ${LANE_PENALTY}px ずらすほうを選ぶ（帯を高くしすぎない）。
 * 段の数はここで決まり、帯の高さはそれに従う。札どうしは重ならない・枠の外に出ない。
 */
export function placeLanes(items: LaneItem[], width: number, gap = 12): { placed: LanePlaced[]; lanes: number } {
  const sorted = [...items].sort((a, b) => a.x - b.x || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const lanes: { l: number; r: number }[][] = []
  const placed: LanePlaced[] = []
  for (const it of sorted) {
    const half = it.w / 2
    const lo = PAD + half
    const hi = Math.max(lo, width - PAD - half)
    const want = Math.min(hi, Math.max(lo, it.x))
    let best: { lane: number; x: number; cost: number } | null = null
    const consider = (lane: number, x: number, taken: { l: number; r: number }[]): void => {
      if (x < lo - 1e-9 || x > hi + 1e-9) return
      if (taken.some((o) => x - half < o.r + gap && x + half > o.l - gap)) return
      const cost = Math.abs(x - want) + lane * LANE_PENALTY
      if (!best || cost < best.cost - 1e-9 || (Math.abs(cost - best.cost) <= 1e-9 && lane < best.lane)) {
        best = { lane, x, cost }
      }
    }
    lanes.forEach((taken, lane) => {
      consider(lane, want, taken)
      for (const o of taken) {
        consider(lane, o.r + gap + half, taken)
        consider(lane, o.l - gap - half, taken)
      }
    })
    // 新しい段（空なので置きたい位置にそのまま置ける）
    const fresh = lanes.length
    if (!best || fresh * LANE_PENALTY < (best as { cost: number }).cost) best = { lane: fresh, x: want, cost: fresh * LANE_PENALTY }
    const pick = best as { lane: number; x: number }
    if (pick.lane === lanes.length) lanes.push([])
    lanes[pick.lane].push({ l: pick.x - half, r: pick.x + half })
    placed.push({ id: it.id, x: pick.x, lane: pick.lane })
  }
  return { placed, lanes: lanes.length }
}

// ── 本体 ─────────────────────────────────────────────────────────
export function layoutSky(sky: SkyData, opts: LayoutOptions): SkyLayout {
  const W = Math.max(320, Math.round(opts.width))
  const lang = opts.lang ?? 'ja'
  const mode: SkyMode = !opts.mode || opts.mode === 'auto' ? chooseMode(sky) : opts.mode
  const datasets = orderedDatasets(sky)
  const nodes = new Map<string, SkyNode>()
  const clusters: SkyCluster[] = []
  const edges: SkyEdge[] = []

  // 丸モードの列の升（幅だけで決まる。データの中身に依らない）。
  const cols = Math.max(1, Math.floor((W - PAD * 2 + GAP) / CIRCLE_COL_W))

  // 星座の箱: 幅は星の数（と名前の長さ）で、高さは星が重ならない広さで決まる。
  const boxes = datasets.map((d) => {
    const n = d.kinds.length
    let w = Math.max(104, 46 * Math.sqrt(n) + 58, Math.min(240, approxLabelWidth(d.label) + 20))
    // 星の名前（11px × 文字数）が 2 列は並べる幅にする。名前が重なって出なくなるのを減らす。
    if (n > 1) {
      const nameW = Math.max(...d.kinds.map((k) => Math.min(190, Math.max(approxLabelWidth(k.label), 56))))
      w = Math.max(w, Math.min(W - PAD * 2, 2 * nameW + 30))
    }
    // 星が多い（9 つ以上）星座は、縦に伸びすぎないよう横にも広げる（図の幅まで）。
    if (n > 8) w = Math.max(w, Math.min(W - PAD * 2, Math.sqrt(n * starArea(n) * 1.4)))
    const h = Math.max(CLUSTER_H_MIN, (n * starArea(n)) / w)
    return { d, w, h }
  })

  // 前から詰める。星のときは左から幅いっぱいに並べて、入らなければ次の行へ。丸のときは同じ幅の升。
  // どちらも「ここまでに置いたもの」だけで位置が決まる（あとから足した星座が前を動かさない）。
  interface Slot {
    /** 箱の左端。 */
    x: number
    row: number
  }
  const slots: Slot[] = []
  const rowHeights: number[] = []
  {
    let row = 0
    let x = PAD
    let col = 0
    for (const b of boxes) {
      if (mode === 'stars') {
        if (x > PAD && x + b.w > W - PAD) {
          row++
          x = PAD
        }
        slots.push({ x, row })
        x += b.w + GAP
        rowHeights[row] = Math.max(rowHeights[row] ?? 0, b.h + ROW_PAD)
      } else {
        if (col >= cols) {
          row++
          col = 0
        }
        slots.push({ x: PAD + col * CIRCLE_COL_W, row })
        col++
        rowHeights[row] = CIRCLE_ROW_H
      }
    }
  }

  // 用語の帯の段数は先に決めたい（星の y の基準 midTop が段数で動く）が、段詰めは星の x が
  // 要る。星の x は y に依らないので、まず y を midTop 基準の相対で置き、帯が決まってから足す。
  // 星の x・基準からの相対 y は、あとから足すデータセットで動かない。
  interface Rel {
    id: string
    x: number
    relY: number
  }
  const rels: Rel[] = []
  const rowTops: number[] = []
  {
    let acc = 0
    for (let r = 0; r < rowHeights.length; r++) {
      rowTops.push(acc)
      acc += rowHeights[r]
    }
  }
  const midHeight = Math.max(MID_MIN, rowHeights.reduce((s, h) => s + h, 0))

  // 件数 → 半径（表示中の最小〜最大で対数・面積比例）
  const kindCounts: number[] = []
  const dsCounts = new Map<string, number>()
  for (const d of datasets) {
    let total = 0
    for (const k of d.kinds) {
      total += k.count
      if (k.count > 0) kindCounts.push(Math.log10(k.count))
    }
    dsCounts.set(d.id, total)
  }
  const kLo = kindCounts.length ? Math.min(...kindCounts) : 0
  const kHi = kindCounts.length ? Math.max(...kindCounts) : 1
  const starR = (count: number): number => {
    const t = kHi > kLo && count > 0 ? (Math.log10(count) - kLo) / (kHi - kLo) : 0.3
    return 5 + 9 * Math.sqrt(t)
  }
  const dsLogs = [...dsCounts.values()].filter((c) => c > 0).map((c) => Math.log10(c))
  const dLo = dsLogs.length ? Math.min(...dsLogs) : 0
  const dHi = dsLogs.length ? Math.max(...dsLogs) : 1
  const circleR = (count: number): number => {
    const t = dHi > dLo && count > 0 ? (Math.log10(count) - dLo) / (dHi - dLo) : 0.3
    return 12 + 16 * Math.sqrt(t)
  }

  const stdKnown = new Set(sky.standards.map((s) => s.iri))
  const stdByIri = new Map(sky.standards.map((s) => [s.iri, s]))

  if (mode === 'stars') {
    boxes.forEach((b, bi) => {
      const sl = slots[bi]
      const cx = sl.x + b.w / 2
      // 行の上端から置く（行の高さは後ろの星座で伸びうるので、中央には置かない）。
      const cyRel = rowTops[sl.row] + ROW_PAD / 2 + b.h / 2
      const ks = b.d.kinds
      const ids = ks.map((k) => kindId(b.d.id, k.iri))
      clusters.push({
        datasetId: b.d.id,
        label: b.d.label,
        cx,
        cy: cyRel, // 相対。あとで midTop を足す。
        w: b.w,
        h: b.h,
        labelMaxW: Math.max(60, b.w + GAP - 12),
        nodeIds: ids,
      })
      const ch = b.h
      // 星座の中: ハッシュで初期位置 → 反発＋つながりの引力＋枠に収める（固定 70 回）
      const pts = ks.map((k, i) => {
        const a = hash01(ids[i], 'angle') * Math.PI * 2
        const r = 0.25 + 0.6 * hash01(ids[i], 'radius')
        return {
          k,
          id: ids[i],
          x: cx + Math.cos(a) * r * b.w * 0.4,
          y: cyRel + Math.sin(a) * r * ch * 0.42,
          vx: 0,
          vy: 0,
        }
      })
      const byIri = new Map(pts.map((p) => [p.k.iri, p]))
      // 星どうしの間隔。名前（丸の下に 2 行）が隣の丸・名前に被らないよう 110px 空ける。
      // 星が多い（9 つ以上）星座は面積が足りないので、72px まで詰める。
      const wantGap = 110 - 38 * Math.min(1, Math.max(0, (pts.length - 8) / 32))
      const hx = b.w / 2 - 30
      const hy = ch / 2 - 26
      for (let it = 0; it < relaxIterations(pts.length); it++) {
        for (const p of pts) {
          p.vx = 0
          p.vy = 0
        }
        for (let i = 0; i < pts.length; i++) {
          for (let j = i + 1; j < pts.length; j++) {
            const a = pts[i]
            const c = pts[j]
            let dx = c.x - a.x
            let dy = c.y - a.y
            let d2 = dx * dx + dy * dy
            if (d2 < 1) {
              dx = 0.5
              dy = 0.3
              d2 = 0.34
            }
            const d = Math.sqrt(d2)
            const want = wantGap
            if (d < want) {
              const f = ((want - d) / d) * 0.5
              a.vx -= dx * f
              a.vy -= dy * f
              c.vx += dx * f
              c.vy += dy * f
            }
          }
        }
        for (const p of pts) {
          for (const lid of p.k.links) {
            const q = byIri.get(lid)
            if (!q || q === p) continue
            const dx = q.x - p.x
            const dy = q.y - p.y
            const d = Math.sqrt(dx * dx + dy * dy) || 1
            const want = 84
            const f = ((d - want) / d) * 0.08
            p.vx += dx * f
            p.vy += dy * f
            q.vx -= dx * f
            q.vy -= dy * f
          }
        }
        for (const p of pts) {
          p.x += p.vx
          p.y += p.vy
          p.x = Math.max(cx - hx, Math.min(cx + hx, p.x))
          p.y = Math.max(cyRel - hy, Math.min(cyRel + hy, p.y))
        }
      }
      for (const p of pts) {
        const node: KindNode = {
          id: p.id,
          type: 'kind',
          x: p.x,
          y: 0,
          label: p.k.label,
          iri: p.k.iri,
          datasetId: b.d.id,
          count: p.k.count,
          r: starR(p.k.count),
        }
        nodes.set(p.id, node)
        rels.push({ id: p.id, x: p.x, relY: p.y })
        for (const lid of p.k.links) {
          if (lid !== p.k.iri && byIri.has(lid)) {
            edges.push({ kind: 'link', from: p.id, to: kindId(b.d.id, lid) })
          }
        }
        if (p.k.standard && stdKnown.has(p.k.standard)) {
          edges.push({ kind: 'kindStd', from: p.id, to: stdId(p.k.standard) })
        }
      }
    })
  } else {
    boxes.forEach((b, bi) => {
      const sl = slots[bi]
      const cx = sl.x + (CIRCLE_COL_W - GAP) / 2
      const cyRel = rowTops[sl.row] + rowHeights[sl.row] / 2 - 8
      const id = datasetNodeId(b.d.id)
      const count = dsCounts.get(b.d.id) ?? 0
      nodes.set(id, {
        id,
        type: 'dataset',
        x: cx,
        y: 0,
        label: b.d.label,
        datasetId: b.d.id,
        count,
        kindCount: b.d.kinds.length,
        kindIris: b.d.kinds.map((k) => k.iri),
        r: circleR(count),
      })
      rels.push({ id, x: cx, relY: cyRel })
      for (const k of b.d.kinds) {
        if (k.standard && stdKnown.has(k.standard)) {
          const e: SkyEdge = { kind: 'kindStd', from: id, to: stdId(k.standard) }
          if (!edges.some((x) => x.kind === 'kindStd' && x.from === e.from && x.to === e.to)) edges.push(e)
        }
      }
    })
  }

  // 点（星・丸）の x の索引: 帯の重心の計算に使う。
  const xOf = (id: string): number | undefined => nodes.get(id)?.x
  const pointIdsOfKindIri = new Map<string, string[]>()
  for (const d of datasets) {
    for (const k of d.kinds) {
      const id = mode === 'stars' ? kindId(d.id, k.iri) : datasetNodeId(d.id)
      const arr = pointIdsOfKindIri.get(k.iri) ?? []
      if (!arr.includes(id)) arr.push(id)
      pointIdsOfKindIri.set(k.iri, arr)
    }
  }

  // ── 用語（上の帯） ──
  const termItems: LaneItem[] = []
  const termMeta = new Map<string, { t: SkyTerm; label: string; w: number; memberIds: string[]; stdId: string | null }>()
  for (const t of sky.terms) {
    const label = termLabel(t, lang)
    const seen = new Set<string>()
    const memberIds: string[] = []
    for (const m of t.members) {
      const id = mode === 'stars' ? kindId(m.dataset_id, m.iri) : datasetNodeId(m.dataset_id)
      if (!nodes.has(id) || seen.has(id)) continue
      seen.add(id)
      memberIds.push(id)
    }
    const std = t.standard && stdKnown.has(t.standard.iri) ? stdId(t.standard.iri) : null
    // 札の幅は名前（12px の太字・余裕をみて 13px × 文字数）と、下の「≡ 標準用語」の字のどちらか広いほう。
    const stdInfo = std ? stdByIri.get(t.standard!.iri) : undefined
    const stdTextW = stdInfo ? (`≡ ${stdInfo.prefix}:${stdInfo.label}`.length * 5.8 + 8) : 0
    const w = Math.min(TERM_MAX_W, Math.max(approxLabelWidth(label, 13) + 26, stdTextW))
    const xs = memberIds.map((i) => xOf(i) as number)
    const x = xs.length
      ? xs.reduce((s, v) => s + v, 0) / xs.length
      : PAD + 60 + (W - PAD * 2 - 120) * hash01(t.iri, 'bandx')
    termItems.push({ id: termId(t.iri), x, w })
    termMeta.set(termId(t.iri), { t, label, w, memberIds, stdId: std })
  }
  // 下書きの札: 同じ下書きは同じ x に置きたがる（ハッシュ）。札どうしは重ならない
  const draftMeta = new Map<string, { d: DraftSky; c: DraftSky['concepts'][number]; w: number; count: number }>()
  for (const d of opts.drafts ?? []) {
    const cards = draftCardConcepts(d)
    const base = PAD + 60 + (W - PAD * 2 - 120) * hash01(d.draft_id, 'draftx')
    for (const c of cards) {
      const id = draftNodeId(d.draft_id, c.key)
      const w = Math.min(TERM_MAX_W, approxLabelWidth(c.label, 13) + 26)
      termItems.push({ id, x: base, w })
      draftMeta.set(id, { d, c, w, count: cards.length })
    }
  }
  const termPlace = placeLanes(termItems, W)

  // ── 標準用語（下の帯） ──
  const stdItems: LaneItem[] = []
  const stdMeta = new Map<string, { s: SkyData['standards'][number]; w: number }>()
  const stdXs = new Map<string, number[]>()
  for (const [tid, m] of termMeta) {
    if (!m.stdId) continue
    const x = termPlace.placed.find((p) => p.id === tid)?.x
    if (x !== undefined) stdXs.set(m.stdId, [...(stdXs.get(m.stdId) ?? []), x])
  }
  for (const e of edges) {
    if (e.kind !== 'kindStd') continue
    const x = xOf(e.from)
    if (x !== undefined) stdXs.set(e.to, [...(stdXs.get(e.to) ?? []), x])
  }
  for (const s of sky.standards) {
    const w = Math.min(STD_MAX_W, approxLabelWidth(s.label, 11) + 22)
    const xs = stdXs.get(stdId(s.iri)) ?? []
    const x = xs.length
      ? xs.reduce((a, v) => a + v, 0) / xs.length
      : PAD + 60 + (W - PAD * 2 - 120) * hash01(s.iri, 'bandx')
    stdItems.push({ id: stdId(s.iri), x, w })
    stdMeta.set(stdId(s.iri), { s, w })
  }
  const stdPlace = placeLanes(stdItems, W)

  // ── ハブ（星座の下の橋の段） ──
  const hubItems: LaneItem[] = []
  const hubMeta = new Map<string, { h: SkyHub; memberIds: string[]; w: number }>()
  for (const h of sky.hubs) {
    const ids: string[] = []
    for (const iri of h.members) for (const id of pointIdsOfKindIri.get(iri) ?? []) if (!ids.includes(id)) ids.push(id)
    const xs = ids.map((i) => xOf(i) as number)
    const x = xs.length ? xs.reduce((a, v) => a + v, 0) / xs.length : W / 2
    const w = Math.max(40, approxLabelWidth(h.label, 11))
    hubItems.push({ id: hubNodeId(h.id), x, w })
    hubMeta.set(hubNodeId(h.id), { h, memberIds: ids, w })
  }
  const hubPlace = placeLanes(hubItems, W)

  // ── 帯の y ──（丸モードは札が多いので段の間隔を詰める）
  const laneH = mode === 'stars' ? LANE_H : LANE_H_COMPACT
  const namesLanes = Math.max(1, termPlace.lanes)
  const midTop = MID_TOP0 + (namesLanes - 1) * laneH
  const midBot = midTop + midHeight
  const hubLanes = hubPlace.lanes
  const hubTop = midBot + HUB_OFFSET
  const stdTop = midBot + HUB_OFFSET + Math.max(1, hubLanes) * HUB_LANE_H + 55
  const stdLanes = Math.max(1, stdPlace.lanes)
  const height = Math.max(opts.minHeight ?? HEIGHT_MIN, stdTop + (stdLanes - 1) * laneH + 43)
  const bands: SkyBands = {
    namesLanes,
    namesLabelY: NAMES_Y0 - 30,
    midTop,
    midBot,
    hubTop,
    hubLanes,
    stdLanes,
    horizonY: stdTop - 16,
    stdLabelY: stdTop - 28,
  }

  // 相対 y を絶対にする
  for (const r of rels) {
    const n = nodes.get(r.id)
    if (n) n.y = midTop + r.relY
  }
  for (const c of clusters) c.cy += midTop

  // 用語の札: 段 0 が星に近い（下）・最上段が NAMES_Y0
  for (const p of termPlace.placed) {
    const y = NAMES_Y0 + (namesLanes - 1 - p.lane) * laneH
    const dm = draftMeta.get(p.id)
    if (dm) {
      const node: DraftNode = {
        id: p.id,
        type: 'draft',
        x: p.x,
        y,
        label: dm.c.label,
        draftId: dm.d.draft_id,
        themeLabel: dm.d.theme_label,
        conceptKey: dm.c.key,
        termKind: dm.c.kind,
        conceptCount: dm.count,
        w: dm.w,
        h: TERM_H,
        lane: p.lane,
      }
      nodes.set(p.id, node)
      continue
    }
    const m = termMeta.get(p.id)!
    const node: TermNode = {
      id: p.id,
      type: 'term',
      x: p.x,
      y,
      label: m.label,
      iri: m.t.iri,
      termKind: m.t.kind,
      w: m.w,
      h: TERM_H,
      memberIds: m.memberIds,
      stdId: m.stdId,
      lane: p.lane,
    }
    nodes.set(p.id, node)
    const rel = new Map(m.t.members.map((mm) => [mm.dataset_id + '\u0001' + mm.iri, mm.relation]))
    for (const mid of m.memberIds) {
      // 点 → 用語。同じもの（≡）は矢じり無し。丸モードでは 1 つでも ⊂ があれば矢じりを付ける。
      let eq: boolean
      if (mode === 'stars') {
        const kn = nodes.get(mid) as KindNode
        eq = isEq(rel.get(kn.datasetId + '\u0001' + kn.iri) ?? 'subClassOf')
      } else {
        const dsId = (nodes.get(mid) as DatasetNode).datasetId
        eq = m.t.members.filter((mm) => mm.dataset_id === dsId).every((mm) => isEq(mm.relation))
      }
      edges.push({ kind: 'member', from: mid, to: p.id, eq })
    }
    if (m.stdId) edges.push({ kind: 'termStd', from: p.id, to: m.stdId })
  }
  for (const p of stdPlace.placed) {
    const m = stdMeta.get(p.id)!
    const node: StdNode = {
      id: p.id,
      type: 'std',
      x: p.x,
      y: stdTop + p.lane * laneH,
      label: m.s.label,
      iri: m.s.iri,
      prefix: m.s.prefix,
      w: m.w,
      h: STD_H,
      lane: p.lane,
    }
    nodes.set(p.id, node)
  }
  for (const p of hubPlace.placed) {
    const m = hubMeta.get(p.id)!
    const node: HubNode = {
      id: p.id,
      type: 'hub',
      x: p.x,
      y: hubTop + p.lane * HUB_LANE_H,
      label: m.h.label,
      hubId: m.h.id,
      memberIds: m.memberIds,
      w: m.w,
      lane: p.lane,
    }
    nodes.set(p.id, node)
    for (const mid of m.memberIds) edges.push({ kind: 'hub', from: mid, to: p.id })
  }

  // 語どうしの知識の線（一部・下げる・上げる・変える）。札の位置は動かさず、線だけを足す。
  const conceptSeen = new Set<string>()
  for (const l of sky.edges ?? []) {
    const from = termId(l.from)
    const to = termId(l.to)
    const key = `${from}>${to}>${l.relation}`
    if (from === to || conceptSeen.has(key) || nodes.get(from)?.type !== 'term' || nodes.get(to)?.type !== 'term') continue
    conceptSeen.add(key)
    edges.push({ kind: 'concept', from, to, relation: l.relation })
  }

  return { width: W, height, mode, bands, nodes, clusters, edges }
}

export interface ConceptEdgeGeom {
  /** 始点・終点（札の縁）と、曲げの制御点。 */
  x1: number
  y1: number
  cx: number
  cy: number
  x2: number
  y2: number
  /** 線の中ほど（小さな札を置く所）。 */
  mx: number
  my: number
}

/** 札の中心から相手へ向かう線が、札の縁（半幅 hw・半高 hh）と交わる点。 */
function clipToCard(x: number, y: number, hw: number, hh: number, tx: number, ty: number): { x: number; y: number } {
  const dx = tx - x
  const dy = ty - y
  if (dx === 0 && dy === 0) return { x, y }
  const k = Math.min(dx === 0 ? Infinity : hw / Math.abs(dx), dy === 0 ? Infinity : hh / Math.abs(dy))
  return { x: x + dx * k, y: y + dy * k }
}

/**
 * 用語の札 2 つを結ぶ知識の線の形（札の縁から縁へ・少し曲げる）。曲げる向きは進む向きで決まる
 * （同じ 2 点を逆向きに結ぶ線は反対の側に曲がり、重ならない）。決定論。どちらかが用語の札でなければ null。
 */
export function conceptEdgeGeom(layout: SkyLayout, e: SkyEdge): ConceptEdgeGeom | null {
  const a = layout.nodes.get(e.from)
  const c = layout.nodes.get(e.to)
  if (!a || !c || a.type !== 'term' || c.type !== 'term') return null
  const pad = 3
  const p1 = clipToCard(a.x, a.y, a.w / 2 + pad, a.h / 2 + pad, c.x, c.y)
  const p2 = clipToCard(c.x, c.y, c.w / 2 + pad, c.h / 2 + pad, a.x, a.y)
  const dx = p2.x - p1.x
  const dy = p2.y - p1.y
  const len = Math.hypot(dx, dy) || 1
  const bow = Math.min(26, len * 0.14) // 進む向きの左へ。逆向きの線は反対の側に曲がる
  const cx = (p1.x + p2.x) / 2 - (dy / len) * bow
  const cy = (p1.y + p2.y) / 2 + (dx / len) * bow
  return { x1: p1.x, y1: p1.y, cx, cy, x2: p2.x, y2: p2.y, mx: 0.25 * p1.x + 0.5 * cx + 0.25 * p2.x, my: 0.25 * p1.y + 0.5 * cy + 0.25 * p2.y }
}

// ── 近傍（hover・選択の濃淡） ─────────────────────────────────────
/**
 * 選んだ点の近傍。自分・直接つながるもの・（星なら）その用語が結ばれている標準用語。
 * モックと同じ規則。つながらない点は薄くする。
 */
export function neighborhood(layout: SkyLayout, id: string | null): Set<string> {
  const near = new Set<string>()
  if (!id) return near
  const n = layout.nodes.get(id)
  if (!n) return near
  near.add(id)
  for (const e of layout.edges) {
    if (e.from === id) near.add(e.to)
    else if (e.to === id) near.add(e.from)
  }
  if (n.type === 'kind' || n.type === 'dataset') {
    // 用語の標準用語まで（星 → 用語 → 標準用語）
    for (const e of layout.edges) {
      if (e.kind === 'member' && e.from === id) {
        const t = layout.nodes.get(e.to)
        if (t?.type === 'term' && t.stdId) near.add(t.stdId)
      }
    }
  }
  return near
}

/** 複数の点の近傍の和（問いを押したときの「要る用語」の強調）。 */
export function neighborhoodOf(layout: SkyLayout, ids: Iterable<string>): Set<string> {
  const out = new Set<string>()
  for (const id of ids) for (const x of neighborhood(layout, id)) out.add(x)
  return out
}

/** IRI（種類・用語）から点の id を引く。`#/home?new=…` の「生まれた輪」に使う。 */
export function nodeIdsForIris(layout: SkyLayout, iris: Iterable<string>): string[] {
  const want = new Set(iris)
  const out: string[] = []
  for (const n of layout.nodes.values()) {
    if ((n.type === 'kind' || n.type === 'term') && want.has(n.iri)) out.push(n.id)
    else if (n.type === 'dataset' && n.kindIris.some((i) => want.has(i))) out.push(n.id)
  }
  return out
}
