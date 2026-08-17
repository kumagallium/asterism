// デスクトップ版の自動更新 — SPA 側（Graphium の lib/updater.ts と同じ形）。
// 起動後と 24 時間ごとに更新を確認し、見つかれば CustomEvent で UI（画面上部の
// UpdateBanner）に知らせる。設定→このアプリ の「今すぐ確認」からも呼ぶ。
//
// 窓は http://127.0.0.1:<port> のリモートオリジンだが、シェルがそのオリジンに
// updater と relaunch の IPC だけを開けている（desktop/src-tauri/src/lib.rs
// `grant_spa_update_ipc`）。ダウンロードと差し替えは Tauri の updater プラグインが
// 署名を検証して行い、接続先（endpoints）と公開鍵は tauri.conf.json 固定＝この
// コードから変えられない。ブラウザ版・web 配備では isTauri() が false で何もしない。
//
// Tauri の JS パッケージは dynamic import: web 版のバンドルには別チャンクとして
// 存在するだけで、デスクトップ以外では一度も読み込まれない。

import type { Update } from '@tauri-apps/plugin-updater'

/** UpdateBanner が購読する window イベント名。detail は UpdateAvailableDetail。 */
export const UPDATE_AVAILABLE_EVENT = 'asterism-update-available'

const FIRST_CHECK_DELAY_MS = 5_000
const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1_000

export interface DownloadProgress {
  downloaded: number
  /** Content-Length が無いフィードでは null（％を出せない）。 */
  total: number | null
}

export interface UpdateAvailableDetail {
  version: string
  currentVersion: string
  /** ダウンロード→差し替え→再起動。成功すれば戻らない（アプリが再起動する）。 */
  install: (onProgress?: (p: DownloadProgress) => void) => Promise<void>
}

export type CheckResult =
  | { status: 'unsupported' }
  | { status: 'up-to-date' }
  | { status: 'available'; version: string }
  | { status: 'error'; message: string }

/** Tauri のウィンドウ内か（IPC ブートストラップが注入されているか）。
 *  リモートオリジンでも Tauri は毎ページに注入するので、これが true なら
 *  invoke まで届く（許可されているかはシェルの capability 次第）。 */
export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

// 直近の確認で見つかった更新。バナーがマウント前だった場合や再マウント時に
// 取りこぼさないよう、イベントに加えてここからも読める。
let pending: { detail: UpdateAvailableDetail; handle: Update } | null = null

/** 直近の確認で見つかっている更新（無ければ null）。 */
export function pendingUpdate(): UpdateAvailableDetail | null {
  return pending?.detail ?? null
}

function retire(): void {
  // 前回の Update ハンドルは Rust 側のリソース。差し替え/解消時に返す。
  const prev = pending
  pending = null
  if (prev) void prev.handle.close().catch(() => {})
}

/**
 * 更新を確認する。Tauri 環境でなければ "unsupported"。更新があれば
 * UPDATE_AVAILABLE_EVENT も発火する（バナーが拾う）。
 */
export async function checkForUpdates(): Promise<CheckResult> {
  if (!isTauri()) return { status: 'unsupported' }
  try {
    const { check } = await import('@tauri-apps/plugin-updater')
    const update = await check()
    if (!update) {
      retire()
      return { status: 'up-to-date' }
    }
    const detail: UpdateAvailableDetail = {
      version: update.version,
      currentVersion: update.currentVersion,
      install: async (onProgress) => {
        let downloaded = 0
        let total: number | null = null
        await update.downloadAndInstall((event) => {
          if (event.event === 'Started') {
            total = event.data.contentLength ?? null
          } else if (event.event === 'Progress') {
            downloaded += event.data.chunkLength
            onProgress?.({ downloaded, total })
          } else if (event.event === 'Finished') {
            onProgress?.({ downloaded, total: total ?? downloaded })
          }
        })
        const { relaunch } = await import('@tauri-apps/plugin-process')
        await relaunch()
      },
    }
    retire()
    pending = { detail, handle: update }
    window.dispatchEvent(new CustomEvent(UPDATE_AVAILABLE_EVENT, { detail }))
    return { status: 'available', version: update.version }
  } catch (e) {
    // フィード到達不能・IPC 不許可（古いシェル）・署名不一致など。呼び出し側は
    // 「確認に失敗」を出す＝誤って「最新です」とは言わない。
    return { status: 'error', message: e instanceof Error ? e.message : String(e) }
  }
}

/** 起動時に 1 回呼ぶ。5 秒後に初回確認、以後 24 時間ごと。 */
export function initUpdater(): void {
  if (!isTauri()) return
  window.setTimeout(() => void checkForUpdates(), FIRST_CHECK_DELAY_MS)
  window.setInterval(() => void checkForUpdates(), CHECK_INTERVAL_MS)
}
