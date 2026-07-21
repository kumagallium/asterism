import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * Reassuring progress card for the long (1-6 min) LLM jobs. The backend streams
 * lifecycle events (started/running + generation progress) and a ~15s heartbeat,
 * not token-by-token text, so we can't show a real % — instead we show a live
 * elapsed timer, an indeterminate animated bar, the expected duration, the last
 * status, and a liveness line ("server responded Ns ago", switching to a warning
 * past 45s of silence while EventSource auto-reconnects). The cancel button asks
 * the server to STOP the job (the 400-minute-runaway guard) — it disables itself
 * on the first click and the stream's terminal `cancelled` event settles the UI.
 *
 * (Moved verbatim from WorkbenchView.tsx so the kantan wizard shares it.)
 */
export function JobProgress({
  label,
  status,
  lastPulseAt,
  onCancel,
}: {
  label: string
  status: string
  /** Epoch ms of the last server-sent SSE event (incl. heartbeats); null until one. */
  lastPulseAt: number | null
  /** Requests a server-side cancel. A rejection re-arms the button for a retry. */
  onCancel: () => void | Promise<void>
}) {
  const { t } = useTranslation()
  const [elapsed, setElapsed] = useState(0)
  // Wall-clock "now", advanced by the same 1s interval as `elapsed` (render must
  // stay pure, so Date.now() lives in the effect, not the render body).
  const [now, setNow] = useState<number | null>(null)
  const [cancelRequested, setCancelRequested] = useState(false)
  useEffect(() => {
    const start = Date.now()
    const tick = () => {
      setElapsed(Math.floor((Date.now() - start) / 1000))
      setNow(Date.now())
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  const mm = Math.floor(elapsed / 60)
  const ss = String(elapsed % 60).padStart(2, '0')
  const showStatus = status && status !== 'done' && status !== 'refined'
  // Liveness, re-derived on the same 1s tick as `elapsed`. The server pulses at
  // least every ~15s (heartbeat), so >45s of silence means the connection is
  // down and EventSource is auto-reconnecting — worth a visible warning.
  const pulseAgeSec =
    lastPulseAt === null || now === null
      ? null
      : Math.max(0, Math.floor((now - lastPulseAt) / 1000))
  const silent = pulseAgeSec !== null && pulseAgeSec * 1000 > 45000
  function onCancelClick() {
    setCancelRequested(true)
    Promise.resolve()
      .then(() => onCancel())
      .catch(() => setCancelRequested(false))
  }
  return (
    <div className="job-progress" role="status" aria-live="polite">
      <div className="job-progress-head">
        <span className="spinner" />
        {label}
        <button
          type="button"
          className="btn btn--ghost btn--sm job-cancel-btn"
          onClick={onCancelClick}
          disabled={cancelRequested}
        >
          {cancelRequested ? t('workbench:job.cancelling') : t('workbench:job.cancel')}
        </button>
      </div>
      <div className="job-progress-bar" aria-hidden="true">
        <span />
      </div>
      <div className="job-progress-meta">
        {showStatus
          ? t('workbench:job.elapsedStatus', { mm, ss, status })
          : t('workbench:job.elapsed', { mm, ss })}
      </div>
      {pulseAgeSec !== null && (
        <div className={`job-progress-pulse${silent ? ' warn' : ''}`}>
          {silent
            ? t('workbench:job.silent', { s: pulseAgeSec })
            : t('workbench:job.pulse', { s: pulseAgeSec })}
        </div>
      )}
    </div>
  )
}
