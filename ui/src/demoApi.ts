// Client for the demo agent's grounded-answer surface.
//
// IMPORTANT (boundary): the runtime answer-generating LLM lives OUTSIDE the
// asterism core — in a separate "demo agent" (consumption layer) on its OWN
// origin (:8090), distinct from the workbench API (:8080). This module only
// speaks the two HTTP contracts below; it contains NO answer-generation logic.
// While the demo agent is built, a front-end mock returns fixtures so the UI
// can be developed against the contract. Flip VITE_DEMO_MODE=live to call the
// real agent at VITE_DEMO_AGENT_URL (default http://localhost:8090).
//
//   POST {AGENT}/demo/ask        { question }            -> AskResponse
//   GET  {AGENT}/demo/provenance?iri=<iri>               -> ProvenanceChain
//
// NB: /demo/* must NOT be routed through the workbench API (:8080); the agent
// is a separate backend. That is why we use an absolute base URL here rather
// than a same-origin path (which the Vite proxy would forward to :8080).
//
// The mock fixtures mirror the contract samples in the demo handoff.

// ---- contract types (§3) -------------------------------------------------

// Field values come from real RDF rows: a missing composition/title/DOI arrives
// as null. Consumers must tolerate null/undefined (CitationCard skips them).
export type CitationFieldValue = string | number | boolean | null | undefined

export interface CitationField {
  [key: string]: CitationFieldValue
}

export interface Citation {
  iri: string
  kind: string // "curve" | "sample" | "paper" | ...
  label: string
  fields: CitationField
}

export interface VerifiedTool {
  dataset: string
  name: string
  title: string
}

export interface AskResponse {
  answer: string
  citations: Citation[]
  notes: string[]
  // Read-only SPARQL the agent ran to derive the answer. Empty for the typed
  // (starrydata) path; populated when the LLM escape writes queries over a
  // user-designed schema. Disclosed in the UI so the answer is verifiable.
  sparql: string[]
  // C (P4-2b) provenance: which human-vetted typed tools the answer used
  // (deterministic, reproducible, citable) and whether an unverified
  // LLM-generated SPARQL escape was used. Drives the answer provenance badge.
  verifiedTools?: VerifiedTool[]
  unverifiedSparql?: boolean
}

export interface ProvenanceStep {
  step: string // "curve" | "sample" | "paper" | "digitization" | "ingestion"
  iri: string
  label: string
  detail: string
}

export interface ProvenanceChain {
  iri: string
  chain: ProvenanceStep[]
}

// ---- mode switch ----------------------------------------------------------

const MODE = (import.meta.env.VITE_DEMO_MODE as string | undefined) ?? 'mock'
const IS_MOCK = MODE !== 'live'

// Absolute base URL of the demo agent (:8090 by default). Trailing slash
// trimmed so `${AGENT_BASE}/demo/ask` is well-formed. Deliberately NOT a
// same-origin path: the agent is a separate backend from the workbench API.
const AGENT_BASE = (
  (import.meta.env.VITE_DEMO_AGENT_URL as string | undefined) ?? 'http://localhost:8090'
).replace(/\/+$/, '')

// ---- fixtures (the 3 canonical demo questions) ----------------------------
// Kept here so the UI renders end-to-end before the demo agent ships. Values
// mirror the handoff contract samples.

const CURVE_IRI = 'https://example.org/starrydata/resource/curve/1-2-3'
const SAMPLE_IRI = 'https://example.org/starrydata/resource/sample/1-2'
const PAPER_IRI = 'https://example.org/starrydata/resource/paper/456'

