import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { AptitudeView } from './AptitudeRow'
import { pct, verdictClass, type AptitudeResult } from './aptitudeApi'

const base: AptitudeResult = { verdict: 'good', shape: 0.95, routing: 0.9, scope: 0.8, failed: [], at: 't' }

describe('AptitudeView', () => {
  it('推奨のバッジと 3 つの率', () => {
    const h = renderToStaticMarkup(<AptitudeView result={base} />)
    expect(h).toContain('data-testid="aptitude-badge"')
    expect(h).toContain('aptitude-badge--good')
    expect(h).toContain('推奨')
    expect(h).toContain('形 95%・振り分け 90%・対象 80%')
  })
  it('条件つきは案内つき・非推奨は muted のクラス', () => {
    const l = renderToStaticMarkup(<AptitudeView result={{ ...base, verdict: 'limited', shape: 0.5 }} />)
    expect(l).toContain('条件つき')
    expect(l).toContain('対象を絞って使うと通りやすい設定です')
    const r = renderToStaticMarkup(<AptitudeView result={{ ...base, verdict: 'limited', routing: 0.8 }} />)
    expect(r).toContain('会話が提案に混ざる')
    const p = renderToStaticMarkup(<AptitudeView result={{ ...base, verdict: 'poor' }} />)
    expect(p).toContain('aptitude-badge--poor')
  })
  it('落ちた問があれば見るボタン・結果が無ければ未検査', () => {
    const f = renderToStaticMarkup(
      <AptitudeView result={{ ...base, failed: [{ kind: 'shape', index: 2, reason: 'x' }] }} />,
    )
    expect(f).toContain('落ちた問を見る')
    expect(renderToStaticMarkup(<AptitudeView result={null} />)).toContain('未検査')
  })
  it('判定ごとの色のクラスと率の丸め', () => {
    expect(verdictClass('good')).toContain('--good')
    expect(verdictClass('limited')).toContain('--limited')
    expect(verdictClass('poor')).toContain('--poor')
    expect(pct(0.876)).toBe(88)
    expect(pct(90)).toBe(90)
  })
})
