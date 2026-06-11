"""Core Tier 0 transform helpers — the high-frequency "head" of cell cleaning.

These are the format-level primitives that show up across almost every dataset
(numbers with separators / currency / percent / ranges, messy dates and epochs,
string hygiene, booleans, DOI / URL normalization, value+unit splitting). Keeping
them small and bounded is the whole governance idea: the *count* tracks formats,
not datasets, so it grows slowly (phase5 §5.1). The long tail still goes to the
parameterized primitives (lookup / regex_extract / template) or the raw-string
fallback — these only cover the head.

This module is the single source of truth for the logic; the Tier 0 function
library (:mod:`asterism.functions`) binds these to FnO. Like the rest of the
library every entry point is ``str -> str`` and returns ``""`` for "no result"
(the empty objectMap is dropped downstream). No code is generated or executed —
these are vetted, closed-set transforms.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_CURRENCY = "$€£¥₩₹฿₫₴₪₦"
# A bare numeric literal (optionally signed, decimal, scientific) — used to lift a
# leading number out of a value+unit cell and to validate cleaned numbers.
_LEADING_NUMBER = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")
# Thousands separators removed by number_clean: comma, ASCII space, NBSP,
# underscore, apostrophe. Spelled with an escape for the NBSP so the source has no
# confusable whitespace.
_THOUSANDS = (",", " ", "\u00a0", "_", "'")


def number_clean(value: str) -> str:
    """Strip thousands separators / currency / accounting parentheses from a number.

    ``"$1,234.50"`` -> ``"1234.50"``; ``"(1,234)"`` -> ``"-1234"``; ``"1.2e3"`` ->
    ``"1.2e3"``. The original precision is preserved (we do not round-trip through
    ``float``); we only validate that the result parses as a number, returning
    ``""`` otherwise. Percent and unit suffixes are *not* stripped here (use
    :func:`percent_to_ratio` / :func:`value_of`).
    """
    s = value.strip()
    if not s:
        return ""
    negative = False
    if s.startswith("(") and s.endswith(")"):  # accounting negative
        negative = True
        s = s[1:-1].strip()
    for ch in _CURRENCY:
        s = s.replace(ch, "")
    for sep in _THOUSANDS:
        s = s.replace(sep, "")
    s = s.strip()
    if s[:1] == "+":
        s = s[1:]
    elif s[:1] == "-":
        negative = True
        s = s[1:]
    if not s:
        return ""
    try:
        float(s)
    except ValueError:
        return ""
    return ("-" + s) if negative else s


def percent_to_ratio(value: str) -> str:
    """``"12%"`` -> ``"0.12"``. Requires an explicit ``%`` (avoids guessing scale);
    the numeric part is cleaned with :func:`number_clean`. ``""`` on no ``%`` /
    non-numeric."""
    s = value.strip()
    if not s.endswith("%"):
        return ""
    cleaned = number_clean(s[:-1])
    if not cleaned:
        return ""
    return repr(float(cleaned) / 100.0)


# Two numbers joined by a range separator: figure / en / em dash, minus sign,
# tilde, "..", "to". The single-char separators are written as escapes (figure
# dash, en dash, em dash, minus sign) so the source carries no confusable Unicode.
# Longer separators come first so "10-20" still splits on the hyphen.
_RANGE = re.compile(
    "^\\s*([+-]?\\d+(?:\\.\\d+)?)\\s*"
    "(?:\\.\\.|to|[\u2012\u2013\u2014\u2212~-])"
    "\\s*([+-]?\\d+(?:\\.\\d+)?)\\s*$",
    re.IGNORECASE,
)


def _range_pair(value: str) -> tuple[str, str] | None:
    m = _RANGE.match(value or "")
    return (m.group(1), m.group(2)) if m else None


def range_min(value: str) -> str:
    """Low end of a numeric range (``"10-20"`` -> ``"10"``). ``""`` if not a range."""
    pair = _range_pair(value)
    return pair[0] if pair else ""


def range_max(value: str) -> str:
    """High end of a numeric range (``"10-20"`` -> ``"20"``). ``""`` if not a range."""
    pair = _range_pair(value)
    return pair[1] if pair else ""


# ---------------------------------------------------------------------------
# Date / time
# ---------------------------------------------------------------------------

_EPOCH = re.compile(r"^-?\d{10,13}$")
# Common non-ISO datetime spellings, tried in order. Note %m/%d/%Y (US) precedes
# %d/%m/%Y — an unavoidable ambiguity for slash dates; documented best-effort.
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)


def datetime_iso(value: str) -> str:
    """Messy datetime OR epoch -> ISO 8601 datetime. ``""`` if unparseable.

    A 13-digit integer is read as epoch milliseconds, a 10-digit one as epoch
    seconds (emitted as UTC, ``...Z``). Otherwise ISO 8601 (``Z`` accepted) is
    tried, then a handful of common spellings. Naive inputs stay naive (no tz
    invented); this complements :func:`date_iso`, which targets ``xsd:date``.
    """
    s = value.strip()
    if not s:
        return ""
    if _EPOCH.match(s):
        n: float = int(s)
        if len(s.lstrip("-")) >= 13:  # milliseconds
            n = n / 1000.0
        try:
            dt = datetime.fromtimestamp(n, tz=UTC)
        except (ValueError, OSError, OverflowError):
            return ""
        return dt.isoformat().replace("+00:00", "Z")
    iso = s.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        return datetime.fromisoformat(iso).isoformat()
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return ""


_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def year_only(value: str) -> str:
    """Extract a 4-digit calendar year (``"March 5, 2014"`` -> ``"2014"``).

    Returns ``""`` when no plausible year (1000-2999) is present. A long all-digit
    run (e.g. an epoch timestamp) is rejected so its leading 4 digits are not
    mistaken for a year.
    """
    s = value.strip()
    if not s or re.fullmatch(r"\d{5,}", s):
        return ""
    m = _YEAR.search(s)
    if not m:
        return ""
    return m.group(1) if 1000 <= int(m.group(1)) <= 2999 else ""


# ---------------------------------------------------------------------------
# String hygiene
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
# Trailing footnote markers: bracketed numbers, asterisks, daggers, section sign,
# superscript digits. Parenthesized numbers are deliberately NOT stripped (they
# are usually years / counts, not footnotes).
_FOOTNOTE = re.compile(r"(?:\s*(?:\[\d+\]|[*†‡§]+|[¹²³⁰-⁹]+))+$")


def nfkc_norm(value: str) -> str:
    """Unicode NFKC normalization (fold full-width / compatibility forms). ``""`` for empty."""
    return unicodedata.normalize("NFKC", value) if value else ""


def trim_collapse(value: str) -> str:
    """Trim ends and collapse internal whitespace runs to a single space."""
    return _WS.sub(" ", value).strip() if value else ""


def strip_footnote(value: str) -> str:
    """Remove trailing footnote markers (``"Hello[1]"`` / ``"World*"`` / ``"x¹"`` -> base)."""
    return _FOOTNOTE.sub("", value).strip() if value else ""


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

_DOI = re.compile(r"(10\.\d{3,9}/\S+)", re.IGNORECASE)


def doi_norm(value: str) -> str:
    """Normalize a DOI to its bare lowercase form (``"https://doi.org/10.1/X"`` ->
    ``"10.1/x"``). Strips URL / ``doi:`` prefixes and trailing punctuation. ``""``
    if no DOI is present."""
    s = value.strip()
    if not s:
        return ""
    m = _DOI.search(s)
    if not m:
        return ""
    return m.group(1).rstrip(".,;)").lower()


def url_canonical(value: str) -> str:
    """Canonicalize a URL: lowercase scheme + host, drop default port and fragment,
    trim a trailing slash. ``""`` if not an absolute URL (no scheme / host)."""
    s = value.strip()
    if not s:
        return ""
    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return ""
    netloc = host
    if parts.port is not None and parts.port not in (
        80 if scheme == "http" else None,
        443 if scheme == "https" else None,
    ):
        netloc = f"{host}:{parts.port}"
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


# ---------------------------------------------------------------------------
# Value + unit
# ---------------------------------------------------------------------------


def value_of(value: str) -> str:
    """Leading numeric part of a value+unit cell (``"300 K"`` -> ``"300"``,
    ``"-3.2e-4 V"`` -> ``"-3.2e-4"``). ``""`` if it does not start with a number."""
    m = _LEADING_NUMBER.match(value or "")
    return m.group(1) if m else ""


def unit_of(value: str) -> str:
    """Trailing unit part of a value+unit cell (``"300 K"`` -> ``"K"``,
    ``"12.5 mm/s"`` -> ``"mm/s"``). ``""`` if there is no trailing unit."""
    m = _LEADING_NUMBER.match(value or "")
    if not m:
        return ""
    return value[m.end() :].strip()


# ---------------------------------------------------------------------------
# JSON single-element unwrap
# ---------------------------------------------------------------------------


def json_array_single(value: str) -> str:
    """Unwrap a JSON array that holds **exactly one** element (``'["X"]'`` ->
    ``"X"``).

    Returns ``""`` for a non-array, an empty array, or an array with more than one
    element. The strict single-element rule means this never silently drops data:
    a genuinely multi-valued cell (several authors / tags) is left untouched, to be
    handled by ``split`` (explode) or a nested TriplesMap, not collapsed to its
    first element.
    """
    if not value:
        return ""
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return ""
    if isinstance(data, list) and len(data) == 1 and data[0] is not None:
        return str(data[0])
    return ""


def json_array(value: str) -> list[str] | None:
    """A JSON-string array of scalars → its elements as a **list**
    (``'["a", "b", "c"]'`` -> ``["a", "b", "c"]``).

    Like ``split``, this returns a list that Morph-KGC EXPLODES into one triple per
    element — the declarative multi-value path for a cell that already holds a JSON
    array of scalars (common in CSV exports, e.g. starrydata's ``project_names``).
    Null and nested (object / array) elements are dropped. A non-array / non-JSON /
    empty input, or an array with no scalar elements, returns ``None`` (dropped
    pre-explode — an empty list would NaN-crash Morph-KGC serialization). For an
    array of OBJECTS use :func:`asterism.primitives.json_pluck`; a one-element
    wrapper uses :func:`json_array_single`.
    """
    if not value:
        return None
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out = [str(el) for el in data if el is not None and not isinstance(el, list | dict)]
    return out or None
