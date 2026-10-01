// 「共通のことば」の育つ地図の**描画**（shared-vocab-graph.md）。
//
// データは `composeVocabGraph`（vocabGraph.ts）が組む。ここは並べて描くだけ:
//   ・データセット = 点線枠のクラスタ（中の段組みと線の通り道は ⑤ と同じ `arrange()`）
//   ・種類の箱 = ⑤ と同じ `ShapeBox`（同じ設計はどの画面でも同じ見た目）
//   ・標準のことば = 画面下の琥珀の帯。ここに線が集まるのがこの図の主役
// `ShapeGraph` 本体は触らない — ④⑤の共有部品に横断図の概念を混ぜない（ADR §3）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
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
import { CloseIcon, ExpandIcon } from './icons'
import { ShapeBox, ShapeEdgeLine, type ShapeNodeData } from './kantan/ShapeGraph'
import { arrange, nodeHeight, type Lane, type Route } from './shapeGraph'
import type { VocabEdge, VocabEdgeKind, VocabNode, VocabShape } from './vocabGraph'

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
}

type ClusterData = { label: string; width: number; height: number }
type BandData = { label: string; hint: string; width: number; height: number }
type StdData = { label: string; vocab: string; width: number; height: number }

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
    <div className="vocab-map-std" style={{ width: d.width, height: d.height }}>
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
  /** 線の名前の位置（`ShapeEdgeLine` の `labelAt`）。帯へ降りる線だけが持つ。 */
  const labelAts: (number | undefined)[] = shape.edges.map(() => undefined)
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

  const stds = shape.nodes.filter((n) => !n.cluster)
  const stdPerRow = Math.max(1, Math.floor((ROW_MAX_W - BAND_PAD_SIDE * 2 + STD_GAP) / (STD_W + STD_GAP)))
  const stdRows: VocabNode[][] = []
  for (let i = 0; i < stds.length; i += stdPerRow) stdRows.push(stds.slice(i, i + stdPerRow))
  const stdRowW = (n: number) => n * STD_W + (n - 1) * STD_GAP
  const bandInnerW = Math.max(0, ...stdRows.map((r) => stdRowW(r.length)))
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

  if (!stds.length) return { nodes, routes, labelAts, width: canvasW, height: top }

  const bandTop = top + BAND_GAP
  const bandH = BAND_PAD_TOP + stdRows.length * (STD_H + STD_GAP) - STD_GAP + BAND_PAD_BOT
  withBand(nodes, stdRows, canvasW, bandTop, bandH)
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
    const inBand = bandLanes(stdRows, canvasW, bandTop, e.to)
    route.via.push(...inBand)
    routes[i] = route
    // 名前は、枠の下（最後の行の下端）から帯までの曲がりのまん中。途中の席は細いので、
    // そこに置くと名前が箱に重なる。位置は縦の長さで測る（`pointOnEdge`）。
    const toY = to.y - HANDLE_R
    const enter = inBand[0]?.top ?? toY
    if (toY > fromY) labelAts[i] = ((lastY + enter) / 2 - fromY) / (toY - fromY)
  })
  return { nodes, routes, labelAts, width: canvasW, height: bandTop + bandH }
}

/** 帯の行の、標準のことばの箱の左端（`withBand` と同じ並べかた）。 */
function stdRowXs(r: VocabNode[], canvasW: number): number[] {
  const w = r.length * STD_W + (r.length - 1) * STD_GAP
  const left = (canvasW - w) / 2
  return r.map((_, k) => left + k * (STD_W + STD_GAP))
}

/** 帯の中で、語 `id` へ向かう線が通る席（上の行から）。語が 1 行目なら無い。
 *  席は、上の行の箱と箱のすき間（両端の外も含む）のうち、行き先のまっすぐ上に
 *  いちばん近いところ（同じ近さなら右）。 */
function bandLanes(stdRows: VocabNode[][], canvasW: number, bandTop: number, id: string): Lane[] {
  const k = stdRows.findIndex((r) => r.some((n) => n.id === id))
  if (k <= 0) return []
  const target = stdRowXs(stdRows[k], canvasW)[stdRows[k].findIndex((n) => n.id === id)] + STD_W / 2
  const lanes: Lane[] = []
  for (let j = 0; j < k; j++) {
    const xs = stdRowXs(stdRows[j], canvasW)
    const gaps = [xs[0] - STD_GAP / 2, ...xs.map((x) => x + STD_W + STD_GAP / 2)]
    let x = gaps[gaps.length - 1]
    for (let g = gaps.length - 1; g >= 0; g--) {
      if (Math.abs(gaps[g] - target) < Math.abs(x - target) - 1e-6) x = gaps[g]
    }
    const top = bandTop + BAND_PAD_TOP + j * (STD_H + STD_GAP)
    lanes.push({ x, top, bottom: top + STD_H })
  }
  return lanes
}

