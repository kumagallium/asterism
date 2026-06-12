# PDF → citable document graph — de-risk spike (document-ontology layer follow-up)

Proves the missing link for the real-world need (most documents are **PDF/Word**,
not JATS): a layout-aware converter recovers structure from an unstructured PDF,
and that structure lands in the **same** deterministic post-pass and the **same**
typed citation tools as the JATS path (ADR `docs/architecture/document-ontology-layer.md`).

## Result — PROVEN

`run_spike.py` over `ma11040649.docling.md` (the Docling conversion of the real
CC-BY PDF of PMC5951533) builds a `doco`/`nif` graph (1941 triples — 14 sections,
the full numbered hierarchy incl. subsections, sentences) and recovers the §4
measurement-condition sentence (PPMS/TTO) with a resolvable IRI + conversion
provenance. **The PDF-derived sentence is byte-identical to the one the JATS path
cited** (`datasets/papers/seed/paper.ttl`) — the two independent sources agree on
the exact citable fact.

```
PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/pdf-docling-spike/run_spike.py
```

## The honest trust model

The ML converter (Docling) is an **offline, provenance-recorded** step — NOT in
the runtime ingest path. Its output is the structured source; the conversion is
stamped as a `lit:DocumentConversionActivity` (converter + version + date), one
rung of confidence below JATS. Re-running the deterministic post-pass over the
committed converter output is byte-stable; only the converter step needs pinned
model versions (recorded in the activity). The committed `ma11040649.docling.md`
lets this spike run with **no torch/Docling dependency**.

## Converter benchmark (this PDF, vs the JATS ground truth)

| | JATS (truth) | **Docling** (layout-aware) | naive (pymupdf4llm) |
|---|---|---|---|
| section headings | 10 top + 5 sub | **14** (all numbered incl. 2.1–2.5) | 2 (garbled) |
| figures | 13 | 17 picture + 23 caption | 0 |
| tables | 10 | **10** | — |
| key sentences (PPMS/argon/TTO) | — | **all recovered** | line-wrapped |

A naive PDF→text tool recovers the text but not the structure; a scholarly
layout-aware converter (Docling, or GROBID→TEI) recovers JATS-grade structure.

## Files

| file | role |
|---|---|
| `ma11040649.docling.md` | committed Docling conversion of the CC-BY PDF (the structured source; lets the spike run without torch) |
| `run_spike.py` | markdown → `doco`/`nif` graph (reusing the JATS post-pass) → recover + cross-check the cited sentence |

## Productization (next, separate PR)

`lit:DocumentConversionActivity` as a first-class step; Docling/GROBID as an
**optional** external converter (kept out of core deps); Word via `pandoc` (the
easy win — `.docx` is structured OOXML); figures/captions/tables into their proper
`doco` classes (this spike folds them into paragraphs). See
`docs/reports/pdf-conversion-feasibility.md`.
