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
import { arrange, nodeHeight, type Route } from './shapeGraph'
import type { VocabEdgeKind, VocabNode, VocabShape } from './vocabGraph'

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
 *  下端）は枠の中の座標で返ってくるので、箱と同じだけずらす（`VocabMap.test.ts`）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（PageChatDrawer と同じ理由）
export function place(shape: VocabShape) {
  const nodes: Node[] = []
  /** 枠の中の線の通り道。`shape.edges` と同じ並びで、枠の中の線でなければ undefined。 */
  const routes: (Route | undefined)[] = shape.edges.map(() => undefined)
  const heightOf = (n: VocabNode) => nodeHeight(n)
  type Placed = {
    id: string
    w: number
    h: number
    inner: Map<string, { x: number; y: number }>
    /** 枠の中の線（`shape.edges` の添字）と、その通り道（枠の中の座標）。 */
    lines: { edge: number; route: Route }[]
  }
  const placed: Placed[] = []
  for (const c of shape.clusters) {
    const inner = shape.nodes.filter((n) => n.cluster === c.id)
    if (inner.length === 0) continue
    const innerIds = new Set(inner.map((n) => n.id))
    // 枠の中の線 = データの中の線で、両端がこの枠の箱のもの。
    const edgeIdx: number[] = []
    shape.edges.forEach((e, i) => {
      if (e.kind === 'link' && innerIds.has(e.from) && innerIds.has(e.to)) edgeIdx.push(i)
    })
    const a = arrange(
      { nodes: inner, edges: edgeIdx.map((i) => shape.edges[i]) },
      { perRow: 3, nodeWidth: KIND_W, heightOf },
    )
    placed.push({
      id: c.id,
      w: a.width + CL_PAD_SIDE * 2,
      h: a.height + CL_PAD_TOP + CL_PAD_BOT,
      inner: a.pos,
      lines: edgeIdx.map((edge, j) => ({ edge, route: a.routes[j] })),
    })
  }

  // クラスタを行に詰める（広すぎたら折り返す）。
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

  const stds = shape.nodes.filter((n) => !n.cluster)
  const stdPerRow = Math.max(1, Math.floor((ROW_MAX_W - BAND_PAD_SIDE * 2 + STD_GAP) / (STD_W + STD_GAP)))
  const stdRows: VocabNode[][] = []
  for (let i = 0; i < stds.length; i += stdPerRow) stdRows.push(stds.slice(i, i + stdPerRow))
  const stdRowW = (n: number) => n * STD_W + (n - 1) * STD_GAP
  const bandInnerW = Math.max(0, ...stdRows.map((r) => stdRowW(r.length)))
  const rowW = (r: Placed[]) => r.reduce((s, p) => s + p.w, 0) + (r.length - 1) * CL_GAP
  const canvasW = Math.max(...rows.map(rowW), bandInnerW + BAND_PAD_SIDE * 2, 1)

  const byId = new Map(shape.nodes.map((n) => [n.id, n]))
  let top = 0
  for (const r of rows) {
    let x = (canvasW - rowW(r)) / 2
    const tallest = Math.max(...r.map((p) => p.h))
    for (const p of r) {
      // 枠の中の座標 → 図の座標。箱も、線の通り道も、同じだけずらす。
      const ox = x + CL_PAD_SIDE
      const oy = top + CL_PAD_TOP
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
      for (const { edge, route } of p.lines) {
        const shifted: Route = {
          via: route.via.map((l) => ({ x: l.x + ox, top: l.top + oy, bottom: l.bottom + oy })),
        }
        if (route.drop !== undefined) shifted.drop = route.drop + oy
        routes[edge] = shifted
      }
      x += p.w + CL_GAP
    }
    top += tallest + CL_GAP
  }
  if (rows.length) top -= CL_GAP

  if (stds.length) {
    const bandTop = top + BAND_GAP
    const bandH = BAND_PAD_TOP + stdRows.length * (STD_H + STD_GAP) - STD_GAP + BAND_PAD_BOT
    return {
      nodes: withBand(nodes, stds, stdRows, canvasW, bandTop, bandH),
      routes,
      width: canvasW,
      height: bandTop + bandH,
    }
  }
  return { nodes, routes, width: canvasW, height: top }
}

function withBand(
  nodes: Node[],
  _stds: VocabNode[],
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
    const w = r.length * STD_W + (r.length - 1) * STD_GAP
    let x = (canvasW - w) / 2
    for (const n of r) {
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
      x += STD_W + STD_GAP
    }
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
  const { nodes: rawNodes, routes, width: contentW, height: contentH } = useMemo(
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
        // 枠の中の線（データの中）は、席と真下の通り道を描ける ShapeGraph の線で引く。
        // 帯へ降りる線（used / candidate / alignment）は React Flow の既定の線のまま —
        // 帯は枠の外にあり、段の考えが無い。
        ...(e.kind === 'link' ? { type: 'shape', data: { route: routes[i] } } : {}),
        label: dup ? undefined : e.label,
        className: `vocab-map-edge vocab-map-edge--${e.kind}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: EDGE_COLOR[e.kind] },
        markerStart: e.both
          ? { type: MarkerType.ArrowClosed, width: 15, height: 15, color: EDGE_COLOR[e.kind] }
          : undefined,
      }
    })
  }, [shape, routes])

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
