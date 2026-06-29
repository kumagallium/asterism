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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asterism_step0.inspect import inspect_source_set, render_markdown

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
- Declare prefixes: `sd:` (ontology), `sdr:` (resource), reused (`schema:`,
  `dcterms:`, `bibo:`, `prov:`)
- For each entity class, give the IRI template using the **uniqueness
  statistics** from the inspection (★ trap T1 — pick the smallest globally
  unique composite key)
- NO blank nodes (T3): every entity has a stable IRI

### 3. Property design
- Datatype properties and object properties
- REUSE existing properties (`schema:author`, `dcterms:identifier`, etc.) —
  do not create new ones when a standard exists
- Cardinality (0..1 / 0..* / 1..* ) derived from non_null_rate

### 4. JSON column strategy
For each column flagged as `json-array` / `json-object`:
- (a) Expand to nodes (e.g. author objects → Person nodes)
- (b) Compress to literal (e.g. date_parts → xsd:date)
- (c) Raw JSON literal + aggregates (e.g. x/y → JSON + xMin/xMax/yMin/yMax)
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

### 9. RML declarative mapping (declarative substrate path)
A single ` ```turtle ` block: an R2RML/RML mapping run by the **Morph-KGC
substrate with NO generated code** (the safe, RCE-free path). One
`rr:TriplesMap` per row type, prefixes/predicates matching §2/§3. Full spec:
`docs/architecture/step0-rml-emission.md`.

Declare these prefixes **verbatim** at the top of the block (the function-execution
vocab MUST use the `http://w3id.org/rml/` namespace — Morph-KGC does NOT support
the old `http://semweb.mmlab.be/ns/fnml#`):
```
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix rmlf: <http://w3id.org/rml/> .
@prefix fn:   <https://kumagallium.github.io/asterism/fn/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
```

**Logical source** — match each `rr:TriplesMap`'s `rml:logicalSource` to the
source kind shown in the inspection (`## CSV:` / `## JSON:` / `## XML:` blocks). Use
the filename, and (for **XML**) the iterator from the inspection block verbatim:
- **CSV** sources:
  ```
  rml:logicalSource [ rml:source "<file>.csv" ; rml:referenceFormulation ql:CSV ] ;
  ```
  `rml:reference "col"` and `rr:template "…/{col}"` use the column name.
- **JSON** sources (#19) — ingest **tabularizes the JSON to CSV** (nested objects →
  dot-path columns, arrays → JSON-string cells), so treat a `## JSON:` block as CSV:
  ```
  rml:logicalSource [ rml:source "<file>.csv" ; rml:referenceFormulation ql:CSV ] ;
  ```
  Use the **`.csv`** name the inspection's JSON block names (the `<file>.json`'s
  stem), `ql:CSV`, and NO `rml:iterator`. `rml:reference` / `rr:template` use the
  **dot-path leaf field exactly as listed** in the inspection (e.g.
  `structure.spacegroup`) — a plain dot-path. An **array column** (type `json-array`)
  holds the array as a JSON string → explode it with `fn:json_array` /
  `fn:json_pluck` (below), exactly as a CSV "JSON in a cell" column.
- **XML / JATS** sources (document-ontology layer) — read declaratively via `ql:XPath`
  (no tabularization; the article is a structure tree, not records):
  ```
  rml:logicalSource [ rml:source "<file>.xml" ;
                      rml:referenceFormulation ql:XPath ;
                      rml:iterator "<iterator from the ## XML: table, e.g. /article/body/sec>" ] ;
  ```
  `rml:reference` / `rr:template` use **iterator-relative element/attribute paths**
  (`@id`, `title`, `label`, `.` for the element's text). HARD XML limits (Morph-KGC's
  XML reader): NO `[@a='v']` predicates and NO parent/ancestor axes in references;
  it returns only an element's `.text` (mixed content like `<sub>`/`<italic>` is
  truncated, so faithful verbatim is a post-pass, not RML); every template needs ≥1
  `{ref}` — so the per-paper IRI base is a CONSTANT (`rr:constant`, the ingest is
  per-paper). Build `po:contains` parent→child via a multi-valued child reference
  (`{sec/@id}`, `{fig/@id}`). Use a node's `@id` for its IRI when the inspection
  marks it stable (✓); nodes without `@id` (e.g. `<p>`) are NOT mapped here — they
  are the deterministic post-pass's job (a dated `lit:DocumentParsingActivity` claim).
- Tier 0 functions, templates, and the HARD RULES below apply identically regardless
  of source kind (e.g. `fn:structural_slug` on a section heading → its structural path).

