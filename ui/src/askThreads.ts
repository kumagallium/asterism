// Ask chat threads — the client-held conversation store (ADR ask-chat-threads.md,
// ADR app-data-on-disk.md).
//
// A thread is a list of turns: the user's question, then the agent's grounded
// answer (the AskResponse contract: answer + citations + notes + sparql), and so
// on. The demo-agent stays STATELESS: on every question the UI sends the
// thread's earlier turns as `history` and the agent replays them as the LLM's
// message prefix.
//
// Persistence has a store-boundary swap (ADR app-data-on-disk.md D1): the
// module always loads synchronously from `localStorage` on boot (unchanged
// browser behavior, so nothing regresses before the server has answered), then
// asks the server `/api/appdata/info` who it is. If the server declares itself
// single-user (a hand-run `asterism-local`), the in-memory list is replaced by
// the server's copy (or, on first run, the localStorage threads are migrated
// up — ADR D5) and every further mutation is persisted to disk via
// PUT/DELETE instead of localStorage. A shared api (production) keeps the
// original browser-only behavior — the privacy-preserving default when there is
// one login for everyone. `useAskThreadsLoaded()` reports when that decision +
// the initial read have both settled. Cross-tab changes are picked up via the
// `storage` event, but only in browser mode (a disk-backed store has no other
// browser tab racing it).
//
// The store is a plain module (not React state) so a thread keeps receiving its
// answer while the user navigates elsewhere; components subscribe through
// useSyncExternalStore. Every mutation produces new array/object references
// (immutable updates) so subscribers re-render only on real change. `getSnapshot`
// stays synchronous always — memory is the source of truth; disk is a write-behind
// mirror.

import { useSyncExternalStore } from 'react'
import {
  deleteAppDataThread,
  fetchAppDataThreads,
  initAppData,
  putAppDataThread,
} from './appdata'
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
  /** The grounded answer; null while pending, on error, when interrupted, or stopped. */
  result: AskResponse | null
  /** Waiting for the agent (in-memory only — persisted as `interrupted`). */
  pending?: boolean
  /** Which ask() attempt owns this pending slot (in-memory): a late response
   *  from a stopped/superseded attempt must not overwrite a newer one. */
  attempt?: number
  /** The call failed; the message is shown with a retry action. */
  error?: string
  /** The page unloaded while the answer was pending (retry-able). */
  interrupted?: boolean
  /** The user stopped waiting (retry-able). The agent's work is not cancelled
   *  server-side; the UI just stops listening. */
  stopped?: boolean
}

export type AskTurn = AskUserTurn | AskAssistantTurn

export interface AskThread {
  id: string
  /** Derived from the first question (deterministic — no LLM titling); the
   *  user can rename it. */
  title: string
  createdAt: number
  updatedAt: number
  turns: AskTurn[]
}

const STORAGE_KEY = 'asterism.ask.threads.v1'
const MAX_THREADS = 60 // oldest threads fall off (localStorage is ~5 MB)
const TITLE_MAX = 48
const FLUSH_DEBOUNCE_MS = 300

// ---- state + subscription -------------------------------------------------

let threads: AskThread[] = load()
// True once the single-user check + (if applicable) the server's own thread
// list have both been read. Browser mode also waits for this (it just settles
// fast) so the UI never has to special-case "which mode am I in".
let loaded = false
// Persist to the server instead of localStorage — decided once by bootstrap().
let serverMode = false
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

function getLoadedSnapshot(): boolean {
  return loaded
}

/** False until the single-user check (and, in single-user mode, the initial
 *  server read) have settled. Use this to hold off empty-state copy — it
 *  should never flash "no chats yet" before the real list has had a chance to
 *  arrive. */
export function useAskThreadsLoaded(): boolean {
  return useSyncExternalStore(subscribe, getLoadedSnapshot, getLoadedSnapshot)
}

export function getThread(id: string | null | undefined): AskThread | undefined {
  return id ? threads.find((t) => t.id === id) : undefined
}

// Another tab wrote the store — adopt its snapshot (an in-flight answer here is
// finished by resolveAnswer, which re-reads by id, so nothing is lost). Only
// meaningful in browser mode: a disk-backed store has no other localStorage tab
// to race, and server mode never writes STORAGE_KEY.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (serverMode || e.key !== STORAGE_KEY) return
    threads = load()
    emit()
  })
  window.addEventListener('beforeunload', flushServerNow)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushServerNow()
  })
  void bootstrap()
}

/** Decide the persistence mode (ADR app-data-on-disk.md D1) and, in single-user
 *  mode, replace the localStorage-loaded snapshot with the server's — migrating
 *  it up first if the server is empty (D5). Runs once at module load. */
