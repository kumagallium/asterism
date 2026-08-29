import { useMemo } from 'react'
import {
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  HUB_H,
  HUB_W,
  SIDE_H,
  SIDE_W,
  xwLinkLayout,
  type XwSide,
} from './xwLinkShape'

/** つながりの関係図（K23）— データ —共通の値— データ を 1 枚の絵で言う。
 *
 *  「つながり」は 3 つの事実の組でできている: **どのデータとどのデータ**が、
 *  **どの項目の値**で、**何を同じとみなして**つながるか。これまでは同じ 3 つが
 *  「参加チップの行」「件数のバッジ」「同一視の 1 文」として画面の別々の場所に
 *  散っていて、読み手が頭の中で組み立て直す必要があった。
 *
 *  描画は React Flow — アプリの図はどこも同じ engine で描く（利用者の指示
 *  2026-08-29）。並べかたは自前: 2 つなら横に、3 つ以上なら左に縦積みして
 *  真ん中の 1 つに集める。**矢印は付けない** — つながりは向きを持たない
 *  （A も B も同じ値を持っている、というだけ）。
 *
 *  ⭐ノードの中身は利用者のデータ名と項目名で、長さが読めない。React Flow の
 *  箱は寸法が要るので、幅は固定して名前は 2 行で省略し、全文は `title` に置く
 *  （以前の flexbox 版は折り返しに頼っていた — 同じ問題への別の答え）。 */

function SideNode({ data }: NodeProps) {
  const d = data as unknown as XwSide & { hasLeft: boolean; hasRight: boolean }
  return (
    <>
      {d.hasLeft && <Handle type="target" position={Position.Left} isConnectable={false} />}
      <div className="xw-diagram-node" style={{ width: SIDE_W, height: SIDE_H }} title={d.title}>
        <span className="xw-diagram-name">{d.name}</span>
        {d.field && <span className="xw-diagram-field">{d.field}</span>}
      </div>
      {d.hasRight && <Handle type="source" position={Position.Right} isConnectable={false} />}
    </>
  )
}

function HubNode({ data }: NodeProps) {
  const d = data as { headline: string; note?: string }
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="xw-diagram-hub" style={{ width: HUB_W, minHeight: HUB_H }}>
        <span className="xw-diagram-head">{d.headline}</span>
        {d.note && <span className="xw-diagram-note">{d.note}</span>}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </>
  )
}

const NODE_TYPES = { xwSide: SideNode, xwHub: HubNode }

function XwLinkDiagramInner({
  sides,
  headline,
  note,
}: {
  /** つながる側。ふつうは 2 つ。 */
  sides: XwSide[]
  /** 中央の 1 行（例「12 件の値が一致」）。 */
  headline: string
  /** 中央の副文（例「書き方のゆれは同じものとして扱います」）。 */
  note?: string
}) {
  const { nodes, edges, height } = useMemo(() => {
    const shape = xwLinkLayout(sides)
    return {
      height: shape.height,
      edges: shape.edges.map((e) => ({ ...e, className: 'xw-edge' })) as Edge[],
      nodes: shape.nodes.map((n) => ({
        id: n.id,
        type: n.kind === 'hub' ? 'xwHub' : 'xwSide',
        position: { x: n.x, y: n.y },
        data:
          n.kind === 'hub'
            ? { headline, note }
            : { ...n.side, hasLeft: n.hasLeft, hasRight: n.hasRight },
        draggable: false,
        selectable: false,
        connectable: false,
      })) as Node[],
    }
  }, [sides, headline, note])

  return (
    <div className="xw-diagram" style={{ height: Math.min(height, 380) }}>
      <ReactFlow
        key={sides.map((s) => s.key).join('|')}
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
        minZoom={0.3}
        maxZoom={1.4}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      />
    </div>
  )
}

export function XwLinkDiagram(props: {
  sides: XwSide[]
  headline: string
  note?: string
}) {
  return (
    <ReactFlowProvider>
      <XwLinkDiagramInner {...props} />
    </ReactFlowProvider>
  )
}
