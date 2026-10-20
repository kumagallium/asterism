// 知識の図（②）の配置。純関数 — 描画（ConceptMap）から切り離して vitest で固定する。
//
// 段は `shapeGraph.rowsOf`（かんたん層の「形」の図と同じ段配置）で決める。段を作る線は
// 「一種」「一部」だけ（子 → 親。細かいものが上、粗いものが下）。「下げる／上げる／変える／同じもの」は
// 段を作らず、**同じ段の横の線**にする — 段の中で、相手の概念の左（原因）と右（結果）に、縦に積んだ
// 列として置く（左から右へ「原因 → 結果」と読める）。多対多で交わっても段は崩れない（design.md §15.2）。
// 横の線でつながる相手が段をもつ概念に無いとき（独立の小さな図）は、その図だけの段を下に足す。
// 線のない概念は一番下に折り返して並べる。
//
// 決定論: 同じ下書きは毎回同じ絵（入力順で並べる）。
import type { RelKind } from './themeDraft'
import { rowsOf, type Shape } from '../shapeGraph'

export interface MapNodeIn {
  key: string
  label: string
  /** 札に「項目」の小さな印を付ける（その分だけ幅を足す）。 */
  mark?: boolean
  /** 標準の語（地平線の色の小さな札）。概念ではない — 関係の端として図に出すだけ。 */
  std?: boolean
}
export interface MapRelIn {
  /** 描く線の識別子（文の一覧の行と結ぶ）。 */
  index: number
  from: string
  to: string
  rel: RelKind
  /** 機械が止めた関係（確定から外れる）。段は作らず、横の線として赤く描く。 */
  problem?: boolean
}

export const MAP_NODE_H = 36
/** 列と列の間（札の間に線と札が入る）。 */
export const MAP_GAP_X = 46
/** 段と段の間。 */
export const MAP_GAP_Y = 54
/** 同じ列に積む札の間。 */
export const MAP_STACK_GAP = 12
export const MAP_PAD = 16
/** 図の上の余白。 */
export const MAP_TOP = 16
/** 線のない概念を 1 段に並べる上限。 */
export const MAP_LOOSE_PER_ROW = 5

export interface MapNodePos {
  key: string
  x: number
  y: number
  w: number
  h: number
  row: number
  /** 標準の語の札か。 */
  std: boolean
}

export interface MapEdgeOut {
  index: number
  from: string
  to: string
  rel: RelKind
  /** 段をまたぐ線（一種・一部）か、同じ段の横の線か。 */
  kind: 'hier' | 'side'
  /** SVG の path。 */
  path: string
  /** 札（↓ ↑ など）を置く点。 */
  mid: { x: number; y: number }
  /** 線の終わり（`to` の札につく端）。ここを持ってドラッグすると付け替えになる。 */
  end: { x: number; y: number }
  /** 同じもの（矢じりが両端）。 */
  both: boolean
  problem: boolean
}

export interface MapLayout {
  width: number
  height: number
  nodes: MapNodePos[]
  byKey: Map<string, MapNodePos>
  edges: MapEdgeOut[]
  /** 段ごとの概念の key（左の列から・列の上から）。 */
  rows: string[][]
}

/** 札の幅（名前の長さから。全角 12.5px・半角 7.2px 見当。印があれば 24px 足す）。 */
export function nodeWidth(label: string, mark = false): number {
  let w = 0
  for (const ch of label) w += (ch.codePointAt(0) ?? 0) > 0xff ? 12.5 : 7.2
  return Math.min(200, Math.max(76, Math.round(w + 28 + (mark ? 24 : 0))))
}

const f = (n: number): string => String(Math.round(n * 10) / 10)

type Pt = { x: number; y: number }

/** 3 次ベジェの t=0.5 の点。 */
const cubicMid = (p0: Pt, c1: Pt, c2: Pt, p3: Pt): Pt => ({
  x: 0.125 * p0.x + 0.375 * c1.x + 0.375 * c2.x + 0.125 * p3.x,
  y: 0.125 * p0.y + 0.375 * c1.y + 0.375 * c2.y + 0.125 * p3.y,
})

export interface MapLayoutOptions {
  /** 段（行）を中央に寄せる（既定）か、左端にそろえるか。幅が足りず横にスクロールさせるときは左端にそろえる
   * （中央寄せだと、細い画面で 2 段目が右の見えない所に出る）。 */
  align?: 'center' | 'start'
  /**
   * 図の幅の上限（余白を含む）。超える段は、**段の中で折り返して**複数の行にする。
   * 折り返しは、横の線でつながる一かたまり（原因 → 相手 → 結果）の境目を優先し、1 かたまりだけで
   * 超えるときだけ列の境目で割る。折り返しても段の前後（一種・一部の上下）は変わらない。
   * 省略すると折り返さない。
   */
  maxWidth?: number
}

