"""Engines for the parameterized Tier 0 primitives (lookup / regex_extract / template).

These three primitives absorb the *long tail* of dataset-specific cleaning that
would otherwise each need a bespoke function. The trick is that their variability
lives in **declarative data** — a lookup table under ``tables/``, a regex pattern,
a template string — never in executable code. So the closed-set / no-codegen
safety of the declarative substrate is preserved: an AI-authored mapping still
references only the vetted Tier 0 set, it just *parameterizes* one of these
primitives with data (phase5 §5.1, "可変性はデータへ逃がす").

This module is the single source of truth for the primitive logic; the Tier 0
function library (:mod:`asterism.functions`) only binds these to FnO. Like the rest
of the library every entry point is ``str -> str`` and returns ``""`` for "no
result" (the empty objectMap is dropped downstream).

Safety notes:

- ``lookup`` validates the table name and resolves only within the packaged
  ``tables/`` directory — a constant in human-approved RML, but path traversal is
  rejected as defense-in-depth.
- ``regex_extract`` uses **google-re2** (a linear-time matcher with no
  catastrophic backtracking) so a ReDoS-prone pattern cannot hang on adversarial
  per-row input. It deliberately does NOT fall back to the stdlib ``re`` engine
  (whose backtracking is ReDoS-prone); if re2 is unavailable it returns ``""``.
- ``template`` interpolates by a single-pass literal token substitution — never
  ``str.format``/``eval`` — so a template string cannot reach into object
  attributes or re-interpret field values.
"""
from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Seed lookup tables ship as package data so the primitives work in any install —
# including a wheel-only image where the datasets/ content tree is absent. Tier 0
# is domain-neutral and shared by every deployment (phase5 §5.2), unlike a single
# dataset's curated table (e.g. datasets/starrydata/qudt_map.yaml).
_TABLES_DIR = Path(__file__).with_name("tables")

# A table name indexes a file under tables/; restrict it to a bare identifier so it
# can never escape that directory (path traversal) before we touch the filesystem.
_TABLE_NAME = re.compile(r"^[a-z0-9_]+$")

# Cap regex input length. re2 is linear-time (no ReDoS), but bounding the input
# still caps absolute CPU/memory per cell against pathological data.
_MAX_REGEX_INPUT = 4096

# Template field tokens: ``{1}``..``{4}`` only. A constant template string, matched
# once; nothing else in the template is interpreted (so ``{1.__class__}``, ``{5}``,
# ``{x}`` are inert literals).
_TEMPLATE_TOKEN = re.compile(r"\{([1-4])\}")


# ---- lookup -----------------------------------------------------------------

@functools.cache
def load_table(name: str) -> dict[str, str]:
    """Load a seed lookup table by name, or an empty dict if it is unavailable.

    Keys are lowercased once at load time so :func:`lookup` is O(1) and
    case-insensitive without re-lowercasing the table per call. An unsafe name, a
    missing file, malformed YAML, or a non-mapping document all yield an empty
    dict (every lookup then returns ``""``) — the same best-effort contract as the
    QUDT table loader, so a packaging gap disables the table rather than erroring.
    """
    if not _TABLE_NAME.match(name):
        logger.warning("rejecting unsafe lookup table name: %r", name)
        return {}
    path = _TABLES_DIR / f"{name}.yaml"
    if not path.is_file():
        logger.warning("lookup table not found: %s", path)
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("failed to read lookup table %s", path, exc_info=True)
        return {}
    if not isinstance(data, dict):
        logger.warning("lookup table %s is not a mapping; ignoring", path)
        return {}
    return {str(k).strip().lower(): str(v) for k, v in data.items()}


def lookup(value: str, table: str) -> str:
    """Return ``table[value]`` (case-insensitive), or ``""`` if value / table /
    key is absent. ``table`` is a constant seed-table name (e.g. ``"bool"``,
    ``"country_iso3166"``, ``"unit_alias"``)."""
    if not value or not table:
        return ""
    return load_table(table).get(value.strip().lower(), "")


# ---- regex_extract ----------------------------------------------------------

@functools.lru_cache(maxsize=256)
def _compile(pattern: str) -> Any:  # re2 has no type stubs; returns a compiled re2 pattern | None
    """Compile ``pattern`` with google-re2 (linear-time, ReDoS-immune), cached.

    Returns ``None`` when re2 is unavailable or the pattern does not compile (e.g.
    an re-only construct like a backreference, which re2 rejects). The caller then
    returns ``""``. We never fall back to the stdlib ``re`` engine: its
    backtracking is ReDoS-prone, and degrading to ``""`` keeps the safety
    invariant intact (phase5 §5.1, "regex は ReDoS を一度ガード").
    """
    try:
        import re2  # type: ignore[import-untyped]  # google-re2
    except ImportError:
        logger.warning("google-re2 not installed; regex_extract disabled (returns '')")
        return None
    try:
        return re2.compile(pattern)
    except Exception:  # re2.Error on invalid / unsupported patterns
        logger.warning("regex_extract: pattern failed to compile: %r", pattern, exc_info=True)
        return None


