"""Staged round-0 proposal (Phase 2b) — skeleton -> per-map -> document.

ADR: ``mapping-ir-phase2b-skeleton-wizard.md``.

Round-0 used to be one LLM call emitting the whole §1-9 Markdown. A weak model
bakes the highest-cost error — the subject key / class of a map — into that one
shot, and it only surfaces after all nine sections exist. Phase 2b splits the
call so the skeleton (which table becomes which class, keyed how) is produced
first, in a tiny guided-JSON shape, and can be confirmed by a human BEFORE any
property or prose is generated:

    inspect -> [1] skeleton (guided) -> <human gate> -> [2] per-map properties
            -> assemble IR -> [3] §1-8 prose -> splice §9 deterministically
            -> the same §1-9 Markdown the single call produced (materialize
               contract unchanged).

This module owns the PURE pieces (assembly, serialization, splice, the
skeleton<->full-IR split) and the thin LLM wrappers for each stage. The two
orchestrators map to the two API jobs (skeleton / continue). Every stage's
system prompt is frozen + byte-stable (cache-friendly); per-call variables ride
the user message, exactly like :mod:`asterism_step0.spec_repair`.

The IR this module assembles goes through the SAME parse -> validate -> compile
-> RML gates as any other round — guided decoding and staging narrow generation,
they never replace validation.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asterism_step0.inspect import inspect_source_set, render_markdown
from asterism_step0.instance_iri import dataset_namespace_block
from asterism_step0.language import language_instruction
from asterism_step0.llm import LLMClient, as_completion
from asterism_step0.mapping_ir import structural_property_issues
from asterism_step0.mapping_ir_schema import (
    permap_json_schema,
    skeleton_json_schema,
)

__all__ = [
    "SkeletonProposal",
    "assemble_mapping_ir",
    "fill_mapping_spec_block",
    "generate_document",
    "generate_map_properties",
    "generate_skeleton",
    "mapping_ir_to_yaml",
    "propose_from_skeleton",
    "propose_skeleton",
    "render_skeleton_context",
    "render_tier0_menu",
    "skeleton_from_full_ir",
]


# ---------------------------------------------------------------------------
# Frozen system prompts (byte-stable / cacheable). One per stage; the §9 rules
# mirror propose.SYSTEM_PROMPT in compact form. The Tier-0 menu and the real
# columns ride the user message so they stay registry/source-synced.
# ---------------------------------------------------------------------------

SKELETON_SYSTEM_PROMPT = """\
You design the SKELETON of an RDF mapping: which source table becomes which
entity class, keyed by which column(s). You do NOT choose properties yet — that
is a later step. Return a SINGLE JSON object, no markdown fence, no prose.

Shape (unknown fields are ERRORS — never invent one):
{
  "version": 1,
  "prefixes": { "sd": "https://…/ontology#", "sdr": "https://…/resource/", … },
  "maps": [
    { "name": "paper", "source": "papers.csv",
      "subject": { "template": "sdr:paper/{SID}", "classes": ["sd:Paper"] },
      "note": "1 row = 1 paper; SID is unique (inspection: 12345/12345)" }
  ]
}

Rules:
- One map per row type / entity class (4-10 classes is typical).
- subject.template uses {column} placeholders and the SMALLEST globally-unique
  composite key from the inspection's uniqueness statistics. A key that is not
  unique collapses distinct rows onto one IRI — the single costliest mistake, so
  justify it in `note` with the distinct/row counts you relied on.
- prefixes: name the dataset's OWN prefix pair after THIS dataset's content
  (e.g. xrd:/xrdr: for X-ray diffraction data — one for the ontology# namespace,
  one for the resource/ namespace). The `sd:`/`sdr:` in the shape above are
  placeholders from an unrelated example dataset — do NOT copy them.
- classes are CURIEs; declare every prefix you use in `prefixes` (xsd: is
  builtin — never declare it). Reuse standard vocabularies (schema:, dcterms:,
  prov:, bibo:) rather than minting new classes when a standard fits.
