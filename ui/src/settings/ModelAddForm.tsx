import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { initAppData } from '../appdata'
import { useLlmSettings } from './context'
import { type InstanceInfo, fetchInstanceInfo } from './instanceApi'
import { fetchAvailableModels } from './modelsApi'
import { ServerKeyError, setServerKey } from './serverKeysApi'
import {
  LOCAL_AI_BASE,
  LOCAL_AI_KEY,
  type Provider,
  makeModel,
  providerOfPastedKey,
} from './store'

// Adding an AI — the ONE registration form, used both for the very first one
// (the list is empty, so this is open) and for every later one.
//
// It used to be two forms with a door between them: a first-run screen offering
// "paste a key" and, behind 「くわしい設定」, an older form asking provider, name,
// model id and address up front. They were the same operation with different
// amounts of typing, and a third shape of it lived in the 「このサーバ」 tab.
// Someone reading the first screen could reasonably ask 「キーを貼るってなんで
// したっけ？」 — the name described a gesture, not what it did (2026-08-20).
//
// One form, and it asks only what it could not work out: the key names its own
// provider, so a public service needs nothing else; a custom endpoint still
// needs its address; an endpoint running several models is asked about only
// once it has said what it runs.

type Choice = 'anthropic' | 'openai' | 'other'

