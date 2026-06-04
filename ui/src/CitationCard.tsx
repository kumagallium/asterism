import type { Citation } from './demoApi'
import { KIND_TO_CLASS } from './galleryApi'
import { TraceIcon } from './icons'

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
}: {
  citation: Citation
  selected?: boolean
  onSelect?: (c: Citation) => void
  onShowVocab?: (className: string) => void
}) {
  const color = kindColor(citation.kind)
  const vocabClass = KIND_TO_CLASS[citation.kind]
  return (
    <div className="citation-card-wrap">
      <button
        type="button"
        className={`citation-card${selected ? ' selected' : ''}`}
        onClick={() => onSelect?.(citation)}
        title="クリックで出どころ（来歴）を表示"
      >
        <span className="citation-bar" style={{ backgroundColor: color }} />
        <span className="citation-body">
          <span className="citation-head">
            <span className="citation-kind" style={{ backgroundColor: color }}>
              {citation.kind}
            </span>
            <span className="citation-label">{citation.label || citation.kind || '(無題)'}</span>
            <span className="citation-trace-hint">
              <TraceIcon size={13} /> 出どころ
            </span>
          </span>
          <span className="citation-fields">
            {/* Drop null/undefined/empty field values: real rows omit
                composition/title/DOI, which arrive as null — don't render "null". */}
            {Object.entries(citation.fields)
              .filter(([, v]) => v !== null && v !== undefined && v !== '')
              .map(([k, v]) => (
                <span key={k} className="citation-field">
                  <span className="citation-field-key">{k}</span>
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
          title={`カタログで語彙クラス「${vocabClass}」を表示`}
        >
          語彙: {vocabClass} →
        </button>
      )}
    </div>
  )
}
