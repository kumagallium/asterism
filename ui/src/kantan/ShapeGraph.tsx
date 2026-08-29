import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background,
  BackgroundVariant,
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
import { layout, NODE_H, NODE_W, type Shape } from '../shapeGraph'

/** かんたん層の「形」の図。④は予告（点線あり）、⑤は結果（実線だけ）で、
 *  **同じ部品・同じ場所・同じ向き**で描く。触れる図にしてあるのは、箱が増えたり
 *  線が引かれたりするのがこの画面の操作の結果だから — 静止画だと、変わったのが
 *  自分の操作のせいなのか分からない。
 *
 *  詳細モードは今も mermaid（`skeletonMermaid`）。あちらは幅いっぱいの静止画で、
 *  読む人も違う。 */

type ShapeNodeData = {
  label: string
  tone: string
  dim: boolean
  hot: boolean
  clickable: boolean
}

/** 箱ひとつ。React Flow の既定の箱は英字前提の余白なので、自前で描く。 */
function ShapeBox({ data }: NodeProps) {
  const d = data as ShapeNodeData
  const cls = [
    'shape-node',
    `shape-node--${d.tone}`,
    d.hot ? 'is-hot' : '',
    d.dim ? 'is-dim' : '',
    d.clickable ? 'is-clickable' : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <div className={cls} style={{ width: NODE_W, minHeight: NODE_H }}>
      {/* 線の出入り口。見せないが、無いと辺が箱の中心から生える。 */}
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <span>{d.label}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  )
}

const NODE_TYPES = { shape: ShapeBox }

function ShapeGraphInner({
  shape,
  ariaLabel,
  onNodeClick,
}: {
  shape: Shape
  ariaLabel: string
  /** 箱を押したときの行き先。渡さなければ箱は押せない。 */
  onNodeClick?: (id: string) => void
}) {
  const [hot, setHot] = useState<string | null>(null)
  const pos = useMemo(() => layout(shape), [shape])
  /* 高さは段数から決める。貼り付く細い列に置くので伸ばせる範囲には上限があり、
     それを超えた分は `fitView` が中で縮める。 */
  const height = useMemo(() => {
    const deepest = Math.max(0, ...[...pos.values()].map((p) => p.y))
    return Math.min(440, Math.max(176, deepest + NODE_H + 56))
  }, [pos])

  const nodes: Node[] = useMemo(
    () =>
      shape.nodes.map((n) => ({
        id: n.id,
        type: 'shape',
        position: pos.get(n.id) ?? { x: 0, y: 0 },
        data: {
          label: n.label,
          tone: n.tone,
          hot: hot === n.id,
          // 1 つに触れているあいだ、関係ない箱は引っ込む。種類が増えるほど
          // 「この線はどこから来たのか」が読めなくなるので。
          dim:
            hot !== null &&
            hot !== n.id &&
            !shape.edges.some(
              (e) =>
                (e.from === hot && e.to === n.id) || (e.to === hot && e.from === n.id),
            ),
          clickable: !!onNodeClick,
        } satisfies ShapeNodeData,
        draggable: false,
        selectable: false,
        connectable: false,
      })),
    [shape, pos, hot, onNodeClick],
  )

  const edges: Edge[] = useMemo(() => {
    /* 同じ文言のラベルは 1 度だけ出す。同じ相手へ 2 本引かれると、細い列では
       文字どうしが重なって両方読めなくなる（実機 2026-08-29）。予告の線は
       そもそもラベルを持たない — 点線の意味は図の下の注記が言っている。 */
    const said = new Set<string>()
    return shape.edges.map((e, i) => {
        const touched = hot === null || hot === e.from || hot === e.to
        const dup = !e.label || e.pending || said.has(e.label)
        if (e.label) said.add(e.label)
        return {
          id: `${e.from}->${e.to}-${i}`,
          source: e.from,
          target: e.to,
          label: dup ? undefined : e.label,
          animated: !!e.pending,
          className: [
            'shape-edge',
            e.pending ? 'shape-edge--pending' : '',
            touched ? '' : 'is-dim',
          ]
            .filter(Boolean)
            .join(' '),
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        }
      })
  }, [shape, hot])

  const handleClick = useCallback(
    (_: unknown, node: Node) => onNodeClick?.(node.id),
    [onNodeClick],
  )

  const fitKey = useMemo(
    () =>
      shape.nodes.map((n) => n.id).join('|') +
      '#' +
      shape.edges.map((e) => `${e.from}>${e.to}`).join('|'),
    [shape],
  )

  /* 形が変わったら、箱の測り直しと拡大率の合わせ直しを**明示的に**やる。
     `fitView` は初回しか効かず、辺は箱の handle の実測（`handleBounds`）が
     取れるまで描かれない — 図が後から差し込まれる画面（⑤は取り込みルールが
     届いてから現れる）では、その実測が空のまま固まって**線だけが消えた**
     （実機 2026-08-29: 箱は出るのに矢印が 1 本も無い）。 */
  const rf = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      updateNodeInternals(shape.nodes.map((n) => n.id))
      rf.fitView({ padding: 0.18, maxZoom: 1 })
    })
    return () => cancelAnimationFrame(raf)
    // fitKey = 形の署名。同じ形で描き直しても測り直さない。
  }, [fitKey, rf, updateNodeInternals, shape.nodes])

  return (
    <div className="shape-graph" style={{ height }} role="img" aria-label={ariaLabel}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
        minZoom={0.4}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        onNodeMouseEnter={(_, n) => setHot(n.id)}
        onNodeMouseLeave={() => setHot(null)}
        onNodeClick={onNodeClick ? handleClick : undefined}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
      </ReactFlow>
    </div>
  )
}

/** React Flow の命令 API（`fitView` / 測り直し）を使うので、Provider の内側に
 *  置く必要がある。呼ぶ側はこれを 1 つ置くだけでよい。 */
export function ShapeGraph(props: {
  shape: Shape
  ariaLabel: string
  onNodeClick?: (id: string) => void
}) {
  return (
    <ReactFlowProvider>
      <ShapeGraphInner {...props} />
    </ReactFlowProvider>
  )
}
