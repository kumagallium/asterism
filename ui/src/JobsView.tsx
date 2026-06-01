import { useEffect, useState } from 'react'
import { getJobs, type IngestJob } from './jobsApi'

// Status → semantic color (mirrors the validation-trap palette).
function statusClass(status: string): string {
  switch (status) {
    case 'ok':
      return 'job-status--ok'
    case 'partial':
      return 'job-status--warn'
    case 'error':
      return 'job-status--error'
    default:
      return 'job-status--muted'
  }
}

// "data/sources/csv/papers/foo.csv" → "foo.csv" (the row is already keyed by kind).
function baseName(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

function fmtTime(iso: string): string {
  if (!iso) return '—'
  // Keep it compact: drop the timezone/microseconds tail.
  return iso.replace('T', ' ').replace(/\.\d+/, '').replace(/[+Z].*$/, '')
}

/**
 * M2 — ingest history. A read-only table of GET /jobs (the watcher's
 * jobs.jsonl). Distinct from the workbench catalog: this is "what was ingested
 * into Oxigraph and when", not "what designs were materialized".
 */
export function JobsView() {
  const [jobs, setJobs] = useState<IngestJob[] | null>(null)

  useEffect(() => {
    let cancelled = false
    getJobs().then((j) => {
      if (!cancelled) setJobs(j)
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <p className="subtitle">
        取り込み（ingest）の履歴です。CSV が監視ディレクトリに入るたびに 1 行記録されます。
        ワークベンチの設計カタログ（Gallery）とは別物 — こちらは「いつ何が Oxigraph に
        取り込まれたか」を示します。
      </p>

      {!jobs && (
        <p className="trace-loading">
          <span className="spinner" />
          履歴を読み込み中…
        </p>
      )}

      {jobs && jobs.length === 0 && (
        <div className="empty-state">
          <p className="empty-title">取り込み履歴はまだありません</p>
          <p className="empty-sub">
            取り込みパイプライン（watcher）が動いて CSV を処理すると、ここに記録が並びます。
          </p>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="table-wrap">
          <table className="jobs-table">
            <thead>
              <tr>
                <th>種別</th>
                <th>状態</th>
                <th>ファイル</th>
                <th className="num">行 (ok/総数)</th>
                <th className="num">triples</th>
                <th>取り込み時刻</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j, i) => (
                <tr key={`${j.csv_path}-${j.ended_at}-${i}`}>
                  <td>
                    <span className="job-kind">{j.kind}</span>
                  </td>
                  <td>
                    <span className={`job-status ${statusClass(j.status)}`}>{j.status}</span>
                    {j.error && (
                      <span className="job-error" title={j.error}>
                        {j.error}
                      </span>
                    )}
                  </td>
                  <td>
                    <code className="job-file" title={j.csv_path}>
                      {baseName(j.csv_path)}
                    </code>
                  </td>
                  <td className="num">
                    {j.rows_ok}/{j.rows_in}
                    {j.rows_err > 0 && <span className="job-err-count"> (err {j.rows_err})</span>}
                  </td>
                  <td className="num">{j.triples_out.toLocaleString()}</td>
                  <td className="job-time">{fmtTime(j.ended_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
