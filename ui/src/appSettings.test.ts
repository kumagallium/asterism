import { describe, expect, it, vi } from 'vitest'

const put = vi.fn(async (_s: Record<string, unknown>) => undefined)
let remote: Record<string, unknown> = {}
vi.mock('./appdata', () => ({
  initAppData: async () => ({ singleUser: true }),
  fetchAppDataSettings: async () => remote,
  putAppDataSettings: (s: Record<string, unknown>) => put(s),
}))

import { bootstrapAppSettings, flushAppSettingsNow, putAppSetting } from './appSettings'

describe('設定の丸ごと PUT', () => {
  it('サーバ側が書いた aptitude を、古い写しで消さない', async () => {
    remote = { aptitude: { 'a::m': { verdict: 'poor' } }, lang: 'ja' }
    await bootstrapAppSettings()
    // 起動後にジョブが新しい結果を書いた
    remote = { aptitude: { 'a::m': { verdict: 'good' } }, lang: 'ja' }
    putAppSetting('lang', 'en')
    flushAppSettingsNow()
    await vi.waitFor(() => expect(put).toHaveBeenCalled())
    const sent = (put.mock.calls[0] as unknown as [Record<string, unknown>])[0]
    expect(sent.lang).toBe('en')
    expect(sent.aptitude).toEqual({ 'a::m': { verdict: 'good' } })
  })
})
