// Generic chat-thread store — the client-held conversation mechanism shared by
// Ask (ADR ask-chat-threads.md) and the design-consult drawer (ADR
// design-consult-chat.md D2). Extracted from what was `askThreads.ts` so both
// namespaces (`ask` / `consult`) get the exact same persistence behavior
// (localStorage <-> single-user server disk, ADR app-data-on-disk.md) with no
// duplicated logic. `askThreads.ts` is now a thin, behavior-preserving wrapper
// around `createThreadStore<AskResponse>(...)` — its public API, storage key,
// and on-disk/localStorage shape are UNCHANGED.
//
// A thread is a list of turns: the user's message, then the assistant's reply
// (of caller-chosen result type `TResult`), and so on. Persistence has a
// store-boundary swap (ADR app-data-on-disk.md D1): the module always loads
// synchronously from `localStorage` on boot (unchanged browser behavior, so
// nothing regresses before the server has answered), then asks the server
// `/api/appdata/info` who it is. If the server declares itself single-user (a
// hand-run `asterism-local`), the in-memory list is replaced by the server's
// copy (or, on first run, the localStorage threads are migrated up — ADR D5)
// and every further mutation is persisted to disk via PUT/DELETE instead of
// localStorage. A shared api (production) keeps the original browser-only
// behavior. Cross-tab changes are picked up via the `storage` event, but only
// in browser mode.
//
// The store is a plain module (not React state) so a thread keeps receiving
// its answer while the user navigates elsewhere; components subscribe through
// useSyncExternalStore.

import { useSyncExternalStore } from 'react'
import {
  deleteAppDataThreadNS,
  fetchAppDataThreadsNS,
  initAppData,
  putAppDataThreadNS,
  type ThreadNamespace,
} from './appdata'

export interface UserTurn {
  id: string
  role: 'user'
  text: string
  at: number
}

export interface AssistantTurn<TResult> {
  id: string
  role: 'assistant'
  at: number
  /** The reply; null while pending, on error, when interrupted, or stopped. */
  result: TResult | null
  /** Waiting for the agent (in-memory only — persisted as `interrupted`). */
  pending?: boolean
  /** Which attempt owns this pending slot (in-memory): a late response from a
   *  stopped/superseded attempt must not overwrite a newer one. */
  attempt?: number
  /** The call failed; the message is shown with a retry action. */
  error?: string
  /** The page unloaded while the answer was pending (retry-able). */
  interrupted?: boolean
  /** The user stopped waiting (retry-able). */
  stopped?: boolean
}

export type Turn<TResult> = UserTurn | AssistantTurn<TResult>

export interface Thread<TResult> {
  id: string
  /** Derived from the first message (deterministic — no LLM titling); the
   *  user can rename it. */
  title: string
  createdAt: number
  updatedAt: number
  turns: Turn<TResult>[]
}

export interface Attempt {
  userTurnId: string
  assistantTurnId: string
  /** Pass back to resolveAnswer / failAnswer so a stale attempt is ignored. */
  attempt: number
}

/** Result of editUserTurn / regenerateFrom — no fresh userTurnId (the user
 *  turn already existed; edit rewrote it in place, regenerate reused it
 *  unchanged), just the new pending assistant slot to settle. */
export interface RegenAttempt {
  assistantTurnId: string
  attempt: number
}

export interface ThreadStoreOptions<TResult> {
  namespace: ThreadNamespace
  /** localStorage key (browser-mode persistence). */
  storageKey: string
  /** Oldest threads fall off past this count. */
  maxThreads?: number
  /** Thread title clip length. */
  titleMax?: number
  /** Server-flush debounce. */
  flushDebounceMs?: number
  /** Parse a persisted (or server-sent) `result` payload back into `TResult`,
   *  or null if it does not look like one (dropped, not thrown). */
  normalizeResult: (raw: unknown) => TResult | null
}

