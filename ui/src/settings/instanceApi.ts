// `/api/instance` — この install の公開プロフィール。設定画面の 3 箇所
// （IRI base / このアプリ / 書き込みトークン）が同じ答えを見るので、
// モジュールレベルで 1 回だけ取得して共有する。

import { authHeaders } from '../authToken'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** 書き込みゲートから見た、この呼び出し元の立ち位置。
 *  - closed: サーバ側にトークンが無い＝誰も書き込めない
 *  - authorized: このリクエストは既にトークンを持っている（デスクトップはループバックで、
 *    本番はセッションゲート通過後に caddy が注入する）
 *  - token_required: 保護されていて、まだ有効なトークンを持っていない */
export type WriteGate = 'closed' | 'authorized' | 'token_required'

export interface InstanceInfo {
  iri_base: string
  iri_base_configured: boolean
  /** 以下は新しい api のみ。旧 api に当てた SPA では undefined になる。 */
  app_version?: string | null
  desktop?: boolean
  write_gate?: WriteGate
}

let pending: Promise<InstanceInfo | null> | null = null

/** 取得（失敗は null）。旧 api には無いフィールドがあるので、呼び出し側は
 *  欠けている前提で読む。 */
export function fetchInstanceInfo(): Promise<InstanceInfo | null> {
  if (!pending) {
    pending = fetch(`${API_BASE}/api/instance`, { headers: authHeaders() })
      .then((res) => (res.ok ? (res.json() as Promise<InstanceInfo>) : null))
      .catch(() => null)
  }
  return pending
}

/** トークンを保存した直後など、ゲートの答えが変わったとき用。 */
export function invalidateInstanceInfo(): void {
  pending = null
}
