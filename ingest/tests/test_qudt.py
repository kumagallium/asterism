"""QUDT normalization lookup tests."""
from __future__ import annotations

from asterism.qudt import (
    QUANTITY_KIND_BASE,
    UNIT_BASE,
    quantity_kind_iri,
    unit_iri,
)

# ----------------------------------------------------------------------------
# quantity_kind_iri
# ----------------------------------------------------------------------------


def test_quantity_kind_basic() -> None:
    assert (
        quantity_kind_iri("Seebeck coefficient")
        == QUANTITY_KIND_BASE + "SeebeckCoefficient"
    )


def test_quantity_kind_synonym_unifies() -> None:
    # The whole point: 'thermopower' is a synonym of 'Seebeck coefficient'
    # and must resolve to the same QUDT IRI.
    assert quantity_kind_iri("thermopower") == quantity_kind_iri("Seebeck coefficient")


def test_quantity_kind_case_insensitive() -> None:
    assert (
        quantity_kind_iri("THERMOPOWER")
        == quantity_kind_iri("thermopower")
        == QUANTITY_KIND_BASE + "SeebeckCoefficient"
    )
    assert quantity_kind_iri("  Thermal Conductivity  ") == (
        QUANTITY_KIND_BASE + "ThermalConductivity"
    )


def test_quantity_kind_unmapped_returns_none() -> None:
    assert quantity_kind_iri("ZT") is None
    assert quantity_kind_iri("Power factor") is None
    assert quantity_kind_iri("") is None
    assert quantity_kind_iri(None) is None


# ----------------------------------------------------------------------------
# unit_iri
# ----------------------------------------------------------------------------


def test_unit_basic() -> None:
    assert unit_iri("V*K^(-1)") == UNIT_BASE + "V-PER-K"
    assert unit_iri("ohm*m") == UNIT_BASE + "OHM-M"
    assert unit_iri("W*m^(-1)*K^(-1)") == UNIT_BASE + "W-PER-M-K"


def test_unit_case_sensitive() -> None:
    # 'K' (kelvin) is mapped; 'k' is not — unit symbols carry case meaning.
    assert unit_iri("K") == UNIT_BASE + "K"
    assert unit_iri("k") is None
    # 'S*m^(-1)' (siemens/m) mapped; lowercasing would corrupt it.
    assert unit_iri("S*m^(-1)") == UNIT_BASE + "S-PER-M"


def test_unit_strips_whitespace() -> None:
    assert unit_iri("  V  ") == UNIT_BASE + "V"


def test_unit_unmapped_returns_none() -> None:
    assert unit_iri("ohm*cm") is None  # only ohm*m is mapped
    assert unit_iri("") is None
    assert unit_iri(None) is None


def test_conductivity_variants_unify() -> None:
    # Both ohm^(-1)*m^(-1) and S*m^(-1) are siemens/metre -> same QUDT unit.
    assert unit_iri("ohm^(-1)*m^(-1)") == unit_iri("S*m^(-1)") == UNIT_BASE + "S-PER-M"
