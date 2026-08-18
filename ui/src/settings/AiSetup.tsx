import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from './context'
import { type InstanceInfo, fetchInstanceInfo } from './instanceApi'
import { fetchAvailableModels } from './modelsApi'
import {
  LOCAL_AI_BASE,
  LOCAL_AI_KEY,
  type Provider,
  makeModel,
  providerOfPastedKey,
} from './store'

// First run: what a person who has never set up an AI service sees instead of
// the model registry. Three ways out, each one thing to do — ask an
// administrator, paste a key, or use the AI already on this computer — and the
// full form one link away for anyone who wants it. Reached only when nothing is
// configured at all: a server-side shared key makes this screen unnecessary.

type Choice = 'anthropic' | 'openai' | 'other'

export function AiSetup({ onAdvanced }: { onAdvanced: () => void }) {
  const { t } = useTranslation('settings')
  const settings = useLlmSettings()
  const [info, setInfo] = useState<InstanceInfo | null>(null)

  const [copied, setCopied] = useState(false)
  // A page served over plain http (a lab machine on the LAN) has no clipboard
  // API, and a copy can be refused. Showing the text is the fallback — the
  // point is that the person can hand the request to their administrator.
  const [showTemplate, setShowTemplate] = useState(false)
  const [key, setKey] = useState('')
  const [choice, setChoice] = useState<Choice | null>(null)
  const [base, setBase] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [localFailed, setLocalFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchInstanceInfo().then((data) => {
      if (!cancelled && data) setInfo(data)
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

  function register(p: Provider, modelId: string, apiBase: string | null, apiKey: string) {
    const created = makeModel({
      provider: p,
      name: modelId || t(`provider.${p === 'openai-compatible' ? 'openaiCompatible' : p}`),
      modelId,
      apiBase,
    })
    settings.addModel(created)
    if (apiKey) settings.setKeyForModel(created, apiKey, true)
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
      const modelId = needsBase
        ? ((await fetchAvailableModels(provider, trimmed, base.trim()))[0]?.id ?? '')
        : (settings.serverDefaultModels[provider] ?? '')
      if (needsBase && !modelId) {
        setError(t('setup.own.noModels'))
        return
      }
      register(provider, modelId, needsBase ? base.trim() : null, trimmed)
      setKey('')
    } catch {
      setError(t('setup.own.failed'))
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
      register('openai-compatible', first, LOCAL_AI_BASE, LOCAL_AI_KEY)
    } catch {
      setLocalFailed(true)
    } finally {
      setBusy(false)
    }
  }

  function copyRequest() {
    const done = navigator.clipboard?.writeText(t('setup.ask.template'))
    if (!done) {
      setShowTemplate(true)
      return
    }
    done.then(
      () => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      },
      () => setShowTemplate(true),
    )
  }

  return (
    <div className="ai-setup">
      <p className="settings-intro">{t('setup.intro')}</p>

      <section className="serverkeys">
        <h4 className="serverkeys-title">{t('setup.ask.title')}</h4>
        <p className="field-help">{t('setup.ask.body')}</p>
        <button type="button" className="btn btn--ghost btn--sm" onClick={copyRequest}>
          {copied ? t('setup.ask.copied') : t('setup.ask.copy')}
        </button>
        {showTemplate && (
          <>
            <p className="field-help field-error">{t('setup.ask.copyFailed')}</p>
            <p className="field-help">{t('setup.ask.template')}</p>
          </>
        )}
      </section>

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
          {error && <p className="field-help field-error">{error}</p>}
        </div>
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

      <section className="serverkeys">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onAdvanced}>
          {t('setup.advanced')}
        </button>
      </section>
    </div>
  )
}
