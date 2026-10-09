// 「ことば」の育つ地図の**描画**（shared-vocab-graph.md）。
//
// データは `composeVocabGraph`（vocabGraph.ts）が組む。ここは並べて描くだけ:
//   ・データセット = 点線枠のクラスタ（中の段組みと線の通り道は ⑤ と同じ `arrange()`）
//   ・種類の箱 = ⑤ と同じ `ShapeBox`（同じ設計はどの画面でも同じ見た目）
//   ・標準のことば = 画面下の琥珀の帯。ここに線が集まるのがこの図の主役
//   ・共有のことば = 標準の帯の上の帯（`sv:`・青）。データセットが 0 件でも描く
// `ShapeGraph` 本体は触らない — ④⑤の共有部品に横断図の概念を混ぜない（ADR §3）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { BigViewOverlay, ExpandButton } from './BigView'
import { useBigViewHeight } from './bigViewSize'
import { ShapeBox, ShapeEdgeLine, type ShapeNodeData } from './kantan/ShapeGraph'
import {
  arrange,
  edgeLabels,
  nodeHeight,
  type EdgeLabel,
  type Lane,
  type Rect,
  type Route,
} from './shapeGraph'
import './vocabMapShared.css'
import type { PickEnd } from './lineChoice'
import { pickEndOfVocabNode, type VocabEdge, type VocabEdgeKind, type VocabNode, type VocabShape } from './vocabGraph'

/** 線の起点に丸を選ぶ操作（「ことば」画面の地図）。有効のあいだ、押すと選択（押した先の画面遷移はしない）。 */
export interface VocabPick {
  active: boolean
  /** 選んでいる節の id（最大 2 つ）。 */
  picked: string[]
  onPick: (end: PickEnd) => void
}

const KIND_W = 232
const STD_W = 224
const STD_H = 54
const STD_GAP = 22
const CL_PAD_TOP = 52
const CL_PAD_SIDE = 26
const CL_PAD_BOT = 24
const CL_GAP = 44
const ROW_MAX_W = 1180
const BAND_PAD_TOP = 56
const BAND_PAD_SIDE = 26
const BAND_PAD_BOT = 24
const BAND_GAP = 88
/** 帯へ降りる線の名前の置き場（枠の下から帯までの曲がりの中の、上からの割合。
 *  まん中が先）。 */
const BAND_LABEL_AT = [0.5, 0.3, 0.7, 0.15, 0.85]
/** 下の行の、枠と枠のすき間に取る席の幅（線を通すだけ — 名前は置かない）。 */
const CL_SEAT_W = 24
/** 線の端は、箱の辺から出入り口（handle）の半分だけ外にある（shapeGraph と同じ）。 */
const HANDLE_R = 3

/** 辺の色。矢じりは同じ設定の辺どうしで共有されるので、色はここから inline で渡す
 *  （ShapeGraph と同じ制約）。 */
const EDGE_COLOR: Record<VocabEdgeKind, string> = {
  link: 'var(--border-strong)',
  used: 'var(--primary)',
  candidate: 'var(--accent)',
  alignment: 'var(--activity)',
  upper: 'var(--link)',
}

type ClusterData = { label: string; width: number; height: number }
type BandData = { label: string; hint: string; width: number; height: number }
type StdData = {
  label: string
  vocab: string
  width: number
  height: number
  /** 共有のことばの箱（標準の箱と見分ける色と札）。 */
  shared?: { orphan: boolean; kind: 'class' | 'property' }
}

function ClusterFrame({ data }: NodeProps) {
  const d = data as ClusterData
  return (
    <div className="vocab-map-cluster" style={{ width: d.width, height: d.height }}>
      <span className="vocab-map-cluster-name">{d.label}</span>
    </div>
  )
}

function BandFrame({ data }: NodeProps) {
  const d = data as BandData
  return (
    <div className="vocab-map-cluster vocab-map-band" style={{ width: d.width, height: d.height }}>
      <span className="vocab-map-cluster-name">{d.label}</span>
      <span className="vocab-map-band-hint">{d.hint}</span>
    </div>
  )
}

/** 標準のことばの箱。種類の箱と見間違えないよう 2 行（語＋語彙名）の別部品。
 *  箱の高さは決まっているので、どちらの行も 1 行に収める（長い語彙名が折り返すと
 *  語を隠す）。切れた分は title で読める。 */
