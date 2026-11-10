import indexHtml from '../index.html?raw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { APPEARANCE_KEY, getAppearance, setAppearance, subscribeAppearance } from './appearance'

const bootstrap = indexHtml.match(/<script>([\s\S]*?)<\/script>/)![1]

function start(saved: string | null, blocked = false) {
  const document = { documentElement: { dataset: {} as Record<string, string> } }
  const localStorage = {
    getItem: vi.fn(() => { if (blocked) throw Error('denied'); return saved }),
    setItem: vi.fn<(key: string, value: string) => void>(() => { if (blocked) throw Error('denied') }),
  }
  new Function('document', 'localStorage', bootstrap)(document, localStorage)
  vi.stubGlobal('document', document)
  vi.stubGlobal('localStorage', localStorage)
  return { document, localStorage }
}

afterEach(() => vi.unstubAllGlobals())

describe('端末ごとの外観', () => {
  it.each([null, 'invalid', 'system', 'dark'])('未設定・無効値 %s は初回描画前からダーク', (saved) => {
    const { document } = start(saved)
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(getAppearance()).toBe('dark')
  })

  it('保存したライトは最初からライトで、選択変更と通知も一致する', () => {
    const { localStorage } = start('light')
    expect(getAppearance()).toBe('light')
    const notify = vi.fn(() => expect(getAppearance()).toBe('dark'))
    const unsubscribe = subscribeAppearance(notify)
    setAppearance('dark')
    expect(localStorage.setItem).toHaveBeenCalledWith(APPEARANCE_KEY, 'dark')
    expect(notify).toHaveBeenCalledOnce()
    unsubscribe()
    setAppearance('light')
    expect(notify).toHaveBeenCalledOnce()
    // 保存値を次回の head スクリプトへ渡して再起動を検査する。
    const saved = localStorage.setItem.mock.calls.at(-1)![1]
    start(saved)
    expect(getAppearance()).toBe('light')
  })

  it('保存不可でも起動・選択・購読が動作する', () => {
    start(null, true)
    const notify = vi.fn()
    const unsubscribe = subscribeAppearance(notify)
    expect(() => setAppearance('light')).not.toThrow()
    expect(getAppearance()).toBe('light')
    expect(notify).toHaveBeenCalledOnce()
    unsubscribe()
  })
})
