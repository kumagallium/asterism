import type { Citation } from './demoApi'

// Map a citation `kind` to a PROV-DM-ish accent color (see index.css tokens).
// Data entities (curve/sample/paper) are green; process steps are blue.
function kindColor(kind: string): string {
  switch (kind) {
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

/**
 * A clickable citation chip-card: shows the entity kind, label, and a few key
 * fields. Clicking asks the parent to open the provenance trace (D2).
 */
export function CitationCard({
  citation,
  onSelect,
}: {
  citation: Citation
  onSelect?: (c: Citation) => void
}) {
  const color = kindColor(citation.kind)
  return (
    <button
      type="button"
      className="citation-card"
      onClick={() => onSelect?.(citation)}
      title="クリックで来歴トレースを表示"
    >
      <span className="citation-kind" style={{ backgroundColor: color }}>
        {citation.kind}
      </span>
      <span className="citation-body">
        <span className="citation-label">{citation.label || citation.kind || '(無題)'}</span>
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
  )
}
