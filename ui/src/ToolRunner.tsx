import { useState } from 'react'
import { runTool, type QueryTool, type ToolRunResult } from './toolsApi'

function fmt(v: unknown): string {
  return v == null ? '' : String(v)
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
  const params = tool.parameters ?? []
  const [args, setArgs] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      params.filter((p) => p.default !== undefined).map((p) => [p.name, String(p.default)]),
    ),
  )
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ToolRunResult | null>(null)
  const [err, setErr] = useState('')

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
        検証済みツールを<strong>キー不要・LLM 不要</strong>で実行します（型付き・決定論・引用つき）。
      </p>
      {params.length > 0 && (
        <div className="tool-run-form">
          {params.map((p) => (
            <label key={p.name} className="run-field">
              <span className="run-label">
                {p.name}
                {p.required && <span className="run-req">必須</span>}
                <span className="run-type">{p.type}</span>
              </span>
              {p.type === 'enum' ? (
                <select
                  className="draft-select"
                  value={args[p.name] ?? ''}
                  onChange={(e) => setArgs((a) => ({ ...a, [p.name]: e.target.value }))}
                >
                  <option value="">（未指定）</option>
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
                  placeholder={p.description || (p.default != null ? `既定: ${p.default}` : '')}
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
            実行中…
          </>
        ) : (
          '実行（キー不要）'
        )}
      </button>
      {err && <pre className="error">{err}</pre>}
      {result && (
        <div className="tool-run-result">
          <p className="hint">
            {result.count} 件{result.truncated && '（上限で切り詰め）'}
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
                      {cols.map((c) => (
                        <td key={c}>
                          <span className="sparql-cell" title={fmt(row[c])}>
                            {fmt(row[c])}
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ds-empty-note">該当する結果はありません。</p>
          )}
          <details className="tool-sparql-details">
            <summary>実行した SPARQL（読み取り専用）</summary>
            <pre className="sparql-block">{result.sparql}</pre>
          </details>
        </div>
      )}
    </div>
  )
}
