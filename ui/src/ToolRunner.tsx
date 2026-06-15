import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { runTool, type QueryTool, type ToolRunResult } from './toolsApi'

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

/**
 * The deterministic, KEY-FREE run panel for one saved (human-vetted) tool: a typed
 * form built from its declared parameters, an execute button, the result table,
 * and the exact read-only SPARQL it ran (citable). No API key, no LLM — the server
 * binds the typed args safely and runs the fixed template over the canonical
 * FROM-merge (the same path MCP exposes). Reused by the catalog ツール tab and the
 * Ask view so a researcher's verified tool is runnable wherever they are.
 */
export function ToolRunner({ datasetId, tool }: { datasetId: string; tool: QueryTool }) {
  const { t } = useTranslation()
  const params = tool.parameters ?? []
  const [args, setArgs] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      params.filter((p) => p.default !== undefined).map((p) => [p.name, String(p.default)]),
    ),
  )
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ToolRunResult | null>(null)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState<string | null>(null)

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

  const cols = result?.items.length
    ? Array.from(new Set(result.items.flatMap((r) => Object.keys(r))))
    : []

  return (
    <div className="tool-run">
      <p className="tool-run-hint">
        <Trans i18nKey="tools:runner.hint">
          検証済みツールを<strong>キー不要・LLM 不要</strong>で実行します（型付き・決定論・引用つき）。
        </Trans>
      </p>
      {params.length > 0 && (
        <div className="tool-run-form">
          {params.map((p) => (
            <label key={p.name} className="run-field">
              <span className="run-label">
                {p.name}
                {p.required && <span className="run-req">{t('tools:runner.required')}</span>}
                <span className="run-type">{p.type}</span>
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
                    p.description ||
                    (p.default != null ? t('tools:runner.defaultHint', { value: p.default }) : '')
                  }
                  onChange={(e) => setArgs((a) => ({ ...a, [p.name]: e.target.value }))}
                />
              )}
            </label>
          ))}
        </div>
      )}
      <button type="button" className="promote-btn" onClick={run} disabled={running}>
        {running ? (
          <>
            <span className="spinner" />
            {t('tools:runner.running')}
          </>
        ) : (
          t('tools:runner.run')
        )}
      </button>
      {err && <pre className="error">{err}</pre>}
      {result && (
        <div className="tool-run-result">
          <p className="hint">
            {result.truncated
              ? t('tools:runner.result.countTruncated', { n: result.count })
              : t('tools:runner.result.count', { n: result.count })}
            {result.count > 0 && (
              <span className="cell-copy-tip">
                {' '}
                <Trans i18nKey="tools:runner.cellCopyTip" components={[<code />, <code />]} />
              </span>
            )}
          </p>
          {result.count > 0 ? (
            <div className="table-wrap">
              <table className="jobs-table sparql-table">
                <thead>
                  <tr>
                    {cols.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ds-empty-note">{t('tools:runner.result.empty')}</p>
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
