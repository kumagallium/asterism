import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { NavRail } from './NavRail'
import { PlaceTabbar } from './PlaceTabbar'

const noop = () => {}
const text = (html: string) => html.replace(/<[^>]*>/g, '')
const rail = (over: Partial<Parameters<typeof NavRail>[0]> = {}) =>
  renderToStaticMarkup(
    <NavRail place="graph" onPlace={noop} folded={false} showToggle sheet={false} onToggle={noop} onOpenSettings={noop} {...over}>
      <div data-testid="the-list">一覧</div>
    </NavRail>,
  )

describe('NavRail', () => {
  it('項目は 3 つで、取り込み・知識グラフ・チャットの順', () => {
    const html = rail()
    const ids = [...html.matchAll(/data-testid="nav-(import|graph|chat)"/g)].map((m) => m[1])
    expect(ids).toEqual(['import', 'graph', 'chat'])
    const t = text(html)
    expect(t.indexOf('取り込み')).toBeLessThan(t.indexOf('知識グラフ'))
    expect(t.indexOf('知識グラフ')).toBeLessThan(t.indexOf('チャット'))
    expect(html).toContain('aria-label="場所"')
  })
  it('今の場所だけ aria-current と is-active', () => {
    const html = rail({ place: 'import' })
    expect(html.match(/aria-current="page"/g)).toHaveLength(1)
    expect(html).toMatch(/is-active[^>]*data-testid="nav-import"[^>]*aria-current="page"/)
    expect(rail({ place: null })).not.toContain('aria-current')
  })
  it('チャットの一覧は「チャット」の項目の後ろに出る・≡ と ⚙ もある', () => {
    const html = rail()
    expect(html.indexOf('data-testid="nav-chat"')).toBeLessThan(html.indexOf('the-list'))
    expect(html.indexOf('data-testid="nav-graph"')).toBeLessThan(html.indexOf('data-testid="nav-chat"'))
    expect(html).toContain('list-toggle')
    expect(html).toContain('設定')
  })
  it('showToggle が false なら ≡ は出ない', () => {
    expect(rail({ showToggle: false })).not.toContain('list-toggle')
  })
  it('たたんだ形: 名前は aria-label・children は出ない', () => {
    const html = rail({ folded: true })
    expect(html).toContain('aria-label="取り込み"')
    expect(html).toContain('aria-label="知識グラフ"')
    expect(html).toContain('aria-label="チャット"')
    expect(text(html)).not.toContain('取り込み')
    expect(html).not.toContain('the-list')
    expect(html).toContain('list-toggle')
    expect(html.match(/data-testid="nav-/g)).toHaveLength(3)
  })
  it('シート: 3 つの項目・≡・⚙ は出ず、ブランドと children だけ', () => {
    const html = rail({ sheet: true })
    expect(html).not.toContain('data-testid="nav-')
    expect(html).not.toContain('list-toggle')
    expect(html).not.toContain('aria-label="設定')
    expect(html).toContain('chat-brand')
    expect(html).toContain('the-list')
  })
})

describe('PlaceTabbar', () => {
  it('3 つのタブが仕事の順・名前つき・nav に名前', () => {
    const html = renderToStaticMarkup(<PlaceTabbar place="chat" onPlace={noop} />)
    expect([...html.matchAll(/data-testid="place-tab-(import|graph|chat)"/g)].map((m) => m[1])).toEqual(['import', 'graph', 'chat'])
    expect(text(html)).toBe('取り込み知識グラフチャット')
    expect(html).toContain('aria-label="場所"')
  })
  it('今の場所だけ aria-current', () => {
    const html = renderToStaticMarkup(<PlaceTabbar place="graph" onPlace={noop} />)
    expect(html.match(/aria-current="page"/g)).toHaveLength(1)
    expect(html).toMatch(/is-active[^>]*data-testid="place-tab-graph"[^>]*aria-current="page"/)
  })
})
