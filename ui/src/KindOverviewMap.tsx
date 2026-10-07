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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { DS_LABEL_W, edgeClassName, type OverviewFocus } from './kindOverviewScale'
import {
  HUB_LABEL_W,
  LABEL_H,
  LABEL_W,
  type OverviewEdge,
  type OverviewEdgeKind,
  type OverviewLayout,
} from './kindOverview'
import { BigViewOverlay, ExpandButton } from './BigView'
import { useBigViewHeight } from './bigViewSize'


const EDGE_COLOR: Record<OverviewEdgeKind, string> = {
  link: 'var(--border-strong)',
  hub: 'var(--link)',
  standard: 'var(--accent)',
  alignment: 'var(--activity)',
}

type FrameData = { label: string; width: number; height: number; omittedText?: string }
type BandData = { label: string; hint: string; width: number; height: number }
type CircleKind = 'kind' | 'hub' | 'dataset'
type CircleData = {
  label: string
  countText: string
  r: number
  kind: CircleKind
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
      {d.omittedText && <span className="kind-ov-frame-omitted">{d.omittedText}</span>}
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
  const w = nodeWidth(d.r, d.kind)
  return (
    <div
      className={`kind-ov-node${d.kind === 'hub' ? ' kind-ov-node--hub' : ''}${d.kind === 'dataset' ? ' kind-ov-node--dataset' : ''}`}
      style={{ width: w, height: 2 * d.r + LABEL_H }}
      title={d.countText ? `${d.label}（${d.countText}）` : d.label}
    >
      <div className="kind-ov-circle" style={{ width: 2 * d.r, height: 2 * d.r }}>
        <Handle type="source" position={Position.Top} isConnectable={false} style={{ ...HANDLE_STYLE, top: d.r }} />
        <Handle type="target" position={Position.Top} isConnectable={false} style={{ ...HANDLE_STYLE, top: d.r }} />
      </div>
      {/* 名前は丸の下（丸の中では小さい丸で切れて読めない）。2 行まで・続きは title。 */}
      <span className="kind-ov-name">{d.label}</span>
      <span className="kind-ov-count">{d.countText}</span>
    </div>
  )
}

