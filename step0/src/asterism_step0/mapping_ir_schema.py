"""JSON Schema for the Mapping IR — the guided-decoding contract (Phase 2 / 2b).

Derived in code from the same closed sets the parser/compiler use (reserved
prefixes, object forms, the Tier-0 catalog), so there is exactly one source of
truth. A guided-decoding server (vLLM's response_format json_schema) uses it to
make off-spec output UNREPRESENTABLE at generation time — the error families
observed live (unknown fields like ``optional:``, type-cast pseudo-functions
like ``function: str``, cardinality-marked predicates like ``schema:author*``)
cannot even be emitted.

The builders, one source of truth (Phase 2b staged generation splits the full
IR into a skeleton + per-map property tables — same shared sub-schemas):

* :func:`mapping_ir_json_schema` — the whole IR (round-0 single call / §9 repair).
* :func:`skeleton_json_schema` — subject-only maps (no ``properties``): which
  source becomes which class, keyed how. The early human-gate artifact.
* :func:`permap_json_schema` — one map's property table (+ optional prefix
  additions the predicates/datatypes introduce).
* :func:`column_meanings_json_schema` — what each source column MEANS, keyed by
  ``(source, column)``. No design exists yet at that point (ADR
  meaning-before-identity), so this one carries no IR shape at all.

Deliberately grammar-friendly: no ``oneOf`` (uneven support across guided
decoders); the object-form exclusivity rules stay with the strict parser
(:mod:`asterism_step0.mapping_ir`), which remains the gate for ALL providers —
the schema narrows generation, it never replaces validation.

Two things the grammar DOES enforce, because the strict parser can only report
them after the fact and a weak model does not recover (live 2026-08-18,
gpt-oss-120b, XRD card: 17 rows with no object form at all, ``unit`` strings
thousands of characters long, degenerate zero-width repetition):

* **an object form is present** — every property row branch (``anyOf`` of
  COMPLETE row schemas, one per form) requires ``column`` / ``columns`` /
  ``object_template`` / ``constant``. "got: none" becomes unrepresentable.
  ``anyOf`` of full object schemas is the one union shape every decoder
  (xgrammar / outlines / llama.cpp / OpenAI) compiles the same way; a sibling
  ``anyOf`` of bare ``required`` clauses is not.
* **bounded strings** — ``maxLength`` on every free-text field. A grammar
  forces the model to CLOSE the string, so a repetition loop inside ``unit``
  or ``label`` cannot eat the whole completion (and the round with it).

The client degrades ``json_schema`` → ``json_object`` → off when a server
rejects the schema, so a decoder that lacks a keyword costs guidance, never
the call.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "column_meanings_json_schema",
    "mapping_ir_json_schema",
    "permap_json_schema",
    "skeleton_json_schema",
]

# Term positions that must not carry an rdf-config cardinality suffix: any
# non-space chars, last char not one of * ? +  (single-char terms allowed).
_TERM_PATTERN = r"^\S*[^*?+\s]$"
_MAP_NAME_PATTERN = r"^[A-Za-z][\w-]*$"
_IRI_PATTERN = r"^https?://\S+$"


# String caps: generous for real content, fatal for a repetition loop. A CSV
# header, a CURIE, a unit — none is legitimately hundreds of characters.
_LEN_TERM = 200  # predicate / class / column header / datatype / map name
_LEN_TEMPLATE = 500  # IRI templates
_LEN_LABEL = 160  # human label
_LEN_UNIT = 40  # display unit ("W/(m·K)", "Ohm m", "Å³")
_LEN_CONSTANT = 2000  # a constant literal can be a sentence, not a page


def _string(pattern: str | None = None, *, max_length: int = _LEN_TERM) -> dict:
    out: dict = {"type": "string", "minLength": 1, "maxLength": max_length}
    if pattern:
        out["pattern"] = pattern
    return out


# The property-row object forms; exactly one must be present (parser-enforced
# exclusivity), at least one is grammar-enforced via ``anyOf`` below.
_OBJECT_FORMS = ("column", "columns", "object_template", "constant")


def _function_value(function_names: Sequence[str] | None) -> dict:
    """``function:`` / ``transform:`` value: a closed enum of the vetted Tier-0
    menu (a non-menu function then cannot be GENERATED), or a name-shaped string
    when the menu is omitted (schema stays registry-agnostic; validation gates)."""
    return (
        {"type": "string", "enum": sorted(function_names)}
        if function_names
        else _string(r"^[a-z][a-z0-9_]*$")
    )


def _prefixes_schema() -> dict:
    # No ``propertyNames``: some guided-decoding backends (Sakura vLLM) reject it
    # ("Grammar error: Unimplemented keys: [propertyNames]"). Prefix-NAME validity
    # is enforced by the strict parser (``mapping_ir._PREFIX_NAME``), not by the
    # schema — the schema only narrows generation, so dropping the key-name pattern
    # loses nothing the gate needs. Values stay constrained to IRI-shaped strings.
    return {
        "type": "object",
        "additionalProperties": _string(_IRI_PATTERN),
    }


def _dialects_schema() -> dict:
    # The optional per-source read dialects (ADR source-dialect.md). The design
    # pipeline overlays this section deterministically — the LLM never has to
    # author it — but a repair round-trips the whole IR, so the schema must be
    # able to REPRESENT it. Same guided-decoding constraint as ``prefixes``: no
    # ``propertyNames`` (Sakura vLLM rejects it) — filename↔source matching and
    # codec validity stay with the strict parser.
    return {
        "type": "object",
        "additionalProperties": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "encoding": _string(),
                "delimiter": _string(),
                "collapse": {"type": "boolean"},
                "skip_rows": {"type": "integer", "minimum": 0},
                "preamble": {
                    "type": "string",
                    "enum": ["drop", "keyvalue", "lines", "keyvalue_cells"],
                },
                "preamble_names": {
                    "type": "object",
                    "additionalProperties": _string(),
                },
            },
        },
    }


def _subject_schema(
    function_names: Sequence[str] | None, *, require_classes: bool = False
) -> dict:
    """``require_classes`` forces at least one class name.

    Used by the SKELETON stage only. A subject with no class is legal RDF and
    the compiler accepts it, but the human gate then has nothing to show for
    「1 件が表すもの」 — it prints an empty box and asks the person to name a
    kind the model was in the best position to name (live 2026-08-27: an XRD
    file came back with a third map called ``dataset`` and no class at all, so
    the gate asked for a name while the same screen quoted the machine's
    internal map name back as if it were the answer). Naming is cheapest where
    the columns are still in view, so the skeleton contract requires it.
    The full IR keeps it optional: an already-saved design must not stop
    parsing because of a rule added later.
    """
    transform_obj = {"type": "object", "additionalProperties": _function_value(function_names)}
    classes: dict = {"type": "array", "items": _string(_TERM_PATTERN)}
    if require_classes:
        classes["minItems"] = 1
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "template": _string(max_length=_LEN_TEMPLATE),
            "constant": _string(max_length=_LEN_TEMPLATE),
            "classes": classes,
            "transform": transform_obj,
        },
    }


def _property_row_fields(function_names: Sequence[str] | None) -> dict:
    function_value = _function_value(function_names)
    transform_obj = {"type": "object", "additionalProperties": function_value}
    return {
        "predicate": _string(_TERM_PATTERN),
        "column": _string(),
        "columns": {"type": "array", "items": _string(), "minItems": 1},
        "function": function_value,
        "args": {
            "type": "object",
            "additionalProperties": {"type": "string", "maxLength": _LEN_TEMPLATE},
        },
        "object_template": _string(max_length=_LEN_TEMPLATE),
        "constant": {"type": "string", "maxLength": _LEN_CONSTANT},
        "object_type": {"type": "string", "enum": ["iri", "literal"]},
        "datatype": _string(),
        "language": _string(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$"),
        "transform": transform_obj,
        "fallback": {"type": "boolean"},
        "label": _string(max_length=_LEN_LABEL),
        "unit": _string(max_length=_LEN_UNIT),
    }


def _property_row_schema(function_names: Sequence[str] | None) -> dict:
    """One property row: ``anyOf`` of COMPLETE row schemas, each requiring
    ``predicate`` plus one object form. Same fields in every branch — the
    branches differ only in ``required`` — so a decoder compiles it as a plain
    union of objects (no cross-branch merging needed)."""
    fields = _property_row_fields(function_names)
    return {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["predicate", form],
                "properties": fields,
            }
            for form in _OBJECT_FORMS
        ]
    }


def _properties_array(function_names: Sequence[str] | None) -> dict:
    return {"type": "array", "minItems": 1, "items": _property_row_schema(function_names)}


def mapping_ir_json_schema(function_names: Sequence[str] | None = None) -> dict:
    """The whole Mapping IR as a JSON Schema object (draft 2020-12 compatible).

    ``function_names`` (the vetted Tier-0 menu, e.g. from
    ``FunctionCatalog.names()``) turns ``function:`` / ``transform:`` values
    into a closed enum — a non-menu function then cannot be GENERATED, not
    merely gets rejected later. Omit it (None) for a name-shaped string
    (schema stays registry-agnostic, validation still gates).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "prefixes", "maps"],
        "properties": {
            "version": {"const": 1},
            "prefixes": _prefixes_schema(),
            "dialects": _dialects_schema(),
            "maps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "source", "subject", "properties"],
                    "properties": {
                        "name": _string(_MAP_NAME_PATTERN),
                        "source": _string(),
                        "iterator": _string(),
                        "subject": _subject_schema(function_names),
                        "properties": _properties_array(function_names),
                    },
                },
            },
        },
    }