function StdBox({ data }: NodeProps) {
  const d = data as StdData
  return (
    <div
      className={`vocab-map-std${d.shared ? ' vocab-map-std--shared' : ''}${d.shared?.orphan ? ' is-orphan' : ''}`}
      style={{ width: d.width, height: d.height }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <span className="vocab-map-std-term" title={d.label}>
        {d.label}
      </span>
      <span className="vocab-map-std-vocab" title={d.vocab}>
        {d.vocab}
      </span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  )
}

// ⭐モジュールの上の階層に置く（ShapeGraph と同じ理由 — 中に置くと毎回作り直され、
// React Flow が節と線を採り直す）。
const NODE_TYPES = { shape: ShapeBox, cluster: ClusterFrame, band: BandFrame, std: StdBox }
const EDGE_TYPES = { shape: ShapeEdgeLine }

/** 決め打ちの段組み: クラスタごとに ⑤ と同じ `arrange()` で中を並べ（段をまたぐ線の
 *  席もそこで取る）、クラスタを行に詰めて折り返し、標準のことばの帯を下に敷く。
 *  すべて入力順で決定論。
 *
 *  枠の幅は、席を含む幅（`arrange` の `width`）から取る — 席は箱ではないので、箱の
 *  座標からは分からない。枠がその幅を持つので、図を枠に合わせるとき（`fitView`）にも
 *  席が入る（ShapeGraph の場所取りの箱は要らない）。線の通り道（席・まっすぐ降りる
 *  下端）は枠の中の座標で返ってくるので、箱と同じだけずらす（`VocabMap.test.ts`）。
 *
 *  ⭐**帯へ降りる線も、箱と枠の裏を通らない。** 枠の中では、下の段に席を取って
 *  枠の下へ抜ける（`arrange` の `exits`）。同じ箱から出る線は、枠の下までは 1 本の
 *  道を重ねて通る（席を線の数だけ取ると枠が広がる）。枠の下からは、その行でいちばん
 *  高い枠の下端までまっすぐ降り（隣の高い枠の裏を通らない）、下の行では枠と枠の
 *  すき間に取った席を通る。下の行の席は枠 1 つに 1 つ — 同じ枠の線は、帯の手前まで
 *  1 本の道を重ねて通り、帯の手前で分かれる（実機 2026-10-01: 見本の地図で、上の段の
 *  種類から帯へ降りる線が、下の段の箱と、下の行の枠の中の箱の裏を通った）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（PageChatDrawer と同じ理由）
export function place(shape: VocabShape) {
  const nodes: Node[] = []
  /** 線の通り道。`shape.edges` と同じ並びで、枠の中の線と帯へ降りる線だけが持つ。 */
  const routes: (Route | undefined)[] = shape.edges.map(() => undefined)
  /** 帯へ降りる線の、名前の置き場の候補（`ShapeEdgeLine` の `labelAt`）。 */
  const bandSpots: (number[] | undefined)[] = shape.edges.map(() => undefined)
  const heightOf = (n: VocabNode) => nodeHeight(n)
  const byId = new Map(shape.nodes.map((n) => [n.id, n]))
  /** 帯へ降りる線 = 枠の中の箱から、帯の標準のことばへ向かう線。 */
  const intoBand = (e: VocabEdge) =>
    !!byId.get(e.from)?.cluster && byId.has(e.to) && !byId.get(e.to)!.cluster
  type Placed = {
    id: string
    w: number
    h: number
    inner: Map<string, { x: number; y: number }>
    /** 枠の中の線（`shape.edges` の添字）と、その通り道（枠の中の座標）。 */
    lines: { edge: number; route: Route }[]
    /** 帯へ降りる線を出す箱と、枠の下へ抜ける道（枠の中の座標）。 */
    exits: Map<string, Route>
  }
  const placed: Placed[] = []
  for (const c of shape.clusters) {
    const inner = shape.nodes.filter((n) => n.cluster === c.id)
    if (inner.length === 0) continue
    const innerIds = new Set(inner.map((n) => n.id))
    // 枠の中の線 = データの中の線で、両端がこの枠の箱のもの。
    const edgeIdx: number[] = []
    const exits: string[] = []
    shape.edges.forEach((e, i) => {
      if (e.kind === 'link' && innerIds.has(e.from) && innerIds.has(e.to)) edgeIdx.push(i)
      else if (innerIds.has(e.from) && intoBand(e) && !exits.includes(e.from)) exits.push(e.from)
    })
    const a = arrange(
      { nodes: inner, edges: edgeIdx.map((i) => shape.edges[i]) },
      { perRow: 3, nodeWidth: KIND_W, heightOf, exits },
    )
    placed.push({
      id: c.id,
      w: a.width + CL_PAD_SIDE * 2,
      h: a.height + CL_PAD_TOP + CL_PAD_BOT,
      inner: a.pos,
      lines: edgeIdx.map((edge, j) => ({ edge, route: a.routes[j] })),
      exits: a.exits,
    })
  }

  // クラスタを行に詰める（広すぎたら折り返す）。席を取る前の幅で決める — 席の
  // 幅で折り返しが変わると、席の要る行が変わる。
  const rows: Placed[][] = []
  let row: Placed[] = []
  let roww = 0
  for (const p of placed) {
    const add = (row.length ? CL_GAP : 0) + p.w
    if (row.length && roww + add > ROW_MAX_W) {
      rows.push(row)
      row = []
      roww = 0
    }
    row.push(p)
    roww += (row.length > 1 ? CL_GAP : 0) + p.w
  }
  if (row.length) rows.push(row)

  // 下の行の席: 帯へ降りる線を持つ枠ごとに、その下の行のそれぞれで 1 つ。席を入れる
  // のは、枠のまっすぐ下にいちばん近いすき間（同じ近さなら右 — `arrange` と同じ）。
  // 位置は席を取る前の、行のまん中を 0 とした座標で決める。
  interface Seat {
    cluster: string
    slot: number
    ideal: number
  }
  const rowW = (r: Placed[]) => r.reduce((s, p) => s + p.w, 0) + (r.length - 1) * CL_GAP
  const centerOf = new Map<string, number>()
  for (const r of rows) {
    let x = -rowW(r) / 2
    for (const p of r) {
      centerOf.set(p.id, x + p.w / 2)
      x += p.w + CL_GAP
    }
  }
  const seats: Seat[][] = rows.map(() => [])
  rows.forEach((r, ri) => {
    for (const p of r) {
      if (p.exits.size === 0) continue
      const ideal = centerOf.get(p.id)!
      for (let below = ri + 1; below < rows.length; below++) {
        const lower = rows[below]
        // すき間 k の位置: 0 = 左端の枠の左、k = 枠 k-1 と枠 k の間、n = 右端の枠の右。
        const gapAt = (k: number): number => {
          let x = -rowW(lower) / 2
          for (let i = 0; i < k; i++) x += lower[i].w + CL_GAP
          return x - CL_GAP / 2
        }
        let slot = lower.length
        let best = Infinity
        for (let k = lower.length; k >= 0; k--) {
          const d = Math.abs(gapAt(k) - ideal)
          if (d < best - 1e-6) {
            best = d
            slot = k
          }
        }
        seats[below].push({ cluster: p.id, slot, ideal })
      }
    }
  })
  // 行の並び: すき間 0 の席 → 枠 0 → すき間 1 の席 → 枠 1 → …（同じすき間では、
  // 枠のまっすぐ下が左のものから — 線どうしが交わりにくい。同じなら入力順）。
  type Item = { w: number; frame?: Placed; seat?: Seat }
  const itemsOf = (r: Placed[], ri: number): Item[] => {
    const order = [...seats[ri]].sort((p, q) => p.slot - q.slot || p.ideal - q.ideal)
    const items: Item[] = []
    for (let k = 0; k <= r.length; k++) {
      for (const s of order) if (s.slot === k) items.push({ w: CL_SEAT_W, seat: s })
      if (k < r.length) items.push({ w: r[k].w, frame: r[k] })
    }
    return items
  }
  const rowItems = rows.map(itemsOf)
  const itemsW = (items: Item[]) =>
    items.reduce((s, it) => s + it.w, 0) + (items.length - 1) * CL_GAP

  // 帯は上から 共有のことば → 標準のことば（どちらも枠の下）。共有のことばは 0 件でも帯を作る。
  const sharedBandNodes = shape.nodes.filter((n) => !n.cluster && n.shared)
  const stds = shape.nodes.filter((n) => !n.cluster && !n.shared)
  const stdPerRow = Math.max(1, Math.floor((ROW_MAX_W - BAND_PAD_SIDE * 2 + STD_GAP) / (STD_W + STD_GAP)))
  const chunk = (list: VocabNode[]) => {
    const out: VocabNode[][] = []
    for (let i = 0; i < list.length; i += stdPerRow) out.push(list.slice(i, i + stdPerRow))
    return out
  }
  const sharedRows = chunk(sharedBandNodes)
  const stdRows = chunk(stds)
  const stdRowW = (n: number) => n * STD_W + (n - 1) * STD_GAP
  const bandInnerW = Math.max(0, ...[...sharedRows, ...stdRows].map((r) => stdRowW(r.length)))
  const canvasW = Math.max(...rowItems.map(itemsW), bandInnerW + BAND_PAD_SIDE * 2, 1)

  /** 帯へ降りる道: 枠 → 箱 → 枠の下へ抜ける道（図の座標）。 */
  const exitRoute = new Map<string, Route>()
  /** 行の下端（その行でいちばん高い枠の下端）。 */
  const rowBottom = new Map<string, number>()
  /** 下の行の席（枠 → 上の行から順に）。 */
  const laneOf = new Map<string, Lane[]>()
  let top = 0
  rows.forEach((r, ri) => {
    const items = rowItems[ri]
    let x = (canvasW - itemsW(items)) / 2
    const tallest = Math.max(...r.map((p) => p.h))
    for (const it of items) {
      if (it.seat) {
        const lanes = laneOf.get(it.seat.cluster) ?? []
        lanes.push({ x: x + it.w / 2, top, bottom: top + tallest })
        laneOf.set(it.seat.cluster, lanes)
      }
      const p = it.frame
      if (p) {
        // 枠の中の座標 → 図の座標。箱も、線の通り道も、同じだけずらす。
        const ox = x + CL_PAD_SIDE
        const oy = top + CL_PAD_TOP
        const shift = (route: Route): Route => {
          const shifted: Route = {
            via: route.via.map((l) => ({ x: l.x + ox, top: l.top + oy, bottom: l.bottom + oy })),
          }
          if (route.drop !== undefined) shifted.drop = route.drop + oy
          return shifted
        }
        nodes.push({
          id: `cluster:${p.id}`,
          type: 'cluster',
          position: { x, y: top },
          data: {
            label: shape.clusters.find((c) => c.id === p.id)?.label ?? p.id,
            width: p.w,
            height: p.h,
          },
          draggable: false,
          selectable: false,
          connectable: false,
          zIndex: 0,
        })
        for (const [id, ip] of p.inner) {
          const n = byId.get(id)!
          nodes.push({
            id,
            type: 'shape',
            position: { x: ox + ip.x, y: oy + ip.y },
            data: {
              label: n.label,
              tone: n.tone,
              width: KIND_W,
              height: heightOf(n),
              fields: n.fields ?? [],
              foldable: false,
              folded: false,
              words: { open: '', close: '' },
              clickable: true,
            } satisfies ShapeNodeData,
            draggable: false,
            selectable: false,
            connectable: false,
            zIndex: 1,
          })
        }
        for (const { edge, route } of p.lines) routes[edge] = shift(route)
        for (const [id, route] of p.exits) {
          exitRoute.set(id, shift(route))
          rowBottom.set(id, top + tallest)
        }
      }
      x += it.w + CL_GAP
    }
    top += tallest + CL_GAP
  })
  if (rows.length) top -= CL_GAP

  if (!stds.length && !sharedBandNodes.length) {
    return { nodes, routes, labels: labelsOf(shape, nodes, routes, bandSpots), width: canvasW, height: top }
  }

  // 帯を上から積む。枠が 0 個（データセット 0 件）なら先頭から。
  const bandHeight = (n: number) => BAND_PAD_TOP + n * (STD_H + STD_GAP) - STD_GAP + BAND_PAD_BOT
  let cursor = rows.length ? top + BAND_GAP : 0
  /** 帯の行ぜんぶ（上から）。帯へ降りる線が上の行の席を通るのに使う。 */
  const bandRows: BandRow[] = []
  const addBand = (id: 'band:shared' | 'band:standard', list: VocabNode[][]) => {
    if (!list.length) return
    const h = bandHeight(list.length)
    withBand(nodes, id, list, canvasW, cursor, h)
    list.forEach((r, j) => bandRows.push({ nodes: r, top: cursor + BAND_PAD_TOP + j * (STD_H + STD_GAP) }))
    cursor += h + BAND_GAP
  }
  addBand('band:shared', sharedRows)
  addBand('band:standard', stdRows)
  const bandBottom = cursor - BAND_GAP
  const at = new Map(nodes.map((n) => [n.id, n.position]))
  shape.edges.forEach((e, i) => {
    const own = exitRoute.get(e.from)
    const from = at.get(e.from)
    const to = at.get(e.to)
    if (!intoBand(e) || !own || !from || !to) return
    const cluster = byId.get(e.from)!.cluster!
    const fromY = from.y + heightOf(byId.get(e.from)!) + HANDLE_R
    // 枠の下へ抜けたところから、行の下端までまっすぐ降りる。
    const bottom = rowBottom.get(e.from)!
    // 出どころの段の下端までまっすぐ降りる（`arrange` の drop）。席へ曲がり始めるのは
    // そこから — 箱の下辺から曲がると、同じ段の隣の高い箱の裏を通る（K54 の 9）。
    const route: Route = { via: [...own.via] }
    if (own.drop !== undefined) route.drop = own.drop
    const last = own.via[own.via.length - 1]
    if (last) route.via.push({ x: last.x, top: last.bottom, bottom })
    else route.drop = bottom
    route.via.push(...(laneOf.get(cluster) ?? []))
    const lastY = route.via.length ? route.via[route.via.length - 1].bottom : bottom
    // 帯の 2 行目より下の語へは、上の行の箱と箱のすき間を通る（箱の裏を通らない）。
    const inBand = bandLanes(bandRows, canvasW, e.to)
    route.via.push(...inBand)
    routes[i] = route
    // 名前の置き場は、枠の下（最後の行の下端）から帯までの曲がりの中だけ。途中の席は
    // 細いので、そこに置くと名前が箱に重なる。位置は縦の長さで測る（`pointOnEdge`）。
    const toY = to.y - HANDLE_R
    const enter = inBand[0]?.top ?? toY
    if (toY > fromY) {
      bandSpots[i] = BAND_LABEL_AT.map((f) => (lastY + f * (enter - lastY) - fromY) / (toY - fromY))
    }
  })
  return {
    nodes,
    routes,
    labels: labelsOf(shape, nodes, routes, bandSpots),
    width: canvasW,
    height: bandBottom,
  }
}

