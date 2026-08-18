import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from './context'
import { type InstanceInfo, fetchInstanceInfo } from './instanceApi'
import { ServerKeyError, setServerKey } from './serverKeysApi'
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
  const [detail, setDetail] = useState('')
  // Clearing here stops AI for everyone on this server and cannot be undone by
  // the person who did it (the secret is not theirs to retype), so it asks
  // first — the same two-step the far smaller "delete a model" already uses.
  const [confirmClear, setConfirmClear] = useState(false)

  async function submit(clear: boolean) {
    setBusy(true)
    setError('')
    setDetail('')
    try {
      await setServerKey(provider, clear ? '' : apiKey.trim(), needsBase ? apiBase.trim() : null)
      setApiKey('')
      setConfirmClear(false)
      refreshServerKeys()
    } catch (e) {
      const status = e instanceof ServerKeyError ? e.status : 0
      setError(t(`serverKeys.error.${saveErrorKind(status)}`))
      setDetail(e instanceof Error ? e.message : String(e))
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
        {isSet &&
          (confirmClear ? (
            <>
              <button
                type="button"
                className="btn btn--danger btn--sm"
                disabled={busy}
                onClick={() => submit(true)}
              >
                {t('serverKeys.confirmClear')}
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setConfirmClear(false)}
              >
                {t('cancel')}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={() => setConfirmClear(true)}
            >
              {t('serverKeys.clear')}
            </button>
          ))}
      </div>
      {confirmClear && <p className="field-help">{t('serverKeys.clearWarning')}</p>}
      {error && (
        <>
          <p className="field-help field-error">{error}</p>
          {detail && (
            <details>
              <summary className="field-help">{t('serverKeys.error.details')}</summary>
              <p className="field-help">{detail}</p>
            </details>
          )}
        </>
      )}
    </div>
  )
}

/** What the person can do about a failed save, from the HTTP status. */
function saveErrorKind(status: number): 'auth' | 'disabled' | 'badBase' | 'generic' {
  if (status === 401 || status === 403) return 'auth'
  if (status === 503) return 'disabled'
  if (status === 400) return 'badBase'
  return 'generic'
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

  // Folded away until it matters: this is the administrator's one-time job, and
  // left open it reads as another key the reader has to produce. Already in use
  // → open, so a setting that is actually in effect is never invisible.
  const anySet = PROVIDERS.some((p) => hasServerKey(p.id))

  return (
    <section className="serverkeys">
      <details open={anySet}>
        <summary className="serverkeys-title">{t('serverKeys.title')}</summary>
        <p className="field-help">{t('serverKeys.intro')}</p>
        {PROVIDERS.map((p) => (
          <ServerKeyRow
            key={p.id}
            provider={p.id}
            name={t(p.nameKey)}
            needsBase={p.needsApiBase}
          />
        ))}
      </details>
    </section>
  )
}
