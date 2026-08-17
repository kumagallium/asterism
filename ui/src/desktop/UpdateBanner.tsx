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
  const { t } = useTranslation('settings')
  // マウント前に見つかっていた更新も拾う（イベントは一度きりなので）
  const [update, setUpdate] = useState<UpdateAvailableDetail | null>(() => pendingUpdate())
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<DownloadProgress | null>(null)
  const [error, setError] = useState<Failure | null>(null)

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
        {t('updater.available', { version: update.version })}
      </span>
      <span className="update-banner-actions">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onRecheck}
          disabled={busy}
        >
          {phase === 'rechecking' ? t('about.checking') : t('about.checkNow')}
        </button>
        <button type="button" className="btn btn--sm" onClick={onInstall} disabled={busy}>
          {installLabel}
        </button>
      </span>
      {error && (
        <span className="update-banner-error">
          {error.step === 'install' ? t('updater.installFailed') : t('about.checkFailed')} —{' '}
          {error.message}
        </span>
      )}
    </div>
  )
}
