// 右の一覧と絞り込みの描画・押したときの配線（DOM の環境は無いので、hook が useTranslation だけの行の部品は関数として呼んで onClick を直に押す）。
import type { ReactElement, ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string, o?: Record<string, unknown>) => (o && 'n' in o ? `${key}#${String(o.n)}` : key), i18n: { language: 'ja' } }),
}))

import { A, B, C, D, ev, ledger, row } from './fixtures'
import { GraphFilters, GraphPlaceSide, RoleLegend } from './GraphPlace'
import { CandidateRow, RelationRow, TermRow } from './LedgerRows'
import { summarizeTerms } from './ledgerView'
import type { LedgersApi } from './useLedgers'
import { DEFAULT_FILTER } from './ledgerView'

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
const press = (el: El) => (el.props.onClick as () => void)()
const base = { open: true, onToggle: () => {}, pending: false, error: null }

describe('関係の行', () => {
  it('未確認: 「確認」と「違う」。押すと確認／拒否が渡る', () => {
    const onAct = vi.fn()
    const tree = RelationRow({ ...base, row: row({ from: A, to: B, evidence: ev(2) }), lang: 'ja', onAct })
    press(byTestId(tree, 'gp-act-confirm')[0])
    expect(onAct).toHaveBeenLastCalledWith('confirm')
    press(byTestId(tree, 'gp-act-reject')[0])
    expect(onAct).toHaveBeenLastCalledWith('reject')
    expect(byTestId(tree, 'gp-act-unconfirm')).toHaveLength(0)
  })
  it('確認済み: 「確認を取り消す」と「違う」', () => {
    const onAct = vi.fn()
    const tree = RelationRow({ ...base, row: row({ from: A, to: B, confirmed: true }), lang: 'ja', onAct })
    expect(byTestId(tree, 'gp-act-confirm')).toHaveLength(0)
    press(byTestId(tree, 'gp-act-unconfirm')[0])
    expect(onAct).toHaveBeenLastCalledWith('unconfirm')
    press(byTestId(tree, 'gp-act-reject')[0])
    expect(onAct).toHaveBeenLastCalledWith('reject')
  })
  it('「違う」とした行: 「戻す」だけ', () => {
    const onAct = vi.fn()
    const tree = RelationRow({ ...base, row: row({ from: A, to: B, state: 'dropped', rejected: true, reason: 'rejected' }), lang: 'ja', onAct })
    expect(byTestId(tree, 'gp-act-confirm')).toHaveLength(0)
    expect(byTestId(tree, 'gp-act-reject')).toHaveLength(0)
    press(byTestId(tree, 'gp-act-restore')[0])
    expect(onAct).toHaveBeenLastCalledWith('restore')
  })
  it('鍵の無い行（取り込み元の関係）は操作を出さない。書き込み中は押せない', () => {
    const noKey = RelationRow({ ...base, row: row({ from: A, to: B, key: null }), lang: 'ja', onAct: () => {} })
    expect(byTestId(noKey, 'gp-act-confirm')).toHaveLength(0)
    const busy = RelationRow({ ...base, pending: true, row: row({ from: A, to: B }), lang: 'ja', onAct: () => {} })
    expect(byTestId(busy, 'gp-act-confirm')[0].props.disabled).toBe(true)
  })
  it('開くと根拠の題・取り込み元で開くリンク・AI の理由が出る。閉じていると出ない', () => {
    const r = row({ from: A, to: B, note: '理由の文', evidence: [{ id: 'c', title: '知見の題', sourceUrl: 'https://example.org/p' }] })
    const html = renderToStaticMarkup(<ul><RelationRow {...base} row={r} lang="ja" onAct={() => {}} /></ul>)
    expect(html).toContain('知見の題')
    expect(html).toContain('href="https://example.org/p"')
    expect(html).toContain('理由の文')
    const closed = renderToStaticMarkup(<ul><RelationRow {...base} open={false} row={r} lang="ja" onAct={() => {}} /></ul>)
    expect(closed).not.toContain('知見の題')
    expect(closed).toContain('row.evidence#1')
  })
  it('見込みと文脈の語が見出しに出る', () => {
    const html = renderToStaticMarkup(<ul><RelationRow {...base} open={false} row={row({ from: A, to: B, tentative: true, context: [{ id: 't-d', name: '粒界' }] })} lang="ja" onAct={() => {}} /></ul>)
    expect(html).toContain('row.tentative')
    expect(html).toContain('row.about')
  })
})

