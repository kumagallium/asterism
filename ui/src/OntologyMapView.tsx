import { useEffect, useMemo, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  type Alignment,
  type CrosswalkPerspective,
  getAlignments,
  getCrosswalks,
} from './crosswalkApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { ArrowIcon, ConnectIcon, DataIcon, LayersIcon } from './icons'

/**
 * 全体像 (overview): a readable, left→right THREE-LANE map of what data lives in
 * Asterism and how it connects — replacing the old auto-laid-out "毛玉" mermaid
 * graph (design_handoff v2, ScreenMap). Lanes: datasets (left, green) → crosswalk
 * bridges (center, blue) → external standards (right, grey). Edges: dataset→bridge
 * solid blue ("connect"), dataset→standard dashed grey ("reuse"). Derived from the
 * live catalog + crosswalk APIs — no new endpoint. Per-dataset class diagrams live
 * in the dataset detail; this view is the high-level connectivity.
 */

// Fixed coordinate canvas (scrolls if it grows). Three lanes at fixed x; nodes are
// distributed vertically within each lane.
const LANE_DS = { x: 0, w: 250 }
const LANE_HUB = { x: 372, w: 224 }
const LANE_EXT = { x: 744, w: 200 }
const H_DS = 116
const H_HUB = 86
const H_EXT = 54
const GAP = 22
const TOP = 40 // room for lane headers

type Box = { x: number; y: number; w: number; h: number }
type DsBox = Box & { d: CatalogDataset }
type HubBox = Box & { p: CrosswalkPerspective; name: string; concepts: string }
type ExtBox = Box & { prefix: string; what: string }
type Edge = { from: Box; to: Box; dsId?: string }

type Layout = {
  ds: DsBox[]
  hubs: HubBox[]
  ext: ExtBox[]
  solid: Edge[]
  dotted: Edge[]
  width: number
  height: number
}

function buildLayout(
  datasets: CatalogDataset[],
  perspectives: CrosswalkPerspective[],
  showExternal: boolean,
): Layout {
  const dsList = datasets.filter((d) => !d.isCrosswalk)

  // Datasets — left lane.
  const ds: DsBox[] = dsList.map((d, i) => ({
    d,
    x: LANE_DS.x,
    y: TOP + i * (H_DS + GAP),
    w: LANE_DS.w,
    h: H_DS,
  }))
  const dsById = new Map<string, DsBox>()
  for (const n of ds) {
    dsById.set(n.d.id, n)
    if (n.d.live?.meta.id) dsById.set(n.d.live.meta.id, n)
  }

  // Crosswalk bridges — center lane.
  const hubs: HubBox[] = perspectives.map((p, i) => ({
    p,
    name: p.dataset?.name || p.perspective_id,
    concepts: (p.config?.concepts ?? []).map((c) => c.name).join(' · '),
    x: LANE_HUB.x,
    y: TOP + i * (H_HUB + GAP),
    w: LANE_HUB.w,
    h: H_HUB,
  }))

  // External standards — right lane (unique reuse prefixes across datasets).
  const extMap = new Map<string, { prefix: string; what: string }>()
  if (showExternal) {
    for (const d of dsList) {
      for (const r of d.reuses ?? []) {
        if (!extMap.has(r.prefix)) extMap.set(r.prefix, r)
      }
    }
  }
  const ext: ExtBox[] = [...extMap.values()].map((r, i) => ({
    ...r,
    x: LANE_EXT.x,
    y: TOP + i * (H_EXT + GAP),
    w: LANE_EXT.w,
    h: H_EXT,
  }))
  const extByPrefix = new Map(ext.map((n) => [n.prefix, n]))

  // Edges. dataset → bridge (solid). dataset → standard (dotted, "reuse").
  const solidSeen = new Set<string>()
  const solid: Edge[] = []
  for (const hn of hubs) {
    for (const c of hn.p.config?.concepts ?? []) {
      for (const part of c.participants) {
        const dn = dsById.get(part.dataset_id)
        if (!dn) continue
        const key = `${dn.d.id}->${hn.p.perspective_id}`
        if (solidSeen.has(key)) continue
        solidSeen.add(key)
        solid.push({ from: dn, to: hn, dsId: dn.d.id })
      }
    }
  }
  const dotted: Edge[] = []
  if (showExternal) {
    const seen = new Set<string>()
    for (const dn of ds) {
      for (const r of dn.d.reuses ?? []) {
        const en = extByPrefix.get(r.prefix)
        if (!en) continue
        const key = `${dn.d.id}->${r.prefix}`
        if (seen.has(key)) continue
        seen.add(key)
        dotted.push({ from: dn, to: en, dsId: dn.d.id })
      }
    }
  }

  const laneCounts = [
    ds.length * (H_DS + GAP),
    hubs.length * (H_HUB + GAP),
    ext.length * (H_EXT + GAP),
    H_DS + GAP,
  ]
  const height = TOP + Math.max(...laneCounts) + 8
  return { ds, hubs, ext, solid, dotted, width: LANE_EXT.x + LANE_EXT.w, height }
}

