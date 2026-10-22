import { describe, expect, it } from 'vitest'
import type { Session, Turn, TurnState } from './chatApi'
import {
  applyConfirm,
  applyRevert,
  applyTurnResponse,
  confirmTargets,
  awaitingChoice,
  showMachineMark,
  handFormShown,
  keptName,
  nameTable,
  runningJobs,
  bindingDecisions,
  preferReloaded,
  settleAsk,
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
    suggestions: [],
    notes: notes.map((kind) => ({ kind, text: 'n', choice: null })),
    answer: null,
    table: null,
  },
  batch: `cabc12345x:${id}`,
  state,
  before: state === 'proposed' ? {} : null,
  commit: null,
  by: 'human',
  attachment: null,
  job_id: null,
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
    expect([...confirmTargets([turn('t1', 'proposed'), turn('t2', 'proposed'), turn('t3', 'none')])]).toEqual(['t2'])
    expect(confirmTargets([turn('t1', 'none'), turn('t2', 'confirmed'), turn('t3', 'reverted')]).size).toBe(0)
    expect(confirmTargets([]).size).toBe(0)
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
    expect(confirmTargets(s.turns).size).toBe(0)
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
    expect([...confirmTargets(base.turns)]).toEqual(['t2'])
    const s = applyRevert(base, { turn: turn('t2', 'reverted'), session: brief(), draft: null, reverted: ['t2'] })
    expect([...confirmTargets(s.turns)]).toEqual(['t1'])
  })
  it('✓ の返りの removed／kept を手番に保つ', () => {
    const base = session([turn('t1', 'proposed', 1), turn('t2', 'proposed', 1)])
    const commit = {
      theme_id: 'th1',
      minted: [],
      lines: 1,
      questions: [],
      batch: 'b',
      at: 'x',
      removed: { lines: 2, terms: 1 },
      kept: [{ slug: 'seebeck', reason: '使われている' }],
    }
    const s = applyConfirm(base, { turn: { ...turn('t2', 'confirmed', 1), commit }, session: brief(), commit, confirmed: ['t1', 't2'] })
    expect(s.turns[0].commit?.removed).toEqual({ lines: 2, terms: 1 })
    expect(s.turns[1].commit?.kept).toEqual([{ slug: 'seebeck', reason: '使われている' }])
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
    // 最後の AI の返事が来ていれば、その前の ai_unset は効かない
    expect(handFormShown(session([{ ...turn('t1', 'proposed', 1), by: 'ai' }]))).toBe(false)
    expect(handFormShown(session([turn('t1', 'proposed', 0, ['ai_unset']), { ...turn('t2', 'none'), by: 'ai' }]))).toBe(false)
  })
})

describe('話の読み直し・チップ・名前', () => {
  it('読み直した話が返りより優先される（戻された手番が残らない）。読み直せなければ返りのまま', () => {
    const base = session([turn('t1', 'proposed'), turn('t2', 'proposed')])
    const applied = applyTurnResponse(base, { turn: turn('t3', 'proposed'), session: brief(), draft: null })
    const fresh = session([turn('t1', 'reverted'), turn('t2', 'reverted'), turn('t3', 'proposed')])
    expect(preferReloaded(applied, fresh).turns.map((t) => t.state)).toEqual(['reverted', 'reverted', 'proposed'])
    expect(preferReloaded(applied, null)).toBe(applied)
  })
  it('チップ: 失敗・例外は false（印を戻す）、成功と void は true', async () => {
    expect(await settleAsk(async () => false)).toBe(false)
    expect(await settleAsk(async () => { throw new Error('x') })).toBe(false)
    expect(await settleAsk(async () => true)).toBe(true)
    expect(await settleAsk(() => undefined)).toBe(true)
  })
  it('残した名前: 図の語と下書きの札から引き、無ければ slug のまま', () => {
    const names = nameTable([{ slug: 'thermal', label: '熱伝導率' }], { concepts: [{ key: 'h1', label: '密度', use: 'existing:dens' }] })
    expect(keptName('thermal', names)).toBe('熱伝導率')
    expect(keptName('dens', names)).toBe('密度')
    expect(keptName('zzz', names)).toBe('zzz')
    expect(keptName('zzz', undefined)).toBe('zzz')
  })
})

