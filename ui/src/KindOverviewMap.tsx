// 「共通のことば」の地図の「全体」表示（案 B）の**描画**（shared-vocab-graph.md §6）。
//
// データと配置は `layoutKindOverview`（kindOverview.ts）。ここは React Flow で描くだけ。
// 罠の回避は `VocabMap` / `GraphView` と同じ:
//   ・Handle は消さずに描く（`isConnectable={false}`・見た目だけ CSS で隠す）。無いと線が描かれない。
//   ・矢じりの色は `markerEnd.color`（CSS からは届かない）。
//   ・NODE_TYPES / EDGE_TYPES はモジュールの上の階層（中に置くと毎回作り直される）。
//   ・形が変わったら `useUpdateNodeInternals` + `fitView` を rAF の中でやり直す。
//   ・`.react-flow__panel`（Controls）は隠さない。
// 「大きく見る」は付けない（共通部品を使って親が付ける）。
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { fitName, type OverviewEdge, type OverviewEdgeKind, type OverviewLayout } from './kindOverview'

const COUNT_H = 18

const EDGE_COLOR: Record<OverviewEdgeKind, string> = {
  link: 'var(--border-strong)',
  hub: 'var(--link)',
  standard: 'var(--accent)',
  alignment: 'var(--activity)',
}

type FrameData = { label: string; width: number; height: number }
type BandData = { label: string; hint: string; width: number; height: number }
type CircleData = {
  label: string
  shown: string
  countText: string
  r: number
  hub: boolean
}
type StdData = { label: string; vocab: string; width: number; height: number }

/** 線の出入り口は丸の真ん中（線は縁にそろえた座標を `data` で持つ）。 */
const HANDLE_STYLE = { left: '50%', top: '50%', opacity: 0 } as const

function FrameBox({ data }: NodeProps) {
  const d = data as FrameData
  return (
    <div className="vocab-map-cluster kind-ov-frame is-clickable" style={{ width: d.width, height: d.height }}>
      <Handle type="source" position={Position.Right} isConnectable={false} style={HANDLE_STYLE} />
      <Handle type="target" position={Position.Left} isConnectable={false} style={HANDLE_STYLE} />
      <span className="vocab-map-cluster-name">{d.label}</span>
    </div>
  )
}

function BandBox({ data }: NodeProps) {
  const d = data as BandData
  return (
    <div className="vocab-map-cluster vocab-map-band" style={{ width: d.width, height: d.height }}>
      <span className="vocab-map-cluster-name">{d.label}</span>
      <span className="vocab-map-band-hint">{d.hint}</span>
    </div>
  )
}

function CircleBox({ data }: NodeProps) {
  const d = data as CircleData
  return (
    <div
      className={`kind-ov-node${d.hub ? ' kind-ov-node--hub' : ''}`}
      style={{ width: 2 * d.r, height: 2 * d.r + COUNT_H }}
      title={d.label}
    >
      <div className="kind-ov-circle" style={{ width: 2 * d.r, height: 2 * d.r }}>
        <Handle type="source" position={Position.Top} isConnectable={false} style={{ ...HANDLE_STYLE, top: d.r }} />
        <Handle type="target" position={Position.Top} isConnectable={false} style={{ ...HANDLE_STYLE, top: d.r }} />
        <span className="kind-ov-name">{d.shown}</span>
      </div>
      <span className="kind-ov-count">{d.countText}</span>
    </div>
  )
}

function StdBox({ data }: NodeProps) {
  const d = data as StdData
  return (
    <div className="vocab-map-std" style={{ width: d.width, height: d.height }}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <Handle type="source" position={Position.Top} isConnectable={false} />
      <span className="vocab-map-std-term" title={d.label}>
        {d.label}
      </span>
      <span className="vocab-map-std-vocab" title={d.vocab}>
        {d.vocab}
      </span>
    </div>
  )
}

/** 縁から縁へのまっすぐな線（座標は配置が決めたもの）。 */
function LineEdge({ id, data, markerEnd, markerStart, style }: EdgeProps) {
  const d = data as { x1: number; y1: number; x2: number; y2: number }
  return (
    <BaseEdge
      id={id}
      path={`M ${d.x1},${d.y1} L ${d.x2},${d.y2}`}
      markerEnd={markerEnd}
      markerStart={markerStart}
      style={style}
    />
  )
}

const NODE_TYPES = { frame: FrameBox, band: BandBox, circle: CircleBox, std: StdBox }
const EDGE_TYPES = { line: LineEdge }

const arrow = (kind: OverviewEdgeKind) => ({
  type: MarkerType.ArrowClosed,
  width: 14,
  height: 14,
  color: EDGE_COLOR[kind],
})

