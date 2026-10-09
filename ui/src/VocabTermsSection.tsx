import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { VocabTermForm } from './VocabTermForm'
import {
  type CqInput,
  type CqOp,
  type SharedTerm,
  VocabApiError,
  addCq,
  listTerms,
  removeTerm,
} from './vocabApi'
import { cqOpFor, isolatedCount, showIsolatedNotice } from './vocabQuestion'
import { localName } from './vocab'
import './vocabTerms.css'

const RELATIONS = new Set([
  'equivalentClass',
  'subClassOf',
  'equivalentProperty',
  'subPropertyOf',
  'hasQuantityKind',
])

/** 既存の語に問いを 1 本足す小さなフォーム（題と op）。保存は親（onAdd）が api へ。 */
function AddCqForm({
  term,
  onAdd,
  onClose,
}: {
  term: SharedTerm
  onAdd: (cq: CqInput) => Promise<void>
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  // op は語の種類で決まる（種類＝件数・項目＝値。ADR §2.3 の閉じた選択）。選ばせない。
  const op: CqOp = cqOpFor(term.kind)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    if (!title.trim()) return
    setBusy(true)
    setErr('')
    try {
      await onAdd({ title: title.trim(), op })
      onClose()
    } catch (e) {
      if (e instanceof VocabApiError && e.status === 422) setErr(e.detail || e.message)
      else setErr(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div className="vocab-form">
      <div className="vocab-form-row">
        <label className="vocab-field">
          <span>{t('vocab:term.cq_title')}</span>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <span className="vocab-field-hint">
          {t('vocab:term.cq_op')}
          {': '}
          {t(op === 'count' ? 'vocab:term.cq_op_count' : 'vocab:term.cq_op_values')}
        </span>
      </div>
      {err && <p className="promote-err">{err}</p>}
      <div className="vocab-term-actions">
        <button
          type="button"
          className="btn btn--accent btn--sm"
          disabled={busy || !title.trim()}
          onClick={() => void submit()}
        >
          {busy ? t('vocab:term.cq_adding') : t('vocab:term.cq_add_submit')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={onClose}>
          {t('vocab:term.cancel')}
        </button>
      </div>
    </div>
  )
}

/** 共有のことば 1 つ分の行（表示と外すボタンだけ。状態は親が持つ）。 */
export function VocabTermRow({
  term,
  confirming,
  removing,
  onAskRemove,
  onCancelRemove,
  onRemove,
  onAddCq,
}: {
  term: SharedTerm
  confirming: boolean
  removing: boolean
  onAskRemove: () => void
  onCancelRemove: () => void
  onRemove: () => void
  /** あれば「問いを足す」が出る（題と op を送る。失敗は投げる）。 */
  onAddCq?: (cq: CqInput) => Promise<void>
}) {
  const { t } = useTranslation()
  const [addingCq, setAddingCq] = useState(false)
  return (
    <div className="vocab-term" data-slug={term.slug}>
      <div className="vocab-term-head">
        <span className="vocab-term-label">{term.label}</span>
        <span className="vocab-term-slug">{term.slug}</span>
        <span className="vocab-term-kind">{t(`vocab:term.kind_badge.${term.kind}`)}</span>
        {!term.wired && <span className="vocab-term-isolated">{t('vocab:term.isolated')}</span>}
      </div>
      <div className="vocab-term-meta">
        <span>{t('vocab:term.narrower_count', { n: term.narrower.length })}</span>
        <span>{t('vocab:term.answering', { n: term.answering_datasets })}</span>
        {term.standards.map((s) => (
          <span key={`${s.iri}|${s.relation}`} title={s.iri}>
            {t('vocab:term.standard_line', {
              relation: RELATIONS.has(s.relation) ? t(`crosswalk:relation.${s.relation}`) : s.relation,
              name: localName(s.iri),
            })}
          </span>
        ))}
      </div>
      {term.cqs.length === 0 ? (
        <p className="vocab-field-hint">{t('vocab:term.no_cq')}</p>
      ) : (
        <ul className="vocab-term-cqs">
          {term.cqs.map((cq) => (
            <li key={cq.tool_name}>
              {cq.title}
              {' — '}
              {t('vocab:term.answering', { n: cq.answering_datasets })}
            </li>
          ))}
        </ul>
      )}
      {addingCq && onAddCq && (
        <AddCqForm term={term} onAdd={onAddCq} onClose={() => setAddingCq(false)} />
      )}
      <div className="vocab-term-actions">
        {onAddCq && !addingCq && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAddingCq(true)}>
            {t('vocab:term.add_cq')}
          </button>
        )}
        {confirming ? (
          <>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={removing}
              onClick={onRemove}
            >
              {removing ? t('vocab:term.removing') : t('vocab:term.remove_confirm')}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={onCancelRemove}>
              {t('vocab:term.cancel')}
            </button>
          </>
        ) : (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onAskRemove}>
            {t('vocab:term.remove')}
          </button>
        )}
      </div>
    </div>
  )
}

