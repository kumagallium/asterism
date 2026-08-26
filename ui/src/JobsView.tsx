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

// 失敗・一部失敗のときだけ、記録そのものに色を敷く。1 行ずつ読ませる面では、
// 「どれが問題だったか」を目で拾えることが状態の文字より先に来る（K23）。
function cardClass(status: string): string {
  return status === 'ok' ? 'job-card' : `job-card job-card--${status === 'partial' ? 'warn' : 'bad'}`
}

// "data/sources/csv/papers/foo.csv" → "foo.csv" (the row is already keyed by kind).
function baseName(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

/**
 * Navigate without threading a callback down from App: the hash IS the router's
 * single source of truth (App re-reads it on `hashchange`), so assigning it is a
 * complete navigation.
 */
function goTo(hash: string): void {
  if (window.location.hash !== hash) window.location.hash = hash
}

function fmtTime(iso: string): string {
  if (!iso) return '—'
  // バックエンドの timestamp は UTC。従来はタイムゾーン接尾辞を削って生表示して
  // いたため、JST では全ジョブが 9 時間前に見えた。閲覧者のローカル時刻へ変換する。
  const d = new Date(iso)
  if (Number.isNaN(d.getTime()))
    return iso.replace('T', ' ').replace(/\.\d+/, '').replace(/[+Z].*$/, '')
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * M2 — ingest history. A read-only table of GET /jobs (the watcher's
 * jobs.jsonl). Distinct from the workbench catalog: this is "what was ingested
 * into Oxigraph and when", not "what designs were materialized".
 */
export function JobsView() {
  const { t } = useTranslation()
  const [jobs, setJobs] = useState<IngestJob[] | null>(null)
  const [error, setError] = useState('')
  // Bumped by the retry button; the fetch effect re-runs on every change.
  const [reloadKey, setReloadKey] = useState(0)

  // 「もう一度読み込む」: 表示を読み込み中に戻してから取得を再実行する
  // （state のリセットは effect の外で行う — effect 内の同期 setState は禁止）。
  function reload() {
    setJobs(null)
    setError('')
    setReloadKey((k) => k + 1)
  }

  useEffect(() => {
    let cancelled = false
    getJobs()
      .then((j) => {
        if (!cancelled) setJobs(j)
      })
      .catch((e) => {
        // 障害を空状態と混同させない（getJobs は失敗時 throw する）
        if (!cancelled) {
          setJobs([])
          setError(e instanceof Error ? e.message : String(e))
        }
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  // kind/status はサーバ由来の英語識別子。既知のものだけ訳し、未知の値は生値の
  // ままにする（将来 kind が増えても空欄にしない = 停止カードと同じ fail-open）。
  const kindLabel = (kind: string) => t(`jobs:kind.${kind}`, { defaultValue: kind })
  const statusLabel = (status: string) => t(`jobs:status.${status}`, { defaultValue: status })

  return (
    <>
      <p className="subtitle">{t('jobs:subtitle')}</p>

      {!jobs && !error && (
        <p className="loading-row">
          <span className="spinner" />
          {t('jobs:loading')}
        </p>
      )}

      {error && (
        <div className="card">
          <p className="empty-title">{t('jobs:loadFailed')}</p>
          <p>
            <button type="button" className="btn btn--sm" onClick={reload}>
              {t('jobs:reload')}
            </button>
          </p>
          {/* 生の文言は畳んで温存する（詳細層の逃がし先）。 */}
          <details className="kz-stop-detail">
            <summary>{t('jobs:detailSummary')}</summary>
            <pre className="error">{error}</pre>
          </details>
        </div>
      )}

      {jobs && jobs.length === 0 && !error && (
        <div className="empty-state">
          <p className="empty-title">{t('jobs:empty.title')}</p>
          <p className="empty-sub">{t('jobs:empty.sub')}</p>
          <p>
            <button type="button" className="btn btn--sm" onClick={() => goTo('#/workbench')}>
              {t('jobs:empty.cta')}
            </button>
          </p>
        </div>
      )}

      {/* K23: 1 行 1 事実のカードにする。6 列の表は「どれが失敗したか」を
          横に読ませ、状態は 2 列目の色付き文字でしか言っていなかった。ここは
          読むだけの面なので、状態を先頭のピルにして、1 件の顛末を 3 行
          （何が・どれだけ入って・いつ）で言い切る。 */}
      {jobs && jobs.length > 0 && (
        <div className="job-cards">
          {jobs.map((j, i) => (
            <div className={cardClass(j.status)} key={`${j.csv_path}-${j.ended_at}-${i}`}>
              <div className="job-card-head">
                <span className={`job-status ${statusClass(j.status)}`}>
                  {statusLabel(j.status)}
                </span>
                <code className="job-file" title={j.csv_path}>
                  {baseName(j.csv_path)}
                </code>
                <span className="job-kind">{kindLabel(j.kind)}</span>
              </div>
              {/* 「何件入ったか」は、この記録を見に来る唯一の理由。行と事実を
                  1 文にする（ingest/append は行数を持たないので、そのときは
                  0/0 でなく事実の数だけを言う）。 */}
              <p className="job-card-fact">
                {j.rows_in > 0
                  ? t('jobs:card.rowsAndFacts', {
                      ok: j.rows_ok.toLocaleString(),
                      total: j.rows_in.toLocaleString(),
                      facts: j.triples_out.toLocaleString(),
                    })
                  : t('jobs:card.factsOnly', { facts: j.triples_out.toLocaleString() })}
                {j.rows_err > 0 && (
                  <span className="job-err-count">{t('jobs:errCount', { n: j.rows_err })}</span>
                )}
              </p>
              <p className="job-card-meta">{fmtTime(j.ended_at)}</p>
              {j.error && (
                <details className="kz-fold">
                  <summary>{t('jobs:detailSummary')}</summary>
                  <pre className="error">{j.error}</pre>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  )
}
