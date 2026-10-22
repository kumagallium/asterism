// 全体の地図（丸＋横の文字）。sigma を素で作って壊す（NetworkView.tsx の NetworkCanvas と同じ流儀）。
// データと配置は mapModel.ts の純関数。契約 pr11h-contract.md §3。
import Sigma from 'sigma'
import { useEffect, useMemo, useRef } from 'react'
import type { DraftSky, SkyData, SkyDataset } from '../../sky/skyApi'
import type { PointSel } from '../chatState'
import { bindMapEvents, buildMapGraph, layoutMap, mapNeighborhood } from './mapModel'
import type { MapNodeAttrs } from './mapModel'

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
  activityThin: string
  entityThin: string
  accent: string
  edge: string
  edgeOn: string
  faded: string
  label: string
}

function readPalette(): Palette {
  const activity = cssColor('--activity', '#3b6fb6')
  const entity = cssColor('--entity', '#2f8f5b')
  const border = cssColor('--border-strong', '#c7d4c4')
  return {
    activity,
    entity,
    activityThin: withAlpha(activity, 0.55),
    entityThin: withAlpha(entity, 0.55),
    accent: cssColor('--accent', '#d6452c'),
    edge: border,
    edgeOn: cssColor('--fg', '#16241a'),
    faded: withAlpha(border, 0.6),
    label: cssColor('--fg', '#16241a'),
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
}: MapViewProps) {
  // グラフを作り直すのは sky／drafts／proposedDatasets／lang が変わったときだけ
  const graph = useMemo(() => {
    const g = buildMapGraph(sky, drafts, proposedDatasets, lang)
    layoutMap(g)
    return g
  }, [sky, drafts, proposedDatasets, lang])

  const hostRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const hovRef = useRef<string | null>(null)
  const propsRef = useRef({ focusIds, selectedId, highlightIds, removeIds, onSelect })
  useEffect(() => {
    propsRef.current = { focusIds, selectedId, highlightIds, removeIds, onSelect }
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host || typeof WebGLRenderingContext === 'undefined') return
    const pal = readPalette()
    // 濃く出す点の集まり（hover か選んだ点の近傍＋1 枚などで押した点とその近傍）。無ければ null＝全部ふつう
    const emphasis = (): { strong: Set<string>; active: string | null } | null => {
      const p = propsRef.current
      const active = hovRef.current ?? p.selectedId
      const hl = p.highlightIds && p.highlightIds.size > 0 ? p.highlightIds : null
      if (!active && !hl) return null
      const strong = mapNeighborhood(graph, active)
      if (hl) for (const id of hl) for (const n of mapNeighborhood(graph, id)) strong.add(n)
      return { strong, active }
    }
    let sigma: Sigma
    try {
      sigma = new Sigma(graph, host, {
        renderEdgeLabels: false,
        zIndex: true,
        labelSize: 12,
        labelFont: 'inherit',
        labelRenderedSizeThreshold: 6,
        labelDensity: 0.6,
        labelGridCellSize: 140,
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
          const base = a.role === 'entity' ? pal.entity : pal.activity
          const thin = a.role === 'entity' ? pal.entityThin : pal.activityThin
          let color = a.proposed ? thin : base
          if (isRemove) color = pal.accent
          const out: Record<string, unknown> = { ...data, type: 'circle', color, zIndex: isFocus ? 2 : 0 }
          if (isFocus) out.forceLabel = true
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
      // WebGL が使えない環境では静かに空にする
      return
    }
    bindMapEvents(
      sigma,
      graph,
      (sel, id) => propsRef.current.onSelect(sel, id),
      (id) => {
        hovRef.current = id
        sigma.refresh()
      },
    )
    sigmaRef.current = sigma
    return () => {
      sigma.kill()
      sigmaRef.current = null
      hovRef.current = null
    }
  }, [graph])

  useEffect(() => {
    sigmaRef.current?.refresh()
  }, [focusIds, selectedId, highlightIds, removeIds])

  // 高さが変わったら描き直す
  useEffect(() => {
    sigmaRef.current?.resize()
    sigmaRef.current?.refresh()
  }, [height])

  return (
    <div className="chat-map" data-testid="chat-map" style={{ height }}>
      <div ref={hostRef} className="chat-map-canvas" style={{ width: '100%', height: '100%' }} role="img" aria-label={ariaLabel} />
      <button
        type="button"
        className="chat-map-fit"
        data-testid="map-fit"
        onClick={() => {
          void sigmaRef.current?.getCamera().animatedReset()
        }}
      >
        {fitLabel}
      </button>
    </div>
  )
}
