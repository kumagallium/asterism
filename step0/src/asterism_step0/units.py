"""Deterministic display-unit extraction from column names (task #10).

Instrument exports name a column with its unit in trailing parentheses —
``Resistivity(Ohm m)``, ``Seebeck coeff.(V/K)``, ``Measurement temp.(C)``.
The Mapping IR carries an optional per-property ``unit`` (display metadata only;
kantan-mode ADR K8), but weak models routinely leave it blank even when the
unit is sitting right there in the header. This module fills that gap WITHOUT a
model call and WITHOUT a materials-specific unit dictionary:

* :func:`extract_unit_from_label` — pure, stdlib-only. Given a column name it
  returns the trailing-parenthesis content **iff** that content reads like a
  unit notation rather than a physical-quantity description or prose. It is
  deliberately conservative (returns ``None`` when in doubt) so it never invents
  a wrong unit — over-completion is worse than a blank the reviewer can fill.
* :func:`enrich_units` — overlays the extracted units onto a Mapping IR YAML
  document, filling only single-column properties whose ``unit`` is still empty
  (an AI/human-authored unit always wins). Byte-identical when nothing is added.

IMPORTANT — this is **display metadata only**. It never touches emitted values
and is never compiled into RML; value/unit conversion stays in the vetted Tier-0
functions (``qudt_unit`` / ``value_of`` / ``unit_of``). The extracted string is a
human-readable notation for the review screen, not a normalized/typed quantity.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping

__all__ = ["enrich_units", "extract_unit_from_label"]

# The longest string we accept as a unit. A trailing parenthetical longer than
# this is almost always a descriptive note ("(measured at room temperature)"),
# not a unit — the single strongest signal separating the two.
_MAX_UNIT_LEN = 12

# Trailing ``Name(...)`` / ``Name (...)`` and the full-width variant (U+FF08 /
# U+FF09). The content group forbids nested parens, so this captures the
# innermost trailing group; a non-empty ``prefix`` (the column's actual name) is
# required so a bare "(V)" — which has no quantity to attach a unit to — is
# rejected. Full-width parens use \u escapes so the source stays ASCII.
_TRAILING_PAREN = re.compile(
    r"^(?P<prefix>.*?)[(\uFF08](?P<content>[^()\uFF08\uFF09]*)[)\uFF09]\s*$",
    re.DOTALL,
)

# A pure number (int/float) is a value or a count, never a unit.
_NUMERIC_ONLY = re.compile(r"-?\d+(?:\.\d+)?")

# Non-alphanumeric characters a unit notation may legitimately contain. ASCII
# letters/digits and the space are allowed separately (see :func:`_is_unit_char`);
# anything outside this union — notably CJK / prose punctuation — disqualifies the
# candidate. Kept general (SI/engineering notation), NOT a materials-unit list.
_UNIT_SYMBOLS = frozenset(
    "/^*.,-+[]%"          # ASCII operators / brackets / percent
    "°"              # ° degree sign
    "‰"              # ‰ per mille
    "·・"        # · (middle dot) and ・ (katakana middle dot)
    "µμ"        # µ micro sign, μ Greek small mu
    "ΩΩ"        # Ω Greek capital omega, Ω ohm sign
    "ÅÅ"        # Å latin A-ring, Å angstrom sign
    "⁰¹²³⁴⁵⁶⁷⁸⁹"  # superscripts ⁰¹²³⁴⁵⁶⁷⁸⁹
    "₀₁₂₃₄₅₆₇₈₉"  # subscripts ₀₁₂₃₄₅₆₇₈₉
)

# Symbol-only unit tokens that carry no alphabetic character but ARE units.
_SYMBOL_UNITS = frozenset("%°‰")  # % ° ‰


def _is_unit_char(ch: str) -> bool:
    """A character permissible inside a unit notation: an ASCII letter/digit, a
    space, or one of the vetted unit symbols. Everything else (CJK, most prose
    punctuation) fails, which is how a Japanese/verbose parenthetical is rejected."""
    if ch == " ":
        return True
    if ch.isascii() and ch.isalnum():
        return True
    return ch in _UNIT_SYMBOLS


def extract_unit_from_label(column_name: str | None) -> str | None:
    """Return the unit hidden in a column name's trailing parentheses, or ``None``.

    Deterministic and conservative — display metadata only (see the module
    docstring). The candidate is the content of the last ``(...)`` group (ASCII
    or full-width parentheses); it is accepted only when it reads like a unit
    and not a description:

    * short — at most :data:`_MAX_UNIT_LEN` characters;
    * made only of ASCII letters/digits, spaces, and vetted unit symbols
      (``/ ^ · % ° µ Ω Å`` … — no CJK, no arbitrary prose punctuation);
    * not a bare number (a value/count, not a unit), and not a value+condition
      like ``300 K`` (a leading standalone numeric token);
    * carries a real unit signal — at least one letter, or a symbol unit
      (``% ° ‰``); pure punctuation such as ``-`` or ``/`` is rejected;
    * when it is plain words (no symbol/digit), it must not look like a phrase —
      at most two whitespace tokens, and a two-word candidate must contain a
      short (≤2 char) token, so ``Ohm m`` / ``V K`` pass while ``room temp`` /
      ``per sample`` do not.

    Examples: ``Resistivity(Ohm m)`` → ``"Ohm m"``; ``Seebeck coeff.(V/K)`` →
    ``"V/K"``; ``Power factor(W/m K^2)`` → ``"W/m K^2"``; ``Measurement temp.(C)``
    → ``"C"``; ``Figure of merit(1/K)`` → ``"1/K"``; ``sample_id`` → ``None``.
    """
    if not column_name:
        return None
    m = _TRAILING_PAREN.match(column_name)
    if m is None:
        return None
    if not m.group("prefix").strip():
        return None  # no quantity name in front of the parentheses
    content = m.group("content").strip()
    if not content or len(content) > _MAX_UNIT_LEN:
        return None
    if any(not _is_unit_char(ch) for ch in content):
        return None
    if _NUMERIC_ONLY.fullmatch(content):
        return None  # a bare number is a value/count, never a unit

    tokens = content.split()
    if len(tokens) > 1 and _NUMERIC_ONLY.fullmatch(tokens[0]):
        return None  # "300 K" / "1.5 T" — a measured value + condition, not a unit

    has_letter = any(ch.isalpha() for ch in content)
    if not has_letter and not any(ch in _SYMBOL_UNITS for ch in content):
        return None  # pure punctuation ("-", "/", "...") — no unit signal

    has_symbol = any(not (ch.isalpha() or ch == " ") for ch in content)
    if not has_symbol:
        # Plain words only: guard against descriptive phrases slipping through.
        if len(tokens) > 2:
            return None
        if len(tokens) == 2 and min(len(t) for t in tokens) > 2:
            return None
    return content


def enrich_units(mapping_ir_yaml: str) -> str:
    """Fill each property's display ``unit`` from a bracketed column name.

    Deterministic post-processing of a Mapping IR YAML document (the same shape
    :func:`asterism_step0.materialize.apply_source_dialects` overlays dialects
    onto): for every property that references a single ``column`` and has no
    ``unit`` yet, set ``unit`` to :func:`extract_unit_from_label` of that column
    name when it yields one. An AI- or human-authored ``unit`` is never
    overwritten, and ``columns`` (multi-input), ``object_template`` and
    ``constant`` rows are skipped (no single source column to read a unit from).

    Units are display metadata and never compile into RML, so this cannot change
    the compiled mapping — it only enriches the spec the review screen reads.
    Returns the input text unchanged (byte-identical) when nothing is added or
    when the YAML cannot be parsed (a broken spec flows on; the compiler reports
    it). Lazy-imports PyYAML like every other IR overlay in the package.
    """
    import yaml

    try:
        doc = yaml.safe_load(mapping_ir_yaml)
    except yaml.YAMLError:
        return mapping_ir_yaml
    if not isinstance(doc, Mapping) or not isinstance(doc.get("maps"), list):
        return mapping_ir_yaml

    changed = False
    for map_obj in doc["maps"]:
        if not isinstance(map_obj, Mapping):
            continue
        props = map_obj.get("properties")
        if not isinstance(props, list):
            continue
        for prop in props:
            if not isinstance(prop, MutableMapping):
                continue
            existing = prop.get("unit")
            if isinstance(existing, str) and existing.strip():
                continue  # an authored unit wins
            column = prop.get("column")
            if not isinstance(column, str):
                continue
            if (
                prop.get("columns")
                or prop.get("object_template") is not None
                or prop.get("constant") is not None
            ):
                continue  # not a single-column literal
            unit = extract_unit_from_label(column)
            if unit:
                prop["unit"] = unit
                changed = True

    if not changed:
        return mapping_ir_yaml
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
