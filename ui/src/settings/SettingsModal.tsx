import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './SettingsModal.css'
import { AboutTab } from './AboutTab'
import { AiSetup } from './AiSetup'
import { InstanceSection } from './InstanceSection'
import { ServerKeysSection } from './ServerKeysSection'
import { WriteTokenSection } from './WriteTokenSection'
import { type SettingsSection, useLlmSettings } from './context'
import { UsageTab } from './UsageTab'
import { fetchAvailableModels, type AvailableModel } from './modelsApi'
import {
  PROVIDERS,
  type CredentialGroupInfo,
  type LlmModelConfig,
  type Provider,
  type RateCurrency,
  type TokenRate,
  credentialGroup,
  getKey,
  isRemembered,
  listCredentialGroups,
  makeModel,
} from './store'

// Four tabs, named after what the user came to do: "AI" (what answers), "this
// server" (things an administrator hands you — the access code, where IDs are
// issued from, the shared AI setup), usage, and the app itself. Previously all
// of those lived under "Models", so guidance like "enter it in settings" landed
// on a page whose tab names said nothing about the field being described.
type Tab = 'ai' | 'server' | 'usage' | 'about'

/** DOM id of the block a section name scrolls to. */
const SECTION_ANCHOR: Record<SettingsSection, string> = {
  ai: 'settings-ai',
  'server-token': 'settings-server-token',
  'server-instance': 'settings-server-instance',
  usage: 'settings-usage',
}

const SECTION_TAB: Record<SettingsSection, Tab> = {
  ai: 'ai',
  'server-token': 'server',
  'server-instance': 'server',
  usage: 'usage',
}

export function SettingsModal({
  open,
  section = null,
  onClose,
}: {
  open: boolean
  /** Where the user was told to go ("enter the access code in settings"). */
  section?: SettingsSection | null
  onClose: () => void
}) {
  const { t } = useTranslation('settings')
  const [tab, setTab] = useState<Tab>('ai')

  // Land on the section the user was sent to. Chosen during render (rather than
  // after paint) so the modal never flashes the wrong tab, and remembered so a
  // tab the user picks afterwards is not yanked back.
  const [routedTo, setRoutedTo] = useState<SettingsSection | null>(null)
  if (open && section && section !== routedTo) {
    setRoutedTo(section)
    setTab(SECTION_TAB[section])
  }
  if (!open && routedTo !== null) setRoutedTo(null)

  // ...and bring it into view: the field the user was told about is often below
  // the fold of its tab.
  useEffect(() => {
    if (!open || !section) return
    const id = SECTION_ANCHOR[section]
    const timer = window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ block: 'start' })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [open, section])

  // Close on Escape.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 開いたらフォーカスをダイアログへ移す（従来は背後のページに残り、
  // Tab がモーダルの外を回っていた）。
  const dialogRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (open) dialogRef.current?.focus()
  }, [open])

  // フォーカストラップ: Tab をダイアログ内で循環させ、背後のページに抜けない。
  function trapTab(e: React.KeyboardEvent) {
    if (e.key !== 'Tab') return
    const dialog = dialogRef.current
    if (!dialog) return
    const focusables = [
      ...dialog.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
    ].filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null)
    if (focusables.length === 0) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === dialog)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }

  if (!open) return null

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t('title')}
        ref={dialogRef}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapTab}
      >
        <header className="settings-head">
          <h2>{t('title')}</h2>
          <button type="button" className="settings-close" aria-label={t('close')} onClick={onClose}>
            ×
          </button>
        </header>
        <nav className="settings-tabs">
          {(['ai', 'server', 'usage', 'about'] as Tab[]).map((id) => (
            <button
              key={id}
              type="button"
              className={`settings-tab${tab === id ? ' active' : ''}`}
              onClick={() => setTab(id)}
            >
              {t(`tabs.${id}`)}
            </button>
          ))}
        </nav>
        <div className="settings-body">
          {tab === 'ai' && <AiTab />}
          {tab === 'server' && <ServerTab />}
          {tab === 'usage' && (
            <div id={SECTION_ANCHOR.usage}>
              <UsageTab />
            </div>
          )}
          {tab === 'about' && <AboutTab />}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AI tab: the first-run screen until something is configured, then the registry
