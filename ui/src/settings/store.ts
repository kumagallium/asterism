// Settings store: the LLM model registry + per-credential-group API keys.
//
// Model definitions (provider / modelId / endpoint / display name / token rate)
// are NOT secret, so they live in localStorage. The API *key* is secret: it is
// stored per credential-group (`provider::apiBase`) in either localStorage
// (remembered across restarts) or sessionStorage (cleared when the tab closes),
// chosen by a per-group "remember on this browser" toggle. The model config
// never carries the raw key.
//
// This module is framework-agnostic (pure storage helpers + types). React state
// lives in SettingsContext, which calls these and re-renders.
//
// Model definitions + the active model id live under the `models` key of the
// app's single settings.json in single-user mode (ADR app-data-on-disk.md
// D1/D5) instead of localStorage; see appSettings.ts for the store-boundary
// swap. `loadModelsState`/`saveModelsState` keep their exact signatures so
// SettingsContext (which owns the React state and calls these once at mount /
// on every write) needs no changes — see appSettings.ts's header comment for
// the one known gap this leaves: a model list migrated up to the server on a
// *previous* launch is not re-read until the app is reloaded, since nothing
// currently re-invokes `loadModelsState()` after mount.

import { getServerSetting, isAppSettingsServerMode, putAppSetting } from '../appSettings'

export type Provider = 'anthropic' | 'openai' | 'openai-compatible'
export type RateCurrency = 'usd' | 'jpy'

/** Token unit price per 1M tokens. Currency lets a domestic provider (Sakura AI
 *  Engine) be priced directly in JPY. Cache prices are derived from `input` at
 *  display time (see model-pricing.cacheMultipliers), not stored here. */
export interface TokenRate {
  input: number
  output: number
  currency: RateCurrency
}

/** One registered model the user can pick as the active model. */
export interface LlmModelConfig {
  id: string
  name: string
  provider: Provider
  modelId: string
  /** Custom base URL. Required for openai-compatible (Sakura/Groq/Ollama/...). */
  apiBase: string | null
  rate?: TokenRate
  /** Max output tokens per generation. Unset/null → the server default (96000).
   *  Small-context models (qwen3 via vLLM etc.) reject the default and need a
   *  lower cap like 16000-32000. */
  maxTokens?: number | null
}

/** Coordinates an LLM-invoking call needs: which model + the key for it. */
export interface LlmCredentials {
  provider: string
  modelId: string
  apiBase: string | null
  apiKey: string
  /** Per-model max output tokens (null → server default). */
  maxTokens: number | null
}

export interface ProviderMeta {
  id: Provider
  name: string
  /** When true the base URL is required (no public default endpoint). */
  needsApiBase: boolean
}

/** Build the per-request LLM headers from the active credentials (or {} when
 *  none). Sent alongside any existing headers on an LLM-invoking call. */
export function llmHeaders(creds: LlmCredentials | null): Record<string, string> {
  if (!creds) return {}
  const h: Record<string, string> = {}
  if (creds.apiKey) h['X-API-Key'] = creds.apiKey
  if (creds.provider) h['X-LLM-Provider'] = creds.provider
  if (creds.modelId) h['X-LLM-Model'] = creds.modelId
  if (creds.apiBase) h['X-LLM-Api-Base'] = creds.apiBase
  // Only a positive number overrides the server-side default (96000).
  if (typeof creds.maxTokens === 'number' && creds.maxTokens > 0) {
    h['X-LLM-Max-Tokens'] = String(creds.maxTokens)
  }
  return h
}

// The provider catalog (ported from Graphium). openai-compatible is the seam for
// any custom endpoint — Sakura AI Engine, Groq, Ollama, vLLM, LM Studio.
export const PROVIDERS: ProviderMeta[] = [
  { id: 'anthropic', name: 'Anthropic (Claude)', needsApiBase: false },
  { id: 'openai', name: 'OpenAI', needsApiBase: false },
  { id: 'openai-compatible', name: 'OpenAI互換 (Sakura AI Engine / Groq / Ollama …)', needsApiBase: true },
]

// Placeholder base-URL hints shown in the form per provider.
export const API_BASE_HINTS: Record<string, string> = {
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com/v1',
  'openai-compatible': 'https://api.openai.iniad.org/v1  (例: Sakura = https://api.ai.sakura.ad.jp/v1)',
}

