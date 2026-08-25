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

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asterism_step0.inspect import SourceInspection, inspect_source_set, render_markdown
from asterism_step0.instance_iri import (
    dataset_namespace_block,
    derive_prefix_pair,
    normalize_dataset_namespace,
    normalize_iri_base,
    slugify_dataset_name,
)
from asterism_step0.language import language_instruction
from asterism_step0.llm import (
    LLMCancelledError,
    LLMClient,
    LLMEmptyOutputError,
    LLMTruncatedError,
    as_completion,
)
from asterism_step0.mapping_ir import structural_property_issues
from asterism_step0.mapping_ir_schema import (
    permap_json_schema,
    skeleton_json_schema,
)
from asterism_step0.spec_yaml import load_spec_yaml

__all__ = [
    "SkeletonProposal",
    "apply_column_decisions",
    "apply_column_decisions_to_document",
    "apply_data_facts",
    "apply_display_meta",
    "apply_display_meta_to_document",
    "apply_numeric_datatypes",
    "assemble_mapping_ir",
    "default_property_table",
    "default_skeleton",
    "fill_mapping_spec_block",
    "generate_document",
    "generate_map_properties",
    "generate_skeleton",
    "mapping_ir_to_yaml",
    "menu_columns",
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
- Pick ONLY the columns whose values DESCRIBE this map's entity. Do NOT
  transcribe the whole source into every map: each source column belongs to
  exactly ONE map — a value that varies per row belongs to the per-row entity;
  a value fixed for the whole file belongs to the one fixed entity. A column
  another map in the skeleton owns appears here ONLY as a link/join key, never
  as another plain datatype property (that would store the same fact twice).
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
        doc = load_spec_yaml(text)
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
# Deterministic defaults — what the machine writes when the model cannot.
# ---------------------------------------------------------------------------
#
# Every stage below has a decision the DATA already settles: which table becomes
# an entity (one per source), how a row is identified (the inspector's proven
# unique keys), which columns describe it (the header, minus the ones another
# map owns), and what type each value has (measured from every row). A model is
# asked first because it names things better — but when it returns broken JSON
# or runs out of tokens, the answer is still knowable, and handing the person a
# raw parse error to relay back to the same model is the loop kantan mode
# exists to end. These builders are LLM-free and never invent a column.

_MEASUREMENT_TYPES = frozenset({"xsd:double", "xsd:float", "xsd:decimal"})

# The failures a deterministic default may stand in for: the model ANSWERED and
# the answer was unusable (unreadable JSON, cut off, empty). Everything else —
# a bad key, an unreachable endpoint, a rate limit, a model name that does not
# exist — means no AI ran at all, and quietly handing back a machine-written
# design would hide a misconfiguration the person can actually fix (the very
# thing the llm error codes exist to tell them). Those propagate, as before.
_UNUSABLE_ANSWER = (ValueError, LLMTruncatedError, LLMEmptyOutputError)
_MENU_COLUMNS_RE = re.compile(r"^\s*[•*-]\s*(?P<name>.+?)\s+[—-]+\s+columns:\s*(?P<cols>.+?)\s*$")


def _identifier(text: str) -> str:
    """``Measurement temp.(C)`` → ``measurementTempC`` (lowerCamel, ASCII-safe)."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", str(text)) if p]
    if not parts:
        return "value"
    head = parts[0]
    head = head if head[:1].islower() else head[:1].lower() + head[1:]
    out = head + "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return f"v{out}" if out[:1].isdigit() else out


def _class_name(text: str) -> str:
    """``xrd_peaks`` → ``XrdPeaks`` (PascalCase, ASCII-safe)."""
    ident = _identifier(text)
    return ident[:1].upper() + ident[1:] if ident else "Record"


def menu_columns(menu: str) -> dict[str, list[str]]:
    """``{source filename: [columns]}`` recovered from the closed-menu appendix.

    The menu is machine-written (the api's oracle reads each header with the
    same BOM-safe reader the validator uses), so these names are the real ones —
    the only reason to parse rather than be handed them is that the menu is the
    one place a caller already passes them in every path.
    """
    out: dict[str, list[str]] = {}
    for line in (menu or "").splitlines():
        m = _MENU_COLUMNS_RE.match(line)
        if not m:
            continue
        cols = [c.strip() for c in m.group("cols").split(", ") if c.strip()]
        if cols:
            out[m.group("name").strip()] = cols
    return out


def default_property_table(
    columns: Sequence[str],
    *,
    ontology_prefix: str,
    owned_elsewhere: Mapping[str, str] | None = None,
    column_types: Mapping[str, str] | None = None,
) -> dict:
    """A property row per column this map owns — column name as the meaning.

    Used when a map's property generation failed outright. The alternative (the
    previous behaviour) was an EMPTY table, which the review screen hides
    entirely: the person's columns disappear with no way back except asking the
    AI again. Column names are a poor ontology and a perfectly good starting
    point — every value is carried, sourced, and typed, and the review screen's
    per-property fields are where the wording gets fixed.

    Two columns whose names differ only in punctuation (``T (K)`` / ``T(K)``)
    slug to the same predicate; the later one is numbered rather than dropped,
    because losing a column here is the exact harm this table exists to prevent.
    """
    from asterism_step0.units import extract_unit_from_label

    owned = dict(owned_elsewhere or {})
    types = dict(column_types or {})
    properties: list[dict[str, Any]] = []
    seen: set[str] = set()
    for column in columns:
        name = str(column)
        if not name or name in owned:
            continue  # another map owns this value; a copy here is the same fact twice
        stem = f"{ontology_prefix}:{_identifier(name)}"
        predicate = stem
        n = 1
        while predicate in seen:
            n += 1
            predicate = f"{stem}{n}"
        seen.add(predicate)
        row: dict[str, Any] = {"predicate": predicate, "column": name}
        datatype = types.get(name)
        if datatype:
            row["datatype"] = datatype
        unit = extract_unit_from_label(name)
        label = name
        if unit:
            row["unit"] = unit
            # The label is the column name minus the unit it already states.
            # Half- and full-width brackets both, matching units.extract_unit_from_label.
            label = re.sub(r"[(\uFF08][^()\uFF08\uFF09]*[)\uFF09]\s*$", "", name).strip() or name
        row["label"] = label
        properties.append(row)
    return {"properties": properties}


def _proven_key(inspection: SourceInspection) -> tuple[str, ...]:
    """The smallest key the inspector PROVED unique, measurement-only keys last.

    Falls back to the first column: not unique, but the skeleton gate then shows
    the collisions with one-click candidates, which is the honest state — a
    silently invented key would be the costliest mistake in the pipeline.
    """
    types = {c.name: c.inferred_type for c in inspection.columns}

    def measurement_only(key: Sequence[str]) -> bool:
        return bool(key) and all(types.get(c) in _MEASUREMENT_TYPES for c in key)

    unique = [r for r in inspection.uniqueness_reports if r.is_unique and r.key]
    if unique:
        best = min(unique, key=lambda r: (measurement_only(r.key), len(r.key)))
        return tuple(best.key)
    return (inspection.columns[0].name,) if inspection.columns else ()


def default_skeleton(
    inspections: Sequence[SourceInspection],
    *,
    iri_base: str | None = None,
    dataset_name: str | None = None,
) -> dict:
    """One entity per source, keyed by a proven-unique column set — LLM-free.

    The shape the human gate was built to review: it opens with the collision
    evidence and the candidate chips either way, so a person confirms or
    re-picks a key in one tap instead of reading a JSON parse error.
    """
    slug = slugify_dataset_name(
        dataset_name or (inspections[0].name.rsplit(".", 1)[0] if inspections else None)
    )
    base = normalize_iri_base(iri_base)
    onto, res = derive_prefix_pair(slug)
    prefixes = {
        onto: f"{base}/datasets/{slug}/ontology#",
        res: f"{base}/datasets/{slug}/resource/",
    }
    maps: list[dict[str, Any]] = []
    taken: set[str] = set()
    for inspection in inspections:
        stem = inspection.name.rsplit(".", 1)[0]
        name = _identifier(stem)
        while name in taken:
            name = f"{name}2"
        taken.add(name)
        entry: dict[str, Any] = {"name": name, "source": inspection.name}
        classes = [f"{onto}:{_class_name(stem)}"]
        if inspection.source_kind == "xml":
            iterators = inspection.xml_iterators or []
            if iterators:
                entry["iterator"] = iterators[0].iterator
            entry["subject"] = {"constant": f"{res}:{name}", "classes": classes}
        else:
            key = _proven_key(inspection)
            segment = "-".join(f"{{{c}}}" for c in key) if key else "1"
            entry["subject"] = {"template": f"{res}:{name}/{segment}", "classes": classes}
        maps.append(entry)
    return {"version": 1, "prefixes": prefixes, "maps": maps}


# ---------------------------------------------------------------------------
# User-message builders (per-call variables).
# ---------------------------------------------------------------------------


def build_skeleton_user(
    inspection_md: str,
    domain_hint: str,
    *,
    language: str | None = None,
    iri_base: str | None = None,
    issues: list[str] | None = None,
) -> str:
    msg = (
        f"# Source inspection\n\n{inspection_md}\n\n"
        f"# Domain context\n\n{domain_hint.strip()}\n\n"
        f"{dataset_namespace_block(iri_base)}\n"
    )
    if issues:
        msg += (
            "# Your previous answer could not be read (fix ONLY this)\n\n"
            + "\n".join(f"- {i}" for i in issues)
            + "\n\nReturn ONLY the JSON object — no prose, no markdown fence.\n\n"
        )
    msg += "Return the skeleton as a single JSON object."
    lang = language_instruction(language)
    return f"{msg}\n\n{lang}\n" if lang else msg


def render_owned_elsewhere(owned_elsewhere: Mapping[str, str] | None) -> str:
    """The columns THIS map must not transcribe, named (ADR column-ownership G6).

    The system prompt already states the rule in general ("a column another map
    owns appears here ONLY as a link/join key"); a weak model still broke it in
    real dogfood (ZEM: 13 instrument columns written onto BOTH maps). The rule is
    machine-decidable from the data, so the gate's verdict rides the per-call
    message as concrete column names — the inspection-trap lesson (a mechanical
    requirement needs a mechanical recipe, not a stronger request).
    """
    if not owned_elsewhere:
        return ""
    lines = [
        "# Columns owned by ANOTHER map (do not add them here)",
        "These values are decided by the other map's key, so writing them here"
        " would store one fact on every row. Use them ONLY inside a link"
        " (object_template) — never as a plain property of this map.",
    ]
    lines += [
        f"- `{col}` → owned by map '{owner}'"
        for col, owner in sorted(owned_elsewhere.items())
    ]
    return "\n".join(lines)


def build_permap_user(
    map_name: str,
    map_skeleton: Mapping[str, Any],
    skeleton_context: str,
    menu: str,
    *,
    issues: list[str] | None = None,
    language: str | None = None,
    owned_elsewhere: Mapping[str, str] | None = None,
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
    owned = render_owned_elsewhere(owned_elsewhere)
    if owned:
        parts += ["", owned]
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
    issues: list[str] | None = None,
) -> dict:
    """One guided call -> the skeleton dict (subject-only maps). Parsed here;
    structural/environment validation is the caller's gate."""
    user = build_skeleton_user(
        inspection_md, domain_hint, language=language, iri_base=iri_base, issues=issues
    )
    schema = skeleton_json_schema(function_names)
    return _load_json_object(_complete_guided(llm, SKELETON_SYSTEM_PROMPT, user, schema))


def drop_borrowed_properties(
    result: Mapping[str, Any], owned_elsewhere: Mapping[str, str] | None
) -> tuple[dict, list[str]]:
    """Remove plain properties that transcribe a column another map owns.

    The deterministic half of ADR column-ownership G6: asking is not enough, so
    a property whose value IS a borrowed column (``column: X``) is dropped even
    when the model adds it anyway. Returns the cleaned result and the dropped
    column names so the caller can report them (never a silent edit).

    Deliberately narrow — only the direct ``column:`` form is a transcription:
    - ``object_template`` keeps them (that IS the link to the owning entity);
    - ``columns:`` (a multi-input function) keeps them (the value is computed
      here, not copied);
    so the join and any genuine derivation survive.
    """
    props = result.get("properties")
    if not owned_elsewhere or not isinstance(props, list):
        return dict(result), []
    kept: list[Any] = []
    dropped: list[str] = []
    for prop in props:
        col = prop.get("column") if isinstance(prop, Mapping) else None
        if isinstance(col, str) and col in owned_elsewhere:
            dropped.append(col)
            continue
        kept.append(prop)
    if not dropped:
        return dict(result), []
    return {**result, "properties": kept}, dropped


def apply_numeric_datatypes(
    result: Mapping[str, Any], column_types: Mapping[str, str] | None
) -> tuple[dict, list[str]]:
    """Stamp ``datatype`` onto plain columns the data proves are numeric.

    An untyped numeric literal is the quietest defect in the whole pipeline:
    every gate passes, the ingest succeeds, and then SPARQL compares the values
    as STRINGS. Observed live — "which angle has the highest intensity?" answered
    77.47° (intensity 9.4) instead of 40.07° (intensity 100.0), because "9.4"
    sorts above "100.0" lexically. No error, a confident wrong answer with
    provenance attached.

    The inspector already knows the column is numeric, so this is a mechanical
    requirement the machine can meet instead of hoping the model does (the same
    posture as the borrowed-column backstop). Only rows that carry a bare
    ``column`` and NO datatype are touched: a ``function`` decides its own output
    type, an IRI object is not a literal, and an explicit datatype is the model's
    (or a human's) call. Returns the result plus the columns typed, for reporting.
    """
    props = result.get("properties")
    if not column_types or not isinstance(props, list):
        return dict(result), []
    typed: list[str] = []
    out: list[Any] = []
    for prop in props:
        if not isinstance(prop, Mapping):
            out.append(prop)
            continue
        col = prop.get("column")
        eligible = (
            isinstance(col, str)
            and col in column_types
            and not prop.get("datatype")
            and not prop.get("function")
            and not prop.get("transform")
            and prop.get("object_type") != "iri"
        )
        if eligible:
            out.append({**prop, "datatype": column_types[str(col)]})
            typed.append(str(col))
        else:
            out.append(prop)
    if not typed:
        return dict(result), []
    return {**result, "properties": out}, typed


def apply_data_facts(
    ir: Mapping[str, Any],
    *,
    column_owners: Mapping[str, Mapping[str, str]] | None = None,
    column_types: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Re-assert on a WHOLE IR what the data proved: ownership and numeric types.

    ``drop_borrowed_properties`` and ``apply_numeric_datatypes`` ran on each map's
    round-0 table — and then a self-correction round handed §9 back to the model,
    which rewrote it from memory: the borrowed columns came back and every
    ``datatype`` was gone (live: a rebuilt XRD dataset, 2 autocorrect rounds, 0
    datatypes in the saved mapping). A fact the machine derived from the rows
    must not depend on which LLM round happened to be last, so the SAME two
    normalisations run on the assembled IR after every round. Idempotent; a map
    with no verdict is untouched; anything not a plain ``column:`` binding is
    left alone (see the two helpers for the exact rules).

    Returns the new IR and ``{map: [changed columns]}`` for reporting.
    """
    maps = ir.get("maps")
    if not isinstance(maps, list):
        return dict(ir), {}
    changed: dict[str, list[str]] = {}
    out_maps: list[Any] = []
    for m in maps:
        if not isinstance(m, Mapping) or not isinstance(m.get("properties"), list):
            out_maps.append(m)
            continue
        name = str(m.get("name") or "")
        table: Mapping[str, Any] = {"properties": m["properties"]}
        table, dropped = drop_borrowed_properties(table, (column_owners or {}).get(name))
        table, typed = apply_numeric_datatypes(table, (column_types or {}).get(name))
        if dropped or typed:
            changed[name] = [*dropped, *typed]
        out_maps.append({**m, "properties": table["properties"]})
    if not changed:
        return dict(ir), {}
    return {**ir, "maps": out_maps}, changed


def _display_meta_matches(
    prop: Mapping[str, Any], map_name: str, map_source: str, edit: Mapping[str, Any]
) -> bool:
    """Is this property row the one the human corrected?

    ``predicate`` is compared on its LAST segment (after ``:``, ``#`` or ``/``),
    so the same row matches whether the client sent the expanded IRI, the CURIE
    the design wrote, or the CURIE under a prefix that has since been re-derived
    (K13 renames those mechanically). ``map`` / ``column``, when given, narrow
    it further — one predicate can legitimately be bound by two maps.
    """
    want = str(edit.get("predicate") or "")
    if not want:
        return False
    wanted_source = str(edit.get("source") or "")
    if wanted_source and wanted_source != map_source:
        return False
    predicate_matches = _term_tail(str(prop.get("predicate") or "")) == _term_tail(want)
    wanted_col = str(edit.get("column") or "")
    # A human-added fallback predicate may acquire a deterministic suffix when an
    # AI rewrite introduces a colliding term. Source+column+fallback is its stable
    # identity; the original predicate spelling is not.
    fallback_identity_matches = bool(
        prop.get("fallback")
        and wanted_source
        and wanted_source == map_source
        and wanted_col
        and wanted_col == str(prop.get("column") or "")
    )
    if not predicate_matches and not fallback_identity_matches:
        return False
    wanted_map = str(edit.get("map") or "")
    if wanted_map and wanted_map != map_name:
        return False
    return not (wanted_col and wanted_col != str(prop.get("column") or ""))


def _term_tail(term: str) -> str:
    for sep in ("#", "/", ":"):
        if sep in term:
            term = term.rsplit(sep, 1)[-1]
    return term


def apply_display_meta(
    ir: Mapping[str, Any], edits: Sequence[Mapping[str, Any]]
) -> tuple[dict, list[str]]:
    """Set the human's ``label`` / ``unit`` on the matching §9 property rows.

    Display metadata ONLY (ADR K8): the meaning of a column and the notation of
    its unit are what a reviewer reads — no triple, no value and no datatype
    changes here. The meaning of a column is knowledge the person who measured it
    holds, so it must be settable without asking a model to rewrite the design
    (KZ-B-05), and it must survive the models that come later: this same function
    re-asserts it after an AI round (ADR data-facts-invariant N6).

    An empty string CLEARS the field (the human saying "this was wrong and I have
    nothing better"); an absent key leaves it alone. Returns the new IR and the
    columns/predicates actually changed, so the caller can say what it did.
    """
    maps = ir.get("maps")
    if not isinstance(maps, list) or not edits:
        return dict(ir), []
    changed: list[str] = []
    out_maps: list[Any] = []
    for m in maps:
        if not isinstance(m, Mapping) or not isinstance(m.get("properties"), list):
            out_maps.append(m)
            continue
        name = str(m.get("name") or "")
        source = str(m.get("source") or "")
        props: list[Any] = []
        for prop in m["properties"]:
            if not isinstance(prop, Mapping):
                props.append(prop)
                continue
            row = dict(prop)
            touched = False
            for edit in edits:
                if not _display_meta_matches(prop, name, source, edit):
                    continue
                for field_name in ("label", "unit"):
                    if field_name not in edit:
                        continue
                    value = edit.get(field_name)
                    if value is None:
                        continue
                    text = str(value).strip()
                    if text == row.get(field_name) or (not text and field_name not in row):
                        continue
                    if text:
                        row[field_name] = text
                    else:
                        row.pop(field_name, None)
                    touched = True
            if touched:
                changed.append(str(row.get("column") or row.get("predicate") or ""))
                props.append(row)
            else:
                props.append(prop)
        out_maps.append({**m, "properties": props} if props else m)
    if not changed:
        return dict(ir), []
    return {**ir, "maps": out_maps}, changed


def apply_display_meta_to_document(
    document_md: str, edits: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    """:func:`apply_display_meta`, spliced back into a design document's §9.

    Byte-preserving outside the mapping-spec block. Raises ``ValueError`` when the
    document has no §9 to edit (a legacy raw-RML design — there is no display
    metadata to carry, and the caller says so instead of pretending it worked).
    """
    import yaml

    from asterism_step0.materialize import materialize_schema
    from asterism_step0.spec_repair import replace_mapping_spec_block

    ir_yaml = materialize_schema(document_md, ".", "display-meta", write=False).mapping_ir_yaml
    if ir_yaml is None:
        raise ValueError("this design has no mapping spec to edit")
    # A §9 a weak model left unparseable is a routine outcome, not a crash: the
    # caller (the refine tail, the S6 edit) has to keep going and let the normal
    # validation report it, so every unreadable spec leaves here as ValueError.
    try:
        doc = yaml.safe_load(ir_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"the design's mapping spec is not readable: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("the design's mapping spec is not a mapping")
    new_doc, changed = apply_display_meta(doc, edits)
    if not changed:
        return document_md, []
    new_yaml = yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)
    return replace_mapping_spec_block(document_md, new_yaml), changed


_SAFE_COLUMN_DATATYPES = frozenset(
    {"xsd:integer", "xsd:double", "xsd:date", "xsd:dateTime", "xsd:string"}
)


def _expanded_mapping_term(term: str, prefixes: Mapping[str, Any]) -> str:
    """Expand a CURIE for identity checks; absolute/unknown terms pass through."""
    head, separator, tail = term.partition(":")
    namespace = prefixes.get(head) if separator else None
    return f"{namespace}{tail}" if isinstance(namespace, str) else term


def _decision_map(
    ir: Mapping[str, Any], decision: Mapping[str, Any]
) -> tuple[Mapping[str, Any], str, str, str]:
    """Resolve a human column decision to its current map.

    The authored map name is the first choice. A later AI structural rewrite may
    rename it, so a persisted include also carries the target class and can
    recover by ``source + class`` (or by a source that now has exactly one map).
    """
    map_name = str(decision.get("map") or "").strip()
    source = str(decision.get("source") or "").strip()
    column = str(decision.get("column") or "").strip()
    if not source or not column:
        raise ValueError("a column decision requires non-empty source and column")
    maps = ir.get("maps")
    if not isinstance(maps, list):
        raise ValueError("mapping spec has no maps")
    source_maps = [
        m
        for m in maps
        if isinstance(m, Mapping) and str(m.get("source") or "") == source
    ]
    map_class = str(decision.get("map_class") or "").strip()
    if map_class:
        prefixes = ir.get("prefixes")
        prefix_map = prefixes if isinstance(prefixes, Mapping) else {}
        wanted_class = _expanded_mapping_term(map_class, prefix_map)
        exact_class = [
            m
            for m in source_maps
            if wanted_class
            in {
                _expanded_mapping_term(str(cls), prefix_map)
                for cls in (
                    ((m.get("subject") or {}).get("classes") or [])
                    if isinstance(m.get("subject"), Mapping)
                    else []
                )
            }
        ]
        if len(exact_class) == 1:
            found = exact_class[0]
            return found, str(found.get("name") or ""), source, column
    exact = next(
        (m for m in maps if isinstance(m, Mapping) and str(m.get("name") or "") == map_name),
        None,
    )
    # With a persisted class, a same-named map is not enough when several maps
    # read this source: an AI rewrite may rename the original and reuse its old
    # name for a different entity. The class match above owns that decision.
    if (
        exact is not None
        and str(exact.get("source") or "") == source
        and (not map_class or len(source_maps) == 1)
    ):
        return exact, map_name, source, column
    if len(source_maps) == 1:
        found = source_maps[0]
        return found, str(found.get("name") or ""), source, column
    if exact is not None:
        raise ValueError(
            f"column decision source {source!r} does not match map {map_name!r}"
        )
    raise ValueError(
        f"column decision map {map_name!r} no longer exists and its owner is ambiguous"
    )


def _column_decision_prefix(ir: Mapping[str, Any], map_obj: Mapping[str, Any]) -> str:
    """Pick this dataset's ontology prefix, not a borrowed vocabulary prefix."""
    prefixes = ir.get("prefixes")
    if not isinstance(prefixes, Mapping):
        raise ValueError("mapping spec has no prefixes for a human-added property")
    own = next(
        (
            str(name)
            for name, iri in prefixes.items()
            if isinstance(iri, str) and "/ontology#" in iri
        ),
        None,
    )
    if own:
        return own
    subject = map_obj.get("subject")
    classes = (subject or {}).get("classes") if isinstance(subject, Mapping) else []
    if isinstance(classes, list):
        for cls in classes:
            prefix = str(cls).split(":", 1)[0]
            if prefix in prefixes:
                return prefix
    if prefixes:
        return str(next(iter(prefixes)))
    raise ValueError("mapping spec has no prefixes for a human-added property")


def _column_predicate(
    ir: Mapping[str, Any], map_obj: Mapping[str, Any], column: str
) -> str:
    """A deterministic term that does not collide anywhere in this ontology."""
    prefix = _column_decision_prefix(ir, map_obj)
    words = [part for part in re.split(r"[^A-Za-z0-9]+", column) if part]
    # A Unicode-only header is a valid CSV header but not a portable CURIE local
    # name. Hash its original UTF-8 spelling rather than dropping its identity.
    local = "has" + "".join(word[:1].upper() + word[1:] for word in words)
    digest = hashlib.sha256(column.encode("utf-8")).hexdigest()[:10]
    if not words or local == "has":
        local = f"column_{digest}"
    candidate = f"{prefix}:{local}"
    prefixes = ir.get("prefixes") if isinstance(ir.get("prefixes"), Mapping) else {}

    predicates = {
        _expanded_mapping_term(str(prop.get("predicate") or ""), prefixes)
        for mapping in (ir.get("maps") or [])
        if isinstance(mapping, Mapping)
        for prop in (mapping.get("properties") or [])
        if isinstance(prop, Mapping)
    }
    if _expanded_mapping_term(candidate, prefixes) in predicates:
        candidate = f"{prefix}:{local}_{digest}"
    suffix = 2
    while _expanded_mapping_term(candidate, prefixes) in predicates:
        candidate = f"{prefix}:{local}_{digest}_{suffix}"
        suffix += 1
    return candidate


_COLUMN_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _template_uses_column(template: object, column: str) -> bool:
    return isinstance(template, str) and column in _COLUMN_PLACEHOLDER.findall(template)


def _property_uses_column(prop: Mapping[str, Any], column: str) -> bool:
    return (
        str(prop.get("column") or "") == column
        or column in (prop.get("columns") or [])
        or _template_uses_column(prop.get("object_template"), column)
    )


def apply_column_decisions(
    ir: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    *,
    source_columns: Mapping[str, Mapping[str, str] | Sequence[str]] | None = None,
) -> tuple[dict, list[str]]:
    """Apply human include/exclude decisions to a Mapping IR.

    Includes add a deliberately plain, raw-passthrough direct property. Excludes
    leave an already-unmapped IR untouched, but remove a later AI rewrite's use of
    that column. The helper is idempotent so it can reassert both decisions.
    ``source_columns`` is an optional closed-set oracle (source -> columns, or
    source -> inferred datatype) used by the API before anything is persisted.
    """
    if not decisions:
        return dict(ir), []
    maps = ir.get("maps")
    if not isinstance(maps, list):
        raise ValueError("mapping spec has no maps")
    out_maps: list[Any] = [dict(m) if isinstance(m, Mapping) else m for m in maps]
    changed: list[str] = []
    for decision in decisions:
        action = str(decision.get("action") or "").strip()
        if action not in {"include", "exclude"}:
            raise ValueError("column decision action must be 'include' or 'exclude'")
        source = str(decision.get("source") or "").strip()
        column = str(decision.get("column") or "").strip()
        if not source or not column:
            raise ValueError("a column decision requires non-empty source and column")
        if source_columns is not None:
            known = source_columns.get(source)
            if known is None or column not in known:
                raise ValueError(f"source {source!r} has no column {column!r}")
        if action == "exclude":
            for target_i, current in enumerate(out_maps):
                if not isinstance(current, Mapping) or str(current.get("source") or "") != source:
                    continue
                subject = current.get("subject")
                subject_template = (
                    subject.get("template") if isinstance(subject, Mapping) else None
                )
                if _template_uses_column(subject_template, column):
                    raise ValueError(
                        f"excluded column {column!r} is an identifier for map "
                        f"{str(current.get('name') or '')!r} and cannot be removed safely"
                    )
                properties = current.get("properties")
                if not isinstance(properties, list):
                    continue
                kept = [
                    prop
                    for prop in properties
                    if not (isinstance(prop, Mapping) and _property_uses_column(prop, column))
                ]
                if len(kept) != len(properties):
                    if not kept:
                        raise ValueError(
                            f"excluded column {column!r} is the only property of map "
                            f"{str(current.get('name') or '')!r} and cannot be removed safely"
                        )
                    out_maps[target_i] = {**current, "properties": kept}
                    if column not in changed:
                        changed.append(column)
            continue
        _map_obj, map_name, source, column = _decision_map(ir, decision)
        for other_i, current in enumerate(out_maps):
            if (
                not isinstance(current, Mapping)
                or str(current.get("source") or "") != source
                or str(current.get("name") or "") == map_name
            ):
                continue
            properties = current.get("properties")
            if not isinstance(properties, list):
                continue
            kept = [
                prop
                for prop in properties
                if not (
                    isinstance(prop, Mapping)
                    and prop.get("fallback") is True
                    and str(prop.get("column") or "") == column
                )
            ]
            if len(kept) != len(properties):
                if not kept:
                    raise ValueError(
                        f"column {column!r} cannot move from map "
                        f"{str(current.get('name') or '')!r} safely"
                    )
                out_maps[other_i] = {**current, "properties": kept}
                if column not in changed:
                    changed.append(column)
        label = str(decision.get("label") or "").strip()
        if not label:
            raise ValueError("an include decision requires a non-empty label")
        target_i = next(
            i
            for i, m in enumerate(out_maps)
            if isinstance(m, Mapping) and str(m.get("name") or "") == map_name
        )
        target = dict(out_maps[target_i])
        props = list(target.get("properties") or [])
        row_i = next(
            (
                i
                for i, prop in enumerate(props)
                if isinstance(prop, Mapping) and str(prop.get("column") or "") == column
            ),
            None,
        )
        if row_i is None:
            target["properties"] = props
            row: dict[str, Any] = {
                "predicate": _column_predicate({**ir, "maps": out_maps}, target, column),
                "column": column,
                "fallback": True,
                "label": label,
            }
            known_types = source_columns.get(source) if source_columns is not None else None
            inferred = (
                known_types.get(column)
                if isinstance(known_types, Mapping)
                else decision.get("datatype")
            )
            if inferred in _SAFE_COLUMN_DATATYPES:
                row["datatype"] = inferred
            unit = decision.get("unit")
            if unit is not None and str(unit).strip():
                row["unit"] = str(unit).strip()
            props.append(row)
            if column not in changed:
                changed.append(column)
        else:
            row = dict(props[row_i])
            touched = False
            if row.get("label") != label:
                row["label"] = label
                touched = True
            if "unit" in decision and decision.get("unit") is not None:
                unit = str(decision.get("unit") or "").strip()
                if unit and row.get("unit") != unit:
                    row["unit"] = unit
                    touched = True
                elif not unit and "unit" in row:
                    row.pop("unit")
                    touched = True
            known_types = source_columns.get(source) if source_columns is not None else None
            datatype = (
                known_types.get(column)
                if isinstance(known_types, Mapping)
                else decision.get("datatype")
            )
            if datatype in _SAFE_COLUMN_DATATYPES and row.get("datatype") != datatype:
                row["datatype"] = datatype
                touched = True
            if touched:
                props[row_i] = row
                if column not in changed:
                    changed.append(column)
        target["properties"] = props
        out_maps[target_i] = target
    if not changed:
        return dict(ir), []
    return {**ir, "maps": out_maps}, changed


def remove_stale_column_includes_from_document(
    document_md: str, decisions: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    """Remove fallback rows created by include decisions whose source column vanished."""
    import yaml

    from asterism_step0.materialize import materialize_schema
    from asterism_step0.spec_repair import replace_mapping_spec_block

    stale = {
        (str(decision.get("source") or ""), str(decision.get("column") or ""))
        for decision in decisions
        if decision.get("action") == "include"
    }
    if not stale:
        return document_md, []
    ir_yaml = materialize_schema(document_md, ".", "column-decisions", write=False).mapping_ir_yaml
    if ir_yaml is None:
        raise ValueError("this design has no mapping spec to edit")
    try:
        doc = yaml.safe_load(ir_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"the design's mapping spec is not readable: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("maps"), list):
        raise ValueError("the design's mapping spec has no maps")
    changed: list[str] = []
    out_maps: list[Any] = []
    for current in doc["maps"]:
        if not isinstance(current, Mapping) or not isinstance(current.get("properties"), list):
            out_maps.append(current)
            continue
        source = str(current.get("source") or "")
        properties = current["properties"]
        kept = [
            prop
            for prop in properties
            if not (
                isinstance(prop, Mapping)
                and prop.get("fallback") is True
                and (source, str(prop.get("column") or "")) in stale
            )
        ]
        if len(kept) == len(properties):
            out_maps.append(current)
            continue
        if not kept:
            raise ValueError(
                f"stale human-added columns are the only properties of map "
                f"{str(current.get('name') or '')!r} and cannot be removed safely"
            )
        changed.extend(
            str(prop.get("column") or "")
            for prop in properties
            if isinstance(prop, Mapping)
            and prop.get("fallback") is True
            and (source, str(prop.get("column") or "")) in stale
        )
        out_maps.append({**current, "properties": kept})
    if not changed:
        return document_md, []
    new_doc = {**doc, "maps": out_maps}
    return replace_mapping_spec_block(document_md, mapping_ir_to_yaml(new_doc)), list(
        dict.fromkeys(changed)
    )


def apply_column_decisions_to_document(
    document_md: str,
    decisions: Sequence[Mapping[str, Any]],
    *,
    source_columns: Mapping[str, Mapping[str, str] | Sequence[str]] | None = None,
) -> tuple[str, list[str]]:
    """Apply :func:`apply_column_decisions` and splice the resulting §9 block."""
    import yaml

    from asterism_step0.materialize import materialize_schema
    from asterism_step0.spec_repair import replace_mapping_spec_block

    ir_yaml = materialize_schema(document_md, ".", "column-decisions", write=False).mapping_ir_yaml
    if ir_yaml is None:
        raise ValueError("this design has no mapping spec to edit")
    try:
        doc = yaml.safe_load(ir_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"the design's mapping spec is not readable: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("the design's mapping spec is not a mapping")
    new_doc, changed = apply_column_decisions(doc, decisions, source_columns=source_columns)
    if not changed:
        return document_md, []
    return replace_mapping_spec_block(document_md, mapping_ir_to_yaml(new_doc)), changed


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
    owned_elsewhere: Mapping[str, str] | None = None,
) -> dict:
    """One guided call -> one map's ``{properties: [...], prefixes?: {...}}``."""
    user = build_permap_user(
        map_name,
        map_skeleton,
        skeleton_context,
        menu,
        issues=issues,
        language=language,
        owned_elsewhere=owned_elsewhere,
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


_SKELETON_PARSE_ROUNDS = 1
"""How many times a skeleton whose JSON could not be read is asked again.

The per-map stage already has bounded structural rounds; the skeleton had zero,
so a model without guided decoding that emitted a stray fence turned S3 into a
raw ``model output is not valid JSON/YAML`` error whose only exit was the person
pressing the same button again. One retry (with the parse error fed back), then
the deterministic default — the retries stay bounded so a model that cannot
produce JSON does not burn a job's worth of tokens proving it."""


def _generate_skeleton_gated(
    inspection_md: str,
    domain_hint: str,
    *,
    llm: LLMClient,
    function_names: Sequence[str] | None,
    language: str | None,
    iri_base: str | None,
    inspections: Sequence[SourceInspection],
    dataset_name: str | None,
) -> tuple[dict, bool]:
    """The skeleton, with a bounded retry and a deterministic floor.

    Returns ``(skeleton, used_fallback)``. Only an unusable ANSWER
    (:data:`_UNUSABLE_ANSWER`) reaches the floor: cancellation, a bad key and an
    unreachable provider propagate untouched, because a person who pressed stop
    — or whose AI never ran — must not be handed a design instead.
    """
    issues: list[str] | None = None
    for attempt in range(_SKELETON_PARSE_ROUNDS + 1):
        try:
            skeleton = generate_skeleton(
                inspection_md,
                domain_hint,
                llm=llm,
                function_names=function_names,
                language=language,
                iri_base=iri_base,
                issues=issues,
            )
        except LLMCancelledError:
            raise
        except _UNUSABLE_ANSWER as exc:  # unreadable JSON, truncation, empty answer
            issues = [" ".join(str(exc).split())[:400]]
            if attempt >= _SKELETON_PARSE_ROUNDS:
                break
            continue
        if isinstance(skeleton.get("maps"), list) and skeleton["maps"]:
            return skeleton, False
        # Parsed, but says nothing — same dead end as a parse failure.
        issues = ["the JSON object had no `maps` entries; every source needs one map"]
        if attempt >= _SKELETON_PARSE_ROUNDS:
            break
    return (
        default_skeleton(inspections, iri_base=iri_base, dataset_name=dataset_name),
        True,
    )


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
    skeleton, fallback = _generate_skeleton_gated(
        inspection_md,
        domain_hint,
        llm=llm,
        function_names=names,
        language=language,
        iri_base=iri_base,
        inspections=inspections,
        dataset_name=Path(csv_paths[0]).stem if csv_paths else None,
    )
    # Canonical namespace shape is a machine requirement, not a model skill
    # (kantan ADR K13): repair base/shape drift and derive the prefix pair
    # deterministically from the minted slug, so the gate never asks a human
    # to judge a name that cannot matter.
    skeleton = normalize_dataset_namespace(
        skeleton,
        iri_base,
        fallback_slug=slugify_dataset_name(Path(csv_paths[0]).stem) if csv_paths else None,
    )
    metadata: dict[str, Any] = {"llm_class": type(llm).__name__}
    if fallback:
        metadata["fallback"] = True
    return SkeletonProposal(
        skeleton=skeleton,
        csv_inspection_md=inspection_md,
        domain_hint=domain_hint,
        metadata=metadata,
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
    owned_elsewhere: Mapping[str, str] | None = None,
    column_types: Mapping[str, str] | None = None,
    source_columns: Sequence[str] | None = None,
    ontology_prefix: str | None = None,
    on_fallback: Callable[[str], None] | None = None,
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
    assembly-stage parse + §9 surgical repair.

    A map whose ANSWER is unusable — unreadable JSON, a truncated answer, a
    reasoning-only answer (:data:`_UNUSABLE_ANSWER`) — degrades to the
    DETERMINISTIC default table (this map's own columns, sourced and typed) and
    the run continues; the other maps' results are kept either way. An empty
    table used to make the whole entity vanish from the review screen, so one
    broken JSON object silently cost the person a kind of thing and every column
    on it. A provider failure (bad key, unreachable, rate limit) still
    propagates: no AI ran, and that is something the person must be told."""

    def _emit(message: str) -> None:
        emit(phase=f"map:{map_name}", index=index, total=total, message=message)

    def _fallback(reason: str) -> dict:
        table = default_property_table(
            source_columns or [],
            ontology_prefix=ontology_prefix or "",
            owned_elsewhere=owned_elsewhere,
            column_types=column_types,
        )
        if table["properties"] and ontology_prefix:
            _emit(
                f"map '{map_name}': AI が項目を書けなかったため、"
                f"{len(table['properties'])} 列の列名をそのまま項目名にしました: {reason}"
            )
        else:
            table = {"properties": []}
            _emit(f"map '{map_name}' の生成に失敗しプロパティ無しで継続します: {reason}")
        if on_fallback is not None:
            on_fallback(map_name)
        return table

    try:
        result = generate_map_properties(
            map_name, map_skeleton, skeleton_context, menu_text,
            llm=llm, function_names=function_names, language=language,
            owned_elsewhere=owned_elsewhere,
        )
    except LLMCancelledError:
        raise  # a person pressed stop — that must never become a design
    except _UNUSABLE_ANSWER as exc:
        # ValueError (unreadable JSON) was the only case caught before, so a
        # truncated or reasoning-only answer (RuntimeError subclasses) killed the
        # whole continue job and discarded every other map's result with it.
        record()
        return _fallback(" ".join(str(exc).split())[:200])
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
                owned_elsewhere=owned_elsewhere,
            )
        except LLMCancelledError:
            raise
        except _UNUSABLE_ANSWER:
            # A failed retry (unreadable / truncated / empty): the LLM call still
            # happened, so record its usage like every other call, then keep the
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
    # G6 backstop: the instruction above is a request; this is the guarantee.
    # A column another map owns never becomes a plain property here, whatever
    # the model returned — reported, never silently edited.
    result, dropped = drop_borrowed_properties(result, owned_elsewhere)
    if dropped:
        _emit(
            f"map '{map_name}': 他のマップが持つ列を外しました - 重複記録の防止: "
            + ", ".join(sorted(set(dropped)))
        )
    # Numeric columns get their datatype from the data, not from the model's
    # memory — an untyped number compares as a string in SPARQL.
    result, typed = apply_numeric_datatypes(result, column_types)
    if typed:
        _emit(
            f"map '{map_name}': 数値列に型を付けました - 文字列比較の防止: "
            + ", ".join(sorted(set(typed)))
        )
    return result


def _ontology_prefix(skeleton: Mapping[str, Any]) -> str:
    """This design's own ontology CURIE prefix — where a default predicate lands.

    The minted ``…/ontology#`` namespace first (K13 makes that pair
    deterministic), then whatever prefix the skeleton's own classes use. An
    empty result disables the default property table rather than inventing a
    namespace: a predicate under a made-up prefix is a fact with no home.
    """
    prefixes = skeleton.get("prefixes")
    if isinstance(prefixes, Mapping):
        for name, iri in prefixes.items():
            if str(iri).endswith("ontology#"):
                return str(name)
    for map_obj in skeleton.get("maps") or []:
        if not isinstance(map_obj, Mapping):
            continue
        subject = map_obj.get("subject")
        classes = subject.get("classes") if isinstance(subject, Mapping) else None
        for cls in classes or []:
            if isinstance(cls, str) and ":" in cls and not cls.startswith(("http://", "https://")):
                return cls.split(":", 1)[0]
    return ""


def _synthesize_document(ir_yaml: str, *, dataset_name: str | None) -> str | None:
    """The §1-9 Markdown written from the assembled spec alone, or None.

    None when the spec does not parse — then there is nothing trustworthy to
    describe and the original failure must surface as it always did.
    """
    try:
        from asterism_step0.doc_synth import synthesize_document
        from asterism_step0.mapping_ir import parse_mapping_ir

        ir = parse_mapping_ir(ir_yaml)
        return synthesize_document(ir, ir_yaml, dataset_name=dataset_name or "dataset")
    except Exception:
        return None


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
    column_owners: Mapping[str, Mapping[str, str]] | None = None,
    column_types: Mapping[str, Mapping[str, str]] | None = None,
    map_columns: Mapping[str, Sequence[str]] | None = None,
    dataset_name: str | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> str:
    """Job 2: from a confirmed skeleton, generate each map's property table, assemble
    the full IR, generate the §1-8 document, and splice §9 in deterministically.
    Returns the §1-9 Markdown (the same artifact the single call produced — the
    self-correction gates run on it in the API layer, unchanged).

    ``menu`` is the closed reference (the API passes the oracle's exact columns +
    function signatures); when omitted a names-only menu is rendered so the stage
    still runs standalone. ``on_progress(**data)`` receives ``phase`` frames per map;
    ``on_llm_call(feature)`` fires after every per-map and document call so the caller
    records usage per call (each tagged ``"propose"``, like the single-shot round-0).

    ``column_owners`` maps a map name to ``{column: owning map}`` — the skeleton
    gate's ownership verdict (ADR column-ownership-and-growth). Those columns are
    named in that map's prompt AND dropped from its result if they come back
    anyway, so one source cell becomes a fact on exactly one entity.

    ``column_types`` maps a map name to ``{column: xsd type}`` for the columns the
    DATA proves numeric; those properties get that ``datatype`` stamped on, so a
    number never lands in the store as a string (where SPARQL compares it
    lexically and quietly returns the wrong maximum).

    ``map_columns`` maps a map name to ITS source's real columns; it is the raw
    material for the deterministic default table used when a map's generation
    fails. Omitted, the columns are recovered from ``menu`` (the api's oracle
    already lists them per file), so the default works on every path.
    ``on_fallback(map_name)`` fires for each map that ended up on that default —
    the caller can record which kinds got column-name meanings."""
    names = _resolve_function_names(function_names)
    menu_text = menu if menu is not None else render_tier0_menu(names)
    context = render_skeleton_context(skeleton)
    by_source = dict(menu_columns(menu_text)) if map_columns is None else {}
    ontology_prefix = _ontology_prefix(skeleton)

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
            # Which of this map's columns another map owns (ADR
            # column-ownership G6) — the gate's verdict, carried into generation.
            owned_elsewhere=(column_owners or {}).get(str(name)),
            column_types=(column_types or {}).get(str(name)),
            source_columns=(
                (map_columns or {}).get(str(name))
                or by_source.get(str(map_obj.get("source") or ""))
            ),
            ontology_prefix=ontology_prefix,
            on_fallback=on_fallback,
        )

    assembled = assemble_mapping_ir(skeleton, permaps)
    ir_yaml = mapping_ir_to_yaml(assembled)

    emit(phase="document", message="設計文書を生成中")
    try:
        document_md = generate_document(
            ir_yaml, inspection_md, domain_hint, llm=llm, language=language
        )
        record()
    except LLMCancelledError:
        raise
    except Exception as exc:
        # §1-8 is the write-up ABOUT the design; §9 above IS the design and is
        # already assembled. A model that ran out of tokens on the prose has not
        # cost the person their dataset — the machine writes the prose from the
        # spec instead of ending the run with a stop card whose only exit is the
        # same (too long) generation again.
        record()
        emit(
            phase="document",
            message="AI の説明文が最後まで届かなかったため、設計から自動生成しました: "
            + " ".join(str(exc).split())[:200],
        )
        synthesized = _synthesize_document(ir_yaml, dataset_name=dataset_name)
        if synthesized is not None:
            if on_fallback is not None:
                on_fallback("document")
            return synthesized
        raise
    return fill_mapping_spec_block(document_md, ir_yaml)
