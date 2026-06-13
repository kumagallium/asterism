# Word (.docx) → citable document graph — de-risk spike

The easy-win sibling of `experiments/pdf-docling-spike/`. **Word is already
structured XML inside the `.docx` (OOXML)**, so a deterministic, dependency-light
converter (**pandoc**) recovers the structure faithfully — and pandoc emits
**JATS** with `<sec id><title><p>` nesting, the EXACT shape the document layer's
existing JATS path ingests. So Word lands in the **unchanged** pipeline (no ML,
no layout heuristics — unlike PDF).

## Result — PROVEN

`run_spike.py` over `sample_agreement.jats.xml` (pandoc's `docx -t jats` of
`sample_agreement.docx`) builds a `doco`/`nif` graph and resolves a specific
clause — **Article 2.2, the 30-day cure period** — to a citable IRI with a
structural path (§2-2) and conversion provenance.

```
PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/word-pandoc-spike/run_spike.py
# → §2-2 / 2.2 Termination for Cause — "...thirty (30) days written notice..." (resolvable IRI)
```

## Why this matters for legal documents

Legal contracts are usually **Word**, with explicit Article/Section/clause
structure — a *better* fit for this layer than scientific prose. The committed
sample is a short Master Services Agreement; the spike cites a clause exactly as
a lawyer would reference it. pandoc preserves the numbered hierarchy and gives
clean readable section ids (`term-and-termination`, `2.1`).

## Trust model

The conversion (`docx → JATS`) is an offline, provenance-recorded step
(`lit:DocumentConversionActivity`, converter + version + date). pandoc is
**deterministic** (no ML), so Word sits HIGH on the confidence ladder —
essentially JATS-grade (vs born-digital-PDF/Docling = medium, scanned/OCR = low).
The committed `sample_agreement.jats.xml` lets the spike run with no pandoc
dependency; `sample_agreement.docx` is the original source (synthetic sample).

## Files

| file | role |
|---|---|
| `sample_agreement.docx` | synthetic sample contract (the Word source) |
| `sample_agreement.jats.xml` | `pandoc sample_agreement.docx -t jats` output (committed; lets the spike run without pandoc) |
| `run_spike.py` | pandoc-JATS → `doco`/`nif` graph (reusing the JATS post-pass) → cite a clause |

## How the source was produced

```bash
pandoc sample_agreement.md  -o sample_agreement.docx       # author the .docx
pandoc sample_agreement.docx -t jats -o sample_agreement.jats.xml   # docx → JATS (pandoc 3.1.11.1)
```
