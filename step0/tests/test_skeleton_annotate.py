"""Tests for skeleton_annotate — deterministic evidence for the skeleton gate."""

from __future__ import annotations

from pathlib import Path

from asterism_step0.dialect import SourceDialect
from asterism_step0.skeleton_annotate import annotate_skeleton

_PREFIXES = {
    "xr": "https://example.org/xrd/resource/",
    "xo": "https://example.org/xrd/ontology#",
}


def _skeleton(template: str, *, classes: list[str] | None = None, source: str = "xrd.csv") -> dict:
    return {
        "version": 1,
        "prefixes": dict(_PREFIXES),
        "maps": [
            {
                "name": "point",
                "source": source,
                "subject": {"template": template, "classes": classes or ["xo:DataPoint"]},
            }
        ],
    }


def _write_xrd(tmp_path: Path) -> Path:
    # 2θ repeats across scans — the production shape that collapses rows.
    p = tmp_path / "xrd.csv"
    p.write_text(
        "2θ (deg),intensity,scan_id\n"
        "10.00,120,S1\n"
        "10.02,135,S1\n"
        "10.00,98,S2\n"
        "10.02,101,S2\n"
        "10.04,77,S2\n",
        encoding="utf-8",
    )
    return p


def test_unique_key_passes_with_previews(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    skeleton = _skeleton("xr:sample/{sample_id}", source="samples.csv")
    ann = annotate_skeleton(skeleton, [p])["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is True
    assert ann["colliding_rows"] == 0
    assert ann["collision_examples"] == []
    assert ann["key_candidates"] == []
    # Previews are prefix-expanded real IDs, in file order.
    assert ann["id_previews"] == [
        "https://example.org/xrd/resource/sample/S-1",
        "https://example.org/xrd/resource/sample/S-2",
    ]
    assert ann["expanded_template"] == "https://example.org/xrd/resource/sample/{sample_id}"


def test_non_unique_key_reports_collisions_and_candidates(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{2θ (deg)}"), [p])["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is False
    assert ann["total_rows"] == 5
    assert ann["distinct_ids"] == 3
    assert ann["colliding_rows"] == 2
    # Concrete colliding rows, numbered as the file reads (header = line 1).
    first = ann["collision_examples"][0]
    assert first["key_values"] == {"2θ (deg)": "10.00"}
    assert first["line_numbers"] == [2, 4]
    # The duplicate ID is visible in the previews (rows 1 and 3 mint the same IRI).
    assert ann["id_previews"][0] == ann["id_previews"][2]
    # Fix candidates exist, and measurement-only ones (intensity) never rank first.
    candidates = ann["key_candidates"]
    assert candidates, "expected unique key candidates"
    assert candidates[0]["measurement_only"] is False


def test_unique_measurement_key_carries_caution(tmp_path: Path) -> None:
    """K7: a key of measurement values that happens to be unique TODAY is flagged
    (real dogfood: an AI-minted ID from 3.6E+1-style readings passed the green
    band on 13 accidentally-distinct rows) — and safer candidates still show."""
    p = tmp_path / "xrd.csv"
    p.write_text(
        "2θ (deg),intensity,scan_id\n10.00,120,S1\n10.02,135,S1\n10.04,98,S2\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(_skeleton("xr:point/{2θ (deg)}"), [p])["maps"]["point"]
    assert ann["is_unique"] is True
    assert ann["key_measurement_caution"] is True
    # Unlike the plain-unique case, the proven alternatives are still offered.
    assert ann["key_candidates"], "expected safer key candidates alongside the caution"
    assert any(not c["measurement_only"] for c in ann["key_candidates"])


def test_unique_text_key_has_no_measurement_caution(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    ann = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])["maps"][
        "point"
    ]
    assert ann["is_unique"] is True
    assert ann["key_measurement_caution"] is False
    assert ann["key_candidates"] == []


def test_composite_key_template_is_checked_as_a_tuple(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{scan_id}/{2θ (deg)}"), [p])["maps"]["point"]
    assert ann["key_columns"] == ["scan_id", "2θ (deg)"]
    assert ann["is_unique"] is True
    assert ann["id_previews"][0] == "https://example.org/xrd/resource/point/S1/10.00"


def test_missing_column_is_reported_not_guessed(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{two_theta}"), [p])["maps"]["point"]
    assert ann["checkable"] is False
    assert ann["reason"] == "missing-columns"
    assert ann["missing_columns"] == ["two_theta"]


def test_undeclared_prefix_is_flagged(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    skeleton = _skeleton("xr:point/{scan_id}", classes=["prov:Entity", "xo:DataPoint"])
    ann = annotate_skeleton(skeleton, [p])["maps"]["point"]
    assert ann["undeclared_prefixes"] == ["prov"]
    expanded = {e["curie"]: e["iri"] for e in ann["expanded_classes"]}
    assert expanded["xo:DataPoint"] == "https://example.org/xrd/ontology#DataPoint"
    assert expanded["prov:Entity"] == "prov:Entity"  # unexpandable stays as-is


def test_constant_subject_is_out_of_scope(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    skeleton = {
        "version": 1,
        "prefixes": dict(_PREFIXES),
        "maps": [
            {
                "name": "doc",
                "source": "xrd.csv",
                "subject": {"constant": "xr:doc/1", "classes": ["xo:Doc"]},
            }
        ],
    }
    ann = annotate_skeleton(skeleton, [p])["maps"]["doc"]
    assert ann["checkable"] is False
    assert ann["reason"] == "constant"
    assert ann["expanded_template"] == "https://example.org/xrd/resource/doc/1"


def test_placeholderless_template_collapses_everything(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/only-one"), [p])["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is False
    assert ann["distinct_ids"] == 1
    assert ann["colliding_rows"] == 4  # 5 rows → 1 ID


def test_source_not_found_degrades(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{scan_id}", source="other.csv"), [p])
    assert ann["maps"]["point"]["checkable"] is False
    assert ann["maps"]["point"]["reason"] == "source-not-found"


def test_dialect_read_and_line_numbers_include_preamble(tmp_path: Path) -> None:
    # Instrument text: 2 preamble lines + tab-separated table (the user's case).
    p = tmp_path / "xrd-scan.txt"
    p.write_text(
        "# Instrument: XRD-9000\n"
        "# Date: 2026-07-13\n"
        "2θ (deg)\tintensity\tscan_id\n"
        "10.00\t120\tS1\n"
        "10.02\t135\tS1\n"
        "10.00\t98\tS2\n",
        encoding="utf-8",
    )
    dialect = SourceDialect(delimiter="\t", skip_rows=2)
    skeleton = _skeleton("xr:point/{2θ (deg)}", source="xrd-scan.txt")
    ann = annotate_skeleton(skeleton, [p], dialects={"xrd-scan.txt": dialect})["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is False
    # File line numbers count the 2 preamble lines + the header: data starts at 4.
    assert ann["collision_examples"][0]["line_numbers"] == [4, 6]
    assert ann["id_previews"][0] == "https://example.org/xrd/resource/point/10.00"


def test_placeholder_prefixes_flagged_at_top_level(tmp_path: Path) -> None:
    """ADR instance-iri-base.md: the gate evidence names prefixes minted on a
    placeholder domain (this file's fixtures deliberately sit on example.org),
    skeleton-level — the gate shows it before the paid continue run."""
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\n", encoding="utf-8")
    out = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])
    flagged = {e["prefix"]: e["iri"] for e in out["placeholder_prefixes"]}
    assert set(flagged) == {"xr", "xo"}
    assert flagged["xr"] == "https://example.org/xrd/resource/"


def test_instance_and_invalid_namespaces_not_flagged(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\n", encoding="utf-8")
    skeleton = _skeleton("xr:sample/{sample_id}", source="samples.csv")
    skeleton["prefixes"] = {
        "xr": "https://asterism.invalid/datasets/xrd/resource/",
        "xo": "https://data.lab.jp/asterism/datasets/xrd/ontology#",
        "schema": "https://schema.org/",
    }
    out = annotate_skeleton(skeleton, [p])
    assert out["placeholder_prefixes"] == []


def test_class_named_after_numeric_key_column_carries_caution(tmp_path: Path) -> None:
    """The ZEM naming trap: a measurement-only key whose CLASS is named after
    the numeric key column ("Temperature" over key {Measurement temp.(C)}) —
    the row identity mislabeled as one of its measurements. Token match is
    prefix-tolerant (temp ≈ temperature)."""
    p = tmp_path / "zem.csv"
    p.write_text(
        "Measurement temp.(C),Resistivity(Ohm m)\n"
        "3.636740E+1,1.294886E-6\n"
        "6.029985E+1,1.381926E-6\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(
        _skeleton("xr:t/{Measurement temp.(C)}", classes=["xo:Temperature"], source="zem.csv"),
        [p],
    )["maps"]["point"]
    assert ann["key_measurement_caution"] is True
    assert ann["class_numeric_key_caution"] == [
        {"class": "xo:Temperature", "column": "Measurement temp.(C)", "token": "temp"}
    ]


def test_row_class_over_mixed_key_has_no_class_caution(tmp_path: Path) -> None:
    """A legitimate row class over a mixed (text+numeric) key never triggers the
    naming caution — even though "Measurement" shares a token with the numeric
    column ("Measurement temp.(C)"), the key is not measurement-only."""
    p = tmp_path / "zem.csv"
    p.write_text(
        "Sample,Measurement temp.(C)\nA,3.6E+1\nA,6.0E+1\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(
        _skeleton(
            "xr:m/{Sample}/{Measurement temp.(C)}",
            classes=["xo:Measurement"],
            source="zem.csv",
        ),
        [p],
    )["maps"]["point"]
    assert ann["key_measurement_caution"] is False
    assert ann["class_numeric_key_caution"] == []


def test_unrelated_class_name_over_numeric_key_has_no_class_caution(tmp_path: Path) -> None:
    """K7 alone (numeric-only key) does not imply the naming trap: a class name
    sharing no token with the key column stays clean (only the K7 caution shows)."""
    p = tmp_path / "xrd.csv"
    p.write_text("2theta,intensity\n10.00,120\n10.02,135\n", encoding="utf-8")
    ann = annotate_skeleton(
        _skeleton("xr:p/{2theta}", classes=["xo:DiffractionPoint"], source="xrd.csv"),
        [p],
    )["maps"]["point"]
    assert ann["key_measurement_caution"] is True
    assert ann["class_numeric_key_caution"] == []


def test_dataset_namespace_info_in_annotations(tmp_path: Path) -> None:
    """The gate's namespace card rides annotations: which prefixes are THIS
    dataset's minted pair, under which base, configured or not (ADR K13)."""
    p = tmp_path / "samples.csv"
    p.write_text("sample_id\nS-1\n", encoding="utf-8")
    skeleton = _skeleton("xr:sample/{sample_id}", source="samples.csv")
    skeleton["prefixes"] = {
        "al3v": "https://asterism.invalid/datasets/al3v-sps2/ontology#",
        "al3vr": "https://asterism.invalid/datasets/al3v-sps2/resource/",
        "schema": "https://schema.org/",
    }
    out = annotate_skeleton(skeleton, [p])
    assert out["dataset_namespace"] == {
        "slug": "al3v-sps2",
        "base": "https://asterism.invalid",
        "base_configured": False,
        "ontology_prefix": "al3v",
        "resource_prefix": "al3vr",
    }
    # Configured base flips the flag (the Settings value arrives resolved).
    out2 = annotate_skeleton(skeleton, [p], iri_base="https://data.lab.jp/asterism")
    assert out2["dataset_namespace"]["base_configured"] is True
    # No minted pair (this file's example.org fixtures) → explicit None.
    out3 = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])
    assert out3["dataset_namespace"] is None
