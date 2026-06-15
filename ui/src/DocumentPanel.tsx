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
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ id: string; name: string } | null>(null)

  const busy = phase !== 'idle' && phase !== 'done'

  function pick(f: File | null) {
    setFile(f)
    if (f && !name.trim()) setName(f.name.replace(/\.(xml|docx)$/i, ''))
    setError('')
    setResult(null)
    setPhase('idle')
  }

  async function run() {
    if (!file) return
    setError('')
    setProgress(null)
    try {
      setPhase('creating')
      const created = await createDocumentDataset(name.trim() || file.name, file)
      const id = created.dataset_id
      setPhase('ingesting')
      await ingestDataset(id, [], (p) => setProgress(p))
      setPhase('promoting')
      await promoteDataset(id)
      setResult({ id, name: created.dataset.name ?? name })
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
            accept=".xml,.docx"
            disabled={busy}
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
        </label>
        <span className={`file-names${file ? '' : ' empty'}`}>{file ? file.name : t('document:noFile')}</span>
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
        <button type="button" onClick={run} disabled={!file || busy}>
          {busy ? (
            <>
              <span className="spinner" />
              {t(`document:phase.${phase as Exclude<Phase, 'idle' | 'done'>}`)}
            </>
          ) : (
            t('document:submit')
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
