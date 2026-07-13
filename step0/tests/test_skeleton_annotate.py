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
