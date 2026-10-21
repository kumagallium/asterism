// 会話で受けた当てはめを外す（`PUT /upper` の remove: true）— ③の「外す」が送る形。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { removeDatasetUpper } from '../api'
import chatSource from './DatasetChat.tsx?raw'
import source from './KantanWizard.tsx?raw'
import cardsSource from '../sky/SkyCards.tsx?raw'
import conversationSource from '../sky/HomeConversation.tsx?raw'
import workspaceChatSource from '../consult/WorkspaceChat.tsx?raw'
import graphChatSource from '../workspace/GraphChat.tsx?raw'

vi.mock('../authToken', async (orig) => ({
  ...(await orig<typeof import('../authToken')>()),
  authHeaders: () => ({ 'X-Asterism-Token': 'tok' }),
}))

afterEach(() => vi.unstubAllGlobals())

describe('removeDatasetUpper', () => {
  it('外す線だけを remove: true で PUT する（ほかの線を送らない）', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ upper: [] }), { status: 200, headers: { 'content-type': 'application/json' } })),
    )
    vi.stubGlobal('fetch', fetchMock)
    await removeDatasetUpper('ds 1', [{ subject: 'property:M/粒径', term: 'https://sv#g', relation: 'equivalentProperty' }])
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/datasets/ds%201/upper')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      upper: [{ subject: 'property:M/粒径', term: 'https://sv#g', relation: 'equivalentProperty', remove: true }],
    })
  })

  it('失敗は投げる', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"detail":"x"}', { status: 404 }))))
    await expect(removeDatasetUpper('d', [])).rejects.toBeTruthy()
  })
})

describe('配線（ソース文字列）', () => {
  it('③の会話の行: 公開で線になった／見送り／これから、の 3 通り。外すは会話で受けた・未消費だけ', () => {
    expect(source).toMatch(/b\.applied\s*\? t\('kantan:chat\.boundDone'\)/)
    expect(source).toMatch(/kantan:chat\.boundSkipped/)
    expect(source).toMatch(/b\.fromChat && !b\.applied && kzDatasetId/)
    expect(source).toMatch(/removeDatasetUpper\(/)
  })

  it('③の説明は 1 行（lead）で、例は入力欄の placeholder', () => {
    expect(chatSource).not.toMatch(/ws-focus-name/)
    expect(chatSource).toMatch(/placeholder=\{t\('kantan:chat\.example'\)\}/)
    expect(workspaceChatSource).toMatch(/placeholder=\{p\.placeholder\}/)
  })

  it('ホームの左の会話の札の順: 星の札 → 手で決める ▸。大きなボタンは見出しの右の小さな「AI に結ばせる」', () => {
    const about = cardsSource.indexOf('<AboutCard')
    const manual = cardsSource.indexOf('<ManualDecide')
    expect(about).toBeGreaterThan(0)
    expect(manual).toBeGreaterThan(about)
    expect(cardsSource).not.toMatch(/<DecideSlot/)
    expect(cardsSource).toMatch(/open=\{open\}/)
    expect(cardsSource).toMatch(/useState\(!p\.aiReady\)/)
    expect(conversationSource).toMatch(/headAction=\{bind\}/)
    expect(conversationSource).not.toMatch(/sky-chat-bind/)
  })

  it('履歴が空のあいだは履歴の枠を出さない・開いたままでも入力欄へフォーカスする', () => {
    expect(workspaceChatSource).toMatch(/hideEmptyHistory/)
    expect(graphChatSource).toMatch(/hideEmptyHistory && count === 0 && !thinking/)
    expect(graphChatSource).toMatch(/inputRef\.current\?\.focus\(\)/)
    expect(workspaceChatSource).toMatch(/setFocusTick\(\(n\) => n \+ 1\)/)
  })
})
