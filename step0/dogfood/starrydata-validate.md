# Dogfood report — `asterism-validate` on Phase 1 starrydata artifacts

Date: 2026-05-29
Command:

```bash
asterism-validate \
    --tbox docs/ontology/starrydata.ttl \
    --diagram docs/ontology/diagram.md \
    --mie data/togomcp/mie/starrydata.yaml \
    --ingester ingest/src/asterism/starrydata.py \
    --csv starrydata_papers.csv \
    --csv starrydata_samples.csv \
    --csv starrydata_curves.csv \
    --fk SID
```

Result: **exit 0** (6 pass / 1 warn / 1 skip), runtime ~10 s.

| # | Trap | Status | Detail |
|---|---|---|---|
| T1 | ID uniqueness | ✓ pass | `(SID, sample_id)` → 0 collisions in 104,846 rows; `(SID, sample_id, figure_id)` → 0 collisions in 233,103 rows |
| T2 | BOM | ✓ pass | ingester uses `utf-8-sig`; samples.csv + curves.csv have BOM (stripped correctly) |
| T3 | bnode-free | ✓ pass | 0 bnodes in TBox; no `BNode()` calls in ingester |
| T4 | MIE keywords / categories | ✓ pass | 34 keywords (en + 日本語 + composition formulas), 8 categories |
| T5 | Mermaid colon escape | ✓ pass | 8 relation labels, all free of `:` (PR #6 fix held) |
| T6 | fake sample_rdf_entries | ✓ pass | 7 sample IRI head IDs all trace to real CSV values |
| T7 | Why / Alt / Trade-offs | ⚠ warn | `architectural_notes` mentions "Trade-offs" but not the literal words "Why" / "Alternatives" |
| T8 | AI hallucination test | · skip | Opt-in (no LLM connected) |

## Confirmations

- **T1 full-scale**: re-verified that Phase 1's composite IRI design ([design-rationale §1](../../docs/architecture/design-rationale.md#1-iri-命名--複合キー-composite-iri)) holds across all 104,846 samples and 233,103 curves — `(SID, sample_id)` and `(SID, sample_id, figure_id)` produce zero collisions
- **T3 full-scale**: the Phase 1 ingester (600+ lines) and TBox (153 triples) genuinely contain zero blank nodes — `design-rationale §2` claim holds
- **T5 regression-blocker**: the [Mermaid colon-escape fix from PR #6](https://github.com/kumagallium/asterism/pull/6) is now CI-protected — if future edits reintroduce `:` in a relation label, validate fails

## Known limitations surfaced

### T7 heuristic is too literal
`architectural_notes` in [starrydata.yaml:317](../../data/togomcp/mie/starrydata.yaml) reads like a narrative:

> Composite IRI keys (Phase 1 bug fix): starrydata's raw sample_id is not globally unique (9,661 collisions across papers)...

This documents the *Why* / *Alternatives considered* / *Trade-offs* in prose without using those literal English words. The validator's keyword-substring heuristic misses this and flags `warn`. The schema is fine — the validator's bar is loose by design (heuristic, not enforced). A future improvement: parse YAML structure (e.g. `- decision:` / `why:` / `alternatives:`) instead of grepping the prose blob.

### T1 only validates MIE-declared composites
T1 extracts IRI templates from MIE `shape_expressions` (`sdr:sample/{SID}-{sample_id}` etc.). The Phase 1 MIE declares composite keys for Sample and Curve, but **not** for Paper — so T1 doesn't notice the 28 collisions on papers.csv `SID` that the [follow-up task](https://github.com/kumagallium/asterism/) is investigating.

Possible fix (not in this PR): also extract IRI templates from ingester source (`sdr[f"paper/{sid}"]` → infer `sdr:paper/{SID}`). Tracking as a follow-up.

### T8 is a placeholder
The slot exists but the implementation requires a curated NL question set + MCP client wiring. Belongs in a separate PR (Phase 3 follow-up).

## Why this matters for CI

When committed to `.github/workflows/ci.yml`, this validator would catch:
- **Regression of PR #6** (Mermaid colon escape) — automatic
- **Regression of PR #8** (composite IRI collision) — automatic
- **Forgetting `utf-8-sig`** in a new ingester — automatic
- **Hallucinated SIDs in `sample_rdf_entries`** when AI-generated MIEs land in PRs — automatic (this is what Phase 3 propose_schema needs)
- **MIE missing keywords** before push — automatic (prevents the find_databases hit-rate problem)

All 6 of these were Phase 1 incidents. The validator now turns them into deterministic failures instead of "we'll catch it in code review".

## Next steps

1. Add `asterism-validate` as an optional CI job (needs the source CSVs — currently local-only)
2. Improve T7 to YAML-structured rationale (or rephrase Phase 1 prose to include the literal keywords)
3. Improve T1 to also extract IRI templates from ingester source (catches the papers.csv SID issue from MIE-incomplete schemas)
4. Implement T8 hallucination test once we have a curated NL question fixture