/** 線の名前と位置を、図全体で ⑤ と同じ決め方（`edgeLabels`）で決める — 名前どうし・
 *  名前と箱・名前と枠や帯の見出しが重ならず、ほかの線をなるべく覆わない位置。
 *  同じ文言の 2 本目は名前を出さない（⑤ と同じ）。
 *
 *  実機 2026-10-01: 見本の world で「国 → 取り込みの記録」の名前「取り込み」が線の
 *  まん中に固定され、同じ行き先へ席から入ってくる「年ごとの記録 → 取り込みの記録」の
 *  線と、帯へ降りる線に横切られて読めなかった。帯へ降りる線の名前も、同じ高さに
 *  並んで重なった。 */
function labelsOf(
  shape: VocabShape,
  nodes: Node[],
  routes: (Route | undefined)[],
  bandSpots: (number[] | undefined)[],
): (EdgeLabel | undefined)[] {
  const pos = new Map<string, { x: number; y: number }>()
  const size = new Map<string, { width: number; height: number }>()
  const obstacles: Rect[] = []
  for (const n of nodes) {
    const d = n.data as { width: number; height: number }
    if (n.type === 'cluster' || n.type === 'band') {
      // 枠と帯の見出しの帯（名前が見出しの字に重ならない）。
      const padTop = n.type === 'band' ? BAND_PAD_TOP : CL_PAD_TOP
      obstacles.push({ x: n.position.x, y: n.position.y, w: d.width, h: padTop - 8 })
      continue
    }
    pos.set(n.id, n.position)
    size.set(n.id, { width: d.width, height: d.height })
  }
  return edgeLabels(shape, pos, {
    widthOf: (n) => size.get(n.id)?.width ?? KIND_W,
    heightOf: (n) => size.get(n.id)?.height ?? nodeHeight(n),
    routes: routes.map((r) => r ?? { via: [] }),
    obstacles,
    spotsOf: (i) => bandSpots[i],
  })
}

