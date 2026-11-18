// 候補の「関係を付ける」: 2 語は pair の順（a・b）で並べ、from／to の向きと思い込まない。
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string, o?: Record<string, unknown>) => (o ? `${key}:${JSON.stringify(o)}` : key), i18n: { language: 'ja' } }),
}))

import { relSentence } from '../sentenceRows'
import { A, B, row } from './fixtures'
import { CandidateRow } from './LedgerRows'

const props = { lang: 'ja' as const, onOpen: () => {}, onForm: () => {}, onSubmit: () => {}, onCancel: () => {}, pending: false, error: null }
// from＝B・to＝A だが、pair は id の文字列順（t-a|t-b）＝ a は A
const cand = row({ from: B, to: A, state: 'none', rel: null, key: 'o:x' })

describe('候補の行', () => {
  it('2 語は pair の順に並び、主語の選択肢の先頭も a（from の向きに依らない）', () => {
    const html = renderToStaticMarkup(<ul><CandidateRow {...props} open row={cand} form={{ rel: 'lowers', subject: 'a' }} /></ul>)
    expect(html.indexOf(A.name)).toBeLessThan(html.indexOf(B.name))
    expect(html).toContain(`<option value="a" selected="">${A.name}</option>`)
  })
  it('プレビューの文は、実際に書かれる向き（a＝pair の先の語）と一致する', () => {
    const a = renderToStaticMarkup(<ul><CandidateRow {...props} open row={cand} form={{ rel: 'lowers', subject: 'a' }} /></ul>)
    expect(a).toContain(relSentence('lowers', A.name, B.name, 'ja'))
    const b = renderToStaticMarkup(<ul><CandidateRow {...props} open row={cand} form={{ rel: 'lowers', subject: 'b' }} /></ul>)
    expect(b).toContain(relSentence('lowers', B.name, A.name, 'ja'))
  })
  it('取り込み元の言い分は行の中に出す', () => {
    const html = renderToStaticMarkup(<ul><CandidateRow {...props} open={false} row={{ ...cand, originSentence: '焼結 は ゲルマ の一種' }} form={{ rel: '', subject: 'a' }} /></ul>)
    expect(html).toContain('candidates.origin')
    expect(html).toContain('焼結 は ゲルマ の一種')
  })
})
