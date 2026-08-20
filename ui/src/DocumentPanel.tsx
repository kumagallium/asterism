import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  createDocumentDataset,
  IngestCancelledError,
  type IngestJobHandle,
  type IngestProgress,
  resumeIngestJob,
  StaleIngestJobError,
  startIngestJob,
} from './api'
import { prefillAskQuestion } from './askPrefill'
import { promoteDataset } from './galleryApi'
import { clearIngestJob, loadIngestJob, saveIngestJob } from './ingestJob'
import { IngestProgressView } from './IngestProgressView'
import { plainError } from './kantan/errorMessages'

// The "文書を追加" flow (PR-3): a JATS (.xml) or Word (.docx) document needs NO
// schema design (unlike CSV/JSON), so this is a single, self-contained path —
// upload → create (server converts .docx→JATS, auto-attaches the recall tools) →
// ingest (the deterministic structurer, sentence-level) → the human publish gate.
// The document is then queryable + citable from the catalog's ツール tab and from
// 質問する. Publishing is NEVER automatic: K10 requires the same confirmation for
// every mode, so ingest stops at a 'confirm' phase and only the explicit
// 「公開する」 button calls promote (the tabular path does the same at S8).

type Phase = 'idle' | 'creating' | 'ingesting' | 'confirm' | 'promoting' | 'done'

// The adopted-id survives a tab switch / reload so a retry never mints a
// duplicate dataset. Only the id+name is persisted (the picked File objects
// can't be serialized and aren't needed to resume from ingest). Self-contained
// key — DocumentPanel takes no props and owns its own persistence.
const DOC_STORAGE = 'asterism.workbench.document'

/** A document file name without its extension — the default dataset name. */
function stemOf(filename?: string): string {
  return (filename ?? '').replace(/\.(xml|docx|pdf)$/i, '')
}

/** Identity of a file set, for "did the caller hand us something else?". */
function fileKey(list?: File[]): string {
  return (list ?? []).map((f) => `${f.name}:${f.size}`).join('|')
}

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

/** `plain` は かんたん層（S1）からの埋め込み用。説明文・完了文だけを平易な言い方に
 *  切り替える（機能は同じ）。詳細モードは既定の false のまま。
 *
 *  `initialFiles` = 呼び出し側が既に受け取っているファイル。かんたん S1 は
 *  「置いたら、あとは自動で進みます」と言うので、同じファイルをこの中でもう一度
 *  選ばせてはいけない（GAL-B-27 / KZ-A-31）。詳細モードは渡さない＝現状のまま。 */
