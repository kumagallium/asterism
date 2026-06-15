import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
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
      <p className="subtitle">{t('jobs:subtitle')}</p>

      {!jobs && (
        <p className="loading-row">
          <span className="spinner" />
          {t('jobs:loading')}
        </p>
      )}

      {jobs && jobs.length === 0 && (
        <div className="empty-state">
          <p className="empty-title">{t('jobs:empty.title')}</p>
          <p className="empty-sub">{t('jobs:empty.sub')}</p>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="table-wrap">
          <table className="jobs-table">
            <thead>
              <tr>
                <th>{t('jobs:col.kind')}</th>
                <th>{t('jobs:col.status')}</th>
                <th>{t('jobs:col.file')}</th>
                <th className="num">{t('jobs:col.rows')}</th>
                <th className="num">{t('jobs:col.triples')}</th>
                <th>{t('jobs:col.endedAt')}</th>
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
                    {j.rows_err > 0 && (
                      <span className="job-err-count">{t('jobs:errCount', { n: j.rows_err })}</span>
                    )}
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