describe('📎 表を添える', () => {
  it('running の手番は ✓ の対象にならず、ジョブの id があれば購読し直す', () => {
    const running: Turn = { ...turn('t1', 'running'), job_id: 'j1' }
    const noJob = turn('t2', 'running')
    expect(confirmTargets([running, noJob]).size).toBe(0)
    expect(proposedCount([running])).toBe(0)
    expect(runningJobs([running, noJob, turn('t3', 'proposed')])).toEqual([{ turnId: 't1', jobId: 'j1' }])
  })
  it('bindings の decided: 既定に人の選びを重ねる', () => {
    const b = (column: string, decided: 'bind' | 'skip') => ({ column, label: column, term: column, term_label: column, relation: 'eq', subject: '', decided })
    const bs = [b('価格', 'bind'), b('産地', 'bind'), b('備考', 'skip')]
    expect(bindingDecisions(bs, {})).toEqual([
      { column: '価格', subject: '', decided: 'bind' },
      { column: '産地', subject: '', decided: 'bind' },
      { column: '備考', subject: '', decided: 'skip' },
    ])
    expect(bindingDecisions(bs, { 1: 'skip', 2: 'bind' }).map((d) => d.decided)).toEqual(['bind', 'skip', 'bind'])
  })
  it('✓ の返りの followup（機械の手番）は話の末尾に足される', () => {
    const t1 = turn('t1', 'proposed')
    const follow: Turn = { ...turn('t9', 'proposed'), by: 'machine', user: null }
    const s = applyConfirm(session([t1]), { turn: { ...t1, state: 'confirmed' }, session: brief(), followup: follow })
    expect(s.turns.map((t) => t.id)).toEqual(['t1', 't9'])
  })
})

describe('✓ の区分（話・表・機械）と選び待ち', () => {
  const table = (id: string): Turn => ({
    ...turn(id, 'proposed'),
    attachment: { dataset_id: 'd', name: 'a.csv', rows: 3, kinds: [], id_column: 'id' },
  })
  const machine = (id: string): Turn => {
    const t = turn(id, 'proposed')
    return { ...t, by: 'machine', user: null, reply: { ...t.reply, suggestions: [{ subject: 'a', term: 'b', relation: 'eq' }] } }
  }
  const waiting = (id: string): Turn => {
    const t = turn(id, 'proposed')
    return {
      ...t,
      reply: { ...t.reply, notes: [{ kind: 'choice_id', text: 'n', choice: { label: 'l', options: [{ value: 'a', label: 'A' }], default: 'a' } }] },
    }
  }
  it('話の提案は最後の 1 つ・表と機械の手番はそれぞれに', () => {
    const ts = [turn('t1', 'proposed', 1), table('t2'), turn('t3', 'proposed', 2), table('t4'), machine('t5')]
    expect([...confirmTargets(ts)].sort()).toEqual(['t2', 't3', 't4', 't5'])
  })
  it('ops の数は話の提案だけ数える', () => {
    const t2 = { ...table('t2'), reply: { ...table('t2').reply, ops: [{ op: 'x' }, { op: 'y' }] } }
    expect(proposedCount([turn('t1', 'proposed', 2), t2])).toBe(2)
  })
  it('選び待ちには ✓ を出さない・決めたあとは出す', () => {
    expect(awaitingChoice(waiting('t1'))).toBe(true)
    expect(confirmTargets([waiting('t1')]).size).toBe(0)
    const t = waiting('t1')
    t.reply.notes[0].choice = { ...t.reply.notes[0].choice!, chosen: 'a' }
    expect(awaitingChoice(t)).toBe(false)
    expect(confirmTargets([t]).has('t1')).toBe(true)
  })
  it('機械の印は名前の一致の手番だけ', () => {
    expect(showMachineMark(machine('t1'))).toBe(true)
    expect(showMachineMark({ ...table('t2'), by: 'machine' })).toBe(false)
    expect(showMachineMark({ ...waiting('t3'), by: 'machine' })).toBe(false)
    expect(showMachineMark(turn('t4', 'proposed'))).toBe(false)
  })
})