/** 帯の行の、標準のことばの箱の左端（`withBand` と同じ並べかた）。 */
function stdRowXs(r: VocabNode[], canvasW: number): number[] {
  const w = r.length * STD_W + (r.length - 1) * STD_GAP
  const left = (canvasW - w) / 2
  return r.map((_, k) => left + k * (STD_W + STD_GAP))
}

/** 帯の 1 行（箱の並びと、その行の上端）。共有のことばの帯と標準の帯の行を通して持つ。 */
interface BandRow {
  nodes: VocabNode[]
  top: number
}

/** 帯の中で、語 `id` へ向かう線が通る席（上の行から）。語が最初の行なら無い。
 *  席は、上の行の箱と箱のすき間（両端の外も含む）のうち、行き先のまっすぐ上に
 *  いちばん近いところ（同じ近さなら右）。共有のことばの帯の行も「上の行」に数える。 */
function bandLanes(bandRows: BandRow[], canvasW: number, id: string): Lane[] {
  const k = bandRows.findIndex((r) => r.nodes.some((n) => n.id === id))
  if (k <= 0) return []
  const target = stdRowXs(bandRows[k].nodes, canvasW)[bandRows[k].nodes.findIndex((n) => n.id === id)] + STD_W / 2
  const lanes: Lane[] = []
  for (let j = 0; j < k; j++) {
    const xs = stdRowXs(bandRows[j].nodes, canvasW)
    const gaps = [xs[0] - STD_GAP / 2, ...xs.map((x) => x + STD_W + STD_GAP / 2)]
    let x = gaps[gaps.length - 1]
    for (let g = gaps.length - 1; g >= 0; g--) {
      if (Math.abs(gaps[g] - target) < Math.abs(x - target) - 1e-6) x = gaps[g]
    }
    const top = bandRows[j].top
    lanes.push({ x, top, bottom: top + STD_H })
  }
  return lanes
}

