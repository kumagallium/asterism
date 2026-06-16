import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { groundSchema, type SchemaTermGrounding } from './groundingApi'
import { LinkIcon } from './icons'

/**
 * Propose-time DISCOVERY: for each class/predicate the AI design would MINT, show the
 * matching famous-standard candidates (cmso:/qudt:/schema.org …) so the designer sees
 * "your data could lean on this standard" (external-standard-alignment.md §8). Purely
 * informational + deterministic (closed-set, never the LLM's memory); the actual REUSE
 * / ALIGN happens later in the catalog (a human-vetted alignment). Renders nothing when
 * nothing grounds, to keep the review uncluttered.
 */
export function SchemaGroundingPanel({ proposalMd }: { proposalMd: string }) {
  const { t } = useTranslation()
  const [terms, setTerms] = useState<SchemaTermGrounding[]>([])
  const [err, setErr] = useState('')

  useEffect(() => {
    // setState only inside the async callbacks (never synchronously in the effect body).
    let off = false
    groundSchema(proposalMd)
      .then((r) => {
        if (off) return
        setTerms(r)
        setErr('')
      })
      .catch((e) => {
        if (off) return
        setTerms([])
        setErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      off = true
    }
  }, [proposalMd])

  if (err) return <p className="grounding-hint schema-grounding-err">{err}</p>
  if (terms.length === 0) return null

  return (
    <section className="schema-grounding">
      <div className="ds-subhead schema-grounding-head">
        <LinkIcon size={14} className="ds-grounding-icon" />
        {t('grounding:schema.head')}
        <span className="xw-hint-inline">{t('grounding:schema.hint')}</span>
      </div>
      <ul className="schema-grounding-list">
        {terms.map((tm) => (
          <li className="schema-grounding-row" key={`${tm.kind}:${tm.source_curie}`}>
            <span className="schema-grounding-src">
              <code className="schema-grounding-term">{tm.source_curie}</code>
              <span className="ds-grounding-kind">
                {tm.kind === 'class' ? t('grounding:kind.class') : t('grounding:kind.property')}
              </span>
            </span>
            <span className="schema-grounding-arrow" aria-hidden="true">
              →
            </span>
            <span className="schema-grounding-cands">
              {tm.candidates.map((c) => (
                <code
                  className="grounding-curie schema-grounding-cand"
                  key={c.iri}
                  title={`${c.label} — ${c.vocab_title}\n${c.iri}`}
                >
                  {c.curie}
                </code>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
