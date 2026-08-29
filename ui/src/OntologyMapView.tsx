import { useEffect, useMemo, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge as Edge2,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Trans, useTranslation } from 'react-i18next'
import {
  type Alignment,
  type CrosswalkPerspective,
  getAlignments,
  getCrosswalks,
} from './crosswalkApi'
import { conceptName, crosswalkError, perspectiveDisplayName } from './crosswalkLabels'
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
// Dataset boxes carry a heading over their kind pills now, so they are a line taller
// (the node clips its overflow — a box that no longer fits hides the standards row).
const H_DS = 132
const H_HUB = 86
const H_EXT = 54
const GAP = 22
const TOP = 40 // room for lane headers

type Box = { x: number; y: number; w: number; h: number }
type DsBox = Box & { d: CatalogDataset }
type HubBox = Box & { p: CrosswalkPerspective; name: string; concepts: string }
type ExtBox = Box & { prefix: string; what: string }
type Edge = { fromId: string; toId: string; dsId?: string }

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
  /** How to say "unnamed connection" / "the value found in both" in the reader's
   * language — passed in so this stays a pure layout function. */
  words: { unnamed: string; sharedValue: string },
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
    name: perspectiveDisplayName(p) ?? words.unnamed,
    concepts: (p.config?.concepts ?? [])
      .map((c) => conceptName(c.name, c.concept_label) ?? words.sharedValue)
      .join(' · '),
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
        solid.push({ fromId: nodeId.ds(dn.d.id), toId: nodeId.hub(hn.p.perspective_id), dsId: dn.d.id })
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
        dotted.push({ fromId: nodeId.ds(dn.d.id), toId: nodeId.ext(r.prefix), dsId: dn.d.id })
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

/** 節の id。3 つのレーンで名前がぶつからないように前置きを付ける。 */
const nodeId = {
  ds: (id: string) => `ds:${id}`,
  hub: (id: string) => `hub:${id}`,
  ext: (prefix: string) => `ext:${prefix}`,
  lane: (key: string) => `lane:${key}`,
}

type MapData = {
  datasets: CatalogDataset[]
  perspectives: CrosswalkPerspective[]
  alignments: Alignment[]
}

/* ── 全体像の節（React Flow）─────────────────────────────────────────────────
   図はどこも同じ engine で描く（利用者の指示 2026-08-29）。並べかたは
   `buildLayout` のまま — 3 レーンの座標はこの画面の意味そのもので、自動整列に
   任せると以前の「毛玉」に戻る。React Flow に任せるのは**描画と操作**（拡大・
   移動・辺の経路）だけ。 */

type LaneData = { color: string; label: string; en: string; w: number }
type DsData = {
  d: CatalogDataset
  w: number
  h: number
  words: { open: string; kinds: string; std: string; selected: string }
  onOpen: () => void
  onHot: (on: boolean) => void
}
type HubData = { name: string; concepts: string; w: number; h: number; title: string; open: string; onOpen: () => void }
type ExtData = { prefix: string; what: string; w: number; h: number }

function LaneNode({ data }: NodeProps) {
  const d = data as LaneData
  return (
    <div className="ontomap-lane-head" style={{ width: d.w }}>
      <span className="ontomap-lane-dot" style={{ background: d.color }} />
      <span className="ontomap-lane-label">{d.label}</span>
      <span className="ontomap-lane-en">{d.en}</span>
    </div>
  )
}

function DsNode({ data }: NodeProps) {
  const n = data as DsData
  const std = [...new Set((n.d.reuses ?? []).map((r) => r.prefix))].slice(0, 3)
  return (
    <>
      <Handle type="source" position={Position.Right} isConnectable={false} />
      <button
        type="button"
        className="ontomap-node ontomap-node--ds"
        style={{ width: n.w, height: n.h }}
        title={n.words.open}
        onClick={n.onOpen}
        onMouseEnter={() => n.onHot(true)}
        onMouseLeave={() => n.onHot(false)}
        onFocus={() => n.onHot(true)}
        onBlur={() => n.onHot(false)}
      >
        <span className="ontomap-node-head">
          <span className="ontomap-node-chip ontomap-node-chip--ds">
            <DataIcon size={14} />
          </span>
          <span className="ontomap-node-name">{n.d.name}</span>
          {/* 触れているしるしは CSS で出す。React の state に持つと節の配列が
              作り直され、React Flow が節を採り直して辺が一瞬消える。 */}
          <span className="ontomap-node-badge">{n.words.selected}</span>
        </span>
        {n.d.classes.length > 0 && (
          <>
            {/* The pills are the design's own English class names. They stay
                as they are — they name what is in the data — but a heading
                says WHAT they are, so they do not read as stray tokens. */}
            <span className="ontomap-node-std-label">{n.words.kinds}</span>
            <span className="ontomap-node-pills">
              {n.d.classes.slice(0, 4).map((c) => (
                <span key={c} className="ontomap-pill">
                  {c}
                </span>
              ))}
            </span>
          </>
        )}
        {std.length > 0 && (
          <span className="ontomap-node-std">
            <span className="ontomap-node-std-label">{n.words.std}</span>
            {std.map((x) => (
              <span key={x} className="ontomap-node-std-tok">
                {x}
              </span>
            ))}
          </span>
        )}
      </button>
    </>
  )
}

