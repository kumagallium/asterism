import { useEffect, useState } from 'react'
import {
  isMockMode,
  provenance,
  type Citation,
  type ProvenanceChain,
  type ProvenanceStep,
} from './demoApi'

// PROV-DM step coloring: data entities green, process activities blue.
function stepColor(step: string): string {
  switch (step) {
    case 'curve':
    case 'sample':
    case 'paper':
      return 'var(--prov-entity)'
    case 'digitization':
    case 'ingestion':
      return 'var(--prov-activity)'
    default:
      return 'var(--muted)'
  }
}

// Human label per step (the chain reads back-in-time: what the datum came from).
const STEP_JA: Record<string, string> = {
  curve: '測定曲線',
  sample: '試料',
  paper: '論文',
  digitization: 'デジタル化',
  ingestion: '取り込み',
}

/**
 * A right-side drawer that resolves and renders the provenance chain for a
 * clicked citation: curve → sample → paper → digitization → ingestion. The
 * chain comes from the demo agent contract (GET /demo/provenance); this view
 * only renders it.
 */
export function ProvenanceTrace({
  citation,
  onClose,
}: {
  citation: Citation
  onClose: () => void
}) {
  // One state object so the effect performs a single async-boundary update
  // (avoids synchronous setState calls inside the effect body).
  const [state, setState] = useState<{
    iri: string
    chain: ProvenanceChain | null
    error: string
    loading: boolean
  }>({ iri: citation.iri, chain: null, error: '', loading: true })

  useEffect(() => {
    let cancelled = false
    provenance(citation.iri)
      .then((c) => {
        if (!cancelled) setState({ iri: citation.iri, chain: c, error: '', loading: false })
      })
      .catch((e) => {
        if (!cancelled)
          setState({
            iri: citation.iri,
            chain: null,
            error: e instanceof Error ? e.message : String(e),
            loading: false,
          })
      })
    return () => {
      cancelled = true
    }
  }, [citation.iri])

  // While a new iri is resolving, show the loading state for it.
  const loading = state.loading || state.iri !== citation.iri
  const chain = state.iri === citation.iri ? state.chain : null
  const error = state.iri === citation.iri ? state.error : ''

  return (
    <div className="trace-overlay" onClick={onClose}>
      <aside className="trace-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="trace-header">
          <div>
            <h2 className="trace-title">
              来歴トレース
              {isMockMode && <span className="demo-badge">demo データ (mock)</span>}
            </h2>
            <p className="trace-sub">
              {citation.label} <span className="trace-kind">{citation.kind}</span>
            </p>
          </div>
          <button className="trace-close" onClick={onClose} aria-label="閉じる">
            ×
          </button>
        </header>

        {loading && <p className="trace-loading">来歴を解決中…</p>}
        {error && <pre className="error">{error}</pre>}

        {chain && (
          <ol className="trace-chain">
            {chain.chain.map((s, i) => (
              <TraceNode key={s.iri} step={s} last={i === chain.chain.length - 1} />
            ))}
          </ol>
        )}
      </aside>
    </div>
  )
}

function TraceNode({ step, last }: { step: ProvenanceStep; last: boolean }) {
  const color = stepColor(step.step)
  return (
    <li className="trace-node">
      <div className="trace-rail">
        <span className="trace-dot" style={{ backgroundColor: color }} />
        {!last && <span className="trace-line" />}
      </div>
      <div className="trace-content">
        <div className="trace-step-head">
          <span className="trace-step-badge" style={{ backgroundColor: color }}>
            {STEP_JA[step.step] ?? step.step}
          </span>
          <span className="trace-step-label">{step.label}</span>
        </div>
        <div className="trace-detail">{step.detail}</div>
        <div className="trace-iri" title={step.iri}>
          {step.iri}
        </div>
      </div>
    </li>
  )
}