describe('用語の行', () => {
  const rows = [row({ from: A, to: B, key: 'k1' }), row({ from: A, to: C, key: 'k2', confirmed: true })]
  it('「この語の関係を確認」を押すと渡る。未確認が無ければ出ない', () => {
    const onConfirmAll = vi.fn()
    const a = summarizeTerms(rows).find((x) => x.termId === 't-a')!
    const tree = TermRow({ ...base, term: a, lang: 'ja', onConfirmAll })
    press(byTestId(tree, 'gp-term-confirm')[0])
    expect(onConfirmAll).toHaveBeenCalledTimes(1)
    const done = summarizeTerms([row({ from: A, to: B, confirmed: true })]).find((x) => x.termId === 't-a')!
    expect(byTestId(TermRow({ ...base, term: done, lang: 'ja', onConfirmAll }), 'gp-term-confirm')).toHaveLength(0)
  })
})

describe('候補の行', () => {
  const cand = row({ from: C, to: D, state: 'none', rel: null, evidence: ev(3) })
  const props = { row: cand, lang: 'ja' as const, open: true, form: { rel: '', subject: 'a' as const }, onOpen: () => {}, onForm: () => {}, onSubmit: () => {}, onCancel: () => {}, pending: false, error: null }
  it('関係を選ぶまで「付ける」は押せない。選ぶと文の見本が出る', () => {
    expect(byTestId(CandidateRow(props), 'gp-assign-submit')[0].props.disabled).toBe(true)
    const withRel = CandidateRow({ ...props, form: { rel: 'makes', subject: 'b' } })
    expect(byTestId(withRel, 'gp-assign-submit')[0].props.disabled).toBe(false)
    const html = renderToStaticMarkup(<ul><CandidateRow {...props} form={{ rel: 'makes', subject: 'b' }} /></ul>)
    expect(html).toContain(`${D.name} で ${C.name} を作る`) // 主語を後ろの語にした文
  })
  it('向きの無い関係（競合する）は主語を選ばせない', () => {
    expect(byTestId(CandidateRow({ ...props, form: { rel: 'competes', subject: 'a' } }), 'gp-assign-subject')).toHaveLength(0)
    expect(byTestId(CandidateRow({ ...props, form: { rel: 'makes', subject: 'a' } }), 'gp-assign-subject')).toHaveLength(1)
  })
  it('「関係を付ける」を押すと欄が開く・検査に落ちた理由がその場に出る', () => {
    const onOpen = vi.fn()
    press(byTestId(CandidateRow({ ...props, open: false, onOpen }), 'gp-assign-open')[0])
    expect(onOpen).toHaveBeenCalledTimes(1)
    const html = renderToStaticMarkup(<ul><CandidateRow {...props} error="この関係は付けられません: 輪になる" /></ul>)
    expect(html).toContain('輪になる')
  })
  it('「関係を付ける」は組の鍵（pair）で出る。取り込み元の行（鍵が組の鍵でない）でも出す', () => {
    expect(byTestId(CandidateRow({ ...props, open: false, row: { ...cand, key: null } }), 'gp-assign-open')).toHaveLength(1)
  })
})

const apiOf = (over: Partial<LedgersApi>): LedgersApi => ({
  status: 'ok',
  ledgers: [],
  failed: [],
  filter: DEFAULT_FILTER,
  setFilter: () => {},
  drafts: [],
  reload: () => {},
  review: async () => {},
  assign: async () => {},
  ...over,
})
const sideProps = { lang: 'ja' as const, focus: null, onOpenImport: () => {}, point: null, onClosePoint: () => {} }

