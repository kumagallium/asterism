import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'

import { authHeaders } from './authToken'

// Same workbench API base as the other clients (same-origin /api via the Vite
// proxy by default; VITE_API_URL overrides for separate hosting).
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// データセット非依存の例（旧例は starrydata 専用語彙にハードコードされ、starrydata の
// 無い配備では初回の「実行」が必ず 0 行になった）。クラス別件数はどのストアでも意味を
// 持ち、最初の実行で必ず何かが返る。
const EXAMPLE = `SELECT ?class (COUNT(?s) AS ?n) WHERE {
  ?s a ?class .
}
GROUP BY ?class
ORDER BY DESC(?n)
LIMIT 20`

// タブ切替で書きかけのクエリと結果が EXAMPLE に巻き戻らないよう保持（リロードで消えるのは許容）
let lastSession: { query: string; results: SparqlResults | null } | null = null

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
  const { t } = useTranslation()
  const [query, setQuery] = useState(() => lastSession?.query ?? EXAMPLE)
  const [results, setResults] = useState<SparqlResults | null>(() => lastSession?.results ?? null)
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)

  async function run() {
    // running ガード: Cmd/Ctrl+Enter は disabled ボタンを迂回するので、ここで並行二重送信を防ぐ
    if (running || !query.trim()) return
    setError('')
    setResults(null)
    setRunning(true)
    try {
      const res = await fetch(`${API_BASE}/api/sparql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ query }),
      })
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ''}`)
      }
      const parsed = (await res.json()) as SparqlResults
      setResults(parsed)
      lastSession = { query, results: parsed }
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
        <Trans i18nKey="sparql:subtitle">
          取り込み済みの RDF に<strong>読み取り専用</strong>の SPARQL を直接実行します。
          上級者向けの<strong>直接クエリ</strong>の場です（通常は「質問する」や「データセット」をご利用ください）。
          UPDATE 系（INSERT/DELETE 等）は実行できません。
        </Trans>
      </p>

      <section className="sparql-editor">
        <textarea
          className="sparql-input"
          value={query}
          spellCheck={false}
          rows={10}
          aria-label={t('sparql:editorLabel')}
          onChange={(e) => {
            setQuery(e.target.value)
            lastSession = { query: e.target.value, results }
          }}
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
                {t('sparql:running')}
              </>
            ) : (
              t('sparql:run')
            )}
          </button>
          <button
            className="btn btn--ghost"
            onClick={() => {
              // 手書きした長いクエリを 1 クリックで失わないよう、変更がある時だけ確認
              if (query !== EXAMPLE && !window.confirm(t('sparql:resetConfirm'))) return
              setQuery(EXAMPLE)
              lastSession = { query: EXAMPLE, results }
            }}
          >
            {t('sparql:reset')}
          </button>
        </div>
      </section>

      {error && <pre className="error">{error}</pre>}

      {isAsk && (
        <p className="sparql-bool">
          {t('sparql:boolResult')}
          <strong>{results?.boolean ? 'true' : 'false'}</strong>
        </p>
      )}

      {!isAsk && results && (
        <>
          <p className="hint">{t('sparql:rows', { n: bindings.length })}</p>
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
          {bindings.length === 0 && <p className="hint">{t('sparql:noResults')}</p>}
        </>
      )}
    </>
  )
}