async function bootstrap(): Promise<void> {
  try {
    const info = await initAppData()
    if (!info.singleUser) return
    serverMode = true
    const serverThreads = await fetchAppDataThreads()
    const normalized = serverThreads
      .map(normalizeThread)
      .filter((t): t is AskThread => t !== null)
    if (normalized.length === 0 && threads.length > 0) {
      const migrated = await migrateToServer(threads)
      if (migrated) {
        try {
          localStorage.removeItem(STORAGE_KEY)
        } catch {
          /* ignore */
        }
      }
      // Keep the in-memory (just-migrated) threads as-is either way.
    } else {
      threads = [...normalized].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_THREADS)
    }
  } catch {
    // /api/appdata/info itself falls back internally; a failure here means the
    // single-user thread GET failed after info said yes. Stay in whatever mode
    // we reached — memory (from localStorage) keeps working either way.
  } finally {
    loaded = true
    emit()
  }
}

/** One-time upload of the localStorage threads to an empty server (ADR D5).
 *  All-or-nothing: on any failure nothing is removed locally, so the next
 *  bootstrap can try again. */
async function migrateToServer(localThreads: AskThread[]): Promise<boolean> {
  try {
    for (const t of localThreads) {
      await putAppDataThread(serializeThread(t))
    }
    return true
  } catch {
    return false
  }
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
        stopped: o.stopped === true && !interrupted ? true : undefined,
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

// Turn shape as written to storage (either localStorage or a server PUT body):
// a pending slot that never resolved becomes `interrupted` (so a reload/restart
// offers retry instead of hanging forever), and the in-memory-only `attempt`
// token is dropped.
function serializeThread(t: AskThread): AskThread {
  return {
    ...t,
    turns: t.turns.map((turn) =>
      turn.role === 'assistant' && turn.pending
        ? { ...turn, pending: undefined, attempt: undefined, interrupted: true }
        : turn.role === 'assistant'
          ? { ...turn, attempt: undefined }
          : turn,
    ),
  }
}

function saveLocal() {
  if (typeof localStorage === 'undefined') return
  const serializable = threads.map(serializeThread)
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

// ---- server persistence (single-user mode) ----------------------------------
//
// Only the threads that actually changed are queued (Graphium-style, one file
// per thread — ADR D2). A short debounce coalesces bursts (an answer streaming
// in touches the same thread repeatedly); `beforeunload` / tab-hide flush
// immediately so a closed window does not lose the last few hundred ms. A PUT
// that fails leaves the id queued so the *next* mutation (or the next flush)
// retries it — the in-memory thread is never rolled back for a network error.

const pendingPuts = new Set<string>()
const pendingDeletes = new Set<string>()
let flushTimer: ReturnType<typeof setTimeout> | null = null

function queueServerPut(id: string) {
  pendingDeletes.delete(id)
  pendingPuts.add(id)
  scheduleFlush()
}

function queueServerDelete(id: string) {
  pendingPuts.delete(id)
  pendingDeletes.add(id)
  scheduleFlush()
}

function scheduleFlush() {
  if (flushTimer !== null) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    void flushServer()
  }, FLUSH_DEBOUNCE_MS)
}

function flushServerNow() {
  if (!serverMode || (pendingPuts.size === 0 && pendingDeletes.size === 0)) return
  if (flushTimer !== null) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
  void flushServer()
}

async function flushServer(): Promise<void> {
  for (const id of [...pendingPuts]) {
    const t = threads.find((th) => th.id === id)
    if (!t) {
      pendingPuts.delete(id)
      continue
    }
    try {
      await putAppDataThread(serializeThread(t))
      pendingPuts.delete(id)
    } catch {
      // Left queued; retried on the next mutation/flush.
    }
  }
  for (const id of [...pendingDeletes]) {
    try {
      await deleteAppDataThread(id)
      pendingDeletes.delete(id)
    } catch {
      // Left queued; retried on the next mutation/flush.
    }
  }
}

function persist(changedIds: Set<string>, deletedIds: Set<string>) {
  if (serverMode) {
    for (const id of changedIds) queueServerPut(id)
    for (const id of deletedIds) queueServerDelete(id)
  } else {
    saveLocal()
  }
}

function commit(next: AskThread[]) {
  const prevById = new Map(threads.map((t) => [t.id, t]))
  // Most recently updated first; cap the count.
  const nextThreads = [...next].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_THREADS)
  const nextIds = new Set(nextThreads.map((t) => t.id))
  const changedIds = new Set<string>()
  for (const t of nextThreads) {
    if (prevById.get(t.id) !== t) changedIds.add(t.id)
  }
  const deletedIds = new Set<string>()
  for (const id of prevById.keys()) {
    if (!nextIds.has(id)) deletedIds.add(id)
  }
  threads = nextThreads
  persist(changedIds, deletedIds)
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

// Each ask() attempt gets a token; a pending slot remembers which attempt owns
// it, so a late response from a stopped or superseded attempt (stop → retry →
// the first fetch finally returns) cannot overwrite the newer state.
let attemptSeq = 0

