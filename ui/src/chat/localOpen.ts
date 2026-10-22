// 「中身」の開閉を覚える（localStorage。読めなくても落ちない・既定は開く）。
export const CONTENTS_OPEN_KEY = 'chat.contents.open'

export const readOpen = (): boolean => {
  try {
    return localStorage.getItem(CONTENTS_OPEN_KEY) !== '0'
  } catch {
    return true
  }
}
export const writeOpen = (on: boolean): void => {
  try {
    localStorage.setItem(CONTENTS_OPEN_KEY, on ? '1' : '0')
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}