// Cubic bezier from a box's right edge to another box's left edge.
function edgePath(e: Edge): string {
  const x1 = e.from.x + e.from.w
  const y1 = e.from.y + e.from.h / 2
  const x2 = e.to.x
  const y2 = e.to.y + e.to.h / 2
  const dx = Math.max(40, (x2 - x1) * 0.5)
  return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`
}

type MapData = {
  datasets: CatalogDataset[]
  perspectives: CrosswalkPerspective[]
  alignments: Alignment[]
}

function LaneHead({
  x,
  w,
  color,
  label,
  en,
}: {
  x: number
  w: number
  color: string
  label: string
  en: string
}) {
  return (
    <div className="ontomap-lane-head" style={{ left: x, width: w }}>
      <span className="ontomap-lane-dot" style={{ background: color }} />
      <span className="ontomap-lane-label">{label}</span>
      <span className="ontomap-lane-en">{en}</span>
    </div>
  )
}

export function OntologyMapView({ onBack }: { onBack?: () => void }) {
  const { t } = useTranslation()
  const [data, setData] = useState<MapData | null>(null)
  const [showExternal, setShowExternal] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let off = false
    Promise.all([getCatalogDatasets(), getCrosswalks(), getAlignments()])
      .then(([datasets, perspectives, al]) => {
        if (off) return
        setData({ datasets, perspectives, alignments: al.alignments })
      })
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  const counts = data
    ? {
        ds: data.datasets.filter((d) => !d.isCrosswalk).length,
        xw: data.perspectives.length,
        al: data.alignments.length,
        ext: new Set(
          data.datasets
            .filter((d) => !d.isCrosswalk)
            .flatMap((d) => (d.reuses ?? []).map((r) => r.prefix)),
        ).size,
      }
    : null

  const layout = useMemo(
    () => (data ? buildLayout(data.datasets, data.perspectives, showExternal) : null),
    [data, showExternal],
  )

  const empty = counts && counts.ds === 0 && counts.xw === 0

  return (
    <div className="ontomap-view">
      {onBack && (
        <button type="button" className="vocab-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> {t('map:back')}
        </button>
      )}

      <div className="vocab-banner">
        <span className="vocab-banner-icon">
          <LayersIcon size={22} />
        </span>
        <div>
          <h2 className="vocab-banner-title">{t('map:title')}</h2>
          <p className="vocab-banner-sub">
            <Trans i18nKey="map:bannerSub" components={[<strong />, <strong />]} />
          </p>
        </div>
      </div>

      {counts && (
        <div className="ontomap-legend">
          <span className="ontomap-chip ontomap-chip--ds">
            {t('map:legend.datasets', { n: counts.ds })}
          </span>
          <span className="ontomap-chip ontomap-chip--xw">
            {t('map:legend.crosswalks', { n: counts.xw })}
          </span>
          {counts.al > 0 && (
            <span className="ontomap-chip">{t('map:legend.alignments', { n: counts.al })}</span>
          )}
          {showExternal && counts.ext > 0 && (
            <span className="ontomap-chip ontomap-chip--ext">
              {t('map:legend.external', { n: counts.ext })}
            </span>
          )}
          <span className="ontomap-legend-keys">
            <span className="ontomap-key">
              <span className="ontomap-key-line ontomap-key-line--solid" /> {t('map:line.connect')}
            </span>
            <span className="ontomap-key">
              <span className="ontomap-key-line ontomap-key-line--dotted" /> {t('map:line.reuse')}
            </span>
            <span className="ontomap-key">
              <span className="ontomap-key-box" /> {t('map:line.selected')}
            </span>
          </span>
          <label className="ontomap-toggle">
            <input
              type="checkbox"
              checked={showExternal}
              onChange={(e) => setShowExternal(e.target.checked)}
            />
            {t('map:showExternal')}
          </label>
        </div>
      )}

      {err && <pre className="error">{err}</pre>}
      {!layout && !err && (
        <p className="loading-row">
          <span className="spinner" />
          {t('map:loading')}
        </p>
      )}
      {empty && (
        <div className="state-block">
          <p className="state-title">{t('map:empty.title')}</p>
          <p className="state-sub">{t('map:empty.sub')}</p>
        </div>
      )}

      {layout && !empty && (
        <div className="ontomap-card">
          <div
            className="ontomap-canvas"
            style={{ width: layout.width, height: layout.height }}
          >
            <LaneHead
              x={LANE_DS.x}
              w={LANE_DS.w}
              color="var(--entity)"
              label={t('map:lane.datasets')}
              en={t('map:lane.datasetsEn')}
            />
            <LaneHead
              x={LANE_HUB.x}
              w={LANE_HUB.w}
              color="var(--link)"
              label={t('map:lane.bridges')}
              en={t('map:lane.bridgesEn')}
            />
            {layout.ext.length > 0 && (
              <LaneHead
                x={LANE_EXT.x}
                w={LANE_EXT.w}
                color="var(--faint)"
                label={t('map:lane.external')}
                en={t('map:lane.externalEn')}
              />
            )}

            {/* edges */}
            <svg
              className="ontomap-edges"
              width={layout.width}
              height={layout.height}
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="om-arrow"
                  markerWidth="8"
                  markerHeight="8"
                  refX="6"
                  refY="4"
                  orient="auto"
                >
                  <path d="M1,1 L6,4 L1,7" fill="none" stroke="var(--link)" strokeWidth="1.6" />
                </marker>
                <marker
                  id="om-arrow-faint"
                  markerWidth="8"
                  markerHeight="8"
                  refX="6"
                  refY="4"
                  orient="auto"
                >
                  <path d="M1,1 L6,4 L1,7" fill="none" stroke="var(--faint)" strokeWidth="1.4" />
                </marker>
              </defs>
              {layout.dotted.map((e, i) => (
                <path
                  key={`d${i}`}
                  d={edgePath(e)}
                  className="ontomap-edge ontomap-edge--dotted"
                  markerEnd="url(#om-arrow-faint)"
                  opacity={selected && e.dsId !== selected ? 0.25 : 0.8}
                />
              ))}
              {layout.solid.map((e, i) => {
                const on = selected != null && e.dsId === selected
                return (
                  <path
                    key={`s${i}`}
                    d={edgePath(e)}
                    className={`ontomap-edge ontomap-edge--solid${on ? ' is-on' : ''}`}
                    markerEnd="url(#om-arrow)"
                    opacity={selected && !on ? 0.3 : 1}
                  />
                )
              })}
            </svg>

            {/* dataset nodes */}
            {layout.ds.map((n) => {
              const on = selected === n.d.id
              const std = [...new Set((n.d.reuses ?? []).map((r) => r.prefix))].slice(0, 3)
              return (
                <button
                  type="button"
                  key={n.d.id}
                  className={`ontomap-node ontomap-node--ds${on ? ' is-selected' : ''}`}
                  style={{ left: n.x, top: n.y, width: n.w, height: n.h }}
                  onClick={() => setSelected(on ? null : n.d.id)}
                >
                  <span className="ontomap-node-head">
                    <span className="ontomap-node-chip ontomap-node-chip--ds">
                      <DataIcon size={14} />
                    </span>
                    <span className="ontomap-node-name">{n.d.name}</span>
                    {on && <span className="ontomap-node-badge">{t('map:line.selected')}</span>}
                  </span>
                  {n.d.classes.length > 0 && (
                    <span className="ontomap-node-pills">
                      {n.d.classes.slice(0, 4).map((c) => (
                        <span key={c} className="ontomap-pill">
                          {c}
                        </span>
                      ))}
                    </span>
                  )}
                  {std.length > 0 && (
                    <span className="ontomap-node-std">
                      <span className="ontomap-node-std-label">{t('map:node.connectStd')}</span>
                      {std.map((s) => (
                        <span key={s} className="ontomap-node-std-tok">
                          {s}
                        </span>
                      ))}
                    </span>
                  )}
                </button>
              )
            })}

            {/* crosswalk hubs */}
            {layout.hubs.map((n) => (
              <div
                key={n.p.perspective_id}
                className="ontomap-node ontomap-node--hub"
                style={{ left: n.x, top: n.y, width: n.w, height: n.h }}
              >
                <span className="ontomap-node-head">
                  <span className="ontomap-node-chip ontomap-node-chip--hub">
                    <ConnectIcon size={14} />
                  </span>
                  <span className="ontomap-node-name">{n.name}</span>
                </span>
                {n.concepts && (
                  <span className="ontomap-hub-key">{t('map:node.crossBy', { key: n.concepts })}</span>
                )}
              </div>
            ))}

            {/* external standards */}
            {layout.ext.map((n) => (
              <div
                key={n.prefix}
                className="ontomap-node ontomap-node--ext"
                style={{ left: n.x, top: n.y, width: n.w, height: n.h }}
              >
                <span className="ontomap-ext-tok">{n.prefix}</span>
                <span className="ontomap-ext-what">{n.what}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
