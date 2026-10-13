// PR F18 契約メモ §1.1: `pageChatThreads.ts` の一覧（`pageChatThreadsFor`）・
// カードへの結びつけ（`pageChatThreadForCard`／`createPageChatThreadForCard`／
// `bindPageChatThreadToCard`）・旧索引（meta の無いスレッド）の取り込みを検証。
//
// このファイルのモジュール内 `store`（`createThreadStore`）は一度だけ作られる
// シングルトンなので、通常のテストは `uniq()` で使い捨ての subjectKey/cardId を
// 使い、テストどうしを混ぜない。旧索引の取り込みだけは「起動時に localStorage
// から読む」という一回きりの経路を検証する必要があるため、`vi.resetModules()`
// ＋動的 import でモジュールを作り直す。

import { afterEach, describe, expect, it, vi } from 'vitest'

function uniq(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2)}`
}

function makeFakeLocalStorage(initial: Record<string, string> = {}): Storage {
  const backing = { ...initial }
  return {
    getItem: (k) => (k in backing ? backing[k] : null),
    setItem: (k, v) => {
      backing[k] = v
    },
    removeItem: (k) => {
      delete backing[k]
    },
    clear: () => {
      for (const k of Object.keys(backing)) delete backing[k]
    },
    key: () => null,
    length: 0,
  }
}

describe('pageChatThreadsFor / pageChatThreadForCard', () => {
  it('subjectKeys のどれかに属する会話だけを返す', async () => {
    const { startPageChatThread, pageChatThreadsFor } = await import('./pageChatThreads')
    const subjectA = uniq('subject')
    const subjectB = uniq('subject')
    const other = uniq('subject')
    const { thread: t1 } = startPageChatThread(subjectA, '質問1')
    const { thread: t2 } = startPageChatThread(subjectB, '質問2')
    startPageChatThread(other, '無関係な質問')
    const result = pageChatThreadsFor([subjectA, subjectB])
    expect(result.map((t) => t.id).sort()).toEqual([t1.id, t2.id].sort())
  })

  it('自由な質問として始めたスレッドは meta.kind が question になる', async () => {
    const { startPageChatThread, pageChatThreadsFor } = await import('./pageChatThreads')
    const subject = uniq('subject')
    const { thread } = startPageChatThread(subject, '質問')
    const [found] = pageChatThreadsFor([subject])
    expect(found.id).toBe(thread.id)
    expect(found.meta).toEqual({ subject_key: subject, kind: 'question' })
  })

  it('createPageChatThreadForCard はターン 0・kind viewpoint の会話を作り、pageChatThreadForCard で引ける', async () => {
    const { createPageChatThreadForCard, pageChatThreadForCard } = await import('./pageChatThreads')
    const subject = uniq('subject')
    const card = { card_id: uniq('card'), title: 'ある問い' }
    const thread = createPageChatThreadForCard(subject, card)
    expect(thread.turns).toEqual([])
    expect(thread.meta).toEqual({ subject_key: subject, card_id: card.card_id, kind: 'viewpoint' })
    expect(pageChatThreadForCard([subject], card.card_id)?.id).toBe(thread.id)
  })

  it('別の subjectKeys のスコープでは見えない', async () => {
    const { createPageChatThreadForCard, pageChatThreadForCard } = await import('./pageChatThreads')
    const subject = uniq('subject')
    const card = { card_id: uniq('card'), title: 'ある問い' }
    createPageChatThreadForCard(subject, card)
    expect(pageChatThreadForCard([uniq('other-subject')], card.card_id)).toBeNull()
  })
})

describe('bindPageChatThreadToCard', () => {
  it('会話をカードに結びつけると kind が viewpoint になり、題名がカードの題名になる', async () => {
    const { startPageChatThread, bindPageChatThreadToCard, pageChatThreadsFor, pageChatThreadForCard } =
      await import('./pageChatThreads')
    const subject = uniq('subject')
    const card = { card_id: uniq('card'), title: 'グラフの題名' }
    const { thread } = startPageChatThread(subject, '人口の推移を出して')
    bindPageChatThreadToCard(thread.id, card)
    const [found] = pageChatThreadsFor([subject])
    expect(found.meta).toEqual({ subject_key: subject, kind: 'viewpoint', card_id: card.card_id })
    expect(found.title).toBe(card.title)
    expect(pageChatThreadForCard([subject], card.card_id)?.id).toBe(thread.id)
  })
})

describe('setPageChatTurnDecision / latestDraft', () => {
  it('決着した turn は latestDraft の下書きに数えない（decision から判定・decidedTurnIds なしでも）', async () => {
    const { startPageChatThread, resolvePageChatAnswer, setPageChatTurnDecision, getPageChatThread, latestDraft } =
      await import('./pageChatThreads')
    const subject = uniq('subject')
    const { thread, assistantTurnId } = startPageChatThread(subject, '人口の推移を出して')
    const proposal = { params: {}, presentation: null, output_kind: 'series', title: '推移' } as const
    resolvePageChatAnswer(thread.id, assistantTurnId, { reply: 'できました', proposal })
    const withProposal = getPageChatThread(thread.id)!
    expect(latestDraft(withProposal.turns)).toBe(proposal)

    setPageChatTurnDecision(thread.id, assistantTurnId, 'added')
    const decided = getPageChatThread(thread.id)!
    expect(decided.turns.find((t) => t.id === assistantTurnId)).toMatchObject({ decision: 'added' })
    expect(latestDraft(decided.turns)).toBeNull()
  })

  it('存在しないターンは何もしない', async () => {
    const { startPageChatThread, setPageChatTurnDecision, getPageChatThread } = await import('./pageChatThreads')
    const subject = uniq('subject')
    const { thread } = startPageChatThread(subject, '質問')
    expect(() => setPageChatTurnDecision(thread.id, 'no-such-turn', 'discarded')).not.toThrow()
    expect(getPageChatThread(thread.id)).toEqual(thread)
  })
})

describe('旧索引（meta の無いスレッド）の取り込み', () => {
  afterEach(() => {
    delete (globalThis as { localStorage?: Storage }).localStorage
  })

  it('旧索引に載っていて meta の無いスレッドは kind: question として一覧に混ざる', async () => {
    const legacyThreadId = 'legacy-thread-1'
    const subject = uniq('subject')
    const fake = makeFakeLocalStorage({
      'asterism.pagechat.threads.v1': JSON.stringify({
        v: 1,
        threads: [
          {
            id: legacyThreadId,
            title: '古い質問',
            createdAt: 1,
            updatedAt: 1,
            turns: [{ id: 'u1', role: 'user', text: '古い質問', at: 1 }],
            // meta フィールド自体が存在しない（F18 より前の保存形）。
          },
        ],
      }),
      'asterism.pagechat.index.v1': JSON.stringify({ [subject]: legacyThreadId }),
    })
    ;(globalThis as { localStorage?: Storage }).localStorage = fake

    vi.resetModules()
    const { pageChatThreadsFor } = await import('./pageChatThreads')

    const result = pageChatThreadsFor([subject])
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe(legacyThreadId)
    expect(result[0].meta).toEqual({ subject_key: subject, kind: 'question' })
  })

  it('旧索引にしか subject_key が無いスレッドを bindPageChatThreadToCard しても、結びついた後も一覧・カード結びつけの両方から見える', async () => {
    const legacyThreadId = 'legacy-thread-2'
    const subject = uniq('subject')
    const fake = makeFakeLocalStorage({
      'asterism.pagechat.threads.v1': JSON.stringify({
        v: 1,
        threads: [
          {
            id: legacyThreadId,
            title: '古い質問',
            createdAt: 1,
            updatedAt: 1,
            turns: [{ id: 'u1', role: 'user', text: '古い質問', at: 1 }],
            // meta フィールド自体が存在しない（F18 より前の保存形）。
          },
        ],
      }),
      'asterism.pagechat.index.v1': JSON.stringify({ [subject]: legacyThreadId }),
    })
    ;(globalThis as { localStorage?: Storage }).localStorage = fake

    vi.resetModules()
    const { bindPageChatThreadToCard, pageChatThreadsFor, pageChatThreadForCard } = await import('./pageChatThreads')

    const card = { card_id: 'card-x', title: '棒グラフ' }
    bindPageChatThreadToCard(legacyThreadId, card)

    const list = pageChatThreadsFor([subject])
    expect(list.map((t) => t.id)).toEqual([legacyThreadId])
    expect(list[0].meta).toEqual({ subject_key: subject, card_id: card.card_id, kind: 'viewpoint' })
    expect(pageChatThreadForCard([subject], card.card_id)?.id).toBe(legacyThreadId)
  })

  it('meta を持つスレッドが既にあれば旧索引のエントリと二重に出さない', async () => {
    const subject = uniq('subject')
    const fake = makeFakeLocalStorage({
      'asterism.pagechat.index.v1': JSON.stringify({ [subject]: 'wont-be-used' }),
    })
    ;(globalThis as { localStorage?: Storage }).localStorage = fake

    vi.resetModules()
    const { startPageChatThread, pageChatThreadsFor } = await import('./pageChatThreads')
    const { thread } = startPageChatThread(subject, '新しい質問')
    const result = pageChatThreadsFor([subject])
    expect(result.map((t) => t.id)).toEqual([thread.id])
  })
})
