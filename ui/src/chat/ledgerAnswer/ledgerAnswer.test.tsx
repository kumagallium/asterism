import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ i18n: { language: 'ja' }, t: (k: string, o?: Record<string, unknown>) => (o ? `${k}${JSON.stringify(o)}` : k) }),
}))

import { answerOf } from '../chatApi'
import { KnowledgeApiError } from '../graphPlace/knowledgeRelationsApi'
import { ReplyAnswerView } from '../ReplyCard'
import { LedgerAnswerView } from './LedgerAnswerView'
import { currentUse } from './current'
import { confirmUsed } from './review'
import { itemPending, ledgerOf, relKey, type LedgerAnswer } from './types'

const block = (over: Partial<LedgerAnswer> = {}): LedgerAnswer => ({
  questions: [{ source: 'graphium', name: 'lq_a', text: 'Aを作る手法は？', count: 2 }],
  items: [
    {
      label: '液体急冷',
      sentence: '液体急冷 で A を作る',
      confirmed: false,
      tentative: true,
      context: ['合金'],
      evidence_total: 30,
      evidence: [{ title: '知見1', source_url: 'https://example.test/1' }],
      uses: [{ source: 'graphium', key: 'k1', confirmed: false }],
    },
    { label: '焼結', sentence: '焼結 で A を作る', confirmed: true, tentative: false, context: [], evidence_total: 0, evidence: [], uses: [{ source: 'graphium', key: 'k2', confirmed: true }] },
  ],
  items_total: 2,
  unconfirmed: [{ source: 'graphium', key: 'k1' }],
  unconfirmed_count: 1,
  confirmed_count: 1,
  ...over,
})

describe('answerOf / ledgerOf', () => {
  it('ledger が無ければ null・壊れた欄は捨てる', () => {
    expect(answerOf({ text: 'x' })?.ledger).toBeNull()
    expect(ledgerOf({ items: 'x' })).toBeNull()
    const l = ledgerOf({ items: [{ sentence: 's', uses: [{ key: 1 }, { source: 'a', key: 'k', confirmed: true }], evidence: [{ title: 't', source_url: '' }] }, { nope: 1 }], items_total: 0 })
    expect(l?.items).toHaveLength(1)
    expect(l?.items[0].uses).toEqual([{ source: 'a', key: 'k', confirmed: true }])
    expect(l?.items[0].evidence[0].source_url).toBeNull()
    expect(l?.items_total).toBe(1)
  })
  it('answer.ledger が通る', () => {
    expect(answerOf({ text: 'x', ledger: block() })?.ledger?.unconfirmed_count).toBe(1)
  })
})

describe('描画', () => {
  it('項目・札・見込み・文脈・帯・確認ボタン・根拠を見る', () => {
    const html = renderToStaticMarkup(<LedgerAnswerView ledger={block()} />)
    expect(html).toContain('液体急冷 で A を作る')
    expect(html).toContain('item.evidence{&quot;n&quot;:30}')
    expect(html).toContain('ledger-unconfirmed-badge')
    expect(html).toContain('ledger-tentative-badge')
    expect(html).toContain('item.about')
    expect(html).toContain('bar.unconfirmed{&quot;n&quot;:1}')
    expect(html).toContain('bar.confirm{&quot;n&quot;:1}')
    expect(html).toContain('ledger-evidence-toggle')
    expect(html.match(/ledger-unconfirmed-badge/g)).toHaveLength(1)
  })
  it('確認済みだけなら帯も札も出ない', () => {
    const l = block({ unconfirmed: [], unconfirmed_count: 0, items: [block().items[1]] })
    const html = renderToStaticMarkup(<LedgerAnswerView ledger={l} />)
    expect(html).not.toContain('ledger-bar')
    expect(html).not.toContain('ledger-unconfirmed-badge')
  })
  it('50 件を超えたら「ほか」', () => {
    expect(renderToStaticMarkup(<LedgerAnswerView ledger={block({ items_total: 80 })} />)).toContain('item.more{&quot;n&quot;:78}')
  })
  it('itemPending: 確認が通った鍵は未確認ではない', () => {
    const it0 = block().items[0]
    expect(itemPending(it0, new Set())).toBe(true)
    expect(itemPending(it0, new Set([relKey('graphium', 'k1')]))).toBe(false)
  })
})

