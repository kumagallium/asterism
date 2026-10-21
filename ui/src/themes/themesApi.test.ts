import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ThemesApiError,
  commitTheme,
  createDraft,
  deleteTheme,
  draftWithAi,
  getDraft,
  getThemeAnswers,
  saveDraft,
  themeLabel,
} from './themesApi'

// 書き込み用の鍵が設定されている状態にする（実際の鍵の置き場は sessionStorage で、この環境には無い）
vi.mock('../authToken', async (orig) => ({
  ...(await orig<typeof import('../authToken')>()),
  authHeaders: () => ({ 'X-Asterism-Token': 'tok' }),
}))

const t = (key: string) => (key === 'themes:firstThemeLabel' ? 'First questions' : key)

describe('themeLabel', () => {
  it('translates only the migration default of the first theme', () => {
    expect(themeLabel({ id: 'first', label: 'はじめの問い' }, t)).toBe('First questions')
  })
  it('keeps a label a person wrote', () => {
    expect(themeLabel({ id: 'first', label: '熱電材料' }, t)).toBe('熱電材料')
    expect(themeLabel({ id: 'other', label: 'はじめの問い' }, t)).toBe('はじめの問い')
  })
})

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

afterEach(() => vi.unstubAllGlobals())

describe('the v2 api client (6a)', () => {
  it('reads the removal result of DELETE /api/themes/{id} (200), and also a bare 204', async () => {
    const result = {
      removed_concepts: [{ slug: 'a', label: 'A' }],
      kept_concepts: [{ slug: 'b', label: 'B', reason: 'other_theme' }],
      removed_relations: [],
      kept_relations: [],
    }
    vi.stubGlobal('fetch', vi.fn(() => json(result)))
    expect(await deleteTheme('t1')).toEqual(result)
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))))
    expect(await deleteTheme('t1')).toBeNull()
  })

  it('turns a 503 into a ThemesApiError the screen can read as "no AI set up"', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: 'AI が設定されていません' }, 503)))
    await expect(draftWithAi(['Q?'], '', 'ja', null)).rejects.toMatchObject({ status: 503, detail: 'AI が設定されていません' })
    await expect(draftWithAi(['Q?'], '', 'ja', null)).rejects.toBeInstanceOf(ThemesApiError)
  })

  it('sends label and lang to /draft and reads the post-processed rows', async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      json({
        theme_label: 'T',
        concepts: [{ key: 'c1', label_ja: 'a', kind: 'class', use: 'new', slug: 'a' }],
        relations: [],
        answers: [],
        suggested_questions: [],
        standards: {},
        dropped: { concepts: [], relations: [] },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const res = await draftWithAi(['Q?'], 'T', 'en', null)
    expect(res.concepts[0].key).toBe('c1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/themes/draft')
    expect(JSON.parse(String(init?.body))).toEqual({ questions: ['Q?'], label: 'T', lang: 'en' })
  })

  it('sends the thin rows and "forget the chat" with a save, and reads the thin rows back', async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      json({
        id: 'x', label: 'L', step: 2, questions: ['Q?'], concepts: [], relations: [], answers: [],
        pending: { concepts: ['n1'], relations: [['n1', 'lowers', 'n2'], ['bad']] },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const draft = await getDraft('x')
    expect(draft.pending).toEqual({ concepts: ['n1'], relations: [['n1', 'lowers', 'n2']] })
    const pending = { concepts: ['n1'], relations: [['n1', 'lowers', 'n2']] }
    await saveDraft(draft, { pending, resetChat: true })
    const sent = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)
    expect(sent.pending).toEqual(pending)
    expect(sent.reset_chat).toBe(true)
    await saveDraft(draft)
    const plain = JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)
    expect('pending' in plain || 'reset_chat' in plain).toBe(false) // 送らなければサーバの控えを残す
  })

  it('makes a draft and reads the commit result (batch included)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ id: 'd', label: 'L', step: 1, questions: [], concepts: [], relations: [], answers: [] }, 201)))
    expect((await createDraft('', ['Q?'])).id).toBe('d')
    const out = { theme: { id: 'd', label: 'L' }, minted: ['a'], lines: 2, questions: ['kq_x'], memo: 0, batch: 'b1' }
    vi.stubGlobal('fetch', vi.fn(() => json(out, 201)))
    const body = { label: 'L', lang: 'ja', ai_drafted: true, questions: ['Q?'], concepts: [], relations: [], answers: [] }
    expect(await commitTheme('d', body)).toEqual(out)
  })

  it('names a 422 refusal (a concept not tied to a question) with the server detail', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: '問いに関係でつながらない概念があります: 粒界散乱' }, 422)))
    const body = { label: 'L', lang: 'ja', ai_drafted: true, questions: [], concepts: [], relations: [], answers: [] }
    await expect(commitTheme('d', body)).rejects.toMatchObject({ status: 422 })
    await expect(commitTheme('d', body)).rejects.toThrow(/themes:error|失敗/)
  })

  it('fetches the answers of a theme for a language', async () => {
    const fetchMock = vi.fn().mockImplementation(() => json({ theme_id: 't', answers: [{ tool_name: 'kq_x', title: 'Q', mode: 'knowledge', shape: '', knowledge: { count: 0, items: [] }, data: null, wanted_data: '' }] }))
    vi.stubGlobal('fetch', fetchMock)
    const a = await getThemeAnswers('t 1', 'en')
    expect(a[0].tool_name).toBe('kq_x')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/themes/t%201/answers?lang=en')
  })

  it('sends the write token when it opens a saved draft (the api gates GET /drafts/{id})', async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      json({ id: 'x', label: 'L', step: 1, questions: [], concepts: [], relations: [], answers: [] }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await getDraft('x 1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/themes/drafts/x%201')
    expect((init as RequestInit).headers).toMatchObject({ 'X-Asterism-Token': 'tok' })
  })
})
