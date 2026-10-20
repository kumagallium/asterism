// What this server can run, beyond the keys it holds: GET /api/llm/capabilities.
// Today that is one thing — whether GitHub Copilot (the signed-in `copilot` CLI
// on this computer) can be used. True only on the desktop app / a local
// single-user server. Any failure (older server, network) reads as "no", so the
// option simply does not appear.

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export interface LlmCapabilities {
  copilotSubscription: boolean
}

export async function fetchLlmCapabilities(): Promise<LlmCapabilities> {
  try {
    const res = await fetch(`${API_BASE}/api/llm/capabilities`)
    if (!res.ok) return { copilotSubscription: false }
    const body = (await res.json()) as { copilot_subscription?: boolean }
    return { copilotSubscription: body.copilot_subscription === true }
  } catch {
    return { copilotSubscription: false }
  }
}
