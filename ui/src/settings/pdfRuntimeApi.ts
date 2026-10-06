// `/api/pdf-runtime` — デスクトップ版の「PDF を読み取る部品」の状態と入れ外し。
// サーバ版には経路が無い（404）。

import { authHeaders } from '../authToken'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export type PdfRuntimeState =
  | 'unsupported'
  | 'external'
  | 'absent'
  | 'installing'
  | 'starting'
  | 'ready'
  | 'failed'

export type PdfRuntimePhase = 'packages' | 'models' | 'starting'

export interface PdfRuntimeStatus {
  state: PdfRuntimeState
  phase: PdfRuntimePhase | null
  bytes_done: number
  bytes_total: number
  error: string | null
  log_path: string | null
  /** 部品のフォルダが在るか。failed のとき、消して入れ直す道を出すかどうかに使う。 */
  installed?: boolean
}

/** 取得。この環境に経路が無い（404＝サーバ版。JSON でない応答も同じ扱い）なら null。
 *  ネットワークの失敗とサーバ側の一時的な失敗（5xx）は投げる — 呼び出し側は
 *  「無い」と「いま聞けなかった」を取り違えず、直前の状態を保てる。 */
export async function fetchPdfRuntime(): Promise<PdfRuntimeStatus | null> {
  const res = await fetch(`${API_BASE}/api/pdf-runtime`)
  if (res.status >= 500) throw new Error(`HTTP ${res.status}`)
  if (!res.ok) return null
  try {
    const body = (await res.json()) as PdfRuntimeStatus
    return typeof body?.state === 'string' ? body : null
  } catch {
    return null
  }
}

async function send(method: 'POST' | 'DELETE', path: string): Promise<PdfRuntimeStatus> {
  const res = await fetch(`${API_BASE}${path}`, { method, headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as PdfRuntimeStatus
}

export function installPdfRuntime(): Promise<PdfRuntimeStatus> {
  return send('POST', '/api/pdf-runtime/install')
}

export function removePdfRuntime(): Promise<PdfRuntimeStatus> {
  return send('DELETE', '/api/pdf-runtime')
}