// ---------------------------------------------------------------------------

function AiTab() {
  const settings = useLlmSettings()
  const [advanced, setAdvanced] = useState(false)
  const configured =
    settings.models.length > 0 || PROVIDERS.some((p) => settings.hasServerKey(p.id))

  return (
    <div id={SECTION_ANCHOR.ai}>
      {configured || advanced ? <ModelsTab /> : <AiSetup onAdvanced={() => setAdvanced(true)} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// This-server tab: the things an administrator sets, not the person reading
// ---------------------------------------------------------------------------

// Each section decides for itself whether it applies to this deployment, and at
// least one of them always does (a current api answers /api/instance, so "where
// IDs are issued from" shows; an older one has no write gate to report, so the
// access code shows), which is why there is no empty-tab case to hide.
function ServerTab() {
  return (
    <div className="server-tab">
      <ServerKeysSection />
      <div id={SECTION_ANCHOR['server-token']}>
        <WriteTokenSection />
      </div>
      <div id={SECTION_ANCHOR['server-instance']}>
        <InstanceSection />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Models tab
// ---------------------------------------------------------------------------

function ModelsTab() {
  const { t } = useTranslation('settings')
  const settings = useLlmSettings()
  const [editing, setEditing] = useState<LlmModelConfig | null>(null)
  const [adding, setAdding] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const showForm = adding || editing !== null
  const serverProvider = PROVIDERS.find((p) => settings.hasServerKey(p.id)) ?? null

  return (
    <div className="models-tab">
      <p className="settings-intro">{t('models.intro')}</p>

      {/* An administrator's setup is the normal case; saying "no models yet —
          add one" on a server that already has AI sends people looking for a
          key they never needed. */}
      {serverProvider && !showForm && (
        <p className="field-help field-ok">
          {t('models.serverConfigured', { provider: t(serverProvider.nameKey) })}
        </p>
      )}

      {settings.models.length === 0 && !showForm && !serverProvider && (
        <p className="settings-empty">{t('models.empty')}</p>
      )}

      {!showForm && (
        <ul className="model-list">
          {settings.models.map((m) => {
            const active = m.id === settings.activeModelId
            const keySet = settings.hasKeyForModel(m)
            const viaServer = !keySet && settings.hasServerKey(m.provider)
            return (
              <li key={m.id} className={`model-row${active ? ' active' : ''}`}>
                <label className="model-pick">
                  <input
                    type="radio"
                    name="active-model"
                    checked={active}
                    onChange={() => settings.setActiveModel(m.id)}
                  />
                </label>
                <div className="model-main">
                  <div className="model-name">
                    {m.name}
                    {active && <span className="model-badge">{t('models.activeBadge')}</span>}
                  </div>
                  <div className="model-sub">
                    <span className="model-provider">{providerName(m.provider, t)}</span>
                    <span className="model-id">{m.modelId}</span>
                    {m.apiBase && <span className="model-base">{m.apiBase}</span>}
                  </div>
                  {/* A model the server holds a key for works as it is; marking
                      it "missing" reads as broken and sends people hunting. */}
                  <div className={`model-key ${keySet || viaServer ? 'ok' : 'missing'}`}>
                    {keySet
                      ? t('models.keySet')
                      : viaServer
                        ? t('models.keyServer')
                        : t('models.keyMissing')}
                  </div>
                </div>
                <div className="model-actions">
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(m)}>
                    {t('models.edit')}
                  </button>
                  {confirmDelete === m.id ? (
                    <>
                      <button
                        type="button"
                        className="btn btn--danger btn--sm"
                        onClick={() => {
                          settings.removeModel(m.id)
                          setConfirmDelete(null)
                        }}
                      >
                        {t('models.confirmDelete')}
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setConfirmDelete(null)}
                      >
                        {t('cancel')}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => setConfirmDelete(m.id)}
                    >
                      {t('models.delete')}
                    </button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {showForm ? (
        <ModelForm
          model={editing}
          onCancel={() => {
            setAdding(false)
            setEditing(null)
          }}
          onSaved={() => {
            setAdding(false)
            setEditing(null)
          }}
        />
      ) : (
        <button type="button" className="btn settings-add" onClick={() => setAdding(true)}>
          + {t('models.add')}
        </button>
      )}
    </div>
  )
}

function providerName(id: string, t: (key: string) => string): string {
  const meta = PROVIDERS.find((p) => p.id === id)
  return meta ? t(meta.nameKey) : id
}

/** Add-model form modes: reuse a registered provider+endpoint, or enter a new one. */
type AddMode = 'existing' | 'new'

/** "Anthropic (Claude) — 2 models" / "OpenAI互換 — https://… — 1 model". */
function groupLabel(
  g: CredentialGroupInfo,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const parts = [providerName(g.provider, t)]
  if (g.apiBase) parts.push(g.apiBase)
  parts.push(t('form.endpointModelCount', { count: g.modelCount }))
  return parts.join(' — ')
}

// ---------------------------------------------------------------------------
// Add / edit form
// ---------------------------------------------------------------------------

function ModelForm({
  model,
  onCancel,
  onSaved,
}: {
  model: LlmModelConfig | null
  onCancel: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation('settings')
  const settings = useLlmSettings()
  const editing = model !== null

  // Registered provider+endpoint pairs (#2 "don't re-enter a provider you already
  // set up"), shaped after Graphium's add-model form: a two-button mode switch
  // ("use one I already registered" / "new connection"), and in the existing mode
  // only an endpoint picker — provider, base URL and key all come from the group,
  // so a second model on the same endpoint asks for the model id alone.
  const groups = useMemo(() => listCredentialGroups(settings.models), [settings.models])
  const defaultPreset = editing ? null : (groups[0] ?? null)
  // Graphium starts in "new"; we start in "existing" when there is something to
  // reuse — that is the whole point of this form, and the mode switch is right
  // there when the user does want a new endpoint.
  const [addMode, setAddMode] = useState<AddMode>(defaultPreset ? 'existing' : 'new')
  const [presetGroup, setPresetGroup] = useState<string>(defaultPreset?.group ?? '')
  const preset: CredentialGroupInfo | null =
    (addMode === 'existing' && groups.find((g) => g.group === presetGroup)) || null

  const [provider, setProvider] = useState<Provider>(
    model?.provider ?? defaultPreset?.provider ?? 'anthropic',
  )
  const [name, setName] = useState(model?.name ?? '')
  const [modelId, setModelId] = useState(model?.modelId ?? '')
  const [apiBase, setApiBase] = useState(model?.apiBase ?? defaultPreset?.apiBase ?? '')
  const initialGroup = credentialGroup(
    model?.provider ?? defaultPreset?.provider ?? 'anthropic',
    model?.apiBase ?? defaultPreset?.apiBase ?? (model ? null : ''),
  )
  // Prefill the key + remember flag from this credential group for BOTH the edit
  // and add forms: a key already saved for this provider+endpoint should populate
  // the (masked) field so the "fetch models" button and save work without
  // retyping it. Previously the add form always started blank, so the fetch
  // button stayed disabled even when a usable key existed for the default
  // provider. getKey/isRemembered return empty/default when the group has none.
  const [apiKey, setApiKey] = useState(() => getKey(initialGroup))
  const [remember, setRemember] = useState(() => isRemembered(initialGroup))

  // Rate (strings in the form; parsed on save). Input + output only — cache cost
  // is derived from the input price at display time (model-pricing.cacheMultipliers).
  const [rateInput, setRateInput] = useState(model?.rate ? String(model.rate.input) : '')
  const [rateOutput, setRateOutput] = useState(model?.rate ? String(model.rate.output) : '')
  const [currency, setCurrency] = useState<RateCurrency>(model?.rate?.currency ?? 'usd')

  // Max output tokens (string in the form; parsed on save). Empty → server
  // default (96000); small-context models (qwen3 etc.) need a lower cap.
  const [maxTokens, setMaxTokens] = useState(model?.maxTokens ? String(model.maxTokens) : '')

  // Model picker (#②): the fetched list feeds the datalist next to the modelId
  // input so the user can pick instead of typing an exact id from memory.
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([])
  const [fetchingModels, setFetchingModels] = useState(false)
  const [modelsError, setModelsError] = useState('')

  const providerMeta = PROVIDERS.find((p) => p.id === provider)
  const needsApiBase = providerMeta?.needsApiBase ?? false
  const baseNormalized = apiBase.trim() || null
  // When the server has an operator key for this provider, a browser key is
  // optional: the fetch button and LLM calls fall back to the server key.
  const providerHasServerKey = settings.hasServerKey(provider)

  // A fetched list belongs to one provider+endpoint; drop it (and any error) so
  // the datalist never offers a different provider's model ids. Called from the
  // provider/apiBase change handlers (not an effect — that cascades renders).
  function clearFetchedModels() {
    setAvailableModels([])
    setModelsError('')
  }

  // When provider/apiBase change (in the add form), prefill the key from that
  // credential group so a second model on the same endpoint reuses its key.
  function onProviderOrBaseChange(nextProvider: Provider, nextBase: string) {
    const group = credentialGroup(nextProvider, nextBase.trim() || null)
    const existing = getKey(group)
    if (existing) {
      setApiKey(existing)
      setRemember(isRemembered(group))
    }
  }

  /** Adopt a registered endpoint: its provider, base URL and stored key. */
  function applyGroup(g: CredentialGroupInfo) {
    setProvider(g.provider)
    setApiBase(g.apiBase ?? '')
    setApiKey(getKey(g.group))
    setRemember(isRemembered(g.group))
  }

  // Pick another registered endpoint from the picker.
  function onPresetChange(nextGroup: string) {
    setPresetGroup(nextGroup)
    clearFetchedModels()
    const g = groups.find((x) => x.group === nextGroup)
    if (g) applyGroup(g)
  }

  function onModeChange(next: AddMode) {
    if (next === addMode) return
    setAddMode(next)
    clearFetchedModels()
    if (next === 'existing') {
      const g = groups.find((x) => x.group === presetGroup) ?? groups[0]
      if (g) {
        setPresetGroup(g.group)
        applyGroup(g)
      }
      return
    }
    // "New connection": start the key blank rather than carrying the previous
    // group's key over — otherwise a key belonging to another endpoint would be
    // silently saved under the new provider+base the user is about to type.
    setApiKey('')
    setRemember(true)
  }

  const idTrimmed = modelId.trim()
  const canSave = idTrimmed.length > 0 && (!needsApiBase || baseNormalized !== null)

  async function onFetchModels() {
    setFetchingModels(true)
    setModelsError('')
    try {
      setAvailableModels(await fetchAvailableModels(provider, apiKey.trim(), baseNormalized))
    } catch (e) {
      setModelsError(e instanceof Error ? e.message : String(e))
    } finally {
      setFetchingModels(false)
    }
  }

  function buildRate(): TokenRate | undefined {
    const inp = Number.parseFloat(rateInput)
    const out = Number.parseFloat(rateOutput)
    if (!Number.isFinite(inp) || !Number.isFinite(out)) return undefined
    return { input: inp, output: out, currency }
  }

  /** Empty / non-positive → null (use the server default). */
  function buildMaxTokens(): number | null {
    const n = Number.parseInt(maxTokens, 10)
    return Number.isFinite(n) && n > 0 ? n : null
  }

  function onSubmit() {
    if (!canSave) return
    const rate = buildRate()
    const displayName = name.trim() || idTrimmed
    if (editing && model) {
      settings.updateModel(model.id, {
        provider,
        name: displayName,
        modelId: idTrimmed,
        apiBase: baseNormalized,
        rate,
        maxTokens: buildMaxTokens(),
      })
      settings.setKeyForModel(
        { ...model, provider, apiBase: baseNormalized },
        apiKey.trim(),
        remember,
      )
    } else {
      const created = makeModel({
        provider,
        name: displayName,
        modelId: idTrimmed,
        apiBase: baseNormalized,
        rate,
        maxTokens: buildMaxTokens(),
      })
      settings.addModel(created)
      if (apiKey.trim()) settings.setKeyForModel(created, apiKey.trim(), remember)
    }
    onSaved()
  }

  return (
    <div className="model-form">
      <h3>{editing ? t('form.editTitle') : t('form.addTitle')}</h3>

      {/* 接続先の決め方 = 「登録済みを使う」か「新しく登録する」かの二択を先に
          出す（Graphium の追加フォームと同じ形）。登録済みが無ければ迷いようが
          ないので出さない。 */}
      {!editing && groups.length > 0 && (
        <div className="settings-seg settings-seg--block" role="group" aria-label={t('form.mode')}>
          <button
            type="button"
            className={addMode === 'existing' ? 'active' : ''}
            onClick={() => onModeChange('existing')}
          >
            {t('form.modeExisting')}
          </button>
          <button
            type="button"
            className={addMode === 'new' ? 'active' : ''}
            onClick={() => onModeChange('new')}
          >
            {t('form.modeNew')}
          </button>
        </div>
      )}

      {preset ? (
        /* 登録済みを使うモード: 接続先を選ぶだけ。プロバイダ・base URL・API キーは
           この接続先のものをそのまま使うので、入力欄そのものを出さない。 */
        <label className="field">
          <span>{t('form.endpoint')}</span>
          <select value={presetGroup} onChange={(e) => onPresetChange(e.target.value)}>
            {groups.map((g) => (
              <option key={g.group} value={g.group}>
                {groupLabel(g, t)}
              </option>
            ))}
          </select>
          <p className="field-help">{t('form.endpointReuseHelp')}</p>
        </label>
      ) : (
        <>
          <label className="field">
            <span>{t('form.provider')}</span>
            <select
              value={provider}
              onChange={(e) => {
                const next = e.target.value as Provider
                setProvider(next)
                onProviderOrBaseChange(next, apiBase)
                clearFetchedModels()
              }}
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {t(p.nameKey)}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>
              {t('form.apiBase')}
              {needsApiBase && <em className="req"> *</em>}
            </span>
            <input
              type="text"
              value={apiBase}
              placeholder={providerMeta ? t(providerMeta.baseHintKey) : ''}
              onChange={(e) => {
                setApiBase(e.target.value)
                clearFetchedModels()
              }}
              onBlur={() => onProviderOrBaseChange(provider, apiBase)}
            />
          </label>

          <label className="field">
            <span>{t('form.apiKey')}</span>
            <input
              type="password"
              value={apiKey}
              autoComplete="off"
              placeholder={apiKeyPlaceholder(provider)}
              onChange={(e) => setApiKey(e.target.value)}
            />
            {providerHasServerKey && !apiKey.trim() && (
              <p className="field-help">{t('form.apiKeyServerHint')}</p>
            )}
          </label>
        </>
      )}

      {/* 接続先が決まったら次の一手は「一覧を取得」＝主動線なので幅いっぱいに
          置く（モデル ID を暗記して打つのは逃げ道であって既定ではない）。 */}
      <button
        type="button"
        className="btn btn--block"
        onClick={onFetchModels}
        disabled={
          fetchingModels ||
          (needsApiBase && baseNormalized === null) ||
          (!apiKey.trim() && !providerHasServerKey)
        }
      >
        {fetchingModels ? t('form.fetchingModels') : t('form.fetchModels')}
      </button>
      {modelsError && <p className="field-help field-error">{modelsError}</p>}

      {/* Native <select> (not a <datalist>): a datalist filters its options by
          the input's current value, so once a full id is chosen you can't see
          the others to re-pick. A select always lists all fetched models. */}
      {availableModels.length > 0 && (
        <label className="field">
          <span>{t('form.pickModel')}</span>
          <select
            value={availableModels.some((m) => m.id === modelId) ? modelId : ''}
            onChange={(e) => setModelId(e.target.value)}
          >
            <option value="">{t('form.pickFetchedModel', { count: availableModels.length })}</option>
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* 一覧が取れない接続先（キーがサーバ側だけ／オフライン／独自 endpoint）でも
          登録できるよう、ID の直接入力は常に出す。 */}
      <label className="field">
        <span>{t('form.modelId')}</span>
        <input
          type="text"
          value={modelId}
          placeholder={modelIdPlaceholder(provider)}
          onChange={(e) => setModelId(e.target.value)}
        />
      </label>

      <label className="field">
        <span>{t('form.name')}</span>
        <input
          type="text"
          value={name}
          placeholder={idTrimmed || t('form.namePlaceholder')}
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <label className="field">
        <span>{t('form.maxTokens')}</span>
        <input
          type="number"
          step="1"
          min="1"
          value={maxTokens}
          placeholder="96000"
          onChange={(e) => setMaxTokens(e.target.value)}
        />
        <p className="field-help">{t('form.maxTokensHelp')}</p>
      </label>

      {/* キーを入力しない（登録済みを使う）モードでは、その接続先で決めた保存先を
          そのまま引き継ぐので出さない。 */}
      {!preset && (
        <>
          <label className="field-check">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>{t('form.remember')}</span>
          </label>
          <p className="field-help">{remember ? t('form.rememberOn') : t('form.rememberOff')}</p>
        </>
      )}

      <fieldset className="rate-fieldset">
        <legend>{t('form.rate')}</legend>
        <p className="field-help">{t('form.rateHelp')}</p>
        <div className="rate-grid">
          <label className="field">
            <span>{t('form.rateInput')}</span>
            <input
              type="number"
              step="any"
              min="0"
              value={rateInput}
              onChange={(e) => setRateInput(e.target.value)}
            />
          </label>
          <label className="field">
            <span>{t('form.rateOutput')}</span>
            <input
              type="number"
              step="any"
              min="0"
              value={rateOutput}
              onChange={(e) => setRateOutput(e.target.value)}
            />
          </label>
        </div>
        <p className="field-help">{t('form.cacheNote')}</p>
        <div className="rate-controls">
          <div className="currency-toggle" role="group" aria-label={t('form.currency')}>
            {(['usd', 'jpy'] as RateCurrency[]).map((c) => (
              <button
                key={c}
                type="button"
                className={`currency-btn${currency === c ? ' active' : ''}`}
                onClick={() => setCurrency(c)}
              >
                {c === 'usd' ? 'USD ($)' : 'JPY (¥)'}
              </button>
            ))}
          </div>
        </div>
      </fieldset>

      <div className="form-actions">
        <button type="button" className="btn" disabled={!canSave} onClick={onSubmit}>
          {t('save')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
          {t('cancel')}
        </button>
      </div>
    </div>
  )
}

function modelIdPlaceholder(provider: string): string {
  if (provider === 'anthropic') return 'claude-opus-4-7'
  if (provider === 'openai') return 'gpt-4o'
  return 'gpt-oss-120b'
}

function apiKeyPlaceholder(provider: string): string {
  return provider === 'anthropic' ? 'sk-ant-...' : 'sk-...'
}
