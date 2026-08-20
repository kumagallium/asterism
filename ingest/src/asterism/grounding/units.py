"""Resolve a unit string a person typed to a REAL QUDT unit IRI (closed set).

A unit is not just another attribute: "300" alone is not a citable fact, and RDF's
type system cannot carry the unit (``xsd:double`` says nothing about kelvin). That is
why units get their own international vocabularies at all. So Asterism resolves them
against their own catalog — :mod:`qudt_units.yaml`, a MIRROR of the QUDT unit
vocabulary — rather than through the term grounding search, which answers a different
question ("which class/property should this column reuse?").

The lookup is deterministic and CLOSED: every hit is a real QUDT IRI, never fabricated.
A miss is reported AS a miss — the point is that a person can see whether the unit they
typed actually landed on a standard, instead of the QUDT triple silently not appearing.

CASE MATTERS for symbols and UCUM codes (``S`` siemens vs ``s`` second, ``T`` tesla vs
``t`` tonne), so those are matched verbatim. Labels and the dataset-level aliases are
matched case-insensitively — they are English words, not symbols.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

logger = logging.getLogger(__name__)

_CATALOG: Final[Path] = Path(__file__).with_name("qudt_units.yaml")
_SPELLINGS: Final[Path] = Path(__file__).with_name("unit_spellings.yaml")

# How a query matched, best first. A symbol/UCUM/name hit is the unit's own identity;
# a label hit is a human wording; an alias hit went through a spelling table first.
MATCH_ORDER: Final[tuple[str, ...]] = ("symbol", "ucum", "name", "label", "alias")


@dataclass(frozen=True)
class UnitMatch:
    """One resolved QUDT unit."""

    name: str
    iri: str
    label: str
    symbol: str | None
    ucum: tuple[str, ...]
    quantity_kinds: tuple[str, ...]
    matched_on: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "iri": self.iri,
            "curie": f"unit:{self.name}",
            "label": self.label,
            "symbol": self.symbol,
            "ucum": list(self.ucum),
            "quantity_kinds": list(self.quantity_kinds),
            "matched_on": self.matched_on,
        }


@dataclass(frozen=True)
class UnitResolution:
    """What a unit string resolved to.

    ``exact`` holds every unit whose own identity matches the query — normally one.
    Two or more means the string is genuinely ambiguous in QUDT and a person has to
    pick; zero means the unit is not in the standard, and ``suggestions`` offers the
    near misses so the person can correct the spelling rather than guess.
    """

    query: str
    exact: tuple[UnitMatch, ...]
    suggestions: tuple[UnitMatch, ...]
    #: A symbol several units share was settled by SI membership (``K`` → kelvin).
    si_settled: bool = False

    @property
    def resolved(self) -> UnitMatch | None:
        """The single unit this string means, or None when it is ambiguous/unknown.
        ``exact`` is SI-ordered, so a symbol several units share resolves to the SI
        one when exactly one of them is SI."""
        if len(self.exact) == 1:
            return self.exact[0]
        return self.exact[0] if self.exact and self.si_settled else None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "status": (
                "resolved" if self.resolved else "ambiguous" if self.exact else "unknown"
            ),
            "si_settled": self.si_settled,
            "exact": [m.to_dict() for m in self.exact],
            "suggestions": [m.to_dict() for m in self.suggestions],
        }


@functools.lru_cache(maxsize=1)
def _catalog() -> dict:
    """The mirrored QUDT unit catalog. A missing file disables unit resolution rather
    than erroring (same wheel-only-install tolerance as the QUDT map)."""
    if not _CATALOG.is_file():
        logger.warning("qudt_units.yaml not found at %s; unit resolution disabled", _CATALOG)
        return {"namespace": "http://qudt.org/vocab/unit/", "units": {}}
    return yaml.safe_load(_CATALOG.read_text(encoding="utf-8")) or {}


def catalog_meta() -> dict:
    """Provenance of the mirrored catalog (source / version / retrieved / license)."""
    c = _catalog()
    return {k: c.get(k) for k in ("source", "version", "retrieved", "license")}


@functools.lru_cache(maxsize=1)
def _spellings() -> tuple[dict[str, str], dict[str, str]]:
    """How real files WRITE a unit → the QUDT local name. Returned twice: verbatim
    (case matters for symbols) and case-folded (a spreadsheet shouting "OHM*M" still
    means ohm metre). A missing file just means no spelling help."""
    if not _SPELLINGS.is_file():
        return {}, {}
    data = yaml.safe_load(_SPELLINGS.read_text(encoding="utf-8")) or {}
    exact = {str(k): str(v) for k, v in (data.get("spellings") or {}).items()}
    folded: dict[str, str] = {}
    for k, v in exact.items():
        folded.setdefault(k.strip().lower(), v)
    return exact, folded


def _prefer_si(matches: list[UnitMatch]) -> list[UnitMatch]:
    """Put SI first when one symbol is claimed by several units. "K" is kelvin AND
    kayser, "S" is siemens AND solar mass — every reader of a data table means the SI
    one, so an SI hit settles it instead of handing the person a coin flip. Ambiguity
    that SI does NOT settle (5 units call themselves "L") stays ambiguous."""
    units = _catalog().get("units") or {}
    si = [m for m in matches if (units.get(m.name) or {}).get("si")]
    return si + [m for m in matches if m not in si] if len(si) == 1 else matches


def _match(name: str, entry: dict, matched_on: str) -> UnitMatch:
    return UnitMatch(
        name=name,
        iri=_catalog().get("namespace", "http://qudt.org/vocab/unit/") + name,
        label=str(entry.get("label") or name),
        symbol=entry.get("symbol"),
        ucum=tuple(entry.get("ucum") or ()),
        quantity_kinds=tuple(entry.get("qk") or ()),
        matched_on=matched_on,
    )


@functools.lru_cache(maxsize=1)
def _index() -> dict[str, dict[str, list[str]]]:
    """Query string → unit names, per match kind. Built once; the catalog is static."""
    idx: dict[str, dict[str, list[str]]] = {k: {} for k in MATCH_ORDER if k != "alias"}
    for name, entry in (_catalog().get("units") or {}).items():
        entry = entry or {}
        idx["name"].setdefault(name, []).append(name)
        if sym := entry.get("symbol"):
            idx["symbol"].setdefault(str(sym), []).append(name)
        for code in entry.get("ucum") or ():
            idx["ucum"].setdefault(str(code), []).append(name)
        if label := entry.get("label"):
            idx["label"].setdefault(str(label).strip().lower(), []).append(name)
    return idx


def _suggest(query: str, limit: int) -> list[UnitMatch]:
    """Near misses for an unresolved query: units whose label or symbol contains it (or
    the other way round). Substring only — no fuzzy scoring, so the list is explainable
    and never surprises with a distant unit."""
    q = query.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, str, dict]] = []
    for name, entry in (_catalog().get("units") or {}).items():
        entry = entry or {}
        label = str(entry.get("label") or "").lower()
        symbol = str(entry.get("symbol") or "").lower()
        # Rank by how close the hit is to being the whole string.
        if symbol and q == symbol:
            rank = 0
        elif label.startswith(q) or symbol.startswith(q):
            rank = 1
        elif q in label or q in symbol:
            rank = 2
        elif label and label in q:
            rank = 3
        else:
            continue
        scored.append((rank, name, entry))
    scored.sort(key=lambda t: (t[0], len(t[1]), t[1]))
    return [_match(n, e, "label") for _, n, e in scored[:limit]]


def resolve_unit(query: str | None, *, limit: int = 6) -> UnitResolution:
    """Resolve a unit string to QUDT units, best identity match first."""
    q = (query or "").strip()
    if not q:
        return UnitResolution(query="", exact=(), suggestions=())

    units = _catalog().get("units") or {}

    # A curated spelling is the most specific thing we know about this string — it was
    # written down BECAUSE the standard does not carry it — so it wins outright.
    spell_exact, spell_folded = _spellings()
    alias = spell_exact.get(q) or spell_folded.get(q.lower())
    if alias and alias in units:
        return UnitResolution(
            query=q, exact=(_match(alias, units[alias], "alias"),), suggestions=()
        )

    idx = _index()
    seen: set[str] = set()
    exact: list[UnitMatch] = []
    for kind in ("symbol", "ucum", "name", "label"):
        key = q.strip().lower() if kind == "label" else q
        for name in idx.get(kind, {}).get(key, ()):
            if name in seen:
                continue
            seen.add(name)
            exact.append(_match(name, units.get(name) or {}, kind))
        if exact:
            # Stop at the strongest kind that hit: a symbol match must not be diluted
            # by a label that happens to read the same.
            break

    ordered = _prefer_si(exact) if len(exact) > 1 else exact
    settled = len(exact) > 1 and ordered is not exact
    suggestions = () if ordered else tuple(_suggest(q, limit))
    return UnitResolution(
        query=q, exact=tuple(ordered), suggestions=suggestions, si_settled=settled
    )


def unit_iri(query: str | None) -> str | None:
    """The QUDT unit IRI for an unambiguous match, else None."""
    m = resolve_unit(query).resolved
    return m.iri if m else None