/** 描く節と線。純関数にして、テストで形を確かめられるようにする。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（VocabMap の place と同じ理由）
export function toFlow(
  layout: OverviewLayout,
  words: { count: (n: number) => string; bandTitle: string; bandHint: string },
): { nodes: Node[]; edges: Edge[] } {
  const base = { draggable: false, selectable: false, connectable: false }
  const nodes: Node[] = []
  for (const f of layout.frames) {
    nodes.push({
      id: f.id,
      type: 'frame',
      position: { x: f.x, y: f.y },
      data: { label: f.label, width: f.w, height: f.h } satisfies FrameData,
      zIndex: 0,
      ...base,
    })
  }
  if (layout.band) {
    nodes.push({
      id: 'band:standard',
      type: 'band',
      position: { x: layout.band.x, y: layout.band.y },
      data: { label: words.bandTitle, hint: words.bandHint, width: layout.band.w, height: layout.band.h },
      zIndex: 0,
      ...base,
    })
  }
  const circle = (
    c: { id: string; label: string; count?: number; r: number; x: number; y: number },
    hub: boolean,
  ) =>
    nodes.push({
      id: c.id,
      type: 'circle',
      position: { x: c.x - c.r, y: c.y - c.r },
      data: {
        label: c.label,
        shown: fitName(c.label, c.r),
        countText: c.count != null ? words.count(c.count) : '',
        r: c.r,
        hub,
      } satisfies CircleData,
      zIndex: 2,
      ...base,
    })
  for (const c of layout.circles) circle(c, false)
  for (const h of layout.hubs) circle(h, true)
  for (const s of layout.stds) {
    nodes.push({
      id: s.id,
      type: 'std',
      position: { x: s.x, y: s.y },
      data: { label: s.label, vocab: s.vocab, width: s.w, height: s.h } satisfies StdData,
      zIndex: 1,
      ...base,
    })
  }
  const edges: Edge[] = layout.edges.map((e: OverviewEdge, i) => ({
    id: `${e.from}->${e.to}-${e.kind}-${i}`,
    source: e.from,
    target: e.to,
    type: 'line',
    data: { x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2 },
    className: `kind-ov-edge kind-ov-edge--${e.kind}`,
    markerEnd: e.kind === 'link' || e.kind === 'alignment' ? arrow(e.kind) : undefined,
    markerStart: e.both ? arrow(e.kind) : undefined,
    zIndex: 1,
  }))
  return { nodes, edges }
}

function KindOverviewInner({
  layout,
  ariaLabel,
  onOpenKind,
  onOpenDataset,
  onOpenCrosswalk,
  maxHeight = 620,
  zoomable = false,
}: {
  layout: OverviewLayout
  ariaLabel: string
  /** 種類の丸を押したときの行き先（種類のページ。引数は種類の IRI）。 */
  onOpenKind?: (classIri: string) => void
  /** データセットの枠を押したときの行き先（引数はカタログの id）。 */
  onOpenDataset?: (datasetId: string) => void
  /** ハブを押したときの行き先（つながりの画面）。 */
  onOpenCrosswalk?: () => void
  maxHeight?: number
  zoomable?: boolean
}) {
  const { t } = useTranslation()
  const { nodes, edges } = useMemo(
    () =>
      toFlow(layout, {
        count: (n) => t('vocab:overview.count', { n: n.toLocaleString('en-US') }),
        bandTitle: t('vocab:map.bandTitle'),
        bandHint: t('vocab:map.bandHint'),
      }),
    [layout, t],
  )

  const handleClick = useCallback(
    (_: unknown, node: Node) => {
      if (node.type === 'circle') {
        if (node.id.startsWith('hub:')) onOpenCrosswalk?.()
        else {
          const c = layout.circles.find((x) => x.id === node.id)
          if (c?.classIri) onOpenKind?.(c.classIri)
        }
      } else if (node.type === 'frame') onOpenDataset?.(node.id)
    },
    [layout, onOpenKind, onOpenDataset, onOpenCrosswalk],
  )

  const height = Math.min(maxHeight, Math.max(260, layout.height + 48))
  const fitKey = useMemo(
    () => nodes.map((n) => n.id).join('|') + '#' + edges.length + '#' + layout.width + 'x' + layout.height,
    [nodes, edges, layout],
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

  return (
    <div className="shape-graph vocab-map kind-ov" style={{ height }} role="img" aria-label={ariaLabel}>
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
        onNodeClick={handleClick}
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

export function KindOverview(props: {
  layout: OverviewLayout
  ariaLabel: string
  onOpenKind?: (classIri: string) => void
  onOpenDataset?: (datasetId: string) => void
  onOpenCrosswalk?: () => void
  maxHeight?: number
  zoomable?: boolean
}) {
  return (
    <ReactFlowProvider>
      <KindOverviewInner {...props} />
    </ReactFlowProvider>
  )
}
