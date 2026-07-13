import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  createDocumentDataset,
  IngestCancelledError,
  type IngestJobHandle,
  type IngestProgress,
  resumeIngestJob,
  StaleIngestJobError,
  startIngestJob,
} from './api'
import { promoteDataset } from './galleryApi'
import { clearIngestJob, loadIngestJob, saveIngestJob } from './ingestJob'
import { IngestProgressView } from './IngestProgressView'

// The "文書を追加" flow (PR-3): a JATS (.xml) or Word (.docx) document needs NO
// schema design (unlike CSV/JSON), so this is a single, self-contained path —
// upload → create (server converts .docx→JATS, auto-attaches the recall tools) →
// ingest (the deterministic structurer, sentence-level) → promote. The document is
// then queryable + citable from the catalog's ツール tab (search_text /
// quote_with_citation). Ingest + promote run here so the friend has one click; both
// remain the same server-side gates the catalog uses.

type Phase = 'idle' | 'creating' | 'ingesting' | 'promoting' | 'done'

// The adopted-id survives a tab switch / reload so a retry never mints a
// duplicate dataset. Only the id+name is persisted (the picked File objects
// can't be serialized and aren't needed to resume from ingest). Self-contained
// key — DocumentPanel takes no props and owns its own persistence.
const DOC_STORAGE = 'asterism.workbench.document'

function loadCreated(): { id: string; name: string } | null {
  try {
    const raw = sessionStorage.getItem(DOC_STORAGE)
    return raw ? (JSON.parse(raw) as { id: string; name: string }) : null
  } catch {
    return null
  }
}

function persistCreated(v: { id: string; name: string } | null) {
  try {
    if (v) sessionStorage.setItem(DOC_STORAGE, JSON.stringify(v))
    else sessionStorage.removeItem(DOC_STORAGE)
  } catch {
    /* sessionStorage may be unavailable — non-fatal */
  }
}