// ---------------------------------------------------------------------------
// Storage keys
// ---------------------------------------------------------------------------

const MODELS_KEY = 'asterism.models' // { models, activeModelId } — localStorage
const KEYS_KEY = 'asterism.keys' // { [group]: apiKey } — local OR session
const REMEMBER_KEY = 'asterism.keyRemember' // { [group]: boolean } — localStorage
const LEGACY_KEY = 'asterism.apiKey' // pre-settings single key (sessionStorage)

interface ModelsState {
  models: LlmModelConfig[]
  activeModelId: string | null
}

// ---------------------------------------------------------------------------
// id + group helpers
// ---------------------------------------------------------------------------

let _counter = 0
function newId(): string {
  // crypto.randomUUID where available; otherwise a good-enough unique id (the
  // counter avoids same-millisecond collisions when adding several quickly).
  const c = globalThis.crypto as Crypto | undefined
  if (c?.randomUUID) return c.randomUUID()
  _counter += 1
  return `m_${Date.now().toString(36)}_${_counter}`
}

/** The credential group a model shares its key with: provider + endpoint. */
export function credentialGroup(provider: string, apiBase: string | null): string {
  return `${provider}::${apiBase ?? ''}`
}

export function groupOfModel(m: LlmModelConfig): string {
  return credentialGroup(m.provider, m.apiBase)
}

/** A provider+endpoint the user has already registered at least one model on. */
export interface CredentialGroupInfo {
  group: string
  provider: Provider
  apiBase: string | null
  /** How many registered models share this provider+endpoint. */
  modelCount: number
  /** Whether a key is already stored for the group (browser-side). */
  hasKey: boolean
}

/** Distinct provider+endpoint pairs across the registered models, in first-seen
 *  order. The add-model form offers these so a second model on an endpoint the
 *  user already set up never asks for the provider / base URL / key again. */
export function listCredentialGroups(models: LlmModelConfig[]): CredentialGroupInfo[] {
  const byGroup = new Map<string, CredentialGroupInfo>()
  for (const m of models) {
    const group = groupOfModel(m)
    const seen = byGroup.get(group)
    if (seen) {
      seen.modelCount += 1
      continue
    }
    byGroup.set(group, {
      group,
      provider: m.provider,
      apiBase: m.apiBase,
      modelCount: 1,
      hasKey: hasKey(group),
    })
  }
  return [...byGroup.values()]
}

// ---------------------------------------------------------------------------
// Models (localStorage)
// ---------------------------------------------------------------------------

