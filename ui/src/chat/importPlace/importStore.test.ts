// 取り込みの置き場: 純関数 storeReducer＋薄い層（偽の api を渡して検査する）。
import { describe, expect, it, vi } from 'vitest'
import { createImportStore, initialStore, isBusy, storeReducer, type StoreDeps } from './importStore'
import { importJobReducer, idle } from './importJob'
import type { ImportSource } from './knowledgeImportApi'
import { result } from './testData'

const row = (over: Partial<ImportSource> = {}): ImportSource => ({
  source: 'graphium',
  title: null,
  dataset_id: 'd',
  copied_at: null,
  counts: { claims: 1, topics: 1, sources: 0, terms: 0 },
  relations: { shown: 0, terms: 0, candidates: 0, unconfirmed: 0, confirmed: 0 },
  running: null,
  ...over,
})

type Handlers = Parameters<StoreDeps['subscribeJob']>[1]
function fake() {
  const handlers: Handlers[] = []
  const closes: number[] = []
  const deps = {
    listImports: vi.fn(async () => [row()]),
    removeImport: vi.fn(async () => {}),
    cancelJob: vi.fn(async () => {}),
    subscribeJob: vi.fn((_id: string, h: Handlers) => {
      handlers.push(h)
      const n = handlers.length
      return { jobId: _id, close: () => closes.push(n), cancel: async () => {} }
    }),
  } as unknown as StoreDeps
  return { deps, handlers, closes }
}
const ok = (id = 'j1') => async () => ({ ok: true as const, jobId: id })
const tick = () => new Promise((r) => setTimeout(r, 0))

describe('reducer（準備中・二重・閉じる）', () => {
  it('prepare → start、走っている・準備中の prepare/start は状態を変えない', () => {
    let s = importJobReducer(idle, { type: 'prepare', source: 'a' })
    expect(s).toEqual({ phase: 'preparing', source: 'a' })
    expect(importJobReducer(s, { type: 'prepare', source: 'b' })).toBe(s)
    s = importJobReducer(s, { type: 'start', source: 'a' })
    expect(importJobReducer(s, { type: 'prepare', source: 'b' })).toBe(s)
    expect(importJobReducer(s, { type: 'start', source: 'b' })).toBe(s)
  })
  it('dismiss は走行中・準備中を idle に戻さない・abort は準備中だけ idle', () => {
    const prep = importJobReducer(idle, { type: 'prepare', source: 'a' })
    expect(importJobReducer(prep, { type: 'dismiss' })).toBe(prep)
    const run = importJobReducer(prep, { type: 'start', source: 'a' })
    expect(importJobReducer(run, { type: 'dismiss' })).toBe(run)
    expect(importJobReducer(run, { type: 'abort' })).toBe(run)
    expect(importJobReducer(prep, { type: 'abort' })).toBe(idle)
  })
  it('storeReducer: 一覧が読めたら一覧の失敗だけ消える', () => {
    let s = storeReducer(initialStore, { type: 'notice', notice: { kind: 'remove', detail: 'x' } })
    s = storeReducer(s, { type: 'sources', sources: [] })
    expect(s.notice?.kind).toBe('remove')
    s = storeReducer(s, { type: 'notice', notice: { kind: 'list', detail: 'y' } })
    expect(storeReducer(s, { type: 'sources', sources: [] }).notice).toBeNull()
  })
})

