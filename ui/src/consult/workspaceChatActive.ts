// 作業場の会話（WorkspaceChat）が画面に出ているあいだ、右下の「AI に相談」（ConsultDrawer）を隠す
// ための合図。会話が 2 つにならないようにする（design.md §16.1・契約 pr7c A3）。
// コンポーネントファイルは export を component だけにする規約なので、状態はここに分ける。
import { useSyncExternalStore } from 'react'

let shown = 0
const listeners = new Set<() => void>()

function emit(): void {
  for (const fn of listeners) fn()
}

/** 会話が画面に出た。外す関数を返す（unmount で呼ぶ）。数えるので、2 つ同時に出ても崩れない。 */
export function registerWorkspaceChat(): () => void {
  shown += 1
  emit()
  let done = false
  return () => {
    if (done) return
    done = true
    shown -= 1
    emit()
  }
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** 作業場の会話がいま画面に出ているか。 */
export function useWorkspaceChatShown(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => shown > 0,
    () => false,
  )
}
