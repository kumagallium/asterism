"""Deterministic display-unit extraction (task #10).

``extract_unit_from_label`` pulls a unit out of a column's trailing parentheses
without a model call or a materials-unit dictionary; ``enrich_units`` overlays
those units onto a Mapping IR, filling only blank single-column properties.
"""
from __future__ import annotations

import pytest

from asterism_step0.units import enrich_units, extract_unit_from_label

# ---------------------------------------------------------------------------
# extract_unit_from_label — accepted units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "unit"),
    [
        ("Resistivity(Ohm m)", "Ohm m"),
        ("Seebeck coeff.(V/K)", "V/K"),
        ("Power factor(W/m K^2)", "W/m K^2"),
        ("Measurement temp.(C)", "C"),
        ("Dark EMF(V)", "V"),
        ("Figure of merit(1/K)", "1/K"),
        ("Temperature (K)", "K"),  # space before the parenthesis
        ("conductivity (S/m)", "S/m"),
        ("Hall coefficient (m^3/C)", "m^3/C"),
        ("Density(g/cm^3)", "g/cm^3"),
        ("Seebeck (µV/K)", "µV/K"),  # micro sign
        ("thermal conductivity (W m^-1 K^-1)", "W m^-1 K^-1"),  # 3 tokens but symbols
        ("efficiency (%)", "%"),  # symbol-only unit
        ("angle (°)", "°"),
        ("torque (N m)", "N m"),  # two words, one short token
        ("count (mol)", "mol"),  # single plain-word token
        ("電気抵抗率\uff08Ω·m\uff09", "Ω·m"),  # full-width parens + omega + middle dot
    ],
)
def test_extracts_unit(column: str, unit: str) -> None:
    assert extract_unit_from_label(column) == unit


# ---------------------------------------------------------------------------
# extract_unit_from_label — rejected (None): no over-completion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        "sample_id",  # no parentheses at all
        "SID",
        "title",
        "composition",
        "",
        "(V)",  # no quantity name in front
        "x (2)",  # bare number is a value/count, not a unit
        "resistivity (300 K)",  # value + condition, not a unit
        "power (1.5 W actual)",  # leading value token
        "ZT (-)",  # pure punctuation, no unit signal
        "field (/)",
        "scale (10^6)",  # a scale factor with no unit letter
        "notes (see appendix)",  # two long words → prose
        "count (per sample)",
        "temp (room temp)",
        "value (at room temp)",  # three words → prose
        "value (measured at 300 K)",  # long descriptive text (>12 chars)
        "col (材料)",  # CJK content is not a unit
        "col (温度 K)",  # mixed CJK
    ],
)
def test_rejects_non_unit(column: str) -> None:
    assert extract_unit_from_label(column) is None


def test_none_input_is_none() -> None:
    assert extract_unit_from_label(None) is None


def test_length_boundary() -> None:
    # 12 chars of unit notation is accepted; 13 is treated as prose/description.
    assert extract_unit_from_label("x (abcdefghijkl)") == "abcdefghijkl"
    assert extract_unit_from_label("x (abcdefghijklm)") is None


def test_last_parenthesis_group_wins() -> None:
    # A qualifier earlier in the name is ignored; the trailing group is the unit.
    assert extract_unit_from_label("Resistivity (in-plane) (Ohm m)") == "Ohm m"


# ---------------------------------------------------------------------------
# enrich_units — Mapping IR overlay
# ---------------------------------------------------------------------------

_IR_BRACKETED = """\
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: measurement
    source: data.csv
    subject:
      template: "exr:m/{id}"
      classes: [ex:Measurement]
    properties:
      - predicate: ex:resistivity
        column: "Resistivity(Ohm m)"
      - predicate: ex:seebeck
        column: "Seebeck coeff.(V/K)"
        label: "ゼーベック係数"
      - predicate: ex:sampleId
        column: sample_id
"""


def _load(yaml_text: str) -> dict:
    import yaml

    return yaml.safe_load(yaml_text)


def _props(doc: dict) -> dict[str, dict]:
    return {p["predicate"]: p for p in doc["maps"][0]["properties"]}


def test_enrich_fills_blank_units_from_brackets() -> None:
    out = enrich_units(_IR_BRACKETED)
    props = _props(_load(out))
    assert props["ex:resistivity"]["unit"] == "Ohm m"
    assert props["ex:seebeck"]["unit"] == "V/K"
    # a pre-existing label is preserved, not clobbered
    assert props["ex:seebeck"]["label"] == "ゼーベック係数"
    # a plain column name yields no unit
    assert "unit" not in props["ex:sampleId"]


def test_enrich_never_overwrites_authored_unit() -> None:
    ir = _IR_BRACKETED.replace(
        "        column: \"Resistivity(Ohm m)\"\n",
        "        column: \"Resistivity(Ohm m)\"\n        unit: \"mΩ cm\"\n",
    )
    props = _props(_load(enrich_units(ir)))
    assert props["ex:resistivity"]["unit"] == "mΩ cm"  # authored value wins


def test_enrich_skips_non_single_column_rows() -> None:
    ir = """\
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: m
    source: data.csv
    subject:
      template: "exr:m/{id}"
      classes: [ex:M]
    properties:
      - predicate: ex:count
        columns: ["Temp(K)", "Power(W)"]
        function: float_array_count
      - predicate: ex:link
        object_template: "exr:other/{Field(T)}"
      - predicate: ex:fixed
        constant: "Voltage(V)"
"""
    # None of these reference a single source column, so nothing is enriched and
    # the text is returned byte-identical.
    assert enrich_units(ir) == ir


def test_enrich_noop_is_byte_identical() -> None:
    clean = """\
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: m
    source: data.csv
    subject:
      template: "exr:m/{id}"
      classes: [ex:M]
    properties:
      - predicate: ex:name
        column: title
"""
    assert enrich_units(clean) is clean or enrich_units(clean) == clean


def test_enrich_broken_yaml_flows_on_unchanged() -> None:
    broken = "version: 1\nmaps: [ - this is : : not yaml"
    assert enrich_units(broken) == broken


def test_enrich_preserves_column_with_function() -> None:
    # A single-column property that also carries a Tier-0 function still gets its
    # unit filled (the column name is the source, function or not).
    ir = _IR_BRACKETED.replace(
        "      - predicate: ex:resistivity\n        column: \"Resistivity(Ohm m)\"\n",
        "      - predicate: ex:resistivity\n        column: \"Resistivity(Ohm m)\"\n"
        "        function: float_of\n",
    )
    props = _props(_load(enrich_units(ir)))
    assert props["ex:resistivity"]["unit"] == "Ohm m"
    assert props["ex:resistivity"]["function"] == "float_of"