describe('右の一覧', () => {
  it('取り込み元が無い: 空の案内と「取り込み」へのリンク', () => {
    const html = renderToStaticMarkup(<GraphPlaceSide api={apiOf({})} {...sideProps} />)
    expect(html).toContain('data-testid="gp-empty"')
    expect(html).toContain('data-testid="gp-empty-import"')
  })
  it('読み込み中は一覧を出さない', () => {
    const html = renderToStaticMarkup(<GraphPlaceSide api={apiOf({ status: 'loading' })} {...sideProps} />)
    expect(html).toContain('state.loading')
    expect(html).not.toContain('gp-empty')
  })
  it('数百行でも 1 度に 50 行＋「もっと見る」（残りの数）', () => {
    const rows = Array.from({ length: 120 }, (_, i) => row({ from: A, to: B, key: `k${i}`, evidence: ev(i % 5) }))
    const html = renderToStaticMarkup(<GraphPlaceSide api={apiOf({ ledgers: [ledger('a', rows)] })} {...sideProps} />)
    expect(html.match(/data-testid="gp-relation-row"/g)).toHaveLength(50)
    expect(html).toContain('act.more#70')
  })
  it('並びは未確認 → 確認済み', () => {
    const rows = [row({ from: A, to: B, key: 'c', confirmed: true }), row({ from: A, to: C, key: 'u' })]
    const html = renderToStaticMarkup(<GraphPlaceSide api={apiOf({ ledgers: [ledger('a', rows)] })} {...sideProps} />)
    expect(html.indexOf('data-status="unconfirmed"')).toBeLessThan(html.indexOf('data-status="confirmed"'))
  })
  it('本物の点を押したときの 1 枚は、一覧の上に出る（一覧は残る）', () => {
    const html = renderToStaticMarkup(
      <GraphPlaceSide api={apiOf({ ledgers: [ledger('a', [row({ from: A, to: B })])] })} {...sideProps} point={<p data-testid="card">1 枚</p>} />,
    )
    expect(html.indexOf('data-testid="gp-point"')).toBeLessThan(html.indexOf('data-testid="gp-tab-relations"'))
    expect(html).toContain('data-testid="gp-relations"')
    expect(html).toContain('data-testid="gp-point-close"')
  })
  it('「違うとしたもの」の絞り込みでは「戻す」の行が出る', () => {
    const rows = [row({ from: A, to: B, state: 'dropped', rejected: true, reason: 'rejected' })]
    const api = apiOf({ ledgers: [ledger('a', rows)], filter: { ...DEFAULT_FILTER, status: 'rejected' } })
    const html = renderToStaticMarkup(<GraphPlaceSide api={api} {...sideProps} focus={null} />)
    expect(html).toContain('data-status="rejected"')
  })
  it('図で押した語を focus で渡すと「用語」のタブでその語が開く', () => {
    const rows = [row({ from: A, to: B, key: 'k1' })]
    const html = renderToStaticMarkup(
      <GraphPlaceSide api={apiOf({ ledgers: [ledger('a', rows)] })} {...sideProps} focus={{ source: 'a', termId: 't-a', nonce: 1 }} />,
    )
    expect(html).toContain('data-testid="gp-terms"')
    expect(html).toContain('data-testid="gp-term-confirm"')
  })
})

describe('絞り込み', () => {
  it('取り込み元・状態・関係の種類・候補のトグルと件数が出る。読めなかった取り込み元は 1 行で知らせる', () => {
    const api = apiOf({
      ledgers: [ledger('a', [row({ from: A, to: B, rel: 'lowers' }), row({ from: C, to: D, state: 'none', rel: null })])],
      failed: ['別の元'],
    })
    const html = renderToStaticMarkup(<GraphFilters api={api} lang="ja" />)
    for (const id of ['gp-filter-source', 'gp-filter-status', 'gp-filter-rel', 'gp-filter-candidates', 'gp-failed']) expect(html).toContain(`data-testid="${id}"`)
    expect(html).toContain('下げる')
    expect(html).toContain('candidates.count#1')
  })
  it('凡例に 4 色', () => {
    const html = renderToStaticMarkup(<RoleLegend />)
    for (const k of ['m', 'p', 't', 'c']) expect(html).toContain(`gp-tone--${k}`)
  })
})