/** 丸の節の幅 = 丸と、その下の名前の幅の広い方（ハブの名前は広めに取る）。 */
function nodeWidth(r: number, kind: CircleKind): number {
  return Math.max(2 * r, kind === 'hub' ? HUB_LABEL_W : kind === 'dataset' ? DS_LABEL_W : LABEL_W)
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
  const d = data as { x1: number; y1: number; x2: number; y2: number; title?: string }
  const line = (
    <BaseEdge
      id={id}
      path={`M ${d.x1},${d.y1} L ${d.x2},${d.y2}`}
      markerEnd={markerEnd}
      markerStart={markerStart}
      style={style}
    />
  )
  // 線の title（データセットごとの俯瞰: 「N 種類が参加」）。
  return d.title ? (
    <g>
      <title>{d.title}</title>
      {line}
    </g>
  ) : (
    line
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
  words: {
    count: (n: number) => string
    bandTitle: string
    bandHint: string
    /** データセットごとの俯瞰の丸の下（種類の数・件数の合計）。 */
    datasetCount: (kinds: number, n?: number) => string
    /** 枠の「ほか N 種類」。 */
    omitted: (n: number) => string
    /** 線の title「N 種類が参加」。 */
    participates: (n: number) => string
    hubBandTitle: string
    hubBandHint: string
  },
): { nodes: Node[]; edges: Edge[] } {
  const base = { draggable: false, selectable: false, connectable: false }
  const nodes: Node[] = []
  for (const f of layout.frames) {
    nodes.push({
      id: f.id,
      type: 'frame',
      position: { x: f.x, y: f.y },
      data: {
        label: f.label,
        width: f.w,
        height: f.h,
        omittedText: f.omitted ? words.omitted(f.omitted) : undefined,
      } satisfies FrameData,
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
  if (layout.hubBand) {
    nodes.push({
      id: 'band:hubs',
      type: 'band',
      position: { x: layout.hubBand.x, y: layout.hubBand.y },
      data: { label: words.hubBandTitle, hint: words.hubBandHint, width: layout.hubBand.w, height: layout.hubBand.h },
      zIndex: 0,
      ...base,
    })
  }
  const circle = (
    c: { id: string; label: string; count?: number; kindCount?: number; r: number; x: number; y: number },
    kind: CircleKind,
  ) =>
    nodes.push({
      id: c.id,
      type: 'circle',
      position: { x: c.x - nodeWidth(c.r, kind) / 2, y: c.y - c.r },
      data: {
        label: c.label,
        countText:
          kind === 'dataset' && c.kindCount != null
            ? words.datasetCount(c.kindCount, c.count)
            : c.count != null
              ? words.count(c.count)
              : '',
        r: c.r,
        kind,
      } satisfies CircleData,
      zIndex: 2,
      ...base,
    })
  for (const c of layout.circles) circle(c, layout.level === 'dataset' ? 'dataset' : 'kind')
  for (const h of layout.hubs) circle(h, 'hub')
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
    data: {
      x1: e.x1,
      y1: e.y1,
      x2: e.x2,
      y2: e.y2,
      kind: e.kind,
      title: e.kinds != null ? words.participates(e.kinds) : undefined,
    },
    className: edgeClassName(e.kind, e.from, e.to, null),
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
  onFocus,
  focus,
  maxHeight = 620,
  zoomable = false,
  expandable = true,
}: {
  layout: OverviewLayout
  ariaLabel: string
  /** 種類の丸を押したときの行き先（種類のページ。引数は種類の IRI）。 */
  onOpenKind?: (classIri: string) => void
  /** データセットの枠を押したときの行き先（引数はカタログの id）。 */
  onOpenDataset?: (datasetId: string) => void
  /** ハブを押したときの行き先（つながりの画面）。 */
  onOpenCrosswalk?: () => void
  /** データセットごとの俯瞰で丸・ハブを押したときの行き先（その周りだけを開く）。 */
  onFocus?: (f: OverviewFocus) => void
  /** 周りだけ開いているとき: いま見ているものの名前と、全体へ戻る操作。 */
  focus?: { label: string; onBack: () => void }
  maxHeight?: number
  zoomable?: boolean
  /** 右上の「大きく見る」（重ね表示の中では出さない）。 */
  expandable?: boolean
}) {
  const { t } = useTranslation()
  const [big, setBig] = useState(false)
  const bigH = useBigViewHeight(big)
  const [active, setActive] = useState<string | null>(null)
  const { nodes, edges: baseEdges } = useMemo(
    () =>
      toFlow(layout, {
        count: (n) => t('vocab:overview.count', { n: n.toLocaleString('en-US') }),
        bandTitle: t('vocab:map.bandTitle'),
        bandHint: t('vocab:map.bandHint'),
        datasetCount: (kinds, n) =>
          n != null
            ? t('vocab:overview.datasetKindsCount', { kinds, n: n.toLocaleString('en-US') })
            : t('vocab:overview.datasetKinds', { kinds }),
        omitted: (n) => t('vocab:overview.omitted', { n }),
        participates: (n) => t('vocab:overview.participates', { n }),
        hubBandTitle: t('vocab:overview.hubBandTitle'),
        hubBandHint: t('vocab:overview.hubBandHint'),
      }),
    [layout, t],
  )
  // 丸・ハブに載せたら、その丸につながる線を濃く・他を薄く（CSS のホバーは線に届かない）。
  const edges = useMemo(
    () =>
      active
        ? baseEdges.map((e) => ({
            ...e,
            className: edgeClassName((e.data as { kind: OverviewEdgeKind }).kind, e.source, e.target, active),
          }))
        : baseEdges,
    [baseEdges, active],
  )

  const handleClick = useCallback(
    (_: unknown, node: Node) => {
      if (layout.level === 'dataset') {
        // データセットごとの俯瞰: 押すとその周りだけを種類まで開く。
        if (node.type === 'circle')
          onFocus?.({ type: node.id.startsWith('hub:') ? 'hub' : 'dataset', id: node.id })
        return
      }
      if (node.type === 'circle') {
        if (node.id.startsWith('hub:')) onOpenCrosswalk?.()
        else {
          const c = layout.circles.find((x) => x.id === node.id)
          if (c?.classIri) onOpenKind?.(c.classIri)
        }
      } else if (node.type === 'frame') onOpenDataset?.(node.id)
    },
    [layout, onOpenKind, onOpenDataset, onOpenCrosswalk, onFocus],
  )

  const height = Math.min(maxHeight, Math.max(260, layout.height + 48))
  const onEnter = useCallback((_: unknown, node: Node) => {
    if (node.type === 'circle') setActive(node.id)
  }, [])
  const onLeave = useCallback(() => setActive(null), [])
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
      {focus && (
        <div className="kind-ov-focusbar">
          <button type="button" className="kind-ov-back" onClick={focus.onBack}>
            {t('vocab:overview.back')}
          </button>
          <span className="kind-ov-around">{t('vocab:overview.around', { name: focus.label })}</span>
        </div>
      )}
      {expandable && <ExpandButton onClick={() => setBig(true)} />}
      <BigViewOverlay open={big} onClose={() => setBig(false)} title={ariaLabel}>
        <KindOverview
          layout={layout}
          ariaLabel={ariaLabel}
          onOpenKind={onOpenKind}
          onOpenDataset={onOpenDataset}
          onOpenCrosswalk={onOpenCrosswalk}
          onFocus={onFocus}
          focus={focus}
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
        onNodeClick={handleClick}
        onNodeMouseEnter={onEnter}
        onNodeMouseLeave={onLeave}
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
  onFocus?: (f: OverviewFocus) => void
  focus?: { label: string; onBack: () => void }
  maxHeight?: number
  zoomable?: boolean
  expandable?: boolean
}) {
  return (
    <ReactFlowProvider>
      <KindOverviewInner {...props} />
    </ReactFlowProvider>
  )
}
