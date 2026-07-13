import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type IngestProgress } from './api'

/**
 * Live progress for a (background) ingest: a spinner while Morph-KGC materializes
 * the RDF (no % yet), then a determinate bar as the rows stream into Oxigraph.
 * Replaces the old silent "取り込み中…" with real feedback for large datasets.
 *
 * `onCancel` (optional) renders the same cancel affordance the propose/refine
 * JobProgress card has — the server stops at its next cooperative checkpoint and
 * reclaims the partial staged graph. `lastPulseAt` (optional, epoch ms of the
 * last server-sent SSE event incl. heartbeats) adds the liveness line: the
 * server pulses at least every ~15s, so >45s of silence means the connection is
 * down and EventSource is auto-reconnecting — worth a visible warning during a
 * minutes-long silent materialize/convert phase.
 */
export function IngestProgressView({
  progress,
  onCancel,
  lastPulseAt,
}: {
  progress: IngestProgress | null
  onCancel?: () => void | Promise<void>
  lastPulseAt?: number | null
}) {
  const { t } = useTranslation()
  const [cancelRequested, setCancelRequested] = useState(false)
  // Wall-clock "now" for the liveness line, advanced by a 1s tick (render must
  // stay pure, so Date.now() lives in the effect, not the render body).
  const [now, setNow] = useState<number | null>(null)
  useEffect(() => {
    if (lastPulseAt === undefined) return
    const tick = () => setNow(Date.now())
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [lastPulseAt])

  const pulseAgeSec =
    lastPulseAt == null || now === null
      ? null
      : Math.max(0, Math.floor((now - lastPulseAt) / 1000))
  const silent = pulseAgeSec !== null && pulseAgeSec > 45

  function onCancelClick() {
    setCancelRequested(true)
    Promise.resolve()
      .then(() => onCancel?.())
      .catch(() => setCancelRequested(false)) // a failed request re-arms the button
  }

  const cancelBtn = onCancel ? (
    <button
      type="button"
      className="btn btn--ghost btn--sm job-cancel-btn"
      onClick={onCancelClick}
      disabled={cancelRequested}
    >
      {cancelRequested ? t('workbench:job.cancelling') : t('workbench:job.cancel')}
    </button>
  ) : null

  const pulseLine =
    pulseAgeSec !== null ? (
      <div className={`job-progress-pulse${silent ? ' warn' : ''}`}>
        {silent
          ? t('workbench:job.silent', { s: pulseAgeSec })
          : t('workbench:job.pulse', { s: pulseAgeSec })}
      </div>
    ) : null

  if (progress?.phase === 'upload' && progress.total) {
    const done = progress.done ?? 0
    const pct = Math.floor((100 * done) / progress.total)
    return (
      <div className="ingest-progress">
        <div className="ingest-progress-label">
          {t('workbench:progress.ingesting')}{' '}
          <span className="mono-strong">{done.toLocaleString()}</span> /{' '}
          {t('workbench:progress.ofCount', {
            total: progress.total.toLocaleString(),
            pct,
          })}
          {cancelBtn}
        </div>
        <div className="ingest-progress-track">
          <span style={{ width: `${pct}%` }} />
        </div>
        {pulseLine}
      </div>
    )
  }
  return (
    <div className="ingest-progress">
      <p className="ingest-progress-msg">
        <span className="spinner" />
        {progress?.message ?? t('workbench:progress.generatingRdf')}
        {cancelBtn}
      </p>
      {pulseLine}
    </div>
  )
}