function pendingSlot(): AskAssistantTurn {
  return {
    id: newId(),
    role: 'assistant',
    at: Date.now(),
    result: null,
    pending: true,
    attempt: ++attemptSeq,
  }
}

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
  const now = Date.now()
  const user: AskUserTurn = { id: newId(), role: 'user', text: question, at: now }
  const assistant = pendingSlot()
  const thread: AskThread = {
    id: newId(),
    title: clipTitle(question),
    createdAt: now,
    updatedAt: now,
    turns: [user, assistant],
  }
  commit([thread, ...threads])
  return {
    thread,
    userTurnId: user.id,
    assistantTurnId: assistant.id,
    attempt: assistant.attempt!,
  }
}

/** Ask a follow-up in an existing thread (pending answer). */
export function appendQuestion(threadId: string, question: string): AskAttempt | null {
  const now = Date.now()
  const user: AskUserTurn = { id: newId(), role: 'user', text: question, at: now }
  const assistant = pendingSlot()
  const next = updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: [...t.turns, user, assistant],
  }))
  return next
    ? { userTurnId: user.id, assistantTurnId: assistant.id, attempt: assistant.attempt! }
    : null
}

// Replace the answer slot ONLY if it is still the pending slot of this attempt
// (a stopped or superseded attempt reports into the void). Returns whether the
// update was applied.
function settleSlot(
  threadId: string,
  assistantTurnId: string,
  attempt: number | undefined,
  make: (slot: AskAssistantTurn, now: number) => AskAssistantTurn,
): boolean {
  const cur = getThread(threadId)
  const slot = cur?.turns.find((t) => t.id === assistantTurnId)
  if (!cur || !slot || slot.role !== 'assistant' || !slot.pending) return false
  if (attempt !== undefined && slot.attempt !== attempt) return false
  const now = Date.now()
  updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: t.turns.map((turn) =>
      turn.id === assistantTurnId && turn.role === 'assistant' ? make(turn, now) : turn,
    ),
  }))
  return true
}

/** The answer arrived. Ignored if the slot was stopped/superseded meanwhile. */
export function resolveAnswer(
  threadId: string,
  assistantTurnId: string,
  result: AskResponse,
  attempt?: number,
): boolean {
  return settleSlot(threadId, assistantTurnId, attempt, (slot, now) => ({
    id: slot.id,
    role: 'assistant',
    at: now,
    result,
  }))
}

/** The call failed — keep the question, mark the answer slot retry-able. */
export function failAnswer(
  threadId: string,
  assistantTurnId: string,
  error: string,
  attempt?: number,
): boolean {
  return settleSlot(threadId, assistantTurnId, attempt, (slot, now) => ({
    id: slot.id,
    role: 'assistant',
    at: now,
    result: null,
    error,
  }))
}

// In-flight requests by answer slot, so "stop" can abort the fetch from
// wherever the UI is (the view may have re-mounted since the question was sent).
const inflight = new Map<string, AbortController>()

export function registerInflight(assistantTurnId: string, controller: AbortController) {
  inflight.set(assistantTurnId, controller)
}

export function unregisterInflight(assistantTurnId: string, controller: AbortController) {
  if (inflight.get(assistantTurnId) === controller) inflight.delete(assistantTurnId)
}

/**
 * Stop waiting for an answer: abort the request (the agent's own work is not
 * cancelled — the UI just stops listening) and mark the slot stopped +
 * retry-able. A response that arrives anyway is ignored (attempt guard).
 */
export function stopAnswer(threadId: string, assistantTurnId: string): boolean {
  const applied = settleSlot(threadId, assistantTurnId, undefined, (slot, now) => ({
    id: slot.id,
    role: 'assistant',
    at: now,
    result: null,
    stopped: true,
  }))
  const ctrl = inflight.get(assistantTurnId)
  if (ctrl) {
    inflight.delete(assistantTurnId)
    ctrl.abort()
  }
  return applied
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
  const thread = getThread(threadId)
  if (!thread) return null
  const idx = thread.turns.findIndex((t) => t.id === assistantTurnId)
  const slot = thread.turns[idx]
  const question = thread.turns[idx - 1]
  if (!slot || slot.role !== 'assistant' || slot.pending || slot.result) return null
  if (!question || question.role !== 'user') return null
  const now = Date.now()
  const attempt = ++attemptSeq
  updateThread(threadId, (t) => ({
    ...t,
    updatedAt: now,
    turns: t.turns.map((turn) =>
      turn.id === assistantTurnId
        ? { id: turn.id, role: 'assistant', at: now, result: null, pending: true, attempt }
        : turn,
    ),
  }))
  return { question: question.text, attempt }
}

/** Rename a thread (empty → back to the first-question title). Does not touch
 *  updatedAt, so the list order stays. */
export function renameThread(threadId: string, title: string) {
  updateThread(threadId, (t) => ({
    ...t,
    title: clipTitle(title) || titleFrom(t.turns),
  }))
}

export function deleteThread(threadId: string) {
  const thread = threads.find((t) => t.id === threadId)
  if (!thread) return
  // A deleted thread has no slot to receive its pending answer — stop listening.
  for (const turn of thread.turns) {
    if (turn.role !== 'assistant' || !turn.pending) continue
    const ctrl = inflight.get(turn.id)
    if (ctrl) {
      inflight.delete(turn.id)
      ctrl.abort()
    }
  }
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
