import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../i18n'
import i18n from '../i18n'
import type { ContentsLoad, ReplyTable } from './chatApi'
import { ContentsPanel, FromPaperLine } from './ContentsPanel'
import { UNPUBLISHED } from './contentsKey'
import { PaperReaderContext } from './paperReaderContext'

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
  it('論文の表から取り込んだデータセット: 出所の 1 行が出て、押すと論文を開く（13e）', () => {
    const sp = { dataset_id: 'paper-1', table_id: 'tbl2', label: 'Table 2', caption: '熱伝導率', name: '論文X' }
    const data = load({ tables: [{ dataset: { id: 'a', label: 'データ A', source_paper: sp }, table: table('試料') }, { dataset: { id: 'a', label: 'データ A', source_paper: sp }, table: table('測定') }] })
    const open = vi.fn()
    const h = html(
      <PaperReaderContext.Provider value={{ open }}>
        <ContentsPanel target={{ dataset: 'a' }} state={{ status: 'ok', data }} />
      </PaperReaderContext.Provider>,
    )
    expect(h.match(/data-testid="contents-from-paper"/g)).toHaveLength(1)
    expect(h).toContain('論文「論文X」の表 2 から')
    expect(h).toContain('data-testid="contents-open-paper"')
    // 文脈が無ければ押せない文字のまま
    const plain = html(<ContentsPanel target={{ dataset: 'a' }} state={{ status: 'ok', data }} />)
    expect(plain).toContain('論文「論文X」の表 2 から')
    expect(plain).not.toContain('contents-open-paper')
  })
  it('出所の行を押すと open が呼ばれる（13e）', () => {
    const open = vi.fn()
    const p = FromPaperLine({ text: '論文「X」の表 2 から', caption: '', open })
    const button = (Array.isArray(p.props.children) ? p.props.children : [p.props.children]).find((c: unknown) => c && typeof c === 'object')
    expect(button.props['data-testid']).toBe('contents-open-paper')
    button.props.onClick()
    expect(open).toHaveBeenCalledWith(null)
    // open が無ければ押せない文字
    expect(FromPaperLine({ text: 'x', caption: '', open: null }).props.children).toBe('x')
  })
  it('出所の無いデータセットは何も足さない', () => {
    const h = html(<ContentsPanel target={{ term: 't' }} state={{ status: 'ok', data: load() }} />)
    expect(h).not.toContain('contents-from-paper')
  })
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
