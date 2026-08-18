// アプリ更新のお知らせバナー（デスクトップ版のみ・画面最上部の全幅ストリップ）。
// updater.ts が更新を見つけると CustomEvent で知らせ、ここが
// 「Asterism X.Y.Z が利用できます ［今すぐ確認］［再起動して更新］」を出す。
// Graphium の UpdateBanner と同じ役割・同じ 2 ボタン構成。
//
// バナーは見つけた時点の版を持ち続けるので、表示中に次のリリースが出ると古い版を
// 案内し続ける。「今すぐ確認」で取り直せる（新しい版が見つかれば同じイベントで
// 差し替わり、最新に追いついていればバナーを閉じる）。

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  checkForUpdates,
  type DownloadProgress,
  pendingUpdate,
  UPDATE_AVAILABLE_EVENT,
  type UpdateAvailableDetail,
} from './updater'

type Phase = 'idle' | 'rechecking' | 'installing'
interface Failure {
  step: 'check' | 'install'
  message: string
}

export function UpdateBanner() {
  // 更新固有の文言は settings、確認 2 段めの文言は common（settings.json は別 chain 所有）
  const { t } = useTranslation(['settings', 'common'])
  // マウント前に見つかっていた更新も拾う（イベントは一度きりなので）
  const [update, setUpdate] = useState<UpdateAvailableDetail | null>(() => pendingUpdate())
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<DownloadProgress | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  // 再起動はデスクトップ版では同梱のローカルサーバごと落ちる＝ S3「AI が読んでいます」
  // や S5「取り込み中」を巻き込む。押した瞬間に実行せず、何が中断されるかを言ってから
  // もう一度押してもらう（K10 と同じ「確認は 1 本」の形）。
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    const onAvailable = (e: Event) => {
      setUpdate((e as CustomEvent<UpdateAvailableDetail>).detail)
      setError(null)
    }
    window.addEventListener(UPDATE_AVAILABLE_EVENT, onAvailable)
    return () => window.removeEventListener(UPDATE_AVAILABLE_EVENT, onAvailable)
  }, [])

  async function onInstall() {
    if (!update) return
    setConfirming(false)
    setPhase('installing')
    setError(null)
    setProgress(null)
    try {
      // 成功すればアプリが再起動する＝ここには戻らない
      await update.install(setProgress)
    } catch (e) {
      setError({ step: 'install', message: e instanceof Error ? e.message : String(e) })
      setPhase('idle')
    }
  }

  async function onRecheck() {
    setPhase('rechecking')
    setError(null)
    try {
      const result = await checkForUpdates()
      if (result.status === 'up-to-date') setUpdate(null)
      else if (result.status === 'error') setError({ step: 'check', message: result.message })
    } finally {
      setPhase((p) => (p === 'rechecking' ? 'idle' : p))
    }
  }

  if (!update) return null

  const busy = phase !== 'idle'
  const pct =
    progress && progress.total
      ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100))
      : null
  const installLabel =
    phase === 'installing'
      ? pct === null
        ? t('updater.installing')
        : t('updater.downloading', { pct })
      : t('updater.install')

  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner-text">
        {confirming
          ? t('common:updater.confirmText')
          : t('updater.available', { version: update.version })}
      </span>
      <span className="update-banner-actions">
        {confirming ? (
          <>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setConfirming(false)}
            >
              {t('common:updater.confirmCancel')}
            </button>
            <button type="button" className="btn btn--sm" onClick={onInstall} disabled={busy}>
              {t('common:updater.confirmGo')}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onRecheck}
              disabled={busy}
            >
              {phase === 'rechecking' ? t('about.checking') : t('about.checkNow')}
            </button>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => (phase === 'installing' ? undefined : setConfirming(true))}
              disabled={busy}
            >
              {installLabel}
            </button>
          </>
        )}
      </span>
      {/* 生の Tauri/Rust エラー（英語）は本文に出さず title に退避する — 共有シェルの
          通常表示は平易文だけにし、次の一手をその文が持つ。 */}
      {error && (
        <span className="update-banner-error" title={error.message}>
          {error.step === 'install'
            ? t('common:updater.installFailed')
            : t('common:updater.checkFailed')}
        </span>
      )}
    </div>
  )
}
