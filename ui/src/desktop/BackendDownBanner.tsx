// バックエンド停止のお知らせバナー（デスクトップ版のみ・画面最上部の全幅ストリップ）。
// backend.ts が死活監視し、down と分かるとここが
// 「バックエンドが止まっています […] ［バックエンドを再起動］」を出す。
//
// UpdateBanner と同じ位置・同じ作りだが、これは「今でなくてよい」お知らせではない
// （止まっている間は API が全滅＝画面の内容が古いまま固まる）ので、［あとで］は無い。
// 自動再起動はしない（死因不明のまま再起動を繰り返すと失敗にも気づけない）— 気づかせて
// 押したら直す、に留める。押して直れば SPA 自体もそのバックエンドから配信されている
// ので、location.reload() で読み直す。

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getBackendState, restartBackend, subscribeBackendState, type BackendState } from './backend'

export function BackendDownBanner() {
  const { t } = useTranslation('settings')
  const [state, setState] = useState<BackendState>(() => getBackendState())
  const [restarting, setRestarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => subscribeBackendState(setState), [])

  async function onRestart() {
    setRestarting(true)
    setError(null)
    try {
      await restartBackend()
      window.location.reload()
    } catch (e) {
      setRestarting(false)
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (state !== 'down') return null

  return (
    <div className="backend-down-banner" role="alert" aria-live="assertive">
      <span className="backend-down-banner-text">{t('backendDown.message')}</span>
      <span className="backend-down-banner-actions">
        <button type="button" className="btn btn--sm" onClick={onRestart} disabled={restarting}>
          {restarting ? t('backendDown.restarting') : t('backendDown.restart')}
        </button>
      </span>
      {/* 生の Tauri/Rust エラー（英語）は本文に出さず title に退避する — UpdateBanner と同じ流儀。 */}
      {error && (
        <span className="backend-down-banner-error" title={error}>
          {t('backendDown.restartFailed')}
        </span>
      )}
    </div>
  )
}
