// Ask chat threads — the client-held conversation store (ADR ask-chat-threads.md).
//
// A thread is a list of turns: the user's question, then the agent's grounded
// answer (the AskResponse contract: answer + citations + notes + sparql), and so
// on. The demo-agent stays STATELESS: on every question the UI sends the
// thread's earlier turns as `history` and the agent replays them as the LLM's
// message prefix. Persistence is per browser (localStorage) — the shared
// production instance has one login for everyone, so keeping each person's
// questions in their own browser is the privacy-preserving default, and it is
// exactly what the desktop (local-first) app wants too. Cross-tab changes are
// picked up via the `storage` event.
//
// The store is a plain module (not React state) so a thread keeps receiving its
// answer while the user navigates elsewhere; components subscribe through
// useSyncExternalStore. Every mutation produces new array/object references
// (immutable updates) so subscribers re-render only on real change.

import { useSyncExternalStore } from 'react'
import type { AskHistoryTurn, AskResponse } from './demoApi'

export interface AskUserTurn {
  id: string
  role: 'user'
  text: string
  at: number
}

export interface AskAssistantTurn {
  id: string
  role: 'assistant'
  at: number
  /** The grounded answer; null while pending, on error, or when interrupted. */
  result: AskResponse | null
  /** Waiting for the agent (in-memory only — persisted as `interrupted`). */
  pending?: boolean
  /** The call failed; the message is shown with a retry action. */
  error?: string
  /** The page unloaded while the answer was pending (retry-able). */
  interrupted?: boolean
}

export type AskTurn = AskUserTurn | AskAssistantTurn

export interface AskThread {
  id: string
  /** Derived from the first question (deterministic — no LLM titling). */
  title: string
  createdAt: number
  updatedAt: number
  turns: AskTurn[]
}

const STORAGE_KEY = 'asterism.ask.threads.v1'
const MAX_THREADS = 60 // oldest threads fall off (localStorage is ~5 MB)
const TITLE_MAX = 48

// ---- state + subscription -------------------------------------------------

let threads: AskThread[] = load()
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): AskThread[] {
  return threads
}

/** All threads, most recently updated first. Re-renders on any change. */
export function useAskThreads(): AskThread[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

export function getThread(id: string | null | undefined): AskThread | undefined {
  return id ? threads.find((t) => t.id === id) : undefined
}

// Another tab wrote the store — adopt its snapshot (an in-flight answer here is
// finished by resolveAnswer, which re-reads by id, so nothing is lost).
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== STORAGE_KEY) return
    threads = load()
    emit()
  })
}

// ---- persistence ------------------------------------------------------------

function load(): AskThread[] {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as { v?: number; threads?: unknown }
    if (!Array.isArray(parsed.threads)) return []
    return parsed.threads.map(normalizeThread).filter((t): t is AskThread => t !== null)
  } catch {
    return []
  }
}

// Tolerate anything a future version / a hand edit left behind: unknown shapes
// are dropped, and a turn that was still pending when the page unloaded comes
// back as `interrupted` (its answer never arrived).
function normalizeThread(raw: unknown): AskThread | null {
  const r = (raw ?? {}) as Record<string, unknown>
  if (typeof r.id !== 'string' || !r.id) return null
  const turns: AskTurn[] = []
  for (const x of Array.isArray(r.turns) ? r.turns : []) {
    const o = (x ?? {}) as Record<string, unknown>
    if (typeof o.id !== 'string') continue
    const at = typeof o.at === 'number' ? o.at : 0
    if (o.role === 'user' && typeof o.text === 'string') {
      turns.push({ id: o.id, role: 'user', text: o.text, at })
    } else if (o.role === 'assistant') {
      const result = o.result && typeof o.result === 'object' ? (o.result as AskResponse) : null
      const interrupted = o.pending === true || o.interrupted === true
      turns.push({
        id: o.id,
        role: 'assistant',
        at,
        result: interrupted ? null : result,
        error: typeof o.error === 'string' && o.error ? o.error : undefined,
        interrupted: interrupted || undefined,
      })
    }
  }
  const createdAt = typeof r.createdAt === 'number' ? r.createdAt : 0
  return {
    id: r.id,
    title: typeof r.title === 'string' && r.title ? r.title : titleFrom(turns),
    createdAt,
    updatedAt: typeof r.updatedAt === 'number' ? r.updatedAt : createdAt,
    turns,
  }
}

