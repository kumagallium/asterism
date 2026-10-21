import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDraftSaver } from './draftSaver'
import { flushDrafts, registerDraftFlush } from './draftFlush'

interface D {
  id: string
  v: number
}

function setup(draft: D | null = { id: 'a', v: 0 }) {
  const state = { draft, saved: [] as number[], fail: 0, errors: 0 }
  const saver = createDraftSaver<D>({
    current: () => state.draft,
    save: async (d) => {
      if (state.fail > 0) {
        state.fail--
        throw new Error('x')
      }
      state.saved.push(d.v)
    },
    delay: 500,
    onError: () => state.errors++,
  })
  return { state, saver }
}

describe('下書きの直しをその場で保存する（debounce 500ms）', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('直してから 500ms 後に 1 回だけ保存する（連続して直しても 1 回・最後の中身）', async () => {
    const { state, saver } = setup()
    state.draft = { id: 'a', v: 1 }
    saver.mark()
    await vi.advanceTimersByTimeAsync(300)
    state.draft = { id: 'a', v: 2 }
    saver.mark()
    await vi.advanceTimersByTimeAsync(499)
    expect(state.saved).toEqual([])
    await vi.advanceTimersByTimeAsync(2)
    expect(state.saved).toEqual([2])
    expect(saver.isDirty()).toBe(false)
  })

  it('flush() は待たずに保存する（離れる前）。直していなければ何もしない', async () => {
    const { state, saver } = setup()
    await saver.flush()
    expect(state.saved).toEqual([])
    state.draft = { id: 'a', v: 3 }
    saver.mark()
    await saver.flush()
    expect(state.saved).toEqual([3])
    await vi.advanceTimersByTimeAsync(1000)
    expect(state.saved).toEqual([3]) // 待っていた保存は重ならない
  })

  it('保存中に直したら、その終わりを待ってからもう一度（順番が入れ替わらない）', async () => {
    const order: string[] = []
    let release: () => void = () => {}
    const cur = { id: 'a', v: 1 }
    const saver = createDraftSaver<D>({
      current: () => ({ ...cur }),
      save: async (d) => {
        order.push(`start${d.v}`)
        if (d.v === 1) await new Promise<void>((r) => (release = r))
        order.push(`end${d.v}`)
      },
      delay: 500,
    })
    saver.mark()
    const first = saver.flush()
    await vi.advanceTimersByTimeAsync(0)
    cur.v = 2
    saver.mark()
    const second = saver.flush()
    await vi.advanceTimersByTimeAsync(0)
    expect(order).toEqual(['start1']) // 2 回目は 1 回目の終わりを待つ
    release()
    await Promise.all([first, second])
    expect(order).toEqual(['start1', 'end1', 'start2', 'end2'])
  })

  it('保存に失敗した直しは残り、次の flush でもう一度送る', async () => {
    const { state, saver } = setup()
    state.draft = { id: 'a', v: 5 }
    state.fail = 1
    saver.mark()
    await saver.flush()
    expect(state.errors).toBe(1)
    expect(saver.isDirty()).toBe(true)
    await saver.flush()
    expect(state.saved).toEqual([5])
  })

  it('id がまだ無い下書き・下書きが無いときは保存しない（最初の保存が全部運ぶ）', async () => {
    const { state, saver } = setup({ id: '', v: 1 })
    saver.mark()
    await saver.flush()
    expect(state.saved).toEqual([])
    state.draft = null
    await saver.flush()
    expect(state.saved).toEqual([])
  })

  it('clear() は待っている保存を取り消す（捨てるとき・置いたあと）', async () => {
    const { state, saver } = setup()
    state.draft = { id: 'a', v: 9 }
    saver.mark()
    saver.clear()
    await vi.advanceTimersByTimeAsync(1000)
    await saver.flush()
    expect(state.saved).toEqual([])
  })
})

describe('離れる前の合図（hash が変わる前に保存を始める）', () => {
  it('登録した flush を flushDrafts() がすべて呼ぶ。外したものは呼ばない', () => {
    const a = vi.fn()
    const b = vi.fn()
    const offA = registerDraftFlush(a)
    const offB = registerDraftFlush(b)
    flushDrafts()
    offB()
    flushDrafts()
    offA()
    flushDrafts()
    expect(a).toHaveBeenCalledTimes(2)
    expect(b).toHaveBeenCalledTimes(1)
  })
})
