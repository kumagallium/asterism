// React provider holding the LLM settings (model registry + active model) and a
// version counter that bumps when keys change, so the modal re-renders. The api
// clients are plain functions, so components read `getActiveCredentials()` (via
// useLlmSettings) and pass the result through `llmHeaders(creds)` on the call.

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { SettingsModal } from './SettingsModal'
import { type LlmSettings, type SettingsSection, SettingsCtx } from './context'
import {
  fetchServerKeyInfo,
  type ServerDefaultModels,
  type ServerKeyProviders,
} from './serverKeysApi'
import {
  cleanupLegacySeed,
  getKey,
  groupOfModel,
  hasKey,
  loadModelsState,
  migrateLegacy,
  saveModelsState,
  setKey as persistKey,
} from './store'

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(() => migrateLegacy(loadModelsState()))
  // Bumped on any key write so consumers that read keys re-render.
  const [keysVersion, setKeysVersion] = useState(0)
  // The modal is owned here so any view can open it via openSettings(), and
  // which section it should land on (null = the first tab, as before).
  const [modalOpen, setModalOpen] = useState(false)
  const [modalSection, setModalSection] = useState<SettingsSection | null>(null)
  // Which providers the server has an operator key for (Option A). Fetched once;
  // {} until then / on failure, so we simply require a browser key in that case.
  const [serverKeyProviders, setServerKeyProviders] = useState<ServerKeyProviders>({})
  // The server's default model id per provider (non-secret), so a browser with
  // nothing registered can still use a shared key.
  const [serverDefaultModels, setServerDefaultModels] = useState<ServerDefaultModels>({})

  const refreshServerKeys = useCallback(() => {
    fetchServerKeyInfo().then(({ providers, defaultModels }) => {
      setServerKeyProviders(providers)
      setServerDefaultModels(defaultModels)
      // One-shot repair of the historical auto-seed, deferred to here because
      // "usable via a server-side key" is only known after this fetch.
      const repaired = cleanupLegacySeed(!!providers.anthropic)
      if (repaired) setState(repaired)
    })
  }, [])

  useEffect(() => {
    refreshServerKeys()
  }, [refreshServerKeys])

  const persist = useCallback((next: typeof state) => {
    saveModelsState(next)
    setState(next)
  }, [])

  const activeModel = useMemo(
    () => state.models.find((m) => m.id === state.activeModelId) ?? null,
    [state.models, state.activeModelId],
  )

  // With nothing registered, an administrator-configured shared key is enough on
  // its own: the browser sends only the provider (+ the server's own default
  // model id) and the backend supplies key and endpoint. Without this, a fresh
  // browser on a fully configured server would still be asked to pick a provider
  // and type a model id before anything worked. openai-compatible needs a model
  // id from the server, since its ids are endpoint-specific and guessing one
  // would just fail at call time.
  const fallbackProvider = useMemo(() => {
    const preference = ['anthropic', 'openai', 'openai-compatible']
    return (
      preference.find((p) => {
        if (!serverKeyProviders[p]) return false
        return p !== 'openai-compatible' || !!serverDefaultModels[p]
      }) ?? null
    )
  }, [serverKeyProviders, serverDefaultModels])

  const value = useMemo<LlmSettings>(() => {
    const hasServerKey = (provider: string) => !!serverKeyProviders[provider]
    const usesServerFallback = !activeModel && fallbackProvider !== null
    const getActiveCredentials = () => {
      if (!activeModel) {
        if (!fallbackProvider) return null
        return {
          provider: fallbackProvider,
          // Empty → llmHeaders omits X-LLM-Model and the server uses its own
          // default. apiBase stays null so a pinned shared endpoint wins.
          modelId: serverDefaultModels[fallbackProvider] ?? '',
          apiBase: null,
          apiKey: '',
          maxTokens: null,
        }
      }
      const apiKey = getKey(groupOfModel(activeModel))
      // No browser key is OK when the server has one for this provider: send the
      // other coordinates with an empty key so llmHeaders omits X-API-Key and the
      // backend falls back to its operator key.
      if (!apiKey && !hasServerKey(activeModel.provider)) return null
      return {
        provider: activeModel.provider,
        modelId: activeModel.modelId,
        apiBase: activeModel.apiBase,
        apiKey,
        maxTokens: activeModel.maxTokens ?? null,
      }
    }
    const activeHasBrowserKey = !!activeModel && hasKey(groupOfModel(activeModel))
    const activeHasServerKey = !!activeModel && hasServerKey(activeModel.provider)
    return {
      models: state.models,
      activeModelId: state.activeModelId,
      activeModel,
      getActiveCredentials,
      isReady: activeHasBrowserKey || activeHasServerKey || usesServerFallback,
      serverKeyProviders,
      serverDefaultModels,
      activeUsesServerKey: usesServerFallback || (!activeHasBrowserKey && activeHasServerKey),
      usesServerFallback,
      hasServerKey,
      refreshServerKeys,
      setActiveModel: (id) => persist({ ...state, activeModelId: id }),
      addModel: (m) =>
        persist({
          models: [...state.models, m],
          activeModelId: state.activeModelId ?? m.id,
        }),
      updateModel: (id, patch) =>
        persist({
          ...state,
          models: state.models.map((m) => (m.id === id ? { ...m, ...patch } : m)),
        }),
      removeModel: (id) => {
        const models = state.models.filter((m) => m.id !== id)
        const activeModelId =
          state.activeModelId === id ? (models[0]?.id ?? null) : state.activeModelId
        persist({ models, activeModelId })
      },
      keyForModel: (m) => getKey(groupOfModel(m)),
      hasKeyForModel: (m) => hasKey(groupOfModel(m)),
      setKeyForModel: (m, apiKey, remember) => {
        persistKey(groupOfModel(m), apiKey, remember)
        setKeysVersion((v) => v + 1)
      },
      openSettings: (section) => {
        // Call sites hand this to onClick, so a MouseEvent lands here too.
        setModalSection(typeof section === 'string' ? section : null)
        setModalOpen(true)
      },
    }
    // keysVersion is a dependency so key-derived fields recompute on key writes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    state,
    activeModel,
    persist,
    keysVersion,
    serverKeyProviders,
    serverDefaultModels,
    fallbackProvider,
    refreshServerKeys,
  ])

  return (
    <SettingsCtx.Provider value={value}>
      {children}
      <SettingsModal
        open={modalOpen}
        section={modalSection}
        onClose={() => {
          setModalOpen(false)
          setModalSection(null)
        }}
      />
    </SettingsCtx.Provider>
  )
}
