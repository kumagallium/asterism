import { useState } from 'react'
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

const PHASE_LABEL: Record<Exclude<Phase, 'idle' | 'done'>, string> = {
  creating: '文書を登録中…',
  ingesting: '構造化して取り込み中…',
  promoting: '公開中…',
}

export function DocumentPanel() {
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
        論文や契約などの文書（<strong>Word</strong> <code>.docx</code> / 構造化XML{' '}
        <code>.xml</code>）をアップロードすると、<strong>設計（AI）不要</strong>で{' '}
        <strong>節 → 段落 → 文</strong> まで構造化し、カタログの「ツール」タブから{' '}
        <strong>全文検索して引用</strong>できるようになります。
      </p>

      <div className="data-source-row">
        <label className="file-btn">
          文書を選択（Word / XML）
          <input
            type="file"
            accept=".xml,.docx"
            disabled={busy}
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
        </label>
        <span className={`file-names${file ? '' : ' empty'}`}>{file ? file.name : 'ファイル未選択'}</span>
        <label className="fk-field">
          <span>文書名（任意）</span>
          <input
            type="text"
            value={name}
            placeholder="例: 半ホイスラー熱電 / サービス契約"
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      </div>

      <div className="data-source-foot">
        <span className="hint">
          .docx はサーバ側で構造化XMLに自動変換します（変換ツール・版は来歴に記録）。
        </span>
        <button type="button" onClick={run} disabled={!file || busy}>
          {busy ? (
            <>
              <span className="spinner" />
              {PHASE_LABEL[phase as Exclude<Phase, 'idle' | 'done'>]}
            </>
          ) : (
            '文書を追加して公開'
          )}
        </button>
      </div>

      {progress && phase === 'ingesting' && (
        <p className="hint">
          取り込み中…
          {progress.total ? `（${progress.done ?? 0} / ${progress.total}）` : ''}
        </p>
      )}

      {error && <pre className="error">{error}</pre>}

      {phase === 'done' && result && (
        <section className="result">
          <p>
            ✓ <strong>{result.name}</strong> を公開しました。カタログで開き、「ツール」タブの{' '}
            <code>search_text</code> /<code>quote_with_citation</code> で全文を検索して引用できます。
          </p>
        </section>
      )}
    </section>
  )
}
