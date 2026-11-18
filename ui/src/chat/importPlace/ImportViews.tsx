// 取り込みの画面の部品（PR 14d-2b・契約 §2）。状態は持たない（props だけ）ので、
// renderToStaticMarkup でも、関数として呼んで onClick を直に押す形でも検査できる。
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { barPercent, lostCount, questionRows, reasonKey, skipKey, stepsOf, type ImportJobState } from './importJob'
import type { ImportRow } from '../importRows'
import type { ImportNotice as ListNotice } from './importStore'
import type { ImportResult, ImportSource } from './knowledgeImportApi'

type Running = Extract<ImportJobState, { phase: 'running' }>

// ── Graphium とつなぐ ──

export interface GraphiumCardProps {
  /** つながっている（取り込むだけ）か。 */
  connected: boolean
  /** 取り込み中はボタンを止める。 */
  busy: boolean
  /** 起動ファイルと vault を機械で探している間（最初の一瞬）。 */
  searching?: boolean
  /** 失敗の理由 1 行（無ければ null）。 */
  error: string | null
  /** つないでいないとき: 起動ファイルと vault の入力（出すものは showScript／showRoot）。 */
  form: { script: string; root: string } | null
  showScript?: boolean
  showRoot?: boolean
  /** 起動ファイルが見つからなかった（何を入れるかを 1 行で出す）。 */
  scriptMissing?: boolean
  /** 両方見つけた場所（欄を出さずに 1 行で見せる）。 */
  found?: { script: string; root: string } | null
  onEdit?: () => void
  onForm: (patch: { script?: string; root?: string }) => void
  onGo: () => void
  onOpenSettings: () => void
}

export function GraphiumCard(p: GraphiumCardProps) {
  const { t } = useTranslation('importPlace')
  const showScript = p.showScript ?? true
  const showRoot = p.showRoot ?? true
  return (
    <section className="imp-card" data-testid="import-graphium">
      <h2 className="imp-card-title">{t('graphium.title')}</h2>
      <p className="imp-card-body">{t('graphium.body')}</p>
      {!p.connected && p.searching && (
        <p className="imp-note" role="status" data-testid="import-graphium-searching">
          {t('graphium.searching')}
        </p>
      )}
      {!p.connected && !p.searching && p.form && (
        <div className="imp-form">
          {showScript && (
            <label className="imp-field">
              <span>{t('graphium.script')}</span>
              <input value={p.form.script} data-testid="import-graphium-script" placeholder="/path/to/Graphium/graphium-mcp.mjs" onChange={(e) => p.onForm({ script: e.target.value })} />
              {p.scriptMissing && <small data-testid="import-graphium-script-hint">{t('graphium.scriptHint')}</small>}
            </label>
          )}
          {showRoot && (
            <label className="imp-field">
              <span>{t('graphium.root')}</span>
              <input value={p.form.root} data-testid="import-graphium-root" onChange={(e) => p.onForm({ root: e.target.value })} />
              <small>{t('graphium.rootHint')}</small>
            </label>
          )}
        </div>
      )}
      <button type="button" className="btn btn--sm" data-testid="import-graphium-go" disabled={p.busy || (!p.connected && !!p.searching)} onClick={p.onGo}>
        {p.connected ? t('graphium.import') : t('graphium.connect')}
      </button>
      {!p.connected && !p.searching && p.found && (
        <p className="imp-found" data-testid="import-graphium-found">
          {t('graphium.found')}: {t('graphium.foundApp')} <code>{p.found.script}</code>・{t('graphium.foundVault')} <code>{p.found.root}</code>{' '}
          <button type="button" className="imp-link" data-testid="import-graphium-edit" onClick={p.onEdit}>
            {t('graphium.change')}
          </button>
        </p>
      )}
      {p.error && (
        <p className="promote-err" role="alert" data-testid="import-graphium-error">
          {p.error}{' '}
          <button type="button" className="imp-link" data-testid="import-open-settings" onClick={p.onOpenSettings}>
            {t('graphium.openSettings')}
          </button>
        </p>
      )}
    </section>
  )
}

// ── 進み ──

