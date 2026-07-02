// Which providers the server has an operator-configured key for (Option A).
// GET /api/llm/server-keys returns booleans only (never the key), so the UI can
// let a user proceed without typing a key when the server already holds one.
// Goes through the /api proxy, so it is live even under the preview's mock mode.

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
