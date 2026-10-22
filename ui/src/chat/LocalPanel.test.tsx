import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../i18n'
import { EMPTY_SKY, type SkyData } from '../sky/skyApi'
import { localSky } from './graph/neighborhood'
import { LocalPanel } from './LocalPanel'
import { LOCAL_OPEN_KEY, readOpen, writeOpen } from './localOpen'

const sky: SkyData = {
  ...EMPTY_SKY,
  terms: [{ iri: 'urn:a', slug: 'a', label: '熱伝導率', label_en: 'k', kind: 'class', members: [], standard: null, answering: 0, questions: [] }],
}
const panel = (termIris: string[]) =>
  renderToStaticMarkup(<LocalPanel lang="ja" local={localSky(sky, [], [], { termIris: new Set(termIris), draftIds: new Set(), selectedId: null })} />)

const stubStorage = (init: Record<string, string> = {}) => {
  const m = new Map(Object.entries(init))
  vi.stubGlobal('localStorage', { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) })
  return m
}
afterEach(() => vi.unstubAllGlobals())

describe('LocalPanel', () => {
  it('核が空なら何も出さない', () => {
    stubStorage()
    expect(panel([])).toBe('')
  })
  it('既定は開く: 見出し・折りたたみボタン・中身', () => {
    stubStorage()
    const html = panel(['urn:a'])
    expect(html).toContain('この話の周り')
    expect(html).toContain('data-testid="local-toggle"')
    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain('chart-pane')
  })
  it('覚えている「たたむ」で始まり、中身を出さない', () => {
    stubStorage({ [LOCAL_OPEN_KEY]: '0' })
    const html = panel(['urn:a'])
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('chart-pane')
  })
  it('開閉を localStorage に書く。読めなくても落ちない', () => {
    const m = stubStorage()
    writeOpen(false)
    expect(m.get(LOCAL_OPEN_KEY)).toBe('0')
    expect(readOpen()).toBe(false)
    writeOpen(true)
    expect(readOpen()).toBe(true)
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    })
    expect(readOpen()).toBe(true)
    expect(() => writeOpen(false)).not.toThrow()
  })
})
