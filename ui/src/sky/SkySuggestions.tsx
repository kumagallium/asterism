import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { applySuggestions, dismissSuggestions, suggestionKey, type Suggestion, type SuggestionKey } from './suggestApi'
import { refusedText, suggestBatchId } from './suggestModel'

// 右の欄の「結べそうな線が N 件 →」を押すと出る一覧（契約 pr7c B2）。
// 公開したデータの種類・項目の名前が、用語の名前と**完全に一致**したものを、機械が数えて置いてある
// （AI は呼んでいない）。人は「N 件をまとめて確定」か「1 件ずつ」で決める。確定は来歴つきの線
// （drafted_by=machine・まとめの ID・理由）。星図には同じ線を薄く重ねてある。

export function SkySuggestions({
  suggestions,
  onClose,
  onApplied,
  onDismissed,
}: {
  suggestions: readonly Suggestion[]
  onClose: () => void
  /** 線にした（星図を取り直す・一覧から外す）。 */
  onApplied: (keys: SuggestionKey[]) => void
  /** 外した（一覧から外す）。 */
  onDismissed: (keys: SuggestionKey[]) => void
}) {
  const { t } = useTranslation('sky')
  const [per, setPer] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  // 閉じた規則で断られたもの（循環・種類違い・形違い）の 1 行。黙って消さない
  const [refused, setRefused] = useState('')

  const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

  async function apply(items: Suggestion[]) {
    if (items.length === 0 || busy) return
    setBusy(true)
    setErr('')
    setNote('')
    setRefused('')
    try {
      const res = await applySuggestions(items, suggestBatchId())
      const gone = new Set(res.dropped.map((d) => d.index))
      const done = items.filter((_, i) => !gone.has(i))
      if (done.length > 0) onApplied(done)
      // 候補に無かった（もう決まっている）ものも一覧から外す — 古い一覧を残さない
      const stale = items.filter((_, i) => gone.has(i))
      if (stale.length > 0) onDismissed(stale)
      setNote(t('suggest.done', { n: res.applied }))
      const why = refusedText(res.dropped, t)
      if (why) setRefused(why)
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  async function dismiss(items: Suggestion[]) {
    if (items.length === 0 || busy) return
    setBusy(true)
    setErr('')
    setNote('')
    try {
      await dismissSuggestions(items)
      onDismissed(items)
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="sky-card sky-suggest" data-testid="suggestions" aria-live="polite">
      <h3>{t('suggest.title')}</h3>
      <p className="sky-hint">{t('suggest.lead')}</p>
      {suggestions.length === 0 ? (
        <p className="sky-meta" data-testid="suggestions-empty">
          {note || t('suggest.empty')}
        </p>
      ) : (
        <ul className="sky-suggest-list">
          {suggestions.map((s) => (
            <li key={suggestionKey(s)} className="sky-suggest-row" data-testid="suggestion-row">
              <span className="sky-suggest-text">
                {t('suggest.sentence', { subject: s.subject_label, term: s.term_label })}
              </span>
              <span className="sky-meta">{s.dataset_label}</span>
              {(per || suggestions.length === 1) && (
                <span className="sky-suggest-ticks">
                  <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void apply([s])}>
                    {t('suggest.one')}
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void dismiss([s])}>
                    {t('suggest.no')}
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {err && (
        <p className="promote-err" role="alert">
          {err}
        </p>
      )}
      {note && suggestions.length > 0 && <p className="sky-meta">{note}</p>}
      {refused && (
        <p className="sky-meta" role="status" data-testid="suggestions-refused">
          {refused}
        </p>
      )}
      <div className="sky-about-actions">
        {suggestions.length > 0 && (
          <button
            type="button"
            className="btn btn--accent btn--sm"
            disabled={busy}
            onClick={() => void apply([...suggestions])}
            data-testid="suggestions-all"
          >
            {t('suggest.all', { n: suggestions.length })}
          </button>
        )}
        {suggestions.length > 1 && !per && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPer(true)} data-testid="suggestions-per">
            {t('suggest.per')}
          </button>
        )}
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          {t('suggest.back')}
        </button>
      </div>
    </section>
  )
}
