// The settings context object + hook live here (no component export) so the
// provider file can satisfy react-refresh's component-only-export rule.

import { createContext, useContext } from 'react'
import type { MouseEvent } from 'react'
import type { ServerDefaultModels, ServerKeyProviders } from './serverKeysApi'
import type { LlmCredentials, LlmModelConfig } from './store'

/** Where openSettings() should land. Named after what the user was told to do,
 *  not after the component: 'ai' = the AI setup, 'server-token' = the access
 *  code, 'server-instance' = where IDs are issued from. */
export type SettingsSection = 'ai' | 'server-token' | 'server-instance' | 'usage'

export interface LlmSettings {
  models: LlmModelConfig[]
  activeModelId: string | null
  activeModel: LlmModelConfig | null
  /** The active model + its key, or null when nothing usable is configured. */
  getActiveCredentials: () => LlmCredentials | null
  /** True when AI calls can go out: a registered model with a key (typed here
   *  or configured server-side), or — with nothing registered at all — a
   *  provider the server holds both a shared key and a default model for. */
  isReady: boolean
  /** Providers the server has an operator-configured fallback key for. */
  serverKeyProviders: ServerKeyProviders
  /** The server's default model id per provider (non-secret; null = none). */
  serverDefaultModels: ServerDefaultModels
  /** True when AI runs on the administrator's setup rather than a key typed
   *  here — either an active model without a browser key, or the implicit
   *  server fallback used when nothing is registered. */
  activeUsesServerKey: boolean
  /** True while nothing is registered and the server's own setup is carrying
   *  the calls (so the UI shows no model name — there is none to show). */
  usesServerFallback: boolean
  /** True if the given provider has a server-side key (form/gate helper). */
  hasServerKey: (provider: string) => boolean
  /** Re-fetch the server-side key providers (after setting/clearing one). */
  refreshServerKeys: () => void
  setActiveModel: (id: string) => void
  addModel: (m: LlmModelConfig) => void
  updateModel: (id: string, patch: Partial<Omit<LlmModelConfig, 'id'>>) => void
  removeModel: (id: string) => void
  // Keys (per credential group).
  keyForModel: (m: LlmModelConfig) => string
  hasKeyForModel: (m: LlmModelConfig) => boolean
  setKeyForModel: (m: LlmModelConfig, apiKey: string, remember: boolean) => void
  /** Open the settings modal, scrolled to the section the user was sent to
   *  find. Callers often pass this straight to `onClick`, so anything that is
   *  not a section name (a MouseEvent) is ignored and opens the first tab. */
  openSettings: (section?: SettingsSection | MouseEvent) => void
}

export const SettingsCtx = createContext<LlmSettings | null>(null)

export function useLlmSettings(): LlmSettings {
  const ctx = useContext(SettingsCtx)
  if (!ctx) throw new Error('useLlmSettings must be used within a SettingsProvider')
  return ctx
}