describe('確認の配線', () => {
  const un = [
    { source: 'a', key: 'k1' },
    { source: 'b', key: 'k2' },
    { source: 'a', key: 'k3' },
  ]
  it('取り込み元ごとに分けて送る・通った分が done', async () => {
    const post = vi.fn(async (_s: string, _a: 'confirm', keys: string[]) => ({ changed: keys, skipped: [] }))
    const out = await confirmUsed(un, post)
    expect(post).toHaveBeenCalledWith('a', 'confirm', ['k1', 'k3'])
    expect(post).toHaveBeenCalledWith('b', 'confirm', ['k2'])
    expect(out.done.size).toBe(3)
    expect(out.notDone).toBe(0)
  })
  it('一部通らない: 理由が 1 つ', async () => {
    const post = async (s: string, _a: 'confirm', keys: string[]) => (s === 'a' ? { changed: ['k1'], skipped: [{ key: 'k3', reason: '行が見つかりません' }] } : { changed: keys, skipped: [] })
    const out = await confirmUsed(un, post)
    expect(out.done.size).toBe(2)
    expect(out.notDone).toBe(1)
    expect(out.reasons).toEqual(['行が見つかりません'])
  })
  it('409: ほかの取り込み元は続ける・文が理由に出る', async () => {
    const post = async (s: string, _a: 'confirm', keys: string[]) => {
      if (s === 'a') throw new KnowledgeApiError(409, '取り込み中は確認できません')
      return { changed: keys, skipped: [] }
    }
    const out = await confirmUsed(un, post)
    expect(out.done.size).toBe(1)
    expect(out.notDone).toBe(2)
    expect(out.reasons).toEqual(['取り込み中は確認できません'])
  })
})

describe('緑の印の出し分け', () => {
  const base = { text: '答え', citations: [], notes: [], warnings: [], verified_tools: ['count_by_kind'], unverified_sparql: false, answered: true }
  it('未確認を使った答えには出さない・確認済みだけなら出す・台帳が無ければ今までどおり', () => {
    expect(renderToStaticMarkup(<ReplyAnswerView answer={{ ...base, ledger: block() }} />)).not.toContain('reply-verified')
    expect(renderToStaticMarkup(<ReplyAnswerView answer={{ ...base, ledger: block({ unconfirmed: [], unconfirmed_count: 0 }) }} />)).toContain('reply-verified')
    expect(renderToStaticMarkup(<ReplyAnswerView answer={{ ...base, ledger: null }} />)).toContain('reply-verified')
    expect(renderToStaticMarkup(<ReplyAnswerView answer={{ ...base, verified_tools: [], ledger: block() }} />)).toContain('ledger-answer')
  })
})

describe('今の台帳で数え直す（currentUse）', () => {
  const un = [
    { source: 'a', key: 'k1' },
    { source: 'a', key: 'k2' },
    { source: 'a', key: 'k3' },
    { source: 'a', key: 'k4' },
    { source: 'b', key: 'k5' },
  ]
  const row = (key: string, confirmed: boolean, rejected = false) => ({ key, confirmed, rejected })
  it('確認済み・「違う」・消えた・今も未確認・読めなかった取り込み元（保存のまま）', () => {
    const rows = new Map([['a', [row('k1', true), row('k2', false, true), row('k4', false)]], ['b', null]])
    const c = currentUse(un, rows)
    expect([...c.resolved]).toEqual([relKey('a', 'k1')])
    expect([...c.rejected]).toEqual([relKey('a', 'k2')])
    expect([...c.gone]).toEqual([relKey('a', 'k3')])
    expect(c.pending).toEqual([{ source: 'a', key: 'k4' }, { source: 'b', key: 'k5' }])
  })
  it('まだ読めていない（rows が null）なら保存のまま', () => {
    expect(currentUse(un, null).pending).toEqual(un)
  })
})
