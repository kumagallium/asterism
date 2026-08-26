// 更新の「ダウンロード → 差し替え → 再起動」を実行する共通のフック。
//
// 押せる場所は 2 つある（画面上部のバナー／設定 →「このアプリ」）が、走らせて
// よい更新は同時に 1 つだけ。実行中かどうかはモジュールに 1 つだけ置き、どちらから
// 押しても両方のボタンが同時に「ダウンロード中…」になる（同じ Update ハンドルを
// 2 回 downloadAndInstall に渡すと Rust 側で失敗する）。
//
// 進捗とエラーも同じ 1 か所に置く＝バナーで始めた更新を設定画面から見ても％が続き、
// 失敗の理由がどちらにも出る。

import { useCallback, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import type { DownloadProgress, UpdateAvailableDetail } from './updater'

interface InstallState {
  running: boolean
  progress: DownloadProgress | null
  /** 直近の失敗の生メッセージ（Tauri/Rust 由来・英語）。本文には出さず title に退避する。 */
  error: string | null
}

let state: InstallState = { running: false, progress: null, error: null }
const listeners = new Set<() => void>()

function set(next: InstallState): void {
  state = next
  for (const notify of listeners) notify()
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify)
  return () => {
    listeners.delete(notify)
  }
}

export interface UpdateInstall {
  /** どこかで更新が走っている間 true（押せる場所すべてを止める）。 */
  installing: boolean
  /** ボタンに出す文言。「再起動して更新」／「ダウンロード中…」／「ダウンロード中… 42%」 */
  label: string
  /** 直近の失敗の生メッセージ（無ければ null）。 */
  error: string | null
  /** 実行する。成功すればアプリが再起動する＝呼び出し元には戻ってこない。 */
  run: (update: UpdateAvailableDetail) => Promise<void>
  /** 直近の失敗表示を消す（次の版が見つかったときなど、その失敗が古くなったら）。 */
  clearError: () => void
}

export function useUpdateInstall(): UpdateInstall {
  const { t } = useTranslation('settings')
  const shared = useSyncExternalStore(subscribe, () => state)

  const run = useCallback(async (update: UpdateAvailableDetail) => {
    // 先客がいれば黙って何もしない（2 つめのボタンを押せてしまった場合）
    if (state.running) return
    set({ running: true, progress: null, error: null })
    try {
      await update.install((progress) => set({ ...state, progress }))
      // 成功すればアプリが再起動する＝ここには戻らない
    } catch (e) {
      set({ running: false, progress: null, error: e instanceof Error ? e.message : String(e) })
    }
  }, [])

  const clearError = useCallback(() => {
    if (state.error !== null) set({ ...state, error: null })
  }, [])

  // Content-Length を返さないフィードでは％を出せない＝「ダウンロード中…」だけ
  const pct =
    shared.progress && shared.progress.total
      ? Math.min(100, Math.round((shared.progress.downloaded / shared.progress.total) * 100))
      : null
  const label = !shared.running
    ? t('updater.install')
    : pct === null
      ? t('updater.installing')
      : t('updater.downloading', { pct })

  return { installing: shared.running, label, error: shared.error, run, clearError }
}
