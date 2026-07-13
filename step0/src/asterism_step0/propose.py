"""AI-driven schema proposal for asterism Phase 3.

Given one or more structured sources (CSV or JSON, #19) + a domain hint, this
module produces:
  1. A rdf-config-formatted ``model.yaml`` (per Phase 3 #3 decision)
  2. A rationale block explaining the design choices

The deterministic prelude (column types, JSON detection, uniqueness stats★) is
delegated to :mod:`asterism_step0.inspect`. The LLM consumes that Markdown plus
the user's ``domain_hint`` and emits the artifact set.

Design constraints:
  * The LLM client is a Protocol so tests can mock it (no API key needed in CI).
  * The default implementation uses Anthropic SDK with **prompt caching** —
    the system prompt is large and stable across calls, so we mark it as
    ``cache_control: ephemeral`` to land cache hits on repeated invocations.
  * We use ``claude-opus-4-7`` with ``thinking={"type": "adaptive"}`` per the
    claude-api skill guidance for Opus 4.7 (manual ``budget_tokens`` returns 400).
  * The system prompt embeds the 8-trap validator checklist from
    :doc:`ai-assisted-step0-prompts.md` §3.1 so the LLM self-checks before
    returning.

This module is the "Step 3 schema proposal" of the workflow. Step 1 (CSV
inspection) is the input; Step 5 (refine_schema) and Step 6 (validate_schema)
are separate modules (future work).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asterism_step0.dialect import SourceDialect
from asterism_step0.inspect import inspect_source_set, render_markdown
from asterism_step0.language import language_instruction

# The LLM client seam lives in asterism_step0.llm (multi-provider). These names
# are re-exported here so existing imports (`from asterism_step0.propose import
# LLMClient, AnthropicLLMClient`) keep working.
from asterism_step0.llm import (
    AnthropicLLMClient,
    LLMClient,
    LLMCompletion,
    LLMTruncatedError,
    LLMUsage,
    as_completion,
    make_llm,
)

__all__ = [
    "AnthropicLLMClient",
    "LLMClient",
    "LLMCompletion",
    "LLMTruncatedError",
    "LLMUsage",
    "SchemaProposal",
    "as_completion",
    "make_llm",
    "propose_schema",
]

# ----------------------------------------------------------------------------
# System prompt — frozen, cacheable, embeds the §3.1 prompt template
# ----------------------------------------------------------------------------
#
# This must stay byte-stable across calls for prompt caching to work. Any
# interpolation here (timestamps, user IDs, dynamic flags) would invalidate
# the cache. Per-call variables (CSV inspection, domain hint) go in the user
# message instead.
#
# Source: docs/architecture/ai-assisted-step0-prompts.md §3.1, lightly trimmed
# for token efficiency (the LLM doesn't need the human-readable framing).

SYSTEM_PROMPT = """\
You are an RDF / OWL / SPARQL ontology engineer building schemas for the
asterism Phase 3 project. Given (a) deterministic CSV inspection output
and (b) a human-provided domain hint, you propose a minimal RDF schema as a
single Markdown document with two top-level artifacts:

1. **rdf-config model.yaml** (per [Phase 3 #3 decision][1])
2. **Design rationale** (Decision / Why / Alternatives / Trade-offs per choice)

[1]: docs/architecture/linkml-vs-rdf-config.md — rdf-config emits ShEx that
     drops in to MIE shape_expressions; LinkML's OWL TBox uses bnodes which
     violates Phase 1's bnode-free policy.

## Output structure (Markdown sections, in this exact order)

### 1. Class hierarchy (Mermaid classDiagram)
- 4-10 entity classes
- GitHub-Mermaid-compatible — NO colons in labels (use the table below)
- Plain prefix-less class names in the diagram; map to TBox IRIs in §2

### 2. IRI scheme
- Declare the dataset's OWN prefix pair, NAMED AFTER THIS DATASET's content
  (e.g. `xrd:` ontology / `xrdr:` resource for X-ray diffraction data; the
  `sd:`/`sdr:` appearing in examples belong to an unrelated example dataset —
  do not copy them), plus reused prefixes (`schema:`, `dcterms:`, `bibo:`,
  `prov:`)
- For each entity class, give the IRI template using the **uniqueness
  statistics** from the inspection (★ trap T1 — pick the smallest globally
  unique composite key)
- NO blank nodes (T3): every entity has a stable IRI

### 3. Property design
- Datatype properties and object properties
- REUSE existing properties (`schema:author`, `dcterms:identifier`, etc.) —
  do not create new ones when a standard exists
- Cardinality (0..1 / 0..* / 1..* ) derived from non_null_rate
- ★ ENTITY LINKING (connectivity): when you design MORE THAN ONE entity class,
  every entity MUST be reachable from the others through object properties —
  join on the shared source key (a measurement links to the thing it measures,
  a record links to its source document). Disconnected entities cannot answer
  ANY cross-entity question ("the material with the highest measured value",
  "which record came from which source"), which defeats the point of a graph.
  The design must form ONE connected component unless the sources are truly
  unrelated — and then §5 must say so explicitly.
  DIRECTION: declare the link FROM the entity whose source table CARRIES the
  foreign key — the child row points at its parent (child → parent), reusing
  the parent's subject IRI template as the object. A parent's table does NOT
  contain its children's keys, so a link written on the parent side references
  a column that does not exist in its source. One direction is enough: SPARQL
  traverses the edge both ways.

### 4. JSON column strategy
For each column flagged as `json-array` / `json-object`:
- (a) Expand to nodes (e.g. author objects → Person nodes)
- (b) Compress to literal (e.g. date_parts → xsd:date)
- (c) Raw JSON literal + MANDATORY aggregates (e.g. x/y → JSON +
  xMin/xMax/yMin/yMax via Tier-0 `float_array_max` / `float_array_min`).
  A numeric series kept only as a JSON string is DEAD for querying — SPARQL
  cannot rank or compare inside a literal, so every "highest/lowest X"
  question becomes unanswerable. If a series column plausibly backs a
  ranking/comparison question, the aggregate predicates are NOT optional.
State the choice and justify

### 5. Design rationale (★ T7: mandatory)
For every non-trivial design choice (IRI scheme, JSON strategy, property
choice, cardinality), write:
- **Decision**: what
- **Why**: grounded in the inspection statistics or domain hint
- **Alternatives**: what you considered and rejected
- **Trade-offs**: the cost of this choice; conditions for re-evaluation

### 6. rdf-config model.yaml
Complete YAML, suitable for `bundle exec rdf-config --config <dir> --shex`.
Format follows `dbcls/rdf-config` — flat list of subjects, each:
```
- ClassName <example-IRI>:
    - a: sd:ClassName
    - property:
        - var_name: example_value
    - optional_property?:
        - var_name: example_value
    - multi_valued_property*:
        - var_name: example_value
```

### 7. MIE YAML extras (schema_info, sparql_query_examples, anti_patterns)
The shape_expressions block is generated by rdf-config from §6; you provide:
- `schema_info`: title / description / categories / **keywords** (★ T4 — at
  least 5 each, include English + 日本語 synonyms + composition formulas if
  applicable)
- `sample_rdf_entries`: 1-3 examples built from **REAL CSV rows** in the
  inspection (★ T6 — never invent SIDs or sample_ids)
- `sparql_query_examples`: 3-5 queries that answer the most likely user
  questions
- `anti_patterns`: known limitations + traps for future maintainers
- `architectural_notes`: summary of §5 Design rationale

### 8. Ingester sketch
Python skeleton (`ingest/src/asterism/{dataset}.py` template):
- `utf-8-sig` open (★ T2)
- Composite IRI helpers
- PROV-O IngestionActivity
- JSON column parsers
- Error log path (jsonl)
NOT a complete implementation — just the public API + helper signatures.

### 9. Declarative mapping spec
A single ` ```yaml ` block: a small **mapping spec** that a deterministic
compiler turns into the RML mapping run by the **Morph-KGC substrate with NO
generated code** (the safe, RCE-free path). You do NOT write RML/Turtle — the
compiler owns all of that syntax. One map per row type, prefixes/predicates
matching §2/§3. Full spec: `docs/architecture/mapping-ir-compiler.md`.

Shape (all fields shown; unknown fields are ERRORS — never invent one):
```yaml
version: 1
prefixes:                     # every prefix used below; xsd: is builtin
  sd:  "https://kumagallium.github.io/asterism/starrydata/ontology#"
  sdr: "https://kumagallium.github.io/asterism/starrydata/resource/"
  schema: "https://schema.org/"
maps:
  - name: paper               # unique identifier per map
    source: papers.csv        # filename EXACTLY as the inspection lists it
    subject:
      template: "sdr:paper/{SID}"     # {column} placeholders; smallest unique key (§2)
      classes: [sd:Paper, schema:ScholarlyArticle]
    properties:
      - predicate: schema:name        # direct column → literal
        column: title
      - predicate: schema:datePublished
        column: issued
        function: date_iso            # vetted Tier-0 function (menu below)
        datatype: xsd:date
      - predicate: schema:url
        column: URL
        function: iri_safe
        object_type: iri              # IRI-returning functions need this
      - predicate: sd:pointCount
        columns: [x, y]               # multi-input function
        function: float_array_count
        datatype: xsd:integer
      - predicate: sd:tag
        column: tags
        function: split
        args: { delimiter: "," }      # constant args by NAME (table/pattern/…)
      - predicate: sd:fromPaper       # link to another entity: IRI template
        object_template: "sdr:paper/{SID}"
      - predicate: dcterms:identifier # literal composed of several columns
        object_template: "{SID}-{sample_id}"
        object_type: literal
      - predicate: sd:isPartOf        # readable IRI segment from messy text
        object_template: "sdr:periodical/{container_title}"
        transform: { container_title: slug }
      - predicate: sd:authorsRaw      # no fitting function → raw passthrough
        column: author
        fallback: true
```

**Source kinds** — match `source` to the inspection (`## CSV:` / `## JSON:` /
`## XML:` blocks). Copy the filename character-for-character; NEVER append,
rename, or invent a suffix (no `_preprocessed`, `_clean`, `_v2`, …) — the
ingest reads the real files on disk. All value cleaning is done by the Tier-0
functions, not by a different file.
- **CSV**: `source: <file>.csv`, no `iterator`. Columns are the header names.
- **JSON** (#19): ingest tabularizes the JSON to CSV (nested objects →
  dot-path columns, arrays → JSON-string cells). Use the **`.csv`** name the
  inspection's JSON block names, no `iterator`; columns are the **dot-path
  leaf fields exactly as listed** (e.g. `structure.spacegroup`). An array
  column (type `json-array`) holds the array as a JSON string → explode it
  with `json_array` / `json_pluck`, exactly as a CSV "JSON in a cell" column.
- **XML / JATS** (document-ontology layer): `source: <file>.xml` plus
  `iterator:` copied verbatim from the `## XML:` table (e.g.
  `/article/body/sec`). Columns/placeholders are **iterator-relative
  element/attribute paths** (`@id`, `title`, `{sec/@id}`, `.` for the
  element's text). HARD XML limits (Morph-KGC's reader): NO `[@a='v']`
  predicates and NO parent/ancestor axes; only an element's `.text` is read
  (mixed content like `<sub>` is truncated — faithful verbatim is a
  post-pass, not this mapping); the per-document IRI base is a subject
  `constant:` (the ingest is per-document). Build `po:contains` parent→child
  via a multi-valued child placeholder (`{sec/@id}`, `{fig/@id}`). Nodes
  without a stable `@id` (e.g. `<p>`) are NOT mapped here.

RULES (a reviewer approves *column→predicate + which vetted function*, not code):
- `function:` / `transform:` values may name ONLY the vetted **Tier 0**
  functions below — bare names, no `fn:` prefix, no other function, no inline
  code, no new logic. Constant args go in `args:` by name.
- Exactly ONE object form per property: `column` | `columns` |
  `object_template` | `constant`. NEVER combine `function` with
  `object_template`/`constant` — a function's output IS the object (one
  literal per value; multi-value functions emit one literal per element).
  Per-element entity IRIs from an in-cell array are NOT expressible — use the
  `…Raw` fallback for that column instead.