export function ImportProgress({ job, canCancel, aiSet = true, onCancel }: { job: Running; canCancel: boolean; aiSet?: boolean; onCancel: () => void }) {
  const { t } = useTranslation('importPlace')
  const steps = stepsOf(job.stage)
  const pct = barPercent(job)
  // 組が 0 のときは「0 / 0 組」も「AI を使います」も出さない（AI 未設定のときも）
  const noPairs = job.total === 0
  return (
    <div className="imp-prog">
      <div role="status" className="imp-prog-body" data-testid="import-progress">
        <ol className="imp-steps">
          {steps.map((s) => (
            <li key={s.id} className={s.state === 'cur' ? 'is-cur' : ''} data-step={s.id} data-state={s.state} aria-current={s.state === 'cur' ? 'step' : undefined}>
              {s.state === 'done' && <b className="imp-mark">{t('steps.mark')}</b>}
              {s.id === 'pairs' && s.state === 'cur' && job.done !== null && job.total !== null && !noPairs
                ? t('progress.pairsCount', { done: job.done.toLocaleString(), total: job.total.toLocaleString() })
                : t(`steps.${s.id}`)}
            </li>
          ))}
        </ol>
        <div className={`imp-bar${pct === null ? ' is-flow' : ''}`} aria-hidden="true">
          <i style={pct === null ? undefined : { width: `${pct}%` }} data-testid="import-bar" />
        </div>
        {aiSet && !noPairs && <p className="imp-note">{t('progress.note')}</p>}
      </div>
      <button type="button" className="btn btn--sm btn--ghost" data-testid="import-cancel" disabled={!canCancel} onClick={onCancel}>
        {t('progress.cancel')}
      </button>
    </div>
  )
}

export function ImportNotice({ job, onDismiss }: { job: Extract<ImportJobState, { phase: 'failed' | 'cancelled' }>; onDismiss: () => void }) {
  const { t } = useTranslation('importPlace')
  return job.phase === 'failed' ? (
    <p className="promote-err" role="alert" data-testid="import-failed">
      {t('progress.failed', { reason: reasonText(t, job.error) })}{' '}
      <button type="button" className="imp-link" onClick={onDismiss}>
        {t('result.close')}
      </button>
    </p>
  ) : (
    <p className="imp-note" role="status" data-testid="import-cancelled">
      {t('progress.cancelled')}{' '}
      <button type="button" className="imp-link" onClick={onDismiss}>
        {t('result.close')}
      </button>
    </p>
  )
}

/** 知っている英語の理由は文にする（知らなければそのまま）。 */
function reasonText(t: TFunction, reason: string): string {
  const k = reasonKey(reason)
  return k ? t(`reason.${k}`) : reason
}

/** 一覧の上の失敗（外す・取り消し・一覧を読めない）。「設定を開く」は付けない。 */
export function ImportListNotice({ notice, onDismiss }: { notice: ListNotice; onDismiss: () => void }) {
  const { t } = useTranslation('importPlace')
  return (
    <p className="promote-err" role="alert" data-testid="import-list-notice" data-kind={notice.kind}>
      {t(`notice.${notice.kind}`, { reason: reasonText(t, notice.detail) })}{' '}
      <button type="button" className="imp-link" onClick={onDismiss}>
        {t('result.close')}
      </button>
    </p>
  )
}

// ── 結果の 1 枚 ──

/** 結果の 1 枚に「答えられる問い」の節を出すか（未確認の関係を答えに使う 14e-2 が入ったので出す）。 */
export const SHOW_RESULT_QUESTIONS = true

export interface ImportResultProps {
  /** 問いの節を出すか（既定は SHOW_RESULT_QUESTIONS。テストで両方を確かめる用）。 */
  showQuestions?: boolean
  result: ImportResult
  /** 取り込み元の表示名（title が無いときは取り込み元の名前）。 */
  name: string
  lang: 'ja' | 'en'
  onAsk: (q: { source: string; name: string; text: string }) => void
  onGraph: () => void
  onClose: () => void
  onOpenSettings: () => void
}

