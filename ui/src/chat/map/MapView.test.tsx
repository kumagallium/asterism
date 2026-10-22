import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { EMPTY_SKY } from '../../sky/skyApi'
import { bindMapEvents, buildMapGraph } from './mapModel'

vi.mock('sigma', () => ({ default: class {} }))

import { MapView } from './MapView'

const sky = {
  ...EMPTY_SKY,
  terms: [{ iri: 'urn:t:a', slug: 'a', label: 'あ', label_en: 'a', kind: 'class' as const, members: [], standard: null, answering: 0, questions: [] }],
}

describe('MapView', () => {
  it('枠・canvas 置き場・全体を見るボタンが出る（WebGL が無くても落ちない）', () => {
    const html = renderToStaticMarkup(
      <MapView
        sky={sky}
        drafts={[]}
        proposedDatasets={[]}
        lang="ja"
        height={300}
        focusIds={new Set()}
        selectedId={null}
        highlightIds={null}
        removeIds={new Set()}
        onSelect={() => {}}
        ariaLabel="全体の地図"
        fitLabel="全体を見る"
      />,
    )
    expect(html).toContain('data-testid="chat-map"')
    expect(html).toContain('data-testid="map-fit"')
    expect(html).toContain('role="img"')
    expect(html).toContain('aria-label="全体の地図"')
    expect(html).toContain('全体を見る')
  })

  it('クリックで onSelect、hover で onHover が呼ばれる', () => {
    const handlers: Record<string, (e?: { node: string }) => void> = {}
    const fake = { on: (ev: string, fn: (e?: { node: string }) => void) => ((handlers[ev] = fn), fake) }
    const g = buildMapGraph(sky, [], [], 'ja')
    const onSelect = vi.fn()
    const onHover = vi.fn()
    bindMapEvents(fake as never, g, onSelect, onHover)
    handlers.clickNode({ node: 't|urn:t:a' })
    expect(onSelect).toHaveBeenCalledWith({ kind: 'term', iri: 'urn:t:a', label: 'あ' }, 't|urn:t:a')
    handlers.clickStage()
    expect(onSelect).toHaveBeenLastCalledWith(null, null)
    handlers.enterNode({ node: 't|urn:t:a' })
    handlers.leaveNode()
    expect(onHover.mock.calls).toEqual([['t|urn:t:a'], [null]])
  })
})
