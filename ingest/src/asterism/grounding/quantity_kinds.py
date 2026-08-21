"""Resolve "what is this column measuring" to a REAL QUDT QuantityKind (closed set).

`units.py` answers "in what" (`V/K` → `unit:V-PER-K`). This answers the other half:
that the numbers ARE a Seebeck coefficient. A materials dataset whose units reach the
standard but whose PROPERTIES do not is half-connected — the quantity is what other
people search on ("who else measured thermal conductivity?"), while the unit only says
how it was written down.

Like the unit catalog this is a MIRROR (`qudt_quantitykinds.yaml`), not a curation:
"what quantity is temperature" has one answer, so a missing entry is a silent hole
rather than a kindness. The term catalog (`known_vocabs.yaml`) keeps its hand-curated
invariant — a QuantityKind is neither a class nor a property, so it does not belong
there anyway.

THE UNIT IS THE STRONGEST HINT. A column named `S` means nothing on its own, but a
column in `V/K` can only be a handful of quantities, and QUDT records exactly which
(``qudt:applicableUnit``). So the caller passes the unit it already resolved and the
ranking uses it — which is how an unreadable abbreviation still lands on the right
quantity.
"""
from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

logger = logging.getLogger(__name__)

_CATALOG: Final[Path] = Path(__file__).with_name("qudt_quantitykinds.yaml")
_QK_BASE: Final[str] = "http://qudt.org/vocab/quantitykind/"


def _split(text: str) -> list[str]:
    """Lowercased word tokens from camelCase / snake / kebab / spaced text.

    Same tokenizer as the term catalog, so `seebeckCoefficient`, `seebeck_coefficient`
    and "Seebeck Coefficient" all reach the same words.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _norm(text: str) -> str:
    return "".join(_split(text))


@dataclass(frozen=True)
class QuantityKindCandidate:
    """One real QUDT QuantityKind, with why it matched."""

    name: str
    iri: str
    curie: str
    label: str
    gloss: str
    symbol: str | None
    units: tuple[str, ...]
    score: int
    match: str
    #: The caller's unit is one this quantity may be measured in.
    unit_fits: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "iri": self.iri,
            "curie": self.curie,
            "label": self.label,
            "gloss": self.gloss,
            "symbol": self.symbol,
            "units": list(self.units),
            "score": self.score,
            "match": self.match,
            "unit_fits": self.unit_fits,
        }


@functools.lru_cache(maxsize=1)
def _catalog() -> dict:
    """The mirrored QUDT quantity-kind catalog. A missing file disables resolution
    rather than erroring (same wheel-only-install tolerance as the unit catalog)."""
    if not _CATALOG.is_file():
        logger.warning(
            "qudt_quantitykinds.yaml not found at %s; quantity-kind grounding disabled",
            _CATALOG,
        )
        return {"namespace": _QK_BASE, "quantity_kinds": {}}
    return yaml.safe_load(_CATALOG.read_text(encoding="utf-8")) or {}


def catalog_meta() -> dict:
    """Provenance of the mirrored catalog (source / version / retrieved / license)."""
    c = _catalog()
    return {k: c.get(k) for k in ("source", "version", "retrieved", "license")}


@dataclass(frozen=True)
class _Indexed:
    name: str
    entry: dict
    name_norm: str
    label_norm: str
    tokens: frozenset[str]
    units: frozenset[str]


@functools.lru_cache(maxsize=1)
def _index() -> tuple[_Indexed, ...]:
    out: list[_Indexed] = []
    for name, entry in (_catalog().get("quantity_kinds") or {}).items():
        entry = entry or {}
        label = str(entry.get("label") or name)
        out.append(
            _Indexed(
                name=name,
                entry=entry,
                name_norm=_norm(name),
                label_norm=_norm(label),
                tokens=frozenset(_split(name)) | frozenset(_split(label)),
                units=frozenset(entry.get("units") or ()),
            )
        )
    return tuple(out)


#: Below this length a column name is an abbreviation (`S`, `rho`, `kappa`), and every
#: fuzzy tier turns to noise: "rho" is a substring of "wate*rho*rsepower", and the one
#: token of "S" matches the "s" that Henry's Law leaves behind an apostrophe. Only an
#: exact hit — or the unit, which is real evidence — counts for those.
_MIN_FUZZY_CHARS: Final[int] = 4


def _score(q_norm: str, q_tokens: frozenset[str], ix: _Indexed) -> tuple[int, str]:
    """Deterministic match score + tier name (0 = no match). Same ladder as the term
    catalog, so one dataset's candidates are ranked the same way everywhere."""
    if not q_norm:
        return 0, ""
    if q_norm in (ix.name_norm, ix.label_norm):
        return 100, "exact"
    if len(q_norm) < _MIN_FUZZY_CHARS:
        return 0, ""
    if q_tokens and q_tokens == ix.tokens:
        return 90, "exact_tokens"
    if q_tokens and q_tokens <= ix.tokens:
        return 70 + max(0, 10 - (len(ix.tokens) - len(q_tokens))), "tokens_subset"
    if q_norm in ix.name_norm or ix.name_norm in q_norm or q_norm in ix.label_norm:
        return 50, "substring"
    overlap = q_tokens & ix.tokens
    if overlap:
        return 20 + len(overlap), "overlap"
    return 0, ""


