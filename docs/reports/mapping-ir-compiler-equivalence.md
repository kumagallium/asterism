# Verification report — Mapping IR: deterministic RML compilation is equivalent and kills the syntax-error classes

**Date:** 2026-07-06 **Verdict:** ✅ **equivalence + structural elimination verified**
(graph-isomorphic goldens, real-Morph-KGC parity with the handwritten Materials
Project mapping, full pipeline over the real Starrydata export) — ✅ **live
weak-model measurement done same day** ([Addendum](#addendum-2026-07-06--live-weak-model-measurement)):
**syntax-class issues = 0 in every round of every run (5 runs)**, and both
target models — real **Qwen3.6-35B-A3B** and **gpt-oss-120b** — converge to
gate-passing, Morph-KGC-materializable designs (1388 / 1468 triples).
**Method/decision of record:** [`architecture/mapping-ir-compiler.md`](../architecture/mapping-ir-compiler.md) ·
harness [`experiments/mapping-ir-weakmodel-dogfood/`](../../experiments/mapping-ir-weakmodel-dogfood/)

## 1. Question

Production dogfooding (2026-07-06, registry `dataset-548d5ca3`) showed a weak
model (Qwen3.6-35B-A3B) **inventing RML syntax** — template-embedded function
calls, a nonexistent `rml:transform` predicate, `rr:predicate a` — so T9 failed
on a parse error and the self-correction loop could never fix it, while the
model's *semantic* choices (column→predicate, which Tier-0 function) were
mostly sound. The ADR's answer is to stop asking the LLM for RML at all: §9
becomes a small closed-choice **mapping spec** (Mapping IR, YAML) and a
deterministic compiler emits the RML.

> **(a) Does compiling the IR reproduce exactly what the handwritten /
> production RML corpus expresses — same mapping graphs, same materialized
> triples? (b) Do the syntax error classes (unparseable Turtle, wrong FnO
> parameter IRIs, the legacy `fnml#` namespace, unexpanded CURIEs in
> templates) become structurally impossible? (c) Does the self-correction
> loop's feedback reach a granularity a weak model can act on?**

## 2. Method

All checks are deterministic and run in CI except the two marked *gated*
(they need `morph-kgc` installed; they run locally and are skip-guarded).

1. **Runtime probes (design inputs, then regression-pinned)** — three
   behaviors the compiler design leans on were probed against real Morph-KGC
   2.8.1 and turned into gated regression tests
   (`step0/tests/test_rml_compile_morph.py`):
   - are `rr:template` placeholders IRI-encoded by the engine?
   - does a nested `rmlf:functionExecution` (function inside a function's
     input) execute?
   - is a bare `rml:reference` + `rr:termType rr:IRI` object encoded?
2. **Golden graph-isomorphism** — IR transcriptions of representative
   handwritten RML compile to *isomorphic* mapping graphs
   (`rdflib.compare`, blank-node-safe): the e2e spike mapping
   (`experiments/phase5-morph-kgc-spike/e2e/mappings.rml.ttl`; CSV sources,
   1- and 2-input functions, datatypes, IRI-link templates) and an XML/XPath
   document-layer reference (iterator, constant subject, attribute refs,
   `structural_slug`).
3. **Materialization parity** (*gated*) — the IR transcription of the
   Materials Project mapping is materialized by real Morph-KGC over the real
   `mp.json` and compared as a triple **set** against the handwritten
   `datasets/materials_project/json/mp.rml.ttl` (the two bare-reference IRI
   objects the compiler refuses go through `fn:iri_safe`, identity on clean
   URLs).
4. **Structural elimination pins** — tests assert the new SYSTEM_PROMPT
   contains **zero RML syntax** (`rr:template`, `rmlf:`, parameter IRIs,
   namespaces, ` ```turtle `) and that the §9 function menu equals
   `asterism.functions.REGISTRY` exactly, in both directions
   (`test_propose.py`, `test_rml_check.py`, ingest's
   `test_prompt_registry_sync.py`). Parameter-IRI errors are unrepresentable:
   the IR names arguments (`args: {field: family}`) and the compiler binds
   IRIs from the registry-derived catalog.
5. **Full pipeline over the real production data shape** (*gated*) — the same
   Starrydata export the production failure used (papers/samples/curves; a
   CSV-safe 20-record subset), driven through the real
   `run_design_loop` with a scripted "weak model": round 0 emits an IR with
   three representative semantic mistakes (typo'd column `titel`, invented
   function `dateiso`, wrong constant-arg name `key`), round 1 the corrected
   IR. Then the final design goes through materialize → deterministic compile
   → the unchanged hard gates (`assert_rml_safe`, `validate_rml_design`) →
   real Morph-KGC materialization.
6. **Suites** — step0 319 / ingest 384 / api 199 tests green, `ruff` clean.

## 3. Result

| Check | Result |
|---|---|
| Probe: template placeholders | **Engine percent-encodes** (R2RML-conformant): spaces, quotes, `<>`, backslash, Unicode all yield valid IRIs (strict N-Triples round-trip). Raw data columns in templates are load-safe **without any wrapping** — and IRI-stable with existing template-based data. |
| Probe: nested functionExecution | **Works** — `transform: {col: slug}` compiles to `fn:template` with a nested `fn:slug` execution and materializes readable segments (`applied-physics-letters`). |
| Probe: bare reference as IRI | **Breaks the store** (`<http://x.org/a b>` — unencoded), the actual production "Invalid IRI code point" mole. Parser + compiler reject it and steer to `fn:iri_safe`. Bonus finding: the old HARD-RULES iri_safe guidance was itself semantically broken — `fn:iri_safe` (= `safe_url`) returns `""` on non-URL text, so following it on e.g. a composition column silently drops the row. |
| Golden: e2e spike mapping | **Isomorphic** (paper/sample/curve; functions incl. 2-input `float_array_count`). |
| Golden: XML document-layer | **Isomorphic** (iterator, constant subject, `{sec/@id}` child links, `structural_slug`). |
| Parity: Materials Project | **Triple set identical** to the handwritten mapping over the real `mp.json` (143 triples path; `iri_safe` identity on clean URLs confirmed). |
| Prompt pins | No RML syntax in the prompt; §9 menu == REGISTRY (31 functions), both directions. |
| Real-data pipeline (scripted weak model) | Round 0: **4 issues, all IR-granularity with did-you-mean** (`column 'titel' … Did you mean: title?`; `'dateiso' is not in the vetted Tier-0 set … date_iso?`; `json_pluck does not take a constant arg 'key'; it takes: field`). Round 1: **converged (0 issues)**. Hard gates pass. Real Morph-KGC: **777 triples** from 20-record subsets — including 111 `schema:author` literals via `json_pluck` on the real in-cell JSON author arrays and slug-derived shared periodical IRIs. Notably, the *old* contract could not express the periodical shared-node or author expansion correctly for weak models at all. |
| Syntax-class issues anywhere above | **0** — the categories `turtle` / `function-set` / parameter-IRI cannot occur on the IR path (they are unrepresentable, not merely unlikely). |

## 4. Conclusion

Deterministic compilation of the Mapping IR is **semantically equivalent** to
the existing handwritten/production RML corpus (mapping-graph isomorphism +
materialization parity) while making the weak-model-fatal **syntax error
classes unrepresentable** rather than just less likely. The self-correction
loop's feedback moves from "Bad syntax at line 20" to closed-menu,
did-you-mean messages a 3B-active model can plausibly act on. Everything
downstream of materialize (registry, ingest, the hard 422 gate, T9, UI) is
untouched and still gates the compiled output — the trust boundary is
unchanged.

## 5. Limitations

- Convergence still ≠ ingest-ready (the 422 gate remains the real gate);
  XML/XPath references are still not column-validated; erase-to-green is
  still only surfaced softly (`coverage_dropped`, now counted on IR property
  rows — it fired once in the addendum runs, see there).
- Equivalence was shown on the corpus shapes we have (CSV, tabularized JSON,
  XML skeleton). Shapes the IR deliberately cannot express (arbitrary nested
  function composition, joins) are absent from the corpus by design.
- Live runs are n=1 per final configuration and weak-model round-0 output has
  visible run-to-run variance (gpt-oss produced different §6-leak shapes on
  each run); the *syntax-class-zero* claim is structural (unrepresentable),
  but convergence-rate statistics would need repeated runs.
- Semantic quality remains the model's job: both converged designs carry
  imperfect key choices (e.g. `curve/{sample_id}` instead of the composite
  `SID-figure_id-sample_id`) and minted predicates where standard ones exist.
  The human gate reviews exactly this — now as a readable YAML table.

## Addendum (2026-07-06) — live weak-model measurement

Same day, with a Sakura AI Engine key: the harness ran the REAL models over
40-record CSV-safe subsets of the same Starrydata export as the production
failure (`dataset-548d5ca3`), `max_rounds=3`. Feedback gaps found by the first
run of each model were fixed and committed before its re-run — that iteration
is part of the result (the moles are now semantic and whackable in the
validator's vocabulary, instead of unfixable RML syntax).

| run | model | issues / round | outcome | gates on final design |
|---|---|---|---|---|
| qwen36 #1 | `preview/Qwen3.6-35B-A3B` | 6 → 4 → 4 → 4 | max_rounds (stall) | correctly **blocked** |
| qwen36 #2 | `preview/Qwen3.6-35B-A3B` | 1 → **0** | **converged**, 109.6 s | pass · **1388 triples** |
| gpt-oss #1 | `gpt-oss-120b` | 9 → 2 → 2 → 2 | max_rounds (stall) | correctly **blocked** |
| gpt-oss #2 | `gpt-oss-120b` | 7 → (refine truncated) | harness cap too low | correctly **blocked** |
| gpt-oss #3 | `gpt-oss-120b` | 7 → 7 → 1 → **0** | **converged**, 152.8 s | pass · **1468 triples** |

- **Syntax-class issues: 0 across all 17 validated rounds of all 5 runs** —
  no unparseable Turtle, no FnO parameter IRIs, no `fnml#`, no non-Tier-0
  function IRIs. The error class the old contract died on did not occur once.
  For scale: the same Qwen model + data under the old contract produced RML
  with **zero** correct `rmlf:functionExecution` uses and T9-fatal Turtle that
  no number of refine rounds could fix; now it reaches a materializable design
  in **109.6 seconds**.
- **Every stall was a §6/model.yaml habit or template micro-syntax leaking
  into the spec**, each answered with a targeted validator message the same
  day: `schema:author*` cardinality markers; a function piped into an
  `object_template`; a Jinja-style `{container_title|slug}` pipe filter
  (steered to `transform:`); a column referenced from another file's map
  (steered to "move the property"); an invented `optional: true` field
  (optionality is implicit). All five guidance messages are now unit-tested.
- **The gates held every time**: non-converged designs were blocked by
  `validate_rml_design` before Morph-KGC; converged designs passed
  `assert_rml_safe` + `validate_rml_design` and materialized for real.
- gpt-oss #2's truncation was a harness setting (a 16384-token cap is too
  tight for a reasoning model rewriting the full document), not a contract
  failure — the default 96 k cap is correct; the truncation guard kept the
  prior complete design as designed.
- gpt-oss #3 surfaced `coverage_dropped` (final 35 property rows after
  removing round-0's invented shapes — a legitimate reduction, honestly
  flagged). Function usage in the converged designs: Qwen 9 distinct Tier-0
  functions incl. a `slug` transform for the shared periodical node and the
  `…Raw` author fallback exactly as steered; gpt-oss 6 distinct + 1 fallback.
- Evidence: `experiments/mapping-ir-weakmodel-dogfood/results/` (per-run JSON
  records + the proposal / mapping spec / compiled RML artifacts).

## 6. Reproduce

```bash
# deterministic suites (CI-equivalent)
cd step0  && uv run pytest tests/ -q && uv run ruff check src tests
cd ingest && uv run pytest tests/ -q
cd api    && uv run pytest tests/ -q

# gated goldens + parity (needs morph-kgc: uv pip install -e 'ingest[substrate]')
cd step0 && uv run pytest tests/test_rml_compile_morph.py -v

# live weak-model dogfood (needs a key; writes JSON + artifacts for this report)
export SAKURA_API_KEY=...
step0/.venv/bin/python experiments/mapping-ir-weakmodel-dogfood/run_dogfood.py \
  --model preview/Qwen3.6-35B-A3B --api-base https://api.ai.sakura.ad.jp/v1 \
  --papers  ../starrydata_dataset/starrydata_papers.csv \
  --samples ../starrydata_dataset/starrydata_samples.csv \
  --curves  ../starrydata_dataset/starrydata_curves.csv \
  --rows 40 --materialize --out dogfood-qwen.json
# (gpt-oss-120b: same command with --model gpt-oss-120b; keep the default token cap)
```

## Addendum (2026-07-07) — Phase 2a: guided JSON surgical repair, live probe

ADR [`mapping-ir-phase2-guided-repair.md`](../architecture/mapping-ir-phase2-guided-repair.md)
landed: the autocorrect fix rounds now regenerate ONLY the §9 spec under a
JSON-Schema contract (Tier-0 menu as an enum, unknown fields and cardinality
suffixes unrepresentable), spliced back deterministically.

Live probe against real **gpt-oss-120b on Sakura AI Engine**: a spec carrying
ALL THREE observed invention families at once (`function: str` ×2,
`optional: true`, `sd:author*`) plus a typo'd column was repaired in **one
guided call** — functions dropped, field dropped, marker stripped, `titel` →
`title`, `json_pluck`+`args` preserved. The result passed the JSON Schema, the
strict validator (0 issues) and compiled. `last_notes: []` — **Sakura's vLLM
accepts `response_format: json_schema` natively** (no degrade), so guided
decoding is fully active on the production provider. Fix rounds drop from ~8k
to hundreds of output tokens, and the whole-document truncation failure mode
(gpt-oss run #2 above) is structurally gone.