export function DocumentPanel() {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [error, setError] = useState('')
  const [cancelled, setCancelled] = useState(false)
  const [result, setResult] = useState<{ id: string; name: string } | null>(null)
  const [job, setJob] = useState<IngestJobHandle | null>(null)
  const [lastPulseAt, setLastPulseAt] = useState<number | null>(null)
  // Adopt the id minted by the first successful create (mirrors the workbench
  // 'adopted' pattern, PR #241): if create succeeds but the later ingest/promote
  // fails, a retry RESUMES from ingest on this same dataset instead of POSTing
  // /api/documents again — which would mint a fresh slug-uuid8 id and leave a
  // duplicate record. Cleared when the user picks different files (a new dataset).
  // 復元: タブ切替 / リロードで created が消えると次の実行が create を再 POST して
  // 重複データセットになるため sessionStorage から復元する。
  const [created, setCreatedState] = useState<{ id: string; name: string } | null>(loadCreated)
  const setCreated = (v: { id: string; name: string } | null) => {
    setCreatedState(v)
    persistCreated(v)
  }

  // Reload recovery: an ingest job saved by a prior run of THIS pipeline (the
  // PDF conversion can take minutes) is re-attached, and — because the panel
  // owns the whole create→ingest→promote chain — the tail (promote) still runs.
  // setState lives in the SSE callbacks (not the effect body), matching the
  // workbench resume effect's convention.
  useEffect(() => {
    const saved = loadIngestJob()
    const target = loadCreated()
    if (!saved || saved.kind !== 'document' || !target || saved.datasetId !== target.id) return
    const handle = resumeIngestJob(saved.jobId, target.id, setProgress, () => {
      setLastPulseAt(Date.now())
      // First replayed frame marks the pipeline active again (mount stays pure).
      setPhase((p) => (p === 'idle' ? 'ingesting' : p))
    })
    void finishPipeline(target, handle)
    return () => handle.close() // release the stream; the server job keeps running
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const busy = phase !== 'idle' && phase !== 'done'
  // A retry is pending when a prior attempt created the dataset but did not finish.
  const resuming = created !== null && phase === 'idle'

  function pick(list: FileList | null) {
    const arr = Array.from(list ?? [])
    setFiles(arr)
    if (arr.length && !name.trim()) setName(arr[0].name.replace(/\.(xml|docx|pdf)$/i, ''))
    setError('')
    setCancelled(false)
    setResult(null)
    setCreated(null) // new files → a new dataset (do not resume the previous create)
    setPhase('idle')
  }

  // The ingest→promote tail, shared by the fresh run and the reload recovery.
  async function finishPipeline(target: { id: string; name: string }, handle: IngestJobHandle) {
    saveIngestJob({ jobId: handle.jobId, datasetId: target.id, kind: 'document' })
    setJob(handle)
    setError('')
    setCancelled(false)
    try {
      await handle.result
      setPhase('promoting')
      await promoteDataset(target.id)
      setResult(target)
      setCreated(null) // published — a further run starts a fresh dataset
      setPhase('done')
    } catch (e) {
      if (e instanceof IngestCancelledError) {
        // Clean stop: nothing was committed; `created` is kept so the next run
        // resumes from ingest on the same dataset (no duplicate record).
        setCancelled(true)
        setProgress(null)
      } else if (e instanceof StaleIngestJobError) {
        setProgress(null) // saved job belonged elsewhere — silent reset
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
      setPhase('idle')
    } finally {
      clearIngestJob(handle.jobId)
      setJob(null)
    }
  }

  async function run() {
    if (!files.length && !created) return
    setError('')
    setProgress(null)
    setCancelled(false)
    try {
      // Resume an already-created dataset (a prior attempt got past create); only
      // create when there is none yet — so retry-after-failure is idempotent and
      // never mints a duplicate record.
      let target = created
      if (!target) {
        setPhase('creating')
        const res = await createDocumentDataset(name.trim() || files[0].name, files)
        target = { id: res.dataset_id, name: res.dataset.name ?? name }
        setCreated(target)
      }
      setPhase('ingesting')
      const handle = await startIngestJob(target.id, [], setProgress, () =>
        setLastPulseAt(Date.now()),
      )
      await finishPipeline(target, handle)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('idle')
    }
  }

  return (
    <section className="document-panel">
      <p className="step-hint">
        <Trans
          i18nKey="document:intro"
          components={[<strong key="0" />, <code key="1" />, <code key="2" />, <strong key="3" />, <strong key="4" />, <strong key="5" />]}
        />
      </p>

      <div className="data-source-row">
        <label className="file-btn">
          {t('document:pickFile')}
          <input
            type="file"
            accept=".xml,.docx,.pdf"
            multiple
            disabled={busy}
            onChange={(e) => pick(e.target.files)}
          />
        </label>
        <span className={`file-names${files.length ? '' : ' empty'}`}>
          {files.length === 0
            ? t('document:noFile')
            : files.length === 1
              ? files[0].name
              : t('document:nFiles', { n: files.length })}
        </span>
        <label className="fk-field">
          <span>{t('document:nameLabel')}</span>
          <input
            type="text"
            value={name}
            placeholder={t('document:namePlaceholder')}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      </div>

      <div className="data-source-foot">
        <span className="hint">
          {t('document:convertHint')}
        </span>
        <button type="button" onClick={run} disabled={(!files.length && !created) || busy}>
          {busy ? (
            <>
              <span className="spinner" />
              {t(`document:phase.${phase as Exclude<Phase, 'idle' | 'done'>}`)}
            </>
          ) : (
            t(resuming ? 'document:retrySubmit' : 'document:submit')
          )}
        </button>
      </div>

      {phase === 'ingesting' && (
        <IngestProgressView
          progress={progress}
          onCancel={job ? job.cancel : undefined}
          lastPulseAt={lastPulseAt}
        />
      )}

      {cancelled && <p className="hint">{t('document:cancelled')}</p>}

      {error && <pre className="error">{error}</pre>}

      {resuming && created && !cancelled && (
        <p className="hint">{t('document:retryResumes', { name: created.name })}</p>
      )}

      {phase === 'done' && result && (
        <section className="result">
          <p>
            <Trans
              i18nKey="document:result"
              values={{ name: result.name }}
              components={[<strong key="0" />, <code key="1" />, <code key="2" />]}
            />
          </p>
        </section>
      )}
    </section>
  )
}
