// App settings — one `settings.json` on disk in single-user mode (ADR
// app-data-on-disk.md D1/D5), `localStorage` otherwise (and, for the two
// boot-critical fields, as a fast-boot cache even in single-user mode).
//
// Mirrors askThreads.ts's store-boundary swap: every setting still loads
// synchronously from localStorage on boot (unchanged behavior before the
// server has had a chance to answer), then this module asks the server
// `/api/appdata/info` who it is. In single-user mode, once the server has
// answered:
//   - if the server's settings.json is EMPTY and localStorage has values,
//     they are pushed up once (D5) and, only on success, the keys the ADR
//     table marks "削除" (`asterism.models` / `asterism.usdJpy`) are removed
//     from localStorage — the server becomes the sole source for those.
//   - otherwise the server's copy is adopted as the in-memory cache.
// `lang` / `workbenchTier` are never removed from localStorage: they affect
// the very first paint, so callers keep writing a localStorage mirror on
// every change even in single-user mode (see i18n/index.ts, WorkbenchTier.tsx).
//
// API keys never pass through here (they are a separate, browser-only
// keystore — see settings/store.ts). `stripSecrets` is defense-in-depth only,
// in case a future field is accidentally key-shaped; the server applies the
// same filter independently (ADR D3).

import { fetchAppDataSettings, initAppData, putAppDataSettings } from './appdata'

const MODELS_KEY = 'asterism.models'
const LANG_KEY = 'asterism.lang'
const TIER_KEY = 'asterism.workbench.tier'
const USDJPY_KEY = 'asterism.usdJpy'
const FLUSH_DEBOUNCE_MS = 300

interface LocalSnapshot {
  models: Record<string, unknown> | null
  lang: string | null
  workbenchTier: string | null
  usdJpy: number | null
}

let serverMode = false
let loaded = false
let serverSettings: Record<string, unknown> = {}
let bootstrapPromise: Promise<void> | null = null

// ---- localStorage read helpers (unchanged shapes/keys — see the callers) ---

function readRaw(key: string): string | null {
  if (typeof localStorage === 'undefined') return null
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function localModels(): Record<string, unknown> | null {
  const raw = readRaw(MODELS_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as { models?: unknown }
    return Array.isArray(parsed.models) ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function localLang(): string | null {
  return readRaw(LANG_KEY) || null
}

function localTier(): string | null {
  return readRaw(TIER_KEY) || null
}

function localUsdJpy(): number | null {
  const raw = readRaw(USDJPY_KEY)
  if (!raw) return null
  const n = Number.parseFloat(raw)
  return Number.isFinite(n) && n > 0 ? n : null
}

function readAllLocal(): LocalSnapshot {
  return { models: localModels(), lang: localLang(), workbenchTier: localTier(), usdJpy: localUsdJpy() }
}

function isEmptySnapshot(s: LocalSnapshot): boolean {
  return !s.models && !s.lang && !s.workbenchTier && s.usdJpy == null
}

/** Drop anything that looks like a secret before it leaves the browser (D3
 *  defense-in-depth; the model registry stored here never carries a raw key —
 *  see settings/store.ts — but this guards against a future mistake). */
function stripSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripSecrets)
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (/key|token|secret|password/i.test(k)) continue
      out[k] = stripSecrets(v)
    }
    return out
  }
  return value
}

function toPayload(s: LocalSnapshot): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (s.models) out.models = s.models
  if (s.lang) out.lang = s.lang
  if (s.workbenchTier) out.workbenchTier = s.workbenchTier
  if (s.usdJpy != null) out.usdJpy = s.usdJpy
  return stripSecrets(out) as Record<string, unknown>
}

/** Only the keys the ADR table marks "削除" — `lang` / `workbenchTier` are
 *  kept as the fast-boot cache and are never removed. */
function removeMigratedLocalKeys() {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.removeItem(MODELS_KEY)
    localStorage.removeItem(USDJPY_KEY)
  } catch {
    /* ignore */
  }
}

// ---- bootstrap ---------------------------------------------------------------

/** Decide the persistence mode (ADR D1) and, in single-user mode, either adopt
 *  the server's settings.json or migrate localStorage up into an empty one
 *  (D5). Safe to call more than once — every caller shares the same promise.
 *  Runs once automatically on module load (see the bottom of this file). */
export function bootstrapAppSettings(): Promise<void> {
  if (!bootstrapPromise) bootstrapPromise = runBootstrap()
  return bootstrapPromise
}

async function runBootstrap(): Promise<void> {
  try {
    const info = await initAppData()
    if (!info.singleUser) return
    serverMode = true
    const remote = await fetchAppDataSettings()
    if (Object.keys(remote).length > 0) {
      serverSettings = remote
      return
    }
    const local = readAllLocal()
    if (isEmptySnapshot(local)) return
    try {
      const payload = toPayload(local)
      await putAppDataSettings(payload)
      serverSettings = payload
      removeMigratedLocalKeys()
    } catch {
      // Nothing pushed, nothing removed — the next bootstrap (next launch)
      // tries again from the still-intact localStorage values.
    }
  } catch {
    // /api/appdata/info (or the settings GET) failed — stay in browser-only
    // mode; every caller below falls back to localStorage.
  } finally {
    loaded = true
  }
}

export function isAppSettingsServerMode(): boolean {
  return serverMode
}

export function isAppSettingsLoaded(): boolean {
  return loaded
}

/** The current best-known server-side value for a top-level settings.json
 *  key. `undefined` until bootstrap has resolved, or in browser mode — callers
 *  fall back to their own localStorage read in that case. */
export function getServerSetting<T>(key: string): T | undefined {
  return serverSettings[key] as T | undefined
}

// ---- debounced whole-file PUT (settings.json is one file — ADR D2) ---------
//
// A short debounce coalesces bursts (e.g. typing a rate, or several model
// edits in a row); `beforeunload` / tab-hide flush immediately. A PUT that
// fails leaves the patch queued so the next write (or the next flush) retries
// it — the in-memory `serverSettings` cache is applied optimistically and is
// never rolled back for a network error.

let pendingPatch: Record<string, unknown> | null = null
let flushTimer: ReturnType<typeof setTimeout> | null = null

function scheduleFlush() {
  if (flushTimer !== null) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    void flushAppSettings()
  }, FLUSH_DEBOUNCE_MS)
}

async function flushAppSettings(): Promise<void> {
  if (!pendingPatch) return
  const patch = pendingPatch
  pendingPatch = null
  const clean = stripSecrets(patch) as Record<string, unknown>
  const merged = { ...serverSettings, ...clean }
  try {
    await putAppDataSettings(merged)
    serverSettings = merged
  } catch {
    // Re-queue (a newer write may have queued more meanwhile — keep both).
    pendingPatch = { ...patch, ...(pendingPatch ?? {}) }
  }
}

/** Queue a partial update to settings.json (single-user mode only; a no-op
 *  otherwise — callers write localStorage themselves in that case). */
export function putAppSetting(key: string, value: unknown): void {
  if (!serverMode) return
  pendingPatch = { ...pendingPatch, [key]: value }
  serverSettings = { ...serverSettings, [key]: stripSecrets(value) }
  scheduleFlush()
}

export function flushAppSettingsNow(): void {
  if (!serverMode || !pendingPatch) return
  if (flushTimer !== null) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
  void flushAppSettings()
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', flushAppSettingsNow)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushAppSettingsNow()
  })
  void bootstrapAppSettings()
}
