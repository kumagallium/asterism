// 判定し直し（R4）: 状態の遷移・API の形・ボタンの出し分け・確かめ・走っている間・結果の 1 行・行の中の出し分け。
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string, o?: Record<string, unknown>) => (o ? `${key}${JSON.stringify(o)}` : key), i18n: { language: 'ja' } }),
}))

import { AddedRow, AddedSection } from './AddedSection'
import { fetchReaskCount, normalizeReaskResult, reaskFailureOf, startReask, type ReaskResult } from './reaskApi'
import { blockedText, resultItems, resultTail } from './reaskCount'
import { AddedDoneNotice, ReaskView } from './ReaskControl'
import { idle, reaskReducer, type ReaskAction, type ReaskState } from './reaskState'
import type { ReaskApi } from './useReask'
import { KnowledgeApiError } from './knowledgeRelationsApi'
import type { AddedRelation } from './relationAddApi'

afterEach(() => vi.unstubAllGlobals())

const run = (actions: ReaskAction[], from: ReaskState = idle): ReaskState => actions.reduce(reaskReducer, from)
const result: ReaskResult = { relation: 3, none: 2, unusable: 0, failed: 1, asked: 6, skipped: {}, stopped: null }
const tt = (k: string, o?: Record<string, unknown>) => (o ? `${k}${JSON.stringify(o)}` : k)
const api = (state: ReaskState): ReaskApi => ({ state, ask: vi.fn(), yes: vi.fn(), no: vi.fn(), cancel: vi.fn(), dismiss: vi.fn() })
const view = (state: ReaskState, n: number | null, name = 'u_1', blocked: { source: string; count: number; reason: string }[] = []) => renderToStaticMarkup(<ReaskView name={name} n={n} blocked={blocked} reask={api(state)} titleOf={(s) => `題-${s}`} />).replaceAll('&quot;', '"')

describe('状態の遷移', () => {
  it('ボタン → 確かめ → はい → 走る → 進み → 結果', () => {
    let s = run([{ type: 'ask', name: 'u_1', n: 4 }])
    expect(s).toEqual({ phase: 'confirm', name: 'u_1', n: 4 })
    s = run([{ type: 'starting' }, { type: 'started' }], s)
    expect(s.phase).toBe('running')
    s = run([{ type: 'progress', index: 1, total: 2, done: 3, pairs: 5 }], s)
    expect(s).toMatchObject({ phase: 'running', index: 1, total: 2, done: 3, pairs: 5 })
    s = run([{ type: 'progress', index: 'x' }], s) // 数でない値は前の値を残す
    expect(s).toMatchObject({ index: 1 })
    s = run([{ type: 'finished', result }], s)
    expect(s).toEqual({ phase: 'done', name: 'u_1', result })
    expect(run([{ type: 'dismiss' }], s)).toEqual(idle)
  })
  it('やめておくで戻る・走っている間は 2 本目の確かめを開かず、閉じもしない', () => {
    expect(run([{ type: 'ask', name: 'u_1', n: 1 }, { type: 'no' }])).toEqual(idle)
    const running = run([{ type: 'ask', name: 'u_1', n: 1 }, { type: 'starting' }, { type: 'started' }])
    expect(reaskReducer(running, { type: 'ask', name: 'u_2', n: 9 })).toBe(running)
    expect(reaskReducer(running, { type: 'dismiss' })).toBe(running)
  })
  it('失敗・取り消しは分類つきで残り、閉じられる', () => {
    const s = run([{ type: 'ask', name: 'u_1', n: 1 }, { type: 'starting' }, { type: 'failed', kind: 'aiUnset' }])
    expect(s).toEqual({ phase: 'failed', name: 'u_1', kind: 'aiUnset' })
    expect(run([{ type: 'dismiss' }], s)).toEqual(idle)
  })
})

describe('API の形', () => {
  it('数える: total と取り込み元ごとの数・形が違えば例外', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ name: 'u_1', keys: ['a'], sources: { s: 2 }, total: 2 }), { status: 200 })))
    expect(await fetchReaskCount('u_1')).toEqual({ total: 2, sources: { s: 2 }, blocked: [] })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ x: 1 }), { status: 200 })))
    await expect(fetchReaskCount('u_1')).rejects.toBeInstanceOf(KnowledgeApiError)
  })
  it('始める: POST・AI の座標を X-LLM-* で送る・job_id と飛ばした取り込み元を返す', async () => {
    const f = vi.fn(async (...args: [string, RequestInit?]) => (args.length ? new Response(JSON.stringify({ job_id: 'j1', sources: ['s'], skipped: { t: 'busy' } }), { status: 202 }) : new Response('{}')))
    vi.stubGlobal('fetch', f)
    const out = await startReask('u_1', 'ja', { apiKey: 'k', provider: 'openai', modelId: 'm' } as never)
    expect(out).toEqual({ jobId: 'j1', sources: ['s'], skipped: { t: 'busy' } })
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/relations/u_1/reask')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Record<string, string>)['X-LLM-Provider']).toBe('openai')
  })
  it('失敗の分類: AI 未設定・組なし・別の作業・その他', () => {
    expect(reaskFailureOf(new KnowledgeApiError(409, 'AI が設定されていません'))).toBe('aiUnset')
    expect(reaskFailureOf(new KnowledgeApiError(409, '判定し直す組がありません'))).toBe('nothing')
    expect(reaskFailureOf(new KnowledgeApiError(409, 'lab は別の作業'))).toBe('busy')
    expect(reaskFailureOf(new KnowledgeApiError(502, 'x'))).toBe('other')
    expect(reaskFailureOf(new Error('net'))).toBe('other')
  })
  it('結果を正す', () => {
    expect(normalizeReaskResult({ total: { asked: 6, relation: 3, none: 2, failed: 1 }, skipped: { s: 'busy' } })).toEqual({ ...result, skipped: { s: 'busy' } })
    expect(normalizeReaskResult({})).toBeNull()
  })
})

