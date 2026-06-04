import { useEffect, useState } from 'react'
import { ArrowIcon, TraceIcon } from './icons'
import { provenance, type Citation, type ProvenanceChain, type ProvenanceStep } from './demoApi'
import { KIND_TO_CLASS } from './galleryApi'

// PROV-DM step coloring: data entities green, process activities blue.
function stepColors(step: string): { color: string; ring: string } {
  switch (step) {
    case 'curve':
    case 'sample':
    case 'paper':
      return { color: 'var(--entity)', ring: 'var(--entity-soft)' }
    case 'digitization':
    case 'ingestion':
      return { color: 'var(--activity)', ring: 'var(--activity-soft)' }
    default:
      return { color: 'var(--muted)', ring: 'var(--surface-alt)' }
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
 * The provenance trace, rendered as a permanent right-hand panel of the Ask
 * view (not a drawer). When a citation is selected it resolves and renders the
 * chain (curve → sample → paper → digitization → ingestion) from the demo agent
 * contract (GET /demo/provenance). With no selection it shows a hint. This view
 * only renders the contract; it generates nothing.
 */
export function ProvenanceTrace({
  citation,
  onShowVocab,
}: {
  citation: Citation | null
  onShowVocab?: (className: string) => void
}) {
  const iri = citation?.iri ?? ''
  // State is keyed by the IRI it resolved, and only ever written at the async
  // boundary (in then/catch) — never synchronously in the effect body. Loading
  // is derived: a selected IRI that the stored result doesn't match yet is
  // "still resolving". This keeps the effect free of cascading setState.
  const [state, setState] = useState<{
    iri: string
    chain: ProvenanceChain | null
    error: string
  }>({ iri: '', chain: null, error: '' })

  useEffect(() => {
    if (!iri) return
    let cancelled = false
    provenance(iri)
      .then((c) => {
        if (!cancelled) setState({ iri, chain: c, error: '' })
      })
      .catch((e) => {
        if (!cancelled) setState({ iri, chain: null, error: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      cancelled = true
    }
  }, [iri])

  const resolved = !!iri && state.iri === iri
  const loading = !!iri && !resolved
  const chain = resolved ? state.chain : null
  const error = resolved ? state.error : ''
  const vocabClass = citation ? KIND_TO_CLASS[citation.kind] : undefined

  return (
    <aside className="ask-trace" aria-label="出どころ（来歴トレース）">
      <div className="trace-header">
        <div className="trace-eyebrow">出どころ · provenance</div>
        <h3 className="trace-title">来歴をたどる</h3>
        <p className="trace-sub">
          この値が<strong>どの曲線・論文・取り込み</strong>から来たかを、たどって示します。
        </p>
      </div>

      {!citation && (
        <div className="trace-empty">
          <span className="trace-empty-icon">
            <TraceIcon size={28} />
          </span>
          引用カードを選ぶと、その値の出どころ（曲線 → 試料 → 論文 → 取り込み）が表示されます。
        </div>
      )}

      {loading && (
        <p className="trace-loading">
          <span className="spinner" />
          来歴を解決中…
        </p>
      )}
      {error && <pre className="error">{error}</pre>}

      {chain && chain.chain.length > 0 && (
        <>
          <div className="trace-body">
            <ol className="trace-chain">
              {chain.chain.map((s, i) => (
                <TraceNode key={`${s.step}:${s.iri}:${i}`} step={s} last={i === chain.chain.length - 1} />
              ))}
            </ol>
          </div>
          <div className="trace-foot">
            <span className="trace-legend">
              <span className="trace-legend-dot" style={{ background: 'var(--entity)' }} />
              データ
            </span>
            <span className="trace-legend">
              <span className="trace-legend-dot" style={{ background: 'var(--activity)' }} />
              処理
            </span>
            {vocabClass && onShowVocab && (
              <button
                type="button"
                className="vocab-link trace-vocab-link"
                onClick={() => onShowVocab(vocabClass)}
                title={`カタログで語彙クラス「${vocabClass}」を表示`}
              >
                語彙を見る <ArrowIcon size={13} />
              </button>
            )}
          </div>
        </>
      )}

      {citation && chain && chain.chain.length === 0 && !loading && !error && (
        <p className="trace-loading">この項目に対する来歴は記録されていません。</p>
      )}
    </aside>
  )
}

function TraceNode({ step, last }: { step: ProvenanceStep; last: boolean }) {
  const { color, ring } = stepColors(step.step)
  return (
    <li className="trace-node">
      <div className="trace-rail">
        <span className="trace-dot" style={{ background: color, boxShadow: `0 0 0 4px ${ring}` }} />
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
