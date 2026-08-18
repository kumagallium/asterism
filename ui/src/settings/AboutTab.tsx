import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type CheckResult, checkForUpdates, isTauri } from '../desktop/updater'
import { type InstanceInfo, fetchInstanceInfo } from './instanceApi'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// どのビルドが動いているか。デスクトップシェル（Tauri）が起動時に自分の版数を
// バックエンドへ env で渡し `/api/instance` が返すので、SPA は IPC なしで版数が分かる。
// サーバ/web インストールでは app_version=null＝更新確認そのものを出さない。
//
// 更新確認の 2 経路:
//  - Tauri の窓の中（isTauri）: updater プラグインを直接叩く（Graphium と同じ）。
//    見つかれば画面上部のバナーが「再起動して更新」を出す＝ここは知らせる係。
//  - デスクトップのバックエンドを普通のブラウザで開いている場合（desktop だが
//    IPC なし）: api の /api/desktop/update-check で版だけ比べる（報告のみ）。
//    更新はアプリ本体で行う旨を案内する。

interface HttpUpdateCheck {
  current: string
  latest: string
  update_available: boolean
}

type CheckState = { status: 'idle' } | { status: 'checking' } | CheckResult

async function checkViaApi(): Promise<CheckResult> {
  try {
    const res = await fetch(`${API_BASE}/api/desktop/update-check`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: HttpUpdateCheck = await res.json()
    return data.update_available
      ? { status: 'available', version: data.latest }
      : { status: 'up-to-date' }
  } catch (e) {
    return { status: 'error', message: e instanceof Error ? e.message : String(e) }
  }
}

export function AboutTab() {
  const { t } = useTranslation('settings')
  const [info, setInfo] = useState<InstanceInfo | null>(null)
  const [check, setCheck] = useState<CheckState>({ status: 'idle' })

  useEffect(() => {
    let cancelled = false
    // 旧 api / 到達不能なら null＝版数は「—」のまま（推測で埋めない）
    fetchInstanceInfo().then((data) => {
      if (!cancelled && data) setInfo(data)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const tauri = isTauri()

  async function onCheck() {
    setCheck({ status: 'checking' })
    setCheck(await (tauri ? checkForUpdates() : checkViaApi()))
  }

  const desktop = info?.desktop ?? false

  return (
    <div className="about-tab">
      <section className="serverkeys">
        <h4 className="serverkeys-title">{t('about.title')}</h4>
        <div className="serverkey-row">
          <div className="about-row">
            <span className="about-label">{t('about.appName')}</span>
            <span className="about-value">Asterism</span>
          </div>
          <div className="about-row">
            <span className="about-label">{t('about.version')}</span>
            <code className="about-value">{info?.app_version ?? '—'}</code>
          </div>
        </div>
      </section>

      <section className="serverkeys">
        <h4 className="serverkeys-title">{t('about.updates')}</h4>
        <p className="field-help">{desktop ? t('about.autoCheckNote') : t('about.webNote')}</p>
        {desktop && (
          <>
            <button
              type="button"
              className="btn btn--sm"
              onClick={onCheck}
              disabled={check.status === 'checking'}
            >
              {check.status === 'checking' ? t('about.checking') : t('about.checkNow')}
            </button>
            {check.status === 'up-to-date' && (
              <p className="field-help field-ok">{t('about.upToDate')}</p>
            )}
            {check.status === 'available' &&
              (tauri ? (
                // ダウンロード/差し替え/再起動は画面上部のバナーの「再起動して更新」。
                <p className="field-help">{t('about.availableBanner', { version: check.version })}</p>
              ) : (
                <>
                  <p className="field-help">{t('about.available', { version: check.version })}</p>
                  <p className="field-help">{t('about.installHint')}</p>
                </>
              ))}
            {/* "HTTP 500" told nobody what to do next. The next move (network,
                try again) is the message; the raw cause stays in the tooltip
                for whoever is debugging. */}
            {(check.status === 'error' || check.status === 'unsupported') && (
              <p
                className="field-help field-error"
                title={check.status === 'error' ? check.message : undefined}
              >
                {t('about.checkFailed')}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  )
}