function withBand(
  nodes: Node[],
  bandId: 'band:shared' | 'band:standard',
  stdRows: VocabNode[][],
  canvasW: number,
  bandTop: number,
  bandH: number,
): Node[] {
  // 帯そのもの（ラベルは呼び出し側が i18n で流し込む — placeholder を後で差し替え）。
  nodes.push({
    id: bandId,
    type: 'band',
    position: { x: 0, y: bandTop },
    data: { label: '', hint: '', width: canvasW, height: bandH },
    draggable: false,
    selectable: false,
    connectable: false,
    zIndex: 0,
  })
  let y = bandTop + BAND_PAD_TOP
  for (const r of stdRows) {
    const xs = stdRowXs(r, canvasW)
    r.forEach((n, k) => {
      const x = xs[k]
      nodes.push({
        id: n.id,
        type: 'std',
        position: { x, y },
        data: {
          label: n.label,
          vocab: n.vocab ?? '',
          width: STD_W,
          height: STD_H,
          ...(n.shared ? { shared: { orphan: n.shared.orphan, kind: n.shared.kind } } : {}),
        } satisfies StdData,
        draggable: false,
        selectable: false,
        connectable: false,
        zIndex: 1,
      })
    })
    y += STD_H + STD_GAP
  }
  return nodes
}