const ASK_FIXTURES: { match: (q: string) => boolean; response: AskResponse }[] = [
  {
    // (2) ZT ranking — the headline "grounding payoff" demo
    match: (q) => /zt|熱電|ranking|ランキング|最も高い|highest/i.test(q),
    response: {
      answer:
        '記録上の最大は SnSe の約 2.6（curve 1-2-3 / paper 456）。>3.5 の極端値が数件あるが、軸ラベル誤りの可能性として除外した。',
      citations: [
        {
          iri: CURVE_IRI,
          kind: 'curve',
          label: 'Fig.3 ZT vs T',
          fields: { propertyY: 'ZT', yMax: 2.6 },
        },
        {
          iri: SAMPLE_IRI,
          kind: 'sample',
          label: 'SnSe',
          fields: { composition: 'SnSe' },
        },
      ],
      notes: ['物理的にあり得ない ZT（>3.5）はデータ誤りの可能性として除外した'],
      sparql: [],
    },
  },
  {
    // (1) composition search
    match: (q) => /組成|composition|SnSe|含む|contain/i.test(q),
    response: {
      answer:
        'SnSe 系の試料は 3 件ヒットした。代表は sample 1-2（SnSe, paper 456）。いずれも熱電測定（Seebeck / ZT）を伴う。',
      citations: [
        {
          iri: SAMPLE_IRI,
          kind: 'sample',
          label: 'SnSe',
          fields: { composition: 'SnSe', measurements: 'Seebeck, ZT' },
        },
        {
          iri: PAPER_IRI,
          kind: 'paper',
          label: 'Snyder et al. (2014)',
          fields: { DOI: '10.1038/nature13184' },
        },
      ],
      notes: [],
      sparql: [],
    },
  },
  {
    // (3) general / user-designed schema — exercises the LLM SPARQL escape, so
    // the answer comes with the read-only query it ran (disclosure panel).
    match: (q) => /sparql|スキーマ|クエリ|一般|どんな|新しい|widget/i.test(q),
    response: {
      answer:
        '新しく設計したスキーマには Widget クラスが 2 件あり、それぞれ name を持ちます（alpha, beta）。型付きツールに該当が無かったため、スキーマを内省して下の SPARQL を生成・実行しました。',
      citations: [
        {
          iri: 'https://example.org/w1',
          kind: 'Widget',
          label: 'alpha',
          fields: { name: 'alpha' },
        },
      ],
      notes: [],
      sparql: [
        'SELECT ?w ?n WHERE {\n  ?w a <https://example.org/Widget> ;\n     <https://example.org/name> ?n\n} LIMIT 50',
      ],
    },
  },
]

const ASK_FALLBACK: AskResponse = {
  answer:
    'この質問に対する根拠付き回答のデモ fixture は未登録です。ZT ランキング・組成検索の例をお試しください。',
  citations: [],
  notes: ['mock モード: 質問に一致する fixture がありません'],
  sparql: [],
}

const PROVENANCE_FIXTURE: ProvenanceChain = {
  iri: CURVE_IRI,
  chain: [
    { step: 'curve', iri: CURVE_IRI, label: 'Fig.3 ZT vs T', detail: 'yMax=2.6' },
    { step: 'sample', iri: SAMPLE_IRI, label: 'SnSe', detail: 'composition=SnSe' },
    { step: 'paper', iri: PAPER_IRI, label: 'Snyder et al. (2014)', detail: 'DOI 10.1038/nature13184' },
    {
      step: 'digitization',
      iri: 'https://example.org/starrydata/resource/digitization/1-2-3',
      label: 'WebPlotDigitizer',
      detail: 'from Fig.3',
    },
    {
      step: 'ingestion',
      iri: 'https://example.org/starrydata/resource/ingestion/2026-05-31',
      label: 'IngestionActivity',
      detail: '2026-05-31',
    },
  ],
}

// ---- response normalization (real-data edge cases) -----------------------
// Real agent responses can omit fields or send nulls (empty citations/notes,
// null composition/title, variable-length chains). Normalize at the boundary
// so every consumer gets well-typed arrays and never crashes on a missing key.

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function normalizeCitation(raw: unknown): Citation {
  const r = (raw ?? {}) as Record<string, unknown>
  const fields = r.fields && typeof r.fields === 'object' ? (r.fields as CitationField) : {}
  return {
    iri: asString(r.iri),
    kind: asString(r.kind),
    label: asString(r.label),
    fields,
  }
}

function normalizeVerifiedTool(raw: unknown): VerifiedTool {
  const r = (raw ?? {}) as Record<string, unknown>
  const name = asString(r.name)
  return { dataset: asString(r.dataset), name, title: asString(r.title) || name }
}

function normalizeAsk(raw: unknown): AskResponse {
  const r = (raw ?? {}) as Record<string, unknown>
  return {
    answer: asString(r.answer),
    citations: Array.isArray(r.citations) ? r.citations.map(normalizeCitation) : [],
    notes: Array.isArray(r.notes) ? r.notes.map(asString).filter(Boolean) : [],
    sparql: Array.isArray(r.sparql) ? r.sparql.map(asString).filter(Boolean) : [],
    verifiedTools: Array.isArray(r.verified_tools)
      ? r.verified_tools.map(normalizeVerifiedTool).filter((t) => t.name)
      : [],
    unverifiedSparql: r.unverified_sparql === true,
  }
}

