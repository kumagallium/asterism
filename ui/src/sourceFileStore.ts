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
// with — two tabs never see each other's files), and a TTL sweep so a tab
// closed without clearing does not leave its upload behind forever.
//
// Everything is best-effort: no IndexedDB (private mode, an old WebView) means
// the old behavior — nothing worse than before.

const DB_NAME = 'asterism-source-files'
const STORE = 'sessions'
const SESSION_KEY = 'asterism.sourceFiles.session'
const TTL_MS = 7 * 24 * 60 * 60 * 1000

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

function sessionId(): string | null {
  try {
    let id = sessionStorage.getItem(SESSION_KEY)
    if (!id) {
      const c = globalThis.crypto as Crypto | undefined
      id = c?.randomUUID ? c.randomUUID() : `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
      sessionStorage.setItem(SESSION_KEY, id)
    }
    return id
  } catch {
    return null
  }
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
  const id = sessionId()
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

/** The files this tab saved, rebuilt as File objects (or [] when none). */
export async function loadSourceFiles(): Promise<File[]> {
  const id = sessionId()
  const db = await openDb()
  if (!id || !db) return []
  try {
    const record = await request(db.transaction(STORE, 'readonly').objectStore(STORE).get(id))
    const rec = record as SessionRecord | undefined
    if (!rec?.files?.length) return []
    return rec.files.map(
      (f) => new File([f.blob], f.name, { type: f.type, lastModified: f.lastModified }),
    )
  } catch {
    return []
  } finally {
    db.close()
  }
}

/** Forget this tab's files (a fresh start, a wiped work area). */
export async function clearSourceFiles(): Promise<void> {
  const id = sessionId()
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
