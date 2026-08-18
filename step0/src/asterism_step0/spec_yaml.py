"""The ONE YAML loader for mapping-spec documents (§9 Mapping IR).

Why not plain ``yaml.safe_load``: PyYAML implements YAML **1.1**, where the bare
scalars ``yes / no / on / off`` (any case) and ``y / n`` are BOOLEANS. A mapping
spec is full of source column headers, and instrument exports name columns
``No`` / ``No.`` as a matter of course. Written unquoted — which is exactly how a
model rewrites YAML in a whole-document refine — ``column: No`` and
``transform: {No: No}`` arrive as ``False`` / ``{False: False}``.

Observed live (2026-08-18, XRD reference card, gpt-oss-120b): the validator then
said ``transform['False'] must be a non-empty string (got False)``. The model had
never written the token ``False`` and could not connect the message to its own
text; three consecutive "AI に直してもらう" clicks changed nothing.

YAML **1.2** (core schema) resolves only ``true / false`` (any case) as booleans —
``No`` is a string. This loader is ``SafeLoader`` with exactly that one change:
the boolean implicit resolver is narrowed to the 1.2 set. Nothing else moves —
ints, floats, ``null``/``~``, and the real booleans the IR uses (``collapse:
false``) resolve as before. ``yaml.safe_dump`` already quotes ``'No'`` on the way
out (it knows the 1.1 ambiguity), so round-trips are stable.

Every reader of a mapping spec goes through :func:`load_spec_yaml`; a stray
``yaml.safe_load`` on IR text re-opens the trap in that one reader.
"""
from __future__ import annotations

import re
from typing import Any

_BOOL_TAG = "tag:yaml.org,2002:bool"
_YAML12_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


def _loader_class() -> type:
    """Build (once) the SafeLoader subclass with the narrowed bool resolver.
    Lazy so PyYAML stays an optional import at module load, like everywhere
    else in step0."""
    import yaml

    cached = getattr(_loader_class, "_cls", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    class SpecLoader(yaml.SafeLoader):
        pass

    # Own copy of the resolver table (never mutate SafeLoader's), minus the
    # 1.1 bool resolver — then re-add the 1.2 one.
    SpecLoader.yaml_implicit_resolvers = {
        first: [(tag, rx) for tag, rx in resolvers if tag != _BOOL_TAG]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    SpecLoader.add_implicit_resolver(_BOOL_TAG, _YAML12_BOOL, list("tTfF"))
    _loader_class._cls = SpecLoader  # type: ignore[attr-defined]
    return SpecLoader


def load_spec_yaml(text: str) -> Any:
    """``yaml.safe_load`` for a mapping spec — YAML 1.2 booleans, so a column
    named ``No`` / ``Yes`` / ``On`` / ``Off`` / ``Y`` / ``N`` stays a string.
    Raises ``yaml.YAMLError`` exactly like ``safe_load`` (callers keep their
    existing handling); ``ImportError`` if PyYAML is absent."""
    import yaml

    return yaml.load(text, Loader=_loader_class())


def describe_bare_scalar(value: Any) -> str:
    """How to tell a model (or a human) that a bare YAML scalar was read as a
    non-string — with the quoted form that fixes it. Empty for strings/None
    (nothing to explain)."""
    if isinstance(value, bool):
        shown = "true" if value else "false"
        kind = "a boolean"
    elif isinstance(value, int | float):
        shown, kind = str(value), "a number"
    else:
        return ""
    return (
        f" YAML read the bare scalar as {kind} — a column header or name that looks "
        f"like {shown!r} must be written in quotes, e.g. '{shown}'."
    )
