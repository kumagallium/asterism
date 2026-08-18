// Which providers the server has an operator-configured key for (Option A).
// GET /api/llm/server-keys returns booleans only (never the key), so the UI can
// let a user proceed without typing a key when the server already holds one.
// Goes through the /api proxy, so it is live even under the preview's mock mode.

import { authHeaders } from '../authToken'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** provider id -> whether the server has a fallback key for it. */
export type ServerKeyProviders = Record<string, boolean>

/** provider id -> the model id to use when the caller pins none (null = none
 *  known, which is the normal case for a custom endpoint). Not secret. */
export type ServerDefaultModels = Record<string, string | null>

export interface ServerKeyInfo {
  providers: ServerKeyProviders
  defaultModels: ServerDefaultModels
}

/**
 * Fetch the providers that have a server-side key, plus the server's default
 * model id per provider. Returns empty maps on any failure (endpoint absent on
 * an older server, network error) so the UI simply falls back to requiring a
 * browser key — never blocks on this optional capability.
 *
 * `default_models` is optional and absent on a server that does not advertise
 * it; every caller must behave as if the map were empty (the two public
 * providers then run on the server's own built-in default, and a custom
 * endpoint — whose ids only it knows — keeps asking for a key here).
 */
export async function fetchServerKeyInfo(): Promise<ServerKeyInfo> {
  try {
    const res = await fetch(`${API_BASE}/api/llm/server-keys`)
    if (!res.ok) return { providers: {}, defaultModels: {} }
    const body = (await res.json()) as {
      providers?: ServerKeyProviders
      default_models?: ServerDefaultModels
    }
    return { providers: body.providers ?? {}, defaultModels: body.default_models ?? {} }
  } catch {
    return { providers: {}, defaultModels: {} }
  }
}

/** A failed save, carrying the HTTP status so the UI can say what to do next
 *  (the server's own detail is English prose and stays as `message`). */
export class ServerKeyError extends Error {
  readonly status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ServerKeyError'
    this.status = status
  }
}

/**
 * Set (or, with a blank `apiKey`, clear) the shared server-side key for a
 * provider. Write-gated: sends the write-auth token (authHeaders). The key is
 * persisted server-side and never returned. Returns the updated provider→bool
 * map; throws a ServerKeyError on failure (e.g. 401 without a token, 400 if an
 * openai-compatible key is sent without a base URL).
 */
export async function setServerKey(
  provider: string,
  apiKey: string,
  apiBase?: string | null,
): Promise<ServerKeyProviders> {
  const res = await fetch(`${API_BASE}/api/llm/server-keys`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ provider, api_key: apiKey, api_base: apiBase || null }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let detail = text
    try {
      const j = JSON.parse(text) as { detail?: unknown }
      if (j && typeof j.detail === 'string') detail = j.detail
    } catch {
      /* not JSON — keep raw text */
    }
    throw new ServerKeyError(res.status, detail || `HTTP ${res.status}`)
  }
  return ((await res.json()) as { providers?: ServerKeyProviders }).providers ?? {}
}
