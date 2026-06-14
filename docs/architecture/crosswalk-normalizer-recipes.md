# ADR: Crosswalk normalizers — a closed, vetted library that grows by curation

Status: **Increment 1 (generic text core) + Increment 2 (declarative recipes:
runtime + api + UI composer) done; compound keys (increment 3) are future**
(2026-06-14). Extends [`crosswalk-hub.md`](crosswalk-hub.md) (the thin growing
bridge) and [`crosswalk-multi-perspective.md`](crosswalk-multi-perspective.md) (the
plural upper ontology). Aligns with the product direction (deterministic, citable,
human-vetted facts) and the **no-generated-code-execution** invariant (`CLAUDE.md`,
[`ingestion-execution-safety.md`](ingestion-execution-safety.md)).

## Context

A crosswalk joins datasets on a **normalized value**: each concept names a *normalizer*
(the join key) and the hub mints one shared entity per normalized value reported by
≥ 2 datasets. The normalizer is the thing that decides **"these two raw strings denote
the same concept instance."**

Two pressures collide:

- **Generality.** The hub is meant for *any* shared concept (composition, crystal
  system, author, country, material name…), not just materials. So the join key must
  not be materials-only.
- **Safety.** Asterism executes **no generated code**: transforms reference a *closed,
  vetted function library* (the Tier-0 model). So a normalizer can NOT be arbitrary
  user code evaluated at runtime.

The tension surfaced as: *if the only way to add a normalizer is a human PR, isn't that
an unbounded "keeps-adding-forever" problem? And if the declarative alternative can't
express a complex normalizer like the composition reorder, is it pointless?*

### What a normalizer actually is

A normalizer is not merely a string transform — it is an **identity claim about the
world**: "`Bi2Te3`" and "`Te3Bi2`" *are the same compound*. A wrong claim silently
**merges distinct entities** and corrupts the graph (a precision failure, the worst
kind here). That is why a normalizer must be **human-vetted**, exactly like an ontology
alignment ([`crosswalk-multi-perspective.md`](crosswalk-multi-perspective.md) §Phase 2)
or a mapping. The "no code execution" rule is, for normalizers, a proxy for "every
identity claim is reviewed."

### Can the existing normalizers be expressed declaratively?

Inspected against the real code (`asterism/crosswalk.py`):

- `normalize_composition` = `fold-subscripts → strip-whitespace` — **trivially a 2-step
  recipe** of stateless primitives.
- `normalize_identity` = `trim` — 1 primitive.
- `normalize_element_canonical` = fold/strip, then **tokenize by a chemistry regex,
  validate every token against the 118 IUPAC element symbols, conditionally fall back
  when the string is not a clean formula, then sort the parsed (element, count) pairs.**
  This is a **small program with domain knowledge and conditional control flow** — it
  does *not* decompose into generic stateless string folds. A naive "sort tokens"
  primitive without the element-validation guard would wrongly merge non-formulas.

So a recipe of generic primitives covers the simple/medium cases but **cannot** express
the genuinely complex, domain-knowledge-bearing ones. That is real, not a tooling gap.

## Decision

Normalizers are a **two-tier closed library that grows by curation, never by runtime
code**:

1. **Vetted functions (compiled, human-PR + tests).** Each is a named function in
   `asterism.crosswalk.NORMALIZERS`. Complex, domain-knowledge normalizers
   (`composition`, `element_canonical`, future stoichiometry / SMILES / gene-symbol …)
   live here. They are **not** user-addable at runtime — and that is correct, because
   they are reviewed identity claims. This is identical to how Tier-0 ingestion
   functions are extended.
2. **A generic text core (this increment).** A small set of domain-neutral, stateless
   normalizers that cover the long tail of *non-materials* concepts: `casefold`,
   `whitespace` (collapse), `nfkc`, and `loose_text` (NFKC + casefold + collapse),
   alongside the existing `identity`. Each folds exactly one well-understood text
   variation and **never reorders or drops tokens**, so distinct strings stay distinct.
