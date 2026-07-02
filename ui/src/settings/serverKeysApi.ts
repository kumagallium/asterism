// Which providers the server has an operator-configured key for (Option A).
// GET /api/llm/server-keys returns booleans only (never the key), so the UI can
// let a user proceed without typing a key when the server already holds one.
// Goes through the /api proxy, so it is live even under the preview's mock mode.

import { authHeaders } from '../authToken'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** provider id -> whether the server has a fallback key for it. */
export type ServerKeyProviders = Record<string, boolean>

/**
 * Fetch the providers that have a server-side key. Returns {} on any failure
 * (endpoint absent on an older server, network error) so the UI simply falls
 * back to requiring a browser key — never blocks on this optional capability.
 */
export async function fetchServerKeyProviders(): Promise<ServerKeyProviders> {
  try {
    const res = await fetch(`${API_BASE}/api/llm/server-keys`)
    if (!res.ok) return {}
    const body = (await res.json()) as { providers?: ServerKeyProviders }
    return body.providers ?? {}
  } catch {
    return {}
  }
}

/**
 * Set (or, with a blank `apiKey`, clear) the shared server-side key for a
 * provider. Write-gated: sends the write-auth token (authHeaders). The key is
 * persisted server-side and never returned. Returns the updated provider→bool
 * map; throws with the server's detail on failure (e.g. 401 without a token,
 * 400 if an openai-compatible key is sent without a base URL).
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
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return ((await res.json()) as { providers?: ServerKeyProviders }).providers ?? {}
}
