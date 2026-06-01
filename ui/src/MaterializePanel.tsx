import type { MaterializeResult, TrapResult } from './api'

const STATUS_GLYPH: Record<TrapResult['status'], string> = {
  pass: '✓',
  fail: '✗',
  warn: '⚠',
  skip: '·',
}

function download(filename: string, contents: string) {
  const blob = new Blob([contents], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * Shows the 4 materialized artifacts as download buttons + the 8-trap
 * validation report. CSV-dependent traps (T1/T6) report `skip` here because
 * the materialize endpoint validates the artifact bundle without source CSVs.
 */
export function MaterializePanel({ result }: { result: MaterializeResult }) {
  const artifacts = Object.entries(result.artifacts).filter(([, v]) => v) as [string, string][]
  return (
    <section className="materialize-panel">
      <h3 className="section-h">Artifacts ({artifacts.length}/4)</h3>
      <div className="artifact-list">
        {Object.entries(result.artifacts).map(([name, contents]) => (
          <button
            key={name}
            type="button"
            className="artifact-btn"
            disabled={!contents}
            onClick={() => contents && download(name, contents)}
            title={contents ? `${name} をダウンロード` : `${name} は抽出されませんでした`}
          >
            ⤓ {name}
          </button>
        ))}
      </div>
      {result.warnings.length > 0 && (
        <ul className="materialize-warnings">
          {result.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      <h3 className="section-h">検証 (8 罠)</h3>
      <div className="trap-grid">
        {result.traps.map((t) => (
          <div key={t.id} className={`trap trap-${t.status}`} title={t.detail}>
            <span className="trap-glyph">{STATUS_GLYPH[t.status]}</span>
            <span className="trap-id">{t.id}</span>
            <span className="trap-name">{t.name}</span>
          </div>
        ))}
      </div>
      <p className="trap-summary">
        {result.exit_code === 0 ? (
          <span className="trap-ok">blocking failure なし (exit 0)</span>
        ) : (
          <span className="trap-bad">blocking failure あり (exit {result.exit_code})</span>
        )}
      </p>
    </section>
  )
}
