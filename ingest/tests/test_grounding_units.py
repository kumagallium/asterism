"""Unit-string → QUDT unit resolution tests (grounding/units.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import asterism.grounding.units as units_mod
from asterism.grounding.units import resolve_unit

# ----------------------------------------------------------------------------
# identity matches — the unit's own spelling in the standard
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "name", "matched_on"),
    [
        ("V/K", "V-PER-K", "symbol"),  # qudt:symbol
        ("V.K-1", "V-PER-K", "ucum"),  # qudt:ucumCode
        ("V-PER-K", "V-PER-K", "name"),  # the local name itself
        ("volt per kelvin", "V-PER-K", "label"),  # rdfs:label, case-insensitive
        ("Volt per Kelvin", "V-PER-K", "label"),
    ],
)
def test_resolves_every_identity_spelling(query: str, name: str, matched_on: str) -> None:
    res = resolve_unit(query)
    assert res.resolved is not None, query
    assert res.resolved.name == name
    assert res.resolved.matched_on == matched_on
    assert res.resolved.iri == "http://qudt.org/vocab/unit/" + name


def test_unknown_unit_is_reported_as_unknown() -> None:
    """The whole point: a miss is SAID, not silently dropped."""
    res = resolve_unit("xyzzy")
    assert res.resolved is None
    assert res.to_dict()["status"] == "unknown"


def test_empty_query_resolves_to_nothing() -> None:
    for q in ("", "   ", None):
        assert resolve_unit(q).resolved is None


# ----------------------------------------------------------------------------
# case — symbols carry it, words do not
# ----------------------------------------------------------------------------


def test_symbol_case_is_significant() -> None:
    """``S`` siemens vs ``s`` second — reading these as the same would change the data."""
    s_upper = resolve_unit("S").resolved
    s_lower = resolve_unit("s").resolved
    assert s_upper is not None and s_lower is not None
    assert s_upper.name == "S"  # siemens
    assert s_lower.name == "SEC"  # second


def test_label_case_is_not_significant() -> None:
    assert resolve_unit("KELVIN").resolved == resolve_unit("kelvin").resolved


# ----------------------------------------------------------------------------
# SI settles the symbols several units share
# ----------------------------------------------------------------------------


def test_si_settles_a_shared_symbol() -> None:
    """``K`` is kelvin AND kayser; a data table always means kelvin."""
    res = resolve_unit("K")
    assert len(res.exact) > 1, "expected the symbol to be genuinely shared"
    assert res.si_settled
    assert res.resolved is not None and res.resolved.name == "K"


def test_ambiguity_si_cannot_settle_stays_ambiguous() -> None:
    """A currency symbol is claimed by many units and none of them is SI — the resolver
    must hand that back to a person rather than invent a winner."""
    res = resolve_unit("$")
    assert len(res.exact) > 1
    assert res.resolved is None
    assert res.to_dict()["status"] == "ambiguous"


# ----------------------------------------------------------------------------
# curated spellings — how files actually write a unit
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("W*m^(-1)*K^(-1)", "W-PER-M-K"),  # starrydata's own spelling
        ("W/(m*K)", "W-PER-M-K"),
        ("W/mK", "W-PER-M-K"),
        ("ohm*m", "OHM-M"),
        ("Ohm m", "OHM-M"),
        ("1/K", "PER-K"),
        ("-", "UNITLESS"),
        ("a.u.", "UNITLESS"),  # "arbitrary units" = the absence of a unit
    ],
)
def test_curated_spellings_resolve(query: str, name: str) -> None:
    res = resolve_unit(query)
    assert res.resolved is not None, query
    assert res.resolved.name == name
    assert res.resolved.matched_on == "alias"


def test_every_curated_spelling_points_at_a_real_unit() -> None:
    """The spelling table is hand-written and the catalog is regenerated per QUDT
    release, so they can drift apart. A dangling target would resolve to an IRI that
    does not exist — exactly the fabrication the closed set exists to prevent."""
    spellings = yaml.safe_load(
        (Path(units_mod.__file__).with_name("unit_spellings.yaml")).read_text(encoding="utf-8")
    )["spellings"]
    catalog = yaml.safe_load(
        (Path(units_mod.__file__).with_name("qudt_units.yaml")).read_text(encoding="utf-8")
    )["units"]
    dangling = {k: v for k, v in spellings.items() if v not in catalog}
    assert not dangling, f"spellings point at units QUDT does not have: {dangling}"


def test_no_curated_spelling_is_redundant() -> None:
    """A spelling QUDT already answers itself must NOT be in the hand-written table:
    two sources for one string is how they drift apart, and the table's whole job is
    to hold only what the standard is missing."""
    spellings = yaml.safe_load(
        (Path(units_mod.__file__).with_name("unit_spellings.yaml")).read_text(encoding="utf-8")
    )["spellings"]
    original = units_mod._spellings
    units_mod._spellings = lambda: ({}, {})  # resolve by identity only
    try:
        redundant = {
            k: v
            for k, v in spellings.items()
            if (r := resolve_unit(k).resolved) is not None and r.name == v
        }
    finally:
        units_mod._spellings = original
    assert not redundant, f"QUDT already resolves these without the table: {redundant}"


def test_microvolt_per_kelvin_is_left_unmapped() -> None:
    """QUDT 3.1.0 has no µV/K. Mapping it to V-PER-K would silently claim a value 10^6
    times larger, so the miss is deliberate — this pins it against a well-meaning fix."""
    for spelling in ("uV/K", "µV/K"):
        assert resolve_unit(spelling).resolved is None


# ----------------------------------------------------------------------------
# the catalog itself
# ----------------------------------------------------------------------------


def test_catalog_meta_carries_provenance() -> None:
    meta = units_mod.catalog_meta()
    assert meta["source"].startswith("https://qudt.org/")
    assert meta["version"] and meta["retrieved"] and meta["license"]


def test_resolution_disabled_when_catalog_missing(tmp_path: Path, monkeypatch) -> None:
    """A wheel-only install with no catalog must disable resolution, not error."""
    monkeypatch.setattr(units_mod, "_CATALOG", tmp_path / "absent.yaml")
    monkeypatch.setattr(units_mod, "_SPELLINGS", tmp_path / "absent-too.yaml")
    units_mod._catalog.cache_clear()
    units_mod._index.cache_clear()
    units_mod._spellings.cache_clear()
    try:
        assert resolve_unit("V/K").resolved is None
    finally:
        units_mod._catalog.cache_clear()
        units_mod._index.cache_clear()
        units_mod._spellings.cache_clear()
