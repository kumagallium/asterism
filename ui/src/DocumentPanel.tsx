import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { createDocumentDataset, ingestDataset, type IngestProgress } from './api'
import { promoteDataset } from './galleryApi'

// The "文書を追加" flow (PR-3): a JATS (.xml) or Word (.docx) document needs NO
// schema design (unlike CSV/JSON), so this is a single, self-contained path —
// upload → create (server converts .docx→JATS, auto-attaches the recall tools) →
// ingest (the deterministic structurer, sentence-level) → promote. The document is
// then queryable + citable from the catalog's ツール tab (search_text /
// quote_with_citation). Ingest + promote run here so the friend has one click; both
// remain the same server-side gates the catalog uses.

type Phase = 'idle' | 'creating' | 'ingesting' | 'promoting' | 'done'

export function DocumentPanel() {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ id: string; name: string } | null>(null)
  // Adopt the id minted by the first successful create (mirrors the workbench
  // 'adopted' pattern, PR #241): if create succeeds but the later ingest/promote
  // fails, a retry RESUMES from ingest on this same dataset instead of POSTing
  // /api/documents again — which would mint a fresh slug-uuid8 id and leave a
  // duplicate record. Cleared when the user picks different files (a new dataset).
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null)

  const busy = phase !== 'idle' && phase !== 'done'
  // A retry is pending when a prior attempt created the dataset but did not finish.
  const resuming = created !== null && phase === 'idle'

  function pick(list: FileList | null) {
    const arr = Array.from(list ?? [])
    setFiles(arr)
    if (arr.length && !name.trim()) setName(arr[0].name.replace(/\.(xml|docx|pdf)$/i, ''))
    setError('')
    setResult(null)
    setCreated(null) // new files → a new dataset (do not resume the previous create)
    setPhase('idle')
  }

  async function run() {
    if (!files.length && !created) return
    setError('')
    setProgress(null)
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
      await ingestDataset(target.id, [], (p) => setProgress(p))
      setPhase('promoting')
      await promoteDataset(target.id)
      setResult(target)
      setCreated(null) // published — a further run starts a fresh dataset
      setPhase('done')
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

      {progress && phase === 'ingesting' && (
        <p className="hint">
          {t('document:ingesting')}
          {progress.total ? t('document:ingestProgress', { done: progress.done ?? 0, total: progress.total }) : ''}
        </p>
      )}

      {error && <pre className="error">{error}</pre>}

      {resuming && created && (
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
