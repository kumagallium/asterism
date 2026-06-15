# ADR: Crosswalk compound keys — joining on more than one attribute at once

Status: **Done — Phase 1a (pure builder) + 1b (runtime + config) + API + UI** (2026-06-14).
A compound key is authorable end-to-end: the crosswalk builder lets a human add extra
"一致条件 (AND)" parts (each with its own normalizer + per-dataset predicate), and the
build sends a `key_parts` config the runtime tuple-joins. Verified live (Oxigraph). Extends
[`crosswalk-hub.md`](crosswalk-hub.md) (the thin growing bridge) and
[`crosswalk-multi-perspective.md`](crosswalk-multi-perspective.md) (plural
perspectives). Sibling of [`crosswalk-normalizer-recipes.md`](crosswalk-normalizer-recipes.md)
(increment 3 of that roadmap). Honors the **no-generated-code-execution** invariant
and the deterministic / citable product direction.

## Context

A crosswalk concept currently joins datasets on **one** normalized value: the builder
mints one shared entity per normalized value of a single concept-bearing predicate,
present in ≥ `min_datasets` datasets (`crosswalk.build_turtle`). That is exactly right
for "same composition" or "same author".

But some cross-dataset identities are **conjunctive** — two records denote the same
thing only when **several** attributes coincide:

- *same composition **AND** same crystal system* (a material's *phase* — `PbTe`
  rocksalt ≠ `PbTe` under high pressure);
- *same gene **AND** same organism*; *same place **AND** same year*; …

