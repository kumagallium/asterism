# Phase 2 #5: full-scale benchmark results

Resolves the Phase 0.5 residual risk #1 — *"Oxigraph full-scale performance
unverified"* (see [`../../docs/architecture/phase05-decisions.md`](../../docs/architecture/phase05-decisions.md) §4).

- **Date**: 2026-05-29
- **Host**: macOS, 64 GB RAM; Oxigraph `ghcr.io/oxigraph/oxigraph:latest` in Docker
- **Dataset**: full starrydata (papers / samples / curves)
- **Ingester**: Phase 2 (composite IRIs + QUDT + DigitizationActivity, `emit_prov=True`)
- **Reproduce**: `convert.py` (stage 1) → `run_oxigraph_bench.sh` (stage 2+3)

## 1. Conversion (CSV → Turtle, rdflib)

| kind | logical rows | triples | wall-clock | TTL size | triples/s |
|---|---:|---:|---:|---:|---:|
| papers | 56,389 | 2,384,910 | 244 s | 129 MB | 9,800 |
| samples | 104,846 | 2,712,569 | 332 s | 182 MB | 8,200 |
| curves | 233,103 | 6,901,066 | 233 s | 428 MB | 29,600 |
| **total** | **394,338** | **11,998,545** | **809 s (13.5 min)** | **738 MB** | **14,800** |

- **0 row errors** across all three (after the invalid-IRI URL fix — see below).
- Note: `samples` logical rows (104,846) < `wc -l` (144,091) because `sample_info`
  JSON cells contain embedded newlines; the CSV reader parses 104,846 logical records.
- **Throughput is rdflib-bound, not data-bound.** curves convert ~3× faster per
  triple than papers because a curve's heavy x/y arrays collapse into 2 string
  literals (方針 C), whereas papers expand author/periodical sub-entities per row.
  The dominant cost is rdflib's in-memory Graph build + Turtle serialization
  (pure Python). Headroom (≈5–50×) if full re-index time ever matters: emit
  N-Triples directly (skip the rdflib Graph), parallelize the 3 kinds, and/or use
  Oxigraph's bulk loader instead of HTTP POST. **Not urgent** — the hot path is the
  Phase 2 watcher's incremental per-file ingest, not full re-index.

## 2. Load into Oxigraph (SPARQL Graph Store Protocol, HTTP POST)

| kind | TTL | load time |
|---|---:|---:|
| papers | 145 MB | 158 s |
| samples | 193 MB | 228 s |
| curves | 433 MB | 705 s |
| **total** | **738 MB** | **1,091 s (18 min)** |

- **HTTP POST load is slow.** For an initial bulk load, prefer Oxigraph's
  offline bulk loader (`oxigraph load --location ... --file ...`), which is
  markedly faster than streaming Turtle over HTTP. The watcher's incremental
  POST path is fine for one-CSV-at-a-time updates; the 18 min figure only
  applies to a from-scratch full load.

## 3. Store size

- **10 GB on disk** for 11,998,545 triples (RocksDB, after load settled).
- Larger than a "typical" ~1–2 GB / 12M-triple store because the curve x/y
  arrays are stored as **large JSON string literals** (方針 C) and Oxigraph
  indexes literals across multiple position indexes → storage amplification.
  This is the storage cost of keeping raw x/y queryable-as-literals. Phase 3
  could revisit (don't index the JSON literals, or store arrays out-of-band).

## 4. Query latency (12M triples, 5 runs each, median)

| query | median | min | max | result |
|---|---:|---:|---:|---|
| count_default_graph | 2.1 ms | 1.7 | 20.2 | **c=0** ⚠ (see §5) |
| count_all_named | 4,878 ms | 3,720 | 34,430 | c=11,998,545 |
| count_by_class | 205 ms | 190 | 727 | papers 56,361 / samples 104,846 / curves 233,103 |
| composition_substring (Bi2Te3, LIMIT 20) | **15 ms** | 14 | 27 | 20 rows |
| **qudt_seebeck_crosssynonym** | **21 ms** | 19 | 36 | **n=37,983** |
| highest_seebeck_ymax (ORDER BY ABS, LIMIT 10) | 202 ms | 197 | 332 | 10 rows |
| digitization_provenance (LIMIT 10) | **4.8 ms** | 2.9 | 5.9 | 10 rows |
| samples_per_paper_top (GROUP BY all samples) | 587 ms | 481 | 634 | max 160 samples/paper |

**Verdict — the query patterns AI actually uses are interactive at full scale:**
- Targeted lookups with a LIMIT (substring, IRI match, provenance walk): **5–21 ms**.
- Occasional full-store aggregates (whole-store COUNT, GROUP BY over all samples,
  global ORDER BY): **0.2–5 s** — acceptable for analytical, not interactive, use.
- The worst case (full `COUNT(*)` over 12M triples ≈ 5 s) is a cold full scan and
  rarely needed in practice.

**Phase 0.5 risk #1 is resolved**: Oxigraph handles full starrydata comfortably
for the real query patterns. No backend change needed for this scale.

**QUDT validated at scale**: `qudt:SeebeckCoefficient` returns **37,983** curves,
unifying the "Seebeck coefficient" (37,370) and "thermopower" (559) string
variants — synonym normalization works on the full dataset, in 21 ms.

## 5. Finding: named-graph vs default-graph gap ⚠ (follow-up)

The Phase 2 watcher loads each kind into a **named graph**
(`sd:graph/{papers,samples,curves}`). SPARQL only sees named-graph triples
through a `GRAPH` clause, so a default-graph query (`SELECT … WHERE { ?s ?p ?o }`)
returns **0** at full scale — and the **MIE `sparql_query_examples` are written
without `GRAPH` clauses**, so as written they would return nothing against a
watcher-loaded store.

This didn't surface in earlier Phase 2 E2E checks because those verification
queries happened to wrap patterns in `GRAPH ?g { … }`.

**Decision needed (tracked as a follow-up, out of scope for this benchmark PR):**
1. Run Oxigraph with a **union default graph** so `{ ?s ?p ?o }` spans named
   graphs (simplest if Oxigraph supports it for the deployment), **or**
2. Load into the **default graph** (drop the per-kind named graphs — the
   `graphs:` split is a nice-to-have the AI queries don't rely on), **or**
3. **`GRAPH`-wrap the MIE example queries** (more verbose for the AI).

Recommended: (2) load into the default graph — it matches how the MIE queries
are written and how Phase 1 / the smoke test loaded data, and keeps the AI's
SPARQL simple.

## 6. Bug surfaced & fixed by this benchmark

Full-scale papers contain **38 legacy Wiley DOI URLs** with angle brackets, e.g.
`http://dx.doi.org/10.1002/(SICI)1521-396X(199910)175:2<683::AID-PSSA683>3.0.CO;2-3`.
These made rdflib emit invalid Turtle (`schema:url` as a malformed IRI) that
Oxigraph would reject. Fixed in `ingest/src/csv2rdf/starrydata.py::safe_url` by
percent-encoding IRI-illegal characters; regression test added. The 100-paper
Phase 1 subset never contained these — a concrete example of full-scale
benchmarking catching a latent robustness bug.
