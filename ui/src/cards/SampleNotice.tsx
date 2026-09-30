// 見本のページの「新しくなった・新しくしていない」の知らせ（ADR kantan K62・画面の知らせ）。
// 置き場所は出どころの行の下・「データの意味を定義する」の帯の前（DatasetPage が置く）。
//
// * 新しくなったとき: 静かな 1 行。
// * 保留があるとき: 注意の帯＋項目。置き換えられる項目があるときだけ「新しい見本に置き換える」。
// * 控えがあるとき: 「控えから戻す」。
// どちらのボタンも確認のモーダルを通す（window.confirm は Enter が続行になるので使わない）。
// 成功したらページごと読み直す（左のレールなど、1 回しか読まない所まで新しくするため）。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { refreshSample, restoreSample, type SampleNotice as Notice } from './cardsApi'
import {
  backupLine,
  canOverride,
  errorText,
  heldLines,
  overridableLabels,
  touchesTools,
  unitLabel,
  updatedLine,
} from './sampleNoticeText'
import './dataset.css'
import './pages.css'

type Mode = 'replace' | 'restore'

export interface SampleNoticeProps {
  datasetId: string
  notice: Notice
}

export function SampleNotice({ datasetId, notice }: SampleNoticeProps) {
  const { t, i18n } = useTranslation('cards')
  const [mode, setMode] = useState<Mode | null>(null)
  const lines = heldLines(notice.held, t)
  const showHeld = lines.length > 0

  if (!notice.updated && !showHeld && !notice.restorable) return null

  return (
    <div className="dataset-sample">
      {notice.updated && (
        <p className="kz-note dataset-sample-updated">{updatedLine(notice.updated, i18n.language, t)}</p>
      )}

      {showHeld && (
        <div className="dataset-band dataset-band--warn">
          <div className="dataset-band-title dataset-band-title--warn">{t('sample.held_title')}</div>
          <ul className="dataset-sample-held">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {canOverride(notice) && (
            <div className="dataset-band-actions">
              <button type="button" className="btn btn--soft btn--sm" onClick={() => setMode('replace')}>
                {t('sample.replace')}
              </button>
            </div>
          )}
        </div>
      )}

      {notice.restorable && (
        <div className="dataset-band">
          <div className="dataset-band-row">
            <span className="dataset-band-text">
              {backupLine(notice.restorable.at, i18n.language, t)}
            </span>
            <div className="dataset-band-actions">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setMode('restore')}>
                {t('sample.restore')}
              </button>
            </div>
          </div>
        </div>
      )}

      {mode && (
        <ConfirmDialog datasetId={datasetId} notice={notice} mode={mode} onClose={() => setMode(null)} />
      )}
    </div>
  )
}

interface ConfirmDialogProps {
  datasetId: string
  notice: Notice
  mode: Mode
  onClose: () => void
}

/** 確認のモーダル（ExportDialog の流儀）: 何が置き換わる／戻るか・控えの説明・キャンセルを
 *  先に・主ボタン。実行中は押せない。失敗はコードから固定の文（`message` は出さない）。 */
function ConfirmDialog({ datasetId, notice, mode, onClose }: ConfirmDialogProps) {
  const { t } = useTranslation('cards')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const isReplace = mode === 'replace'
  const units = isReplace ? notice.overridable : (notice.restorable?.units ?? [])
  const labels = isReplace
    ? overridableLabels(notice, t)
    : units.map((u) => unitLabel(u, t))

  async function run() {
    if (isReplace && (notice.seq === null || notice.revision === null)) return
    setBusy(true)
    setError(null)
    try {
      if (isReplace) {
        await refreshSample(
          datasetId,
          { seq: notice.seq ?? 0, revision: notice.revision ?? '' },
          notice.overridable,
        )
      } else if (notice.restorable) {
        await restoreSample(datasetId, notice.restorable.at)
      }
      // 左のレールなど、1 回しか読まない所まで新しくするため、ページごと読み直す。
      window.location.reload()
    } catch (e) {
      setError(errorText(e, t))
      setBusy(false)
    }
  }

  const title = t(isReplace ? 'sample.confirm_replace_title' : 'sample.confirm_restore_title')
  return (
    <div className="export-overlay" onClick={busy ? undefined : onClose}>
      <div
        className="export-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="export-modal-head">
          <h3>{title}</h3>
          <button
            type="button"
            className="export-modal-close"
            onClick={onClose}
            disabled={busy}
            aria-label={t('sample.confirm_close')}
          >
            ×
          </button>
        </div>
        <p className="export-modal-what">
          {t(isReplace ? 'sample.confirm_replace_what' : 'sample.confirm_restore_what', {
            units: labels.join(t('dataset.join')),
          })}
        </p>
        <p className="export-modal-intro">
          {t(isReplace ? 'sample.confirm_replace_backup' : 'sample.confirm_restore_note')}
        </p>
        {touchesTools(units) && <p className="export-modal-intro">{t('sample.confirm_tools_note')}</p>}
        {error && (
          <p className="export-modal-error" role="alert">
            {error}
          </p>
        )}
        <div className="export-modal-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} disabled={busy}>
            {t('sample.confirm_cancel')}
          </button>
          <button type="button" className="btn btn--soft btn--sm" disabled={busy} onClick={run}>
            {busy
              ? t(isReplace ? 'sample.confirm_replace_busy' : 'sample.confirm_restore_busy')
              : t(isReplace ? 'sample.confirm_replace_submit' : 'sample.confirm_restore_submit')}
          </button>
        </div>
      </div>
    </div>
  )
}
