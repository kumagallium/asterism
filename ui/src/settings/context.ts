// The settings context object + hook live here (no component export) so the
// provider file can satisfy react-refresh's component-only-export rule.

import { createContext, useContext } from 'react'
import type { LlmCredentials, LlmModelConfig } from './store'

export interface LlmSettings {
  models: LlmModelConfig[]
  activeModelId: string | null
  activeModel: LlmModelConfig | null
  /** The active model + its key, or null when nothing usable is configured. */
  getActiveCredentials: () => LlmCredentials | null
  /** True when an active model exists AND its key is set (ready to call). */
  isReady: boolean
  setActiveModel: (id: string) => void
  addModel: (m: LlmModelConfig) => void
  updateModel: (id: string, patch: Partial<Omit<LlmModelConfig, 'id'>>) => void
  removeModel: (id: string) => void
  // Keys (per credential group).
  keyForModel: (m: LlmModelConfig) => string
  hasKeyForModel: (m: LlmModelConfig) => boolean
  setKeyForModel: (m: LlmModelConfig, apiKey: string, remember: boolean) => void
  // Open the settings modal (so any view can route the user to configure a model).
  openSettings: () => void
}

export const SettingsCtx = createContext<LlmSettings | null>(null)

export function useLlmSettings(): LlmSettings {
  const ctx = useContext(SettingsCtx)
  if (!ctx) throw new Error('useLlmSettings must be used within a SettingsProvider')
  return ctx
}
