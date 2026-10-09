// 「ことば」の地図の「全体」表示（案 B）の**描画**（shared-vocab-graph.md §6）。
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
import './vocabMapShared.css'
import { DS_LABEL_W, edgeClassName, type OverviewFocus } from './kindOverviewScale'
import { relationSymbol } from './lineChoice'
import {
  compactCount,
  countInside,
  HUB_LABEL_W,
  LABEL_H,
  LABEL_W,
  pickEndOfOverview,
  type OverviewEdge,
  type OverviewEdgeKind,
  type OverviewLayout,
} from './kindOverview'
import { BigViewOverlay, ExpandButton } from './BigView'
import { useBigViewHeight } from './bigViewSize'
import type { VocabPick } from './VocabMap'


const EDGE_COLOR: Record<OverviewEdgeKind, string> = {
  link: 'var(--border-strong)',
  hub: 'var(--link)',
  standard: 'var(--accent)',
  alignment: 'var(--activity)',
  upper: 'var(--link)',
}

type FrameData = { label: string; width: number; height: number; omittedText?: string }
type BandData = { label: string; hint: string; width: number; height: number; variant: 'std' | 'hubs' | 'shared' }
type CircleKind = 'kind' | 'hub' | 'dataset' | 'shared'
type CircleData = {
  label: string
  /** 名前の下に出す件数の字（丸の中に出すときは空・データセットは「N 種類」だけ）。 */
  countText: string
  /** 丸の中に出す短い件数（半径 22 以上のときだけ）。 */
  insideText: string
  /** title 用の全文（正確な数）。 */
  fullText: string
  r: number
  kind: CircleKind
  /** 共有のことばだけ: まだ線になっていない（点線の縁）。 */
  orphan?: boolean
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
    <div className={`kind-ov-band kind-ov-band--${d.variant}`} style={{ width: d.width, height: d.height }}>
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
      className={`kind-ov-node${d.kind === 'hub' ? ' kind-ov-node--hub' : ''}${d.kind === 'dataset' ? ' kind-ov-node--dataset' : ''}${d.kind === 'shared' ? ' kind-ov-node--shared' : ''}${d.orphan ? ' is-orphan' : ''}`}
      style={{ width: w, height: 2 * d.r + LABEL_H }}
      title={d.fullText ? `${d.label}（${d.fullText}）` : d.label}
    >
      <div className="kind-ov-circle" style={{ width: 2 * d.r, height: 2 * d.r }}>
        {d.insideText && <span className="kind-ov-inside">{d.insideText}</span>}
        <Handle type="source" position={Position.Top} isConnectable={false} style={{ ...HANDLE_STYLE, top: d.r }} />
        <Handle type="target" position={Position.Top} isConnectable={false} style={{ ...HANDLE_STYLE, top: d.r }} />
      </div>
      {/* 名前は丸の下（丸の中では小さい丸で切れて読めない）。2 行まで・続きは title。 */}
      <span className="kind-ov-name">{d.label}</span>
      {d.countText && <span className="kind-ov-count">{d.countText}</span>}
    </div>
  )
}

