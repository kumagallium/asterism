import { useState } from 'react'

// Same workbench API base as the other clients (same-origin /api via the Vite
// proxy by default; VITE_API_URL overrides for separate hosting).
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

const EXAMPLE = `PREFIX sd: <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
SELECT ?sample ?comp WHERE {
  ?sample a sd:Sample ;
          sd:compositionString ?comp .
  FILTER(CONTAINS(LCASE(STR(?comp)), "bi2te3"))
}
LIMIT 20`

interface SparqlBinding {
  [key: string]: { type: string; value: string } | undefined
}
interface SparqlResults {
  head?: { vars?: string[] }
  results?: { bindings?: SparqlBinding[] }
  boolean?: boolean
}

/**
 * M3 — read-only SPARQL editor. Deliberately NOT the main surface: an advanced
 * escape hatch (ADR §5) for power users who want to query the ingested RDF
 * directly. Relays to the read-only POST /api/sparql; update forms are rejected.
 */
export function SparqlView() {
  const [query, setQuery] = useState(EXAMPLE)
  const [results, setResults] = useState<SparqlResults | null>(null)
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)

  async function run() {
    if (!query.trim()) return
    setError('')
    setResults(null)
    setRunning(true)
    try {
      const res = await fetch(`${API_BASE}/api/sparql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ''}`)
      }
      setResults((await res.json()) as SparqlResults)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  const vars = results?.head?.vars ?? []
  const bindings = results?.results?.bindings ?? []
  const isAsk = results != null && typeof results.boolean === 'boolean'

  return (
    <>
      <p className="subtitle">
        取り込み済みの RDF に<strong>読み取り専用</strong>の SPARQL を直接実行します。
        これは上級者向けの<strong>脱出ハッチ</strong>です（通常は Ask / Gallery をご利用ください）。
        UPDATE 系（INSERT/DELETE 等）は実行できません。
      </p>

      <section className="sparql-editor">
        <textarea
          className="sparql-input"
          value={query}
          spellCheck={false}
          rows={10}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              run()
            }
          }}
        />
        <div className="sparql-actions">
          <button onClick={run} disabled={running || !query.trim()}>
            {running ? (
              <>
                <span className="spinner" />
                実行中…
              </>
            ) : (
              '実行 (Ctrl+Enter)'
            )}
          </button>
          <button className="secondary-btn" onClick={() => setQuery(EXAMPLE)}>
            例に戻す
          </button>
        </div>
      </section>

      {error && <pre className="error">{error}</pre>}

      {isAsk && (
        <p className="sparql-bool">
          結果: <strong>{results?.boolean ? 'true' : 'false'}</strong>
        </p>
      )}

      {!isAsk && results && (
        <>
          <p className="hint">{bindings.length} 行</p>
          <div className="table-wrap">
            <table className="jobs-table sparql-table">
              <thead>
                <tr>
                  {vars.map((v) => (
                    <th key={v}>{v}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bindings.map((b, i) => (
                  <tr key={i}>
                    {vars.map((v) => (
                      <td key={v}>
                        <span className="sparql-cell" title={b[v]?.value}>
                          {b[v]?.value ?? ''}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {bindings.length === 0 && <p className="hint">該当する結果はありません。</p>}
        </>
      )}
    </>
  )
}