def skeleton_json_schema(function_names: Sequence[str] | None = None) -> dict:
    """The IR SKELETON (Phase 2b): the same top-level shape, but each map carries
    only ``{name, source, iterator?, subject}`` — no ``properties`` yet — plus an
    optional free-text ``note`` explaining the subject-key choice (a hint for the
    human gate; dropped from the final IR at assembly). Reuses the same
    ``subject`` and ``prefixes`` sub-schemas as the full IR (one source of truth).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "prefixes", "maps"],
        "properties": {
            "version": {"const": 1},
            "prefixes": _prefixes_schema(),
            "maps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "source", "subject"],
                    "properties": {
                        "name": _string(_MAP_NAME_PATTERN),
                        "source": _string(),
                        "iterator": _string(),
                        "subject": _subject_schema(function_names, require_classes=True),
                        "note": _string(),
                    },
                },
            },
        },
    }


def permap_json_schema(function_names: Sequence[str] | None = None) -> dict:
    """ONE map's property table (Phase 2b per-map step): ``{properties: [...]}``
    with the same ``property_row`` sub-schema as the full IR, plus an optional
    ``prefixes`` object for any vocab a predicate/datatype introduces that the
    skeleton did not already declare (assembly unions them; ``_check_curies``
    gates the result). The subject/classes are fixed by the confirmed skeleton
    and are NOT re-emitted here.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["properties"],
        "properties": {
            "properties": _properties_array(function_names),
            "prefixes": _prefixes_schema(),
        },
    }