function VocabMapInner({
  shape,
  ariaLabel,
  onOpenDataset,
  pick,
  maxHeight = 620,
  expandable = true,
  zoomable = false,
}: {
  shape: VocabShape
  ariaLabel: string
  /** 種類の箱を押したときの行き先（データセット詳細）。 */
  onOpenDataset?: (datasetId: string) => void
  /** 線の起点に丸を選ぶ操作。有効のあいだ、押すと選択になり画面遷移はしない。 */
  pick?: VocabPick
  maxHeight?: number
  expandable?: boolean
  zoomable?: boolean
}) {
  const { t } = useTranslation()
  const { nodes: rawNodes, routes, labels, width: contentW, height: contentH } = useMemo(
    () => place(shape),
    [shape],
  )
  const byId = useMemo(() => new Map(shape.nodes.map((n) => [n.id, n])), [shape])
  const pickedKey = (pick?.picked ?? []).join('|')
  const nodes = useMemo(() => {
    const picked = new Set(pickedKey ? pickedKey.split('|') : [])
    return rawNodes.map((n) => {
      if (n.id === 'band:standard')
        return { ...n, data: { ...n.data, label: t('vocab:map.bandTitle'), hint: t('vocab:map.bandHint') } }
      if (n.id === 'band:shared')
        return {
          ...n,
          data: { ...n.data, label: t('vocabmap:sharedBand.title'), hint: t('vocabmap:sharedBand.hint') },
        }
      // 選んだ丸には className を付ける（見た目は CSS）。
      return picked.has(n.id) ? { ...n, className: 'is-picked' } : n
    })
  }, [rawNodes, t, pickedKey])
  const edges: Edge[] = useMemo(() => {
    return shape.edges.map((e, i) => {
      return {
        id: `${e.from}->${e.to}-${i}`,
        source: e.from,
        target: e.to,
        // どの線も ShapeGraph の線で引く（席と真下の通り道、名前の位置を描ける）。
        // 通り道の無い線（帯から出る対応など）は、React Flow の既定の線と同じ形。
        type: 'shape',
        data: { route: routes[i], labelAt: labels[i]?.at },
        label: labels[i]?.text,
        className: `vocab-map-edge vocab-map-edge--${e.kind}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: EDGE_COLOR[e.kind] },
        markerStart: e.both
          ? { type: MarkerType.ArrowClosed, width: 15, height: 15, color: EDGE_COLOR[e.kind] }
          : undefined,
      }
    })
  }, [shape, routes, labels])

  const handleClick = useCallback(
    (_: unknown, node: Node) => {
      // 線を引く選択のあいだは、押すと丸を選ぶだけ（枠・帯は選べない）。
      if (pick?.active) {
        const end = pickEndOfVocabNode(byId.get(node.id))
        if (end) pick.onPick(end)
        return
      }
      if (!onOpenDataset || node.type !== 'shape') return
      const dsId = String(node.id).split('::')[0]
      if (dsId) onOpenDataset(dsId)
    },
    [onOpenDataset, pick, byId],
  )

  const height = Math.min(maxHeight, Math.max(240, contentH + 48))

  // 形が変わったら測り直して合わせ直す（ShapeGraph と同じ理由 — 辺は handle の
  // 実測が取れるまで描かれない）。
  const fitKey = useMemo(
    () =>
      shape.nodes.map((n) => n.id).join('|') +
      '#' +
      shape.edges.map((e) => `${e.from}>${e.to}`).join('|') +
      '#' +
      // 席の幅は線の名前で決まる。名前だけが変わっても、図の幅が変わる。
      contentW,
    [shape, contentW],
  )
  const rf = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  const idsRef = useRef<string[]>([])
  useEffect(() => {
    idsRef.current = nodes.map((n) => n.id)
  })
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      updateNodeInternals(idsRef.current)
      rf.fitView({ padding: 0.06, maxZoom: 1 })
    })
    return () => cancelAnimationFrame(raf)
  }, [fitKey, rf, updateNodeInternals])

  const [big, setBig] = useState(false)
  const bigH = useBigViewHeight(big)

  return (
    <div className="shape-graph vocab-map" style={{ height }} role="img" aria-label={ariaLabel}>
      {expandable && <ExpandButton onClick={() => setBig(true)} />}
      <BigViewOverlay open={big} onClose={() => setBig(false)} title={ariaLabel}>
        <VocabMap
          shape={shape}
          ariaLabel={ariaLabel}
          onOpenDataset={onOpenDataset}
          pick={pick}
          maxHeight={bigH}
          expandable={false}
          zoomable
        />
      </BigViewOverlay>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.06, maxZoom: 1 }}
        minZoom={0.08}
        maxZoom={2.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll={false}
        zoomOnScroll={zoomable}
        zoomOnDoubleClick={zoomable}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={onOpenDataset || pick?.active ? handleClick : undefined}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls
          showInteractive={false}
          position="bottom-left"
          aria-label={t('skeletongate:diagram.controls')}
        />
      </ReactFlow>
    </div>
  )
}

export function VocabMap(props: {
  shape: VocabShape
  ariaLabel: string
  onOpenDataset?: (datasetId: string) => void
  pick?: VocabPick
  maxHeight?: number
  expandable?: boolean
  zoomable?: boolean
}) {
  return (
    <ReactFlowProvider>
      <VocabMapInner {...props} />
    </ReactFlowProvider>
  )
}