function withBand(
  nodes: Node[],
  stdRows: VocabNode[][],
  canvasW: number,
  bandTop: number,
  bandH: number,
): Node[] {
  // 帯そのもの（ラベルは呼び出し側が i18n で流し込む — placeholder を後で差し替え）。
  nodes.push({
    id: 'band:standard',
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
        data: { label: n.label, vocab: n.vocab ?? '', width: STD_W, height: STD_H },
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
  maxHeight = 620,
  expandable = true,
  zoomable = false,
}: {
  shape: VocabShape
  ariaLabel: string
  /** 種類の箱を押したときの行き先（データセット詳細）。 */
  onOpenDataset?: (datasetId: string) => void
  maxHeight?: number
  expandable?: boolean
  zoomable?: boolean
}) {
  const { t } = useTranslation()
  const { nodes: rawNodes, routes, labelAts, width: contentW, height: contentH } = useMemo(
    () => place(shape),
    [shape],
  )
  const nodes = useMemo(
    () =>
      rawNodes.map((n) =>
        n.id === 'band:standard'
          ? {
              ...n,
              data: {
                ...n.data,
                label: t('vocab:map.bandTitle'),
                hint: t('vocab:map.bandHint'),
              },
            }
          : n,
      ),
    [rawNodes, t],
  )
  const edges: Edge[] = useMemo(() => {
    const said = new Set<string>()
    return shape.edges.map((e, i) => {
      const dup = !e.label || said.has(e.label)
      if (e.label) said.add(e.label)
      return {
        id: `${e.from}->${e.to}-${i}`,
        source: e.from,
        target: e.to,
        // 通り道のある線（枠の中の線・帯へ降りる線）は、席と真下の通り道を描ける
        // ShapeGraph の線で引く。ほかの線（帯から出る対応など）は React Flow の既定の線。
        ...(routes[i] ? { type: 'shape', data: { route: routes[i], labelAt: labelAts[i] } } : {}),
        label: dup ? undefined : e.label,
        className: `vocab-map-edge vocab-map-edge--${e.kind}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: EDGE_COLOR[e.kind] },
        markerStart: e.both
          ? { type: MarkerType.ArrowClosed, width: 15, height: 15, color: EDGE_COLOR[e.kind] }
          : undefined,
      }
    })
  }, [shape, routes, labelAts])

  const handleClick = useCallback(
    (_: unknown, node: Node) => {
      if (!onOpenDataset || node.type !== 'shape') return
      const dsId = String(node.id).split('::')[0]
      if (dsId) onOpenDataset(dsId)
    },
    [onOpenDataset],
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
  useEffect(() => {
    if (!big) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setBig(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [big])

  return (
    <div className="shape-graph vocab-map" style={{ height }} role="img" aria-label={ariaLabel}>
      {expandable && (
        <button
          type="button"
          className="shape-graph-expand"
          onClick={() => setBig(true)}
          aria-label={t('skeletongate:diagram.expand')}
          title={t('skeletongate:diagram.expand')}
        >
          <ExpandIcon size={15} />
        </button>
      )}
      {big &&
        createPortal(
          <div
            className="shape-overlay"
            role="dialog"
            aria-modal="true"
            aria-label={ariaLabel}
            onClick={() => setBig(false)}
          >
            <div className="shape-overlay-panel" onClick={(e) => e.stopPropagation()}>
              <div className="shape-overlay-head">
                <span>{ariaLabel}</span>
                <button
                  type="button"
                  className="shape-overlay-close"
                  onClick={() => setBig(false)}
                  aria-label={t('skeletongate:diagram.close')}
                >
                  <CloseIcon size={18} />
                </button>
              </div>
              <VocabMap
                shape={shape}
                ariaLabel={ariaLabel}
                onOpenDataset={onOpenDataset}
                maxHeight={Math.max(360, Math.round(window.innerHeight * 0.8))}
                expandable={false}
                zoomable
              />
            </div>
          </div>,
          document.body,
        )}
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
        onNodeClick={onOpenDataset ? handleClick : undefined}
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
