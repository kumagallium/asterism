// The design wizard's source files, kept across a reload.
//
// Both tiers snapshot their state into sessionStorage, but a File cannot go
// there — so a reload used to land the user back on "drop the files again",
// with the skeleton gate half-alive: the re-check silently skipped, the
// one-click "add the row kind" did nothing, "ask the AI again" vanished, and
// re-ingest demanded the same files back (real dogfood, 2026-08-14). None of
// that was a design choice; it was the storage's limitation leaking into UX.
//
// IndexedDB stores Blobs. One record per TAB SESSION (an id minted into
// sessionStorage, so it dies with the tab exactly like the snapshot it belongs
// with), and a TTL sweep so a tab closed without clearing does not leave its
// upload behind forever.
//
// The id is NOT unique on its own: "Duplicate tab", opening a link in a new tab
// and the browser's session restore all COPY sessionStorage, so two live tabs
// can hold the same id — and then one record, so "start over" in one tab wipes
// the other's files and both write the same draft. `sessionId()` settles that
// over a BroadcastChannel before any read or write: whoever claims an id that is
// already taken re-mints its own, leaving the original tab untouched.
//
// Everything is best-effort: no IndexedDB (private mode, an old WebView) means
// no files come back. That case is reported (`reason: 'unavailable'`) rather
// than left to look like a bug — the sessionStorage snapshot has no TTL and
// survives, so the wizard can otherwise resume empty-handed with no explanation.

const DB_NAME = 'asterism-source-files'
const STORE = 'sessions'
const SESSION_KEY = 'asterism.sourceFiles.session'
const TAB_CHANNEL = 'asterism.kantan.tab'
const TTL_MS = 7 * 24 * 60 * 60 * 1000
// How long to wait for another tab to answer "that id is mine". A local
// BroadcastChannel round-trip is sub-millisecond; this is slack, not a poll.
const CLAIM_WAIT_MS = 150

interface StoredFile {
  name: string
  type: string
  lastModified: number
  blob: Blob
}

interface SessionRecord {
  id: string
  savedAt: number
  files: StoredFile[]
}