- Predicates/classes are plain terms: NO cardinality markers (`schema:author`,
  never `schema:author*` — the `*`/`?` suffixes belong to §6 model.yaml only).
- `function:` NEVER casts types (`function: str` / `int` / `date` are errors).
  A bare column already emits a string literal; type a literal with
  `datatype: xsd:…`; use `function:` only for the cleaning menu below.
- A bare `column` can NEVER be an IRI: for a URL column use
  `function: iri_safe` + `object_type: iri`; for an entity link use
  `object_template`. Raw data columns inside templates are IRI-encoded
  automatically by the engine — do not invent cleaning. When a segment should
  be a *readable* slug instead (shared nodes like periodicals), declare
  `transform: { column: slug }`.
- An `object_template` is an IRI link unless you mark it
  `object_type: literal` (identifiers composed of several columns).
- Multi-valued / nested cells — prefer the vetted multi-value functions over
  a raw fallback (each explodes into one triple per element automatically):
  one-element array → `json_array_single`; fixed position → `array_at`; flat
  delimited list → `split`; JSON array of scalars as a string → `json_array`;
  JSON array of objects as a string → `json_pluck` (per sub-field — covers
  JSON-source arrays too, since ingest tabularizes them to string cells).
  Reserve the `…Raw` fallback (`fallback: true` on a bare column, predicate
  named `…Raw`) only for deeply irregular structures none of these reach.
  DO NOT invent a function. One unmapped column must never block the ingest.

