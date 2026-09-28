// 「エージェントを持ち帰る」ダイアログ（契約メモ §6・引き継ぎ書 §5.7）。
// SubjectPage.tsx / SetPage.tsx の両方が使うので共通化した（CardTile.tsx が
// 同じ理由で両ページ共通にした前例と同じ判断 — 新設・notes に記載）。
//
// 「配れる版（自分のデータを抜く）」／「全部（手元限り）」の 2 択 ＋ 言語 ＋
// 「作る」→ `POST /api/subjects/export` → zip を保存。409（`share: 'shareable'`
// で配れるカードが 1 枚も無い）は理由を表示してダイアログを閉じない。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { CardRef, CardRunSubject, ExportLang, ExportShareMode } from './cardsApi'
import { NotShareableExportError, exportSubjectAgent } from './cardsApi'
import { formatShareReasons } from './shareReasons'
import './pages.css'

export interface ExportDialogProps {
  subject: CardRunSubject
  /** ページに並んでいる既定カード（契約メモ §3 の `cards[]` に詰め替える）。 */
  cards: CardRef[]
  onClose: () => void
}

/** blob を `<a download>` 相当で保存する（Artifact のサンドボックスと違い、
 *  実アプリ（ブラウザ／Tauri webview）ではこの経路が普通に動く）。 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // revoke は次のイベントループへ — click() が拾うより先に外さない。
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ExportDialog({ subject, cards, onClose }: ExportDialogProps) {
  const { t } = useTranslation('cards')
  const [share, setShare] = useState<ExportShareMode>('shareable')
  const [lang, setLang] = useState<ExportLang>('ja')
  const [busy, setBusy] = useState(false)
  const [reasons, setReasons] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleCreate() {
    setBusy(true)
    setReasons(null)
    setError(null)
    try {
      const { blob, filename } = await exportSubjectAgent(
        subject,
        cards.map((c) => ({ card_id: c.card_id, tool: c.tool, params: c.params })),
        share,
        lang,
      )
      saveBlob(blob, filename)
      onClose()
    } catch (e) {
      if (e instanceof NotShareableExportError) {
        setReasons(e.reasons.length > 0 ? e.reasons : ['no_materials'])
      } else {
        setError(t('export.error'))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="export-overlay" onClick={onClose}>
      <div
        className="export-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t('export.title')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="export-modal-head">
          <h3>{t('export.title')}</h3>
          <button type="button" className="export-modal-close" onClick={onClose} aria-label={t('export.cancel')}>
            ×
          </button>
        </div>
        <fieldset className="export-modal-field">
          <legend>{t('export.share_label')}</legend>
          <label className="export-modal-radio">
            <input
              type="radio"
              name="export-share"
              checked={share === 'shareable'}
              onChange={() => setShare('shareable')}
            />
            {t('export.share_shareable')}
          </label>
          <label className="export-modal-radio">
            <input type="radio" name="export-share" checked={share === 'full'} onChange={() => setShare('full')} />
            {t('export.share_full')}
          </label>
        </fieldset>
        <fieldset className="export-modal-field">
          <legend>{t('export.lang_label')}</legend>
          <label className="export-modal-radio">
            <input type="radio" name="export-lang" checked={lang === 'ja'} onChange={() => setLang('ja')} />
            {t('export.lang_ja')}
          </label>
          <label className="export-modal-radio">
            <input type="radio" name="export-lang" checked={lang === 'en'} onChange={() => setLang('en')} />
            {t('export.lang_en')}
          </label>
        </fieldset>
        {reasons && (
          <p className="export-modal-error" role="alert">
            {t('export.not_shareable', { reasons: formatShareReasons(reasons, t) })}
          </p>
        )}
        {error && (
          <p className="export-modal-error" role="alert">
            {error}
          </p>
        )}
        <div className="export-modal-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
            {t('export.cancel')}
          </button>
          <button
            type="button"
            className="btn btn--soft btn--sm"
            disabled={busy || cards.length === 0}
            onClick={handleCreate}
          >
            {busy ? t('export.building') : t('export.submit')}
          </button>
        </div>
      </div>
    </div>
  )
}
