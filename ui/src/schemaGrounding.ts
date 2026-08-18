import { useEffect, useState } from 'react'
import { groundSchema, type SchemaTermGrounding } from './groundingApi'

/**
 * Propose-time DISCOVERY, shared by both tiers: for each class/predicate the AI
 * design would MINT, the curated closed-set search returns the matching famous
 * standards (cmso:/qudt:/schema.org …). Deterministic — no LLM, never a guessed
 * IRI (external-standard-alignment.md §8).
 *
 * The detail tier renders the full CURIE → CURIE table (`SchemaGroundingPanel`).
 * The simple tier must NOT show that table, but it still has to be TOLD that some
 * of its new words could lean on a standard — one sentence keyed off
 * `terms.length` and a button to the dataset's align screen (DETAIL-GAP-10).
 * Both go through this hook so the lookup happens once per proposal, identically.
 */
export function useSchemaGrounding(proposalMd: string): {
  terms: SchemaTermGrounding[]
  err: string
} {
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

  return { terms, err }
}
