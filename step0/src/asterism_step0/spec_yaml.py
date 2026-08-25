"""The ONE YAML loader/dumper for mapping-spec documents (§9 Mapping IR).

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

Every WRITER goes through :func:`dump_spec_yaml` for the matching reason, even
though ``yaml.safe_dump`` already quotes 1.1-ambiguous scalars on its own
(``SafeDumper``'s resolver knows the same ``no``/``yes``/``on``/``off`` table
this file's loader narrows away) — belt-and-suspenders so this file is the ONE
place that documents, and a round-trip test pins, that the Norway problem
cannot creep back in from either side (real-user incident 2026-08-25: a
display-meta edit spliced through a stray ``yaml.safe_load`` on §9 text, not
a dump — the loader was the actual hole, but a future stray ``safe_dump`` call
should not become the next one).
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


# The YAML 1.1 bare-scalar spellings a Norway-problem-aware dumper must force
# into quotes — mirrors ``_YAML12_BOOL``'s complement: this is the FULL 1.1
# table (unlike the loader's narrowed 1.2 one), because the danger here is a
# stray *reader* elsewhere in the stack (or a future one) that has not been
# routed through :func:`load_spec_yaml` yet — the dump must be safe to reload
# even with the vanilla 1.1 loader, not just this file's own.
_YAML11_BOOL_WORDS = re.compile(
    r"^(?:y|Y|yes|Yes|YES|n|N|no|No|NO|"
    r"true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$"
)


def _dumper_class() -> type:
    """Build (once) the SafeDumper subclass that force-quotes any string
    scalar (key OR value) spelled like a YAML 1.1 boolean. Lazy for the same
    reason as :func:`_loader_class`."""
    import yaml

    cached = getattr(_dumper_class, "_cls", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    class SpecDumper(yaml.SafeDumper):
        pass

    def _represent_str(dumper: yaml.SafeDumper, data: str) -> Any:
        style = "'" if _YAML11_BOOL_WORDS.match(data) else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)

    SpecDumper.add_representer(str, _represent_str)
    _dumper_class._cls = SpecDumper  # type: ignore[attr-defined]
    return SpecDumper


def dump_spec_yaml(data: Any, **kwargs: Any) -> str:
    """``yaml.safe_dump`` for a mapping spec, with an EXPLICIT (not merely
    inherited-from-SafeDumper) guarantee that a string spelled like a YAML 1.1
    boolean — a key (``No: slug``) or a value (``label: 'No'``) — always comes
    out quoted, so a round trip through even a plain ``yaml.safe_load``
    elsewhere in the stack cannot turn it back into ``False``. Accepts the
    same keyword arguments as ``yaml.safe_dump`` (``sort_keys``,
    ``allow_unicode``, ``default_flow_style``, ...)."""
    import yaml

    return yaml.dump(data, Dumper=_dumper_class(), **kwargs)


# YAML 1.1 boolean spellings. The loader above deliberately does NOT resolve
# these (a column named ``No`` must stay a string), so the few fields that are
# genuinely booleans accept them here instead — see :func:`coerce_bool`.
_TRUE_WORDS = frozenset({"true", "yes", "on", "y"})
_FALSE_WORDS = frozenset({"false", "no", "off", "n"})


def coerce_bool(value: Any) -> bool | None:
    """A boolean-typed IR field's value, or None when it is not boolean at all.

    Accepts the YAML 1.1 spellings (``yes/no/on/off/y/n``, any case) that
    :func:`load_spec_yaml` intentionally leaves as strings. Without this, moving
    to YAML 1.2 would turn ``collapse: no`` — unambiguous in the author's intent
    and silently accepted before — into a fresh design issue, costing an LLM
    round to fix punctuation. Column names stay strings; only fields declared
    boolean go through here.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


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
