import { useEffect, useMemo, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  type Alignment,
  type CrosswalkPerspective,
  getAlignments,
  getCrosswalks,
} from './crosswalkApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { ArrowIcon, LayersIcon } from './icons'
import { Mermaid } from './Mermaid'

/**
 * Ontology map (俯瞰): a single bird's-eye diagram of what ontologies live in Asterism
 * and how they connect. Each DATASET has its own ontology (its classes); each CROSSWALK
 * PERSPECTIVE is a thin bridge ontology that joins datasets on a shared concept; and
 * ALIGNMENTS relate two perspectives' terms (crosswalk-multi-perspective.md). The graph
 * is derived from the live catalog + crosswalk APIs — no new endpoint. Per-dataset class
 * diagrams live in the catalog; this view is the high-level connectivity.
 */

/** A mermaid-safe node id from an arbitrary registry id. */
function nodeId(prefix: string, raw: string): string {
  return prefix + raw.replace(/[^a-zA-Z0-9]/g, '_')
}

/** Escape a label fragment for a mermaid quoted node label (no quotes / angle brackets,
 * bounded length — the diagram is an overview, not a data dump). */
function esc(s: string): string {
  return s
    .replace(/["<>]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 48)
}

function buildChart(
  datasets: CatalogDataset[],
  perspectives: CrosswalkPerspective[],
  alignments: Alignment[],
  t: (k: string) => string,
  showExternal: boolean,
): string {
  const dsList = datasets.filter((d) => !d.isCrosswalk)
  // dataset_id (both catalog id and live id) -> the dataset's mermaid node id.
  const dsNode = new Map<string, string>()
  const dsLines: string[] = []
  const dsIds: string[] = []
  for (const d of dsList) {
    const id = nodeId('DS_', d.id)
    dsIds.push(id)
    const classes = d.classes.slice(0, 4).join(' · ') + (d.classes.length > 4 ? ' …' : '')
    const label = esc(d.name) + (classes ? `<br/>${esc(classes)}` : '')
    dsLines.push(`    ${id}["${label}"]`)
    dsNode.set(d.id, id)
    if (d.live?.meta.id) dsNode.set(d.live.meta.id, id)
  }

  const xwLines: string[] = []
  const xwIds: string[] = []
  const xwByName = new Map<string, string>()
  const edges = new Set<string>()
  for (const p of perspectives) {
    const id = nodeId('XW_', p.perspective_id)
    xwIds.push(id)
    const pname = p.dataset?.name || p.perspective_id
    const concepts = (p.config?.concepts ?? []).map((c) => c.name).join(' · ')
    xwLines.push(`    ${id}{{"${esc(pname)}${concepts ? `<br/>${esc(concepts)}` : ''}"}}`)
    xwByName.set(pname, id)
    for (const c of p.config?.concepts ?? []) {
      for (const part of c.participants) {
        const dn = dsNode.get(part.dataset_id)
        if (dn) edges.add(`  ${dn} --> ${id}`)
      }
    }
  }

  // Alignment edges connect two perspectives (best-effort match by display name).
  for (const a of alignments) {
    const from = xwByName.get(a.from_perspective)
    const to = xwByName.get(a.to_perspective)
    if (from && to && from !== to) edges.add(`  ${from} -. ${esc(a.relation)} .-> ${to}`)
  }

  // Existing standard ontologies each dataset reuses (schema.org / PROV / Dublin Core /
  // BIBO / QUDT / SKOS — `CatalogDataset.reuses`, derived from real term IRIs). One node
  // per vocabulary (deduped), a "reuses" edge from each dataset. Opt-in to bound clutter.
  const extLines: string[] = []
  const extIds: string[] = []
  if (showExternal) {
    const seen = new Set<string>()
    for (const d of dsList) {
      const dn = dsNode.get(d.id)
      for (const r of d.reuses ?? []) {
        const eid = nodeId('EXT_', r.prefix)
        if (!seen.has(eid)) {
          seen.add(eid)
          extIds.push(eid)
          extLines.push(`    ${eid}(["${esc(r.prefix)}"])`)
        }
        if (dn) edges.add(`  ${dn} -. ${esc(t('map:chart.reuses'))} .-> ${eid}`)
      }
    }
  }

  const lines = ['flowchart LR']
  lines.push(`  subgraph datasets["${esc(t('map:chart.datasets'))}"]`, ...dsLines, '  end')
  if (xwLines.length) {
    lines.push(`  subgraph bridges["${esc(t('map:chart.bridges'))}"]`, ...xwLines, '  end')
  }
  if (extLines.length) {
    lines.push(`  subgraph external["${esc(t('map:chart.external'))}"]`, ...extLines, '  end')
  }
  lines.push(...edges)
  if (dsIds.length) lines.push(`  class ${dsIds.join(',')} dsCls`)
  if (xwIds.length) lines.push(`  class ${xwIds.join(',')} xwCls`)
  if (extIds.length) lines.push(`  class ${extIds.join(',')} extCls`)
  lines.push('  classDef dsCls fill:#eef6ee,stroke:#6aa06a,color:#243;')
  lines.push('  classDef xwCls fill:#fdf3e3,stroke:#d9a44e,color:#523;')
  lines.push('  classDef extCls fill:#eef0fb,stroke:#7080c0,color:#234;')
  return lines.join('\n')
}

type MapData = {
  datasets: CatalogDataset[]
  perspectives: CrosswalkPerspective[]
  alignments: Alignment[]
}

export function OntologyMapView({ onBack }: { onBack?: () => void }) {
  const { t, i18n } = useTranslation()
  const [data, setData] = useState<MapData | null>(null)
  const [showExternal, setShowExternal] = useState(true)
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
          data.datasets.filter((d) => !d.isCrosswalk).flatMap((d) => (d.reuses ?? []).map((r) => r.prefix)),
        ).size,
      }
    : null

  // The chart's subgraph labels are localized, so rebuild on language change too.
  const chart = useMemo(
    () =>
      data ? buildChart(data.datasets, data.perspectives, data.alignments, t, showExternal) : null,
    [data, showExternal, t, i18n.language],
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
            <Trans i18nKey="map:bannerSub" components={[<strong />, <strong />, <strong />]} />
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
      {!chart && !err && (
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
      {chart && !empty && (
        <div className="ontomap-diagram">
          <Mermaid chart={chart} />
        </div>
      )}
    </div>
  )
}
