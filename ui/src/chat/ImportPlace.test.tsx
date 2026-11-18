import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import type { SkyDataset } from '../sky/skyApi'
import { ImportPlace } from './ImportPlace'
import { importRows } from './importRows'

const noop = () => {}
const text = (html: string) => html.replace(/<[^>]*>/g, '')
const ds = (over: Partial<SkyDataset> = {}): SkyDataset => ({
  id: 'd1',
  label: '価格表',
  created_at: '2026-11-10T00:00:00Z',
  kinds: [
    { iri: 'k1', label: '商品', count: 1200, links: [], standard: null },
    { iri: 'k2', label: '店', count: 34, links: [], standard: null },
  ],
  ...over,
})
const place = (over: Partial<Parameters<typeof ImportPlace>[0]> = {}) =>
  renderToStaticMarkup(
    <ImportPlace
      datasets={[ds()]}
      proposed={[]}
      status="ok"
      onRetry={noop}
      busy={false}
      noKey={false}
      sourceHome={null}
      onAttach={noop}
      onAttachFromSource={async () => true}
      onOpenDataset={noop}
      lang="ja"
      {...over}
    />,
  )

describe('importRows', () => {
  it('公開済み → 未公開・各群は新しい順・壊れた日付は最後で date null', () => {
    const rows = importRows(
      [ds({ id: 'a', created_at: '2026-01-01' }), ds({ id: 'b', created_at: '2026-06-01' }), ds({ id: 'x', created_at: 'oops' })],
      [ds({ id: 'p1', created_at: '2026-02-01', proposed: true }), ds({ id: 'p2', created_at: '2026-03-01', proposed: true })],
    )
    expect(rows.map((r) => r.id)).toEqual(['b', 'a', 'x', 'p2', 'p1'])
    expect(rows.map((r) => r.published)).toEqual([true, true, true, false, false])
    expect(rows[2].created).toBeNull()
  })
  it('種類の数と件数の合計・論文は null', () => {
    const [a, b] = importRows([ds(), ds({ id: 'doc', document: true, kinds: [] })], [])
    expect(a.kinds).toBe(2)
    expect(a.rows).toBe(1234)
    expect(b.paper).toBe(true)
    expect(b.kinds).toBeNull()
    expect(b.rows).toBeNull()
  })
})

describe('ImportPlace', () => {
  it('見出し・入口の札・「この PC のファイル」と隠した input・Graphium は出ない', () => {
    const html = place()
    expect(html).toContain('<h1')
    expect(text(html)).toContain('ファイルを置く')
    expect(html).toContain('data-testid="import-attach-local"')
    expect(html).toContain('data-testid="import-attach-input"')
    expect(text(html)).toContain('この PC のファイル')
    expect(html).not.toContain('Graphium')
  })
  it('取り込み元があるときだけ 2 つ目の入口', () => {
    expect(place()).not.toContain('import-attach-source')
    const html = place({ sourceHome: 'graphium' })
    expect(html).toContain('data-testid="import-attach-source"')
    expect(text(html)).toContain('graphium のファイル')
  })
  it('busy で入口のボタンを止める', () => {
    expect(place({ busy: true })).toMatch(/disabled=""[^>]*data-testid="import-attach-local"|data-testid="import-attach-local"[^>]*disabled=""/)
  })
  it('空: まだ何も取り込んでいません', () => {
    const html = place({ datasets: [] })
    expect(text(html)).toContain('まだ何も取り込んでいません。')
    expect(html).not.toContain('import-dataset-row')
  })
  it('公開済みの行: 名前・種類の数と件数（3 桁区切り）・日付・札は出ない', () => {
    const html = place()
    expect(html.match(/data-testid="import-dataset-row"/g)).toHaveLength(1)
    const t = text(html)
    expect(t).toContain('価格表')
    expect(t).toContain('種類 2・1,234 件')
    expect(t).toContain('2026')
    expect(t).not.toContain('まだ公開していません')
    expect(html).not.toContain('import-row-tag')
  })
  it('未公開の行は札・論文は「論文」の札で種類と件数が出ない・行にボタンは 1 つだけ', () => {
    const html = place({
      datasets: [ds({ id: 'doc', label: '熱電の論文', document: true, kinds: [] })],
      proposed: [ds({ id: 'p', label: '下書き表', proposed: true })],
    })
    const t = text(html)
    expect(html).toContain('import-row-tag">論文<')
    expect(t).toContain('まだ公開していません')
    expect(t.match(/種類 \d/g)).toHaveLength(1)
    expect(t.indexOf('熱電の論文')).toBeLessThan(t.indexOf('下書き表'))
    expect(html.match(/<button[^>]*data-testid="import-dataset-row"/g)).toHaveLength(2)
    const rows = html.slice(html.indexOf('imp-ds'))
    expect(rows.match(/<button/g)).toHaveLength(2)
  })
  it('鍵が無いときは入口の代わりに 1 行', () => {
    const html = place({ noKey: true })
    expect(html).not.toContain('import-attach-local')
    expect(text(html)).toContain('設定 › サーバで鍵を入れると、チャットを保存できます')
  })
  it('loading・error', () => {
    expect(text(place({ status: 'loading' }))).toContain('読み込み中…')
    expect(place({ status: 'loading' })).not.toContain('import-dataset-row')
    const e = place({ status: 'error' })
    expect(text(e)).toContain('知識グラフを読み込めませんでした')
    expect(text(e)).toContain('もう一度')
  })
  it('英語: 数は lang で整える', () => {
    expect(text(place({ lang: 'en' }))).toContain('1,234')
  })
})