describe('ボタンと確かめ・進み・結果の出し分け', () => {
  it('N > 0 でボタン（N 組）・0 組と読めないときは何も出さない', () => {
    expect(view(idle, 4)).toContain('reask.button{"n":4}')
    expect(view(idle, 0)).toBe('')
    expect(view(idle, null)).toBe('')
  })
  it('確かめの 1 行と はい／やめておく', () => {
    const h = view({ phase: 'confirm', name: 'u_1', n: 4 }, 4)
    expect(h).toContain('reask.confirm{"n":4}')
    expect(h).toContain('data-testid="gp-reask-yes"')
    expect(h).toContain('data-testid="gp-reask-no"')
  })
  it('走っている間: 取り込み元の何番目と取り消し', () => {
    const h = view({ phase: 'running', name: 'u_1', index: 1, total: 2, done: 3, pairs: 5 }, 4)
    expect(h).toContain('reask.running{"index":1,"total":2}')
    expect(h).toContain('3/5')
    expect(h).toContain('data-testid="gp-reask-cancel"')
    expect(h).not.toContain('gp-reask-button')
  })
  it('結果の 1 行: 項目ごとに折り返さない・使えなかったは 0 でないときだけ・飛ばした理由と止めた理由', () => {
    const h = view({ phase: 'done', name: 'u_1', result: { ...result, skipped: { s: 'busy' } } }, 0)
    expect(h).toContain('graphPlace:reask.item.relation{"n":3}')
    expect(h).toContain('class="gp-nw"')
    expect(h).toContain('題-s')
    expect(h).not.toContain('reask.item.unusable')
    const items = resultItems(tt, { ...result, unusable: 2 })
    expect(items).toEqual(['graphPlace:reask.item.relation{"n":3}', 'graphPlace:reask.item.none{"n":2}', 'graphPlace:reask.item.unusable{"n":2}', 'graphPlace:reask.item.failed{"n":1}'])
    expect(resultTail(tt, { ...result, stopped: 'retired' }, (s) => s)).toContain('reask.stoppedRetired')
    expect(resultTail(tt, result, (s) => s)).toBe('')
  })
  it('いま判定し直せない取り込み元は別の 1 行（ボタンは走れる分だけ）', () => {
    const b = [{ source: 's', count: 3, reason: 'busy' }]
    const h = view(idle, 2, 'u_1', b)
    expect(h).toContain('gp-reask-blocked')
    expect(h).toContain('reask.button{"n":2}')
    expect(h).toContain('題-s')
    const only = view(idle, 0, 'u_1', b)
    expect(only).toContain('gp-reask-blocked')
    expect(only).not.toContain('gp-reask-button')
    expect(blockedText(tt, b, (s) => s)).toContain('reask.blocked')
  })
  it('失敗の 1 行を出し、ボタンは戻る・別の関係の状態のときは自分のボタンだけ（走っている間は押せない）', () => {
    const f = view({ phase: 'failed', name: 'u_1', kind: 'other' }, 4)
    expect(f).toContain('reask.fail.other')
    expect(f).toContain('gp-reask-button')
    const other = view({ phase: 'running', name: 'u_2', index: 1, total: 1, done: null, pairs: null }, 4)
    expect(other).toContain('gp-reask-button')
    expect(other).toContain('disabled')
  })
})

const added = (over: Partial<AddedRelation> = {}): AddedRelation => ({
  name: 'u_1', label: { ja: '併用', en: 'Used together' }, definition: { ja: '説明', en: '' }, symmetric: true, toPropertyOnly: false,
  exactMatch: null, retired: false, addedAt: '2026-11-21T00:00:00+00:00', used: 0, ...over,
})
const rowHtml = (r: AddedRelation, reask?: boolean) =>
  renderToStaticMarkup(<AddedRow r={r} lang="ja" open onToggle={() => {}} onChanged={() => {}} reask={reask ? <i data-x="reask" /> : undefined} />)

describe('足した関係の行', () => {
  it('開いた中に出る・やめた関係には出ない', () => {
    expect(rowHtml(added(), true)).toContain('data-x="reask"')
    expect(rowHtml(added({ retired: true }), true)).not.toContain('data-x="reask"')
  })
  it('節は renderReask があるときだけ行に渡す', () => {
    const base = { load: { status: 'ok' as const, relations: [added()], unavailable: false }, lang: 'ja' as const, openName: 'u_1', onToggle: () => {}, onChanged: () => {}, adder: <i />, adding: false, onAdd: () => {} }
    expect(renderToStaticMarkup(<AddedSection {...base} renderReask={() => <i data-x="reask" />} />)).toContain('data-x="reask"')
    expect(renderToStaticMarkup(<AddedSection {...base} />)).not.toContain('data-x="reask"')
  })
})

describe('足した直後の知らせ', () => {
  const notice = (state: ReaskState) => renderToStaticMarkup(<AddedDoneNotice name="u_1" label="併用" trigger={{}} reask={api(state)} titleOf={(s) => s} />).replaceAll('&quot;', '"')
  it('始める前は元の文・始めたら「今ある組は聞き直しません」を言わない', () => {
    expect(notice(idle)).toContain('added.done{"name":"併用"}')
    const running: ReaskState = { phase: 'running', name: 'u_1', index: 1, total: 2, done: null, pairs: null }
    expect(notice(running)).toContain('added.doneReasked{"name":"併用"}')
    expect(notice({ phase: 'done', name: 'u_1', result })).toContain('added.doneReasked')
  })
})
