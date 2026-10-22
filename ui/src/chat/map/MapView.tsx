// 全体の地図（丸＋横の文字）。sigma を素で作って壊す（NetworkView.tsx の NetworkCanvas と同じ流儀）。
// データと配置は mapModel.ts の純関数。契約 pr11h-contract.md §3。
import Sigma from 'sigma'
import type Graph from 'graphology'
import { useEffect, useMemo, useRef } from 'react'
import type { DraftSky, SkyData, SkyDataset } from '../../sky/skyApi'
import type { PointSel } from '../chatState'
import { bindMapEvents, buildMapGraph, layoutMap, mapNeighborhood, mapSignature, safeResize, syncMapAttributes } from './mapModel'
import type { MapNodeAttrs } from './mapModel'

/** 全体を見るときのカメラの引き（1 より大きいと余白が出て、端の名前が切れない）。 */
const FIT_RATIO = 1.15

export interface MapViewProps {
  sky: SkyData
  drafts: readonly DraftSky[]
  proposedDatasets: readonly SkyDataset[]
  lang: 'ja' | 'en'
  height: number
  /** この話の点（濃く・名前を必ず出す）。 */
  focusIds: ReadonlySet<string>
  selectedId: string | null
  /** 1 枚・表・出典で押したもの（11d の highlight）。null なら無し。 */
  highlightIds: ReadonlySet<string> | null
  /** 外す提案の用語の id。 */
  removeIds: ReadonlySet<string>
  onSelect: (sel: PointSel | null, nodeId: string | null) => void
  ariaLabel: string
  fitLabel: string
  /** 点が 1 つも無いときの 1 行。 */
  emptyLabel?: string
}

