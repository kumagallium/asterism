// 置けなかった件の 1 行・確認の返りの store の 1 行（PR 14e-1b）。DOM の環境は無いので、hook が useTranslation だけの部品は関数として呼ぶ。
import type { ReactElement, ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    // 文言を持つ記号は鍵のまま・持たない記号は defaultValue（＝記号）を返す、本物の i18next と同じ動き
    t: (key: string, o?: Record<string, unknown>) =>
      o && 'defaultValue' in o ? (/\.(role_mismatch|kind_mismatch|cycle|self|error)$/.test(key) ? key : String(o.defaultValue)) : o && 'reason' in o ? `${key}(${String(o.reason)})` : key,
    i18n: { language: 'ja' },
  }),
}))

import en from '../../i18n/locales/en/graphPlace.json'
import ja from '../../i18n/locales/ja/graphPlace.json'
import { A, B, row } from './fixtures'
import { RelationRow } from './LedgerRows'
import type { StoreReport } from './knowledgeRelationsApi'
import { useStoreText } from './storeText'

type El = ReactElement<Record<string, unknown>>
const isEl = (n: unknown): n is El => !!n && typeof n === 'object' && 'props' in (n as object)
function byTestId(node: ReactNode, id: string, out: El[] = []): El[] {
  if (Array.isArray(node)) for (const c of node) byTestId(c, id, out)
  else if (isEl(node)) {
    if (node.props['data-testid'] === id) out.push(node)
    byTestId(node.props.children as ReactNode, id, out)
  }
  return out
}
const base = { open: false, onToggle: () => {}, pending: false, error: null, lang: 'ja' as const, onAct: () => {} }
const rep = (over: Partial<StoreReport>): StoreReport => ({ placed: 0, removed: 0, failed: [], remaining: 0, error: false, ...over })

describe('置けなかった件の行', () => {
  it('store.ok === false の行にだけ 1 行（閉じていても見える）。理由は記号ごとの文', () => {
    const bad = RelationRow({ ...base, row: row({ from: A, to: B, confirmed: true, store: { ok: false, reason: 'role_mismatch' } }) })
    const line = byTestId(bad, 'gp-store-failed')
    expect(line).toHaveLength(1)
    expect(String(line[0].props.children)).toBe('store.line(store.reason.role_mismatch)')
    expect(byTestId(RelationRow({ ...base, row: row({ from: A, to: B, confirmed: true, store: { ok: true } }) }), 'gp-store-failed')).toHaveLength(0)
    expect(byTestId(RelationRow({ ...base, row: row({ from: A, to: B }) }), 'gp-store-failed')).toHaveLength(0)
  })
  it('知らない記号・理由なしでも黙って捨てない（記号をそのまま・無ければ error）', () => {
    const s = useStoreText()
    expect(s.lineText('weird')).toBe('store.line(weird)')
    expect(s.lineText(undefined)).toBe('store.line(store.reason.error)')
  })
})

describe('確認の返りの store の 1 行', () => {
  const s = useStoreText()
  it('置けなかった件が無ければ何も出さない（全部置けた・store 無し）', () => {
    expect(s.noteText(null)).toBeNull()
    expect(s.noteText(undefined)).toBeNull()
    expect(s.noteText(rep({ placed: 3, removed: 1 }))).toBeNull()
  })
  it('置けなかった件があれば件数と最初の理由。置く段が落ちたら別の 1 行', () => {
    expect(s.noteText(rep({ failed: [{ key: 'a', reason: 'cycle' }, { key: 'b', reason: 'self' }] }))).toBe('store.failedNote(store.reason.cycle)')
    expect(s.noteText(rep({ error: true }))).toBe('store.errorNote')
  })
})

describe('文言（ja／en とも記号ごとにそろう）', () => {
  const syms = ['role_mismatch', 'kind_mismatch', 'cycle', 'self', 'error']
  it('理由の記号 5 つ・行・まとめの文が ja・en にある', () => {
    for (const d of [ja, en]) {
      for (const k of syms) expect((d.store.reason as Record<string, string>)[k]).toBeTruthy()
      expect(d.store.line).toContain('{{reason}}')
      expect(d.store.failedNote).toContain('{{n}}')
      expect(d.store.errorNote).toBeTruthy()
    }
    expect(ja.store.line).toContain('確認しましたが、正本には置けませんでした')
  })
})

describe('図で押した語を先頭に（pinFirst）', () => {
  it('その語を先頭へ・ほかの並びは変えない。無い・先頭・null はそのまま', async () => {
    const { pinFirst } = await import('./ledgerView')
    const mk = (id: string) => ({ id, source: 's', termId: id, name: id, role: null, tone: 'c' as const, relations: 1, unconfirmed: 0, rows: [] })
    const ts = ['a', 'b', 'c', 'd'].map(mk)
    expect(pinFirst(ts, 'c').map((t) => t.id)).toEqual(['c', 'a', 'b', 'd'])
    expect(pinFirst(ts, 'a')).toBe(ts)
    expect(pinFirst(ts, 'zz')).toBe(ts)
    expect(pinFirst(ts, null)).toBe(ts)
  })
})
