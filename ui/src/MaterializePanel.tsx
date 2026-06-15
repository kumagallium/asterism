import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  type IngestProgress,
  type IngestResult,
  type MaterializeResult,
  type TrapResult,
  ingestDataset,
} from './api'
import { IngestProgressView } from './IngestProgressView'

const STATUS_GLYPH: Record<TrapResult['status'], string> = {
  pass: '✓',
  fail: '✗',
  warn: '⚠',
  skip: '·',
}

// Known trap ids that have a localized label (workbench:trap.<id>). The backend
// `name` is English and is used as the fallback for unknown ids.
const TRAP_IDS = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8'] as const

// Status order shown in the legend; label is resolved via workbench:status.<key>.
const STATUS_ORDER: TrapResult['status'][] = ['pass', 'skip', 'warn', 'fail']

function download(filename: string, contents: string) {
  const blob = new Blob([contents], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * Shows the materialized artifacts as download buttons + the trap validation
 * report + the human-gated Oxigraph ingest (Phase 5 #15). CSV-dependent traps
 * (T1/T6) report `skip` here because the materialize endpoint validates the
 * artifact bundle without source CSVs.
 */
export function MaterializePanel({
  result,
  csvFiles = [],
}: {
  result: MaterializeResult
  /** The CSVs used to design the schema — needed to run the substrate ingest. */
  csvFiles?: File[]
}) {
  const { t } = useTranslation()
  const artifacts = Object.entries(result.artifacts).filter(([, v]) => v) as [string, string][]
  // Localized trap label, falling back to the backend's English `name` for ids
  // we don't have a translation for.
  const trapLabel = (id: string, name: string) =>
    (TRAP_IDS as readonly string[]).includes(id) ? t(`workbench:trap.${id}`) : name
  return (
    <section className="materialize-panel">
      <h3 className="section-h">{t('workbench:materialize.artifactsHeading', { n: artifacts.length })}</h3>
      <div className="artifact-list">
        {Object.entries(result.artifacts).map(([name, contents]) => (
          <button
            key={name}
            type="button"
            className="artifact-btn"
            disabled={!contents}
            onClick={() => contents && download(name, contents)}
            title={
              contents
                ? t('workbench:materialize.downloadTitle', { name })
                : t('workbench:materialize.notExtractedTitle', { name })
            }
          >
            ⤓ {name}
          </button>
        ))}
      </div>
      {result.warnings.length > 0 && (
        <ul className="materialize-warnings">
          {result.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      <h3 className="section-h">{t('workbench:materialize.validationHeading')}</h3>
      <div className="trap-legend">
        {STATUS_ORDER.map((status) => (
          <span key={status} className="trap-legend-item">
            <span className={`trap-glyph trap-${status}`}>{STATUS_GLYPH[status]}</span>
            {t(`workbench:status.${status}`)}
          </span>
        ))}
      </div>
      <div className="trap-grid">
        {result.traps.map((tr) => (
          <div
            key={tr.id}
            className={`trap trap-${tr.status}`}
            title={`${trapLabel(tr.id, tr.name)}${tr.detail ? ` — ${tr.detail}` : ''}`}
          >
            <span className="trap-glyph">{STATUS_GLYPH[tr.status]}</span>
            <span className="trap-id">{tr.id}</span>
            <span className="trap-name">{trapLabel(tr.id, tr.name)}</span>
          </div>
        ))}
      </div>
      <p className="trap-summary">
        {result.exit_code === 0 ? (
          <span className="trap-ok">{t('workbench:materialize.summaryOk')}</span>
        ) : (
          <span className="trap-bad">
            {t('workbench:materialize.summaryBad', { code: result.exit_code })}
          </span>
        )}
      </p>

      <IngestGate result={result} csvFiles={csvFiles} />
    </section>
  )
}

/**
 * The human gate (#15): approve the declarative RML and run it through the
 * Morph-KGC substrate into an *isolated draft graph*. Ask cites the canonical
 * graph by default, so draft data is not a citable fact until promoted.
 */
function IngestGate({ result, csvFiles }: { result: MaterializeResult; csvFiles: File[] }) {
  const { t } = useTranslation()
  const rml = (result.artifacts['mapping.rml.ttl'] ?? '').trim()
  const datasetId = result.dataset?.id
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [done, setDone] = useState<IngestResult | null>(null)
  const [err, setErr] = useState('')

  if (!rml) {
    return (
      <div className="ingest-gate">
        <h3 className="section-h">{t('workbench:ingest.heading')}</h3>
        <p className="ingest-hint">{t('workbench:ingest.noRml')}</p>
      </div>
    )
  }

  const canIngest = !!datasetId && csvFiles.length > 0 && !busy

  async function onIngest() {
    if (!datasetId) return
    setBusy(true)
    setErr('')
    setProgress(null)
    try {
      setDone(await ingestDataset(datasetId, csvFiles, setProgress))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ingest-gate">
      <h3 className="section-h">{t('workbench:ingest.heading')}</h3>
      <p className="ingest-note">
        <Trans i18nKey="workbench:ingest.note" components={{ strong: <strong /> }} />
      </p>
      <details className="rml-preview">
        <summary>
          {t('workbench:ingest.previewSummary', { n: rml.split('\n').length })}
        </summary>
        <pre className="rml-pre">{rml}</pre>
      </details>

      {done ? (
        <p className="ingest-ok">
          {t('workbench:ingest.doneCount', { n: done.triple_count })}
          <br />
          <code className="ingest-graph">{done.graph_iri}</code>
        </p>
      ) : (
        <>
          <button type="button" onClick={onIngest} disabled={!canIngest}>
            {busy ? t('workbench:ingest.ingesting') : t('workbench:ingest.approve')}
          </button>
          {busy && <IngestProgressView progress={progress} />}
          {!datasetId && <p className="ingest-hint">{t('workbench:ingest.notSaved')}</p>}
          {datasetId && csvFiles.length === 0 && (
            <p className="ingest-hint">{t('workbench:ingest.needCsv')}</p>
          )}
          {err && <p className="ingest-err">{t('workbench:ingest.failed', { message: err })}</p>}
        </>
      )}
    </div>
  )
}