function readJSON<T>(store: Storage, key: string, fallback: T): T {
  try {
    const raw = store.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

export function loadModelsState(): ModelsState {
  if (isAppSettingsServerMode()) {
    const remote = getServerSetting<ModelsState>('models')
    if (remote && Array.isArray(remote.models)) return remote
  }
  const state = readJSON<ModelsState>(localStorage, MODELS_KEY, { models: [], activeModelId: null })
  if (!Array.isArray(state.models)) return { models: [], activeModelId: null }
  return state
}

export function saveModelsState(state: ModelsState): void {
  if (isAppSettingsServerMode()) {
    putAppSetting('models', state)
    return
  }
  localStorage.setItem(MODELS_KEY, JSON.stringify(state))
}

export function makeModel(input: Omit<LlmModelConfig, 'id'>): LlmModelConfig {
  return { ...input, id: newId() }
}

// ---------------------------------------------------------------------------
// Keys (localStorage when remembered, else sessionStorage)
// ---------------------------------------------------------------------------

function readKeyMap(store: Storage): Record<string, string> {
  return readJSON<Record<string, string>>(store, KEYS_KEY, {})
}
function writeKeyMap(store: Storage, map: Record<string, string>): void {
  store.setItem(KEYS_KEY, JSON.stringify(map))
}

export function getKey(group: string): string {
  return readKeyMap(localStorage)[group] ?? readKeyMap(sessionStorage)[group] ?? ''
}

export function isRemembered(group: string): boolean {
  const flags = readJSON<Record<string, boolean>>(localStorage, REMEMBER_KEY, {})
  // Default ON (the approved UX): a fresh key for a group is remembered unless
  // the user unticks the box.
  return flags[group] ?? true
}

function setRemembered(group: string, remember: boolean): void {
  const flags = readJSON<Record<string, boolean>>(localStorage, REMEMBER_KEY, {})
  flags[group] = remember
  localStorage.setItem(REMEMBER_KEY, JSON.stringify(flags))
}

/** Store (or clear, when empty) the key for a group in the chosen store, and
 *  remove it from the other so a key never lingers in both. */
export function setKey(group: string, apiKey: string, remember: boolean): void {
  const target = remember ? localStorage : sessionStorage
  const other = remember ? sessionStorage : localStorage
  const targetMap = readKeyMap(target)
  const otherMap = readKeyMap(other)
  if (otherMap[group] !== undefined) {
    delete otherMap[group]
    writeKeyMap(other, otherMap)
  }
  if (apiKey) targetMap[group] = apiKey
  else delete targetMap[group]
  writeKeyMap(target, targetMap)
  setRemembered(group, remember)
}

export function hasKey(group: string): boolean {
  return getKey(group).length > 0
}

// ---------------------------------------------------------------------------
// Migration from the pre-settings single key
// ---------------------------------------------------------------------------

/** One-time: if there are no models yet but a legacy `asterism.apiKey` exists,
 *  seed a default Anthropic model and move the key into the keystore (session,
 *  matching the legacy ephemerality). Without a legacy key there is nothing to
 *  migrate — a fresh install starts with an empty registry, so the first model
 *  the user sees is one they added themselves. */
export function migrateLegacy(state: ModelsState): ModelsState {
  if (state.models.length > 0) return state
  let legacy: string
  try {
    legacy = sessionStorage.getItem(LEGACY_KEY) ?? ''
  } catch {
    legacy = ''
  }
  if (!legacy) return state
  const seed = makeModel({
    name: 'Claude Opus 4.7',
    provider: 'anthropic',
    modelId: 'claude-opus-4-7',
    apiBase: null,
  })
  const next: ModelsState = { models: [seed], activeModelId: seed.id }
  saveModelsState(next)
  setKey(groupOfModel(seed), legacy, false) // session-only, like the legacy key
  try {
    sessionStorage.removeItem(LEGACY_KEY)
  } catch {
    /* ignore */
  }
  return next
}

// ---------------------------------------------------------------------------
// One-time cleanup of the historical unconditional auto-seed
// ---------------------------------------------------------------------------

const SEED_CLEANUP_KEY = 'asterism.seedCleanup' // '1' once the pass has run

/** Exactly the entry the pre-fix migrateLegacy auto-created, with no user
 *  customization since (no endpoint/rate/max-tokens overrides). */
function isLegacySeedShape(m: LlmModelConfig): boolean {
  return (
    m.provider === 'anthropic' &&
    m.modelId === 'claude-opus-4-7' &&
    m.name === 'Claude Opus 4.7' &&
    !m.apiBase &&
    m.rate == null &&
    m.maxTokens == null
  )
}

/** One-time repair for browsers that already received the unconditional
 *  auto-seed (the bug fixed in migrateLegacy): drop seed-shaped entries that
 *  nothing can use — no browser key for their group and no server-side
 *  Anthropic key. Entries with a key anywhere are in real use and are kept.
 *  Runs at most once per browser so a deliberate keyless registration made
 *  after this pass is never touched. Returns the repaired state, or null when
 *  nothing changed. */
export function cleanupLegacySeed(hasAnthropicServerKey: boolean): ModelsState | null {
  try {
    if (localStorage.getItem(SEED_CLEANUP_KEY)) return null
    localStorage.setItem(SEED_CLEANUP_KEY, '1')
  } catch {
    return null
  }
  if (hasAnthropicServerKey) return null
  const state = loadModelsState()
  const models = state.models.filter((m) => !(isLegacySeedShape(m) && !hasKey(groupOfModel(m))))
  if (models.length === state.models.length) return null
  const activeModelId = models.some((m) => m.id === state.activeModelId)
    ? state.activeModelId
    : (models[0]?.id ?? null)
  const next: ModelsState = { models, activeModelId }
  saveModelsState(next)
  return next
}