export function ImportResultCard(p: ImportResultProps) {
  const { t } = useTranslation('importPlace')
  const r = p.result
  const n = (v: number) => v.toLocaleString(p.lang)
  const skip = skipKey(r)
  const lost = lostCount(r)
  const { shown, more } = questionRows(r)
  return (
    <div className="imp-res" role="status" aria-label={t('result.label')} data-testid="import-result">
      <p className="imp-big">{t('result.read', { name: p.name, claims: n(r.copied.claims), topics: n(r.copied.topics) })}</p>
      <p>{t('result.added', { terms: n(r.relations.terms), relations: n(r.relations.shown) })}</p>
      {r.relations.candidates > 0 && <p>{t('result.candidates', { n: n(r.relations.candidates) })}</p>}
      {lost > 0 && <p data-testid="import-lost">{t('result.lost', { n: n(lost) })}</p>}
      {skip && (
        <p className="imp-note" data-testid="import-skip" data-skip={skip}>
          {t(`result.skip.${skip}`)}{' '}
          {skip === 'ai_unset' && (
            <button type="button" className="imp-link" onClick={p.onOpenSettings}>
              {t('graphium.openSettings')}
            </button>
          )}
        </p>
      )}
      {(p.showQuestions ?? SHOW_RESULT_QUESTIONS) && r.questions_total > 0 && (
        <>
          <p className="imp-big">{t('result.questions', { n: n(r.questions_total) })}</p>
          <ul className="imp-qs">
            {shown.map((q, i) => (
              <li key={`${q.term.id}-${q.op}-${i}`}>
                <button type="button" className="imp-qrow" data-testid="import-question" onClick={() => p.onAsk({ source: q.source ?? r.source, name: q.name ?? '', text: q.text })}>
                  <span>{q.text}</span>
                  <span aria-hidden="true">›</span>
                </button>
              </li>
            ))}
          </ul>
          {more > 0 && <p className="imp-note">{t('result.more', { n: n(more) })}</p>}
        </>
      )}
      <div className="imp-res-actions">
        <button type="button" className="btn btn--sm" data-testid="import-see-graph" onClick={p.onGraph}>
          {t('result.seeGraph')}
        </button>
        <button type="button" className="btn btn--sm btn--ghost" data-testid="import-close-result" onClick={p.onClose}>
          {t('result.close')}
        </button>
      </div>
    </div>
  )
}

// ── 取り込んだものの一覧 ──

export interface SourcesProps {
  sources: ImportSource[]
  lang: 'ja' | 'en'
  /** 今ジョブが走っている取り込み元（進みを別に出すので「走っています」は付けない）。 */
  jobSource: string | null
  /** 写し直せる取り込み元の名前（MCP サーバとして登録されているもの）。 */
  rerunnable: string[]
  /** 外す確認を開いている取り込み元。 */
  confirming: string | null
  /** 外す・写し直しの最中は押せない。 */
  busy: boolean
  onRerun: (source: string) => void
  onAskRemove: (source: string | null) => void
  onRemove: (source: string) => void
  /** 写し以外のデータセットの行（同じ表の行にそろえる。押すと知識グラフへ）。 */
  rows?: ImportRow[]
  onOpen?: (datasetId: string) => void
}

export function ImportSources(p: SourcesProps) {
  const { t } = useTranslation('importPlace')
  const n = (v: number) => v.toLocaleString(p.lang)
  const when = (s: string | null) => {
    const d = s ? new Date(s) : null
    return d && !Number.isNaN(d.getTime()) ? d.toLocaleString(p.lang, { dateStyle: 'short', timeStyle: 'short' }) : ''
  }
  return (
    <table className="imp-table" data-testid="import-sources">
      <colgroup>
        <col className="imp-c-name" />
        <col className="imp-c-contents" />
        <col className="imp-c-num" />
        <col className="imp-c-rel" />
        <col className="imp-c-at" />
        <col className="imp-c-act" />
      </colgroup>
      <thead>
        <tr>
          <th>{t('sources.source')}</th>
          <th>{t('sources.contents')}</th>
          <th>{t('sources.terms')}</th>
          <th>{t('sources.relations')}</th>
          <th>{t('sources.at')}</th>
          <th>{t('sources.actions')}</th>
        </tr>
      </thead>
      <tbody>
        {p.sources.map((s) => sourceRow(s, p, t, n, when))}
        {(p.rows ?? []).map((r) => datasetRow(r, p, t, n, when))}
      </tbody>
    </table>
  )
}