Vetted **Tier 0** functions (the complete closed set — choose only from here):
- `date_iso` (1 column → `xsd:date`) — messy date → ISO 8601
- `float_array_max` / `float_array_min` (1 column → `xsd:double`)
  — numeric JSON array → max / min
- `float_array_count` (2 columns → `xsd:integer`)
  — x,y arrays → `min(len)` = point count
- `qudt_quantity` / `qudt_unit` (1 column → IRI, needs `object_type: iri`)
  — property name / unit → QUDT IRI (no match ⇒ triple skipped)
- `iri_safe` (1 column → IRI, needs `object_type: iri`) — URL → IRI-safe
- `slug` (1 column → string) — string → readable IRI segment (also the usual
  `transform:` function)
- `structural_slug` (1 column → string) — numbered heading → structural path
  (`"3.2 Results"` → `"3-2"`)
- `number_clean` (1 column → `xsd:double`) — strip thousands sep / currency /
  accounting parens (`"$1,234.50"` → `1234.50`)
- `percent_to_ratio` (1 column → `xsd:double`) — `"12%"` → `0.12`
- `range_min` / `range_max` (1 column → `xsd:double`) — `"10-20"` → low / high end
- `datetime_iso` (1 column → `xsd:dateTime`) — messy datetime OR epoch (ms/s) → ISO 8601
- `year_only` (1 column → `xsd:gYear`) — extract a 4-digit year
- `nfkc_norm` (1 column → string) — Unicode NFKC (fold full-width / compatibility)
- `trim_collapse` (1 column → string) — trim + collapse internal whitespace
- `strip_footnote` (1 column → string) — drop trailing footnote markers (`"x[1]"` → `"x"`)
- `bool_norm` (1 column → `xsd:boolean`) — `Yes/1/on` → `true`, `No/0/off` → `false`
- `doi_norm` (1 column → string) — normalize a DOI to its bare lowercase form
- `url_canonical` (1 column → string) — lowercase scheme+host, drop default port / fragment
- `value_of` / `unit_of` (1 column → string) — split value+unit (`"300 K"` → `300` / `K`)
- `json_array_single` (1 column → string) — unwrap a **one-element** JSON array
  (`["X"]` → `X`); multi-element arrays return "" (use `split` / `json_array`)
- `array_at` (1 column, `args: {index: "1"}` → string) — element at a fixed
  0-based index of a JSON array; negatives count from the end