export interface ThreadStore<TResult> {
  useThreads: () => Thread<TResult>[]
  useThreadsLoaded: () => boolean
  getThread: (id: string | null | undefined) => Thread<TResult> | undefined
  /** Every thread, unordered, without subscribing (plain read — for a
   *  one-shot "what's the latest one" computation outside render). */
  getAllThreads: () => Thread<TResult>[]
  titleFrom: (turns: Turn<TResult>[]) => string
  startThread: (message: string) => Attempt & { thread: Thread<TResult> }
  appendMessage: (threadId: string, message: string) => Attempt | null
  resolveAnswer: (
    threadId: string,
    assistantTurnId: string,
    result: TResult,
    attempt?: number,
  ) => boolean
  failAnswer: (
    threadId: string,
    assistantTurnId: string,
    error: string,
    attempt?: number,
  ) => boolean
  registerInflight: (assistantTurnId: string, controller: AbortController) => void
  unregisterInflight: (assistantTurnId: string, controller: AbortController) => void
  stopAnswer: (threadId: string, assistantTurnId: string) => boolean
  retryAnswer: (
    threadId: string,
    assistantTurnId: string,
  ) => { message: string; attempt: number } | null
  /** Rewrite a user turn's text and DROP every turn after it (the edit
   *  discards whatever the conversation became from that point on), then
   *  append a fresh pending assistant slot for the re-send. Null if
   *  `userTurnId` doesn't name a user turn in this thread. */
  editUserTurn: (threadId: string, userTurnId: string, newText: string) => RegenAttempt | null
  /** Drop an assistant turn (and anything after it) and append a fresh
   *  pending slot in its place, so the SAME preceding question can be
   *  re-asked. Null if `assistantTurnId` doesn't name an assistant turn. */
  regenerateFrom: (threadId: string, assistantTurnId: string) => RegenAttempt | null
  renameThread: (threadId: string, title: string) => void
  deleteThread: (threadId: string) => void
  isThreadBusy: (thread: Thread<TResult> | undefined) => boolean
}

function newId(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c?.randomUUID) return c.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * Build one namespace's chat-thread store (Ask or design-consult). Each call
 * owns its own module-level state — the two namespaces never share threads,
 * listeners, or storage keys, only this mechanism.
 */
