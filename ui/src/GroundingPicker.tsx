import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type GroundCandidate, groundTerms } from './groundingApi'
import { SearchIcon } from './icons'

/**
 * Reusable picker for GROUNDING a class/predicate to an external standard term
 * (external-standard-alignment.md §8). Seeded with the term's name, it queries the
 * closed-set `/api/ground` and lists the ranked REAL candidates (no fabrication); the
 * human picks one. Adopting the pick is the caller's job (it asserts an alignment).
 */
export function GroundingPicker({
  seed,
  kind,
  domain,
  onPick,
  onCancel,
}: {
  seed: string
  kind: 'class' | 'property'
  domain?: string
  onPick: (c: GroundCandidate) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [q, setQ] = useState(seed)
  const [results, setResults] = useState<GroundCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    const query = q.trim()
    let off = false
    // All state changes happen inside the (debounced) timer, never synchronously in the
    // effect body — empty query clears, otherwise search the closed-set catalog.
    const handle = setTimeout(
      () => {
        if (off) return
        if (!query) {
          setResults([])
          setLoading(false)
          setErr('')
          return
        }
        setLoading(true)
        setErr('')
        groundTerms(query, { kind, domain, limit: 8 })
          .then((r) => !off && setResults(r))
          .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
          .finally(() => !off && setLoading(false))
      },
      query ? 200 : 0,
    )
    return () => {
      off = true
      clearTimeout(handle)
    }
  }, [q, kind, domain])

  return (
    <div className="grounding-picker">
      <div className="grounding-search">
        <SearchIcon size={14} className="grounding-search-icon" />
        {/* biome-ignore lint/a11y/noAutofocus: the picker opens on an explicit click */}
        <input
          className="grounding-input"
          value={q}
          autoFocus
          placeholder={t('grounding:picker.placeholder')}
          onChange={(e) => setQ(e.target.value)}
        />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
          {t('grounding:picker.cancel')}
        </button>
      </div>
      {err && <p className="promote-err">{err}</p>}
      {loading && <p className="grounding-hint">{t('grounding:picker.searching')}</p>}
      {!loading && !err && q.trim() !== '' && results.length === 0 && (
        <p className="grounding-hint">{t('grounding:picker.none')}</p>
      )}
      {results.length > 0 && (
        <ul className="grounding-results">
          {results.map((c) => (
            <li key={c.iri}>
              <button
                type="button"
                className="grounding-cand"
                onClick={() => onPick(c)}
                title={c.iri}
              >
                <code className="grounding-curie">{c.curie}</code>
                <span className="grounding-cand-label">{c.label}</span>
                <span className="grounding-vocab">{c.vocab_title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
