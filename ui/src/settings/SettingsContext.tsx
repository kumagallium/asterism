// React provider holding the LLM settings (model registry + active model) and a
// version counter that bumps when keys change, so the modal re-renders. The api
// clients are plain functions, so components read `getActiveCredentials()` (via
// useLlmSettings) and pass the result through `llmHeaders(creds)` on the call.

import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { SettingsModal } from './SettingsModal'
import { type LlmSettings, SettingsCtx } from './context'
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

  const persist = useCallback((next: typeof state) => {
    saveModelsState(next)
    setState(next)
  }, [])

  const activeModel = useMemo(
    () => state.models.find((m) => m.id === state.activeModelId) ?? null,
    [state.models, state.activeModelId],
  )

  const value = useMemo<LlmSettings>(() => {
    const getActiveCredentials = () => {
      if (!activeModel) return null
      const apiKey = getKey(groupOfModel(activeModel))
      if (!apiKey) return null
      return {
        provider: activeModel.provider,
        modelId: activeModel.modelId,
        apiBase: activeModel.apiBase,
        apiKey,
      }
    }
    return {
      models: state.models,
      activeModelId: state.activeModelId,
      activeModel,
      getActiveCredentials,
      isReady: !!activeModel && hasKey(groupOfModel(activeModel)),
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
  }, [state, activeModel, persist, keysVersion])

  return (
    <SettingsCtx.Provider value={value}>
      {children}
      <SettingsModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </SettingsCtx.Provider>
  )
}