export function DocumentPanel({
  plain = false,
  initialFiles,
}: { plain?: boolean; initialFiles?: File[] } = {}) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>(initialFiles ?? [])
  const [name, setName] = useState(() => stemOf(initialFiles?.[0]?.name))
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

  // The caller's files are adopted whenever the SET changes, so dropping a
  // different document upstream replaces the selection here instead of leaving
  // the first one behind. Keyed by name+size so a re-render with an equal array
  // is a no-op (the parent rebuilds the array on every render).
  const adoptedRef = useRef<string>(fileKey(initialFiles))
  useEffect(() => {
    const key = fileKey(initialFiles)
    if (key === adoptedRef.current) return
    adoptedRef.current = key
    if (!initialFiles || initialFiles.length === 0) return
    pick(initialFiles)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialFiles])

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
  }, [])

  // `busy` = a server call is running. 'confirm' is NOT busy — it is the human
  // gate, where nothing runs until the user decides.
  const busy = phase === 'creating' || phase === 'ingesting' || phase === 'promoting'
  // The publish gate owns the screen from the moment ingest finishes until the
  // dataset is published (including while the promote call is in flight).
  const awaitingPublish = phase === 'confirm' || phase === 'promoting'
  // A retry is pending when a prior attempt created the dataset but did not finish.
  const resuming = created !== null && phase === 'idle'

  function pick(list: FileList | File[] | null) {
    const arr = Array.from(list ?? [])
    setFiles(arr)
    if (arr.length && !name.trim()) setName(stemOf(arr[0].name))
    setError('')
    setCancelled(false)
    setResult(null)
    setCreated(null) // new files → a new dataset (do not resume the previous create)
    setPhase('idle')
  }

  // Publish — the explicit human gate (K10). Only this call promotes; nothing in
  // the ingest path does it implicitly.
  async function publish() {
    if (!created) return
    const target = created
    setError('')
    setPhase('promoting')
    try {
      await promoteDataset(target.id)
      setResult(target)
      setCreated(null) // published — a further run starts a fresh dataset
      setPhase('done')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('confirm') // stay at the gate so 公開する can be pressed again
    }
  }

  // The ingest tail, shared by the fresh run and the reload recovery. It stops at
  // the publish gate — it never publishes on its own.
  async function finishPipeline(target: { id: string; name: string }, handle: IngestJobHandle) {
    saveIngestJob({ jobId: handle.jobId, datasetId: target.id, kind: 'document' })
    setJob(handle)
    setError('')
    setCancelled(false)
    try {
      await handle.result
      setPhase('confirm')
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

  // Jump straight into 質問する with the question pre-typed (never auto-sent) —
  // the same one-shot handoff the kantan S9 chips use. `location.hash` is the
  // app's single source of truth for the route, and setting it fires the
  // hashchange the shell listens to, so this panel stays prop-free.
  function openAsk(name: string) {
    prefillAskQuestion(t('document:tryAskQuestion', { name }))
    window.location.hash = '#/ask'
  }

  return (
    <section className="document-panel">
      <p className="step-hint">{t(plain ? 'document:introPlain' : 'document:intro')}</p>

      <div className="data-source-row">
        <label className="file-btn">
          {/* Already holding the caller's file: the picker is a change of mind,
              not the way in — say so instead of "文書を選択" (GAL-B-27). */}
          {t(files.length > 0 && initialFiles ? 'document:pickAnother' : 'document:pickFile')}
          <input
            type="file"
            accept=".xml,.docx,.pdf"
            multiple
            disabled={busy || awaitingPublish}
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
            disabled={busy || awaitingPublish}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      </div>

      {!awaitingPublish && (
        <div className="data-source-foot">
          <span className="hint">
            {t(plain ? 'document:convertHintPlain' : 'document:convertHint')}
          </span>
          <button type="button" onClick={run} disabled={(!files.length && !created) || busy}>
            {busy ? (
              <>
                <span className="spinner" />
                {t(`document:phase.${phase as 'creating' | 'ingesting'}`)}
              </>
            ) : (
              t(resuming ? 'document:retrySubmit' : 'document:submit')
            )}
          </button>
        </div>
      )}

      {phase === 'ingesting' && (
        <IngestProgressView
          progress={progress}
          onCancel={job ? job.cancel : undefined}
          lastPulseAt={lastPulseAt}
        />
      )}

      {cancelled && <p className="hint">{t('document:cancelled')}</p>}

      {/* A failure says what happened, what is special about documents, and how
          to get out of it. The raw api string stays in the folded view (K11). */}
      {error && (
        <div className="doc-error">
          <p className="doc-error-head">
            {t(awaitingPublish ? 'document:publishErrHead' : 'document:errHead')}
          </p>
          <p className="hint">{t(plainError(error).body)}</p>
          {/* Only reading can hit the scanned-PDF wall — a failed publish cannot. */}
          {!awaitingPublish && <p className="hint">{t('document:errScanned')}</p>}
          {/* At the publish gate the card below already carries 公開する — one
              way out, not two. */}
          {!awaitingPublish && (
            <div className="doc-error-actions">
              <button type="button" onClick={run} disabled={busy}>
                {t('document:errRetry')}
              </button>
            </div>
          )}
          <details className="tool-sparql-details">
            <summary>{t('document:techSummary')}</summary>
            <pre className="sparql-block">{error}</pre>
          </details>
        </div>
      )}

      {resuming && created && !cancelled && (
        <p className="hint">{t('document:retryResumes', { name: created.name })}</p>
      )}

      {/* The publish gate (K10): the same promise the tabular path makes at S8 —
          nothing is public until this button is pressed, and it can be undone. */}
      {awaitingPublish && created && (
        <section className="doc-confirm card">
          <p className="doc-confirm-head">{t('document:confirm.head')}</p>
          <dl className="doc-confirm-facts">
            <dt>{t('document:confirm.nameLabel')}</dt>
            <dd>{created.name}</dd>
            {files.length > 0 && (
              <>
                <dt>{t('document:confirm.filesLabel')}</dt>
                <dd>{t('document:confirm.files', { n: files.length })}</dd>
              </>
            )}
          </dl>
          <p className="hint">{t('document:confirm.body')}</p>
          <p className="hint">{t('document:confirm.promise')}</p>
          <button type="button" onClick={publish} disabled={busy}>
            {phase === 'promoting' ? (
              <>
                <span className="spinner" />
                {t('document:confirm.publishing')}
              </>
            ) : (
              t('document:confirm.publish')
            )}
          </button>
        </section>
      )}

      {phase === 'done' && result && (
        <section className="result">
          <p>{t(plain ? 'document:resultPlain' : 'document:result', { name: result.name })}</p>
          <div className="doc-done-actions">
            <button type="button" onClick={() => openAsk(result.name)}>
              {t('document:tryAsk')}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                window.location.hash = `#/datasets/${encodeURIComponent(result.id)}`
              }}
            >
              {t('document:openDataset')}
            </button>
          </div>
        </section>
      )}
    </section>
  )
}