/** 丸の節の幅 = 丸と、その下の名前の幅の広い方（ハブの名前は広めに取る）。 */
function nodeWidth(r: number, kind: CircleKind): number {
  return Math.max(2 * r, kind === 'hub' || kind === 'shared' ? HUB_LABEL_W : kind === 'dataset' ? DS_LABEL_W : LABEL_W)
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

/** 線の path。制御点（cx, cy）があれば二次ベジェ、無ければ直線。純関数にしてテストで確かめる。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（toFlow と同じ理由）
export function edgePath(d: { x1: number; y1: number; x2: number; y2: number; cx?: number; cy?: number }): string {
  return d.cx != null && d.cy != null ? `M ${d.x1},${d.y1} Q ${d.cx},${d.cy} ${d.x2},${d.y2}` : `M ${d.x1},${d.y1} L ${d.x2},${d.y2}`
}

/** 縁から縁への線（座標は配置が決めたもの）。別の丸をよけるときは二次ベジェ、そうでなければ直線。 */
function LineEdge({ id, data, markerEnd, markerStart, style }: EdgeProps) {
  const d = data as { x1: number; y1: number; x2: number; y2: number; cx?: number; cy?: number; title?: string }
  const line = (
    <BaseEdge
      id={id}
      path={edgePath(d)}
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
    /** 件数の短い形（丸の中に書く）。 */
    compact: (n: number) => string
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
    /** 共有のことばの帯（省略可 — 無ければ空）。 */
    sharedBandTitle?: string
    sharedBandHint?: string
    /** 共有のことばの丸の title「子の合計 N 件」（件数のある子が無いときは n なし）。 */
    sharedTotal?: (n?: number) => string
    /** 共有のことばの札「まだ線になっていない」。 */
    sharedOrphan?: string
  },
  /** 選んでいる節の id（線を引く選択）。 */
  picked: readonly string[] = [],
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
      data: { label: words.bandTitle, hint: words.bandHint, width: layout.band.w, height: layout.band.h, variant: 'std' },
      zIndex: 0,
      ...base,
    })
  }
  if (layout.sharedBand) {
    nodes.push({
      id: 'band:shared',
      type: 'band',
      position: { x: layout.sharedBand.x, y: layout.sharedBand.y },
      data: {
        label: words.sharedBandTitle ?? '',
        hint: words.sharedBandHint ?? '',
        width: layout.sharedBand.w,
        height: layout.sharedBand.h,
        variant: 'shared',
      },
      zIndex: 0,
      ...base,
    })
  }
  if (layout.hubBand) {
    nodes.push({
      id: 'band:hubs',
      type: 'band',
      position: { x: layout.hubBand.x, y: layout.hubBand.y },
      data: { label: words.hubBandTitle, hint: words.hubBandHint, width: layout.hubBand.w, height: layout.hubBand.h, variant: 'hubs' },
      zIndex: 0,
      ...base,
    })
  }
  // 半径 22 以上は件数を丸の中に短く・それ未満は名前の下に「N 件」。title は常に正確な数。
  const circleTexts = (
    c: { count?: number; kindCount?: number; r: number },
    kind: CircleKind,
  ): Pick<CircleData, 'countText' | 'insideText' | 'fullText'> => {
    const inside = c.count != null && countInside(c.r)
    if (kind === 'dataset' && c.kindCount != null)
      return {
        countText: inside ? words.datasetCount(c.kindCount) : words.datasetCount(c.kindCount, c.count),
        insideText: inside ? words.compact(c.count!) : '',
        fullText: words.datasetCount(c.kindCount, c.count),
      }
    if (kind === 'shared')
      return {
        countText: c.count != null && !inside ? words.count(c.count) : '',
        insideText: inside ? words.compact(c.count!) : '',
        fullText: words.sharedTotal ? words.sharedTotal(c.count) : '',
      }
    if (c.count == null) return { countText: '', insideText: '', fullText: '' }
    return {
      countText: inside ? '' : words.count(c.count),
      insideText: inside ? words.compact(c.count) : '',
      fullText: words.count(c.count),
    }
  }
  const pickedSet = new Set(picked)
  const withOrphan = (t: Pick<CircleData, 'countText' | 'insideText' | 'fullText'>, orphan?: boolean) =>
    orphan && words.sharedOrphan
      ? { ...t, fullText: t.fullText ? `${t.fullText}・${words.sharedOrphan}` : words.sharedOrphan }
      : t
  const circle = (
    c: { id: string; label: string; count?: number; kindCount?: number; r: number; x: number; y: number; orphan?: boolean },
    kind: CircleKind,
  ) =>
    nodes.push({
      id: c.id,
      type: 'circle',
      position: { x: c.x - nodeWidth(c.r, kind) / 2, y: c.y - c.r },
      data: {
        label: c.label,
        ...withOrphan(circleTexts(c, kind), c.orphan),
        r: c.r,
        kind,
        ...(c.orphan ? { orphan: true } : {}),
      } satisfies CircleData,
      ...(pickedSet.has(c.id) ? { className: 'is-picked' } : {}),
      zIndex: 2,
      ...base,
    })
  for (const c of layout.circles) circle(c, layout.level === 'dataset' ? 'dataset' : 'kind')
  for (const h of layout.hubs) circle(h, 'hub')
  for (const sh of layout.shared ?? []) circle(sh, 'shared')
  for (const s of layout.stds) {
    nodes.push({
      id: s.id,
      type: 'std',
      position: { x: s.x, y: s.y },
      data: { label: s.label, vocab: s.vocab, width: s.w, height: s.h } satisfies StdData,
      ...(pickedSet.has(s.id) ? { className: 'is-picked' } : {}),
      zIndex: 1,
      ...base,
    })
  }
  // 'upper' の線の title「A ⊂ B」/「A ≡ B」用の名前。
  const nameOf = new Map<string, string>()
  for (const c of layout.circles) nameOf.set(c.id, c.label)
  for (const sh of layout.shared ?? []) nameOf.set(sh.id, sh.label)
  for (const s of layout.stds) nameOf.set(s.id, s.label)
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
      cx: e.cx,
      cy: e.cy,
      kind: e.kind,
      title:
        e.kinds != null
          ? words.participates(e.kinds)
          : e.kind === 'upper'
            ? `${nameOf.get(e.from) ?? e.from} ${e.both ? '≡' : relationSymbol(e.relation ?? 'subClassOf')} ${nameOf.get(e.to) ?? e.to}`
            : undefined,
    },
    className: edgeClassName(e.kind, e.from, e.to, null),
    markerEnd: e.kind === 'link' || e.kind === 'alignment' || e.kind === 'upper' ? arrow(e.kind) : undefined,
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
  pick,
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
  focus?: { label: string; onBack: () => void; hiddenDatasets?: number; hiddenHubs?: number }
  /** 線の起点に丸を選ぶ操作。有効のあいだ、押すと選択になり画面遷移・フォーカスはしない。 */
  pick?: VocabPick
  maxHeight?: number
  zoomable?: boolean
  /** 右上の「大きく見る」（重ね表示の中では出さない）。 */
  expandable?: boolean
}) {
  const { t, i18n } = useTranslation()
  const [big, setBig] = useState(false)
  const bigH = useBigViewHeight(big)
  // 強調する id は「どの図の」ものかも持つ。図が切り替わったら（押した丸の DOM が消えて
  // mouseleave が来なくても）古い id は無いものとして扱う。
  const [hover, setHover] = useState<{ layout: OverviewLayout; id: string } | null>(null)
  const active = hover && hover.layout === layout ? hover.id : null
  const pickedKey = (pick?.picked ?? []).join('|')
  const pickedList = useMemo(() => (pickedKey ? pickedKey.split('|') : []), [pickedKey])
  const { nodes, edges: baseEdges } = useMemo(
    () =>
      toFlow(layout, {
        count: (n) => t('vocab:overview.count', { n: n.toLocaleString('en-US') }),
        compact: (n) => compactCount(n, i18n.language),
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
        sharedBandTitle: t('vocabmap:sharedBand.title'),
        sharedBandHint: t('vocabmap:sharedBand.hint'),
        sharedTotal: (n) =>
          n != null
            ? t('vocabmap:sharedBand.total', { n: n.toLocaleString('en-US') })
            : t('vocabmap:sharedBand.totalNone'),
        sharedOrphan: t('vocabmap:sharedBand.orphan'),
      }, pickedList),
    [layout, t, i18n.language, pickedList],
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
      // 線を引く選択のあいだは、押すと丸を選ぶだけ（遷移・フォーカスはしない）。
      if (pick?.active) {
        const end = pickEndOfOverview(layout, node.id)
        if (end) pick.onPick(end)
        return
      }
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
    [layout, onOpenKind, onOpenDataset, onOpenCrosswalk, onFocus, pick],
  )

  const height = Math.min(maxHeight, Math.max(260, layout.height + 48))
  const onEnter = useCallback(
    (_: unknown, node: Node) => {
      if (node.type === 'circle') setHover({ layout, id: node.id })
    },
    [layout],
  )
  const onLeave = useCallback(() => setHover(null), [])
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
          {(focus.hiddenDatasets ?? 0) > 0 && (
            <span className="kind-ov-hidden">{t('vocab:overview.hiddenDatasets', { n: focus.hiddenDatasets })}</span>
          )}
          {(focus.hiddenHubs ?? 0) > 0 && (
            <span className="kind-ov-hidden">{t('vocab:overview.hiddenHubs', { n: focus.hiddenHubs })}</span>
          )}
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
  focus?: { label: string; onBack: () => void; hiddenDatasets?: number; hiddenHubs?: number }
  pick?: VocabPick
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
