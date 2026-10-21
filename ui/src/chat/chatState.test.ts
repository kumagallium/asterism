import { describe, expect, it } from 'vitest'
import type { Session, Turn, TurnState } from './chatApi'
import {
  applyConfirm,
  applyRevert,
  applyTurnResponse,
  confirmTarget,
  handFormShown,
  nextHandKey,
  proposedCount,
  sessionTitle,
} from './chatState'

const turn = (id: string, state: TurnState, ops = 0, notes: string[] = []): Turn => ({
  id,
  at: '2026-10-22T00:00:00Z',
  user: { text: 'q', attachments: [] },
  reply: {
    text: 'r',
    ops: Array.from({ length: ops }, () => ({ op: 'add_concept' })),
    sentences: [],
    questions: [],
    bindings: [],
    notes: notes.map((kind) => ({ kind, text: 'n', choice: null })),
    answer: null,
    table: null,
  },
  batch: `cabc12345x:${id}`,
  state,
  before: state === 'proposed' ? {} : null,
  commit: null,
  by: 'human',
})
const session = (turns: Turn[], over: Partial<Session> = {}): Session => ({
  id: 'cabc12345x',
  title: '',
  theme_id: null,
  draft_id: 'd1',
  updated_at: '1',
  created_at: '0',
  turns,
  ...over,
})
const brief = (over = {}) => ({ id: 'cabc12345x', title: '熱電材料', theme_id: null, draft_id: 'd1', updated_at: '2', ...over })

describe('chatState', () => {
  it('✓ の対象は最後の未確認の返事だけ。none や確認済みには出ない', () => {
    expect(confirmTarget([turn('t1', 'proposed'), turn('t2', 'proposed'), turn('t3', 'none')])).toBe('t2')
    expect(confirmTarget([turn('t1', 'none'), turn('t2', 'confirmed'), turn('t3', 'reverted')])).toBeNull()
    expect(confirmTarget([])).toBeNull()
  })
  it('件数は未確認の手番の差分の合計', () => {
    expect(proposedCount([turn('t1', 'proposed', 2), turn('t2', 'confirmed', 5), turn('t3', 'proposed', 1)])).toBe(3)
  })
  it('題が空なら「新しい話」', () => {
    expect(sessionTitle({ title: ' ' }, '新しい話')).toBe('新しい話')
    expect(sessionTitle({ title: '熱電材料' }, '新しい話')).toBe('熱電材料')
  })
  it('手番の返りは足す（同じ id は置き換え）・題と下書きの欄が変わる', () => {
    const s = applyTurnResponse(session([]), { turn: turn('t1', 'proposed'), session: brief(), draft: null })
    expect(s.turns).toHaveLength(1)
    expect(s.title).toBe('熱電材料')
    const again = applyTurnResponse(s, { turn: turn('t1', 'none'), session: brief(), draft: null })
    expect(again.turns).toHaveLength(1)
    expect(again.turns[0].state).toBe('none')
  })
  it('✓ の返りは置けた手番を全部確認済みにして、話にテーマが付き下書きが外れる', () => {
    const base = session([turn('t1', 'proposed', 1), turn('t2', 'proposed', 1)])
    const commit = { theme_id: 'th1', minted: [], lines: 1, questions: [], batch: 'b', at: 'x' }
    const s = applyConfirm(base, {
      turn: { ...turn('t2', 'confirmed', 1), commit },
      session: brief({ theme_id: 'th1', draft_id: null }),
      confirmed: ['t1', 't2'],
    })
    expect(s.turns.map((t) => t.state)).toEqual(['confirmed', 'confirmed'])
    expect(s.turns[0].commit).toEqual(commit)
    expect(s.theme_id).toBe('th1')
    expect(s.draft_id).toBeNull()
    expect(confirmTarget(s.turns)).toBeNull()
  })
  it('✓ が置けなかった返り（commit_error）は手番を置き換えるだけ', () => {
    const base = session([turn('t1', 'proposed', 1)])
    const s = applyConfirm(base, { turn: turn('t1', 'proposed', 1, ['commit_error']), session: brief() })
    expect(s.turns[0].state).toBe('proposed')
    expect(s.turns[0].reply.notes[0].kind).toBe('commit_error')
  })
  it('戻すの返りは、あとの未確認も取り消し済みに', () => {
    const base = session([turn('t1', 'proposed'), turn('t2', 'proposed')])
    const s = applyRevert(base, { turn: turn('t1', 'reverted'), session: brief({ draft_id: null }), draft: null, reverted: ['t1', 't2'] })
    expect(s.turns.map((t) => t.state)).toEqual(['reverted', 'reverted'])
    expect(s.draft_id).toBeNull()
  })
  it('戻したあと ✓ の対象は前の未確認へ移る', () => {
    const base = session([turn('t1', 'proposed'), turn('t2', 'proposed')])
    expect(confirmTarget(base.turns)).toBe('t2')
    const s = applyRevert(base, { turn: turn('t2', 'reverted'), session: brief(), draft: null, reverted: ['t2'] })
    expect(confirmTarget(s.turns)).toBe('t1')
  })
  it('✓ の返りに commit が無いときは手番の commit を保つ', () => {
    const commit = { theme_id: 'th1', minted: [], lines: 1, questions: [], batch: 'b', at: 'x' }
    const base = session([turn('t1', 'proposed', 1), turn('t2', 'proposed', 1)])
    const s = applyConfirm(base, {
      turn: { ...turn('t2', 'confirmed', 1), commit },
      session: brief(),
      confirmed: ['t1', 't2'],
    })
    expect(s.turns[0].commit).toEqual(commit)
    expect(s.turns[1].commit).toEqual(commit)
  })
  it('手で足す key は h<n> の次', () => {
    expect(nextHandKey([])).toBe('h1')
    expect(nextHandKey(['t1', 'h1', 'h4', 'hx'])).toBe('h5')
  })
  it('手で足す欄は下書きがあり、最後の返事が AI 未設定のときだけ', () => {
    expect(handFormShown(session([turn('t1', 'proposed', 0, ['ai_unset'])]))).toBe(true)
    expect(handFormShown(session([turn('t1', 'proposed', 0, ['ai_unset'])], { draft_id: null }))).toBe(false)
    // 手で足した手番（注記なし）が続いても出たまま。AI の返事が来たら消える
    expect(handFormShown(session([turn('t1', 'proposed', 0, ['ai_unset']), turn('t2', 'proposed', 1)]))).toBe(true)
    expect(handFormShown(session([turn('t1', 'proposed', 0, ['ai_unset']), { ...turn('t2', 'proposed', 1), by: 'ai' }]))).toBe(false)
    expect(handFormShown(session([]))).toBe(false)
  })
})
