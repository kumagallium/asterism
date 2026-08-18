// App data on disk — the server-side capability check (ADR
// app-data-on-disk.md D1). Whether Ask threads / settings live in localStorage
// or on the user's disk is decided by the SERVER, not by whether the UI happens
// to be running inside Tauri: a browser tab pointed at a single-user
// `asterism-local` gets the same disk-backed behavior as the desktop shell, and
// a desktop shell pointed at a shared api keeps using localStorage (so one
// person's questions never land where another person could read them).
//
// `GET /api/appdata/info` always returns 200 (even on a shared api, where it
// answers `{single_user: false}`); every other /api/appdata/* route 404s on a
// shared api. All requests are same-origin relative paths; write requests carry
// the existing `authHeaders()` token exactly like the rest of the app.

import { authHeaders } from './authToken'
import type { AskThread } from './askThreads'

export interface AppDataInfo {
  singleUser: boolean
  home: string | null
}

const FALLBACK: AppDataInfo = { singleUser: false, home: null }

let info: AppDataInfo | null = null
let inFlight: Promise<AppDataInfo> | null = null

/** Fetch `/api/appdata/info` once and cache the result for the session. Safe to
 *  call more than once (concurrent callers share the same in-flight promise). */
export function initAppData(): Promise<AppDataInfo> {
  if (info) return Promise.resolve(info)
  if (inFlight) return inFlight
  inFlight = fetchInfo()
    .then((result) => {
      info = result
      return result
    })
    .finally(() => {
      inFlight = null
    })
  return inFlight
}

async function fetchInfo(): Promise<AppDataInfo> {
  try {
    const res = await fetch('/api/appdata/info')
    if (!res.ok) return FALLBACK
    const data = (await res.json()) as { single_user?: unknown; home?: unknown }
    return {
      singleUser: data.single_user === true,
      home: typeof data.home === 'string' ? data.home : null,
    }
  } catch {
    return FALLBACK
  }
}

/** Synchronous read of the cached info; null until `initAppData()` resolves. */
export function getAppDataInfo(): AppDataInfo | null {
  return info
}

// ---- Ask threads (single-user server storage) ------------------------------

export async function fetchAppDataThreads(): Promise<AskThread[]> {
  const res = await fetch('/api/appdata/ask/threads', { headers: authHeaders() })
  if (!res.ok) throw new Error(`GET /api/appdata/ask/threads: ${res.status}`)
  const data = (await res.json()) as { threads?: unknown }
  return Array.isArray(data.threads) ? (data.threads as AskThread[]) : []
}

export async function putAppDataThread(thread: AskThread): Promise<void> {
  const res = await fetch(`/api/appdata/ask/threads/${encodeURIComponent(thread.id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(thread),
  })
  if (!res.ok) throw new Error(`PUT /api/appdata/ask/threads/${thread.id}: ${res.status}`)
}

export async function deleteAppDataThread(id: string): Promise<void> {
  const res = await fetch(`/api/appdata/ask/threads/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok && res.status !== 404) {
    throw new Error(`DELETE /api/appdata/ask/threads/${id}: ${res.status}`)
  }
}

// ---- settings (single-user server storage) ----------------------------------

export async function fetchAppDataSettings(): Promise<Record<string, unknown>> {
  const res = await fetch('/api/appdata/settings', { headers: authHeaders() })
  if (!res.ok) throw new Error(`GET /api/appdata/settings: ${res.status}`)
  const data = (await res.json()) as { settings?: unknown }
  return data.settings && typeof data.settings === 'object'
    ? (data.settings as Record<string, unknown>)
    : {}
}

export async function putAppDataSettings(settings: Record<string, unknown>): Promise<void> {
  const res = await fetch('/api/appdata/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(settings),
  })
  if (!res.ok) throw new Error(`PUT /api/appdata/settings: ${res.status}`)
}