/** 共有のことば（一覧・作る・外す）。ADR upper-structure-shared-terms.md §2.5.1 (4)。 */
export function VocabTermsSection({
  reloadKey = 0,
  onChanged,
}: {
  /** 親の版。語・問い・線がどの節で変わっても、変わったら読み直す。 */
  reloadKey?: number
  /** 語を作った／外した／問いを足した（親が版を進める）。 */
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [terms, setTerms] = useState<SharedTerm[] | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [creating, setCreating] = useState(false)
  const [confirm, setConfirm] = useState('')
  const [removing, setRemoving] = useState('')
  const [actErr, setActErr] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => {
    let off = false
    // 読み込みの結果で状態を決める: 成功なら誤りを消し、失敗なら古い一覧は残さず空にする
    // （effect の中で同期に state は触らない）。
    listTerms()
      .then((ts) => {
        if (off) return
        setLoadErr('')
        setTerms(ts)
      })
      .catch((e) => {
        if (off) return
        setTerms(null)
        setLoadErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      off = true
    }
  }, [reloadKey])

  async function remove(term: SharedTerm) {
    setRemoving(term.slug)
    setActErr('')
    setNote('')
    try {
      await removeTerm(term.slug)
      setConfirm('')
      onChanged()
    } catch (e) {
      if (e instanceof VocabApiError && e.status === 409) setActErr(t('vocab:term.removeBlocked'))
      else setActErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  async function addQuestion(term: SharedTerm, cq: CqInput) {
    setActErr('')
    setNote('')
    await addCq(term.slug, cq)
    setNote(t('vocab:term.cq_added'))
    onChanged()
  }

  return (
    <section className="vocab-sec" id="vocab-terms" aria-labelledby="vocab-terms-h">
      <h2 className="vocab-sec-h" id="vocab-terms-h">
        {t('vocab:sections.terms')}
      </h2>
      {loadErr && <pre className="error">{loadErr}</pre>}
      {terms === null && !loadErr && (
        <p className="loading-row">
          <span className="spinner" />
          {t('vocab:term.list_loading')}
        </p>
      )}
      {terms && showIsolatedNotice(terms) && (
        <p className="vocab-notice" role="status">
          {t('vocab:term.many_isolated', { n: isolatedCount(terms) })}
        </p>
      )}
      {terms && terms.length === 0 && <p className="ds-empty-note">{t('vocab:term.list_empty')}</p>}
      {terms && terms.length > 0 && (
        <div className="vocab-term-list">
          {terms.map((term) => (
            <VocabTermRow
              key={term.slug}
              term={term}
              confirming={confirm === term.slug}
              removing={removing === term.slug}
              onAskRemove={() => {
                setConfirm(term.slug)
                setActErr('')
              }}
              onCancelRemove={() => setConfirm('')}
              onRemove={() => void remove(term)}
              onAddCq={(cq) => addQuestion(term, cq)}
            />
          ))}
        </div>
      )}
      {actErr && <p className="promote-err">{actErr}</p>}
      {note && <p className="lifecycle-ok">{note}</p>}
      {creating ? (
        <VocabTermForm
          onCancel={() => setCreating(false)}
          onMinted={(term) => {
            setCreating(false)
            setNote(t('vocab:term.created', { label: term.label }))
            onChanged()
          }}
        />
      ) : (
        <p>
          <button type="button" className="btn btn--sm" onClick={() => setCreating(true)}>
            {t('vocab:term.open_form')}
          </button>
        </p>
      )}
    </section>
  )
}
