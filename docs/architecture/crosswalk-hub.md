# ADR: Crosswalk hub — one thin, growing bridge across datasets

Status: **Builder is a tested package module** (`asterism.crosswalk` — pure,
multi-concept, provenance; 2026-06-10) validated on live data (composition,
2026-06-09). Remaining productization (runtime wiring on promote, per-link
provenance, governance, UI) below. Related: [`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)
(2-axis TBox/ABox × draft/canonical, dataset decoupling), [`design-rationale.md`](design-rationale.md),
[`product_direction_citable_facts`](../../README.md) (deterministic typed tools are the main path).

## Context

Cross-dataset questions are first-class ("crystal structure × ZT" spans Materials
Project + starrydata). Today they work only by **raw value equality** in a query
(`sd:compositionString == mp:formula`), which is fragile: `Bi2Te3` / `Bi₂Te₃` /
`Bi2 Te3` / `ZnFe2O4 ` are the "same" composition but not byte-equal, and a
cross-dataset tool has no natural home (it belongs to neither source dataset).

Two non-answers:

- **A field-wide "ultimate ontology"** (model everything top-down) — boil-the-ocean,
  brittle, never finished. Rejected (and against the dataset-decoupling of
  `ontology-canonical-lifecycle.md`).
- **Pairwise bridges** (A↔B, A↔C, B↔C …) — N² and they don't compose.

## Decision

A **thin, purpose-scoped crosswalk HUB** that **grows** as datasets are added.

- **Two terms only**: `xw:Composition` (a normalized composition shared across
  datasets) and `xw:hasComposition` (links a dataset entity — a sample, a material
  — to it). Namespace `…/asterism/crosswalk/ontology#`.
- **A registry of participation rules**: each dataset declares "my `<predicate>`
  carries a composition string" (`Rule(label, predicate, graph)`). Adding a dataset
  = appending a rule.
- **Build = hub over the union**: collect every participating dataset's distinct
  *normalized* compositions; mint **one** `xw:Composition` per composition shared by
  **≥ 2** datasets (singletons add no cross-dataset value); link each dataset's
  entities to that shared IRI. The hub is one canonical named graph
  (`…/graph/canonical/crosswalk`) the FROM-merge already unions in — **engine
  unchanged**.
- **Cross-dataset tools live in the hub** (a `crosswalk` registry dataset's
  `query_tools.yaml`), not under either source dataset, because they belong to the
  *join*. They are ordinary verified tools: deterministic, citable, **key-free**.
- **Provenance**: the hub is a *derived dated claim*. Each build records a
  `prov:Activity` (participating datasets, normalization id, time); each
  `xw:Composition` is `prov:wasGeneratedBy` it.

### It grows (the point)

N datasets map into **one** hub, not N² bridges. A composition the new dataset
shares with any existing one becomes a shared entity, and the new dataset's entities
link into the **existing** IRI. Monotonic, additive.

Validated live (2026-06-09): hub over `[starrydata, materials_project]` = **7**
shared compositions; adding `thermoelectric_demo` → **215** shared compositions in
the *same* hub graph. `Bi2Te3` → {starrydata, materials_project}; `Ba8Ga16Ge30` →
{starrydata, thermoelectric_demo}. The IRI join also caught 16 samples raw string
equality missed (whitespace/subscript variants). The hub tool
`zt_by_crystal_structure` returns the ZT × space-group join (PbTe Fm-3m Cubic ZT
2.81 …) key-free.

## Why this is consistent with the philosophy

- **Lines between stars, not a new sun.** The hub is the *lines*; it does not
  re-model the datasets.
- **Additive + one-directional** (bridge → datasets). starrydata / MP / … never
  depend on the hub or change — preserves the decoupling of
  `ontology-canonical-lifecycle.md`.
- **Cross-dataset answers become first-class verified tools** (deterministic /
  citable / key-free), which is the structural fix for "cross-dataset reproducibility
  is weak": the bridge IRI is a stable, vetted join key.

## Consequences / risks

- **Normalization is the join key** and therefore a *claim that must be vetted*. The
  prototype folds unicode subscripts + strips whitespace (`fold-subscripts+strip-
  whitespace/v1`). Over-aggressive normalization could wrongly merge distinct
  compositions — keep it conservative; promote richer rules (element-canonical
  ordering, so `Bi2Te3 ≡ Te3Bi2`) only as a vetted **Tier 0 function**.
- The hub is **derived data**: it needs governance — a human-reviewed mapping gate
  and versioning, like any verified tool. (Today's prototype rebuilds idempotently.)
- The hub bounds itself to **shared (≥2)** compositions to stay small and
  join-relevant; a full mint (every composition) is possible but heavier.

## Productization path

1. **Deterministic normalization as a Tier 0 function** (`asterism.functions`), so
   the join key is vetted, versioned, and reusable. *Partial*: the builder uses
   **named normalizers** (`asterism.crosswalk.NORMALIZERS`, e.g. `composition`); an
   element-canonical key (`Bi2Te3 ≡ Te3Bi2`) is a future, separately-vetted one.
2. **`crosswalk` as a first-class concept** — a participation registry + a build step
   in the substrate. **Done (library)**: `asterism.crosswalk` is a pure, tested,
   **multi-concept** builder (`CrosswalkConfig(concepts=…)`); a concept is a shared
   class + link predicate + per-dataset rules + a normalizer. **Remaining**: wire a
   substrate build step (re-run on dataset promote) and **per-link provenance** (this
   link was normalized from *this* raw string by *this* function — today provenance
   is per build Activity).
3. **Multi-concept upper ontology** — accumulate shared *concepts* (Material,
   Property, Measurement, …) beyond Composition, including **schema-level alignment**
   (map a dataset class/property → an upper class/property via
   `owl:equivalentClass` / `rdfs:subPropertyOf`), so "all materials with property P"
   spans datasets regardless of local vocabulary. The library is multi-concept-ready
   (value-based sharing); schema alignment is the next layer.
4. **Human-gated mapping review** (the mappings are claims) + versioning.
5. **UI**: create/manage the crosswalk (which datasets/concepts participate, review
   mappings), and a catalog surface ("this composition is reported by N datasets").

## Implementation

- **Library (tested, in-package):** `asterism.crosswalk` — `build_turtle(config,
  observations)` is pure (no I/O): mints one shared entity per normalized value
  shared by ≥ `min_datasets` datasets, emits crosswalk links + build provenance, for
  any number of concepts. Unit tests in `ingest/tests/test_crosswalk.py` cover
  normalization, the shared/singleton split, **growth** (adding a dataset grows the
  shared set), multi-concept, and provenance.
- **Spike (runtime I/O):** `experiments/crosswalk-hub/build.py` reads the live store
  (read-only api FROM-merge), **delegates Turtle construction to `asterism.crosswalk`**,
  and writes the hub graph + control flag + the `crosswalk` registry dataset + tools.
  Runtime only (the hub data lives in Oxigraph). Run with the ingest venv.
