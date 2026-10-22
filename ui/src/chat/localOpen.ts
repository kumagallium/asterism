// 「この話の周り」の開閉を覚える（localStorage。読めなくても落ちない・既定は開く）。
export const LOCAL_OPEN_KEY = 'asterism.chat.localOpen'

export const readOpen = (): boolean => {
  try {
    return localStorage.getItem(LOCAL_OPEN_KEY) !== '0'
  } catch {
    return true
  }
}
export const writeOpen = (on: boolean): void => {
  try {
    localStorage.setItem(LOCAL_OPEN_KEY, on ? '1' : '0')
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}