HARD RULES (a reviewer approves *column→predicate + which vetted function*, not code):
- May reference ONLY these vetted **Tier 0** functions
  (`@prefix fn: <https://kumagallium.github.io/asterism/fn/>`). No other
  function, no inline code, no new logic:
  - `fn:date_iso` (value → `xsd:date`) — messy date → ISO 8601
  - `fn:float_array_max` / `fn:float_array_min` (value → `xsd:double`)
    — numeric JSON array → max / min
  - `fn:float_array_count` (value1, value2 → `xsd:integer`)
    — x,y arrays → `min(len)` = point count (2 inputs)
  - `fn:qudt_quantity` / `fn:qudt_unit` (value → IRI)
    — property name / unit → QUDT IRI (empty ⇒ triple skipped)
  - `fn:iri_safe` (value → IRI) — URL → IRI-safe
  - `fn:slug` (value → string) — string → IRI segment
  - `fn:number_clean` (value → `xsd:double`) — strip thousands sep / currency /
    accounting parens (`"$1,234.50"` → `1234.50`)
  - `fn:percent_to_ratio` (value → `xsd:double`) — `"12%"` → `0.12`
  - `fn:range_min` / `fn:range_max` (value → `xsd:double`) — `"10-20"` → low / high end
  - `fn:datetime_iso` (value → `xsd:dateTime`) — messy datetime OR epoch (ms/s) → ISO 8601
  - `fn:year_only` (value → `xsd:gYear`) — extract a 4-digit year
  - `fn:nfkc_norm` (value → string) — Unicode NFKC (fold full-width / compatibility)
  - `fn:trim_collapse` (value → string) — trim + collapse internal whitespace
  - `fn:strip_footnote` (value → string) — drop trailing footnote markers (`"x[1]"` → `"x"`)
  - `fn:bool_norm` (value → `xsd:boolean`) — `Yes/1/on` → `true`, `No/0/off` → `false`
  - `fn:doi_norm` (value → string) — normalize a DOI to its bare lowercase form
  - `fn:url_canonical` (value → string) — lowercase scheme+host, drop default port / fragment
  - `fn:value_of` / `fn:unit_of` (value → string) — split value+unit (`"300 K"` → `300` / `K`)
  - `fn:json_array_single` (value → string) — unwrap a **one-element** JSON array
    (`["X"]` → `X`); multi-element arrays return "" (use `fn:split` / nested map)
  - `fn:array_at` (value, **index** const → string) — element at a fixed 0-based
    index of a JSON array (`[lon,lat,depth]`, index `1` → lat); negatives from end
  - `fn:split` (value, **delimiter** const → MULTIPLE values) — split a delimited
    cell into many; returns a list that Morph-KGC **explodes into one triple per
    element** (`",ci,us,"` with delimiter `","` → two `ex:tag` triples). Flat
    comma/semicolon lists.
  - `fn:json_array` (value → MULTIPLE values) — a cell holding a JSON **array of
    scalars as a string** (`'["P1","P2"]'`) → one triple per element (explodes)
  - `fn:json_pluck` (value, **field** const → MULTIPLE values) — a cell holding a
    JSON **array of objects as a string** (`'[{"family":"Adams"},{"family":"Brown"}]'`)
    → the `field` of each object, one triple each (`field`="family" → Adams, Brown).
    This is the multi-value path for object arrays stored as **string cells** (e.g.
    starrydata `author`). A JSON-source nested array arrives here as such a string
    cell too (ingest tabularizes JSON to CSV), so use `fn:json_pluck` / `fn:json_array`
    directly — no nested TriplesMap.
  - Parameterized primitives — take the column value(s) PLUS a **constant** config
    argument (a table / regex / template), to absorb the long tail without a new
    function. The config is data, not code:
    - `fn:lookup` (value, table → string) — map a value via a vetted seed table.
      Tables: `bool` (Yes/No/1/0/… → `true`/`false`), `country_iso3166` (country
      name → ISO alpha-2), `unit_alias` (unit spelling → symbol, e.g. `kelvin`→`K`;
      chain into `fn:qudt_unit` for the IRI). Miss ⇒ "" (triple skipped).
    - `fn:regex_extract` (value, pattern → string) — extract a substring: a named
      group `(?P<v>…)` if present, else group 1, else the whole match. Use a
      **re2-compatible** pattern (no backreferences, no look-around). Miss ⇒ "".
    - `fn:template` (template, field1…field4 → string) — safe interpolation: the
      constant template uses positional tokens `{1}`…`{4}` filled by the field
      columns (e.g. `"{1}-{2}"`). Missing field ⇒ "". (For simple IRI/string
      composition prefer a plain `rr:template` term map.)