- source: copy the filename character-for-character from the inspection
  (`## CSV:` / `## JSON:` / `## XML:`). Never append/rename/invent a suffix.
- XML/JATS sources add `iterator:` copied verbatim from the `## XML:` table and
  use a subject `constant:` (the ingest is per-document).
- ENTITY LINKING: design keys so entities can join later (a measurement carries
  the key of the thing it measures, a record carries its source key). Skeletons
  whose entities cannot reach each other cannot answer cross-entity questions.
- `note` (optional, free text) records the key/class rationale for the human who
  reviews this skeleton. It is dropped from the final mapping — put no data in it.
"""

PERMAP_SYSTEM_PROMPT = """\
You fill the PROPERTY TABLE for ONE map of an RDF mapping whose skeleton
(subject template, key, classes) is ALREADY fixed and shown to you — do NOT
restate or change the subject/classes. Return a SINGLE JSON object, no fence,
no prose:
{ "properties": [ … ], "prefixes": { …only NEW prefixes you introduce… } }

Each property row is one predicate-object binding with EXACTLY ONE object form:
`column` (direct) | `columns` (multi-input function) | `object_template` (IRI
link or, with object_type:literal, a composed literal) | `constant`. Every row
MUST carry one of these four keys DIRECTLY under `predicate` (as a sibling) — a
row with no object form is rejected.

Rules:
- `function:` / `transform:` name ONLY a vetted Tier-0 function from the menu in
  the user message — bare name, no `fn:` prefix, no new logic. Constant args go
  in `args:` by name. A function's output IS the object; NEVER combine `function`
  with `object_template`/`constant`.
- NEVER nest `function`/`args`/`column` inside `transform:` — writing
  `transform: {function: X, args: {…}}` leaves the row with NO object form.
  `transform:` is ONLY the `{object_template placeholder: single-input function}`
  map for readable IRI segments (e.g. `transform: {container_title: slug}`). Put
  `function:` and `column:` as direct siblings of `predicate:`.
- EVERY property row carries its data source: `column:` with the header text
  copied EXACTLY (or columns / object_template / constant). `unit:` / `label:`
  are display metadata only — a row with just `predicate` + `unit` has no data
  source and cannot compile. Write `unit` once, as one short notation ("Ohm m"),
  never repeated.
- `function:` NEVER casts types (`function: str`/`int`/`date` are errors): a bare
  column already emits a string literal; type a literal with `datatype: xsd:…`.
- Predicates are plain CURIEs — NO cardinality markers (`schema:author`, never
  `schema:author*`).
- A bare `column` can never be an IRI: a URL column uses `function: iri_safe` +
  `object_type: iri`; an entity link uses `object_template` (an IRI link unless
  marked `object_type: literal`). Template data columns are IRI-encoded by the
  engine automatically — do not invent cleaning; for a readable segment declare
  `transform: { column: slug }`.
- Multi-valued cells use the multi-value functions (split / json_array /
  json_pluck — one triple per element); a deeply irregular structure goes to a
  `…Raw` predicate with `fallback: true`. Never invent a function; one unmapped
  column must not block the ingest.
- Use ONLY column names for THIS map's source, exactly as the menu lists them.
- Give EVERY measurement-like property a `label:` (human-readable meaning, in
  the output language requested for prose) and, when the column carries a
  physical quantity, a `unit:` (human-readable notation like `µV/K`). Display
  metadata only — values are unchanged and unit-conversion stays in the Tier-0
  functions.
- Declare in `prefixes` any vocabulary your predicates/datatypes use that the
  skeleton did not already declare (xsd: is builtin — never declare it).
"""

DOCUMENT_SYSTEM_PROMPT = """\
You write the human-readable design document (sections 1-8) for an RDF dataset
whose §9 mapping spec is ALREADY decided and given to you below. Describe the
ACTUAL design encoded in that spec — the classes, keys and properties it
contains — and invent nothing that is not in it.

