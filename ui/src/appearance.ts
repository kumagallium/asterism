import { useSyncExternalStore } from 'react'

export type Appearance = 'dark' | 'light'
export const APPEARANCE_KEY = 'asterism.appearance'
const listeners = new Set<() => void>()

/** 起動時は head の同期スクリプトが設定済み。DOM を唯一の現在値として読む。 */
export function getAppearance(): Appearance {
  return typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

export function setAppearance(appearance: Appearance): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.theme = appearance
  try { localStorage.setItem(APPEARANCE_KEY, appearance) } catch { /* 保存不可でも今の画面では使える。 */ }
  for (const notify of listeners) notify()
}

function onStorage(event: StorageEvent): void {
  if (event.key !== APPEARANCE_KEY && event.key !== null) return
  if (typeof document !== 'undefined') document.documentElement.dataset.theme = event.newValue === 'light' ? 'light' : 'dark'
  for (const notify of listeners) notify()
}

/** React と canvas が同じ変更通知を購読する。 */
export function subscribeAppearance(notify: () => void): () => void {
  if (listeners.size === 0 && typeof window !== 'undefined') window.addEventListener('storage', onStorage)
  listeners.add(notify)
  return () => {
    listeners.delete(notify)
    if (listeners.size === 0 && typeof window !== 'undefined') window.removeEventListener('storage', onStorage)
  }
}

export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribeAppearance, getAppearance, () => 'dark')
}
