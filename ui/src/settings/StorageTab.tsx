import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getAppDataInfo } from '../appdata'
import { isTauri } from '../desktop/updater'

// ストレージタブ — Graphium と同じ「保存先フォルダを選べる」機能のデスクトップ版。
// Tauri 側の IPC（get_data_home_override / set_data_home_override /
// get_storage_notice / clear_storage_notice）は実装済み（desktop/src-tauri/src/settings.rs）。
// 保存だけで再起動はしない＝次回起動から効く。実際のデータ移行は次回起動時、
// サイドカーを立ち上げる前に Tauri 側が行う（稼働中は動かせない）。
// フォルダ選択は @tauri-apps/plugin-dialog（`dialog:allow-open` のみ許可、
// save/message は呼べない）。
//
// Graphium は同期フォルダ（Dropbox 等）を指定すればデバイス間同期できると勧め
// ているが、Asterism はバイナリのグラフストア（Oxigraph）を抱えているため
// 同期とは相性が悪い。選択自体は許すが、同期フォルダらしいパスには警告を出す
// （勧める文言は書かない）。

/** パス文字列に同期フォルダらしい語が含まれるか（大文字小文字を無視）。 */
const SYNC_FOLDER_MARKERS = [
  'dropbox',
  'google drive',
  'googledrive',
  'onedrive',
  'library/mobile documents', // iCloud
  'icloud drive',
  'box sync',
  'nextcloud',
  'syncthing',
  'pcloud',
]

function looksLikeSyncFolder(path: string): boolean {
  const lower = path.toLowerCase()
  return SYNC_FOLDER_MARKERS.some((marker) => lower.includes(marker))
}

type StorageNoticeKind = 'moved' | 'copied' | 'failed'

interface StorageNotice {
  kind: StorageNoticeKind
  from: string
  to: string
  detail: string
}

