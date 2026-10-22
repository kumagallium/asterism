import { describe, expect, it } from 'vitest'
import { IME_CONFIRM_KEY_WINDOW_MS, isImeKeyEvent, shouldSubmitOnEnter } from './imeEnter'

const plain = { composingNow: false, isComposing: false, keyCode: 13, msSinceCompositionEnd: 10_000 }

describe('imeEnter', () => {
  it('普通の Enter は送る・Shift+Enter は送らない', () => {
    expect(shouldSubmitOnEnter({ ...plain, isEnter: true, shiftKey: false })).toBe(true)
    expect(shouldSubmitOnEnter({ ...plain, isEnter: true, shiftKey: true })).toBe(false)
    expect(shouldSubmitOnEnter({ ...plain, isEnter: false, shiftKey: false })).toBe(false)
  })
  it('Chrome: 変換中（isComposing・keyCode 229）は送らない', () => {
    expect(isImeKeyEvent({ ...plain, isComposing: true })).toBe(true)
    expect(isImeKeyEvent({ ...plain, keyCode: 229 })).toBe(true)
    expect(isImeKeyEvent({ ...plain, composingNow: true })).toBe(true)
  })
  it('WebKit: compositionend 直後の確定 Enter（普通の Enter に見える）は送らない', () => {
    expect(isImeKeyEvent({ ...plain, msSinceCompositionEnd: 0 })).toBe(true)
    expect(isImeKeyEvent({ ...plain, msSinceCompositionEnd: IME_CONFIRM_KEY_WINDOW_MS - 1 })).toBe(true)
    expect(isImeKeyEvent({ ...plain, msSinceCompositionEnd: IME_CONFIRM_KEY_WINDOW_MS })).toBe(false)
  })
})
