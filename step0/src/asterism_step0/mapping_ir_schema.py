"""JSON Schema for the Mapping IR — the guided-decoding contract (Phase 2 / 2b).

Derived in code from the same closed sets the parser/compiler use (reserved
prefixes, object forms, the Tier-0 catalog), so there is exactly one source of
truth. A guided-decoding server (vLLM's response_format json_schema) uses it to
make off-spec output UNREPRESENTABLE at generation time — the error families
observed live (unknown fields like ``optional:``, type-cast pseudo-functions
like ``function: str``, cardinality-marked predicates like ``schema:author*``)
cannot even be emitted.

Three builders, one source of truth (Phase 2b staged generation splits the full
IR into a skeleton + per-map property tables — same shared sub-schemas):

* :func:`mapping_ir_json_schema` — the whole IR (round-0 single call / §9 repair).
* :func:`skeleton_json_schema` — subject-only maps (no ``properties``): which
  source becomes which class, keyed how. The early human-gate artifact.
* :func:`permap_json_schema` — one map's property table (+ optional prefix
  additions the predicates/datatypes introduce).

Deliberately grammar-friendly: no ``oneOf`` (uneven support across guided
decoders); the object-form exclusivity rules stay with the strict parser
(:mod:`asterism_step0.mapping_ir`), which remains the gate for ALL providers —
the schema narrows generation, it never replaces validation.
"""
from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "mapping_ir_json_schema",
    "permap_json_schema",
    "skeleton_json_schema",
]

# Term positions that must not carry an rdf-config cardinality suffix: any
# non-space chars, last char not one of * ? +  (single-char terms allowed).
_TERM_PATTERN = r"^\S*[^*?+\s]$"
_MAP_NAME_PATTERN = r"^[A-Za-z][\w-]*$"
_IRI_PATTERN = r"^https?://\S+$"


def _string(pattern: str | None = None) -> dict:
    out: dict = {"type": "string", "minLength": 1}
    if pattern:
        out["pattern"] = pattern
    return out


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
            },
        },
    }


def _subject_schema(function_names: Sequence[str] | None) -> dict:
    transform_obj = {"type": "object", "additionalProperties": _function_value(function_names)}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "template": _string(),
            "constant": _string(),
            "classes": {"type": "array", "items": _string(_TERM_PATTERN)},
            "transform": transform_obj,
        },
    }


def _property_row_schema(function_names: Sequence[str] | None) -> dict:
    function_value = _function_value(function_names)
    transform_obj = {"type": "object", "additionalProperties": function_value}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["predicate"],
        "properties": {
            "predicate": _string(_TERM_PATTERN),
            "column": _string(),
            "columns": {"type": "array", "items": _string(), "minItems": 1},
            "function": function_value,
            "args": {"type": "object", "additionalProperties": {"type": "string"}},
            "object_template": _string(),
            "constant": {"type": "string"},
            "object_type": {"type": "string", "enum": ["iri", "literal"]},
            "datatype": _string(),
            "language": _string(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$"),
            "transform": transform_obj,
            "fallback": {"type": "boolean"},
        },
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
                        "subject": _subject_schema(function_names),
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