Today you cannot express this. You can make a `composition` perspective and a
`crystal_system` perspective, but each joins independently — there is no shared entity
that means "the (composition, crystal-system) pair both datasets report." The
`crosswalk-multi-perspective.md` ROADMAP names this gap explicitly ("compound keys,
design-first").

### Why not just two perspectives + alignment

Schema alignment (`crosswalk-multi-perspective.md` §Phase 2) relates *terms* (classes /
predicates), not *value tuples*. Two independent perspectives joined by alignment still
mint **separate** shared entities per single value; nothing represents the *pair*. A
compound key is an **entity-level** construct: one minted entity per shared tuple.

## Decision

**Generalize a concept from one join value to an ordered list of KEY PARTS; the join key
is the TUPLE of the parts' normalized values (conjunctive — all parts must match).**

A single-part concept is the current behavior, so this is a backward-compatible
generalization, not a parallel mechanism.

- **A concept has `key_parts` (≥ 1).** Each part has a `name`, a `normalizer` (named or a
  recipe — `crosswalk-normalizer-recipes.md`), and per-dataset predicates. The legacy
  single-predicate concept is the 1-part case.
- **The join key is the tuple** `(normalize₁(v₁), …, normalizeₙ(vₙ))` gathered **from the
  same source entity**. Two dataset entities coincide iff every part's normalized value
  is equal. "Shared" = a tuple present in ≥ `min_datasets` datasets.
- **One minted entity per shared tuple**, IRI `…/resource/<concept>/<k₁>~<k₂>~…`
  (parts percent-encoded, joined by a reserved delimiter), `rdfs:label` the human tuple.
  Each dataset entity links to it (`<link_predicate>`); per-link provenance records the
  **raw** tuple + each part's normalizer (auditable, like today).
- **Engine unchanged downstream.** The compound entity is an ordinary hub entity in the
  perspective's promoted graph; the FROM-merge unions it and cross-dataset queries join
  on it for free — only the *build* changes.

### Config shape (generalization, back-compat)

```yaml
concepts:
  - name: phase
    class_iri: xw:Phase
    link_predicate: xw:hasPhase
    key_parts:                       # NEW (ordered)
      - name: composition
        normalizer: composition
      - name: crystal_system
        normalizer: identity
    participants:
      - dataset_id: starrydata
        predicates: {composition: sd:comp, crystal_system: sd:cryst}
      - dataset_id: materials_project
        predicates: {composition: mp:formula, crystal_system: mp:spacegroup}
```

Back-compat: a concept with the existing `normalizer` + per-participant `predicate`
(string) is read as a **single** key part named after the concept — current configs and
the current builder keep working untouched.

## The build-model change (the hard part)

This is the one place that genuinely changes, and the reason this is design-first.

1. **Per-entity, multi-predicate gather.** Today the runtime reads *distinct values per
   predicate* independently. A compound key needs, **for each source entity**, the value
   of **every** part. So the bounded read becomes: per dataset, fetch `(entity, v₁, …, vₙ)`
   for entities that have all parts (a single SELECT with one `OPTIONAL`-free join over the
   part predicates, or n reads keyed by entity then aligned in Python).
2. **Require all parts present.** An entity missing any part cannot form a complete tuple,
   so it does not participate (conservative — never invent a wildcard match). `log` how
   many were dropped for missing parts (no silent truncation).
3. **Multi-valued parts → cross product.** If an entity reports several compositions and
   several crystal systems, it yields the **cross product** of tuples (each combination is
   a candidate key, exactly as the single-part model already mints one entity per value).
   Bound the explosion: cap tuples-per-entity and `log` when the cap trims (a real risk —
   document it, don't fail silently).
4. **Bucket by tuple; shared = tuple in ≥ `min_datasets`.** Then mint + link exactly as
   the single-part builder does, with the tuple as the key.

`build_turtle` stays pure and testable: its observation input generalizes from
`(concept, dataset) -> [(entity, raw)]` to `(concept, dataset) -> [(entity, raw_tuple)]`,
and the normalizer applies per-part. The runtime's two-pass bounded read is what carries
most of the new logic.

**Phase 1a (done — the pure builder).** `asterism.crosswalk` now has `KeyPart` and
`Concept.key_parts` / `Concept.parts()`; `build_turtle` builds tuple keys (each part
normalized; the join key joins parts with a collision-safe unit separator `\x1f`, with a
readable `" | "` label + provenance). A single-part concept is **byte-identical** to
before (key == value, same IRIs / labels / provenance) — verified by the unchanged
existing tests plus new compound tests (tuple join, per-part normalization, arity-mismatch
skip). The runtime still builds single-part concepts, so `key_parts` is **dormant until
1b wires the store-gather** — no config path can yet produce a wrong compound hub.

## Consequences / risks

- **Core join logic changes** (`build_turtle` + the runtime bounded read) — the highest
  blast-radius area of the system. Hence: implement behind the back-compat single-part
  path, with extensive unit tests (single-part unchanged; 2-part join; missing-part drop;
  multi-valued cross product; cap) **before** any UI.
- **Cross-product explosion** is the real foot-gun (m compositions × k crystal systems).
  Cap + `log`; the human authoring the compound key is the vet step.
- **More minted entities** (one per shared tuple) than a single-part concept — still
  O(shared tuples), and the FROM-merge enumeration is unchanged (per-graph, not per-triple).
- **A compound key is still one human-vetted CLAIM** ("these attributes together identify
  the same thing"), consistent with the product thesis; it is built (= vetted), dated, and
  retractable like any perspective.
- **UI complexity**: the builder must let a human add N key parts (each: name + normalizer
  / recipe) and map each part to a per-dataset predicate. Larger than the single-value
  builder — a later increment, after the runtime is proven.

## Phased plan

1a. **Pure builder (done).** `KeyPart` + `Concept.key_parts` / `parts()`; `build_turtle`
   tuple keys (per-part normalization, collision-safe key, readable label + provenance);
   single-part byte-identical. Unit tests (back-compat + tuple join + per-part norm +
   arity-mismatch skip).
1b. **Runtime store-gather + config (done).** `RuntimeKeyPart` + `RuntimeConcept.key_parts`
   + `RuntimeParticipant.predicates` (per-part; single-part back-compat); `parse_config` /
   `config_to_dict` round-trip them (a compound participant missing a part's predicate is
   rejected). `build_hub` branches: legacy single-part path UNCHANGED; a compound concept
   gathers per-entity tuples via `_entity_tuples` (`SELECT ?e ?v0 ?v1 …` inner-joined over
   the part predicates — an entity missing any part never enters; multi-valued = SPARQL
   natural join = cross product; capped per entity at `_COMPOUND_TUPLE_CAP=256` with a
   `log`), buckets by the normalized tuple, and feeds tuple observations to the pure
   builder. Tested against the real-store harness (tuple join; missing-part exclusion;
   config round-trip + validation) and smoke-built live against Oxigraph.
2. **API (done by 1b).** The build endpoint accepts the generalized config for free (it
   just `parse_config` → `build_hub`) — verified live (compound config → 200, persisted
   with `key_parts`). `propose` stays single-concept for now.
3. **UI (done).** The crosswalk builder keeps the single-value flow as default and adds
   an **"追加の一致条件 (AND)"** section: add/remove extra parts, each with a name + a
   normalizer + a per-dataset predicate. With ≥ 1 extra part the build sends `key_parts`
   + per-participant `predicates`; with none it sends the legacy single-value config
   (byte-identical). v1 limitation: extra parts use a named normalizer (the recipe
   composer stays on the primary part). `CrosswalkView` shows compound concepts
   ("複合キー（a × b）") + per-part predicates.
4. **(Optional) cross-perspective compound** via alignment — out of scope here.

## Alternatives considered

- **Concatenate parts into one string upstream, then single-value join.** Rejected: it
  buries the structure (you can't query "by composition" alone), the concatenation is
  ad-hoc per dataset, and it pushes a join decision into ingestion. The tuple stays
  structured and the parts remain independently inspectable.
- **Two perspectives + entity-level `owl:sameAs`.** Rejected for the conjunctive case: it
  asserts identity per single value, not per tuple; the pair is never represented.
