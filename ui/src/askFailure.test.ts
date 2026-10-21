import { describe, expect, it } from 'vitest'
import i18n from './i18n'
import { askFailureMessage } from './demoApi'

describe('askFailureMessage', () => {
  it('Copilot の構造化 detail は設定画面と同じ案内文になる', async () => {
    await i18n.changeLanguage('ja')
    const body = JSON.stringify({ detail: { code: 'copilot_cli_missing', message: 'raw' } })
    expect(askFailureMessage(400, body)).toBe(i18n.t('settings:form.copilotError.copilot_cli_missing'))
    expect(askFailureMessage(400, body)).toContain('npm install')
  })

  it('知らない code は message を出す', () => {
    const body = JSON.stringify({ detail: { code: 'other', message: 'ひとこと' } })
    expect(askFailureMessage(400, body)).toBe('ひとこと')
  })

  it('文字列 detail は従来どおり生のまま', () => {
    const body = JSON.stringify({ detail: 'boom' })
    expect(askFailureMessage(500, body)).toBe(`ask failed (HTTP 500): ${body}`)
    expect(askFailureMessage(502, '')).toBe('ask failed (HTTP 502)')
  })
})
