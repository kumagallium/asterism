import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type IngestProgress } from './api'

/**
 * Live progress for a (background) ingest: a spinner while the data is being
 * converted (no % yet), then a determinate bar as it streams into the store.
 * Replaces the old silent "取り込み中…" with real feedback for large datasets.
 *
 * The wording comes from the PHASE, not from the server's `message`: the server
 * describes its own machinery (「RDF を生成中」「取り込み先グラフを準備中」), which is
 * the vocabulary ADR K4 keeps off shared screens, and it named the same steps
 * differently from the wizard. An unknown phase falls back to the server line
 * rather than to silence, so a future phase never shows a blank card.
 *
 * `onCancel` (optional) renders the same cancel affordance the propose/refine
 * JobProgress card has — the server stops at its next cooperative checkpoint and
 * reclaims the partial staged graph. `lastPulseAt` (optional, epoch ms of the
 * last server-sent SSE event incl. heartbeats) drives the liveness WARNING: the
 * server pulses at least every ~15s, so >45s of silence means the connection is
 * down and EventSource is auto-reconnecting — worth saying during a minutes-long
 * silent materialize/convert phase. A healthy connection says nothing.
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

  // Only the ABNORMAL case is worth a line. "サーバ応答: 3秒前", ticking once a
  // second next to a progress bar, is the machine reporting on itself; what the
  // reader needs to know is when it has gone quiet.
  const pulseLine = silent ? (
    <div className="job-progress-pulse warn">{t('workbench:job.silent', { s: pulseAgeSec })}</div>
  ) : null

  if (progress?.phase === 'upload' && progress.total) {
    const done = progress.done ?? 0
    const pct = Math.floor((100 * done) / progress.total)
    return (
      <div className="ingest-progress">
        {/* The raw counter is a triple count, which K12 keeps off the plain
            layer — and the English string called it "rows", which it never was. */}
        <div className="ingest-progress-label">
          {t('gallery:progress.ingestingPct', { pct })}
          {cancelBtn}
        </div>
        <div className="ingest-progress-track">
          <span style={{ width: `${pct}%` }} />
        </div>
        {pulseLine}
      </div>
    )
  }
  const phaseKey: Record<string, string> = {
    materialize: 'gallery:progress.materialize',
    materialized: 'gallery:progress.materialize',
    preparing: 'gallery:progress.preparing',
    converting: 'gallery:progress.converting',
    upload: 'gallery:progress.ingesting',
  }
  const known = progress?.phase ? phaseKey[progress.phase] : undefined
  const label = known
    ? t(known)
    : (progress?.message ?? t('gallery:progress.unknown'))
  return (
    <div className="ingest-progress">
      <p className="ingest-progress-msg">
        <span className="spinner" />
        {label}
        {cancelBtn}
      </p>
      {pulseLine}
    </div>
  )
}