- `split` (1 column, `args: {delimiter: ","}` → MULTIPLE values) — split a
  delimited cell; one triple per element. Flat comma/semicolon lists.
- `json_array` (1 column → MULTIPLE values) — a cell holding a JSON **array of
  scalars as a string** (`'["P1","P2"]'`) → one triple per element
- `json_pluck` (1 column, `args: {field: "family"}` → MULTIPLE values) — a cell
  holding a JSON **array of objects as a string** → that field of each object,
  one triple each (e.g. starrydata `author` → each family name)
- `lookup` (1 column, `args: {table: …}` → string) — map a value via a vetted
  seed table: `bool` (Yes/No/1/0/… → `true`/`false`), `country_iso3166`
  (country name → ISO alpha-2), `unit_alias` (unit spelling → symbol, e.g.
  `kelvin`→`K`; chain into `qudt_unit` for the IRI). Miss ⇒ "" (triple skipped).
- `regex_extract` (1 column, `args: {pattern: …}` → string) — extract a
  substring: named group `(?P<v>…)` if present, else group 1, else the whole
  match. **re2-compatible** patterns only (no backreferences, no look-around).
  Miss ⇒ "".
- `template` (up to 4 columns, `args: {template: "{1}-{2}"}` → string) — safe
  positional interpolation of the column values. (For simple IRI/string
  composition prefer a plain `object_template`.)

## Self-check before responding (quality traps)
- [ ] T1: IRI scheme uses uniqueness statistics from inspection?
- [ ] T2: ingester opens with utf-8-sig?
- [ ] T3: zero blank nodes (no rdflib.BNode() calls)?
- [ ] T4: MIE keywords ≥ 5 in English AND in domain-relevant languages?
- [ ] T5: Mermaid labels free of colons?
- [ ] T6: sample_rdf_entries reference REAL row values from the inspection?
- [ ] T7: every non-trivial design choice has Why / Alternatives / Trade-offs?
- [ ] T8: domain-specific synonyms (jp / formulas / aliases) propagated to MIE keywords?
- [ ] T9: §9 mapping spec names ONLY Tier 0 functions from the menu (in
      `function:` / `transform:`), uses only real columns/files from the
      inspection, and unmappable multi-valued columns use the `…Raw`
      fallback (`fallback: true`)?

## What you receive (user message)

The user message will be structured as:
```
# Source inspection
<output of asterism-inspect — CSV (`## CSV:`) and/or JSON (`## JSON:`) blocks;
 see docs/architecture/ai-assisted-step0-prompts.md §1>

