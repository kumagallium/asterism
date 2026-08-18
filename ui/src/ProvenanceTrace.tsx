import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { ArrowIcon, CloseIcon, TraceIcon } from './icons'
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
    case 'activity':
      return { color: 'var(--activity)', ring: 'var(--activity-soft)' }
    default:
      return { color: 'var(--muted)', ring: 'var(--surface-alt)' }
  }
}

// Steps that have a human label (the chain reads back-in-time: what the datum
// came from). Resolved per-render via t('shared:step.<step>'); unknown steps fall
// back to the raw key. `activity` is what the generic (non-starrydata) provenance
// path returns for a prov:Activity.
const KNOWN_STEPS = new Set(['curve', 'sample', 'paper', 'digitization', 'ingestion', 'activity'])

// Activity class names the agent hands back as a step LABEL when the activity has
// no rdfs:label of its own. Only these are translated — anything else is a real
// label from the data and must be shown verbatim.
const KNOWN_ACTIVITY_LABELS = new Set([
  'Activity',
  'IngestionActivity',
  'DigitizationActivity',
  'DocumentParsingActivity',
])

// Detail keys the agent emits as `key=value`. Anything else keeps its own key.
const KNOWN_DETAIL_KEYS = new Set(['atTime', 'composition', 'id', 'title', 'doi'])

/**
 * Turn the agent's `key=value` detail line into a readable one.
 *
 * The contract sends detail as machine pairs (`atTime=2026-…`, `composition=SnSe`).
 * Rendered raw that is an English identifier and an ISO timestamp. This maps the
 * known keys to words and formats a timestamp in the reader's locale. Anything
 * that is not entirely `key=value` pairs is passed through untouched — the detail
 * may also be a plain human sentence, and rewriting that would lose meaning.
 */
function formatDetail(detail: string, t: (k: string) => string, lng: string): string {
  const raw = detail.trim()
  if (!raw || !raw.includes('=')) return detail
  const parts = raw.split(/\s*[,;]\s+/)
  const out: string[] = []
  for (const part of parts) {
    const m = /^([A-Za-z_][A-Za-z0-9_]*)=([\s\S]*)$/.exec(part)
    if (!m) return detail // not a pure key=value line — leave it alone
    const [, key, value] = m
    const label = KNOWN_DETAIL_KEYS.has(key) ? t(`shared:detailKey.${key}`) : key
    out.push(`${label}: ${key === 'atTime' ? formatWhen(value, lng) : value}`)
  }
  return out.join(' · ')
}

function formatWhen(value: string, lng: string): string {
  const ms = Date.parse(value)
  if (Number.isNaN(ms)) return value
  return new Intl.DateTimeFormat(lng.startsWith('en') ? 'en' : 'ja', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(ms))
}

/**
 * The provenance trace, rendered as the right-hand panel of the Ask chat: it
 * opens when a citation is clicked (and closes via `onClose`). When a citation
 * is selected it resolves and renders the chain (curve → sample → paper →
 * digitization → ingestion) from the demo agent contract (GET /demo/provenance).
 * With no selection it shows a hint. This view only renders the contract; it
 * generates nothing.
 */
export function ProvenanceTrace({
  citation,
  onShowVocab,
  onClose,
}: {
  citation: Citation | null
  onShowVocab?: (className: string) => void
  /** Rendered as a close (×) button in the header when given. */
  onClose?: () => void
}) {
  const { t } = useTranslation()
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
    <aside className="ask-trace" aria-label={t('shared:trace.ariaLabel')}>
      <div className="trace-header">
        {onClose && (
          <button
            type="button"
            className="trace-close"
            onClick={onClose}
            aria-label={t('shared:trace.close')}
            title={t('shared:trace.close')}
          >
            <CloseIcon size={16} />
          </button>
        )}
        <div className="trace-eyebrow">{t('shared:trace.eyebrow')}</div>
        <h3 className="trace-title">{t('shared:trace.title')}</h3>
        <p className="trace-sub">
          <Trans i18nKey="shared:trace.sub">
            この値が<strong>どの曲線・論文・取り込み</strong>から来たかを、たどって示します。
          </Trans>
        </p>
      </div>

      {!citation && (
        <div className="trace-empty">
          <span className="trace-empty-icon">
            <TraceIcon size={28} />
          </span>
          {t('shared:trace.empty')}
        </div>
      )}

      {loading && (
        <p className="trace-loading">
          <span className="spinner" />
          {t('shared:trace.loading')}
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
              {t('shared:trace.legendData')}
            </span>
            <span className="trace-legend">
              <span className="trace-legend-dot" style={{ background: 'var(--activity)' }} />
              {t('shared:trace.legendProcess')}
            </span>
            {vocabClass && onShowVocab && (
              <button
                type="button"
                className="vocab-link trace-vocab-link"
                onClick={() => onShowVocab(vocabClass)}
                title={t('shared:trace.vocabTitle', { className: vocabClass })}
              >
                {t('shared:trace.vocabLink')} <ArrowIcon size={13} />
              </button>
            )}
          </div>
        </>
      )}

      {citation && chain && chain.chain.length === 0 && !loading && !error && (
        <p className="trace-loading">{t('shared:trace.noRecord')}</p>
      )}
    </aside>
  )
}

function TraceNode({ step, last }: { step: ProvenanceStep; last: boolean }) {
  const { t, i18n } = useTranslation()
  const { color, ring } = stepColors(step.step)
  const label = KNOWN_ACTIVITY_LABELS.has(step.label)
    ? t(`shared:activityLabel.${step.label}`)
    : step.label
  const detail = formatDetail(step.detail, t, i18n.language)
  // The citation landing page (IRI dereference). Same origin as the SPA in both
  // production (Caddy) and dev (vite proxy), so a relative href is enough.
  const describeHref = `/describe?iri=${encodeURIComponent(step.iri)}`
  // 引用 IRI は「引用できる事実」の中核成果物 — 手動選択に頼らずクリックでコピー。
  const [copied, setCopied] = useState(false)
  function copyIri() {
    navigator.clipboard
      ?.writeText(step.iri)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      })
      .catch(() => {})
  }
  return (
    <li className="trace-node">
      <div className="trace-rail">
        <span className="trace-dot" style={{ background: color, boxShadow: `0 0 0 4px ${ring}` }} />
        {!last && <span className="trace-line" />}
      </div>
      <div className="trace-content">
        <div className="trace-step-head">
          <span className="trace-step-badge" style={{ backgroundColor: color }}>
            {KNOWN_STEPS.has(step.step) ? t(`shared:step.${step.step}`) : step.step}
          </span>
          <span className="trace-step-label">{label}</span>
        </div>
        <div className="trace-detail">{detail}</div>
        {/* The ID is the citable artifact: copy it (existing) OR open the page it
            lands on. Without the second action /describe is unreachable from the
            app, and "show this to a colleague" has no button. */}
        <div className="trace-iri-line">
          <button
            type="button"
            className={`trace-iri trace-iri-copy${copied ? ' cell-copied' : ''}`}
            title={t('shared:trace.copyIri')}
            onClick={copyIri}
          >
            <span className="trace-iri-label">{t('shared:trace.idLabel')}</span> {step.iri}
            <span className="trace-iri-copied" aria-live="polite">
              {copied ? t('shared:trace.copied') : ''}
            </span>
          </button>
          <a
            className="link-btn trace-iri-open"
            href={describeHref}
            target="_blank"
            rel="noreferrer"
            title={t('shared:trace.openDescribe')}
          >
            {t('shared:trace.openDescribe')} <ArrowIcon size={12} />
          </a>
        </div>
      </div>
    </li>
  )
}