Output the Markdown sections in this exact order (English headings; prose in the
requested language):
### 1. Class hierarchy (Mermaid classDiagram — no colons in labels)
### 2. IRI scheme (prefixes + each class's IRI template, from the spec's subjects)
### 3. Property design (datatype/object properties, reuse standards, cardinality)
### 4. JSON column strategy (expand / compress / raw+aggregates)
### 5. Design rationale (Decision / Why / Alternatives / Trade-offs per choice)
### 6. rdf-config model.yaml (classes + properties matching the spec)
### 7. MIE YAML extras (schema_info with ≥5 `keywords` AND ≥1 `categories` entry
     — BOTH are required for T4; sample_rdf_entries from REAL inspection rows,
     sparql_query_examples, anti_patterns)
### 8. Ingester sketch (utf-8-sig, composite IRI helpers, PROV — signatures only)

End with `### 9. Declarative mapping spec` containing the given spec verbatim in a
single ```yaml fence (it will be normalized deterministically — reproduce it as
given, change nothing). No preamble, no follow-up questions.
"""


# ---------------------------------------------------------------------------
# Pure: assembly, serialization, the skeleton<->full-IR split, §9 splice.
# ---------------------------------------------------------------------------


def _load_json_object(raw: str) -> dict:
    """Model output (bare JSON — the guided path — or a fenced block, or YAML —
    the ungated fallback; YAML is a superset of JSON) -> a dict. Raises
    ``ValueError`` with a loop-feedable message when nothing parses to an object."""
    import yaml

    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        closing = text.rfind("```")
        if first_nl != -1 and closing > first_nl:
            text = text[first_nl + 1 : closing].strip()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"model output is not valid JSON/YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("model output must be a single JSON object")
    return doc


def _clean_map(map_obj: Mapping[str, Any], properties: list[Any]) -> dict:
    """One assembled map: skeleton fields (name/source/iterator?/subject) + the
    per-map properties, with the skeleton-only ``note`` dropped. Key order matches
    the single-shot IR so the serialized YAML reads the same."""
    out: dict = {"name": map_obj.get("name"), "source": map_obj.get("source")}
    if map_obj.get("iterator") is not None:
        out["iterator"] = map_obj["iterator"]
    out["subject"] = dict(map_obj.get("subject") or {})
    out["properties"] = properties
    return out


def assemble_mapping_ir(
    skeleton: Mapping[str, Any], permaps: Mapping[str, Mapping[str, Any]]
) -> dict:
    """Merge a confirmed skeleton with the per-map property tables into a full
    Mapping IR dict ``{version, prefixes, maps}``.

    ``permaps`` is keyed by map name; each value is a per-map result
    ``{properties: [...], prefixes?: {...}}``. Prefixes are unioned
    (skeleton wins on conflict — declared vocabularies are authoritative; per-map
    only ADDS new ones). A map with no per-map entry gets an empty property list
    (the parser/validator then decides whether that is acceptable). The result is
    a plain dict so it round-trips through ``parse_mapping_ir`` unchanged.
    """
    prefixes: dict[str, str] = dict(skeleton.get("prefixes") or {})
    maps_out: list[dict] = []
    for map_obj in skeleton.get("maps") or []:
        name = map_obj.get("name")
        permap = permaps.get(name) or {}
        for pfx, iri in (permap.get("prefixes") or {}).items():
            prefixes.setdefault(str(pfx), iri)
        properties = list(permap.get("properties") or [])
        maps_out.append(_clean_map(map_obj, properties))
    return {"version": 1, "prefixes": prefixes, "maps": maps_out}


def skeleton_from_full_ir(ir: Mapping[str, Any]) -> tuple[dict, dict[str, dict]]:
    """Inverse of :func:`assemble_mapping_ir`: split a full IR dict into
    ``(skeleton, permaps)``. Lets a single-shot proposal be re-expressed in the
    staged shape (equivalence tests; a "regenerate one map" path over an existing
    design). ``skeleton`` carries subject-only maps; ``permaps`` maps each name to
    its ``{properties: [...]}``."""
    sk_maps: list[dict] = []
    permaps: dict[str, dict] = {}
    for map_obj in ir.get("maps") or []:
        name = map_obj.get("name")
        sk_map: dict = {"name": name, "source": map_obj.get("source")}
        if map_obj.get("iterator") is not None:
            sk_map["iterator"] = map_obj["iterator"]
        sk_map["subject"] = dict(map_obj.get("subject") or {})
        sk_maps.append(sk_map)
        permaps[name] = {"properties": list(map_obj.get("properties") or [])}
    skeleton = {
        "version": 1,
        "prefixes": dict(ir.get("prefixes") or {}),
        "maps": sk_maps,
    }
    return skeleton, permaps