def regex_extract(value: str, pattern: str) -> str:
    """Extract a substring of ``value`` using the constant ``pattern``.

    Returns, in order of preference: a named group ``(?P<v>…)`` if the pattern
    defines one, else capture group 1, else the whole match. ``""`` on no match,
    an empty / bad / re2-unsupported pattern, or input longer than the cap. The
    matcher is google-re2, so a ReDoS-prone pattern stays linear-time on hostile
    input.
    """
    if not value or not pattern:
        return ""
    if len(value) > _MAX_REGEX_INPUT:
        return ""
    rx = _compile(pattern)
    if rx is None:
        return ""
    m = rx.search(value)
    if m is None:
        return ""
    # Prefer an explicit named target, then group 1, then the whole match so a
    # capture-group-less pattern still yields the matched text. str() casts pin
    # the result type (re2 has no stubs, so its return is otherwise Any).
    named = m.groupdict()
    if named.get("v"):
        return str(named["v"])
    if m.groups():
        g1 = m.group(1)
        return str(g1) if g1 is not None else ""
    return str(m.group(0))


# ---- template ---------------------------------------------------------------

def template(
    template: str,
    field1: str = "",
    field2: str = "",
    field3: str = "",
    field4: str = "",
) -> str:
    """Interpolate up to four fields into a constant ``template`` by replacing the
    positional tokens ``{1}``..``{4}`` with ``field1``..``field4``.

    Safe by construction: a single-pass literal token substitution, never
    ``str.format`` / ``eval``. So a template like ``{1.__class__}`` is inert (it
    does not match ``{1}``), and a field value that itself contains ``{2}`` is not
    re-interpreted. Unset / missing fields substitute to ``""``; an empty template
    returns ``""``.
    """
    if not template:
        return ""
    fields = (field1 or "", field2 or "", field3 or "", field4 or "")
    return _TEMPLATE_TOKEN.sub(lambda m: fields[int(m.group(1)) - 1], template)


# ---- array_at (constant index into a JSON array) ----------------------------


def array_at(value: str, index: str) -> str:
    """Element at a constant 0-based ``index`` of a JSON array (``"[10,20,30]"``,
    index ``"1"`` -> ``"20"``). Negative indices count from the end (``"-1"`` ->
    last). ``""`` for a non-array, an out-of-range / non-integer index, or a null
    element. Use it to pull a fixed-position scalar out of a structured array (e.g.
    ``[lon, lat, depth]``)."""
    if not value or not index:
        return ""
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(data, list):
        return ""
    try:
        element = data[int(index)]
    except (ValueError, IndexError):
        return ""
    return "" if element is None else str(element)


# ---- split (constant delimiter → MULTIPLE values) ---------------------------


def split(value: str, delimiter: str) -> list[str] | None:
    """Split ``value`` on a constant ``delimiter`` into **multiple** values.

    Unlike every other entry point (which is ``str -> str``), this returns a
    ``list[str]``: Morph-KGC explodes a list result into one triple per element —
    the declarative multi-value path, no nested TriplesMap needed. Surrounding
    whitespace is trimmed and empty tokens are dropped, so a wrapped list like
    ``",ci,us,"`` yields ``["ci", "us"]``. When there is nothing to emit (empty
    input, no delimiter, all-blank tokens) it returns ``None`` — Morph-KGC drops a
    ``None`` row *before* exploding, whereas an empty list would explode to a NaN
    and break serialization. For an array-of-objects use a nested TriplesMap — split
    is for flat delimited scalars.
    """
    if not value or not delimiter:
        return None
    tokens = [token for token in (part.strip() for part in value.split(delimiter)) if token]
    return tokens or None


# ---- json_pluck (sub-field of each object in a JSON-string array → list) -----


def json_pluck(value: str, field: str) -> list[str] | None:
    """From a JSON-string array of OBJECTS, the named ``field`` of each element →
    a **list** (``'[{"family":"A"},{"family":"B"}]'``, field ``"family"`` ->
    ``["A", "B"]``).

    Like ``split`` this returns a list that Morph-KGC EXPLODES into one triple per
    element — so each author / keyword / tag becomes its own value linked to the
    parent row, without a nested TriplesMap. It operates on a cell that holds the
    array **as a JSON string** (a CSV / string-valued column, e.g. starrydata's
    ``author`` = ``[{given, family}, …]``); Morph-KGC does not pass a *native* JSON
    array into a function, so for a JSON-source nested array use a nested iterator
    instead. Objects missing the field (or with a null / non-scalar value) are
    skipped; a non-array / non-JSON / empty input, or no matching fields, returns
    ``None`` (dropped pre-explode — an empty list would NaN-crash serialization).
    """
    if not value or not field:
        return None
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out: list[str] = []
    for obj in data:
        if isinstance(obj, dict):
            element = obj.get(field)
            if element is not None and not isinstance(element, list | dict):
                out.append(str(element))
    return out or None
