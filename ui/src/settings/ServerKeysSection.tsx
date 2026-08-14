import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from './context'
import { type InstanceInfo, fetchInstanceInfo } from './instanceApi'
import { setServerKey } from './serverKeysApi'
import { PROVIDERS } from './store'

// Admin section: register the instance-wide "shared" key server-side, so users
// don't have to enter one. Written via the write-gated POST /api/llm/server-keys
// (any logged-in user, same trust as the other write routes); the value is
// persisted server-side and never read back (we only ever see set/unset).
//
// This is a SHARED-instance feature (an authenticated deployment where one
// operator key serves everyone). On the desktop app there is nobody to share
// with — the per-connection key in the models tab already persists — so the
// section only clutters the "where do I put my key?" question. Hidden there
// unless a shared key is actually set (never make an already-effective key
// invisible), and hidden anywhere the write gate is shut (saving would 503).

function ServerKeyRow({
  provider,
  name,
  needsBase,
}: {
  provider: string
  name: string
  needsBase: boolean
}) {
  const { t } = useTranslation('settings')
  const { hasServerKey, refreshServerKeys } = useLlmSettings()
  const isSet = hasServerKey(provider)
  const [apiKey, setApiKey] = useState('')
  const [apiBase, setApiBase] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(clear: boolean) {
    setBusy(true)
    setError('')
    try {
      await setServerKey(provider, clear ? '' : apiKey.trim(), needsBase ? apiBase.trim() : null)
      setApiKey('')
      refreshServerKeys()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const canSave = !busy && apiKey.trim().length > 0 && (!needsBase || apiBase.trim().length > 0)

  return (
    <div className="serverkey-row">
      <div className="serverkey-head">
        <span className="serverkey-name">{name}</span>
        <span className={`serverkey-status ${isSet ? 'ok' : 'off'}`}>
          {isSet ? t('serverKeys.set') : t('serverKeys.unset')}
        </span>
      </div>
      {needsBase && (
        <input
          type="text"
          className="serverkey-base"
          placeholder={t('serverKeys.basePlaceholder')}
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
        />
      )}
      <div className="serverkey-controls">
        <input
          type="password"
          autoComplete="off"
          placeholder={t('serverKeys.keyPlaceholder')}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <button type="button" className="btn btn--ghost btn--sm" disabled={!canSave} onClick={() => submit(false)}>
          {t('serverKeys.save')}
        </button>
        {isSet && (
          <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => submit(true)}>
            {t('serverKeys.clear')}
          </button>
        )}
      </div>
      {error && <p className="field-help field-error">{error}</p>}
    </div>
  )
}

export function ServerKeysSection() {
  const { t } = useTranslation('settings')
  const { hasServerKey } = useLlmSettings()
  // undefined = 判定中（出さない）。null = 旧 api / 到達不能で、判断材料が無い
  // ので従来どおり出す。
  const [info, setInfo] = useState<InstanceInfo | null | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    fetchInstanceInfo().then((data) => {
      if (!cancelled) setInfo(data)
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (info === undefined) return null
  if (info) {
    // 保存が必ず 503 になる配備では出さない。
    if (info.write_gate === 'closed') return null
    // デスクトップは単一ユーザー。既に効いている共有キーがあるときだけ見せる。
    if (info.desktop && !PROVIDERS.some((p) => hasServerKey(p.id))) return null
  }

  return (
    <section className="serverkeys">
      <h4 className="serverkeys-title">{t('serverKeys.title')}</h4>
      <p className="field-help">{t('serverKeys.intro')}</p>
      {PROVIDERS.map((p) => (
        <ServerKeyRow key={p.id} provider={p.id} name={p.name} needsBase={p.needsApiBase} />
      ))}
    </section>
  )
}
