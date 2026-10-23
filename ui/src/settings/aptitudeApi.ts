// AI の適性検査（PR 11q）。POST で走らせ、ジョブの SSE で進みと結果を読む。
import { authHeaders } from '../authToken'
import { subscribeJob, type JobHandle } from '../api'
import { llmHeaders, type LlmCredentials } from './store'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export type Verdict = 'good' | 'limited' | 'poor'

export interface AptitudeFailure {
  kind: string
  index: number
  reason: string
}

export interface AptitudeResult {
  verdict: Verdict
  /** 0〜1 の率 */
  shape: number
  routing: number
  scope: number
  failed: AptitudeFailure[]
  at: string
  model?: string
}

/** 保存の鍵（"<provider>::<model>"）。 */
export function aptitudeKey(provider: string, modelId: string): string {
  return `${provider}::${modelId}`
}

/** 率（0〜1 か 0〜100）を整数のパーセントに。 */
export function pct(r: number): number {
  return Math.round(r <= 1 ? r * 100 : r)
}

/** 判定ごとの色のクラス（色はトークンだけ・CSS 側）。 */
export function verdictClass(v: Verdict): string {
  return `aptitude-badge aptitude-badge--${v}`
}

export async function fetchAptitudes(): Promise<Record<string, AptitudeResult>> {
  try {
    const res = await fetch(`${API_BASE}/api/ai/aptitude`, { headers: authHeaders() })
    if (!res.ok) return {}
    const body: unknown = await res.json()
    return body && typeof body === 'object' ? (body as Record<string, AptitudeResult>) : {}
  } catch {
    return {}
  }
}

export async function startAptitude(creds: LlmCredentials | null): Promise<string> {
  const res = await fetch(`${API_BASE}/api/ai/aptitude`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...llmHeaders(creds) },
    body: '{}',
  })
  if (!res.ok) throw new Error(`aptitude ${res.status}`)
  const body = (await res.json()) as { job_id?: string }
  if (!body.job_id) throw new Error('aptitude: no job_id')
  return body.job_id
}

export function watchAptitude(
  jobId: string,
  h: {
    onProgress: (n: number, total: number) => void
    onDone: (r: AptitudeResult) => void
    onError: (m: string) => void
  },
): JobHandle {
  return subscribeJob<AptitudeResult>(jobId, {
    onRunning: (d) => {
      if (typeof d.n === 'number' && typeof d.total === 'number') h.onProgress(d.n, d.total)
    },
    onDone: h.onDone,
    onError: h.onError,
    onCancelled: () => h.onError('cancelled'),
  })
}