/** 写し以外のデータセットの 1 行（取り込み元＝名前・中身＝種類と件数・用語と関係は「—」・日付・操作は空）。 */
function datasetRow(r: ImportRow, p: SourcesProps, t: TFunction, n: (v: number) => string, when: (s: string | null) => string) {
  const dash = '—'
  return (
    <tr key={`ds-${r.id}`} className="imp-ds" data-testid="import-ds-row" onClick={() => p.onOpen?.(r.id)}>
      <td data-label={t('sources.source')} className="imp-name">
        <button type="button" className="imp-ds-open" data-testid="import-dataset-row">
          {r.name}
        </button>
        {r.paper && <span className="imp-tag import-row-tag">{t('sources.paper')}</span>}
        {!r.published && <span className="imp-tag imp-tag--soft">{t('sources.unpublished')}</span>}
      </td>
      <td data-label={t('sources.contents')}>{r.kinds !== null && r.rows !== null ? t('sources.kinds', { kinds: n(r.kinds), rows: n(r.rows) }) : dash}</td>
      <td data-label={t('sources.terms')}>{dash}</td>
      <td data-label={t('sources.relations')}>{dash}</td>
      <td data-label={t('sources.at')}>{when(r.created ? r.created.toISOString() : null)}</td>
      <td data-label={t('sources.actions')} className="imp-act" />
    </tr>
  )
}

/** 1 行（hook を持たない関数。押したときの配線を検査できるように、部品にしない）。 */
function sourceRow(s: ImportSource, p: SourcesProps, t: TFunction, n: (v: number) => string, when: (s: string | null) => string) {
  const confirming = p.confirming === s.source
  const running = s.running !== null && p.jobSource !== s.source
  const row = (
    <tr key={s.source} data-testid="import-source-row" data-source={s.source}>
      <td data-label={t('sources.source')} className="imp-name">
        {s.title || s.source}
        {running && (
          <span className="imp-running" data-testid="import-running">
            {t('sources.running')}
          </span>
        )}
      </td>
      <td data-label={t('sources.contents')}>{t('sources.counts', { claims: n(s.counts.claims), topics: n(s.counts.topics) })}</td>
      <td data-label={t('sources.terms')}>{n(s.relations.terms)}</td>
      <td data-label={t('sources.relations')}>
        <span>
          {n(s.relations.shown)} <span className="imp-un">{t('sources.unconfirmed', { n: n(s.relations.unconfirmed) })}</span>
        </span>
      </td>
      <td data-label={t('sources.at')}>{when(s.copied_at)}</td>
      <td data-label={t('sources.actions')} className="imp-act">
        {!confirming && (
          <>
            {p.rerunnable.includes(s.source) && (
              <button type="button" className="btn btn--sm btn--ghost" data-testid="import-rerun" aria-label={t('sources.rerunNamed', { name: s.title || s.source })} disabled={p.busy || s.running !== null} onClick={() => p.onRerun(s.source)}>
                {t('sources.rerun')}
              </button>
            )}{' '}
            <button type="button" className="btn btn--sm btn--ghost" data-testid="import-remove" aria-label={t('sources.removeNamed', { name: s.title || s.source })} disabled={p.busy || s.running !== null} onClick={() => p.onAskRemove(s.source)}>
              {t('sources.remove')}
            </button>
          </>
        )}
      </td>
    </tr>
  )
  if (!confirming) return row
  return [
    row,
    <tr key={`${s.source}-confirm`} className="imp-confirm-row">
      <td colSpan={6}>
        <div className="imp-confirm" role="group" aria-label={t('sources.confirmLabel', { name: s.title || s.source })} data-testid="import-confirm">
          <p>{t('sources.confirmRemove')}</p>
          <button type="button" className="btn btn--sm btn--ghost" data-testid="import-confirm-remove" disabled={p.busy} onClick={() => p.onRemove(s.source)}>
            {t('sources.remove')}
          </button>{' '}
          <button type="button" className="btn btn--sm btn--ghost" data-testid="import-confirm-cancel" onClick={() => p.onAskRemove(null)}>
            {t('sources.keep')}
          </button>
        </div>
      </td>
    </tr>,
  ]
}
