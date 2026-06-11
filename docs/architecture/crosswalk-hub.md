# ADR: Crosswalk hub — one thin, growing bridge across datasets

Status: **Runtime + per-link provenance productized** (2026-06-11). The pure builder
(`asterism.crosswalk`) now gains per-link provenance; a new I/O module
(`asterism.crosswalk_runtime`) reads the promoted canonical graphs, builds the hub, and
writes it back, driven by a persisted participation config (`crosswalk-bridge/
crosswalk.yaml`). api endpoints `POST /api/crosswalk/build`, `GET /api/crosswalk`, and a
key-gated `POST /api/crosswalk/propose` (AI-assist for the per-dataset predicate) author
it; promote rebuilds the hub inline and append self-heals it via a debounced background
rebuild. Validated live (215 shared compositions, per-link raws preserved, 2026-06-11).
Remaining: governance / versioning of the mapping; multi-concept + schema-level alignment.
Related: [`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)
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
   in the substrate. **Done**: `asterism.crosswalk` is a pure, tested, **multi-concept**
   builder (`CrosswalkConfig(concepts=…)`) and now records **per-link provenance**
   (each link's `xw:CrosswalkLink` node carries the *raw* string it normalized + the
   normalizer — the auditable join claim, default on); `asterism.crosswalk_runtime`
   reads the promoted canonical graphs (exact-graph, no draft leakage), builds the hub
   bounded to shared values (normalization 100% in Python), writes the graph + control
   flag, and persists the registry scaffold + the participation config
   (`crosswalk-bridge/crosswalk.yaml`). `POST /api/crosswalk/build` (config in body ⇒
   author; omitted ⇒ rebuild) is the product path; **promote rebuilds the hub inline**
   (the dataset just became citable) and **append schedules a debounced background
   rebuild** (a burst of device-feed batches coalesces into one — the append stays
   O(new)). Both idempotent (drop + replace), best-effort (never block).
3. **Multi-concept upper ontology** — accumulate shared *concepts* (Material,
   Property, Measurement, …) beyond Composition, including **schema-level alignment**
   (map a dataset class/property → an upper class/property via
   `owl:equivalentClass` / `rdfs:subPropertyOf`), so "all materials with property P"
   spans datasets regardless of local vocabulary. The library + config are
   multi-concept-ready (value-based sharing); the authoring UI starts with composition.
4. **Human-gated mapping review** (the mappings are claims) + versioning. *Partial*:
   building a config IS the vet gate (like saving a query tool); the per-dataset
   predicate is human-chosen (a dropdown, AI-suggested via `POST /api/crosswalk/propose`
   — key-gated, returns a draft only). Versioning of the mapping is future.
5. **UI**: **Done** — 作成 in "データを追加" (multi-select promoted datasets → pick each
   one's concept-bearing predicate, AI-assist optional → build) / 管理 in カタログ (a
   crosswalk surface: participants, "this composition is reported by N datasets", the
   hub tools, manual rebuild).

## Implementation

- **Library (tested, in-package):** `asterism.crosswalk` — `build_turtle(config,
  observations)` is pure (no I/O): mints one shared entity per normalized value
  shared by ≥ `min_datasets` datasets, emits crosswalk links + build provenance +
  (default on) a per-link `xw:CrosswalkLink` node (raw string + normalizer), for any
  number of concepts. Unit tests in `ingest/tests/test_crosswalk.py` cover
  normalization, the shared/singleton split, **growth**, multi-concept, build and
  **per-link** provenance.
- **Runtime (tested, in-package):** `asterism.crosswalk_runtime` — `build_hub(client,
  config)` resolves each participant's exact promoted graph (`canonical_graphs` /
  `live_graph_of`), does a two-pass bounded read (distinct values → shared keys →
  entities for shared raws), delegates the Turtle to `asterism.crosswalk`, and writes
  the hub graph + control `promoted` flag (so the FROM-merge unions it). `load/
  save_config` + `write_registry_scaffold` persist the participation config + the
  `crosswalk-bridge` dataset (seeding the generic `datasets_for_composition` tool only
  if absent, never clobbering authored tools). Tests in `test_crosswalk_runtime.py`
  (a real `rdflib.Dataset`) cover graph resolution / skip-unpromoted, the bounded read,
  the write, config round-trip, and the scaffold.
- **api:** `POST /api/crosswalk/build` · `GET /api/crosswalk` · `POST
  /api/crosswalk/propose` (AI-assist, `step0.crosswalk_propose`), plus the promote
  (inline) / append (debounced `CrosswalkRebuilder`) auto-rebuild hooks. Tested in
  `api/tests/test_crosswalk_api.py`.
- **CLI (manual):** `experiments/crosswalk-hub/build.py` is now a thin wrapper over
  `asterism.crosswalk_runtime` (`--default` bootstraps the demo-stack config,
  `--remove` tears the hub down). Run with the ingest venv.
