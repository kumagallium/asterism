import { type IngestProgress } from './api'

/**
 * Live progress for a (background) ingest: a spinner while Morph-KGC materializes
 * the RDF (no % yet), then a determinate bar as the rows stream into Oxigraph.
 * Replaces the old silent "取り込み中…" with real feedback for large datasets.
 */
export function IngestProgressView({ progress }: { progress: IngestProgress | null }) {
  if (progress?.phase === 'upload' && progress.total) {
    const done = progress.done ?? 0
    const pct = Math.floor((100 * done) / progress.total)
    return (
      <div className="ingest-progress">
        <div className="ingest-progress-label">
          投入中… <span className="mono-strong">{done.toLocaleString()}</span> /{' '}
          {progress.total.toLocaleString()} 件（{pct}%）
        </div>
        <div className="ingest-progress-track">
          <span style={{ width: `${pct}%` }} />
        </div>
      </div>
    )
  }
  return (
    <p className="ingest-progress-msg">
      <span className="spinner" />
      {progress?.message ?? 'RDF を生成中…'}
    </p>
  )
}
