// 関係の入力欄（R3c・契約 §1）。足す・直すで同じ部品。値は文字として描く（HTML として解釈しない）。
// 標準の述語は、既存の検索の口（`groundTerms`・kind=property）で探して選ぶか、IRI を貼る。既定は畳む。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { groundTerms, type GroundCandidate } from '../../groundingApi'
import { DEFINITION_MAX, LABEL_MAX, problemsOf, type FormProblem, type RelationFormValues } from './relationFormModel'

export interface RelationFormProps {
  values: RelationFormValues
  onChange: (v: RelationFormValues) => void
  /** 直すとき: 向き・項目に限るは読むだけ（変えられない理由を 1 行）。 */
  editing?: boolean
  /** 種類から開いたとき、読むだけで並べる例（AI の理由）。 */
  examples?: string[]
  pending: boolean
  error: string | null
  submitLabel: string
  onSubmit: () => void
  onCancel: () => void
  testPrefix: string
}

const problemKey: Record<FormProblem, string> = {
  labelEmpty: 'form.problem.labelEmpty',
  labelLong: 'form.problem.labelLong',
  definitionEmpty: 'form.problem.definitionEmpty',
  definitionLong: 'form.problem.definitionLong',
  exactBad: 'form.problem.exactBad',
}

function ExactMatch({ value, onPick, id }: { value: string; onPick: (iri: string) => void; id: string }) {
  const { t } = useTranslation('graphPlace')
  const [open, setOpen] = useState(value !== '')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<GroundCandidate[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const search = () => {
    if (!q.trim()) return
    setBusy(true)
    setFailed(false)
    groundTerms(q.trim(), { kind: 'property', limit: 5 }).then(
      (r) => { setHits(r); setBusy(false) },
      () => { setHits(null); setFailed(true); setBusy(false) },
    )
  }
  if (!open) {
    return <button type="button" className="btn btn--sm btn--ghost gp-exact-open" data-testid={`${id}-exact-open`} onClick={() => setOpen(true)}>{t('form.exactOpen')}</button>
  }
  return (
    <div className="gp-exact" data-testid={`${id}-exact`}>
      <label className="gp-sel">
        {t('form.exact')}
        <input type="text" inputMode="url" value={value} placeholder="https://…" data-testid={`${id}-exact-iri`} onChange={(e) => onPick(e.target.value)} />
      </label>
      <p className="gp-ev">{t('form.exactNote')}</p>
      <div className="gp-acts">
        <input type="text" value={q} aria-label={t('form.exactSearch')} placeholder={t('form.exactSearch')} data-testid={`${id}-exact-q`} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); search() } }} />
        <button type="button" className="btn btn--sm btn--ghost" disabled={busy || !q.trim()} data-testid={`${id}-exact-search`} onClick={search}>{t('form.exactFind')}</button>
      </div>
      {failed && <p className="gp-err" role="alert">{t('form.exactFailed')}</p>}
      {hits && hits.length === 0 && <p className="gp-ev">{t('form.exactNone')}</p>}
      {hits && hits.length > 0 && (
        <ul className="gp-evl gp-hits" data-testid={`${id}-exact-hits`}>
          {hits.map((h) => (
            <li key={h.iri}>
              <button type="button" className="gp-hit" onClick={() => { onPick(h.iri); setHits(null) }}>
                <span>{h.label || h.iri}</span>
                <span className="gp-ev">{h.iri}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function RelationForm({ values, onChange, editing = false, examples = [], pending, error, submitLabel, onSubmit, onCancel, testPrefix: id }: RelationFormProps) {
  const { t } = useTranslation('graphPlace')
  const problems = problemsOf(values)
  const set = (patch: Partial<RelationFormValues>) => onChange({ ...values, ...patch })
  return (
    <div className="gp-rform" data-testid={id}>
      <label className="gp-sel">
        {t('form.label')}
        <input type="text" value={values.label} maxLength={LABEL_MAX * 2} data-testid={`${id}-label`} onChange={(e) => set({ label: e.target.value })} />
      </label>
      <label className="gp-sel">
        {t('form.definition')}
        <textarea rows={2} value={values.definition} data-testid={`${id}-definition`} onChange={(e) => set({ definition: e.target.value })} />
      </label>
      <p className="gp-ev">{t('form.definitionNote')}</p>
      {examples.length > 0 && (
        <div data-testid={`${id}-examples`}>
          <p className="gp-lbl">{t('form.examples')}</p>
          <ul className="gp-evl">
            {examples.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      <label className="gp-chk">
        <input type="checkbox" checked={values.symmetric} disabled={editing} data-testid={`${id}-symmetric`} onChange={(e) => set({ symmetric: e.target.checked })} />
        <span>
          {t('form.symmetric')}
          <span className="gp-ev"> {t('form.symmetricNote')}</span>
        </span>
      </label>
      <label className="gp-chk">
        <input type="checkbox" checked={values.toPropertyOnly} disabled={editing} data-testid={`${id}-toprop`} onChange={(e) => set({ toPropertyOnly: e.target.checked })} />
        <span>
          {t('form.toProperty')}
          <span className="gp-ev"> {t('form.toPropertyNote')}</span>
        </span>
      </label>
      {editing && <p className="gp-ev" data-testid={`${id}-fixed`}>{t('form.fixed')}</p>}
      <ExactMatch value={values.exactMatch} onPick={(iri) => set({ exactMatch: iri })} id={id} />
      {problems.length > 0 && (values.label !== '' || values.definition !== '' || values.exactMatch !== '') && (
        <ul className="gp-problems" data-testid={`${id}-problems`}>
          {problems.filter((p) => !(p === 'labelEmpty' && values.label === '') && !(p === 'definitionEmpty' && values.definition === '')).map((p) => (
            <li key={p} className="gp-ev">{t(problemKey[p], { label: LABEL_MAX, definition: DEFINITION_MAX })}</li>
          ))}
        </ul>
      )}
      <div className="gp-acts">
        <button type="button" className="btn btn--sm btn--accent" data-testid={`${id}-submit`} disabled={pending || problems.length > 0} onClick={onSubmit}>{submitLabel}</button>
        <button type="button" className="btn btn--sm btn--ghost" data-testid={`${id}-cancel`} onClick={onCancel}>{t('act.close')}</button>
      </div>
      {error && <p className="gp-err" role="alert" data-testid={`${id}-error`}>{error}</p>}
    </div>
  )
}
