import { useTranslation } from 'react-i18next'
import type { Citation } from './demoApi'
import { vocabClassFor } from './galleryApi'
import { TraceIcon } from './icons'

// The citation kinds that have a human label in `shared:step.*` — the same set
// ProvenanceTrace uses for its step badges, so a datum is named identically in
// the card and in the trace panel. The LLM escape can return any string as a
// kind (a class name, an empty string), so anything outside this set is NEVER
// pushed through t(): it would render a raw i18n key.
const KNOWN_KINDS = new Set(['curve', 'sample', 'paper', 'digitization', 'ingestion'])

// Field keys the tools themselves define (query_tools outputs / the starrydata
// typed path). Only these are translated; a key that came from someone's own
// column stays exactly as they wrote it — renaming a person's column would be a
// lie, and t() on an unknown key would print the raw i18n key.
const KNOWN_FIELD_KEYS = new Set([
  'propertyY',
  'yMax',
  'yMin',
  'composition',
  'paper',
  'title',
  'name',
  'measurements',
  'doi',
  'DOI',
  'id',
])

/** `https://…/resource/sample/1-2` → `sample/1-2` — the tail a person can still
 *  recognise. Used when the answer carried no label at all (a weak model may
 *  omit it), instead of repeating the kind or printing "(untitled)". */
function iriTail(iri: string): string {
  const path = iri.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]+/i, '')
  const parts = path.split(/[/#]/).filter(Boolean)
  return parts.length > 0 ? parts.slice(-2).join('/') : ''
}

// Map a citation `kind` to a PROV-DM accent color (see index.css tokens).
// Data entities (curve/sample/paper) are green; process steps are blue.
function kindColor(kind: string): string {
  switch (kind) {
    case 'curve':
    case 'sample':
    case 'paper':
      return 'var(--entity)'
    case 'digitization':
    case 'ingestion':
      return 'var(--activity)'
    default:
      return 'var(--muted)'
  }
}

/**
 * A clickable citation chip-card: a colored kind bar, the entity kind + label,
 * and a few key fields. Clicking selects the card, which loads its provenance
 * into the always-on trace panel beside the answer (Ask). A separate "語彙"
 * link (when the kind maps to a vocabulary class) jumps to the Catalog and
 * highlights that class — connecting a grounded answer to the ontology that
 * backs it (Ask⇄Catalog).
 */
export function CitationCard({
  citation,
  selected,
  onSelect,
  onShowVocab,
  datasetName,
  vocabClasses,
}: {
  citation: Citation
  selected?: boolean
  onSelect?: (c: Citation) => void
  onShowVocab?: (className: string) => void
  /** Which dataset this datum came from — resolved from the IRI, never guessed.
   *  With several datasets published, the card alone did not say whose number
   *  this is (a person could read someone else's data as their own). */
  datasetName?: string
  /** Class names present in the catalog, so a self-designed kind links too. */
  vocabClasses?: ReadonlySet<string>
}) {
  const { t } = useTranslation()
  const color = kindColor(citation.kind)
  const vocabClass = vocabClassFor(citation.kind, vocabClasses)
  // Known kind → the plain-language name; unknown/empty → no chip at all (the
  // colored bar still marks the card), rather than an English identifier in
  // uppercase mono that reads as a code.
  const kindLabel = KNOWN_KINDS.has(citation.kind) ? t(`shared:step.${citation.kind}`) : ''
  return (
    <div className="citation-card-wrap">
      <button
        type="button"
        className={`citation-card${selected ? ' selected' : ''}`}
        onClick={() => onSelect?.(citation)}
        title={t('shared:citation.cardTitle')}
      >
        <span className="citation-bar" style={{ backgroundColor: color }} />
        <span className="citation-body">
          <span className="citation-head">
            {kindLabel && (
              <span className="citation-kind" style={{ backgroundColor: color }}>
                {kindLabel}
              </span>
            )}
            <span className="citation-label">
              {citation.label || iriTail(citation.iri) || kindLabel || t('shared:citation.untitled')}
            </span>
            <span className="citation-trace-hint">
              <TraceIcon size={13} /> {t('shared:citation.traceHint')}
            </span>
          </span>
          <span className="citation-fields">
            {/* Which dataset this row belongs to, first — read from the IRI the
                citation already carries (no lookup, no guess). */}
            {datasetName && (
              <span className="citation-field citation-dataset">
                <span className="citation-field-val" title={datasetName}>
                  {t('shared:citation.fromDataset', { name: datasetName })}
                </span>
              </span>
            )}
            {/* Drop null/undefined/empty field values: real rows omit
                composition/title/DOI, which arrive as null — don't render "null". */}
            {Object.entries(citation.fields)
              .filter(([, v]) => v !== null && v !== undefined && v !== '')
              .map(([k, v]) => (
                <span key={k} className="citation-field">
                  <span className="citation-field-key">
                    {KNOWN_FIELD_KEYS.has(k) ? t(`shared:field.${k}`) : k}
                  </span>
                  <span className="citation-field-val" title={String(v)}>
                    {String(v)}
                  </span>
                </span>
              ))}
          </span>
        </span>
      </button>

      {vocabClass && onShowVocab && (
        <button
          type="button"
          className="vocab-link"
          onClick={() => onShowVocab(vocabClass)}
          title={t('shared:citation.vocabTitle', { className: vocabClass })}
        >
          {t('shared:citation.vocabLink', { className: vocabClass })}
        </button>
      )}
    </div>
  )
}
