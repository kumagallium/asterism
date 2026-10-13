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

## Phase 2 — the dereference responder (accepted 2026-07-14)

`GET /describe?iri=<IRI>` on the api answers "what does this install's
PUBLISHED data say about this identifier?":

- **Scope**: the canonical + ontology graph merge — the same published scope
  every typed tool reads. The graph list comes from the server (control-graph
  `promoted` flags), never from the caller, so drafts stay unreachable by
  construction. Statements are bounded (500 outbound / 200 inbound) so one
  dereference can never become a graph dump.
- **Negotiation**: `Accept: text/turtle` (or `?format=ttl`) → the CONSTRUCT
  result as Turtle; browsers get a self-contained HTML view whose object IRIs
  link back through `/describe` — published data becomes *browsable*, with a
  provenance (graph) column per statement.
- **Exposure**: tokenless. One-IRI-in / its-published-description-out is the
  same exposure class as the typed tools and strictly narrower than the
  raw-SPARQL escape, so it stays available even where `/api/sparql` is
  withheld. On the private 1-box deployment the whole-site cookie gate still
  fronts it; Caddy routes `/datasets/*` → `/describe?iri=https://<domain><path>`
  inside the gate, with a documented block-move to make citation links public.
- **Unknown IRIs** are an honest 404 naming how many published graphs were
  searched (not yet promoted here / minted by another install).

With `ASTERISM_IRI_BASE=https://<the install's domain>`, minted IRIs therefore
actually resolve. IRIs on domains the install does not control (the bundled
kumagallium.github.io data, an unset `.invalid` base) still describe fine via
`/describe?iri=` — the identifier just doesn't route there by itself; that gap
is what a w3id.org base (or the operator's own domain) closes.

## Deterministic naming on top of the base (accepted 2026-07-23, kantan ADR K13)

The base fixed WHERE a design mints; naming still leaked two judgments to the
wrong parties. The 2026-07-23 ZEM dogfood showed the skeleton gate asking a
researcher to evaluate a CURIE prefix (`al3v:` vs `zem:`) — a token that never
appears in stored data — while the judgment that IS permanent (the dataset
slug inside the minted IRI) sat uneditable inside two raw-IRI textboxes.

Decision (implemented in `step0/instance_iri.py`, mirrored in
`ui/src/datasetNamespace.ts`):

- **`derive_prefix_pair(slug)`** — the CURIE prefix pair derives from the slug
  deterministically (first token; token-concatenation on collision with
  reserved/standard names; `ds`/`ds2`… last resort; resource = ontology+`r`;
  NCName-safe). Neither the LLM nor the human chooses it.
- **`normalize_dataset_namespace(skeleton, iri_base)`** — every AI-proposed
  skeleton is normalized before a human sees it: a mint matching
  `…/datasets/<slug>/(ontology#|resource/)` on ANY host is re-minted under
  THIS instance's base (wrong-owner repair), placeholder-domain mints
  (example.org & co) are classified by use (classes → ontology, subject
  templates → resource) and repaired, prefix names are forced to the derived
  pair, and every CURIE in the maps renames in lockstep. Unrecognizable
  skeletons pass through — the placeholder gate still guards them.
- **The skeleton gate edits ONE name** — the dataset slug ("データセットの
  名前"), cascading deterministically (IRI pair + prefix pair + CURIEs, UI
  twin `renameDatasetNamespace`); the evidence pass re-checks like any other
  edit. The BASE is never editable at the gate: an unconfigured base shows a
  provisional-issuer warning routing to Settings (`dataset_namespace` in the
  annotate payload carries slug/base/base_configured/pair).

## データ自身の IRI 列を主語の元にする（accepted 2026-10-09 — かんたんモードの初回設計）

**問題**: Graphium のような外のツールが主張（判断・規則・観察）を flat JSON（型ごとの
ファイル・参照と根拠は完全 IRI・入れ子は `*_iri` + `position` の平らな子ファイル）で書き出し、
Asterism に通常のデータセットとして登録する。行の ID が元の IRI と同じでなければ、外で
配られた参照はつながらない。IR は `https://<固定>/{id}` を許す（`mapping_ir._check_curies` /
`rml_compile.expand_template`）が、かんたんモードの決定論の組み立て
（`skeleton-from-easy-judgments.md` D5・`assemble_skeleton_from_judgments`）は常に
`{res}:{kind}/{key}` に鋳造していた。

**決定**（すべて決定論・事実から。LLM 0）:

- **検査が事実を持つ** — `inspect.ColumnSummary.iri_base`: 列の**全行**（サンプルリングでは
  なく）の非空セルが絶対 http(s) IRI で、共通の先頭を持つとき、その先頭を最後の `/` または
  `#` までで切った base。authority の直下（`https://doi.org/`）は base にしない。
  `/api/inspect` は `X-Asterism-Iri-Bases` `{source: {列: base}}` で画面に渡す（表の形の派生表で
  `max_rows` が効くときは先頭 N 行の base。組み立ては全行で計算し直すので、ずれれば却下の知らせ
  になる）。
- **④「つながりを選ぶ」に「ID をこのデータの IRI に合わせる」欄** — 候補のある source
  ごとに「Asterism が作る ID のまま（既定）」と「『列』の先頭 base を ID の元にする」。
  **既定は従来どおり**（opt-in）。人が受けると `assemble_skeleton_from_judgments(
  subject_bases={source: 列})` が、その source の**行の種類**の `subject.template` を
  `<base>` + キー列（`{k1}/{k2}`）にする。カード・つながる受け口は変えない。
- **却下は事実で決める** — 列が IRI 列でない（`not_iri_column`）／base がプレースホルダ
  （`placeholder_host`。example.* / localhost の拒否は本 ADR の方針そのまま）／IRI 列を除くと
  一意の証明がある候補が無い（`no_unique_key`。仮キーに base を貼ると複数の行が 1 つの IRI に
  潰れる — JSON は行を読まないので、これが唯一の防波堤）／IRI 列自身が行のキーになる
  （`iri_column_is_key`。カードの ID が IRI 列のとき。base を二重に貼ることになる）／CSV で
  base + キーを **RML と同じ percent-encode**（値は strip しない）で鋳造した形が元の IRI と
  一致しない行がある（`values_do_not_match`。JSON は行を読まないので検査しない）。
  却下は `metadata.subject_base_rejected` で⑤に出し、既定の ID に戻す。
- **出すのは初回設計のときだけ**（見直し `redesigning` では出さない）。公開したあとに変えると
  `id-move-after-publish.md` の台帳が要る。
- **base の所有はよそ** — その base は外のツールの名前空間で、この install の
  `ASTERISM_IRI_BASE` ではない。`/describe?iri=` は host に依らず効く（Phase 2）が、IRI 自体が
  ここに route することはない。「種類を分ける」などで新しい種類を派生させるときは、絶対頭から
  兄弟を作らず、この dataset の `resource/` 接頭辞に戻す（`ui/src/skeletonKinds.ts`
  `subjectHeadFor`）。

**非目標**: IRI 列そのものを主語にする（`{iri}` だけのテンプレート）は IR が許さない（head
必須）。`regex_extract` で base を剥がす形は `subject.transform`（引数無し）に乗らない。
必要になったら別に決める。
