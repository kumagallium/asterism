import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type GroundCandidate, groundTerms } from './groundingApi'
import { type SharedTerm, type SharedTermKind, VocabApiError, mintTerm } from './vocabApi'
import {
  type MintProblem,
  buildMintInput,
  initialCqTitle,
  isValidSlug,
  pickExactStandard,
  suggestSlug,
} from './vocabQuestion'
import './vocabTerms.css'

/**
 * 共有のことばを 1 つ作るフォーム（ADR upper-structure-shared-terms.md §2.1・§2.3）。
 * 「問いを書く入口」と「共有のことば」の節で同じものを使う。
 *
 * 標準の語が先: 英語 label（か列名）で標準語を引き、完全一致があれば「標準の語をそのまま使う」を
 * 既定に出す。使わないと決めたときだけ理由を聞いて、ことばを作る。問いは 1 本以上を人が
 * 確認して、語と一緒に 1 操作で保存する（POST /api/vocab/shared）。
 */
export function VocabTermForm({
  question = '',
  column = '',
  onMinted,
  onCancel,
}: {
  /** 問いが先の入口で書いた題。あれば問いの初期値になる。 */
  question?: string
  /** 列名（ASCII）。英語 label が無いとき標準語の照合に使う。 */
  column?: string
  onMinted: (term: SharedTerm) => void
  onCancel?: () => void
}) {
  const { t } = useTranslation()
  const [labelJa, setLabelJa] = useState('')
  const [labelEn, setLabelEn] = useState('')
  const [kind, setKind] = useState<SharedTermKind>('class')
  const [slugInput, setSlugInput] = useState<string | null>(null) // null = 既定値のまま
  const [comment, setComment] = useState('')
  // null = まだ触っていない（題か既定のテンプレートを見せる）。触ったら実際の文になる。
  const [cqEdits, setCqEdits] = useState<string[] | null>(null)
  const [choice, setChoice] = useState<'use' | 'decline'>('use')
  // 理由は「どの標準の語を退けたか」の鍵つきで持つ。標準の語が変われば前の理由は引き継がない
  // （別の語に対する理由を、そのまま記録に載せないため）。
  const [reasonDraft, setReasonDraft] = useState<{ iri: string; text: string }>({ iri: '', text: '' })
  // 標準語の検索結果は「何を引いたか」の鍵つきで持つ。鍵が違えば未取得（effect で state を戻さない）。
  const [found, setFound] = useState<{ key: string; cand: GroundCandidate | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const lookupText = labelEn.trim() || column.trim()
  const lookupKey = lookupText.length >= 2 ? `${kind}:${lookupText}` : ''
  const searching = !!lookupKey && found?.key !== lookupKey
  const std = lookupKey && found?.key === lookupKey ? found.cand : null

  useEffect(() => {
    if (!lookupKey) return
    let off = false
    const timer = window.setTimeout(() => {
      groundTerms(lookupText, { kind, limit: 5 })
        .then((c) => !off && setFound({ key: lookupKey, cand: pickExactStandard(c) }))
        .catch(() => !off && setFound({ key: lookupKey, cand: null }))
    }, 350)
    return () => {
      off = true
      window.clearTimeout(timer)
    }
  }, [lookupKey, lookupText, kind])

  const slug = slugInput ?? suggestSlug(labelEn, column, std?.name)
  const cqTitles = cqEdits ?? [initialCqTitle(question, kind, labelJa, (k, o) => t(k, o))]
  const usingStandard = !!std && choice === 'use'
  const reason = std && reasonDraft.iri === std.iri ? reasonDraft.text : ''
  // 標準の語を使う分岐では何も保存しない。書きかけの問いがあるなら、そう伝える。
  const hasWrittenQuestion = question.trim() !== '' || !!cqEdits?.some((c) => c.trim() !== '')

  async function submit() {
    setErr('')
    const built = buildMintInput({
      labelJa,
      labelEn,
      slug,
      kind,
      comment,
      cqTitles,
      declined: std ? { iri: std.iri, reason } : null,
    })
    if ('problem' in built) {
      setErr(problemText(built.problem))
      return
    }
    setBusy(true)
    try {
      onMinted(await mintTerm(built.input))
    } catch (e) {
      if (e instanceof VocabApiError && e.status === 409) setErr(t('vocab:term.exists'))
      else if (e instanceof VocabApiError && e.status === 422) setErr(e.detail || e.message)
      else setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function problemText(p: MintProblem): string {
    if (p === 'label') return t('vocab:term.label')
    if (p === 'slug') return t('vocab:term.slug_invalid')
    if (p === 'cq') return t('vocab:term.cqRequired')
    return t('vocab:term.std_reason_required')
  }

  function gotoLines() {
    document.getElementById('vocab-lines')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const slugBad = slug !== '' && !isValidSlug(slug)

  return (
    <div className="vocab-form">
      <div className="vocab-form-row">
        <label className="vocab-field">
          <span>
            {t('vocab:term.label')}
            <span className="vocab-field-req">{t('vocab:term.required')}</span>
          </span>
          <input type="text" value={labelJa} onChange={(e) => setLabelJa(e.target.value)} />
          <span className="vocab-field-hint">{t('vocab:term.label_ja_hint')}</span>
        </label>
        <label className="vocab-field">
          <span>{t('vocab:term.label_en')}</span>
          <input type="text" value={labelEn} onChange={(e) => setLabelEn(e.target.value)} />
        </label>
        <label className="vocab-field" style={{ flex: '0 1 11rem' }}>
          <span>{t('vocab:term.kind')}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as SharedTermKind)}>
            <option value="class">{t('vocab:term.kind_class')}</option>
            <option value="property">{t('vocab:term.kind_property')}</option>
          </select>
        </label>
      </div>

      {/* 標準の語が先（external-standard-alignment.md §2「直接再利用が第一」）。 */}
      {searching && <p className="vocab-field-hint">{t('vocab:term.std_searching')}</p>}
      {std && (
        <div className="vocab-std" role="group" aria-label={t('vocab:term.standard_first')}>
          <div className="vocab-std-head">{t('vocab:term.std_found')}</div>
          <div className="vocab-std-term">
            <strong>{std.label}</strong>
            <code>{std.curie}</code>
            <span className="vocab-field-hint">{std.vocab_title}</span>
            {!labelEn.trim() && column.trim() && (
              <span className="vocab-field-hint">
                {t('vocab:term.std_from_column', { column: column.trim() })}
              </span>
            )}
          </div>
          {usingStandard ? (
            <>
              <p className="vocab-field-hint">{t('vocab:term.std_use_note')}</p>
              <p className="vocab-field-hint">{t('vocab:question.standardNote')}</p>
              {hasWrittenQuestion && (
                <p className="vocab-field-hint">{t('vocab:question.notSaved')}</p>
              )}
              <div className="vocab-std-actions">
                <button type="button" className="btn btn--sm" onClick={gotoLines}>
                  {t('vocab:term.std_goto_lines')}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setChoice('decline')}
                >
                  {t('vocab:term.decline')}
                </button>
              </div>
            </>
          ) : (
            <label className="vocab-field">
              <span>
                {t('vocab:term.decline_reason')}
                <span className="vocab-field-req">{t('vocab:term.required')}</span>
              </span>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReasonDraft({ iri: std.iri, text: e.target.value })}
              />
              <span className="vocab-std-actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setChoice('use')}
                >
                  {t('vocab:term.use_standard')}
                </button>
              </span>
            </label>
          )}
        </div>
      )}

      {!usingStandard && (
        <>
          <div className="vocab-form-row">
            <label className="vocab-field">
              <span>
                {t('vocab:term.slug')}
                <span className="vocab-field-req">{t('vocab:term.required')}</span>
              </span>
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlugInput(e.target.value)}
                aria-invalid={slugBad}
              />
              <span className="vocab-field-hint">{t('vocab:term.slug_hint')}</span>
            </label>
            <label className="vocab-field">
              <span>{t('vocab:term.comment')}</span>
              <input type="text" value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
          </div>

          <div className="vocab-field">
            <span>
              {t('vocab:term.cq')}
              <span className="vocab-field-req">{t('vocab:term.required')}</span>
            </span>
            {cqTitles.map((title, i) => (
              <div className="vocab-cq-row" key={i}>
                <input
                  type="text"
                  aria-label={t('vocab:term.cq_title')}
                  value={title}
                  onChange={(e) => {
                    const next = [...cqTitles]
                    next[i] = e.target.value
                    setCqEdits(next)
                  }}
                />
                {cqTitles.length > 1 && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => setCqEdits(cqTitles.filter((_, j) => j !== i))}
                  >
                    {t('vocab:term.cq_remove')}
                  </button>
                )}
              </div>
            ))}
            <span className="vocab-field-hint">{t('vocab:term.cq_hint')}</span>
            <span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setCqEdits([...cqTitles, ''])}
              >
                {t('vocab:term.add_cq')}
              </button>
            </span>
          </div>

          {err && <p className="promote-err">{err}</p>}
          <div className="vocab-term-actions">
            <button
              type="button"
              className="btn btn--accent btn--sm"
              disabled={busy || searching || !labelJa.trim()}
              onClick={() => void submit()}
            >
              {busy ? t('vocab:term.creating') : t('vocab:term.mint')}
            </button>
            {onCancel && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
                {t('vocab:term.cancel')}
              </button>
            )}
          </div>
        </>
      )}
      {usingStandard && onCancel && (
        <div className="vocab-term-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            {t('vocab:term.cancel')}
          </button>
        </div>
      )}
    </div>
  )
}
