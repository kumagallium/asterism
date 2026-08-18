import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { isBundledTool } from './bundledTools'
import { plainError } from './kantan/errorMessages'
import { runTool, type QueryTool, type QueryToolParam, type ToolRunResult } from './toolsApi'

function fmt(v: unknown): string {
  return v == null ? '' : String(v)
}

/** Copy text to the clipboard, with an execCommand fallback for non-secure contexts. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

/** The tool a search hit is quoted with, and the parameter carrying the hit. */
const QUOTE_TOOL = 'quote_with_citation'
const QUOTE_PARAM = 'node'
const SENTENCE_COLUMN = 'sentence_iri'

/** Columns that carry an ID rather than something to read. */
function isIdColumn(name: string): boolean {
  return name.endsWith('_iri')
}

/**
 * The deterministic, KEY-FREE run panel for one saved (human-vetted) tool: a typed
 * form built from its declared parameters, an execute button, the result table,
 * and the exact read-only SPARQL it ran (citable). No API key, no LLM — the server
 * binds the typed args safely and runs the fixed template over the canonical
 * FROM-merge (the same path MCP exposes). Reused by the catalog ツール tab and the
 * Ask view so a researcher's verified tool is runnable wherever they are.
 */
export function ToolRunner({ datasetId, tool }: { datasetId: string; tool: QueryTool }) {
  const { t, i18n } = useTranslation()
  const params = tool.parameters ?? []
  const [args, setArgs] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      params.filter((p) => p.default !== undefined).map((p) => [p.name, String(p.default)]),
    ),
  )
  const [running, setRunning] = useState(false)
  // 必須パラメータ未入力での実行はサーバの生エラーで返るだけ — 手前で無効化する
  const missingRequired = params.filter(
    (p) => p.required && (args[p.name] === undefined || args[p.name] === ''),
  )
  const [result, setResult] = useState<ToolRunResult | null>(null)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState<string | null>(null)
  // The citation pulled for one result row (search_text → quote_with_citation),
  // so nobody has to copy an ID between two tools by hand.
  const [cite, setCite] = useState<{
    key: string
    loading: boolean
    row?: Record<string, unknown>
    err?: string
  } | null>(null)

  const bundled = isBundledTool(tool.name)
  /** A translated string for this bundled tool, or undefined when there is none. */
  function bundledText(rest: string): string | undefined {
    if (!bundled) return undefined
    const key = `tools:bundled.${tool.name}.${rest}`
    return i18n.exists(key) ? t(key) : undefined
  }
  function paramLabel(p: QueryToolParam): string {
    return bundledText(`params.${p.name}.label`) ?? p.name
  }
  function columnLabel(col: string): string {
    return bundledText(`columns.${col}`) ?? col
  }
  /** Column names of the citation tool — the inline citation panel below reads
   *  its rows, not this tool's. */
  function quoteColumnLabel(col: string): string {
    const key = `tools:bundled.${QUOTE_TOOL}.columns.${col}`
    return i18n.exists(key) ? t(key) : col
  }

  async function copyCell(key: string, value: string) {
    if (!value) return
    if (await copyText(value)) {
      setCopied(key)
      setTimeout(() => setCopied((k) => (k === key ? null : k)), 1200)
    }
  }

  async function run() {
    setRunning(true)
    setErr('')
    setResult(null)
    setCite(null)
    try {
      const payload: Record<string, unknown> = {}
      for (const p of params) {
        const v = args[p.name]
        if (v === undefined || v === '') continue // omit → server uses default / errors if required
        payload[p.name] = p.type === 'number' || p.type === 'integer' ? Number(v) : v
      }
      setResult(await runTool(datasetId, tool.name, payload))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  /** Quote one search hit: run the shipped citation tool on the same dataset with
   *  the row's sentence ID. Deterministic, key-free — the same call the reader
   *  would otherwise assemble by copying an identifier between two forms. */
  async function citeRow(key: string, sentenceIri: string) {
    setCite({ key, loading: true })
    try {
      const r = await runTool(datasetId, QUOTE_TOOL, { [QUOTE_PARAM]: sentenceIri })
      setCite({ key, loading: false, row: r.items[0] })
    } catch (e) {
      setCite({ key, loading: false, err: e instanceof Error ? e.message : String(e) })
    }
  }

  const rawCols = result?.items.length
    ? Array.from(new Set(result.items.flatMap((r) => Object.keys(r))))
    : []
  // For the tools we ship, the text to read comes first and the IDs trail behind
  // (they are needed for citation, not for reading). A user's own tool keeps the
  // column order they chose.
  const cols = bundled
    ? [...rawCols.filter((c) => !isIdColumn(c)), ...rawCols.filter(isIdColumn)]
    : rawCols
  const canCite = tool.name === 'search_text' && rawCols.includes(SENTENCE_COLUMN)
  const plain = err ? plainError(err) : null

  return (
    <div className="tool-run">
      <p className="tool-run-hint">
        <Trans i18nKey="tools:runner.hint">
          検証済みツールを<strong>キー不要・LLM 不要</strong>で実行します（型付き・決定論・引用つき）。
        </Trans>
      </p>
      {params.length > 0 && (
        <div className="tool-run-form">
          {params.map((p) => {
            const label = paramLabel(p)
            return (
              <label key={p.name} className="run-field">
                <span className={`run-label${label === p.name ? '' : ' run-label--plain'}`}>
                  {label}
                  {p.required && <span className="run-req">{t('tools:runner.required')}</span>}
                  {/* The declared type only helps whoever authored the tool. */}
                  {label === p.name && <span className="run-type">{p.type}</span>}
                </span>
                {p.type === 'enum' ? (
                  <select
                    className="draft-select"
                    value={args[p.name] ?? ''}
                    onChange={(e) => setArgs((a) => ({ ...a, [p.name]: e.target.value }))}
                  >
                    <option value="">{t('tools:runner.enumUnset')}</option>
                    {(p.enum ?? []).map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="draft-text"
                    type={p.type === 'number' || p.type === 'integer' ? 'number' : 'text'}
                    value={args[p.name] ?? ''}
                    placeholder={
                      bundledText(`params.${p.name}.placeholder`) ||
                      p.description ||
                      (p.default != null
                        ? t('tools:runner.defaultHint', { value: p.default })
                        : '')
                    }
                    onChange={(e) => setArgs((a) => ({ ...a, [p.name]: e.target.value }))}
                  />
                )}
              </label>
            )
          })}
        </div>
      )}
      <button
        type="button"
        className="promote-btn"
        onClick={run}
        disabled={running || missingRequired.length > 0}
        title={
          missingRequired.length > 0
            ? t('tools:runner.missingRequired', {
                names: missingRequired.map((p) => paramLabel(p)).join(', '),
              })
            : undefined
        }
      >
        {running ? (
          <>
            <span className="spinner" />
            {t('tools:runner.running')}
          </>
        ) : (
          t('tools:runner.run')
        )}
      </button>
      {/* A failed run says what happened and what to try; the raw api string
          stays reachable in the folded technical view (K11). */}
      {plain && (
        <div className="tool-run-err">
          <p className="doc-error-head">{t('tools:runner.errHead')}</p>
          <p className="hint">{t('tools:runner.errBody')}</p>
          <p className="hint">{t(plain.body)}</p>
          <details className="tool-sparql-details">
            <summary>{t('tools:runner.techSummary')}</summary>
            <pre className="sparql-block">{err}</pre>
          </details>
        </div>
      )}
      {result && (
        <div className="tool-run-result">
          <p className="hint">
            {result.truncated
              ? t('tools:runner.result.countTruncated', { n: result.count })
              : t('tools:runner.result.count', { n: result.count })}
            {result.count > 0 && (
              <span className="cell-copy-tip"> {t('tools:runner.cellCopyTip')}</span>
            )}
          </p>
          {result.count > 0 ? (
            <div className="table-wrap">
              <table className="jobs-table sparql-table">
                <thead>
                  <tr>
                    {cols.map((c) => (
                      <th key={c} title={columnLabel(c) === c ? undefined : c}>
                        {columnLabel(c)}
                      </th>
                    ))}
                    {canCite && <th />}
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((row, i) => (
                    <tr key={i}>
                      {cols.map((c) => {
                        const val = fmt(row[c])
                        const key = `${i}:${c}`
                        if (!val) return <td key={c} />
                        return (
                          <td key={c}>
                            <button
                              type="button"
                              className={`cell-copy${copied === key ? ' cell-copied' : ''}`}
                              title={t('tools:runner.cellCopyTitle')}
                              onClick={() => copyCell(key, val)}
                            >
                              <span className="sparql-cell">{val}</span>
                              <span className="cell-copy-hint" aria-hidden>
                                {copied === key ? t('tools:runner.cellCopied') : '⧉'}
                              </span>
                            </button>
                          </td>
                        )
                      })}
                      {canCite && (
                        <td>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            disabled={cite?.loading}
                            onClick={() => citeRow(`${i}`, fmt(row[SENTENCE_COLUMN]))}
                          >
                            {cite?.key === `${i}` && cite.loading
                              ? t('tools:runner.citing')
                              : t('tools:runner.cite')}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ds-empty-note">{t('tools:runner.result.empty')}</p>
          )}
          {cite && !cite.loading && (
            <section className="tool-cite card">
              <div className="tool-cite-head">
                <span>{cite.err ? t('tools:runner.citeErrHead') : t('tools:runner.citeHead')}</span>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setCite(null)}
                >
                  {t('tools:runner.citeClose')}
                </button>
              </div>
              {cite.err ? (
                <details className="tool-sparql-details">
                  <summary>{t('tools:runner.techSummary')}</summary>
                  <pre className="sparql-block">{cite.err}</pre>
                </details>
              ) : cite.row ? (
                <>
                  <p className="tool-cite-text">{fmt(cite.row.verbatim)}</p>
                  <dl className="tool-cite-facts">
                    {['paper_title', 'section_title', 'node_iri'].map((c) =>
                      fmt(cite.row?.[c]) ? (
                        <div key={c}>
                          <dt>{quoteColumnLabel(c)}</dt>
                          <dd>{fmt(cite.row?.[c])}</dd>
                        </div>
                      ) : null,
                    )}
                  </dl>
                </>
              ) : (
                <p className="ds-empty-note">{t('tools:runner.result.empty')}</p>
              )}
            </section>
          )}
          <details className="tool-sparql-details">
            <summary>{t('tools:runner.result.sparqlSummary')}</summary>
            <pre className="sparql-block">{result.sparql}</pre>
          </details>
        </div>
      )}
    </div>
  )
}
