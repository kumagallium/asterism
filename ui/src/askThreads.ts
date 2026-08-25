// Ask chat threads — the client-held conversation store (ADR ask-chat-threads.md,
// ADR app-data-on-disk.md).
//
// A thread is a list of turns: the user's question, then the agent's grounded
// answer (the AskResponse contract: answer + citations + notes + sparql), and so
// on. The demo-agent stays STATELESS: on every question the UI sends the
// thread's earlier turns as `history` and the agent replays them as the LLM's
// message prefix.
//
// The store mechanism (persistence, subscription, mutations) is generic and
// lives in `threadStore.ts` (ADR design-consult-chat.md D2 factored it out so
// the design-consult drawer's threads share it under the `consult` namespace).
// This module is a thin, behavior-preserving wrapper: the exported names,
// storage key, and on-disk/localStorage shape are UNCHANGED from before the
// extraction — only the mechanics moved.

import type { AskHistoryTurn, AskResponse } from './demoApi'
import {
  createThreadStore,
  type AssistantTurn,
  type Thread,
  type Turn,
  type UserTurn,
} from './threadStore'

export type AskUserTurn = UserTurn
export type AskAssistantTurn = AssistantTurn<AskResponse>
export type AskTurn = Turn<AskResponse>
export type AskThread = Thread<AskResponse>

const STORAGE_KEY = 'asterism.ask.threads.v1'
const MAX_THREADS = 60 // oldest threads fall off (localStorage is ~5 MB)

function normalizeResult(raw: unknown): AskResponse | null {
  return raw && typeof raw === 'object' ? (raw as AskResponse) : null
}

const store = createThreadStore<AskResponse>({
  namespace: 'ask',
  storageKey: STORAGE_KEY,
  maxThreads: MAX_THREADS,
  normalizeResult,
})

/** All threads, most recently updated first. Re-renders on any change. */
export const useAskThreads = store.useThreads

/** False until the single-user check (and, in single-user mode, the initial
 *  server read) have settled. Use this to hold off empty-state copy — it
 *  should never flash "no chats yet" before the real list has had a chance to
 *  arrive. */
export const useAskThreadsLoaded = store.useThreadsLoaded

export const getThread = store.getThread

/** Thread title = the first question, whitespace-collapsed and clipped. */
export const titleFrom = store.titleFrom

/** Rename a thread (empty → back to the first-question title). Does not touch
 *  updatedAt, so the list order stays. */
export const renameThread = store.renameThread

export const deleteThread = store.deleteThread

/** True while an answer is pending in this thread (one question at a time). */
export const isThreadBusy = store.isThreadBusy

/** The answer arrived. Ignored if the slot was stopped/superseded meanwhile. */
export const resolveAnswer = store.resolveAnswer

/** The call failed — keep the question, mark the answer slot retry-able. */
export const failAnswer = store.failAnswer

// In-flight requests by answer slot, so "stop" can abort the fetch from
// wherever the UI is (the view may have re-mounted since the question was sent).
export const registerInflight = store.registerInflight
export const unregisterInflight = store.unregisterInflight

/**
 * Stop waiting for an answer: abort the request (the agent's own work is not
 * cancelled — the UI just stops listening) and mark the slot stopped +
 * retry-able. A response that arrives anyway is ignored (attempt guard).
 */
export const stopAnswer = store.stopAnswer

export interface AskAttempt {
  userTurnId: string
  assistantTurnId: string
  /** Pass back to resolveAnswer / failAnswer so a stale attempt is ignored. */
  attempt: number
}

/**
 * Start a new thread with the first question already asked (pending answer).
 * A "new chat" is transient until the first message — no empty threads pile up
 * in the list. Returns the thread and the attempt handle for the answer slot.
 */
export function startThread(question: string): AskAttempt & { thread: AskThread } {
  return store.startThread(question)
}

/** Ask a follow-up in an existing thread (pending answer). */
export function appendQuestion(threadId: string, question: string): AskAttempt | null {
  return store.appendMessage(threadId, question)
}

/**
 * Re-ask the question that precedes an errored/interrupted/stopped answer. Puts
 * the answer slot back to pending and returns the question text plus the new
 * attempt handle (null if the slot is not retry-able).
 */
export function retryAnswer(
  threadId: string,
  assistantTurnId: string,
): { question: string; attempt: number } | null {
  const r = store.retryAnswer(threadId, assistantTurnId)
  return r ? { question: r.message, attempt: r.attempt } : null
}

// ---- history for the agent -----------------------------------------------------

const HISTORY_CITATIONS_MAX = 8

/**
 * The earlier turns to send with a question: completed question→answer pairs
 * only (pending/errored slots and their questions are skipped), oldest first,
 * up to (not including) `beforeTurnId` — the assistant slot being (re)asked.
 * The assistant content is what the user saw (answer text) plus the cited IRIs
 * so a follow-up like "その論文の DOI は？" can be grounded by querying that IRI
 * in the new turn; the agent's system prompt forbids re-citing from memory.
 */
export function historyFor(thread: AskThread, beforeTurnId?: string): AskHistoryTurn[] {
  const out: AskHistoryTurn[] = []
  const turns = thread.turns
  for (let i = 0; i < turns.length; i++) {
    const t = turns[i]
    if (t.id === beforeTurnId) break
    if (t.role !== 'user') continue
    const a = turns[i + 1]
    if (!a || a.role !== 'assistant' || a.id === beforeTurnId || !a.result) continue
    out.push({ role: 'user', content: t.text })
    out.push({ role: 'assistant', content: assistantContent(a.result) })
  }
  return out
}

function assistantContent(r: AskResponse): string {
  const lines = [r.answer.trim()]
  if (r.citations.length > 0) {
    lines.push('', '[citations]')
    for (const c of r.citations.slice(0, HISTORY_CITATIONS_MAX)) {
      lines.push(`- ${[c.kind, c.label].filter(Boolean).join(' ')} <${c.iri}>`)
    }
  }
  return lines.join('\n')
}
