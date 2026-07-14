# Instance IRI base: who owns a newly minted namespace

Status: accepted (2026-07-14)

## Problem

A minted IRI is the permanent identity of a published fact — its domain says
*who answers for this identifier*. Two failure modes were live in production:

1. **AI-designed datasets minted `example.org`.** The design prompts never said
   where new namespaces should live (the Mapping IR examples show `https://…/`
   ellipses), so models fell back to the placeholder habit — a real XRD
   dataset shipped `https://example.org/xrd-ontology#` in cited answers.
   `tool_propose` has carried a "NEVER invent a placeholder namespace" rule
   since #260; the *design* path — the one that actually mints — was unguarded.
2. **Every install mints under the author's domain.** The bundled example
   datasets correctly live under `https://kumagallium.github.io/asterism/…`
   (declared in their `dataset.toml`; pinned immutable by CLAUDE.md). But a
   third-party lab's Asterism has no business minting ITS data there: the
   domain owner never answers for those identifiers, and two labs' slugs can
   collide inside one namespace they neither control.

Related but distinct: instance IRIs are not yet HTTP-dereferenceable (the
documented "phase 1" of `docs/starrydata/resource/index.html`). Resolution is
a convenience layered on identity; this ADR fixes identity. A custom Pages
`404.html` now explains an identifier to humans who click one.

## Decision

Split namespaces into two layers with different owners:

- **Engine vocabulary** (`…/asterism/fn/`, `…/asterism/vocab#`, crosswalk,
  graph bases, bundled datasets): product-owned, identical on every install —
  that shared identity is what makes cross-instance reuse work. Unchanged.
- **Instance data namespaces** (every NEWLY designed dataset): owned by the
  operator, set once per install via **`ASTERISM_IRI_BASE`**. Designs mint
  `<base>/datasets/<slug>/ontology#` and `<base>/datasets/<slug>/resource/`
  (the `datasets/` segment keeps slugs out of the engine's path space).

Mechanics (all deterministic-side; the model is *told*, then *checked*):

1. **Prompt injection** — `dataset_namespace_block(iri_base)` rides the USER
   message of skeleton and single-shot round-0 (`build_skeleton_user`,
   `propose_schema`; API threads `Settings.iri_base`, the CLI reads the env).
   System prompts stay byte-stable (#244 caching pattern).
   `render_skeleton_context` now lists the gated skeleton's prefixes so the
   per-map stage reuses settled namespaces instead of minting.
2. **Policy gate** — `placeholder_prefix_issue` flags RFC 2606 example
   domains + localhost in a generated spec's `prefixes`. It runs in
   `design_loop._collect_ir_issues` (the AI-design pipeline) so the
   self-correction loop re-mints; it is deliberately NOT in
   `parse_mapping_ir`: `example.org` is legal RDF and standard in hand-written
   fixtures — only *generated designs* are held to the minting policy.
   Materialize of pre-existing designs is not blocked (fix by re-design).
3. **Unset default** — `https://asterism.invalid`. RFC 2606 guarantees
   `.invalid` never resolves, so an unconfigured install's IRIs are
   self-describing ("no published home yet") instead of squatting on
   kumagallium's or example.org's namespace. Trial use works with zero
   config; publishing is the moment to set a real base. The gate never flags
   `.invalid`.

## Consequences

- IRI immutability holds: changing `ASTERISM_IRI_BASE` affects only designs
  created afterwards; nothing rewrites existing graphs.
- The two XRD datasets minted on `example.org` predate the gate; re-designing
  them re-mints under the instance base (they cannot silently persist — any
  new design round now trips the gate).
- Choosing a base is choosing a *permanent identifier namespace*, not a
  deployment host: `asterism.env.example` documents that (org domain or
  `github.io` namespace; a movable hostname is the wrong value).
- Dereference ("phase 2": content-negotiated `DESCRIBE` behind the base
  domain, or a w3id.org redirector as the base for new installs) stays open —
  this ADR makes the identifiers worth dereferencing.
