// 判定し直しの手続き（R4）: 「はい」「取り消し」・二重押し・AI 未設定・ジョブが見つからない・時間切れ。
import { describe, expect, it, vi } from 'vitest'
import { KnowledgeApiError } from './knowledgeRelationsApi'
import { createReaskController, type ReaskDeps } from './reaskController'
import type { ReaskAction } from './reaskState'

type Handlers = Parameters<ReaskDeps['subscribe']>[1]

function setup(over: { creds?: object | null; start?: ReaskDeps['start'] } = {}) {
  const actions: ReaskAction[] = []
  const handlers: { h: Handlers | null } = { h: null }
  const handle = { jobId: 'j1', close: vi.fn(), cancel: vi.fn(async () => undefined) }
  const timers: { fn: () => void; ms: number; id: number; live: boolean }[] = []
  const start = over.start ?? vi.fn(async () => ({ jobId: 'j1', sources: ['s'], skipped: { t: 'busy' } }))
  const deps: ReaskDeps = {
    start,
    subscribe: (_id, h) => { handlers.h = h; return handle },
    setTimer: (fn, ms) => { const t = { fn, ms, id: timers.length, live: true }; timers.push(t); return t.id },
    clearTimer: (id) => { const t = timers[id as number]; if (t) t.live = false },
  }
  const settled = vi.fn()
  const ctl = createReaskController({
    lang: 'ja',
    getCreds: () => (over.creds === undefined ? ({ provider: 'p' } as never) : (over.creds as never)),
    dispatch: (a) => actions.push(a),
    onSettled: settled,
    deps,
    stallMs: 1000,
  })
  return { ctl, actions, handlers, handle, timers, start, settled }
}
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('判定し直しの手続き', () => {
  it('はい: 始める → 進み → 結果（飛ばした取り込み元を足す）→ 読み直し', async () => {
    const t = setup()
    t.ctl.yes('u_1')
    await flush()
    expect(t.start).toHaveBeenCalledTimes(1)
    expect(t.actions.map((a) => a.type)).toEqual(['starting', 'started'])
    t.handlers.h?.onRunning({ source_index: 1, source_total: 2, done: 1, total: 3 })
    t.handlers.h?.onDone({ total: { asked: 3, relation: 2, none: 1, unusable: 0, failed: 0 }, skipped: { u: 'error' } })
    const fin = t.actions.at(-1)
    expect(fin).toMatchObject({ type: 'finished', result: { relation: 2, skipped: { t: 'busy', u: 'error' } } })
    expect(t.handle.close).toHaveBeenCalled()
    expect(t.settled).toHaveBeenCalledTimes(1)
  })
  it('二重に押しても始めるのは 1 回（終わるまで受けない・終われば受ける）', async () => {
    const t = setup()
    t.ctl.yes('u_1')
    t.ctl.yes('u_1')
    await flush()
    t.ctl.yes('u_1')
    expect(t.start).toHaveBeenCalledTimes(1)
    t.handlers.h?.onDone({ total: {}, skipped: {} })
    t.ctl.yes('u_1')
    expect(t.start).toHaveBeenCalledTimes(2)
  })
  it('取り消し: ジョブの取り消しを頼み、止まったかはストリームの cancelled で分かる', async () => {
    const t = setup()
    t.ctl.yes('u_1')
    await flush()
    t.ctl.cancel()
    expect(t.handle.cancel).toHaveBeenCalledTimes(1)
    t.handlers.h?.onCancelled()
    expect(t.actions.at(-1)).toEqual({ type: 'failed', kind: 'cancelled' })
    expect(t.settled).toHaveBeenCalledTimes(1)
  })
  it('AI 未設定: 始めずに失敗の 1 行・また押せる', async () => {
    const t = setup({ creds: null })
    t.ctl.yes('u_1')
    expect(t.start).not.toHaveBeenCalled()
    expect(t.actions.at(-1)).toEqual({ type: 'failed', kind: 'aiUnset' })
    t.ctl.yes('u_1')
    expect(t.actions.filter((a) => a.type === 'starting')).toHaveLength(2)
  })
  it('始める口の失敗は分類して出し、押せる状態に戻る', async () => {
    const t = setup({ start: vi.fn(async () => { throw new KnowledgeApiError(409, '判定し直す組がありません') }) })
    t.ctl.yes('u_1')
    await flush()
    expect(t.actions.at(-1)).toEqual({ type: 'failed', kind: 'nothing' })
    expect(t.settled).toHaveBeenCalledTimes(1)
    t.ctl.yes('u_1')
    expect(t.start).toHaveBeenCalledTimes(2)
  })
  it('ジョブが見つからない・接続が切れた: 失敗の 1 行にして戻る', async () => {
    const t = setup()
    t.ctl.yes('u_1')
    await flush()
    t.handlers.h?.onError('connection lost')
    expect(t.actions.at(-1)).toEqual({ type: 'failed', kind: 'other' })
    expect(t.handle.close).toHaveBeenCalled()
    t.ctl.yes('u_1')
    expect(t.start).toHaveBeenCalledTimes(2)
  })
  it('時間切れ: 何も届かないまま上限を過ぎたら失敗にする・心拍で延びる', async () => {
    const t = setup()
    t.ctl.yes('u_1')
    await flush()
    const first = t.timers.at(-1)
    t.handlers.h?.onPulse() // 心拍: 時計を張り直す
    expect(first?.live).toBe(false)
    t.timers.at(-1)?.fn()
    expect(t.actions.at(-1)).toEqual({ type: 'failed', kind: 'other' })
    expect(t.settled).toHaveBeenCalledTimes(1)
    t.ctl.yes('u_1')
    expect(t.start).toHaveBeenCalledTimes(2)
  })
  it('画面を離れたあとに届いた答えは何もしない', async () => {
    const t = setup()
    t.ctl.yes('u_1')
    t.ctl.dispose()
    await flush()
    expect(t.actions.map((a) => a.type)).toEqual(['starting'])
  })
})