def mapping_ir_to_yaml(ir: Mapping[str, Any]) -> str:
    """Serialize an IR dict to the readable YAML that becomes the §9 block (same
    serializer as ``spec_repair.parse_spec_json`` — insertion order preserved)."""
    import yaml

    return yaml.safe_dump(
        dict(ir), sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def fill_mapping_spec_block(document_md: str, ir_yaml: str) -> str:
    """Put ``ir_yaml`` into the document's §9 mapping-spec block. If the document
    already has one (the document step tends to reproduce it), overwrite it with
    the assembled IR verbatim — byte-preserving everything else — so §9 is exactly
    the assembled spec regardless of the model's copy fidelity. If none is present,
    append a `### 9. Declarative mapping spec` section deterministically."""
    from asterism_step0.materialize import materialize_schema
    from asterism_step0.spec_repair import replace_mapping_spec_block

    extracted = materialize_schema(document_md, ".", "fill", write=False).mapping_ir_yaml
    if extracted is not None:
        return replace_mapping_spec_block(document_md, ir_yaml)
    body = ir_yaml.strip("\n")
    return document_md.rstrip() + f"\n\n### 9. Declarative mapping spec\n\n```yaml\n{body}\n```\n"


def render_skeleton_context(skeleton: Mapping[str, Any]) -> str:
    """A compact view of every map's subject/classes, so the per-map step can link
    a property to another entity (object_template to that map's subject)."""
    lines = ["# Skeleton (fixed — subjects/classes of every map)", ""]
    prefixes = skeleton.get("prefixes") or {}
    if prefixes:
        # The gated skeleton's prefixes are this dataset's settled namespaces —
        # show them so the per-map step reuses them instead of minting new ones.
        lines += [f"- prefix {name}: <{iri}>" for name, iri in prefixes.items()]
        lines.append("")
    for map_obj in skeleton.get("maps") or []:
        subject = map_obj.get("subject") or {}
        key = subject.get("template") or subject.get("constant") or "?"
        classes = ", ".join(subject.get("classes") or [])
        lines.append(
            f"- map '{map_obj.get('name')}' (source {map_obj.get('source')}): "
            f"subject {key} a {classes}"
        )
    return "\n".join(lines)


def render_tier0_menu(function_names: Sequence[str] | None) -> str:
    """A minimal closed-set menu (names only) — a safe default when a caller has
    no richer menu. The API passes the full oracle (exact columns + function
    signatures) instead; both keep generation inside the vetted set."""
    if not function_names:
        return ""
    listed = ", ".join(sorted(function_names))
    return f"# Vetted Tier-0 functions (choose only from these)\n\n{listed}\n"


# ---------------------------------------------------------------------------
# User-message builders (per-call variables).
# ---------------------------------------------------------------------------


def build_skeleton_user(
    inspection_md: str,
    domain_hint: str,
    *,
    language: str | None = None,
    iri_base: str | None = None,
) -> str:
    msg = (
        f"# Source inspection\n\n{inspection_md}\n\n"
        f"# Domain context\n\n{domain_hint.strip()}\n\n"
        f"{dataset_namespace_block(iri_base)}\n"
        "Return the skeleton as a single JSON object."
    )
    lang = language_instruction(language)
    return f"{msg}\n\n{lang}\n" if lang else msg


def build_permap_user(
    map_name: str,
    map_skeleton: Mapping[str, Any],
    skeleton_context: str,
    menu: str,
    *,
    issues: list[str] | None = None,
    language: str | None = None,
) -> str:
    subject = map_skeleton.get("subject") or {}
    key = subject.get("template") or subject.get("constant") or "?"
    classes = ", ".join(subject.get("classes") or [])
    parts = [
        f"# This map: '{map_name}' (source {map_skeleton.get('source')})",
        f"subject {key} a {classes}",
        "",
        skeleton_context,
        "",
        menu.strip(),
    ]
    if issues:
        parts += ["", "# Issues to fix (fix ONLY these)", *[f"- {i}" for i in issues]]
    parts += ["", f"Return the property table for map '{map_name}' as a single JSON object."]
    lang = language_instruction(language)
    if lang:
        parts += ["", lang]
    return "\n".join(parts)


def build_document_user(
    assembled_ir_yaml: str, inspection_md: str, domain_hint: str, *, language: str | None = None
) -> str:
    msg = (
        f"# Decided §9 mapping spec (describe THIS; invent nothing else)\n\n"
        f"```yaml\n{assembled_ir_yaml.strip()}\n```\n\n"
        f"# Source inspection\n\n{inspection_md}\n\n"
        f"# Domain context\n\n{domain_hint.strip()}\n\n"
        "Return the §1-8 document followed by §9 reproducing the spec above."
    )
    lang = language_instruction(language)
    return f"{msg}\n\n{lang}\n" if lang else msg


# ---------------------------------------------------------------------------
# Thin LLM wrappers (guided when the client supports it; parsed + gated either
# way). Same set/restore-attribute pattern as design_loop._surgical_spec_repair.
# ---------------------------------------------------------------------------


def _complete_guided(llm: Any, system: str, user: str, schema: dict | None) -> str:
    had_attr = hasattr(llm, "response_schema")
    prior = getattr(llm, "response_schema", None)
    try:
        if had_attr and schema is not None:
            llm.response_schema = schema
        return as_completion(llm.complete(system, user)).text
    finally:
        if had_attr:
            llm.response_schema = prior


def generate_skeleton(
    inspection_md: str,
    domain_hint: str,
    *,
    llm: LLMClient,
    function_names: Sequence[str] | None = None,
    language: str | None = None,
    iri_base: str | None = None,
) -> dict:
    """One guided call -> the skeleton dict (subject-only maps). Parsed here;
    structural/environment validation is the caller's gate."""
    user = build_skeleton_user(inspection_md, domain_hint, language=language, iri_base=iri_base)
    schema = skeleton_json_schema(function_names)
    return _load_json_object(_complete_guided(llm, SKELETON_SYSTEM_PROMPT, user, schema))


def generate_map_properties(
    map_name: str,
    map_skeleton: Mapping[str, Any],
    skeleton_context: str,
    menu: str,
    *,
    llm: LLMClient,
    function_names: Sequence[str] | None = None,
    issues: list[str] | None = None,
    language: str | None = None,
) -> dict:
    """One guided call -> one map's ``{properties: [...], prefixes?: {...}}``."""
    user = build_permap_user(
        map_name, map_skeleton, skeleton_context, menu, issues=issues, language=language
    )
    schema = permap_json_schema(function_names)
    return _load_json_object(_complete_guided(llm, PERMAP_SYSTEM_PROMPT, user, schema))


def generate_document(
    assembled_ir_yaml: str,
    inspection_md: str,
    domain_hint: str,
    *,
    llm: LLMClient,
    language: str | None = None,
) -> str:
    """One free-form call -> the §1-8 Markdown (+ a §9 the caller normalizes).
    No response_schema: this stage is prose, not IR."""
    user = build_document_user(assembled_ir_yaml, inspection_md, domain_hint, language=language)
    return _complete_guided(llm, DOCUMENT_SYSTEM_PROMPT, user, None)


# ---------------------------------------------------------------------------
# Orchestrators — one per API job (skeleton / continue).
# ---------------------------------------------------------------------------


@dataclass
class SkeletonProposal:
    """Result of :func:`propose_skeleton` — the early human-gate artifact."""

    skeleton: dict
    csv_inspection_md: str
    domain_hint: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _resolve_function_names(function_names: Sequence[str] | None) -> list[str] | None:
    if function_names is not None:
        return list(function_names)
    try:
        from asterism_step0.mapping_ir import catalog_from_registry

        return catalog_from_registry().names()
    except ImportError:
        return None


def propose_skeleton(
    csv_paths: list[Path | str],
    domain_hint: str,
    *,
    fk_hint_columns: list[str] | None = None,
    record_path: str | None = None,
    llm: LLMClient,
    language: str | None = None,
    function_names: Sequence[str] | None = None,
    dialects: Mapping[str, Any] | None = None,
    iri_base: str | None = None,
) -> SkeletonProposal:
    """Job 1: inspect the source(s) and generate the skeleton for human review.
    Does NOT generate properties or prose — that is :func:`propose_from_skeleton`,
    run after the human confirms/edits the skeleton.

    ``dialects`` (ADR source-dialect.md) is the effective per-source read dialect
    (detected ⊕ human override); forwarded to ``inspect_source_set`` so the
    skeleton's key/column choices see the SAME columns the pinned §9 dialect
    produces (``skip_rows`` moves the header row).

    ``iri_base`` (ADR instance-iri-base.md) is where THIS instance mints new
    dataset namespaces; unset falls back to the ``.invalid`` default inside
    :func:`dataset_namespace_block`."""
    inspections, fks = inspect_source_set(
        csv_paths,
        fk_hint_columns=fk_hint_columns,
        record_path=record_path,
        dialects=dialects,
    )
    inspection_md = render_markdown(inspections, fks)
    names = _resolve_function_names(function_names)
    skeleton = generate_skeleton(
        inspection_md,
        domain_hint,
        llm=llm,
        function_names=names,
        language=language,
        iri_base=iri_base,
    )
    return SkeletonProposal(
        skeleton=skeleton,
        csv_inspection_md=inspection_md,
        domain_hint=domain_hint,
        metadata={"llm_class": type(llm).__name__},
    )


_PERMAP_STRUCTURAL_ROUNDS = 2
"""Bounded per-map structural self-correction rounds (ADR mapping-ir-phase2b §4:
"per-map ステップは run_design_loop の中で回す … no-progress で有界停止"; §11: call
count "1 → (2 + N + 自己修正ラウンド)"). Kept small — the assembly-stage parse + §9
surgical repair stay the full gate; this only spares the whole-IR loop the easy,
single-map-decidable structural breakages (object-form-none / transform misuse)."""


def _generate_map_properties_gated(
    map_name: str,
    map_skeleton: Mapping[str, Any],
    skeleton_context: str,
    menu_text: str,
    *,
    llm: LLMClient,
    function_names: Sequence[str] | None,
    language: str | None,
    index: int,
    total: int,
    emit: Callable[..., None],
    record: Callable[[], None],
) -> dict:
    """Generate ONE map's property table, then run a BOUNDED structural repair.

    A per-map result whose ROWS are structurally broken — object-form-none, the
    ``transform:`` misuse family, unknown fields, function shape: exactly the
    single-map-decidable failures :func:`structural_property_issues` reports — is
    regenerated with those issues fed back (``generate_map_properties(issues=…)``),
    up to :data:`_PERMAP_STRUCTURAL_ROUNDS` times. A round is kept only when it
    STRICTLY reduces the structural issue count (no-progress stop, mirroring the
    full loop's oscillation guard), so a model that cannot fix the row keeps its
    best attempt instead of thrashing.

    This is the per-map arm of the ADR's "per-map runs inside the self-correction
    loop". Whole-IR concerns (CURIE/prefix, cross-map joins, column existence) are
    deliberately NOT judged here — they need the assembled IR and stay the
    assembly-stage parse + §9 surgical repair. Truncated / unparseable output
    degrades this map to no properties and continues (unchanged resilience: the
    assembled IR then surfaces the gap to the full validation, exactly as the
    single-shot round-0 would)."""

    def _emit(message: str) -> None:
        emit(phase=f"map:{map_name}", index=index, total=total, message=message)

    try:
        result = generate_map_properties(
            map_name, map_skeleton, skeleton_context, menu_text,
            llm=llm, function_names=function_names, language=language,
        )
    except ValueError as exc:
        _emit(f"map '{map_name}' の生成に失敗しプロパティ無しで継続します: {exc}")
        record()
        return {"properties": []}
    record()

    issues = structural_property_issues(
        result.get("properties"), where=f"map '{map_name}'.properties"
    )
    rounds = 0
    while issues and rounds < _PERMAP_STRUCTURAL_ROUNDS:
        rounds += 1
        _emit(f"map '{map_name}' のプロパティ構造を修正中: {len(issues)} 件の問題")
        try:
            retry = generate_map_properties(
                map_name, map_skeleton, skeleton_context, menu_text,
                llm=llm, function_names=function_names, issues=issues, language=language,
            )
        except ValueError:
            # A truncated retry: the LLM call still happened (parse failed after
            # completion), so record its usage like every other call, then keep the
            # better prior result and stop.
            record()
            break
        record()
        retry_issues = structural_property_issues(
            retry.get("properties"), where=f"map '{map_name}'.properties"
        )
        if len(retry_issues) < len(issues):
            result, issues = retry, retry_issues  # progress: adopt the cleaner table
        else:
            break  # no progress: keep the prior (better-or-equal) table and stop
    return result


def propose_from_skeleton(
    skeleton: Mapping[str, Any],
    inspection_md: str,
    domain_hint: str,
    *,
    llm: LLMClient,
    menu: str | None = None,
    language: str | None = None,
    function_names: Sequence[str] | None = None,
    on_progress: Any = None,
    on_llm_call: Callable[[str], None] | None = None,
) -> str:
    """Job 2: from a confirmed skeleton, generate each map's property table, assemble
    the full IR, generate the §1-8 document, and splice §9 in deterministically.
    Returns the §1-9 Markdown (the same artifact the single call produced — the
    self-correction gates run on it in the API layer, unchanged).

    ``menu`` is the closed reference (the API passes the oracle's exact columns +
    function signatures); when omitted a names-only menu is rendered so the stage
    still runs standalone. ``on_progress(**data)`` receives ``phase`` frames per map;
    ``on_llm_call(feature)`` fires after every per-map and document call so the caller
    records usage per call (each tagged ``"propose"``, like the single-shot round-0)."""
    names = _resolve_function_names(function_names)
    menu_text = menu if menu is not None else render_tier0_menu(names)
    context = render_skeleton_context(skeleton)

    def emit(**data: Any) -> None:
        if on_progress is not None:
            on_progress(**data)

    def record() -> None:
        if on_llm_call is not None:
            on_llm_call("propose")

    maps = list(skeleton.get("maps") or [])
    permaps: dict[str, dict] = {}
    for i, map_obj in enumerate(maps):
        name = map_obj.get("name")
        emit(phase=f"map:{name}", index=i, total=len(maps), message=f"プロパティ表を生成中: {name}")
        # Generate this map's properties + a bounded per-map structural repair
        # (object-form-none / transform misuse etc.). Truncated output degrades to
        # no properties and continues — the same resilience the single-shot round-0
        # has (a bad proposal surfaces as issues at the assembly gate, it does not
        # crash). Whole-IR concerns (CURIE/prefix, joins, columns) stay the
        # assembly-stage parse + §9 surgical repair, NOT this per-map gate.
        permaps[name] = _generate_map_properties_gated(
            name,
            map_obj,
            context,
            menu_text,
            llm=llm,
            function_names=names,
            language=language,
            index=i,
            total=len(maps),
            emit=emit,
            record=record,
        )

    assembled = assemble_mapping_ir(skeleton, permaps)
    ir_yaml = mapping_ir_to_yaml(assembled)

    emit(phase="document", message="設計文書を生成中")
    document_md = generate_document(ir_yaml, inspection_md, domain_hint, llm=llm, language=language)
    record()
    return fill_mapping_spec_block(document_md, ir_yaml)
