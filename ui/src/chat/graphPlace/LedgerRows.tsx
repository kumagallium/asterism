// 右の一覧の行（関係・用語・候補）。見た目だけ持ち、開閉・送信は親（GraphPlace）が持つ（押したときの配線は関数として直に呼んで検査できる）。
import { useTranslation } from 'react-i18next'
import { draftRelationLabel } from '../map/mapModel'
import { relSentence } from '../sentenceRows'
import type { LedgerRow, ReviewAction } from './knowledgeRelationsApi'
import type { TermSummary } from './ledgerView'
import { isSymmetricRel, rowId, rowText, ASSIGN_RELS, type AssignForm } from './rowFns'

type Lang = 'ja' | 'en'

interface RowBase {
  open: boolean
  onToggle: () => void
  /** 書き込み中（押せなくする）。 */
  pending: boolean
  error: string | null
}

export function RelationRow({ row, lang, open, onToggle, onAct, pending, error }: RowBase & { row: LedgerRow; lang: Lang; onAct: (action: ReviewAction) => void }) {
  const { t } = useTranslation('graphPlace')
  const status = row.rejected ? 'rejected' : row.confirmed ? 'confirmed' : 'unconfirmed'
  const canAct = !!row.key
  return (
    <li className={`gp-item${open ? ' open' : ''}`} data-testid="gp-relation-row" data-status={status}>
      <button type="button" className="gp-item-h" aria-expanded={open} data-testid="gp-row-toggle" onClick={onToggle}>
        <span className={`gp-st${row.confirmed && !row.rejected ? ' gp-st--ok' : ''}`}>{t(`row.${status}`)}</span>
        <span className="gp-sent">{rowText(row, lang)}</span>
        <span className="gp-meta">
          {row.context.length > 0 && <span className="gp-ev">{t('row.about', { names: row.context.map((c) => c.name).join('、') })}</span>}
          {row.tentative && <em className="gp-tag">{t('row.tentative')}</em>}
          <span className="gp-ev">{t('row.evidence', { n: row.evidence.length })}</span>
        </span>
        <span className="gp-chev" aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="gp-item-b">
          {row.evidence.length > 0 && (
            <>
              <p className="gp-lbl">{t('row.evidenceLabel')}</p>
              <ul className="gp-evl">
                {row.evidence.map((e) => (
                  <li key={e.id}>
                    {e.title || t('row.untitled')}
                    {e.sourceUrl && (
                      <>
                        {' '}
                        <a href={e.sourceUrl} target="_blank" rel="noreferrer" data-testid="gp-evidence-link">{t('row.openSource')}</a>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {(row.note || row.reason) && row.reason !== 'rejected' && (
            <>
              <p className="gp-lbl">{t('row.reason')}</p>
              <p className="gp-ev">{row.note ?? row.reason}</p>
            </>
          )}
          {row.by === 'user' && <p className="gp-ev">{t('row.byUser')}</p>}
          {!canAct && <p className="gp-ev">{t('row.noKey')}</p>}
          {canAct && (
            <div className="gp-acts">
              {row.rejected ? (
                <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-act-restore" disabled={pending} onClick={() => onAct('restore')}>{t('act.restore')}</button>
              ) : (
                <>
                  {row.confirmed ? (
                    <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-act-unconfirm" disabled={pending} onClick={() => onAct('unconfirm')}>{t('act.unconfirm')}</button>
                  ) : (
                    <button type="button" className="btn btn--sm btn--accent" data-testid="gp-act-confirm" disabled={pending} onClick={() => onAct('confirm')}>{t('act.confirm')}</button>
                  )}
                  <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-act-reject" disabled={pending} onClick={() => onAct('reject')}>{t('act.reject')}</button>
                </>
              )}
            </div>
          )}
          {error && <p className="gp-err" role="alert">{error}</p>}
        </div>
      )}
    </li>
  )
}

export function TermRow({ term, lang, open, onToggle, onConfirmAll, pending, error }: RowBase & { term: TermSummary; lang: Lang; onConfirmAll: () => void }) {
  const { t } = useTranslation('graphPlace')
  const roleKey = term.tone === 'p' ? 'property' : term.role === 'material' || term.role === 'method' ? term.role : 'concept'
  return (
    <li className={`gp-item${open ? ' open' : ''}`} data-testid="gp-term-row" data-term={term.id}>
      <button type="button" className="gp-item-h" aria-expanded={open} data-testid="gp-row-toggle" onClick={onToggle}>
        <span className="gp-sent">{term.name}</span>
        <span className="gp-meta">
          <span className="gp-role"><i className={`gp-dot gp-tone--${term.tone}`} aria-hidden="true" />{t(`role.${roleKey}`)}</span>
          <span className="gp-ev">{t('term.relations', { n: term.relations })}</span>
          <span className="gp-ev">{t('term.unconfirmed', { n: term.unconfirmed })}</span>
        </span>
        <span className="gp-chev" aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="gp-item-b">
          <p className="gp-lbl">{t('term.relationsOf')}</p>
          <ul className="gp-evl">
            {term.rows.map((r) => (
              <li key={rowId(r)}>{rowText(r, lang)}</li>
            ))}
          </ul>
          {term.unconfirmed > 0 && (
            <div className="gp-acts">
              <button type="button" className="btn btn--sm btn--accent" data-testid="gp-term-confirm" disabled={pending} onClick={onConfirmAll}>{t('term.confirmAll')}</button>
            </div>
          )}
          {error && <p className="gp-err" role="alert">{error}</p>}
        </div>
      )}
    </li>
  )
}

export function CandidateRow({
  row,
  lang,
  open,
  form,
  onOpen,
  onForm,
  onSubmit,
  onCancel,
  pending,
  error,
}: {
  row: LedgerRow
  lang: Lang
  /** 「関係を付ける」の欄を開いているか。 */
  open: boolean
  form: AssignForm
  onOpen: () => void
  onForm: (f: AssignForm) => void
  onSubmit: () => void
  onCancel: () => void
  pending: boolean
  error: string | null
}) {
  const { t } = useTranslation('graphPlace')
  const [a, b] = [row.from.name, row.to.name]
  const preview = form.rel ? (form.subject === 'a' || isSymmetricRel(form.rel) ? relSentence(form.rel, a, b, lang) : relSentence(form.rel, b, a, lang)) : ''
  return (
    <li className="gp-item" data-testid="gp-candidate-row">
      <div className="gp-flat">
        <span className="gp-sent">
          {a} ― {b}
          <span className="gp-ev">（{t('candidates.pairEvidence', { n: row.evidence.length })}）</span>
        </span>
        {row.key && !open && (
          <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-assign-open" onClick={onOpen}>{t('act.assign')}</button>
        )}
      </div>
      {open && row.key && (
        <div className="gp-assign">
          <label className="gp-sel">
            {t('assign.rel')}
            <select data-testid="gp-assign-rel" value={form.rel} onChange={(e) => onForm({ ...form, rel: e.target.value })}>
              <option value="">{t('assign.pick')}</option>
              {ASSIGN_RELS.map((r) => (
                <option key={r} value={r}>{draftRelationLabel(r, lang)}</option>
              ))}
            </select>
          </label>
          {form.rel && !isSymmetricRel(form.rel) && (
            <label className="gp-sel">
              {t('assign.subject')}
              <select data-testid="gp-assign-subject" value={form.subject} onChange={(e) => onForm({ ...form, subject: e.target.value === 'b' ? 'b' : 'a' })}>
                <option value="a">{a}</option>
                <option value="b">{b}</option>
              </select>
            </label>
          )}
          {preview && <p className="gp-ev">{t('assign.preview')} {preview}</p>}
          <div className="gp-acts">
            <button type="button" className="btn btn--sm btn--accent" data-testid="gp-assign-submit" disabled={pending || !form.rel} onClick={onSubmit}>{t('act.assignDo')}</button>
            <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-assign-cancel" onClick={onCancel}>{t('act.cancel')}</button>
          </div>
          {error && <p className="gp-err" role="alert">{error}</p>}
        </div>
      )}
    </li>
  )
}