def labelfill_json_schema() -> dict:
    """The targeted label-fill re-ask: ONLY the missing labels come back, keyed
    by the predicate they belong to. ``label`` stays optional in the property
    row schema (a required label makes weak models fail the WHOLE row), so this
    tiny schema is where a dropped label gets a second, focused chance — the
    rows themselves are never re-emitted, so the retry cannot break a binding.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["labels"],
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["predicate", "label"],
                    "properties": {
                        "predicate": {"type": "string"},
                        "label": {"type": "string"},
                    },
                },
            },
        },
    }


def column_meanings_json_schema() -> dict:
    """The COLUMN-MEANING ask: what each source column MEANS — before any design.

    ADR ``meaning-before-identity.md``. The meaning and unit of a column are a
    property of the COLUMN (the data decides them); the design is the human's
    judgement built ON TOP of them. So this stage runs first and its answer
    carries no predicate, no map and no class — the only identity a column has
    before a skeleton exists is ``(source, column)``, and both are copied from
    the question so the answer can be filed deterministically.

    ``unit`` is optional: most columns do not carry a physical quantity, and a
    required unit makes a weak model invent one. Same bounded strings as every
    other stage (a repetition loop inside ``label``/``unit`` cannot eat the
    completion).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["columns"],
        "properties": {
            "columns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "column", "label"],
                    "properties": {
                        "source": _string(),
                        "column": _string(),
                        "label": _string(max_length=_LEN_LABEL),
                        "unit": _string(max_length=_LEN_UNIT),
                    },
                },
            },
        },
    }