- Direct column: `rr:objectMap [ rml:reference "col" ]`. Composite IRI: `rr:template "…/{a}-{b}"`.
- IRI from a DATA value MUST be made IRI-safe: when a subject/object `rr:template` /
  `rml:template` builds an IRI from a free-text or data-derived column (composition,
  title, name, comment, label, formula, …), pass that column through `fn:iri_safe`
  FIRST and template on the function's OUTPUT — never `{raw_col}` directly. A raw value
  with `<`, a space, a quote, `{`/`}` etc. produces an invalid IRI that fails at load
  ("Invalid IRI code point"). A value already known to be a clean id/slug (a numeric
  SID, an existing URL) needs no wrapping. WRONG: `rr:template "…/composition/{composition}"`
  (raw value → invalid IRI). RIGHT: compute an IRI-safe segment, then template on it:
  ```
  <#CompMap> a rr:TriplesMap ;
    rml:logicalSource [ rml:source "data.csv" ; rml:referenceFormulation ql:CSV ] ;
    rr:subjectMap [ rr:template "sdr:composition/{comp_iri}" ] ;
    rr:predicateObjectMap [ rr:predicate <…/hasFormula> ;
      rr:objectMap [ rml:reference "composition" ] ] .   # keep the raw value as a literal
  ```
  where `comp_iri` is the `fn:iri_safe` output of the `composition` column, produced by a
  function objectMap exactly like the others (param `fn:p_value`):
  `rmlf:functionExecution [ rmlf:function fn:iri_safe ;
    rmlf:input [ rmlf:parameter fn:p_value ;
                 rmlf:inputValueMap [ rml:reference "composition" ] ] ]`.
- Function objectMap: `rmlf:functionExecution [ rmlf:function fn:NAME ;
  rmlf:input [ rmlf:parameter fn:p_value ; rmlf:inputValueMap [ rml:reference "col" ] ] ]`.
  2-input (`fn:float_array_count`): two `rmlf:input`, params `fn:p_value1` / `fn:p_value2`.
- Constant primitive arguments (table / pattern / template) are passed with
  `rmlf:inputValueMap [ rmlf:constant "…" ]` — note `rmlf:constant` (the
  `http://w3id.org/rml/` namespace); the legacy `rml:` namespace has no `constant`.
  Primitive param IRIs: `fn:p_table` / `fn:p_pattern` / `fn:p_template` /
  `fn:p_field1`…`fn:p_field4`. Example (lookup = value column + constant table):
  `rmlf:functionExecution [ rmlf:function fn:lookup ;
    rmlf:input [ rmlf:parameter fn:p_value ; rmlf:inputValueMap [ rml:reference "flag" ] ] ;
    rmlf:input [ rmlf:parameter fn:p_table ; rmlf:inputValueMap [ rmlf:constant "bool" ] ] ]`.
- Multi-valued / nested cells — prefer the vetted multi-value functions over a raw
  fallback when they fit (each explodes to many triples linked to the row):
  one-element array → `fn:json_array_single`; fixed-position array → `fn:array_at`;
  flat delimited list → `fn:split`; **JSON array of scalars as a string** →
  `fn:json_array`; **JSON array of objects as a string** → `fn:json_pluck` (per
  sub-field, e.g. each author's family) — this covers JSON-source arrays too, since
  ingest tabularizes them to JSON-string cells. Reserve the `…Raw` fallback only for
  a deeply irregular structure none of these reach (e.g. an array of arrays, or a
  child entity needing several correlated fields). Emit the raw string to a `…Raw`
  predicate with a
  `# fallback: <col> not expanded` comment. DO NOT invent a function. One unmapped
  column must never block the rest of the ingest.

## Self-check before responding (quality traps)
- [ ] T1: IRI scheme uses uniqueness statistics from inspection?
- [ ] T2: ingester opens with utf-8-sig?
- [ ] T3: zero blank nodes (no rdflib.BNode() calls)?
- [ ] T4: MIE keywords ≥ 5 in English AND in domain-relevant languages?
- [ ] T5: Mermaid labels free of colons?
- [ ] T6: sample_rdf_entries reference REAL row values from the inspection?
- [ ] T7: every non-trivial design choice has Why / Alternatives / Trade-offs?
- [ ] T8: domain-specific synonyms (jp / formulas / aliases) propagated to MIE keywords?
- [ ] T9: §9 RML references ONLY `fn:*` Tier 0 functions (no other functions, no
      code); unmappable multi-valued columns use the `…Raw` fallback?

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

    Returns:
        :class:`SchemaProposal` with the inspection Markdown, the domain hint,
        and the LLM's full proposal Markdown.
    """
    if llm is None:
        llm = AnthropicLLMClient()

    # Step 1: deterministic inspection (CSV and/or JSON, dispatched by extension)
    inspections, fks = inspect_source_set(
        csv_paths, fk_hint_columns=fk_hint_columns, record_path=record_path
    )
    inspection_md = render_markdown(inspections, fks)

    # Step 3: assemble user message and call LLM
    user_message = (
        f"# Source inspection\n\n{inspection_md}\n\n# Domain context\n\n{domain_hint.strip()}\n"
    )
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
    )
    if args.output is None:
        print(proposal.proposal_md)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(proposal.proposal_md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