/** CSS 変数を実際の色に解決する（sigma は CSS 変数を読めない）。 */
function cssColor(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

/** #rgb／#rrggbb に透明度を掛ける。ほかの書き方はそのまま返す。 */
function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color)
  if (!m) return color
  const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1]
  const n = parseInt(h, 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

interface Palette {
  activity: string
  entity: string
  dataset: string
  activityThin: string
  entityThin: string
  accent: string
  edge: string
  edgeOn: string
  faded: string
  label: string
  /** 線の名前の札の地と枠。 */
  surface: string
  border: string
}

type EdgeLabelCtx = CanvasRenderingContext2D
type EdgeLabelData = { label?: string | null; size?: number }
type EdgeLabelEnd = { x: number; y: number; size: number }
type EdgeLabelSettings = { edgeLabelSize: number; edgeLabelFont: string; edgeLabelWeight: string }

/** 線の名前を、線の長さに関わらず中点に横書きで出す（小さな札つき）。sigma の既定（線に沿って回し、線より長ければ「…」）は、
 * 短い線で名前が出ないので使わない。hover 中の 1 本にだけ label が残るので、重なりは考えない。 */
function makeEdgeLabelDrawer(pal: Palette) {
  return (context: EdgeLabelCtx, edge: EdgeLabelData, source: EdgeLabelEnd, target: EdgeLabelEnd, settings: EdgeLabelSettings): void => {
    const label = edge.label
    if (!label) return
    const px = settings.edgeLabelSize
    context.font = `${settings.edgeLabelWeight} ${px}px ${settings.edgeLabelFont}`
    const w = context.measureText(label).width + 10
    const h = px + 6
    const cx = (source.x + target.x) / 2
    const cy = (source.y + target.y) / 2
    const x = cx - w / 2
    const y = cy - h / 2
    const r = 4
    context.beginPath()
    context.moveTo(x + r, y)
    context.lineTo(x + w - r, y)
    context.quadraticCurveTo(x + w, y, x + w, y + r)
    context.lineTo(x + w, y + h - r)
    context.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
    context.lineTo(x + r, y + h)
    context.quadraticCurveTo(x, y + h, x, y + h - r)
    context.lineTo(x, y + r)
    context.quadraticCurveTo(x, y, x + r, y)
    context.closePath()
    context.fillStyle = pal.surface
    context.fill()
    context.strokeStyle = pal.border
    context.lineWidth = 1
    context.stroke()
    context.fillStyle = pal.label
    context.textBaseline = 'middle'
    context.textAlign = 'center'
    context.fillText(label, cx, cy + 0.5)
    context.textAlign = 'start'
    context.textBaseline = 'alphabetic'
  }
}

function readPalette(): Palette {
  const activity = cssColor('--activity', '#3b6fb6')
  const entity = cssColor('--entity', '#2f8f5b')
  const dataset = cssColor('--muted', '#54695b')
  const border = cssColor('--border-strong', '#c7d4c4')
  return {
    activity,
    entity,
    dataset,
    activityThin: withAlpha(activity, 0.55),
    entityThin: withAlpha(entity, 0.55),
    accent: cssColor('--accent', '#d6452c'),
    edge: border,
    edgeOn: cssColor('--fg', '#16241a'),
    faded: withAlpha(cssColor('--border-strong', '#c7d4c4'), 0.9),
    label: cssColor('--fg', '#16241a'),
    surface: cssColor('--surface', '#ffffff'),
    border,
  }
}

export function MapView({
  sky,
  drafts,
  proposedDatasets,
  lang,
  height,
  focusIds,
  selectedId,
  highlightIds,
  removeIds,
  onSelect,
  ariaLabel,
  fitLabel,
  emptyLabel,
}: MapViewProps) {
  // グラフは sky／drafts／proposedDatasets／lang が変わるたびに作る（速い）。形（点の id と線）が前と同じなら、
  // 配置済みのグラフと sigma を使い回して属性（色・大きさ・薄さ）だけ写す（配置は遅い・作り直すとカメラが戻る）
  const fresh = useMemo(() => buildMapGraph(sky, drafts, proposedDatasets, lang), [sky, drafts, proposedDatasets, lang])

  const hostRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const refreshRef = useRef<(() => void) | null>(null)
  const hovRef = useRef<string | null>(null)
  const hovEdgeRef = useRef<string | null>(null)
  const laidRef = useRef<{ sig: string; graph: Graph } | null>(null)
  const roRef = useRef<ResizeObserver | null>(null)
  const propsRef = useRef({ focusIds, selectedId, highlightIds, removeIds, onSelect })
  useEffect(() => {
    propsRef.current = { focusIds, selectedId, highlightIds, removeIds, onSelect }
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host || typeof WebGLRenderingContext === 'undefined') return
    const sig = mapSignature(fresh)
    const laid = laidRef.current
    if (laid && laid.sig === sig && sigmaRef.current) {
      // 形が同じ: 属性だけ写して描き直す（sigma はそのまま。カメラも動かない）
      syncMapAttributes(laid.graph, fresh)
      refreshRef.current?.()
      return
    }
    // 形が変わった: 前の sigma を壊し、配置して作り直す
    roRef.current?.disconnect()
    roRef.current = null
    sigmaRef.current?.kill()
    sigmaRef.current = null
    refreshRef.current = null
    hovRef.current = null
    hovEdgeRef.current = null
    layoutMap(fresh)
    laidRef.current = { sig, graph: fresh }
    const graph = fresh
    const pal = readPalette()
    // 濃く出す点の集まり（hover か選んだ点の近傍＋1 枚などで押した点とその近傍）。無ければ null＝全部ふつう。
    // reducer の呼び出しごとに作り直さず、refresh の前に 1 回だけ作る
    const computeEmphasis = (): { strong: Set<string>; active: string | null } | null => {
      const p = propsRef.current
      // 地図に無い点（形が変わって消えた提案の札など）を選んだままなら、強調は無しとして扱う
      const want = hovRef.current ?? p.selectedId
      const active = want && graph.hasNode(want) ? want : null
      const hl = p.highlightIds && p.highlightIds.size > 0 ? p.highlightIds : null
      if (!active && !hl) return null
      const strong = mapNeighborhood(graph, active)
      if (hl) for (const id of hl) for (const n of mapNeighborhood(graph, id)) strong.add(n)
      return { strong, active }
    }
    let em = computeEmphasis()
    const emphasis = () => em
    const refresh = (): void => {
      em = computeEmphasis()
      sigma.refresh()
    }
    let sigma: Sigma
    try {
      sigma = new Sigma(graph, host, {
        // 隠れた容器（375 のタブ・データを添える欄の間）でも例外にしない。見えたら resize する
        allowInvalidContainer: true,
        enableEdgeEvents: true,
        // 線の名前は hover 中の 1 本だけ出す（edgeReducer で他は ''）
        renderEdgeLabels: true,
        edgeLabelSize: 11,
        edgeLabelColor: { color: pal.label },
        edgeLabelFont: 'inherit',
        defaultDrawEdgeLabel: makeEdgeLabelDrawer(pal),
        zIndex: true,
        labelSize: 12,
        labelFont: 'inherit',
        labelRenderedSizeThreshold: 2,
        labelDensity: 1,
        labelGridCellSize: 100,
        labelColor: { color: pal.label },
        defaultEdgeColor: pal.edge,
        minCameraRatio: 0.02,
        maxCameraRatio: 8,
        nodeReducer: (id, data) => {
          const a = data as unknown as MapNodeAttrs & Record<string, unknown>
          const p = propsRef.current
          const em = emphasis()
          const isFocus = p.focusIds.has(id)
          const isRemove = p.removeIds.has(id) || a.remove === true
          const base = a.role === 'entity' ? pal.entity : a.role === 'dataset' ? pal.dataset : pal.activity
          const thin = a.role === 'entity' ? pal.entityThin : a.role === 'dataset' ? withAlpha(pal.dataset, 0.55) : pal.activityThin
          let color = a.proposed ? thin : base
          if (isRemove) color = pal.accent
          // この話の点は前に出す（名前は重なりの規則に任せる。必ず出すのは選んだ点と hover の近傍だけ。
          // この話の名前は下の「この話の周り」で全部読める）
          const out: Record<string, unknown> = { ...data, type: 'circle', color, zIndex: isFocus ? 2 : 0 }
          // データセットの丸（表の名前）は星座の見出し。名前を必ず出す
          if (a.type === 'dataset') out.forceLabel = true
          if (em) {
            const isStrong = em.strong.has(id)
            const isActive = id === em.active || (p.highlightIds?.has(id) ?? false)
            if (isStrong || isFocus) {
              out.zIndex = isActive ? 3 : isFocus ? 2 : 1
              if (isStrong) out.forceLabel = true
              if (isActive) out.highlighted = true
            } else {
              out.color = isRemove ? pal.accent : pal.faded
              out.label = null
              out.forceLabel = false
              out.zIndex = 0
            }
          }
          return out
        },
        edgeReducer: (edge, data) => {
          // 線の名前は hover 中の線だけ。その線は濃く少し太く
          const hovered = edge === hovEdgeRef.current
          // forceLabel: sigma は両端の名前が出ている線しか名前を描かない。hover 中の線は必ず描く
          if (hovered) return { ...data, color: pal.edgeOn, size: ((data.size as number | undefined) ?? 1) + 1, zIndex: 2, forceLabel: true }
          data = { ...data, label: '' }
          const em = emphasis()
          if (!em) return data
          const [s, t] = graph.extremities(edge)
          const p = propsRef.current
          const on =
            (em.active !== null && (s === em.active || t === em.active)) ||
            (p.highlightIds !== null && (p.highlightIds.has(s) || p.highlightIds.has(t)))
          return on ? { ...data, color: pal.edgeOn, zIndex: 1 } : { ...data, color: pal.faded, zIndex: 0 }
        },
      })
    } catch {
      // WebGL が使えない環境（作れない GPU・ブロックリスト）では静かに空にする。refresh の先は無い
      refreshRef.current = null
      laidRef.current = null
      return
    }
    refreshRef.current = refresh
    // 端の点の名前が切れないよう、少し引いて始める（全体を見るも同じ）
    sigma.getCamera().setState({ x: 0.5, y: 0.5, ratio: FIT_RATIO })
    // 撮影と検証の道具のために、sigma を容器から引けるようにしておく（製品の動きには使わない）
    ;(host as HTMLDivElement & { sigma?: Sigma }).sigma = sigma
    bindMapEvents(
      sigma,
      graph,
      (sel, id) => propsRef.current.onSelect(sel, id),
      (id) => {
        hovRef.current = id
        refresh()
      },
    )
    sigma.on('enterEdge', ({ edge }) => {
      hovEdgeRef.current = edge
      refresh()
    })
    sigma.on('leaveEdge', () => {
      hovEdgeRef.current = null
      refresh()
    })
    sigmaRef.current = sigma
    // 容器が見えた（幅と高さが 0 でなくなった）・大きさが変わったときに測り直す
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(() => {
        const r = host.getBoundingClientRect()
        safeResize(sigma, r.width, r.height)
      })
      ro.observe(host)
      roRef.current = ro
    }
  }, [fresh])

  // 外すときだけ壊す
  useEffect(
    () => () => {
      roRef.current?.disconnect()
      roRef.current = null
      sigmaRef.current?.kill()
      sigmaRef.current = null
      refreshRef.current = null
      if (hostRef.current) delete (hostRef.current as HTMLDivElement & { sigma?: Sigma }).sigma
    },
    [],
  )

  useEffect(() => {
    refreshRef.current?.()
  }, [focusIds, selectedId, highlightIds, removeIds])

  // 高さが変わったら描き直す（隠れているときは何もしない）
  useEffect(() => {
    const host = hostRef.current
    const sigma = sigmaRef.current
    if (!host || !sigma) return
    const r = host.getBoundingClientRect()
    safeResize(sigma, r.width, r.height)
  }, [height])

  return (
    <div className="chat-map" data-testid="chat-map" style={{ height }}>
      <div ref={hostRef} className="chat-map-canvas" style={{ width: '100%', height: '100%' }} role="img" aria-label={ariaLabel} />
      {fresh.order === 0 && emptyLabel && (
        <p className="chat-map-empty" data-testid="map-empty">
          {emptyLabel}
        </p>
      )}
      <button
        type="button"
        className="chat-map-fit"
        data-testid="map-fit"
        onClick={() => {
          void sigmaRef.current?.getCamera().animate({ x: 0.5, y: 0.5, ratio: FIT_RATIO, angle: 0 }, { duration: 300 })
        }}
      >
        {fitLabel}
      </button>
    </div>
  )
}
