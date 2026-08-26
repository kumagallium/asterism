import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type CheckResult,
  checkForUpdates,
  isTauri,
  pendingUpdate,
  UPDATE_AVAILABLE_EVENT,
  type UpdateAvailableDetail,
} from '../desktop/updater'
import { useUpdateInstall } from '../desktop/useUpdateInstall'
import { type InstanceInfo, fetchInstanceInfo } from './instanceApi'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// どのビルドが動いているか。デスクトップシェル（Tauri）が起動時に自分の版数を
// バックエンドへ env で渡し `/api/instance` が返すので、SPA は IPC なしで版数が分かる。
// サーバ/web インストールでは app_version=null＝更新確認そのものを出さない。
//
// 更新の 2 経路:
//  - Tauri の窓の中（isTauri）: updater プラグインを直接叩く。見つかった更新は
//    この場で入れられる（［再起動して更新］→ 何が中断されるかを言う → 実行）。
//    同じ更新は画面上部のバナーからも押せるが、実行は useUpdateInstall が
//    モジュールに 1 つだけ持つ＝二重には走らない。バナーを「あとで」で引っ込めた
//    あとの受け皿がここ（"laterTitle" が案内している先）。
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
  // 更新固有の文言は settings、確認 2 段めの文言は common（バナーと同じものを使う）
  const { t } = useTranslation(['settings', 'common'])
  const [info, setInfo] = useState<InstanceInfo | null>(null)
  const [check, setCheck] = useState<CheckState>({ status: 'idle' })
  // すでに見つかっている更新（起動時の自動確認・バナー経由）。設定を開いた時点で
  // 分かっていれば「今すぐ確認」を押さずにそのまま入れられる。
  const [update, setUpdate] = useState<UpdateAvailableDetail | null>(() => pendingUpdate())
  // 再起動は同梱のローカルサーバごと落ちる＝進行中の読み取り・取り込みを巻き込む。
  // 押した瞬間に実行せず、何が中断されるかを言ってからもう一度押してもらう。
  const [confirming, setConfirming] = useState(false)
  const install = useUpdateInstall()

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

  const { clearError } = install
  useEffect(() => {
    const onAvailable = (e: Event) => {
      setUpdate((e as CustomEvent<UpdateAvailableDetail>).detail)
      clearError()
    }
    window.addEventListener(UPDATE_AVAILABLE_EVENT, onAvailable)
    return () => window.removeEventListener(UPDATE_AVAILABLE_EVENT, onAvailable)
  }, [clearError])

  const tauri = isTauri()

  async function onCheck() {
    setCheck({ status: 'checking' })
    const result = await (tauri ? checkForUpdates() : checkViaApi())
    setCheck(result)
    // 最新に追いついていたら、前に見つけていた更新はここからも畳む
    if (tauri && result.status === 'up-to-date') {
      setUpdate(null)
      setConfirming(false)
    }
  }

  async function onInstall() {
    if (!update) return
    setConfirming(false)
    await install.run(update)
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
              disabled={check.status === 'checking' || install.installing}
            >
              {check.status === 'checking' ? t('about.checking') : t('about.checkNow')}
            </button>
            {check.status === 'up-to-date' && (
              <p className="field-help field-ok">{t('about.upToDate')}</p>
            )}

            {/* Tauri の窓の中＝ここで入れられる。見つかっているかぎり出しておく
                （「今すぐ確認」を押した直後も、起動時に見つかっていた場合も同じ形）。 */}
            {tauri && update && (
              <div className="about-update">
                <p className="field-help">{t('about.available', { version: update.version })}</p>
                {confirming ? (
                  <>
                    <p className="field-help field-warn">{t('common:updater.confirmText')}</p>
                    <div className="about-actions">
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setConfirming(false)}
                      >
                        {t('common:updater.confirmCancel')}
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={onInstall}
                        disabled={install.installing}
                      >
                        {t('common:updater.confirmGo')}
                      </button>
                    </div>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => (install.installing ? undefined : setConfirming(true))}
                    disabled={install.installing}
                  >
                    {install.label}
                  </button>
                )}
                {/* 生の Tauri/Rust エラー（英語）は本文に出さず title に退避する。 */}
                {install.error && (
                  <p className="field-help field-error" title={install.error}>
                    {t('common:updater.installFailed')}
                  </p>
                )}
              </div>
            )}

            {/* ブラウザから見ている場合は報告のみ＝更新はアプリ本体で行ってもらう。 */}
            {!tauri && check.status === 'available' && (
              <>
                <p className="field-help">{t('about.available', { version: check.version })}</p>
                <p className="field-help">{t('about.installHint')}</p>
              </>
            )}

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