function mintId(): string {
  const c = globalThis.crypto as Crypto | undefined
  return c?.randomUUID
    ? c.randomUUID()
    : `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}

function storedId(): string | null {
  try {
    let id = sessionStorage.getItem(SESSION_KEY)
    if (!id) {
      id = mintId()
      sessionStorage.setItem(SESSION_KEY, id)
    }
    return id
  } catch {
    return null
  }
}

interface TabMessage {
  type: 'claim' | 'taken'
  /** The session id being claimed / already taken. */
  id: string
  /** Who is speaking. In memory only, so a duplicated tab gets a fresh one even
   *  though it copied the session id — that is what makes the two tabs
   *  distinguishable at all. */
  from: string
}

/** This page load's identity on the tab channel (never persisted — see above). */
const TAB_NONCE = mintId()

let claim: Promise<string | null> | null = null
// Kept alive for the tab's lifetime: this is how the ORIGINAL tab answers a
// copy made later. Dropping the reference would let it be collected mid-session.
const openChannels: BroadcastChannel[] = []

/**
 * This tab's session id, once it is known to belong to this tab alone.
 *
 * A duplicated tab starts out holding the original's id (sessionStorage is
 * copied). It announces the id; whoever already owns it answers "taken"; the
 * copy re-mints and, from then on, reads and writes its own record. The other
 * tab's files are never deleted or overwritten — the copy simply arrives
 * empty-handed, which `loadSourceFilesWithReason` reports so the wizard can ask
 * for the files again.
 *
 * Exactly one tab must end up owning the id, including when several copies load
 * at the same moment and none of them is "the original" yet. So a tab answers a
 * claim when it already owns the id, or — before anyone owns it — when its own
 * nonce sorts first. That elects a single owner in one round: the lowest nonce
 * answers everyone and is answered by no one, and every tab that hears "taken"
 * yields. Without the ordering, simultaneous copies would either all yield (the
 * files orphaned) or all keep the id (one record, shared).
 */
function sessionId(): Promise<string | null> {
  if (claim) return claim
  claim = new Promise<string | null>((resolve) => {
    const id = storedId()
    if (!id || typeof BroadcastChannel === 'undefined') return resolve(id)
    let ch: BroadcastChannel
    try {
      ch = new BroadcastChannel(TAB_CHANNEL)
    } catch {
      return resolve(id)
    }
    openChannels.push(ch)
    const post = (msg: TabMessage) => ch.postMessage(msg)
    let current = id
    let settled = false
    ch.onmessage = (ev: MessageEvent<TabMessage>) => {
      const msg = ev.data
      if (!msg?.id || !msg.from || msg.from === TAB_NONCE || msg.id !== current) return
      if (msg.type === 'claim') {
        // Answer only if the id is ours: we already own it, or we are the tab
        // that wins the tie while ownership is still being decided.
        if (settled || TAB_NONCE < msg.from) post({ type: 'taken', id: current, from: TAB_NONCE })
      } else if (msg.type === 'taken' && !settled) {
        // Another tab owns this id — take a fresh one and leave its record be.
        settled = true
        current = mintId()
        try {
          sessionStorage.setItem(SESSION_KEY, current)
        } catch {
          /* best-effort */
        }
        resolve(current)
      }
    }
    post({ type: 'claim', id, from: TAB_NONCE })
    setTimeout(() => {
      if (settled) return
      settled = true
      resolve(current)
    }, CLAIM_WAIT_MS)
  })
  return claim
}

function openDb(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') return resolve(null)
    let req: IDBOpenDBRequest
    try {
      req = indexedDB.open(DB_NAME, 1)
    } catch {
      return resolve(null)
    }
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' }).createIndex('savedAt', 'savedAt')
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => resolve(null)
    req.onblocked = () => resolve(null)
  })
}

function request<T>(r: IDBRequest<T>): Promise<T | undefined> {
  return new Promise((resolve) => {
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => resolve(undefined)
  })
}

/** Remember the files this tab is designing from. Replaces the previous set. */
export async function saveSourceFiles(files: File[]): Promise<void> {
  const id = await sessionId()
  const db = await openDb()
  if (!id || !db) return
  try {
    const tx = db.transaction(STORE, 'readwrite')
    const store = tx.objectStore(STORE)
    const record: SessionRecord = {
      id,
      savedAt: Date.now(),
      files: files.map((f) => ({
        name: f.name,
        type: f.type,
        lastModified: f.lastModified,
        blob: f, // a File is a Blob; structured clone keeps the bytes
      })),
    }
    store.put(record)
    // Sweep: records older than the TTL belong to tabs long gone.
    const cutoff = Date.now() - TTL_MS
    const idx = store.index('savedAt')
    idx.openCursor(IDBKeyRange.upperBound(cutoff)).onsuccess = (ev) => {
      const cursor = (ev.target as IDBRequest<IDBCursorWithValue | null>).result
      if (cursor) {
        cursor.delete()
        cursor.continue()
      }
    }
    await new Promise<void>((resolve) => {
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
      tx.onabort = () => resolve()
    })
  } catch {
    /* best-effort */
  } finally {
    db.close()
  }
}

/** Why a resume came back without the files it was designing from.
 *  - `ok`         the files are here
 *  - `empty`      this browser can keep files, but none were kept for this tab
 *                 (nothing saved yet, swept by the TTL, or a duplicated tab that
 *                 had to re-mint its id)
 *  - `unavailable` this browser cannot keep files at all (private window, an old
 *                 WebView) — asking the person to "drop them again" is pointless
 *                 advice, so the wizard says so plainly instead. */
export type SourceFilesReason = 'ok' | 'empty' | 'unavailable'

export interface SourceFilesResult {
  files: File[]
  reason: SourceFilesReason
}

/**
 * The files this tab saved, rebuilt as File objects — with WHY when there are
 * none. The sessionStorage snapshot has no TTL and survives what IndexedDB does
 * not, so a resume can legitimately land on S4-S9 empty-handed; without a reason
 * to show, that looks like the app losing the work.
 */
export async function loadSourceFilesWithReason(): Promise<SourceFilesResult> {
  const id = await sessionId()
  const db = await openDb()
  if (!id || !db) return { files: [], reason: 'unavailable' }
  try {
    const record = await request(db.transaction(STORE, 'readonly').objectStore(STORE).get(id))
    const rec = record as SessionRecord | undefined
    if (!rec?.files?.length) return { files: [], reason: 'empty' }
    return {
      files: rec.files.map(
        (f) => new File([f.blob], f.name, { type: f.type, lastModified: f.lastModified }),
      ),
      reason: 'ok',
    }
  } catch {
    return { files: [], reason: 'empty' }
  } finally {
    db.close()
  }
}

/** The files this tab saved (or [] when none). Callers that want to explain an
 *  empty result should use `loadSourceFilesWithReason`. */
export async function loadSourceFiles(): Promise<File[]> {
  return (await loadSourceFilesWithReason()).files
}

/** Forget this tab's files (a fresh start, a wiped work area). */
export async function clearSourceFiles(): Promise<void> {
  const id = await sessionId()
  const db = await openDb()
  if (!id || !db) return
  try {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).delete(id)
    await new Promise<void>((resolve) => {
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
      tx.onabort = () => resolve()
    })
  } catch {
    /* best-effort */
  } finally {
    db.close()
  }
}
