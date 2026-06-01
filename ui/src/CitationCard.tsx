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
        <span className="citation-label">{citation.label}</span>
        <span className="citation-fields">
          {Object.entries(citation.fields).map(([k, v]) => (
            <span key={k} className="citation-field">
              <span className="citation-field-key">{k}</span>
              <span className="citation-field-val">{String(v)}</span>
            </span>
          ))}
        </span>
      </span>
    </button>
  )
}