export function createThreadStore<TResult>(opts: ThreadStoreOptions<TResult>): ThreadStore<TResult> {
  const { namespace, storageKey } = opts
  const MAX_THREADS = opts.maxThreads ?? 60
  const TITLE_MAX = opts.titleMax ?? 48
  const FLUSH_DEBOUNCE_MS = opts.flushDebounceMs ?? 300
  const normalizeResult = opts.normalizeResult

  // ---- state + subscription -------------------------------------------------

  let threads: Thread<TResult>[] = load()
  let loaded = false
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

  function getSnapshot(): Thread<TResult>[] {
    return threads
  }

  function useThreads(): Thread<TResult>[] {
    return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  }

  function getLoadedSnapshot(): boolean {
    return loaded
  }

  function useThreadsLoaded(): boolean {
    return useSyncExternalStore(subscribe, getLoadedSnapshot, getLoadedSnapshot)
  }

  function getThread(id: string | null | undefined): Thread<TResult> | undefined {
    return id ? threads.find((t) => t.id === id) : undefined
  }

  function getAllThreads(): Thread<TResult>[] {
    return threads
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('storage', (e) => {
      if (serverMode || e.key !== storageKey) return
      threads = load()
      emit()
    })
    window.addEventListener('beforeunload', flushServerNow)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') flushServerNow()
    })
    void bootstrap()
  }

  async function bootstrap(): Promise<void> {
    try {
      const info = await initAppData()
      if (!info.singleUser) return
      serverMode = true
      const serverThreads = await fetchAppDataThreadsNS<unknown>(namespace)
      const normalized = serverThreads
        .map(normalizeThread)
        .filter((t): t is Thread<TResult> => t !== null)
      if (normalized.length === 0 && threads.length > 0) {
        const migrated = await migrateToServer(threads)
        if (migrated) {
          try {
            localStorage.removeItem(storageKey)
          } catch {
            /* ignore */
          }
        }
      } else {
        threads = [...normalized].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_THREADS)
      }
    } catch {
      // /api/appdata/info itself falls back internally; stay in whatever mode
      // we reached — memory (from localStorage) keeps working either way.
    } finally {
      loaded = true
      emit()
    }
  }

  async function migrateToServer(localThreads: Thread<TResult>[]): Promise<boolean> {
    try {
      for (const t of localThreads) {
        await putAppDataThreadNS(namespace, serializeThread(t))
      }
      return true
    } catch {
      return false
    }
  }

  // ---- persistence ------------------------------------------------------------

  function load(): Thread<TResult>[] {
    if (typeof localStorage === 'undefined') return []
    try {
      const raw = localStorage.getItem(storageKey)
      if (!raw) return []
      const parsed = JSON.parse(raw) as { v?: number; threads?: unknown }
      if (!Array.isArray(parsed.threads)) return []
      return parsed.threads.map(normalizeThread).filter((t): t is Thread<TResult> => t !== null)
    } catch {
      return []
    }
  }

  function normalizeThread(raw: unknown): Thread<TResult> | null {
    const r = (raw ?? {}) as Record<string, unknown>
    if (typeof r.id !== 'string' || !r.id) return null
    const turns: Turn<TResult>[] = []
    for (const x of Array.isArray(r.turns) ? r.turns : []) {
      const o = (x ?? {}) as Record<string, unknown>
      if (typeof o.id !== 'string') continue
      const at = typeof o.at === 'number' ? o.at : 0
      if (o.role === 'user' && typeof o.text === 'string') {
        turns.push({ id: o.id, role: 'user', text: o.text, at })
      } else if (o.role === 'assistant') {
        const result = normalizeResult(o.result)
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

  function serializeThread(t: Thread<TResult>): Thread<TResult> & { id: string } {
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
      localStorage.setItem(storageKey, JSON.stringify({ v: 1, threads: serializable }))
    } catch {
      try {
        const half = serializable.slice(0, Math.max(1, Math.floor(serializable.length / 2)))
        localStorage.setItem(storageKey, JSON.stringify({ v: 1, threads: half }))
      } catch {
        /* ignore */
      }
    }
  }

  // ---- server persistence (single-user mode) ----------------------------------

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
        await putAppDataThreadNS(namespace, serializeThread(t))
        pendingPuts.delete(id)
      } catch {
        // Left queued; retried on the next mutation/flush.
      }
    }
    for (const id of [...pendingDeletes]) {
      try {
        await deleteAppDataThreadNS(namespace, id)
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

  function commit(next: Thread<TResult>[]) {
    const prevById = new Map(threads.map((t) => [t.id, t]))
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

  function clipTitle(text: string): string {
    const one = text.replace(/\s+/g, ' ').trim()
    return one.length > TITLE_MAX ? `${one.slice(0, TITLE_MAX - 1).trimEnd()}…` : one
  }

  function titleFrom(turns: Turn<TResult>[]): string {
    const first = turns.find((t): t is UserTurn => t.role === 'user')
    return clipTitle(first?.text ?? '')
  }

  function updateThread(
    id: string,
    fn: (t: Thread<TResult>) => Thread<TResult>,
  ): Thread<TResult> | undefined {
    const cur = threads.find((t) => t.id === id)
    if (!cur) return undefined
    const next = fn(cur)
    commit(threads.map((t) => (t.id === id ? next : t)))
    return next
  }

  // ---- mutations ----------------------------------------------------------------

  let attemptSeq = 0

  function pendingSlot(): AssistantTurn<TResult> {
    return {
      id: newId(),
      role: 'assistant',
      at: Date.now(),
      result: null,
      pending: true,
      attempt: ++attemptSeq,
    }
  }

  function startThread(message: string): Attempt & { thread: Thread<TResult> } {
    const now = Date.now()
    const user: UserTurn = { id: newId(), role: 'user', text: message, at: now }
    const assistant = pendingSlot()
    const thread: Thread<TResult> = {
      id: newId(),
      title: clipTitle(message),
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

  function appendMessage(threadId: string, message: string): Attempt | null {
    const now = Date.now()
    const user: UserTurn = { id: newId(), role: 'user', text: message, at: now }
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

  function settleSlot(
    threadId: string,
    assistantTurnId: string,
    attempt: number | undefined,
    make: (slot: AssistantTurn<TResult>, now: number) => AssistantTurn<TResult>,
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

  function resolveAnswer(
    threadId: string,
    assistantTurnId: string,
    result: TResult,
    attempt?: number,
  ): boolean {
    return settleSlot(threadId, assistantTurnId, attempt, (slot, now) => ({
      id: slot.id,
      role: 'assistant',
      at: now,
      result,
    }))
  }

  function failAnswer(
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

  const inflight = new Map<string, AbortController>()

  function registerInflight(assistantTurnId: string, controller: AbortController) {
    inflight.set(assistantTurnId, controller)
  }

  function unregisterInflight(assistantTurnId: string, controller: AbortController) {
    if (inflight.get(assistantTurnId) === controller) inflight.delete(assistantTurnId)
  }

  function stopAnswer(threadId: string, assistantTurnId: string): boolean {
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

  function retryAnswer(
    threadId: string,
    assistantTurnId: string,
  ): { message: string; attempt: number } | null {
    const thread = getThread(threadId)
    if (!thread) return null
    const idx = thread.turns.findIndex((t) => t.id === assistantTurnId)
    const slot = thread.turns[idx]
    const message = thread.turns[idx - 1]
    if (!slot || slot.role !== 'assistant' || slot.pending || slot.result) return null
    if (!message || message.role !== 'user') return null
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
    return { message: message.text, attempt }
  }

  function editUserTurn(threadId: string, userTurnId: string, newText: string): RegenAttempt | null {
    const thread = getThread(threadId)
    if (!thread) return null
    const idx = thread.turns.findIndex((t) => t.id === userTurnId)
    const turn = thread.turns[idx]
    if (idx < 0 || !turn || turn.role !== 'user') return null
    const now = Date.now()
    const editedUser: UserTurn = { id: turn.id, role: 'user', text: newText, at: now }
    const assistant = pendingSlot()
    updateThread(threadId, (t) => ({
      ...t,
      updatedAt: now,
      // Everything up to (excluding) the edited turn, the edit itself, then a
      // fresh pending slot — whatever the conversation became after the
      // ORIGINAL wording of this turn is discarded, same as Graphium's
      // edit-and-resend (onEditResend rewindIndex).
      turns: [...t.turns.slice(0, idx), editedUser, assistant],
    }))
    return { assistantTurnId: assistant.id, attempt: assistant.attempt! }
  }

  function regenerateFrom(threadId: string, assistantTurnId: string): RegenAttempt | null {
    const thread = getThread(threadId)
    if (!thread) return null
    const idx = thread.turns.findIndex((t) => t.id === assistantTurnId)
    const turn = thread.turns[idx]
    if (idx < 0 || !turn || turn.role !== 'assistant') return null
    const now = Date.now()
    const assistant = pendingSlot()
    updateThread(threadId, (t) => ({
      ...t,
      updatedAt: now,
      // Drop this answer (and anything after it) and reuse the SAME
      // preceding question — the caller re-sends with that question as the
      // new last message.
      turns: [...t.turns.slice(0, idx), assistant],
    }))
    return { assistantTurnId: assistant.id, attempt: assistant.attempt! }
  }

  function renameThread(threadId: string, title: string) {
    updateThread(threadId, (t) => ({
      ...t,
      title: clipTitle(title) || titleFrom(t.turns),
    }))
  }

  function deleteThread(threadId: string) {
    const thread = threads.find((t) => t.id === threadId)
    if (!thread) return
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

  function isThreadBusy(thread: Thread<TResult> | undefined): boolean {
    return !!thread?.turns.some((t) => t.role === 'assistant' && t.pending)
  }

  return {
    useThreads,
    useThreadsLoaded,
    getThread,
    getAllThreads,
    titleFrom,
    startThread,
    appendMessage,
    resolveAnswer,
    failAnswer,
    registerInflight,
    unregisterInflight,
    stopAnswer,
    retryAnswer,
    editUserTurn,
    regenerateFrom,
    renameThread,
    deleteThread,
    isThreadBusy,
  }
}
