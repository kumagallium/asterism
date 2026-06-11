# ADR: Multi-perspective crosswalk — the upper ontology is plural

Status: **Phase 1 (runtime/api) + Phase 2 (schema alignment runtime+api) + Phase 3
(UI: perspective list/create + the "視点をつなぐ" alignment surface) done** (2026-06-11).
Extends [`crosswalk-hub.md`](crosswalk-hub.md) (the thin growing bridge). Related:
[`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md) (2-axis
TBox/ABox × draft/canonical), [`product_direction_citable_facts`](../../README.md).

## Context

A single dataset is defined by **one** ontology (its own schema). The *cross-dataset*
upper concept, though, is **not single**: datasets relate through **many viewpoints** —
the same materials join by **composition**, by **crystal structure**, by **measurement
conditions**, by **provenance**, … Each viewpoint is a different lens for relating the
same datasets.

The crosswalk hub (`crosswalk-hub.md`) already mints shared entities and is
*multi-concept-capable* in the library, but it is operated as a **singleton**: one
registry dataset (`crosswalk-bridge`), one canonical graph (`…/graph/canonical/
crosswalk`), one config, one `composition` concept. Forcing every cross-dataset viewpoint
into that one hub conflates viewpoints that are conceptually independent and may never
relate — the same "boil-the-ocean ultimate ontology" anti-pattern `crosswalk-hub.md`
already rejects, one level up.

## Decision

The upper ontology is **plural and modular**: a growing **set of distinct crosswalk
PERSPECTIVES**, each independently defined and held, with **deferred, additive merging**
when two perspectives are found to relate.

- **A perspective is its own crosswalk.** It has an id, a human name, its **own
  canonical named graph** (`…/graph/canonical/crosswalk/<id>`), its **own config**
  (participants + concept(s) + normalizer), its **own registry entry**, and its own
  build provenance. A perspective can hold ≥ 1 concept, but is typically one coherent
  lens.
- **Perspectives coexist, distinctly.** Each is a separate graph you can independently
  build / rebuild / retract / inspect. The **FROM-merge unions every promoted
  perspective graph** (as it already unions dataset + crosswalk graphs), so a query can
  traverse two perspectives' links on the **same dataset-entity IRI** — **cross-perspective
  joins work for free**, with the engine unchanged.
- **Merging is later, additive, and reversible.** When two perspectives are found to
  relate, the relationship is **asserted as new triples** in a dedicated *alignment*
  graph — never by destroying either perspective:
  - **Schema-level**: `owl:equivalentClass` / `rdfs:subPropertyOf` between the
    perspectives' classes / link predicates (e.g. `xw_a:Composition ≡ xw_b:Material`).
  - **Entity-level**: the perspectives' shared entities coincide (`owl:sameAs`).
  This is human-gated and removable; each perspective stays independently inspectable.
  "Merge" means the perspectives become navigable as one **once the line is drawn** —
  the line is added, the stars are not melted together.

### Why this is consistent with the philosophy

- **Lines between stars, not a new sun — recursively.** `crosswalk-hub.md` drew lines
  between datasets; this draws lines between *perspectives*. The upper ontology grows as
  a set of independent lenses + the lines later found between them, never as a top-down
  monolith.
- **Additive + monotonic.** Adding a perspective adds a graph; merging two adds
  alignment triples. Nothing existing is rewritten; everything is reversible.
- **Each perspective is a derived, dated, citable claim** (it already records a
  `prov:Activity`); a perspective can be retracted/rebuilt without touching the others.

## Consequences / risks

- **More graphs** (one per perspective). The FROM-merge enumeration is already
  O(#graphs) (control-graph promoted flags, no triple scan — `crosswalk-hub.md` perf
  note), so this scales.
- **Merging is a claim that must be vetted** (like a normalizer or a mapping). It is
  human-gated and lives in its own graph so it can be reviewed / withdrawn.
- **Premature merging is the risk to avoid** — the whole point is to *not* force a
  relationship. Default is distinct; merging is opt-in.

## Phased plan

1. **Phase 1 — runtime generalization (this ADR's first deliverable).** Generalize the
   singleton crosswalk to **N named perspectives** in `asterism.crosswalk_runtime` +
   the api: a perspective is identified by id, lives in `…/graph/canonical/crosswalk/
   <id>`, has its own config + registry entry. `GET /api/crosswalks` lists them;
   `GET /api/crosswalk/{id}` + `POST /api/crosswalk/{id}/build` operate one. The
   existing `composition` perspective (registry `crosswalk-bridge`, legacy graph
   `…/graph/canonical/crosswalk`) is preserved **unchanged** (back-compat), and the
   no-id endpoints (`GET /api/crosswalk`, `POST /api/crosswalk/build`) keep operating it
   so the current UI keeps working. promote / append auto-rebuild iterate **every**
   perspective the dataset participates in.
2. **Phase 2 — merge / alignment (SCHEMA-level; runtime + api done).** A human-gated
   step asserts a **schema-level** relationship from a CLOSED set
   (`owl:equivalentClass` / `rdfs:subClassOf` / `owl:equivalentProperty` /
   `rdfs:subPropertyOf`) between two perspectives' concept terms (classes / link
   predicates), into a dedicated, promoted **alignment graph** (`…/graph/canonical/
   crosswalk/alignment`) the FROM-merge unions. Each assertion carries a dated
   provenance node so it can be listed + withdrawn (additive, reversible). `asterism.
   crosswalk_runtime.assert_alignment` / `list_alignments` / `remove_alignment`; api
   `POST /api/crosswalk/align` (+`remove`) / `GET /api/crosswalk/alignments`.
   **Caveat (no reasoning):** Oxigraph does not run an OWL reasoner, so an alignment is
   a human-vetted, **citable, queryable FACT a tool can follow** — it is NOT
   auto-applied to rewrite queries (consistent with the deterministic / citable product
   direction). Entity-level (`owl:sameAs`) alignment is a possible later addition.
3. **Phase 3 — UI (done).** The catalog "クロスウォーク" surface is a **list of
   perspectives** (each viewable / rebuildable); authoring creates a **new named
   perspective**; a **"視点をつなぐ"** section drives Phase 2's alignment: pick two
   perspectives' terms (a concept class or its link predicate) + a relation from the
   CLOSED set, assert (`POST /api/crosswalk/align`), and list/withdraw existing
   alignments (`GET /api/crosswalk/alignments`, `remove`). The relation set is filtered
   by term KIND (class relations for a class, property relations for a predicate) and
   the target term must match the source's kind, so a class can only align to a class.
   `crosswalkApi.getAlignments` / `align` / `unalign`; `CrosswalkView`'s
   `PerspectiveAlignment`.

## Implementation (Phase 1)

- **Perspective identity.** A perspective's id is a slug; its graph is
  `crosswalk_graph_iri(id) = …/graph/canonical/crosswalk/<id>` and its registry id is
  `crosswalk-<id>`. Perspectives are discovered by the `is_crosswalk` meta flag (so no
  naming convention is load-bearing), each meta recording `crosswalk_perspective_id` +
  its `canonical_graph`. The **legacy** composition perspective keeps its existing
  registry id (`crosswalk-bridge`) + graph (`…/graph/canonical/crosswalk`) — it is just
  one perspective among N, with `perspective_id = "composition"`.
- **Engine unchanged.** Each perspective graph is flagged `promoted` in the control
  graph, so the FROM-merge unions it exactly as it unions dataset graphs; cross-perspective
  queries need no new machinery.
- **Build / read** reuse `asterism.crosswalk.build_turtle` (pure, multi-concept,
  per-link provenance) per perspective; only the target graph + registry entry differ.
