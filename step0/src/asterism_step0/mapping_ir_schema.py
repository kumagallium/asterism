"""JSON Schema for the Mapping IR — the guided-decoding contract (Phase 2).

Derived in code from the same closed sets the parser/compiler use (reserved
prefixes, object forms, the Tier-0 catalog), so there is exactly one source of
truth. A guided-decoding server (vLLM's response_format json_schema) uses it to
make off-spec output UNREPRESENTABLE at generation time — the error families
observed live (unknown fields like ``optional:``, type-cast pseudo-functions
like ``function: str``, cardinality-marked predicates like ``schema:author*``)
cannot even be emitted.

Deliberately grammar-friendly: no ``oneOf`` (uneven support across guided
decoders); the object-form exclusivity rules stay with the strict parser
(:mod:`asterism_step0.mapping_ir`), which remains the gate for ALL providers —
the schema narrows generation, it never replaces validation.
"""
from __future__ import annotations

from collections.abc import Sequence

__all__ = ["mapping_ir_json_schema"]

# Term positions that must not carry an rdf-config cardinality suffix: any
# non-space chars, last char not one of * ? +  (single-char terms allowed).
_TERM_PATTERN = r"^\S*[^*?+\s]$"
_PREFIX_NAME_PATTERN = r"^[A-Za-z][\w.-]*$"
_MAP_NAME_PATTERN = r"^[A-Za-z][\w-]*$"
_IRI_PATTERN = r"^https?://\S+$"


def _string(pattern: str | None = None) -> dict:
    out: dict = {"type": "string", "minLength": 1}
    if pattern:
        out["pattern"] = pattern
    return out


def mapping_ir_json_schema(function_names: Sequence[str] | None = None) -> dict:
    """The Mapping IR as a JSON Schema object (draft 2020-12 compatible).

    ``function_names`` (the vetted Tier-0 menu, e.g. from
    ``FunctionCatalog.names()``) turns ``function:`` / ``transform:`` values
    into a closed enum — a non-menu function then cannot be GENERATED, not
    merely gets rejected later. Omit it (None) for a name-shaped string
    (schema stays registry-agnostic, validation still gates).
    """
    function_value: dict = (
        {"type": "string", "enum": sorted(function_names)}
        if function_names
        else _string(r"^[a-z][a-z0-9_]*$")
    )
    transform_obj = {
        "type": "object",
        "additionalProperties": function_value,
    }
    subject = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "template": _string(),
            "constant": _string(),
            "classes": {"type": "array", "items": _string(_TERM_PATTERN)},
            "transform": transform_obj,
        },
    }
    property_row = {
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
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "prefixes", "maps"],
        "properties": {
            "version": {"const": 1},
            "prefixes": {
                "type": "object",
                "propertyNames": {"pattern": _PREFIX_NAME_PATTERN},
                "additionalProperties": _string(_IRI_PATTERN),
            },
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
                        "subject": subject,
                        "properties": {
                            "type": "array",
                            "minItems": 1,
                            "items": property_row,
                        },
                    },
                },
            },
        },
    }
