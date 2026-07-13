// In-flight ingest job persistence — the ingest analogue of the workbench's
// JOB_STORAGE (propose/refine). A reload/crash/disconnect would otherwise lose
// the job id and with it both cancel and recovery: the server job keeps running
// (and its SSE replay keeps the full history), but nothing on the client could
// find it again. sessionStorage on purpose (tab-scoped, dies with the tab) —
// same lifetime the workbench chose for its job slot.
//
// A single slot, like the workbench: one ingest at a time is the UI reality
// (the controls disable while busy). `datasetId` scopes recovery — a surface
// only resumes a job for the dataset it is showing — and `kind` separates the
// catalog/workbench snapshot ingest from the document panel's create→ingest→
// promote pipeline (which must continue with promote after the ingest lands).

const INGEST_JOB_STORAGE = 'asterism.ingest.job'

export interface SavedIngestJob {
  jobId: string
  datasetId: string
  kind: 'ingest' | 'document'
}

export function saveIngestJob(job: SavedIngestJob): void {
  try {
    sessionStorage.setItem(INGEST_JOB_STORAGE, JSON.stringify(job))
  } catch {
    /* sessionStorage may be unavailable — non-fatal */
  }
}

export function loadIngestJob(): SavedIngestJob | null {
  try {
    const raw = sessionStorage.getItem(INGEST_JOB_STORAGE)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<SavedIngestJob>
    if (typeof parsed.jobId !== 'string' || typeof parsed.datasetId !== 'string') return null
    return {
      jobId: parsed.jobId,
      datasetId: parsed.datasetId,
      kind: parsed.kind === 'document' ? 'document' : 'ingest',
    }
  } catch {
    return null
  }
}

/** Clear the slot — but only if it still holds `jobId` (a concurrent surface
 *  may have saved a newer job; its record must survive this cleanup). */
export function clearIngestJob(jobId: string): void {
  try {
    const current = loadIngestJob()
    if (current && current.jobId !== jobId) return
    sessionStorage.removeItem(INGEST_JOB_STORAGE)
  } catch {
    /* non-fatal */
  }
}
