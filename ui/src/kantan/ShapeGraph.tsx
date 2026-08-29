import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import {
  FIELD_H,
  layout,
  nodeHeight,
  NODE_H,
  NODE_W,
  type Shape,
  type ShapeField,
} from '../shapeGraph'

/** かんたん層の「形」の図。④は予告（点線あり）、⑤は結果（実線だけ）で、
 *  **同じ部品・同じ場所・同じ向き**で描く。触れる図にしてあるのは、箱が増えたり
 *  線が引かれたりするのがこの画面の操作の結果だから — 静止画だと、変わったのが
 *  自分の操作のせいなのか分からない。
 *
 *  ④⑤（かんたん層）・詳細モードの骨格図・データセット詳細の構造図は、すべて
 *  この 1 つの部品で描く。違うのは箱の呼び方と、項目を並べるかどうかだけ。 */

type ShapeNodeData = {
  label: string
  tone: string
  clickable: boolean
  width: number
  height: number
  /** 箱の中に並ぶ項目（構造図だけ）。畳んでいるときは空。 */
  fields: ShapeField[]
  /** 項目を持っているか（畳んでいても真）。畳むボタンを出すかの判断。 */
  foldable: boolean
  folded: boolean
  onFold?: () => void
}

/** 箱ひとつ。React Flow の既定の箱は英字前提の余白なので、自前で描く。 */
function ShapeBox({ data }: NodeProps) {
  const d = data as ShapeNodeData
  const cls = ['shape-node', `shape-node--${d.tone}`, d.clickable ? 'is-clickable' : '']
    .filter(Boolean)
    .join(' ')
  return (
    <div className={cls} style={{ width: d.width, height: d.height }}>
      {/* 線の出入り口。見せないが、無いと辺が箱の中心から生える。 */}
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="shape-node-head" style={{ height: NODE_H }}>
        <span>{d.label}</span>
        {d.foldable && (
          <button
            type="button"
            className="shape-node-fold"
            aria-expanded={!d.folded}
            onClick={(e) => {
              e.stopPropagation()
              d.onFold?.()
            }}
          >
            {d.folded ? '+' : '−'}
          </button>
        )}
      </div>
      {d.fields.length > 0 && (
        <ul className="shape-node-fields">
          {d.fields.map((f, i) => (
            <li key={i} style={{ height: FIELD_H }}>
              <span className="shape-field-name">{f.name}</span>
              {f.unit && <code className="shape-field-unit">{f.unit}</code>}
              {f.type && <code className="shape-field-type">{f.type}</code>}
            </li>
          ))}
        </ul>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  )
}

const NODE_TYPES = { shape: ShapeBox }

function ShapeGraphInner({
  shape,
  ariaLabel,
  onNodeClick,
  perRow = 2,
  nodeWidth = NODE_W,
  maxHeight = 440,
  foldedByDefault = false,
}: {
  shape: Shape
  ariaLabel: string
  /** 箱を押したときの行き先。渡さなければ箱は押せない。 */
  onNodeClick?: (id: string) => void
  /** 1 段に横並びにする上限。細い列は 2、幅のある画面は増やす。 */
  perRow?: number
  nodeWidth?: number
  maxHeight?: number
  /** 項目を最初は畳んでおく。細い列に置く図（⑤）は、開いたままだと縦に
   *  伸びすぎて `fitView` が縮め、字が読めなくなる。 */
  foldedByDefault?: boolean
}) {
  /* ⭐**ホバーの見た目は CSS だけでやる。** 触れた箱を React の state に持つと、
     描き直しのたびに `nodes` の配列が作り直され、React Flow が節を採り直す ——
     その 1 フレームのあいだ辺が DOM から消えて、また現れる（利用者報告
     2026-08-30「ノードにポインタを置くとチカチカ揺れる」。実測: ホバー 1 回で
     `react-flow__edges` から辺が remove → add、ホバーを外すと DOM 変化 0 件）。
     関係のない箱を沈める演出はここで手放した。箱は 2〜6 個で矢印も見えている
     ので、触れた箱が浮くだけで足りる。 */
  /** 畳んだ箱。項目が多い種類は自分で畳める（構造図）。 */
  const [folded, setFolded] = useState<ReadonlySet<string>>(
    () => new Set(foldedByDefault ? shape.nodes.map((n) => n.id) : []),
  )
  const heightOf = useCallback(
    (n: { id: string; fields?: ShapeField[] }) =>
      nodeHeight(n as Parameters<typeof nodeHeight>[0], folded.has(n.id)),
    [folded],
  )
  const pos = useMemo(
    () => layout(shape, { perRow, nodeWidth, heightOf }),
    [shape, perRow, nodeWidth, heightOf],
  )
  /* 高さは段数から決める。貼り付く細い列に置くので伸ばせる範囲には上限があり、
     それを超えた分は `fitView` が中で縮める。 */
  const height = useMemo(() => {
    const deepest = Math.max(
      0,
      ...shape.nodes.map((n) => (pos.get(n.id)?.y ?? 0) + heightOf(n)),
    )
    return Math.min(maxHeight, Math.max(176, deepest + 56))
  }, [pos, shape.nodes, heightOf, maxHeight])

  const nodes: Node[] = useMemo(
    () =>
      shape.nodes.map((n) => ({
        id: n.id,
        type: 'shape',
        position: pos.get(n.id) ?? { x: 0, y: 0 },
        data: {
          label: n.label,
          tone: n.tone,
          width: nodeWidth,
          height: heightOf(n),
          fields: folded.has(n.id) ? [] : (n.fields ?? []),
          foldable: (n.fields ?? []).length > 0,
          folded: folded.has(n.id),
          onFold: (n.fields ?? []).length
            ? () =>
                setFolded((prev) => {
                  const next = new Set(prev)
                  if (!next.delete(n.id)) next.add(n.id)
                  return next
                })
            : undefined,
          clickable: !!onNodeClick,
        } satisfies ShapeNodeData,
        draggable: false,
        selectable: false,
        connectable: false,
      })),
    [shape, pos, onNodeClick, nodeWidth, heightOf, folded],
  )

  const edges: Edge[] = useMemo(() => {
    /* 同じ文言のラベルは 1 度だけ出す。同じ相手へ 2 本引かれると、細い列では
       文字どうしが重なって両方読めなくなる（実機 2026-08-29）。予告の線は
       そもそもラベルを持たない — 点線の意味は図の下の注記が言っている。 */
    const said = new Set<string>()
    return shape.edges.map((e, i) => {
        const dup = !e.label || e.pending || said.has(e.label)
        if (e.label) said.add(e.label)
        return {
          id: `${e.from}->${e.to}-${i}`,
          source: e.from,
          target: e.to,
          label: dup ? undefined : e.label,
          animated: !!e.pending,
          className: e.pending ? 'shape-edge shape-edge--pending' : 'shape-edge',
          /* ⭐矢じりの定義は同じ設定の辺どうしで共有されるので、辺に付けた
             class から CSS では届かない。色はここで渡す（inline style になる
             ので CSS 変数が効く）。 */
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 16,
            height: 16,
            color: e.pending ? 'var(--accent)' : 'var(--border-strong)',
          },
        }
      })
  }, [shape])

  const handleClick = useCallback(
    (_: unknown, node: Node) => onNodeClick?.(node.id),
    [onNodeClick],
  )

  const fitKey = useMemo(
    () =>
      shape.nodes.map((n) => n.id).join('|') +
      '#' +
      shape.edges.map((e) => `${e.from}>${e.to}`).join('|') +
      '#' +
      [...folded].sort().join(','),
    [shape, folded],
  )

  /* 形が変わったら、箱の測り直しと拡大率の合わせ直しを**明示的に**やる。
     `fitView` は初回しか効かず、辺は箱の handle の実測（`handleBounds`）が
     取れるまで描かれない — 図が後から差し込まれる画面（⑤は取り込みルールが
     届いてから現れる）では、その実測が空のまま固まって**線だけが消えた**
     （実機 2026-08-29: 箱は出るのに矢印が 1 本も無い）。 */
  const rf = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  /* 測り直しの引き金は**形の署名だけ**。`shape` は呼ぶ側が毎レンダー組み直すので、
     `shape.nodes` を deps に入れると描き直しのたびに測り直すことになる。 */
  const shapeRef = useRef(shape)
  // 書き込みは描画中ではなく effect で（宣言順に走るので、下の合わせ直しより先）。
  useEffect(() => {
    shapeRef.current = shape
  })
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      updateNodeInternals(shapeRef.current.nodes.map((n) => n.id))
      rf.fitView({ padding: 0.08, maxZoom: 1 })
    })
    return () => cancelAnimationFrame(raf)
    // fitKey = 形の署名。同じ形で描き直しても測り直さない。
  }, [fitKey, rf, updateNodeInternals])

  return (
    <div className="shape-graph" style={{ height }} role="img" aria-label={ariaLabel}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
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
  perRow?: number
  nodeWidth?: number
  maxHeight?: number
  foldedByDefault?: boolean
}) {
  return (
    <ReactFlowProvider>
      <ShapeGraphInner {...props} />
    </ReactFlowProvider>
  )
}