describe('createImportStore', () => {
  it('連打しても 1 本・準備中も busy・2 本目の進みや結果を消さない', async () => {
    const { deps, handlers } = fake()
    const st = createImportStore(deps)
    const begin = vi.fn(ok())
    const p1 = st.start('graphium', begin)
    expect(st.getSnapshot().job.phase).toBe('preparing')
    expect(isBusy(st.getSnapshot())).toBe(true)
    await st.start('graphium', begin) // 準備中の 2 本目
    await p1
    await st.start('graphium', begin) // 走行中の 2 本目
    expect(begin).toHaveBeenCalledTimes(1)
    expect(handlers).toHaveLength(1)
    expect(st.getSnapshot().job.phase).toBe('running')
    handlers[0].onRunning?.({ stage: 'pairs', done: 3, total: 10 })
    expect(st.getSnapshot().job).toMatchObject({ stage: 'pairs', done: 3, total: 10 })
  })
  it('購読が外れても（画面を離れても）続き、終わりに changed が 1 回・結果は残る', async () => {
    const { deps, handlers } = fake()
    const st = createImportStore(deps)
    const changed = vi.fn()
    st.setChanged(changed)
    const unsub = st.subscribe(() => {})
    await st.start('graphium', ok())
    unsub() // ImportPlace のアンマウント
    handlers[0].onRunning?.({ stage: 'copy' })
    handlers[0].onDone({ ...result(), source: 'graphium' })
    await tick()
    expect(changed).toHaveBeenCalledTimes(1)
    expect(st.getSnapshot().job.phase).toBe('done')
    expect(st.getSnapshot().jobId).toBeNull()
    st.dismiss()
    expect(st.getSnapshot().job).toBe(idle)
  })
  it('準備の失敗は startError で idle に戻り、走っている 1 本目は消えない', async () => {
    const { deps } = fake()
    const st = createImportStore(deps)
    await st.start('graphium', async () => ({ ok: false, failure: { code: 'old', tool: 'x' } }))
    expect(st.getSnapshot().job).toBe(idle)
    expect(st.getSnapshot().startError).toEqual({ code: 'old', tool: 'x' })
    await st.start('graphium', async () => {
      throw new Error('409 別の作業が走っています')
    })
    expect(st.getSnapshot().startError).toEqual({ code: 'http', detail: '409 別の作業が走っています' })
    await st.start('graphium', ok())
    expect(st.getSnapshot().startError).toBeNull()
    expect(st.getSnapshot().job.phase).toBe('running')
  })
  it('SSE の失敗・壊れた結果は failed（英語の理由はそのまま持つ）', async () => {
    const { deps, handlers } = fake()
    const st = createImportStore(deps)
    await st.start('graphium', ok())
    handlers[0].onDone({ nope: 1 })
    expect(st.getSnapshot().job).toMatchObject({ phase: 'failed', error: 'bad result' })
  })
  it('外す・取り消しの失敗は notice（新しい失敗が置き換える）・外す後に changed', async () => {
    const { deps, handlers } = fake()
    deps.removeImport = vi.fn(async () => {
      throw new Error('409 走っています')
    })
    deps.cancelJob = vi.fn(async () => {
      throw new Error('404')
    })
    const st = createImportStore(deps)
    const changed = vi.fn()
    st.setChanged(changed)
    await st.remove('graphium')
    expect(st.getSnapshot().notice).toEqual({ kind: 'remove', detail: '409 走っています' })
    expect(changed).toHaveBeenCalledTimes(1)
    await st.start('graphium', ok())
    expect(st.getSnapshot().notice).toBeNull()
    expect(handlers).toHaveLength(1)
    st.cancel()
    await tick()
    expect(st.getSnapshot().notice).toEqual({ kind: 'cancel', detail: '404' })
  })
  it('読み直しは中身が変わったときだけ changed・古い応答は捨てる', async () => {
    const { deps } = fake()
    const st = createImportStore(deps)
    const changed = vi.fn()
    st.setChanged(changed)
    await st.reload()
    await st.poll() // 同じ中身
    expect(changed).not.toHaveBeenCalled()
    deps.listImports = vi.fn(async () => [row({ running: 'import' })])
    await st.poll()
    expect(changed).toHaveBeenCalledTimes(1)
    // 遅い古い応答と速い新しい応答
    let slow!: (r: ImportSource[]) => void
    const calls = [new Promise<ImportSource[]>((r) => (slow = r)), Promise.resolve([row({ title: '新' })])]
    deps.listImports = vi.fn(() => calls.shift()!)
    const a = st.reload()
    await st.reload()
    slow([row({ title: '古' })])
    await a
    expect(st.getSnapshot().sources?.[0].title).toBe('新')
  })
  it('一覧を読めない失敗は notice list', async () => {
    const { deps } = fake()
    deps.listImports = vi.fn(async () => {
      throw new Error('HTTP 500')
    })
    const st = createImportStore(deps)
    await st.reload()
    expect(st.getSnapshot().notice).toEqual({ kind: 'list', detail: 'HTTP 500' })
  })
})