export function StorageTab() {
  const { t } = useTranslation('settings')
  const appData = getAppDataInfo()
  const tauri = isTauri()

  const [override, setOverride] = useState<string | null | undefined>(undefined)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [syncWarning, setSyncWarning] = useState(false)
  // フォルダを選んだ直後〜確定操作までの「移すか」確認待ち。
  const [pendingSelection, setPendingSelection] = useState<string | null>(null)
  const [migrateChecked, setMigrateChecked] = useState(true)
  // 直近の変更で移行を予約したか（再起動待ちメッセージの出し分け用）。
  const [migratePending, setMigratePending] = useState(false)
  // 前回起動時の移行結果。
  const [notice, setNotice] = useState<StorageNotice | null>(null)

  useEffect(() => {
    if (!tauri) return
    let cancelled = false
    ;(async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core')
        const value = await invoke<string | null>('get_data_home_override')
        if (!cancelled) setOverride(value)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [tauri])

  useEffect(() => {
    if (!tauri) return
    let cancelled = false
    ;(async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core')
        const value = await invoke<StorageNotice | null>('get_storage_notice')
        if (!cancelled && value) {
          setNotice(value)
          // サーバ側は読んだ時点で消す。画面には閉じるまで表示し続ける
          // （少なくとも一度は描画されているので、消えても再度は出ない）。
          invoke('clear_storage_notice').catch(() => {})
        }
      } catch {
        // 通知の取得失敗は致命的ではない（黙って出さない）。
      }
    })()
    return () => {
      cancelled = true
    }
  }, [tauri])

  // 共有ブラウザ版（複数ユーザーが同じ api を見ている）ではタブ自体を出さない。
  if (appData?.singleUser !== true) return null

  async function saveOverride(path: string | null, moveFrom: string | null = null) {
    setBusy(true)
    setError('')
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('set_data_home_override', { path, moveFrom })
      setOverride(path)
      setMigratePending(Boolean(moveFrom))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onChange() {
    setSyncWarning(false)
    setError('')
    setPendingSelection(null)
    try {
      const { open } = await import('@tauri-apps/plugin-dialog')
      const selected = await open({ directory: true })
      if (typeof selected !== 'string') return
      setSyncWarning(looksLikeSyncFolder(selected))
      setMigrateChecked(true)
      setPendingSelection(selected)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function confirmChange() {
    if (!pendingSelection) return
    const moveFrom = migrateChecked ? (currentHome ?? null) : null
    await saveOverride(pendingSelection, moveFrom)
    setPendingSelection(null)
  }

  function cancelChange() {
    setPendingSelection(null)
    setSyncWarning(false)
  }

  const currentHome = appData?.home ?? null
  const pendingHome = override ?? null
  // override は「保存はしたが未確認（読み込み中）」を undefined、「未設定」を
  // null で表す。次回起動先の表示・食い違い判定は override が読み込まれてから。
  const overrideKnown = override !== undefined
  const restartPending = overrideKnown && pendingHome !== null && pendingHome !== currentHome

  const noticeClass =
    notice?.kind === 'failed' ? 'field-error' : notice?.kind === 'copied' ? 'field-warn' : 'field-ok'

  return (
    <div className="storage-tab">
      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('storage.title')}</h4>
        <p className="field-help">{t('storage.intro')}</p>

        {notice && (
          <div className={`field-help storage-notice ${noticeClass}`}>
            <span>
              {notice.kind === 'moved' && t('storage.noticeMoved', { to: notice.to })}
              {notice.kind === 'copied' && t('storage.noticeCopied', { from: notice.from, to: notice.to })}
              {notice.kind === 'failed' && t('storage.noticeFailed', { detail: notice.detail })}
            </span>
            <button
              type="button"
              className="btn btn--ghost btn--sm storage-notice-close"
              onClick={() => setNotice(null)}
            >
              {t('close')}
            </button>
          </div>
        )}

        <div className="serverkey-row storage-current">
          <div className="storage-current-info">
            <span className="about-label">{t('storage.currentLabel')}</span>
            <code className="about-value storage-path">{currentHome ?? t('storage.unknown')}</code>
          </div>
          {tauri && (
            <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={onChange}>
              {t('storage.change')}
            </button>
          )}
        </div>

        {!tauri && <p className="field-help">{t('storage.browserNote')}</p>}

        {pendingSelection && (
          <div className="storage-confirm">
            <p className="field-help">{t('storage.confirmIntro', { path: pendingSelection })}</p>
            <label className="storage-confirm-checkbox">
              <input
                type="checkbox"
                checked={migrateChecked}
                onChange={(e) => setMigrateChecked(e.target.checked)}
              />
              {t('storage.migrateCheckbox')}
            </label>
            <p className="field-help">{t('storage.migrateCheckboxHelp')}</p>
            {syncWarning && <p className="field-help field-warn">{t('storage.syncWarning')}</p>}
            <div className="storage-confirm-actions">
              <button type="button" className="btn btn--sm" disabled={busy} onClick={confirmChange}>
                {t('storage.confirmChange')}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={cancelChange}>
                {t('cancel')}
              </button>
            </div>
          </div>
        )}

        {restartPending && (
          <p className="field-help field-warn">
            {t('storage.restartPending', { path: pendingHome })}
            {migratePending && ` ${t('storage.restartPendingMigrate')}`}
          </p>
        )}

        {/* Only where the folder can actually be changed — on a screen that
            cannot change it, a note about migrating data is just noise. Also
            skipped when a migration was already scheduled (would contradict it). */}
        {/* Not while a choice is on screen: the panel above offers to move the
            data, so "it is not moved automatically" would contradict it. */}
        {tauri && !migratePending && pendingSelection === null && (
          <p className="field-help">{t('storage.migrateNote')}</p>
        )}

        {error && <p className="field-help field-error">{error}</p>}

        {tauri && overrideKnown && pendingHome !== null && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={busy}
            onClick={() => saveOverride(null)}
          >
            {t('storage.resetDefault')}
          </button>
        )}
      </section>
    </div>
  )
}
