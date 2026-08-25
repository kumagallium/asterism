"""Column → QUDT QuantityKind resolution tests (grounding/quantity_kinds.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import asterism.grounding.quantity_kinds as qk_mod
from asterism.grounding.quantity_kinds import resolve_quantity_kind

QK_BASE = "http://qudt.org/vocab/quantitykind/"


def _names(cands) -> list[str]:
    return [c.name for c in cands]


# ----------------------------------------------------------------------------
# the column says what it is
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["temperature", "Temperature", "TEMPERATURE"],
)
def test_a_plain_name_resolves_exactly(query: str) -> None:
    top = resolve_quantity_kind(query)[0]
    assert top.name == "Temperature"
    assert top.match == "exact"
    assert top.iri == QK_BASE + "Temperature"


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("thermalConductivity", "ThermalConductivity"),  # camelCase
        ("thermal_conductivity", "ThermalConductivity"),  # snake_case
        ("Thermal Conductivity", "ThermalConductivity"),  # spaced label
        ("seebeckCoefficient", "SeebeckCoefficient"),
    ],
)
def test_the_same_words_reach_the_same_quantity(query: str, name: str) -> None:
    assert resolve_quantity_kind(query)[0].name == name


def test_a_column_that_is_not_a_quantity_gets_nothing() -> None:
    """`sampleName` is a label, not a measurement — offering it a quantity kind would
    be worse than offering nothing."""
    assert resolve_quantity_kind("sampleName") == []


# ----------------------------------------------------------------------------
# the unit says what the column cannot
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "unit", "expected"),
    [
        ("S", "V-PER-K", "SeebeckCoefficient"),
        ("rho", "OHM-M", "Resistivity"),
        ("kappa", "W-PER-M-K", "ThermalConductivity"),
    ],
)
def test_an_abbreviation_still_lands_via_its_unit(column: str, unit: str, expected: str) -> None:
    """A one-letter column name means nothing on its own. The unit is real evidence,
    and QUDT records which quantities each unit can express."""
    cands = resolve_quantity_kind(column, unit=unit)
    assert expected in _names(cands), f"{column} in {unit} should reach {expected}"
    assert all(c.match == "unit" for c in cands)
    assert all(c.unit_fits for c in cands)


def test_a_fitting_unit_promotes_a_name_match() -> None:
    """Name and unit agreeing is the strongest signal the catalog can give."""
    with_unit = resolve_quantity_kind("thermalConductivity", unit="W-PER-M-K")[0]
    without = resolve_quantity_kind("thermalConductivity")[0]
    assert with_unit.name == without.name == "ThermalConductivity"
    assert with_unit.score > without.score
    assert with_unit.match.endswith("+unit")


def test_one_exact_hit_is_the_answer_not_the_head_of_a_list() -> None:
    """Showing "Thermal Conductivity" next to "Conductivity" and "Thermal Resistivity"
    turns a decision already made into a quiz."""
    assert _names(resolve_quantity_kind("thermalConductivity")) == ["ThermalConductivity"]
    assert _names(resolve_quantity_kind("temperature")) == ["Temperature"]


def test_a_known_unit_drops_quantities_the_column_cannot_be() -> None:
    """A column in ohm·m cannot be measuring thermal resistivity, whatever its name
    shares with it."""
    names = _names(resolve_quantity_kind("resistivity", unit="OHM-M"))
    assert "ThermalResistivity" not in names
    assert names[0] == "Resistivity"


def test_a_name_that_disagrees_with_its_unit_still_shows() -> None:
    """An exact name match survives a unit that does not fit: a design where the two
    disagree is worth seeing, not hiding."""
    top = resolve_quantity_kind("temperature", unit="OHM-M")[0]
    assert top.name == "Temperature"
    assert not top.unit_fits


def test_the_unit_alone_never_outranks_a_real_name_match() -> None:
    """A quantity found only by its unit is a suggestion, not an answer."""
    cands = resolve_quantity_kind("conductivity", unit="S-PER-M")
    assert cands[0].name == "Conductivity"
    assert cands[0].score > max((c.score for c in cands[1:]), default=0)


def test_unit_only_matches_are_all_offered_rather_than_guessed() -> None:
    """23 quantity kinds are measured in kelvin and QUDT gives no way to rank them —
    handing back several is honest; inventing a winner would not be."""
    cands = resolve_quantity_kind("T", unit="K")
    assert len(cands) > 1
    assert "Temperature" in _names(cands)


# ----------------------------------------------------------------------------
# short names must not match by accident
# ----------------------------------------------------------------------------


def test_short_names_do_not_match_fuzzily() -> None:
    """"rho" is a substring of "wate*rho*rsepower" and the single token of "S" matches
    the "s" Henry's Law leaves behind an apostrophe. Both used to win."""
    assert resolve_quantity_kind("rho") == []
    assert resolve_quantity_kind("S") == []
    for c in resolve_quantity_kind("rho", unit="OHM-M"):
        assert c.match == "unit"
        assert "Horsepower" not in c.name


def test_empty_query_and_unit_resolve_to_nothing() -> None:
    assert resolve_quantity_kind("") == []
    assert resolve_quantity_kind(None) == []
    assert resolve_quantity_kind("   ", unit="  ") == []


# ----------------------------------------------------------------------------
# the catalog itself
# ----------------------------------------------------------------------------


def test_catalog_meta_carries_provenance() -> None:
    meta = qk_mod.catalog_meta()
    assert meta["source"].startswith("https://qudt.org/")
    assert meta["version"] and meta["retrieved"] and meta["license"]


def test_the_materials_quantities_this_product_exists_for_are_present() -> None:
    """A thermoelectric dataset must be able to reach all of these; a QUDT release
    that dropped one would break the reason this catalog is here."""
    catalog = yaml.safe_load(
        Path(qk_mod.__file__).with_name("qudt_quantitykinds.yaml").read_text(encoding="utf-8")
    )["quantity_kinds"]
    for name in (
        "Temperature",
        "ThermalConductivity",
        "Resistivity",
        "ElectricConductivity",
        "SeebeckCoefficient",
        "Density",
        "SpecificHeatCapacity",
    ):
        assert name in catalog, name


def test_resolution_disabled_when_catalog_missing(tmp_path: Path, monkeypatch) -> None:
    """A wheel-only install with no catalog must disable resolution, not error."""
    monkeypatch.setattr(qk_mod, "_CATALOG", tmp_path / "absent.yaml")
    qk_mod._catalog.cache_clear()
    qk_mod._index.cache_clear()
    try:
        assert resolve_quantity_kind("temperature") == []
    finally:
        qk_mod._catalog.cache_clear()
        qk_mod._index.cache_clear()
