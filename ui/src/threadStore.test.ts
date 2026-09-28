// PR F18 契約メモ §1.1: `Thread.meta`（往復・旧データ）と、新しい API
// （`startThread(message, meta?)`／`createEmptyThread`／`setThreadMeta`／
// `setThreadTitle`）の検証。`window`/`localStorage` が無い実行環境（vitest は
// node 環境）なので `createThreadStore` はメモリ内の状態だけで動く —
// bootstrap（サーバ問い合わせ）は `typeof window === 'undefined'` で走らない。

import { describe, expect, it } from 'vitest'
import { createThreadStore, type ThreadMeta } from './threadStore'

interface Result {
  reply: string
}

function normalizeResult(raw: unknown): Result | null {
  if (raw === null || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  return typeof r.reply === 'string' ? { reply: r.reply } : null
}

function makeStore() {
  return createThreadStore<Result>({
    namespace: 'pagechat',
    storageKey: `test.threads.${Math.random().toString(36).slice(2)}`,
    normalizeResult,
  })
}

describe('Thread.meta — 往復', () => {
  it('startThread(message, meta) は meta を持つスレッドを作る', () => {
    const store = makeStore()
    const meta: ThreadMeta = { subject_key: 'subject-1', kind: 'question' }
    const { thread } = store.startThread('こんにちは', meta)
    expect(thread.meta).toEqual(meta)
    expect(store.getThread(thread.id)?.meta).toEqual(meta)
  })

  it('meta を渡さない startThread は meta の無いスレッドを作る（既存の呼び出し元は不変）', () => {
    const store = makeStore()
    const { thread } = store.startThread('こんにちは')
    expect(thread.meta).toBeUndefined()
  })

  it('createEmptyThread はターン 0 かつ meta つきのスレッドを作る', () => {
    const store = makeStore()
    const meta: ThreadMeta = { subject_key: 'subject-1', card_id: 'card-1', kind: 'viewpoint' }
    const thread = store.createEmptyThread('題名', meta)
    expect(thread.turns).toEqual([])
    expect(thread.title).toBe('題名')
    expect(thread.meta).toEqual(meta)
    expect(store.getThread(thread.id)?.turns).toEqual([])
  })
})

describe('setThreadMeta / setThreadTitle', () => {
  it('setThreadMeta は既存の meta に上書きでなく合成する', () => {
    const store = makeStore()
    const { thread } = store.startThread('質問', { subject_key: 'subject-1', kind: 'question' })
    store.setThreadMeta(thread.id, { card_id: 'card-9', kind: 'viewpoint' })
    expect(store.getThread(thread.id)?.meta).toEqual({
      subject_key: 'subject-1',
      card_id: 'card-9',
      kind: 'viewpoint',
    })
  })

  it('存在しない threadId は何もしない', () => {
    const store = makeStore()
    expect(() => store.setThreadMeta('no-such-id', { kind: 'viewpoint' })).not.toThrow()
  })

  it('setThreadTitle は renameThread と同じ挙動（既存名の別名）', () => {
    const store = makeStore()
    const { thread } = store.startThread('元の題名')
    store.setThreadTitle(thread.id, '新しい題名')
    expect(store.getThread(thread.id)?.title).toBe('新しい題名')
  })
})

describe('setTurnDecision', () => {
  it('assistant turn に decision を書き、読み返せる（往復）', () => {
    const store = makeStore()
    const { thread, assistantTurnId } = store.startThread('こんにちは')
    store.resolveAnswer(thread.id, assistantTurnId, { reply: '了解' })
    store.setTurnDecision(thread.id, assistantTurnId, 'replaced')
    const turn = store.getThread(thread.id)?.turns.find((t) => t.id === assistantTurnId)
    expect(turn && turn.role === 'assistant' ? turn.decision : undefined).toBe('replaced')
  })

  it('存在しないスレッド/ターンは何もしない', () => {
    const store = makeStore()
    const { thread, assistantTurnId } = store.startThread('こんにちは')
    expect(() => store.setTurnDecision('no-such-thread', assistantTurnId, 'added')).not.toThrow()
    expect(() => store.setTurnDecision(thread.id, 'no-such-turn', 'added')).not.toThrow()
    const turn = store.getThread(thread.id)?.turns.find((t) => t.id === assistantTurnId)
    expect(turn && turn.role === 'assistant' ? turn.decision : 'unset').toBeUndefined()
  })

  it('未知の decision（旧データ）は捨てて読める', () => {
    const store = makeStore()
    const { thread, assistantTurnId } = store.startThread('こんにちは')
    store.resolveAnswer(thread.id, assistantTurnId, { reply: '了解' })
    // 直接 normalizeThread を通す経路（localStorage 読み込み）で確かめる。
    const raw = {
      v: 1,
      threads: [
        {
          id: 'legacy-decision',
          title: 'x',
          createdAt: 1,
          updatedAt: 1,
          turns: [{ id: 'a1', role: 'assistant', at: 1, result: { reply: 'ok' }, decision: 'not-a-real-decision' }],
        },
      ],
    }
    const g = globalThis as { localStorage?: Storage }
    const backing: Record<string, string> = { legacykey: JSON.stringify(raw) }
    const prev = g.localStorage
    g.localStorage = {
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
    try {
      const reloaded = createThreadStore<Result>({ namespace: 'pagechat', storageKey: 'legacykey', normalizeResult })
      const turn = reloaded.getThread('legacy-decision')?.turns.find((t) => t.id === 'a1')
      expect(turn && turn.role === 'assistant' ? turn.decision : 'unset').toBeUndefined()
    } finally {
      g.localStorage = prev
    }
  })
})

describe('旧データ（meta の無い保存済みスレッド）', () => {
  /** vitest は node 環境（`window`/`localStorage` は既定で無い）なので、
   *  `localStorage` を最小限のメモリ実装で仮に立てて `load()`（実際の
   *  `normalizeThread` の入口）を通す。テストの後で必ず消す。 */
  function withFakeLocalStorage<T>(initial: Record<string, string>, fn: () => T): T {
    const backing = { ...initial }
    const fake: Storage = {
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
    const g = globalThis as { localStorage?: Storage }
    const prev = g.localStorage
    g.localStorage = fake
    try {
      return fn()
    } finally {
      g.localStorage = prev
    }
  }

  it('meta フィールドの無い旧形の JSON は例外にならず、meta 無しのスレッドとして読める', () => {
    const key = `test.threads.legacy.${Math.random().toString(36).slice(2)}`
    const legacy = {
      v: 1,
      threads: [
        {
          id: 'old-thread-1',
          title: '古い会話',
          createdAt: 1,
          updatedAt: 1,
          turns: [{ id: 'u1', role: 'user', text: '古い質問', at: 1 }],
          // meta フィールド自体が存在しない（F18 より前の保存形）。
        },
      ],
    }
    withFakeLocalStorage({ [key]: JSON.stringify(legacy) }, () => {
      const store = createThreadStore<Result>({ namespace: 'pagechat', storageKey: key, normalizeResult })
      const thread = store.getThread('old-thread-1')
      expect(thread).toBeDefined()
      expect(thread?.meta).toBeUndefined()
      expect(thread?.title).toBe('古い会話')
    })
  })

  it('meta が壊れた形（配列や不明な kind）のときは meta を落として読める', () => {
    const key = `test.threads.broken.${Math.random().toString(36).slice(2)}`
    const broken = {
      v: 1,
      threads: [
        { id: 't-array', title: 'x', createdAt: 1, updatedAt: 1, turns: [], meta: ['not', 'an', 'object'] },
        { id: 't-badkind', title: 'y', createdAt: 1, updatedAt: 1, turns: [], meta: { kind: 'not-a-real-kind' } },
      ],
    }
    withFakeLocalStorage({ [key]: JSON.stringify(broken) }, () => {
      const store = createThreadStore<Result>({ namespace: 'pagechat', storageKey: key, normalizeResult })
      expect(store.getThread('t-array')?.meta).toBeUndefined()
      expect(store.getThread('t-badkind')?.meta).toBeUndefined()
    })
  })
})
