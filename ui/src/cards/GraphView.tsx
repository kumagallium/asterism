import { useCallback, useEffect, useMemo, useRef } from 'react'
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
import { useTranslation } from 'react-i18next'
import '@xyflow/react/dist/style.css'
import './graph.css'
import { layoutGraph } from './graphLayout'
import { nodePropLines } from './graphProps'
import type { GraphNodeKind, GraphSpec } from './viewSpec'

/** GraphSpec を React Flow で描く。`ui/src/kantan/ShapeGraph.tsx` の
 *  `ShapeGraphInner` の罠回避パターンをそのまま写す:
 *  - Handle は `display:none` にせず `isConnectable={false}` のまま描画し、
 *    見た目だけ CSS（`graph.css`）で隠す。
 *  - 矢じりの色は `markerEnd.color`（CSS からは届かない）。
 *  - ホバーの見た目は CSS だけ（React state に持たない）。
 *  - `fitView` は初回しか効かないので、形が変わったら
 *    `useUpdateNodeInternals` + `rf.fitView` を rAF の中でやり直す。
 *  - `.react-flow__panel`（Controls）は隠さない。 */

type FlowBoxData = {
  label: string
  kind: GraphNodeKind
  propLines: string[]
  horizontal: boolean
  clickable: boolean
}

function FlowBox({ data }: NodeProps) {
  const d = data as unknown as FlowBoxData
  const cls = ['flow-node', `flow-node--${d.kind}`, d.clickable ? 'is-clickable' : '']
    .filter(Boolean)
    .join(' ')
  const targetPos = d.horizontal ? Position.Left : Position.Top
  const sourcePos = d.horizontal ? Position.Right : Position.Bottom
  return (
    <div className={cls}>
      <Handle type="target" position={targetPos} isConnectable={false} />
      <span className="flow-node-label">{d.label}</span>
      {d.propLines.length > 0 && (
        <div className="flow-node-props">
          {d.propLines.map((line) => (
            <code key={line}>{line}</code>
          ))}
        </div>
      )}
      <Handle type="source" position={sourcePos} isConnectable={false} />
    </div>
  )
}

/** `ingest/src/asterism/prov_graph.py` の `_EDGE_LABELS` が辺に付ける短い英語
 *  （例: `generated`）→ 契約メモ §1.6 の i18n ローカル名（`graph.edge.<局所名>`）。
 *  知らないラベル（マップに無い）はそのまま素通しする（`defaultValue` と同じ
 *  考え方）。 */
const EDGE_LABEL_LOCAL_NAME: Record<string, string> = {
  used: 'used',
  generated: 'wasGeneratedBy',
  derived: 'wasDerivedFrom',
  attributed: 'wasAttributedTo',
  associated: 'wasAssociatedWith',
  informed: 'wasInformedBy',
  quoted: 'wasQuotedFrom',
}

function translateEdgeLabel(t: (key: string, options?: Record<string, unknown>) => string, label?: string): string | undefined {
  if (!label) return label
  const localName = EDGE_LABEL_LOCAL_NAME[label]
  if (!localName) return label
  return t(`graph.edge.${localName}`, { defaultValue: label })
}

const NODE_TYPES = { flowbox: FlowBox }
const NODE_W = 168
const COMPACT_NODE_W = 120
const NODE_H = 46

function GraphViewInner({
  graph,
  ariaLabel,
  onNodeClick,
  maxHeight = 240,
  compact = false,
}: {
  graph: GraphSpec
  ariaLabel: string
  onNodeClick?: (id: string) => void
  maxHeight?: number
  compact?: boolean
}) {
  const { t } = useTranslation('cards')
  const horizontal = (graph.direction ?? 'LR') === 'LR'
  const nodeW = compact ? COMPACT_NODE_W : NODE_W

  const { positions, height } = useMemo(
    () => layoutGraph(graph, { nodeWidth: nodeW, nodeHeight: NODE_H }),
    [graph, nodeW],
  )

  const nodes: Node[] = useMemo(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: 'flowbox',
        position: positions.get(n.id) ?? { x: 0, y: 0 },
        style: { width: nodeW },
        data: {
          label: n.label,
          kind: n.kind,
          propLines: compact ? [] : nodePropLines(n.props, t),
          horizontal,
          clickable: !!onNodeClick,
        } satisfies FlowBoxData,
        draggable: false,
        selectable: false,
        connectable: false,
      })),
    [graph, positions, horizontal, onNodeClick, compact, nodeW, t],
  )

  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((e, i) => ({
        id: `${e.from}->${e.to}-${i}`,
        source: e.from,
        target: e.to,
        label: translateEdgeLabel(t, e.label),
        // ⭐矢じりの色は markerEnd.color（辺の className から CSS では届かない）。
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 16,
          height: 16,
          color: 'var(--border-strong)',
        },
      })),
    [graph, t],
  )

  const handleClick = useCallback((_: unknown, node: Node) => onNodeClick?.(node.id), [onNodeClick])

  const fitKey = useMemo(
    () =>
      graph.nodes.map((n) => n.id).join('|') +
      '#' +
      graph.edges.map((e) => `${e.from}>${e.to}`).join('|') +
      '#' +
      (graph.direction ?? 'LR'),
    [graph],
  )

  /* ⭐形が変わったら箱の測り直しと fitView をやり直す。`graph` は呼ぶ側が
     毎レンダー組み直すことがあるので、deps には形の署名（fitKey）だけを使う。 */
  const rf = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  const graphRef = useRef(graph)
  useEffect(() => {
    graphRef.current = graph
  })
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      updateNodeInternals(graphRef.current.nodes.map((n) => n.id))
      rf.fitView({ padding: compact ? 0.1 : 0.2, maxZoom: 1 })
    })
    return () => cancelAnimationFrame(raf)
    // fitKey = 形の署名。同じ形で描き直しても測り直さない。
  }, [fitKey, rf, updateNodeInternals, compact])

  const viewHeight = Math.min(maxHeight, Math.max(176, height + 56))
  const rootClass = compact ? 'flow-graph flow-graph--compact' : 'flow-graph'

  return (
    <div className={rootClass} style={{ height: viewHeight }} role="img" aria-label={ariaLabel}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: compact ? 0.1 : 0.2, maxZoom: 1 }}
        minZoom={0.08}
        maxZoom={2.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={!compact}
        panOnScroll={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={onNodeClick ? handleClick : undefined}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        {!compact && <Controls showInteractive={false} position="bottom-right" />}
      </ReactFlow>
    </div>
  )
}

/** React Flow の命令 API を使うので Provider の内側に置く。呼ぶ側はこれを
 *  1 つ置くだけでよい（`ShapeGraph` と同じ形）。 */
export function GraphView(props: {
  graph: GraphSpec
  ariaLabel: string
  onNodeClick?: (id: string) => void
  maxHeight?: number
  compact?: boolean
}) {
  return (
    <ReactFlowProvider>
      <GraphViewInner {...props} />
    </ReactFlowProvider>
  )
}