# Domain context
<dataset name, purpose, ontology constraints, synonyms — per §2>
```

You respond with the Markdown document above. No preamble, no follow-up
questions — just the artifact set.
"""


# ----------------------------------------------------------------------------
# propose_schema
# ----------------------------------------------------------------------------


@dataclass
class SchemaProposal:
    """Result of one :func:`propose_schema` call."""

    csv_inspection_md: str
    """The Markdown the deterministic inspector produced (Step 1 output)."""

    domain_hint: str
    """The user-supplied domain context (Step 2 input)."""

    proposal_md: str
    """The LLM's full Markdown proposal (Step 3 output)."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Optional: model name, latency, token usage, etc."""


def propose_schema(
    csv_paths: list[Path | str],
    domain_hint: str,
    *,
    fk_hint_columns: list[str] | None = None,
    record_path: str | None = None,
    llm: LLMClient | None = None,
    language: str | None = None,
    dialects: Mapping[str, SourceDialect] | None = None,
) -> SchemaProposal:
    """Run Step 1 (inspect) and Step 3 (propose) end-to-end.

    Args:
        csv_paths: One or more source files to model. CSV and JSON (#19) are
            both accepted; the kind is picked per file by extension
            (``.json`` / ``.geojson`` → JSON).
        domain_hint: Free-form Markdown following the ``ai-assisted-step0-prompts.md``
            §2 template (dataset name, purpose, ontology constraints, synonyms).
        fk_hint_columns: Optional FK columns to seed composite key search
            (e.g. ``["SID"]`` for starrydata). Forwarded to inspect_source_set.
        record_path: For JSON sources whose records live under a top-level key,
            the key holding the array of records (auto-detected when omitted).
        llm: An :class:`LLMClient`. Defaults to :class:`AnthropicLLMClient`
            (requires ``ANTHROPIC_API_KEY``). Tests pass a mock.
        language: Output language for the proposal's human-readable prose
            (e.g. ``"ja"``). Headings / identifiers / code stay English (see
            :mod:`asterism_step0.language`). ``None`` → English.
        dialects: Per-source read dialect overrides (ADR source-dialect.md), the
            effective dialect (detected ⊕ human override) for each tabular
            source. Forwarded to ``inspect_source_set`` so the inline inspection
            reports the SAME columns the pinned §9 dialect will produce; sources
            not listed are auto-detected.

    Returns:
        :class:`SchemaProposal` with the inspection Markdown, the domain hint,
        and the LLM's full proposal Markdown.
    """
    if llm is None:
        llm = AnthropicLLMClient()

    # Step 1: deterministic inspection (CSV and/or JSON, dispatched by extension)
    inspections, fks = inspect_source_set(
        csv_paths, fk_hint_columns=fk_hint_columns, record_path=record_path, dialects=dialects
    )
    inspection_md = render_markdown(inspections, fks)

    # Step 3: assemble user message and call LLM
    user_message = (
        f"# Source inspection\n\n{inspection_md}\n\n# Domain context\n\n{domain_hint.strip()}\n"
    )
    lang_block = language_instruction(language)
    if lang_block:
        user_message += f"\n{lang_block}\n"
    proposal_md = as_completion(llm.complete(SYSTEM_PROMPT, user_message)).text

    return SchemaProposal(
        csv_inspection_md=inspection_md,
        domain_hint=domain_hint,
        proposal_md=proposal_md,
        metadata={"llm_class": type(llm).__name__},
    )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser():  # type: ignore[no-untyped-def]
    import argparse

    p = argparse.ArgumentParser(
        prog="asterism-propose",
        description=(
            "Inspect source(s) (CSV or JSON) + propose an rdf-config schema using "
            "Claude. Requires ANTHROPIC_API_KEY."
        ),
    )
    p.add_argument("source", type=Path, nargs="+", help="Source file(s) to model (CSV or JSON)")
    p.add_argument(
        "--domain",
        required=True,
        help="Domain hint (Markdown). Use --domain-file for longer content.",
    )
    p.add_argument(
        "--domain-file",
        type=Path,
        default=None,
        help="Read the domain hint from this file (overrides --domain).",
    )
    p.add_argument(
        "--fk",
        dest="fk_hint",
        action="append",
        default=[],
        help="Foreign-key companion column. Repeatable.",
    )
    p.add_argument(
        "--record-path",
        dest="record_path",
        default=None,
        help=(
            "For JSON sources whose records live under a top-level key, the key "
            "holding the array of records (auto-detected when omitted)."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the proposal Markdown to this file. Defaults to stdout.",
    )
    p.add_argument(
        "--model",
        default="claude-opus-4-7",
        help="Anthropic model ID (default: claude-opus-4-7).",
    )
    p.add_argument(
        "--effort",
        default="xhigh",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="output_config.effort (default: xhigh — best for coding/agentic on Opus 4.7).",
    )
    p.add_argument(
        "--language",
        default=None,
        help=(
            "Output language for the proposal's prose (e.g. 'ja'). Headings / "
            "identifiers / code stay English. Default: English."
        ),
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    domain_hint = args.domain_file.read_text(encoding="utf-8") if args.domain_file else args.domain
    llm = AnthropicLLMClient(model=args.model, effort=args.effort)
    proposal = propose_schema(
        args.source,
        domain_hint,
        fk_hint_columns=args.fk_hint or None,
        record_path=args.record_path,
        llm=llm,
        language=args.language,
    )
    if args.output is None:
        print(proposal.proposal_md)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(proposal.proposal_md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
