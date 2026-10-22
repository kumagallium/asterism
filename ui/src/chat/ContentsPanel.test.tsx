import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../i18n'
import i18n from '../i18n'
import type { ContentsLoad, ReplyTable } from './chatApi'
import { ContentsPanel } from './ContentsPanel'
import { UNPUBLISHED } from './contentsKey'

const table = (label: string, rows = 1): ReplyTable => ({
  kind: { iri: `k:${label}`, label },
  columns: [{ iri: 'c:1', label: '列' }],
  rows: Array.from({ length: rows }, (_, i) => ({ iri: `r:${i}`, label: `行${i}`, cells: { 'c:1': { text: '値' } } })),
  total: rows,
  truncated: false,
})
const load = (over: Partial<ContentsLoad> = {}): ContentsLoad => ({
  title: '熱伝導率',
  tables: [
    { dataset: { id: 'a', label: 'データ A' }, table: table('試料') },
    { dataset: { id: 'b', label: 'データ B' }, table: table('測定') },
  ],
  more: 0,
  note: null,
  ...over,
})
const html = (el: React.ReactElement) => renderToStaticMarkup(el)

afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('ja')
})

describe('ContentsPanel', () => {
  it('何も押していない: 1 行の案内', () => {
    const h = html(<ContentsPanel target={null} state={{ status: 'idle' }} />)
    expect(h).toContain('data-testid="contents-panel"')
    expect(h).toContain('知識グラフの点を押すと、そのデータがここに出ます')
    expect(h).not.toContain('reply-table')
  })
  it('複数の表: データセットごとの小見出し＋ReplyTableView', () => {
    const h = html(<ContentsPanel target={{ term: 't' }} state={{ status: 'ok', data: load() }} name="熱伝導率" />)
    expect(h.match(/data-testid="contents-table"/g)).toHaveLength(2)
    expect(h.match(/data-testid="reply-table"/g)).toHaveLength(2)
    expect(h).toContain('データ A')
    expect(h).toContain('データ B')
    expect(h).toContain('熱伝導率 のデータ')
  })
  it('小見出しは複数のデータセットにまたがるときだけ', () => {
    const one = load({ tables: [{ dataset: { id: 'a', label: 'データ A' }, table: table('試料') }, { dataset: { id: 'a', label: 'データ A' }, table: table('測定') }] })
    const h = html(<ContentsPanel target={{ dataset: 'a' }} state={{ status: 'ok', data: one }} />)
    expect(h).not.toContain('<h4>')
  })
  it('失敗: 文言と「もう一度」', () => {
    const h = html(<ContentsPanel target={{ term: 't' }} state={{ status: 'error' }} />)
    expect(h).toContain('データを読み込めませんでした')
    expect(h).toContain('もう一度')
  })
  it('ほか N 表は「値のつながり」で', () => {
    const h = html(<ContentsPanel target={{ term: 't' }} state={{ status: 'ok', data: load({ more: 2 }) }} />)
    expect(h).toContain('ほか 2 表は「値のつながり」で')
    expect(h).toContain('data-testid="contents-full"')
  })
  it('行が 0 件: 表の枠を出さず 1 行', () => {
    const data = load({ tables: [{ dataset: { id: 'a', label: 'A' }, table: table('試料', 0) }] })
    const h = html(<ContentsPanel target={{ kind: 'k' }} state={{ status: 'ok', data }} />)
    expect(h).toContain('まだ行がありません')
    expect(h).not.toContain('reply-table')
  })
  it('未公開の提案の用語: 公開を促す 1 行', () => {
    const h = html(<ContentsPanel target={UNPUBLISHED} state={{ status: 'idle' }} />)
    expect(h).toContain('承認して公開すると、ここに行が出ます')
  })
  it('折りたたみ: 既定は開く（aria-expanded=true）、覚えていれば閉じる', () => {
    expect(html(<ContentsPanel target={null} state={{ status: 'idle' }} />)).toContain('aria-expanded="true"')
    vi.stubGlobal('localStorage', { getItem: (k: string) => (k === 'chat.contents.open' ? '0' : null), setItem: () => {} })
    const h = html(<ContentsPanel target={null} state={{ status: 'idle' }} />)
    expect(h).toContain('aria-expanded="false"')
    expect(h).toMatch(/chat-contents-body"? hidden|hidden=""[^>]*chat-contents-body|class="chat-contents-body"[^>]*hidden/)
  })
  it('英語でも出る', async () => {
    await i18n.changeLanguage('en')
    expect(html(<ContentsPanel target={null} state={{ status: 'idle' }} />)).toContain('Click a dot in the knowledge graph')
  })
})