function normalizeChain(raw: unknown, iri: string): ProvenanceChain {
  const r = (raw ?? {}) as Record<string, unknown>
  const chain = Array.isArray(r.chain)
    ? r.chain.map((s): ProvenanceStep => {
        const o = (s ?? {}) as Record<string, unknown>
        return {
          step: asString(o.step),
          iri: asString(o.iri),
          label: asString(o.label),
          detail: asString(o.detail),
        }
      })
    : []
  return { iri: asString(r.iri) || iri, chain }
}

// ---- public API -----------------------------------------------------------

/** Ask a natural-language question; get a grounded answer + citations + notes.
 *
 * The deterministic typed path needs no key. When it finds nothing (e.g. a
 * user-designed schema), the agent falls back to an LLM that writes read-only
 * SPARQL — that path needs a key. We reuse the workbench's user-brought key
 * (sessionStorage, never persisted) so a question over a freshly-designed
 * schema "just works" without a second key prompt. */
export async function ask(question: string, apiKey?: string): Promise<AskResponse> {
  if (IS_MOCK) {
    await delay(450) // feel of a real call
    const hit = ASK_FIXTURES.find((f) => f.match(question))
    return hit ? hit.response : ASK_FALLBACK
  }
  const key = apiKey ?? sessionStorage.getItem('asterism.apiKey') ?? ''
  const res = await fetch(`${AGENT_BASE}/demo/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { 'X-API-Key': key } : {}),
    },
    body: JSON.stringify({ question }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`ask failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return normalizeAsk(await res.json())
}

/** Resolve the provenance chain (curve→sample→paper→digitization→ingestion) for an IRI. */
export async function provenance(iri: string): Promise<ProvenanceChain> {
  if (IS_MOCK) {
    await delay(250)
    // The mock returns the canonical chain regardless of iri; live mode keys on it.
    return { ...PROVENANCE_FIXTURE, iri }
  }
  const res = await fetch(`${AGENT_BASE}/demo/provenance?iri=${encodeURIComponent(iri)}`)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`provenance failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return normalizeChain(await res.json(), iri)
}

// ---- live vocabulary (schema_summary) -------------------------------------
// The schema-agnostic introspection of whatever is ACTUALLY in the store
// (classes / predicates with usage counts), enriched with rdfs:label projected
// from each dataset's TBox (#20 step5). This is what makes the vocabulary view
// real + generic: it reflects the live canonical FROM-merge, not a hardcoded
// starrydata fixture, and labels appear once a dataset's ontology is projected.

export interface SchemaTerm {
  iri: string
  count: number
  label?: string
}

export interface SchemaSummary {
  classes: SchemaTerm[]
  predicates: SchemaTerm[]
}

const SCHEMA_FIXTURE: SchemaSummary = {
  classes: [
    { iri: 'https://example.org/starrydata/ontology#Curve', count: 1240, label: 'Curve' },
    { iri: 'https://example.org/starrydata/ontology#Sample', count: 318, label: 'Sample' },
    { iri: 'https://example.org/starrydata/ontology#Paper', count: 96, label: 'Paper' },
  ],
  predicates: [
    { iri: 'https://example.org/starrydata/ontology#yMax', count: 1240, label: 'yMax' },
    { iri: 'https://example.org/starrydata/ontology#ofSample', count: 1240, label: 'ofSample' },
    { iri: 'https://example.org/starrydata/ontology#fromPaper', count: 318, label: 'fromPaper' },
  ],
}

function normalizeTerms(raw: unknown): SchemaTerm[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((t): SchemaTerm => {
      const o = (t ?? {}) as Record<string, unknown>
      return {
        iri: asString(o.iri),
        count: typeof o.count === 'number' ? o.count : Number(o.count) || 0,
        label: typeof o.label === 'string' ? o.label : undefined,
      }
    })
    .filter((t) => t.iri)
}

/** Live vocabulary actually present in the canonical store (best-effort).
 *
 * Returns ``null`` when the agent is unreachable so callers can fall back to the
 * curated ontology fixture rather than showing an error. */
export async function getSchema(): Promise<SchemaSummary | null> {
  if (IS_MOCK) {
    await delay(300)
    return SCHEMA_FIXTURE
  }
  try {
    const res = await fetch(`${AGENT_BASE}/demo/schema`)
    if (!res.ok) return null
    const data = (await res.json()) as Record<string, unknown>
    return {
      classes: normalizeTerms(data.classes),
      predicates: normalizeTerms(data.predicates),
    }
  } catch {
    return null
  }
}

/** True when serving fixtures (so the UI can show a "demo data" hint). */
export const isMockMode = IS_MOCK

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