function save() {
  if (typeof localStorage === 'undefined') return
  const serializable = threads.map((t) => ({
    ...t,
    turns: t.turns.map((turn) =>
      turn.role === 'assistant' && turn.pending
        ? { ...turn, pending: undefined, interrupted: true }
        : turn,
    ),
  }))
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ v: 1, threads: serializable }))
  } catch {
    // Quota: drop the oldest half and try once more; if that still fails the
    // in-memory store keeps working for this session.
    try {
      const half = serializable.slice(0, Math.max(1, Math.floor(serializable.length / 2)))
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ v: 1, threads: half }))
    } catch {
      /* ignore */
    }
  }
}

function commit(next: AskThread[]) {
  // Most recently updated first; cap the count.
  threads = [...next].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_THREADS)
  save()
  emit()
}

// ---- helpers ------------------------------------------------------------------

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** Thread title = the first question, whitespace-collapsed and clipped. */
export function titleFrom(turns: AskTurn[]): string {
  const first = turns.find((t): t is AskUserTurn => t.role === 'user')
  return clipTitle(first?.text ?? '')
}

function clipTitle(text: string): string {
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > TITLE_MAX ? `${one.slice(0, TITLE_MAX - 1).trimEnd()}…` : one
}

function updateThread(id: string, fn: (t: AskThread) => AskThread): AskThread | undefined {
  const cur = threads.find((t) => t.id === id)
  if (!cur) return undefined
  const next = fn(cur)
  commit(threads.map((t) => (t.id === id ? next : t)))
  return next
}

// ---- mutations ----------------------------------------------------------------

/**
 * Start a new thread with the first question already asked (pending answer).
 * A "new chat" is transient until the first message — no empty threads pile up
 * in the list. Returns the thread and the ids of the two turns just added.
 */
export function startThread(question: string): {
  thread: AskThread
  userTurnId: string
  assistantTurnId: string
} {
  const now = Date.now()
  const user: AskUserTurn = { id: newId(), role: 'user', text: question, at: now }
  const assistant: AskAssistantTurn = {
    id: newId(),
    role: 'assistant',
    at: now,
    result: null,
    pending: true,
  }
  const thread: AskThread = {
    id: newId(),
    title: clipTitle(question),
    createdAt: now,
    updatedAt: now,
    turns: [user, assistant],
  }
  commit([thread, ...threads])
  return { thread, userTurnId: user.id, assistantTurnId: assistant.id }
}

/** Ask a follow-up in an existing thread (pending answer). */
export function appendQuestion(
  threadId: string,
  question: string,
): { userTurnId: string; assistantTurnId: string } | null {
  const now = Date.now()
  const user: AskUserTurn = { id: newId(), role: 'user', text: question, at: now }
  const assistant: AskAssistantTurn = {
    id: newId(),
    role: 'assistant',
    at: now,
    result: null,
    pending: true,
  }
  const next = updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: [...t.turns, user, assistant],
  }))
  return next ? { userTurnId: user.id, assistantTurnId: assistant.id } : null
}

/** The answer arrived. */
export function resolveAnswer(threadId: string, assistantTurnId: string, result: AskResponse) {
  const now = Date.now()
  updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: t.turns.map((turn) =>
      turn.id === assistantTurnId && turn.role === 'assistant'
        ? { id: turn.id, role: 'assistant', at: now, result }
        : turn,
    ),
  }))
}

/** The call failed — keep the question, mark the answer slot retry-able. */
export function failAnswer(threadId: string, assistantTurnId: string, error: string) {
  const now = Date.now()
  updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: t.turns.map((turn) =>
      turn.id === assistantTurnId && turn.role === 'assistant'
        ? { id: turn.id, role: 'assistant', at: now, result: null, error }
        : turn,
    ),
  }))
}

/**
 * Re-ask the question that precedes an errored/interrupted answer. Puts the
 * answer slot back to pending and returns the question text (null if the slot
 * is not retry-able).
 */
export function retryAnswer(threadId: string, assistantTurnId: string): string | null {
  const thread = getThread(threadId)
  if (!thread) return null
  const idx = thread.turns.findIndex((t) => t.id === assistantTurnId)
  const slot = thread.turns[idx]
  const question = thread.turns[idx - 1]
  if (!slot || slot.role !== 'assistant' || slot.pending || slot.result) return null
  if (!question || question.role !== 'user') return null
  const now = Date.now()
  updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: t.turns.map((turn) =>
      turn.id === assistantTurnId
        ? { id: turn.id, role: 'assistant', at: now, result: null, pending: true }
        : turn,
    ),
  }))
  return question.text
}

export function deleteThread(threadId: string) {
  if (!threads.some((t) => t.id === threadId)) return
  commit(threads.filter((t) => t.id !== threadId))
}

/** True while an answer is pending in this thread (one question at a time). */
export function isThreadBusy(thread: AskThread | undefined): boolean {
  return !!thread?.turns.some((t) => t.role === 'assistant' && t.pending)
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
