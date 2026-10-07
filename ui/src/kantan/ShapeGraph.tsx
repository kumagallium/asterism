import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BigViewOverlay, ExpandButton } from '../BigView'
import { useBigViewHeight } from '../bigViewSize'
import { ChevronIcon } from '../icons'
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  getBezierPath,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  useUpdateNodeInternals,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  arrange,
  boxWidthFor,
  edgeLabels,
  edgePath,
  FIELD_H,
  hasSideBySide,
  isPlainEdge,
  pointOnEdge,
  nodeHeight,
  NODE_H,
  NODE_W,
  type Route,
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

export type ShapeNodeData = {
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
  words: { open: string; close: string }
}

/** 箱ひとつ。React Flow の既定の箱は英字前提の余白なので、自前で描く。
 *  「共通のことば」の地図（VocabGraph）も同じ箱を使う — 同じ種類は
 *  どの画面でも同じ見た目であってほしい。 */
export function ShapeBox({ data }: NodeProps) {
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
          /* ⭐`+` / `−` は「足す・消す」に読めた（利用者評価 2026-08-30）。
             開き閉じは山形で言う — 閉じているときは右、開いたら下。 */
          <button
            type="button"
            className={d.folded ? 'shape-node-fold' : 'shape-node-fold is-open'}
            aria-expanded={!d.folded}
            aria-label={d.folded ? d.words.open : d.words.close}
            title={d.folded ? d.words.open : d.words.close}
            onClick={(e) => {
              e.stopPropagation()
              d.onFold?.()
            }}
          >
            <ChevronIcon size={13} />
          </button>
        )}
      </div>
      {d.fields.length > 0 && (
        <ul className="shape-node-fields">
          {d.fields.map((f, i) => (
            <li key={i} style={{ height: FIELD_H }}>
              <span className="shape-field-name">{f.name}</span>
              {/* 実データの例。項目名と型だけでは「何が入っているか」は分からない
                  （Phase 2・中身タブ）。型/単位より前に置くのは、読み手が見に
                  来たのが中身のほうだから。 */}
              {f.example && (
                <span className="shape-field-example" title={f.example}>
                  {f.example}
                </span>
              )}
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

/** 線ひとつ。React Flow の既定の線は、名前を必ず線のまん中に置く。交わる線は
 *  まん中が同じ点になり、長い名前は隣の名前に重なるので、`edgeLabels()` が
 *  決めた位置に置く。線そのものは既定と同じ bezier で、見た目は変えない。位置は
 *  実測の端から計算する（`pointOnEdge` は React Flow の bezier と同じ式）。
 *
 *  まっすぐな部分を持つ線（途中の段の席を通る線・出どころの段の下端まで降りる線）
 *  だけは、自前の経路にする — 既定の線は途中の点を持てず、箱の裏を通る。
 *
 *  「共通のことば」の地図（VocabMap）の、データセットの枠の中の線もこれで引く —
 *  枠の中は同じ `arrange` で並べるので、席も同じ線で通す。 */
export function ShapeEdgeLine({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerStart,
  markerEnd,
  label,
  data,
}: EdgeProps) {
  const [bezier, midX, midY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })
  const { labelAt: at, route } = (data ?? {}) as { labelAt?: number; route?: Route }
  const from = { x: sourceX, y: sourceY }
  const to = { x: targetX, y: targetY }
  const plain = isPlainEdge(from, to, route)
  const path = plain ? bezier : edgePath(from, to, route)
  const p =
    at === undefined && plain ? { x: midX, y: midY } : pointOnEdge(from, to, at ?? 0.5, route)
  return (
    <BaseEdge
      id={id}
      path={path}
      labelX={p.x}
      labelY={p.y}
      label={label}
      markerStart={markerStart}
      markerEnd={markerEnd}
    />
  )
}

/** 席のぶんの場所取り。何も描かない。席は箱ではないので React Flow は席を知らず、
 *  図を枠に合わせるとき（`fitView`）に数えるのは箱だけ — 箱の外側にある席と、
 *  そこを通る線・線の名前が、枠からはみ出す。図と同じ大きさの見えない箱を 1 つ
 *  置いて、合わせる範囲に席を入れる。 */
function SeatFrame({ data }: NodeProps) {
  const d = data as { width: number; height: number }
  return <div style={{ width: d.width, height: d.height }} />
}
/** 場所取りの箱の ID。種類の ID と重なったら、重ならなくなるまで伸ばす。 */
const seatFrameId = (shape: Shape): string => {
  const taken = new Set(shape.nodes.map((n) => n.id))
  let id = '(seats)'
  while (taken.has(id)) id += '*'
  return id
}

// ⭐モジュールの上の階層に置く。中に置くと毎回作り直され、React Flow が辺を採り直す。
const EDGE_TYPES = { shape: ShapeEdgeLine }
const NODE_TYPES = { shape: ShapeBox, seats: SeatFrame }

/** 段数の上限。これを超えたら横に広げる。 */
const MAX_ROWS = 4

/** 細い列（17rem）で箱が横に並ぶときの幅の下限。2 つ並んで約 0.9 倍に収まり、
 *  5 字の名前（値段の記録）が 1 行で入る。112 なら等倍だが名前が 2 行に折れ、
 *  176 のままだと 0.67 倍まで縮む（実機 2026-09-30）。名前が長いときは、2 行に
 *  収まる幅まで広げる（`boxWidthFor`）。 */
const NARROW_W = 132
/** 細い列の図の、上の帯の高さ（操作ボタンの段）。**CSS と一致していること。** */
const NARROW_BAND = 40
/* 図を枠に合わせるときの余白。細い列の図は上の帯を空け、残りは列の幅を
   使い切る。⭐モジュールの上の階層に置く（毎回作り直すと合わせ直しが走る）。 */
const FIT = { padding: 0.08, maxZoom: 1 }
const FIT_NARROW = {
  padding: { top: `${NARROW_BAND}px`, right: '8px', bottom: '10px', left: '8px' },
  maxZoom: 1,
} as const

function ShapeGraphInner({
  shape,
  ariaLabel,
  onNodeClick,
  perRow = 2,
  nodeWidth = NODE_W,
  maxHeight = 440,
  foldedByDefault = false,
  expandable = true,
  zoomable = false,
  narrow = false,
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
  /** 「大きく見る」を出すか。全画面の中身自身は出さない。 */
  expandable?: boolean
  /** ホイールで拡大縮小できるか。ページの中に貼り付いた図で有効にすると
   *  ページのスクロールを奪うので、全画面のときだけ。 */
  zoomable?: boolean
  /** 細い列に貼り付く図（段 6「ためす」）。同じ親の子を横に並べると、図は列の
   *  幅いっぱいに広がる — そのままだと縮んで字が読めず、左下の操作ボタンが
   *  箱に重なる（実機 2026-09-30）。横に並ぶ段があるときは箱を細くし、操作
   *  ボタンは上の帯に横に並べる。 */
  narrow?: boolean
}) {
  /* ⭐**ホバーの見た目は CSS だけでやる。** 触れた箱を React の state に持つと、
     描き直しのたびに `nodes` の配列が作り直され、React Flow が節を採り直す ——
     その 1 フレームのあいだ辺が DOM から消えて、また現れる（利用者報告
     2026-08-30「ノードにポインタを置くとチカチカ揺れる」。実測: ホバー 1 回で
     `react-flow__edges` から辺が remove → add、ホバーを外すと DOM 変化 0 件）。
     関係のない箱を沈める演出はここで手放した。箱は 2〜6 個で矢印も見えている
     ので、触れた箱が浮くだけで足りる。 */
  const { t } = useTranslation()
  /** 畳んだ箱。項目が多い種類は自分で畳める（構造図）。 */
  const [folded, setFolded] = useState<ReadonlySet<string>>(
    () => new Set(foldedByDefault ? shape.nodes.map((n) => n.id) : []),
  )
  const heightOf = useCallback(
    (n: { id: string; fields?: ShapeField[] }) =>
      nodeHeight(n as Parameters<typeof nodeHeight>[0], folded.has(n.id)),
    [folded],
  )
  /* ⭐節が増えても縦一列のままだと、細い列に収めるために `fitView` が縮め続け、
     やがて `minZoom` で止まって**画面から溢れる**（利用者報告 2026-08-30
     「ノード数を増やすとグラフが消えました」。実測: 10 個で 0.4 に張り付き、
     11 個目から外に出ていた）。段数の上限を決めて、超えた分は横に広げる。
     いま効くのは**線のない箱**だけ — 線のある箱は深さごとに 1 行で、折り返さ
     ない（`rowsOf`）。 */
  const cols = Math.max(perRow, Math.ceil(shape.nodes.length / MAX_ROWS))
  // 段をまたぐ線の席も、横に並ぶものに数える（箱 1 つと席 1 つの段も、幅を取る）。
  const sideBySide = useMemo(() => hasSideBySide(shape, cols), [shape, cols])
  const boxWidth = useMemo(
    () =>
      narrow && sideBySide
        ? boxWidthFor(shape, Math.min(nodeWidth, NARROW_W), nodeWidth)
        : nodeWidth,
    [shape, narrow, sideBySide, nodeWidth],
  )
  const placed = useMemo(
    () => arrange(shape, { perRow: cols, nodeWidth: boxWidth, heightOf }),
    [shape, cols, boxWidth, heightOf],
  )
  const pos = placed.pos
  /** 場所取りの箱の ID。席の無い図には置かない。 */
  const frameId = useMemo(
    () => (placed.routes.some((r) => r.via.length > 0) ? seatFrameId(shape) : undefined),
    [placed, shape],
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

  const boxes: Node[] = useMemo(
    () =>
      shape.nodes.map((n) => ({
        id: n.id,
        type: 'shape',
        position: pos.get(n.id) ?? { x: 0, y: 0 },
        data: {
          label: n.label,
          tone: n.tone,
          width: boxWidth,
          height: heightOf(n),
          fields: folded.has(n.id) ? [] : (n.fields ?? []),
          foldable: (n.fields ?? []).length > 0,
          folded: folded.has(n.id),
          words: { open: t('skeletongate:diagram.openFields'), close: t('skeletongate:diagram.closeFields') },
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
    [shape, pos, onNodeClick, boxWidth, heightOf, folded, t],
  )
  // 席がある図にだけ、場所取りの箱を足す。席の無い図は、今までと同じ節のまま。
  const nodes: Node[] = useMemo(
    () =>
      frameId !== undefined
        ? [
            {
              id: frameId,
              type: 'seats',
              position: { x: 0, y: 0 },
              data: { width: placed.width, height: placed.height },
              draggable: false,
              selectable: false,
              connectable: false,
              focusable: false,
              zIndex: -1,
            },
            ...boxes,
          ]
        : boxes,
    [boxes, frameId, placed.width, placed.height],
  )

  const edges: Edge[] = useMemo(() => {
    // 名前を出すか・どこに置くかは `edgeLabels()` が決める（規則は 1 か所）。
    const labels = edgeLabels(shape, pos, { nodeWidth: boxWidth, heightOf, routes: placed.routes })
    return shape.edges.map((e, i) => ({
      id: `${e.from}->${e.to}-${i}`,
      source: e.from,
      target: e.to,
      type: 'shape',
      label: labels[i]?.text,
      data: { labelAt: labels[i]?.at, route: placed.routes[i] },
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
    }))
  }, [shape, pos, placed, boxWidth, heightOf])

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
      [...folded].sort().join(',') +
      '#' +
      boxWidth +
      '#' +
      // 席の幅は線の名前で決まる。名前だけが変わっても、図の幅が変わる。
      placed.width,
    [shape, folded, boxWidth, placed.width],
  )

  /* 形が変わったら、箱の測り直しと拡大率の合わせ直しを**明示的に**やる。
     `fitView` は初回しか効かず、辺は箱の handle の実測（`handleBounds`）が
     取れるまで描かれない — 図が後から差し込まれる画面（⑤は取り込みルールが
     届いてから現れる）では、その実測が空のまま固まって**線だけが消えた**
     （実機 2026-08-29: 箱は出るのに矢印が 1 本も無い）。 */
  const rf = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  const fit = narrow ? FIT_NARROW : FIT
  /* 枠の大きさ。箱を開くと図の高さが変わるが、React Flow が新しい高さを知るのは
     次のコマ — 形が変わった瞬間に合わせると、古い高さに収めようとして縮みすぎる
     （実測 2026-09-30: 開いた直後 0.70 倍、「画面ぴったり」を押すと 0.89 倍）。
     枠の大きさが変わったときにも合わせ直す。 */
  const paneW = useStore((st) => st.width)
  const paneH = useStore((st) => st.height)
  /* 測り直しの引き金は**形の署名だけ**。`shape` は呼ぶ側が毎レンダー組み直すので、
     `shape.nodes` を deps に入れると描き直しのたびに測り直すことになる。 */
  const shapeRef = useRef(shape)
  const frameRef = useRef(frameId)
  // 書き込みは描画中ではなく effect で（宣言順に走るので、下の合わせ直しより先）。
  useEffect(() => {
    shapeRef.current = shape
    frameRef.current = frameId
  })
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      // 場所取りの箱も測り直す。席の幅は線の名前で変わる。
      const ids = shapeRef.current.nodes.map((n) => n.id)
      updateNodeInternals(frameRef.current === undefined ? ids : [...ids, frameRef.current])
      rf.fitView(fit)
    })
    return () => cancelAnimationFrame(raf)
    // fitKey = 形の署名。同じ形で描き直しても測り直さない。
  }, [fitKey, fit, paneW, paneH, rf, updateNodeInternals])

  /** 大きく見る。細い列に貼り付いた図は、節が増えると読める大きさで収まらない
   *  （利用者評価 2026-08-30）。同じ図を画面いっぱいで開く。 */
  const [big, setBig] = useState(false)
  const bigH = useBigViewHeight(big)

  return (
    <div
      className={narrow ? 'shape-graph shape-graph--narrow' : 'shape-graph'}
      style={{ height }}
      role="img"
      aria-label={ariaLabel}
    >
      {expandable && <ExpandButton onClick={() => setBig(true)} />}
      <BigViewOverlay open={big} onClose={() => setBig(false)} title={ariaLabel}>
        {/* 中身は同じ部品。⭐広いほうでは**段を折り返さない** — 折り返すと
            同じ段の箱が上下に並び、親から下の段へ引かれた線が上の段の箱から
            出ているように見える（実測 2026-08-30: 10 個の兄弟が 3 段に割れて
            鎖のように読めた）。横に長くなった分はホイールと手で動かせる。 */}
        <ShapeGraph
          shape={shape}
          ariaLabel={ariaLabel}
          onNodeClick={onNodeClick}
          perRow={Math.max(1, shape.nodes.length)}
          nodeWidth={216}
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
        fitViewOptions={fit}
        /* ⭐段を折り返さないので、兄弟が多い図は横に長い。下限に張り付くと図が
           枠の外へ出る（0.08 だと、細い列で兄弟 15 個から）。 */
        minZoom={0.02}
        maxZoom={2.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll={false}
        zoomOnScroll={zoomable}
        zoomOnDoubleClick={zoomable}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={onNodeClick ? handleClick : undefined}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        {/* 拡大・縮小・画面ぴったり。図が小さくなるほど要る（利用者評価
            2026-08-30）。錠前は出さない — 節はもともと動かせない。
            細い列の図では上の帯に横に並べる — 左下に置くと、列の幅いっぱいに
            広がった図の箱に重なる。「画面ぴったり」も帯を空けて合わせる。 */}
        <Controls
          showInteractive={false}
          position={narrow ? 'top-left' : 'bottom-left'}
          orientation={narrow ? 'horizontal' : 'vertical'}
          fitViewOptions={narrow ? { padding: FIT_NARROW.padding } : undefined}
          aria-label={t('skeletongate:diagram.controls')}
        />
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
  expandable?: boolean
  zoomable?: boolean
  narrow?: boolean
}) {
  return (
    <ReactFlowProvider>
      <ShapeGraphInner {...props} />
    </ReactFlowProvider>
  )
}
