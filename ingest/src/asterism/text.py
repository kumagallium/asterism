"""Domain-neutral text / parsing / IRI helpers.

Extracted from :mod:`asterism.starrydata` (#20 P2). These functions carry **no
starrydata semantics** — they are generic string→value utilities (slugging, JSON
array/date parsing, IRI sanitization) that the schema-agnostic core depends on.
Keeping them under a neutral name lets the Tier 0 function library
(:mod:`asterism.functions`) and any future non-starrydata dataset use them
*without importing a domain module*. ``asterism.starrydata`` re-exports them, so
existing ``from asterism.starrydata import slugify`` call sites keep working.

Per ADR ``ontology-canonical-lifecycle.md`` §4: starrydata is being demoted from
"core default" to one example dataset; this split is the first step (generic core
no longer lives under the ``starrydata`` name).
"""

from __future__ import annotations

import json
import re
from datetime import date

# ----------------------------------------------------------------------------
# Slug
# ----------------------------------------------------------------------------

_SLUG_DROP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_len: int = 80) -> str:
    """IRI segment 用の slug。a-z0-9 + 1 個の ``-`` のみに正規化。

    引用符付き文字列 (例: starrydata の container_title) は、まず json.loads を
    試みて剥がしてから slug 化する。
    """
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            s = json.loads(s)
        except json.JSONDecodeError:
            s = s.strip('"')
    s = s.lower()
    s = _SLUG_DROP.sub("-", s).strip("-")
    if not s:
        return "unknown"
    return s[:max_len]


# ----------------------------------------------------------------------------
# JSON value parsing
# ----------------------------------------------------------------------------


def parse_issued(raw: str) -> str | None:
    """CSL-JSON ``{"date_parts": [[YYYY, MM?, DD?]]}`` -> ISO 8601 date (best effort)."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        parts = data.get("date_parts", [[]])[0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        return None
    if not parts:
        return None
    y = parts[0] if len(parts) >= 1 else None
    m = parts[1] if len(parts) >= 2 else 1
    d = parts[2] if len(parts) >= 3 else 1
    if not isinstance(y, int):
        return None
    try:
        return date(y, m or 1, d or 1).isoformat()
    except ValueError:
        return None


def parse_float_array(raw: str) -> list[float]:
    """JSON 数値配列を ``list[float]`` にパース。

    失敗した個別要素は除外し (NaN / None / 非数値文字列など)、配列自体が壊れていれば
    空リストを返す。
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[float] = []
    for v in data:
        try:
            if v is None:
                continue
            fv = float(v)
            if fv != fv:  # NaN check
                continue
            out.append(fv)
        except (TypeError, ValueError):
            continue
    return out


def strip_quoted(value: str) -> str:
    """JSON-quoted 文字列なら剥がす。それ以外はトリムして返す。"""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v.strip('"')
    return v


# ----------------------------------------------------------------------------
# IRI sanitization
# ----------------------------------------------------------------------------

# Characters that are illegal in an IRI and would make rdflib emit Turtle that
# Oxigraph rejects (e.g. legacy Wiley DOI URLs with angle brackets). We
# percent-encode them so the URL stays a valid, dereferenceable IRI rather than
# dropping the triple or breaking serialization.
_IRI_ILLEGAL = {
    " ": "%20", '"': "%22", "<": "%3C", ">": "%3E", "{": "%7B", "}": "%7D",
    "|": "%7C", "\\": "%5C", "^": "%5E", "`": "%60",
}
_IRI_ILLEGAL_RE = re.compile("[" + re.escape("".join(_IRI_ILLEGAL)) + "]")

# A usable IRI must be absolute, i.e. start with an RFC 3986 scheme
# (``ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":"``). Scheme-less placeholders
# like "unknown" would otherwise become invalid Turtle that Oxigraph's bulk
# loader rejects, taking the whole file down with it.
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def safe_url(value: str) -> str | None:
    """Return an IRI-safe URL (illegal chars percent-encoded), or None.

    Returns None for empty values and for scheme-less placeholders (e.g.
    "unknown") that are not absolute IRIs — we skip the triple rather than emit
    an invalid IRI. Otherwise only the characters that are illegal in an IRI are
    encoded; the URL's structure (scheme, slashes, already-encoded sequences) is
    left intact.
    """
    v = strip_quoted(value)
    if not v or not _URI_SCHEME_RE.match(v):
        return None
    return _IRI_ILLEGAL_RE.sub(lambda m: _IRI_ILLEGAL[m.group()], v)
