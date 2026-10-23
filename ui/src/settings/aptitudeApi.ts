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
    const body = (await res.json()) as { aptitude?: unknown } | null
    const t = body?.aptitude
    return t && typeof t === 'object' ? (t as Record<string, AptitudeResult>) : {}
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

/** 落ちた問の表示用の分け方。種類ごとに 1 から数える（形の修正は index 5〜9 → 修正 #1〜5）。 */
export function failureParts(f: AptitudeFailure): { kindKey: string; no: number; reason: string } {
  const isDiff = f.kind === 'shape' && (f.reason === 'diff' || f.index >= 5)
  const kindKey = f.kind === 'shape' ? (isDiff ? 'shapeDiff' : 'shapeCreate') : f.kind
  const no = (isDiff ? f.index - 5 : f.index) + 1
  return { kindKey, no, reason: f.reason }
}

/** 条件つきの案内の種類: 形か対象が足りない → scope、振り分けだけ → routing。 */
export function limitedHintKey(r: AptitudeResult): 'limitedHint' | 'limitedHintRouting' {
  return pct(r.shape) < 90 || pct(r.scope) < 80 ? 'limitedHint' : 'limitedHintRouting'
}
