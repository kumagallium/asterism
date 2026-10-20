// Model listing for the picker (#②): POST /api/models/available with the user's
// provider + key (+ base URL for openai-compatible). The key is sent in the body
// and never persisted server-side (D7). Goes through the /api proxy, so it is
// live even under the preview's mock demo mode (which only swaps the demo-agent).

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export interface AvailableModel {
  id: string
  display_name: string
}

/** A failed listing. `code` is the server's machine-readable reason when it
 *  sends one (`copilot_cli_missing` / `copilot_not_signed_in`), so the form can
 *  show the matching next step instead of the raw message. */
export class AvailableModelsError extends Error {
  readonly code: string | null
  constructor(message: string, code: string | null) {
    super(message)
    this.name = 'AvailableModelsError'
    this.code = code
  }
}

/**
 * List the models the given credentials can use. Throws with the server's
 * detail message on failure (bad key → 502, SSRF-blocked base URL → 400; Copilot → a `code`) so the
 * form can surface it next to the "fetch" button.
 */
export async function fetchAvailableModels(
  provider: string,
  apiKey: string,
  apiBase?: string | null,
): Promise<AvailableModel[]> {
  const res = await fetch(`${API_BASE}/api/models/available`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, api_key: apiKey || null, api_base: apiBase || null }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let detail = text
    let code: string | null = null
    try {
      const j = JSON.parse(text) as { detail?: unknown }
      if (j && typeof j.detail === 'string') detail = j.detail
      else if (j && j.detail && typeof j.detail === 'object') {
        const d = j.detail as { code?: unknown; message?: unknown }
        if (typeof d.code === 'string') code = d.code
        if (typeof d.message === 'string') detail = d.message
      }
    } catch {
      /* not JSON — keep raw text */
    }
    throw new AvailableModelsError(detail || `HTTP ${res.status}`, code)
  }
  return ((await res.json()) as { models?: AvailableModel[] }).models ?? []
}
