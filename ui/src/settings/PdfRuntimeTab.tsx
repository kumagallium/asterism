import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { progressPercent, sizeGb, usePdfRuntime } from '../pdfRuntime'

// 「PDF」タブ — デスクトップ版で、PDF を読み取る部品をあとから入れる／消す。
// 状態は pdfRuntime.ts の共有ストアから来る（入れている最中は 1.5 秒おきに更新）。
// 画面を閉じても入れる処理は続く（バックエンド側で動いている）。

export function PdfRuntimeTab() {
  const { t } = useTranslation('settings')
  const { status, install, remove } = usePdfRuntime()
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [confirming, setConfirming] = useState(false)

  if (!status) return null
  const size = sizeGb(status)

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setFailed(false)
    try {
      await action()
      setConfirming(false)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  const pct = progressPercent(status)

  return (
    <div className="pdf-tab" id="settings-pdf">
      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('pdf.title')}</h4>

        {status.state === 'absent' && (
          <>
            <p className="field-help">{t('pdf.intro', { size })}</p>
            <div className="pdf-actions">
              <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void run(install)}>
                {t('pdf.install', { size })}
              </button>
            </div>
            <p className="field-help">{t('pdf.installNote')}</p>
          </>
        )}

        {status.state === 'installing' && (
          <>
            <p className="field-help">
              {status.phase ? t(`pdf.phase.${status.phase}`) : t('pdf.phase.packages')}{' '}
              <span>{t('pdf.progress', { pct })}</span>
            </p>
            <progress
              className="pdf-progress"
              max={100}
              value={pct}
              aria-label={t('pdf.progress', { pct })}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
            />
            <p className="field-help">{t('pdf.keepsGoing')}</p>
            <div className="pdf-actions">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={busy}
                onClick={() => void run(remove)}
              >
                {t('pdf.cancel')}
              </button>
            </div>
          </>
        )}

        {status.state === 'starting' && <p className="field-help">{t('pdf.starting')}</p>}

        {status.state === 'ready' && (
          <>
            <p className="pdf-ready">
              <span aria-hidden="true">✓ </span>
              {t('pdf.ready')}
            </p>
            <p className="field-help">{t('pdf.readyNote', { size })}</p>
            {confirming ? (
              <div className="storage-confirm">
                <p className="field-help">{t('pdf.removeConfirm', { size })}</p>
                <div className="storage-confirm-actions">
                  <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void run(remove)}>
                    {t('pdf.removeYes')}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={busy}
                    onClick={() => setConfirming(false)}
                  >
                    {t('pdf.removeNo')}
                  </button>
                </div>
              </div>
            ) : (
              <div className="pdf-actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy}
                  onClick={() => setConfirming(true)}
                >
                  {t('pdf.remove')}
                </button>
              </div>
            )}
          </>
        )}

        {status.state === 'failed' && (
          <>
            <p className="field-help field-error">{t('pdf.failed')}</p>
            {status.error && <p className="field-help">{status.error}</p>}
            {status.log_path && (
              <p className="field-help storage-path">{t('pdf.logPath', { path: status.log_path })}</p>
            )}
            <div className="pdf-actions">
              <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void run(install)}>
                {t('pdf.retry')}
              </button>
              {/* 入っているのに動かないときは「もう一度」が同じ失敗を返すだけになり得る。
                  消して入れ直せる道を必ず残す。 */}
              {status.installed && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy}
                  onClick={() => void run(remove)}
                >
                  {t('pdf.remove')}
                </button>
              )}
            </div>
          </>
        )}

        {failed && (
          <p className="field-help field-error" role="alert">
            {t('pdf.requestFailed')}
          </p>
        )}
      </section>
    </div>
  )
}
