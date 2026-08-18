import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getAppDataInfo } from '../appdata'
import { isTauri } from '../desktop/updater'

// ストレージタブ — Graphium と同じ「保存先フォルダを選べる」機能のデスクトップ版。
// Tauri 側の IPC（get_data_home_override / set_data_home_override）は実装済み
// （desktop/src-tauri/src/settings.rs）。保存だけで再起動はしない＝次回起動から
// 効く。フォルダ選択は @tauri-apps/plugin-dialog（`dialog:allow-open` のみ許可、
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

export function StorageTab() {
  const { t } = useTranslation('settings')
  const appData = getAppDataInfo()
  const tauri = isTauri()

  const [override, setOverride] = useState<string | null | undefined>(undefined)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [syncWarning, setSyncWarning] = useState(false)

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

  // 共有ブラウザ版（複数ユーザーが同じ api を見ている）ではタブ自体を出さない。
  if (appData?.singleUser !== true) return null

  async function saveOverride(path: string | null) {
    setBusy(true)
    setError('')
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('set_data_home_override', { path })
      setOverride(path)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onChange() {
    setSyncWarning(false)
    setError('')
    try {
      const { open } = await import('@tauri-apps/plugin-dialog')
      const selected = await open({ directory: true })
      if (typeof selected !== 'string') return
      if (looksLikeSyncFolder(selected)) setSyncWarning(true)
      await saveOverride(selected)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const currentHome = appData?.home ?? null
  const pendingHome = override ?? null
  // override は「保存はしたが未確認（読み込み中）」を undefined、「未設定」を
  // null で表す。次回起動先の表示・食い違い判定は override が読み込まれてから。
  const overrideKnown = override !== undefined
  const restartPending = overrideKnown && pendingHome !== null && pendingHome !== currentHome

  return (
    <div className="storage-tab">
      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('storage.title')}</h4>
        <p className="field-help">{t('storage.intro')}</p>

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

        {restartPending && (
          <p className="field-help field-warn">
            {t('storage.restartPending', { path: pendingHome })}
          </p>
        )}

        {syncWarning && <p className="field-help field-warn">{t('storage.syncWarning')}</p>}

        {/* Only where the folder can actually be changed — on a screen that
            cannot change it, a note about migrating data is just noise. */}
        {tauri && <p className="field-help">{t('storage.migrateNote')}</p>}

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
