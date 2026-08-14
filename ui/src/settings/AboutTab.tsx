import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// どのビルドが動いているか。デスクトップシェル（Tauri）が起動時に自分の版数を
// バックエンドへ env で渡すので、窓が remote http://127.0.0.1 オリジンでも
// （＝Tauri IPC に繋がなくても）SPA から版数が分かる。
// サーバ/web インストールでは app_version=null＝更新確認そのものを出さない。
interface InstanceInfo {
  app_version: string | null
  desktop: boolean
}

interface UpdateCheck {
  current: string
  latest: string
  update_available: boolean
}

type CheckState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'up-to-date' }
  | { status: 'available'; version: string }
  | { status: 'error'; message: string }

export function AboutTab() {
  const { t } = useTranslation('settings')
  const [info, setInfo] = useState<InstanceInfo | null>(null)
  const [check, setCheck] = useState<CheckState>({ status: 'idle' })

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/instance`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data: InstanceInfo) => {
        if (!cancelled) setInfo(data)
      })
      .catch(() => {
        // 旧 api / 到達不能: 版数は「—」のまま（推測で埋めない）
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function onCheck() {
    setCheck({ status: 'checking' })
    try {
      const res = await fetch(`${API_BASE}/api/desktop/update-check`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: UpdateCheck = await res.json()
      setCheck(
        data.update_available
          ? { status: 'available', version: data.latest }
          : { status: 'up-to-date' },
      )
    } catch (e) {
      setCheck({ status: 'error', message: e instanceof Error ? e.message : String(e) })
    }
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
            {check.status === 'available' && (
              <>
                <p className="field-help">{t('about.available', { version: check.version })}</p>
                {/* ダウンロード/差し替えはネイティブ側（起動時の自動確認＋メニュー
                    「アップデートを確認…」）が署名検証つきで行う。ここは知らせる係。 */}
                <p className="field-help">{t('about.installHint')}</p>
              </>
            )}
            {check.status === 'error' && (
              <p className="field-help field-error">
                {t('about.checkFailed')} — {check.message}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  )
}