#: A name that IS the quantity's own name or label. Nothing ranks above it, and one of
#: them alone is the answer rather than the head of a list.
_EXACT_SCORE: Final[int] = 100
#: Below this a name match is noise (one shared word like "specific" or "electric").
_MIN_SCORE: Final[int] = 30
#: What a unit match alone is worth. Enough to surface a quantity whose NAME the column
#: never says (`S` in V/K), but below any real name match so it never outranks one.
_UNIT_ONLY_SCORE: Final[int] = 25
#: Added to a name match when the column's unit also fits — the two agreeing is the
#: strongest signal this catalog can give.
_UNIT_BONUS: Final[int] = 15


def resolve_quantity_kind(
    query: str | None,
    *,
    unit: str | None = None,
    limit: int = 8,
) -> list[QuantityKindCandidate]:
    """Candidate QUDT quantity kinds for a column, best first.

    ``query`` is the column's own name or label; ``unit`` is the QUDT unit LOCAL NAME
    already resolved for it (e.g. ``"V-PER-K"``), which both ranks name matches higher
    and, on its own, offers the quantities that unit can express. Closed-set: every
    candidate is a real QUDT IRI, never fabricated — a human still confirms.
    """
    q = (query or "").strip()
    u = (unit or "").strip()
    if not q and not u:
        return []
    q_norm = _norm(q)
    q_tokens = frozenset(_split(q))

    scored: list[tuple[int, str, bool, _Indexed]] = []
    for ix in _index():
        score, match = _score(q_norm, q_tokens, ix)
        fits = bool(u) and u in ix.units
        exact = score >= _EXACT_SCORE
        if score < _MIN_SCORE:
            # No usable name match. The unit alone can still answer, but only as a
            # suggestion — never dressed up as if the name had matched.
            if not fits:
                continue
            score, match = _UNIT_ONLY_SCORE, "unit"
        elif u and not fits and not exact:
            # The unit is real evidence about what this column CAN be. A near-neighbour
            # the column cannot possibly be measuring (thermal resistivity, for a column
            # in ohm·m) is noise once we know that. An exact NAME match survives anyway:
            # a name and a unit that disagree is worth seeing, not hiding.
            continue
        elif fits:
            score += _UNIT_BONUS
            match = f"{match}+unit"
        scored.append((score, match, fits, ix))

    # One unambiguous answer is an answer. Showing "Thermal Conductivity" together with
    # "Conductivity" and "Thermal Resistivity" turns a decision already made into a quiz.
    exact_hits = [c for c in scored if c[0] >= _EXACT_SCORE]
    if len(exact_hits) == 1:
        scored = exact_hits

    # Deterministic: score desc, then unit-fitting first, then shortest name, then name.
    scored.sort(key=lambda s: (-s[0], not s[2], len(s[3].name), s[3].name))
    return [
        QuantityKindCandidate(
            name=ix.name,
            iri=_catalog().get("namespace", _QK_BASE) + ix.name,
            curie=f"quantitykind:{ix.name}",
            label=str(ix.entry.get("label") or ix.name),
            gloss=str(ix.entry.get("gloss") or ""),
            symbol=ix.entry.get("symbol"),
            units=tuple(ix.entry.get("units") or ()),
            score=score,
            match=match,
            unit_fits=fits,
        )
        for score, match, fits, ix in scored[: max(0, limit)]
    ]