3. **Recipes (done — user-composable in the UI).** A declarative, ordered composition of
   closed primitives (`asterism.crosswalk.RECIPE_PRIMITIVES`: `nfkc` / `casefold` /
   `strip` / `collapse_ws` / `remove_ws` / `fold_subscripts`), authored and saved with
   the perspective like a typed query tool — **data, not code**. A concept carries a
   `normalizer_recipe` (ordered ids); `resolve_normalizer` prefers it over the named
   normalizer and `apply_recipe` folds the closed set (an unknown id is rejected — the
   safety gate). The build records the recipe spec in provenance (`xw:normalizer
   "recipe(nfkc>casefold>collapse_ws)"`). Recipes **compose** the closed set; they do
   not let users *write* new complex logic — a domain normalizer like `element_canonical`
   would join as a single vetted primitive (not yet exposed as a recipe step). A recipe
   can reproduce the simple named normalizers (`loose_text` = `nfkc>casefold>collapse_ws`,
   `composition` = `fold_subscripts>remove_ws`); only the domain-knowledge ones cannot.

### Why a closed, curated library is enough (the "keeps-adding" answer)

- **Demand is power-law, not uniform.** A few generic normalizers cover the long tail
  of any concept; a few domain normalizers cover each *active* domain. The number of
  domains a deployment actually uses is small and grows slowly. (`composition` is reused
  by *every* materials dataset — Starrydata, Materials Project, the demo — one function,
  N datasets: O(functions), not O(datasets).)
- **Shared + amortized, like QUDT units or an ontology vocabulary.** A normalizer is
  written and reviewed once, then reused forever in a public repo. Curated catalogs of
  this shape are a proven way to scale; they do not exhaust.
- **Bespoke quirks go UPSTREAM, not into the catalog.** A one-off vendor quirk (a weird
  dopant notation) is handled at **ingestion** (declarative Tier-0 cleaning) so the
  stored value is already canonical, then joined with `identity`. The normalizer catalog
  only needs the *shared* normalizations.
- **Graceful degradation — missing ≠ broken.** The default is the conservative
  `identity`, so a missing normalizer costs **recall** (fewer joins), never **precision**
  (wrong joins). You are never blocked or corrupted by an incomplete catalog; coverage
  grows incrementally. This is what makes a finite catalog acceptable in practice.
- **AI drafts, humans review.** A candidate normalizer/recipe can be LLM-drafted (like
  tool `propose`), shrinking the human cost to *review*. A genuinely new compiled
  function still needs code review (it is an identity claim), which is the point.

## Consequences / risks

- The complex-normalizer catalog is **human-curated** — adding `element_canonical`-class
  functions is a reviewed PR, by design. That cost is the cost of vetting an identity
  claim; it is irreducible and consistent with the product thesis.
- The generic core can be **misapplied** (e.g. `casefold` on a composition would merge
  `Co`/`CO`). Mitigated by clear UI labels (材料向け vs 汎用) and the concept-driven
  default (composition → `composition`, else → `identity`); the human picking it is the
  vet step.
- Recipes (tier 3) help the medium tail but cannot do the complex cases — they compose
  the closed set, they don't replace vetted functions. Still lower-leverage than the
  generic core + ingestion-time cleaning, but they remove the "we must code every combo"
  bottleneck for the long tail.

## Increment plan

1. **Generic text core (done).** `casefold` / `whitespace` / `nfkc` / `loose_text` added
   to `NORMALIZERS` (+ unit tests) and surfaced in the crosswalk builder, grouped
   汎用 vs 材料向け with per-option hints. Non-materials concepts get a sensible join key
   without any new code.
2. **Recipes (done).** A declarative recipe model + an authoring UI that composes the
   closed primitives. `RECIPE_PRIMITIVES` + `apply_recipe` + `resolve_normalizer` in
   `asterism.crosswalk`; a concept's `normalizer_recipe` round-trips through
   `parse_config` / `config_to_dict` (unknown primitive rejected); both normalization
   sites (runtime bounded read + pure `build_turtle`) use the resolver. api:
   `GET /api/crosswalk/normalizer/primitives` + `POST /api/crosswalk/normalizer/preview`
   (pure compute, closed-set gate). UI: a composer in the crosswalk builder
   (add/reorder/remove steps + a live preview of the join key via the preview endpoint),
   sent as part of the perspective config (build = the vet gate). Verified live: a recipe
   perspective joined values by the recipe (lowercased keys) with the recipe recorded in
   provenance.
3. **Compound keys (designed, not implemented).** Joining on more than one attribute at
   once ("same composition AND same crystal system") — a multi-attribute key the hub does
   not model today (each concept joins on one value independently). Design:
   [`crosswalk-compound-keys.md`](crosswalk-compound-keys.md).
