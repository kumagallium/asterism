import { useEffect, useState } from 'react'
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

  const lines = ['flowchart LR']
  lines.push(`  subgraph datasets["${esc(t('map:chart.datasets'))}"]`, ...dsLines, '  end')
  if (xwLines.length) {
    lines.push(`  subgraph bridges["${esc(t('map:chart.bridges'))}"]`, ...xwLines, '  end')
  }
  lines.push(...edges)
  if (dsIds.length) lines.push(`  class ${dsIds.join(',')} dsCls`)
  if (xwIds.length) lines.push(`  class ${xwIds.join(',')} xwCls`)
  lines.push('  classDef dsCls fill:#eef6ee,stroke:#6aa06a,color:#243;')
  lines.push('  classDef xwCls fill:#fdf3e3,stroke:#d9a44e,color:#523;')
  return lines.join('\n')
}

export function OntologyMapView({ onBack }: { onBack?: () => void }) {
  const { t, i18n } = useTranslation()
  const [chart, setChart] = useState<string | null>(null)
  const [counts, setCounts] = useState<{ ds: number; xw: number; al: number } | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let off = false
    Promise.all([getCatalogDatasets(), getCrosswalks(), getAlignments()])
      .then(([datasets, perspectives, al]) => {
        if (off) return
        setChart(buildChart(datasets, perspectives, al.alignments, t))
        setCounts({
          ds: datasets.filter((d) => !d.isCrosswalk).length,
          xw: perspectives.length,
          al: al.alignments.length,
        })
      })
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
    // Re-fetch + rebuild (the chart's subgraph labels are localized) on language change.
  }, [t, i18n.language])

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