function HubNode({ data }: NodeProps) {
  const n = data as HubData
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <button
        type="button"
        className="ontomap-node ontomap-node--hub"
        style={{ width: n.w, height: n.h, font: 'inherit', color: 'inherit' }}
        title={n.open}
        onClick={n.onOpen}
      >
        <span className="ontomap-node-head">
          <span className="ontomap-node-chip ontomap-node-chip--hub">
            <ConnectIcon size={14} />
          </span>
          <span className="ontomap-node-name" title={n.title}>
            {n.name}
          </span>
        </span>
        {n.concepts && <span className="ontomap-hub-key">{n.concepts}</span>}
      </button>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </>
  )
}

function ExtNode({ data }: NodeProps) {
  const n = data as ExtData
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="ontomap-node ontomap-node--ext" style={{ width: n.w, height: n.h }}>
        <span className="ontomap-ext-tok">{n.prefix}</span>
        <span className="ontomap-ext-what">{n.what}</span>
      </div>
    </>
  )
}

const OM_NODE_TYPES = { omLane: LaneNode, omDs: DsNode, omHub: HubNode, omExt: ExtNode }

export function OntologyMapView({
  onBack,
  onAddData,
  onCreateConnection,
  onOpenDataset,
  onOpenConnections,
}: {
  onBack?: () => void
  /** Where the empty states and the nodes send people. All default to the hash routes
   * the app already owns, so this view works wherever it is mounted. */
  onAddData?: () => void
  onCreateConnection?: () => void
  onOpenDataset?: (datasetId: string) => void
  /** Given a connection's id when a connection node was the thing pressed. */
  onOpenConnections?: (perspectiveId?: string) => void
}) {
  const { t } = useTranslation()
  const addData = onAddData ?? (() => (window.location.hash = '#/workbench'))
  const createConnection = onCreateConnection ?? (() => (window.location.hash = '#/crosswalk/new'))
  /* 節の組み立て（`useMemo`）が毎回作り直されないよう、既定の行き先も固定する。 */
  const openDataset = useMemo(
    () =>
      onOpenDataset ??
      ((id: string) => {
        window.location.hash = `#/datasets/${encodeURIComponent(id)}`
      }),
    [onOpenDataset],
  )
  const openConnections = useMemo(
    () =>
      onOpenConnections ??
      ((id?: string) => {
        window.location.hash = id ? `#/crosswalk/${encodeURIComponent(id)}` : '#/crosswalk'
      }),
    [onOpenConnections],
  )
  const [data, setData] = useState<MapData | null>(null)
  const [showExternal, setShowExternal] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [err, setErr] = useState('')
  // Bumped by "load again": a map whose first fetch failed must be recoverable
  // without a browser reload.
  const [reloads, setReloads] = useState(0)

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
  }, [reloads])

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

  const unnamed = t('crosswalk:view.unnamed')
  const sharedValue = t('crosswalk:create.sharedValueLabel')
  const layout = useMemo(
    () =>
      data
        ? buildLayout(data.datasets, data.perspectives, showExternal, { unnamed, sharedValue })
        : null,
    [data, showExternal, unnamed, sharedValue],
  )

  const empty = counts && counts.ds === 0 && counts.xw === 0

  /* レイアウトの結果を React Flow の形に写す。座標は `buildLayout` のまま —
     ここでやるのは「箱を節に、線を辺に」だけ。 */
  const flowNodes: Node[] = useMemo(() => {
    if (!layout) return []
    const out: Node[] = []
    const lane = (key: string, x: number, w: number, color: string, label: string, en: string) => {
      out.push({
        id: nodeId.lane(key),
        type: 'omLane',
        position: { x, y: 0 },
        data: { color, label, en, w } satisfies LaneData,
        draggable: false,
        selectable: false,
        connectable: false,
        zIndex: 0,
      })
    }
    lane('ds', LANE_DS.x, LANE_DS.w, 'var(--entity)', t('map:lane.datasets'), t('map:lane.datasetsEn'))
    lane('hub', LANE_HUB.x, LANE_HUB.w, 'var(--link)', t('map:lane.bridges'), t('map:lane.bridgesEn'))
    if (layout.ext.length > 0) {
      lane('ext', LANE_EXT.x, LANE_EXT.w, 'var(--faint)', t('map:lane.external'), t('map:lane.externalEn'))
    }
    for (const n of layout.ds) {
      out.push({
        id: nodeId.ds(n.d.id),
        type: 'omDs',
        position: { x: n.x, y: n.y },
        data: {
          d: n.d,
          w: n.w,
          h: n.h,
          words: {
            open: t('map:node.openDs', { name: n.d.name }),
            kinds: t('map:node.kindsHead'),
            std: t('map:node.connectStd'),
            selected: t('map:line.selected'),
          },
          onOpen: () => openDataset(n.d.id),
          onHot: (on: boolean) =>
            setSelected((cur) => (on ? n.d.id : cur === n.d.id ? null : cur)),
        } satisfies DsData,
        draggable: false,
        selectable: false,
        connectable: false,
      })
    }
    for (const n of layout.hubs) {
      out.push({
        id: nodeId.hub(n.p.perspective_id),
        type: 'omHub',
        position: { x: n.x, y: n.y },
        data: {
          name: n.name,
          concepts: n.concepts ? t('map:node.crossBy', { key: n.concepts }) : '',
          w: n.w,
          h: n.h,
          title: n.p.perspective_id,
          open: t('map:node.openHub'),
          onOpen: () => openConnections(n.p.perspective_id),
        } satisfies HubData,
        draggable: false,
        selectable: false,
        connectable: false,
      })
    }
    for (const n of layout.ext) {
      out.push({
        id: nodeId.ext(n.prefix),
        type: 'omExt',
        position: { x: n.x, y: n.y },
        data: { prefix: n.prefix, what: t(n.what), w: n.w, h: n.h } satisfies ExtData,
        draggable: false,
        selectable: false,
        connectable: false,
      })
    }
    return out
    // ⭐`selected` を deps に入れない。触れるたびに節の配列を作り直すと、React Flow
    // が節を採り直して**辺が 1 フレーム消える**（＝チカチカする）。強調は辺だけの話。
  }, [layout, t, openDataset, openConnections])

  const flowEdges: Edge2[] = useMemo(() => {
    if (!layout) return []
    const mk = (e: Edge, i: number, kind: 'solid' | 'dotted'): Edge2 => {
      const on = selected != null && e.dsId === selected
      return {
        id: `${kind}-${e.fromId}-${e.toId}-${i}`,
        source: e.fromId,
        target: e.toId,
        className: `ontomap-edge ontomap-edge--${kind}${on ? ' is-on' : ''}${
          selected && !on ? ' is-dim' : ''
        }`,
        /* 矢じりの色は class では届かない（定義が共有される）ので、ここで渡す。 */
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: kind === 'solid' ? 'var(--link)' : 'var(--faint)',
        },
      }
    }
    return [
      ...layout.dotted.map((e, i) => mk(e, i, 'dotted')),
      ...layout.solid.map((e, i) => mk(e, i, 'solid')),
    ]
  }, [layout, selected])

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

      {/* Same treatment as the connections screen: what happened, what to do, and the
          raw HTTP/JSON still reachable for whoever needs it. */}
      {err && (
        <div className="state-block">
          <p className="state-title">{t('map:loadErr.title')}</p>
          <p className="state-sub">{t(crosswalkError(err).body)}</p>
          <div className="kz-actions">
            <button
              type="button"
              onClick={() => {
                setErr('')
                setData(null)
                setReloads((n) => n + 1)
              }}
            >
              {t('crosswalk:view.retryBtn')}
            </button>
          </div>
          <details className="kz-stop-detail">
            <summary>{t('crosswalk:create.details')}</summary>
            <pre className="error">{err}</pre>
          </details>
        </div>
      )}
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
          <div className="kz-actions">
            <button type="button" onClick={addData}>
              {t('map:empty.addBtn')}
            </button>
          </div>
        </div>
      )}

      {/* Data but no connections — the state the first-time reader actually lands in.
          The middle lane would otherwise be a headline over nothing. */}
      {layout && !empty && layout.hubs.length === 0 && (
        <div className="state-block">
          <p className="state-title">{t('map:noHubs.title')}</p>
          <p className="state-sub">{t('map:noHubs.sub')}</p>
          <div className="kz-actions">
            <button type="button" onClick={createConnection}>
              {t('map:noHubs.btn')}
            </button>
          </div>
        </div>
      )}

      {layout && !empty && (
        <div className="ontomap-card">
          <div className="ontomap-flow" style={{ height: Math.min(layout.height + 48, 760) }}>
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={OM_NODE_TYPES}
              fitView
              fitViewOptions={{ padding: 0.06, maxZoom: 1 }}
              minZoom={0.3}
              maxZoom={1.5}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              zoomOnScroll={false}
              zoomOnDoubleClick={false}
              preventScrolling={false}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
            </ReactFlow>
          </div>
        </div>
      )}
    </div>
  )
}
