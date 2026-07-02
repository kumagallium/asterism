// React provider holding the LLM settings (model registry + active model) and a
// version counter that bumps when keys change, so the modal re-renders. The api
// clients are plain functions, so components read `getActiveCredentials()` (via
// useLlmSettings) and pass the result through `llmHeaders(creds)` on the call.

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { SettingsModal } from './SettingsModal'
import { type LlmSettings, SettingsCtx } from './context'
import { fetchServerKeyProviders, type ServerKeyProviders } from './serverKeysApi'
import {
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
  // The modal is owned here so any view can open it via openSettings().
  const [modalOpen, setModalOpen] = useState(false)
  // Which providers the server has an operator key for (Option A). Fetched once;
  // {} until then / on failure, so we simply require a browser key in that case.
  const [serverKeyProviders, setServerKeyProviders] = useState<ServerKeyProviders>({})

  const refreshServerKeys = useCallback(() => {
    fetchServerKeyProviders().then(setServerKeyProviders)
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

  const value = useMemo<LlmSettings>(() => {
    const hasServerKey = (provider: string) => !!serverKeyProviders[provider]
    const getActiveCredentials = () => {
      if (!activeModel) return null
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
      }
    }
    const activeHasBrowserKey = !!activeModel && hasKey(groupOfModel(activeModel))
    const activeHasServerKey = !!activeModel && hasServerKey(activeModel.provider)
    return {
      models: state.models,
      activeModelId: state.activeModelId,
      activeModel,
      getActiveCredentials,
      isReady: activeHasBrowserKey || activeHasServerKey,
      serverKeyProviders,
      activeUsesServerKey: !activeHasBrowserKey && activeHasServerKey,
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
      openSettings: () => setModalOpen(true),
    }
    // keysVersion is a dependency so key-derived fields recompute on key writes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, activeModel, persist, keysVersion, serverKeyProviders, refreshServerKeys])

  return (
    <SettingsCtx.Provider value={value}>
      {children}
      <SettingsModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </SettingsCtx.Provider>
  )
}
