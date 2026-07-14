import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// このインスタンスが新規設計データセットの名前空間を mint する IRI base
// （ADR instance-iri-base.md）。サーバ env ASTERISM_IRI_BASE で決まる read-only 情報 —
// 未設定（RFC 2606 の .invalid フォールバック）なら「公開できる識別子ではない」ことを
// ここで運用者に伝える。設計画面の骨格ゲートにも同じ namespace が出るが、
// 「どこで設定するか」はサーバ設定の話なので Settings が正位置。
interface InstanceInfo {
  iri_base: string
  iri_base_configured: boolean
}

export function InstanceSection() {
  const { t } = useTranslation('settings')
  const [info, setInfo] = useState<InstanceInfo | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/instance`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data: InstanceInfo) => {
        if (!cancelled) setInfo(data)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (failed) return null // 旧 api（エンドポイント無し）や到達不能では黙って非表示

  return (
    <section className="serverkeys">
      <h4 className="serverkeys-title">{t('instance.title')}</h4>
      <p className="field-help">{t('instance.intro')}</p>
      <div className="serverkey-row">
        <div className="serverkey-head">
          <span className="serverkey-name">{t('instance.name')}</span>
          <span className={`serverkey-status ${info?.iri_base_configured ? 'ok' : 'off'}`}>
            {info === null
              ? '…'
              : info.iri_base_configured
                ? t('serverKeys.set')
                : t('instance.unset')}
          </span>
        </div>
        {info && <code className="instance-iri-base">{info.iri_base}</code>}
        {info && !info.iri_base_configured && (
          <p className="field-help">{t('instance.unsetHelp')}</p>
        )}
      </div>
    </section>
  )
}