export function ModelAddForm({
  onDone,
  onCancel,
}: {
  /** A registration landed. */
  onDone: () => void
  /** Given only when there is something to go back TO (the list is not empty). */
  onCancel?: () => void
}) {
  const { t } = useTranslation('settings')
  const settings = useLlmSettings()
  const [info, setInfo] = useState<InstanceInfo | null>(null)
  // Null until the check resolves; treated as "not shared" so the block never
  // flashes in on a desktop app before disappearing.
  const [shared, setShared] = useState(false)

  const [key, setKey] = useState('')
  const [choice, setChoice] = useState<Choice | null>(null)
  const [base, setBase] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [localFailed, setLocalFailed] = useState(false)
  // The models a custom endpoint reported. Taking `[0]` silently picked one of
  // several on the person's behalf, at the one moment the choice had become
  // answerable — the endpoint had just told us what it runs (2026-08-20).
  const [choices, setChoices] = useState<{ id: string; label: string }[] | null>(null)
  // 「みんなで使う」: the key goes to the SERVER instead of this browser, so
  // everyone on it can use the AI without bringing their own. Offered only where
  // it means something — a shared server — and only to someone allowed to write
  // there; the value never comes back to any browser. The store, the endpoint
  // and the pinned-base rule are the ones that were already there: this moves
  // WHERE the setting is made, not how it is kept.
  const [share, setShare] = useState(false)
  const canShare = shared && info?.write_gate === 'authorized'

  useEffect(() => {
    let cancelled = false
    fetchInstanceInfo().then((data) => {
      if (!cancelled && data) setInfo(data)
    })
    initAppData().then((data) => {
      if (!cancelled) setShared(!data.singleUser)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const trimmed = key.trim()
  const detected = providerOfPastedKey(trimmed)
  // Only ask which service the key belongs to when we cannot tell.
  const mustChoose = trimmed.length > 0 && detected === null
  const provider: Provider | null = detected ?? (choice === 'other' ? 'openai-compatible' : choice)
  const needsBase = provider === 'openai-compatible'
  const canSave =
    !busy && trimmed.length > 0 && provider !== null && (!needsBase || base.trim().length > 0)

  async function register(p: Provider, modelId: string, apiBase: string | null, apiKey: string) {
    const created = makeModel({
      provider: p,
      name: modelId || t(`provider.${p === 'openai-compatible' ? 'openaiCompatible' : p}`),
      modelId,
      apiBase,
    })
    if (canShare && share && apiKey) {
      // Server-side: the api resolves it for anyone who brings none of their
      // own, so the model entry needs no key at all.
      await setServerKey(p, apiKey, apiBase)
      settings.addModel(created)
    } else {
      settings.addModel(created)
      if (apiKey) settings.setKeyForModel(created, apiKey, true)
    }
    onDone()
  }

  async function saveKey() {
    if (!canSave || !provider) return
    setBusy(true)
    setError('')
    try {
      // Custom endpoints have endpoint-specific model ids, so ask the endpoint
      // itself rather than guessing one that would fail at call time. For the
      // two public services the id comes from the server (never hardcoded
      // here); an empty one is fine and means "whatever the server runs by
      // default", which is what the call then uses.
      if (needsBase) {
        const found = await fetchAvailableModels(provider, trimmed, base.trim())
        if (found.length === 0) {
          setError(t('setup.own.noModels'))
          return
        }
        if (found.length > 1) {
          // Ask now: the endpoint has answered, so the question is one the
          // person can actually answer. The key stays in the box until they do.
          setChoices(found.map((m) => ({ id: m.id, label: m.id })))
          return
        }
        await register(provider, found[0].id, base.trim(), trimmed)
      } else {
        // The two public services: the id comes from the server (never
        // hardcoded here); an empty one means "whatever the server runs by
        // default", which is what the call then uses.
        await register(provider, settings.serverDefaultModels[provider] ?? '', null, trimmed)
      }
      setKey('')
    } catch (e) {
      setError(e instanceof ServerKeyError ? e.message : t('setup.own.failed'))
    } finally {
      setBusy(false)
    }
  }

  async function useLocalAi() {
    setBusy(true)
    setLocalFailed(false)
    try {
      const models = await fetchAvailableModels('openai-compatible', LOCAL_AI_KEY, LOCAL_AI_BASE)
      const first = models[0]?.id
      if (!first) {
        setLocalFailed(true)
        return
      }
      await register('openai-compatible', first, LOCAL_AI_BASE, LOCAL_AI_KEY)
    } catch {
      setLocalFailed(true)
    } finally {
      setBusy(false)
    }
  }

  async function pickModel(modelId: string) {
    if (!provider) return
    setBusy(true)
    try {
      await register(provider, modelId, base.trim(), trimmed)
      setChoices(null)
      setKey('')
    } catch (e) {
      setError(e instanceof ServerKeyError ? e.message : t('setup.own.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ai-setup">

      <section className="serverkeys">
        <h4 className="serverkeys-title">{t('setup.own.title')}</h4>
        <p className="field-help">{t('setup.own.body')}</p>
        <div className="serverkey-row">
          {mustChoose && (
            <div
              className="settings-seg settings-seg--block"
              role="group"
              aria-label={t('setup.own.which')}
            >
              {(['anthropic', 'openai', 'other'] as Choice[]).map((c) => (
                <button
                  key={c}
                  type="button"
                  className={choice === c ? 'active' : ''}
                  onClick={() => setChoice(c)}
                >
                  {t(`setup.own.choice.${c}`)}
                </button>
              ))}
            </div>
          )}
          {needsBase && (
            <input
              type="text"
              className="serverkey-base"
              placeholder={t('setup.own.basePlaceholder')}
              value={base}
              onChange={(e) => setBase(e.target.value)}
            />
          )}
          <div className="serverkey-controls">
            <input
              type="password"
              autoComplete="off"
              placeholder={t('setup.own.placeholder')}
              value={key}
              onChange={(e) => {
                setKey(e.target.value)
                setError('')
              }}
            />
            <button type="button" className="btn btn--sm" disabled={!canSave} onClick={saveKey}>
              {busy ? t('setup.working') : t('setup.own.save')}
            </button>
          </div>
          {canShare && (
            <>
              <label className="field-check">
                <input
                  type="checkbox"
                  checked={share}
                  onChange={(e) => setShare(e.target.checked)}
                />
                {t('setup.own.share')}
              </label>
              <p className="field-help">{t('setup.own.shareHelp')}</p>
            </>
          )}
          {error && <p className="field-help field-error">{error}</p>}
          {choices && (
            <div className="ai-setup-models">
              <p className="field-help">{t('setup.own.pickModel')}</p>
              <div
                className="settings-seg settings-seg--block"
                role="group"
                aria-label={t('setup.own.pickModel')}
              >
                {choices.map((m) => (
                  <button key={m.id} type="button" onClick={() => pickModel(m.id)}>
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        {onCancel && (
          <p className="field-help">
            <button type="button" className="linklike" onClick={onCancel}>
              {t('setup.cancel')}
            </button>
          </p>
        )}
      </section>

      {info?.desktop && (
        <section className="serverkeys">
          <h4 className="serverkeys-title">{t('setup.local.title')}</h4>
          <p className="field-help">{t('setup.local.body')}</p>
          <button type="button" className="btn btn--sm" disabled={busy} onClick={useLocalAi}>
            {busy ? t('setup.working') : t('setup.local.use')}
          </button>
          {localFailed && <p className="field-help field-error">{t('setup.local.failed')}</p>}
        </section>
      )}

    </div>
  )
}
