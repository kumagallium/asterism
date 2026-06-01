// Client for the demo agent's grounded-answer surface.
//
// IMPORTANT (boundary): the runtime answer-generating LLM lives OUTSIDE the
// csv2rdf core — in a separate "demo agent" (consumption layer). This module
// only speaks the two HTTP contracts below; it contains NO answer-generation
// logic. While the demo agent is built, a front-end mock returns fixtures so
// the UI can be developed against the contract. Flip VITE_DEMO_MODE=live (and
// point the proxy at the agent) to use the real endpoints unchanged.
//
//   POST /demo/ask           { question }                -> AskResponse
//   GET  /demo/provenance?iri=<iri>                      -> ProvenanceChain
//
// The mock fixtures mirror the contract samples in the demo handoff.

// ---- contract types (§3) -------------------------------------------------

export interface CitationField {
  [key: string]: string | number
}

export interface Citation {
  iri: string
  kind: string // "curve" | "sample" | "paper" | ...
  label: string
  fields: CitationField
}

export interface AskResponse {
  answer: string
  citations: Citation[]
  notes: string[]
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
    },
  },
]

const ASK_FALLBACK: AskResponse = {
  answer:
    'この質問に対する根拠付き回答のデモ fixture は未登録です。ZT ランキング・組成検索の例をお試しください。',
  citations: [],
  notes: ['mock モード: 質問に一致する fixture がありません'],
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

// ---- public API -----------------------------------------------------------

/** Ask a natural-language question; get a grounded answer + citations + notes. */
export async function ask(question: string): Promise<AskResponse> {
  if (IS_MOCK) {
    await delay(450) // feel of a real call
    const hit = ASK_FIXTURES.find((f) => f.match(question))
    return hit ? hit.response : ASK_FALLBACK
  }
  const res = await fetch('/demo/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`ask failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return (await res.json()) as AskResponse
}

/** Resolve the provenance chain (curve→sample→paper→digitization→ingestion) for an IRI. */
export async function provenance(iri: string): Promise<ProvenanceChain> {
  if (IS_MOCK) {
    await delay(250)
    // The mock returns the canonical chain regardless of iri; live mode keys on it.
    return { ...PROVENANCE_FIXTURE, iri }
  }
  const res = await fetch(`/demo/provenance?iri=${encodeURIComponent(iri)}`)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`provenance failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return (await res.json()) as ProvenanceChain
}

/** True when serving fixtures (so the UI can show a "demo data" hint). */
export const isMockMode = IS_MOCK

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