export function layoutConceptMap(nodes: MapNodeIn[], rels: MapRelIn[], opts: MapLayoutOptions = {}): MapLayout {
  const known = new Set(nodes.map((n) => n.key))
  const stdKeys = new Set(nodes.filter((n) => n.std).map((n) => n.key))
  const valid = rels.filter((r) => r.from !== r.to && known.has(r.from) && known.has(r.to))
  const isHier = (r: MapRelIn): boolean => (r.rel === 'sub' || r.rel === 'part') && !r.problem
  const hier = valid.filter(isHier)
  const side = valid.filter((r) => !isHier(r))

  // ① 段をつくる線だけで段を決める（rowsOf: 子が上・親が下）
  const hierKeys = new Set(hier.flatMap((r) => [r.from, r.to]))
  const shape: Shape = {
    nodes: nodes.filter((n) => hierKeys.has(n.key)).map((n) => ({ id: n.key, label: n.label, tone: 'record' as const })),
    edges: hier.map((r) => ({ from: r.from, to: r.to })),
  }
  const hierRows = shape.nodes.length > 0 ? rowsOf(shape).filter((r) => r.length > 0) : []

  // ② 横の線でつながる概念を、つながる相手の左（原因）・右（結果）の列に置く。
  //    原因の原因はさらに左の列、結果の結果はさらに右の列。
  const placed = new Set(hierKeys)
  const clusterOf = (anchor: string): string[][] => {
    const left: string[][] = []
    const right: string[][] = []
    let frontier = [anchor]
    for (;;) {
      const next: string[] = []
      for (const k of frontier) {
        for (const r of side) {
          if (r.to === k && !placed.has(r.from)) {
            placed.add(r.from)
            next.push(r.from)
          }
        }
      }
      if (next.length === 0) break
      left.push(next)
      frontier = next
    }
    frontier = [anchor]
    for (;;) {
      const next: string[] = []
      for (const k of frontier) {
        for (const r of side) {
          if (r.from === k && !placed.has(r.to)) {
            placed.add(r.to)
            next.push(r.to)
          }
        }
      }
      if (next.length === 0) break
      right.push(next)
      frontier = next
    }
    return [...left.reverse(), [anchor], ...right]
  }
  // 段 = かたまり（横の線でつながる列の並び）の並び。列 = 縦に積む札
  const rowGroups: string[][][][] = hierRows.map((row) => row.map((k) => clusterOf(k)))

  // ③ 段をもつ概念につながらない小さな図は、その図だけの段を下に足す
  const sideKeys = new Set(side.flatMap((r) => [r.from, r.to]))
  for (const n of nodes) {
    if (placed.has(n.key) || !sideKeys.has(n.key)) continue
    placed.add(n.key)
    rowGroups.push([clusterOf(n.key)])
  }
  // ④ 線のない概念は一番下に折り返して並べる（幅の上限があればその幅で・無ければ 1 行 5 つ）
  const loose = nodes.filter((n) => !placed.has(n.key)).map((n) => n.key)
  if (opts.maxWidth === undefined) {
    for (let i = 0; i < loose.length; i += MAP_LOOSE_PER_ROW) {
      rowGroups.push(loose.slice(i, i + MAP_LOOSE_PER_ROW).map((k) => [[k]]))
    }
  } else if (loose.length > 0) {
    rowGroups.push(loose.map((k) => [[k]]))
  }

  // ⑤ 座標: 列の幅は札の最大幅・段の高さは最も高い列・段ごとに中央寄せ
  const widthOf = new Map(nodes.map((n) => [n.key, nodeWidth(n.label, n.mark)]))
  const colW = (col: string[]): number => Math.max(...col.map((k) => widthOf.get(k) ?? 76))
  const colH = (col: string[]): number => col.length * MAP_NODE_H + (col.length - 1) * MAP_STACK_GAP
  const colsW = (cols: string[][]): number =>
    cols.reduce((s, c) => s + colW(c), 0) + Math.max(0, cols.length - 1) * MAP_GAP_X
  // 幅の上限を超える段は折り返す（かたまりの境目を優先・1 かたまりで超えるなら列の境目で割る）
  const limit = opts.maxWidth === undefined ? Infinity : Math.max(0, opts.maxWidth - MAP_PAD * 2)
  const wrap = (groups: string[][][]): string[][][] => {
    const lines: string[][][] = []
    let cur: string[][] = []
    const flush = () => {
      if (cur.length > 0) lines.push(cur)
      cur = []
    }
    for (const g of groups) {
      if (cur.length > 0 && colsW([...cur, ...g]) <= limit) {
        cur = [...cur, ...g]
      } else if (colsW(g) <= limit) {
        flush()
        cur = [...g]
      } else {
        flush()
        for (const col of g) {
          if (cur.length > 0 && colsW([...cur, col]) > limit) flush()
          cur = [...cur, col]
        }
      }
    }
    flush()
    return lines
  }
  const rowCols: string[][][] = rowGroups.flatMap((groups) => (limit === Infinity ? [groups.flat()] : wrap(groups)))
  const rowW = rowCols.map(colsW)
  const rowH = rowCols.map((cols) => Math.max(...cols.map(colH)))
  const inner = Math.max(0, ...rowW)
  const width = inner + MAP_PAD * 2
  const placedNodes: MapNodePos[] = []
  const byKey = new Map<string, MapNodePos>()
  const colOf = new Map<string, number>()
  let y0 = MAP_TOP
  rowCols.forEach((cols, r) => {
    let x = MAP_PAD + (opts.align === 'start' ? 0 : (inner - rowW[r]) / 2)
    cols.forEach((col, ci) => {
      const cw = colW(col)
      let y = y0 + (rowH[r] - colH(col)) / 2
      for (const k of col) {
        const w = widthOf.get(k) ?? 76
        // 列の中では、札を列の幅の中央に置く
        const p: MapNodePos = { key: k, x: x + (cw - w) / 2, y, w, h: MAP_NODE_H, row: r, std: stdKeys.has(k) }
        placedNodes.push(p)
        byKey.set(k, p)
        colOf.set(k, ci)
        y += MAP_NODE_H + MAP_STACK_GAP
      }
      x += cw + MAP_GAP_X
    })
    y0 += rowH[r] + MAP_GAP_Y
  })
  const height = rowCols.length > 0 ? y0 - MAP_GAP_Y + MAP_PAD : 0

  // ⑥ 線
  const edges: MapEdgeOut[] = []
  for (const r of valid) {
    const a = byKey.get(r.from)!
    const b = byKey.get(r.to)!
    const problem = !!r.problem
    const both = r.rel === 'eq'
    let kind: 'hier' | 'side' = isHier(r) ? 'hier' : 'side'
    let path: string
    let mid: Pt
    let endPt: Pt
    const horizontal = a.row === b.row && colOf.get(r.from) !== colOf.get(r.to)
    if (horizontal) {
      // 同じ段の別の列: 向かい合う辺のまん中どうしを、横向きの曲線で結ぶ
      kind = 'side'
      const leftward = a.x > b.x
      const p0 = { x: leftward ? a.x : a.x + a.w, y: a.y + a.h / 2 }
      const p3 = { x: leftward ? b.x + b.w : b.x, y: b.y + b.h / 2 }
      const dx = (p3.x - p0.x) / 2
      const c1 = { x: p0.x + dx, y: p0.y }
      const c2 = { x: p3.x - dx, y: p3.y }
      path = `M${f(p0.x)},${f(p0.y)} C${f(c1.x)},${f(c1.y)} ${f(c2.x)},${f(c2.y)} ${f(p3.x)},${f(p3.y)}`
      mid = cubicMid(p0, c1, c2, p3)
      endPt = p3
    } else if (a.row === b.row) {
      // 同じ列に積まれた 2 つ: 縦にまっすぐ
      kind = 'side'
      const downward = a.y < b.y
      const p0 = { x: a.x + a.w / 2, y: downward ? a.y + a.h : a.y }
      const p3 = { x: b.x + b.w / 2, y: downward ? b.y : b.y + b.h }
      path = `M${f(p0.x)},${f(p0.y)} L${f(p3.x)},${f(p3.y)}`
      mid = { x: (p0.x + p3.x) / 2, y: (p0.y + p3.y) / 2 }
      endPt = p3
    } else {
      // 段をまたぐ: 上の段の下辺から下の段の上辺へ
      const aboveIsFrom = a.row < b.row
      const upper = aboveIsFrom ? a : b
      const lower = aboveIsFrom ? b : a
      const uy = upper.y + upper.h
      const c = (lower.y - uy) / 2
      const p0 = aboveIsFrom ? { x: upper.x + upper.w / 2, y: uy } : { x: lower.x + lower.w / 2, y: lower.y }
      const p3 = aboveIsFrom ? { x: lower.x + lower.w / 2, y: lower.y } : { x: upper.x + upper.w / 2, y: uy }
      const c1 = { x: p0.x, y: p0.y + (aboveIsFrom ? c : -c) }
      const c2 = { x: p3.x, y: p3.y + (aboveIsFrom ? -c : c) }
      path = `M${f(p0.x)},${f(p0.y)} C${f(c1.x)},${f(c1.y)} ${f(c2.x)},${f(c2.y)} ${f(p3.x)},${f(p3.y)}`
      mid = cubicMid(p0, c1, c2, p3)
      endPt = p3
    }
    edges.push({ index: r.index, from: r.from, to: r.to, rel: r.rel, kind, path, mid, end: endPt, both, problem })
  }

  return { width, height, nodes: placedNodes, byKey, edges, rows: rowCols.map((cols) => cols.flat()) }
}
