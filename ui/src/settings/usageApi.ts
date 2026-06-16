// Read the backend usage ledger (token counts only; cost is computed in the UI
// from the model rate table). Same-origin `/api` path → Vite proxies it to the
// workbench API, like the other live (non-mock) surfaces.

export interface UsageEvent {
  ts: string
  feature: string
  provider: string
  model_id: string
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface UsageMonthly {
  month: string
  feature: string
  provider: string
  model_id: string
  call_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  total_tokens: number
}

export interface UsageResponse {
  events: UsageEvent[]
  monthly: UsageMonthly[]
}

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

export async function fetchUsage(): Promise<UsageResponse> {
  const res = await fetch(`${API_BASE}/api/usage`)
  if (!res.ok) throw new Error(`usage failed (HTTP ${res.status})`)
  const data = (await res.json()) as Partial<UsageResponse>
  return { events: data.events ?? [], monthly: data.monthly ?? [] }
}
