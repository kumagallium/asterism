"""Tests for csv2rdf_step0.inspect.

These cover the deterministic CSV inspection pipeline:
  - column type inference (int / float / date / json / string)
  - JSON column detection (array / object)
  - uniqueness statistics — including the Phase 1 starrydata trap where
    ``sample_id`` is paper-local but not globally unique
  - foreign-key candidates across CSV pairs
  - BOM tolerance (UTF-8-sig)
  - Markdown rendering shape (smoke check)
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from csv2rdf_step0.inspect import (
    inspect_csv,
    inspect_csv_set,
    render_markdown,
)

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _write_csv(path: Path, content: str) -> Path:
    """Write content to a CSV (utf-8 by default, no BOM)."""
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def _write_csv_with_bom(path: Path, content: str) -> Path:
    """Write content with a UTF-8 BOM at the front (mimics starrydata CSVs)."""
    path.write_bytes(b"\xef\xbb\xbf" + dedent(content).lstrip("\n").encode("utf-8"))
    return path


# ----------------------------------------------------------------------------
# Column type inference
# ----------------------------------------------------------------------------


def test_column_type_inference_basic(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "basic.csv",
        """
        id,name,count,ratio,published
        1,Alice,10,0.5,2024-01-15
        2,Bob,20,0.75,2025-03-01
        3,Carol,30,1.25,2026-05-28
        """,
    )
    ins = inspect_csv(csv_path)
    assert ins.total_rows == 3
    by_name = {c.name: c for c in ins.columns}
    assert by_name["id"].inferred_type == "xsd:integer"
    assert by_name["name"].inferred_type == "xsd:string"
    assert by_name["count"].inferred_type == "xsd:integer"
    assert by_name["ratio"].inferred_type == "xsd:double"
    assert by_name["published"].inferred_type == "xsd:date"


def test_column_type_inference_mixed_int_float(tmp_path: Path) -> None:
    """Mixed int/float should widen to double."""
    csv_path = _write_csv(
        tmp_path / "mixed.csv",
        """
        value
        1
        2
        3.5
        """,
    )
    ins = inspect_csv(csv_path)
    assert ins.column("value").inferred_type == "xsd:double"  # type: ignore[union-attr]


def test_column_type_inference_invalid_date_falls_back(tmp_path: Path) -> None:
    """A column with one invalid date (e.g. month 13) should not be xsd:date."""
    csv_path = _write_csv(
        tmp_path / "baddate.csv",
        """
        d
        2024-01-15
        2024-13-99
        """,
    )
    ins = inspect_csv(csv_path)
    assert ins.column("d").inferred_type == "xsd:string"  # type: ignore[union-attr]


def test_non_null_rate(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "nulls.csv",
        """
        id,label
        1,
        2,foo
        3,
        4,bar
        """,
    )
    ins = inspect_csv(csv_path)
    label = ins.column("label")
    assert label is not None
    assert label.non_null_count == 2
    assert label.total_rows == 4
    assert label.non_null_rate == 0.5


# ----------------------------------------------------------------------------
# JSON column detection
# ----------------------------------------------------------------------------


def test_json_array_detected(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "arr.csv",
        """
        id,xs
        1,"[1, 2, 3]"
        2,"[4.5, 6.7]"
        """,
    )
    ins = inspect_csv(csv_path)
    xs = ins.column("xs")
    assert xs is not None
    assert xs.inferred_type == "json-array"
    assert xs.json_element_kind == "number"


def test_json_object_detected_with_keys(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "obj.csv",
        """
        id,info
        1,"{""given"": ""Alice"", ""family"": ""Smith""}"
        2,"{""given"": ""Bob"", ""family"": ""Jones""}"
        """,
    )
    ins = inspect_csv(csv_path)
    info = ins.column("info")
    assert info is not None
    assert info.inferred_type == "json-object"
    assert set(info.json_keys) == {"given", "family"}


def test_json_mixed_falls_back_to_string(tmp_path: Path) -> None:
    """A column where some cells are JSON and others are plain strings should
    NOT be classified as json-* — leave it to the LLM."""
    csv_path = _write_csv(
        tmp_path / "mixed_json.csv",
        """
        id,maybe
        1,"[1, 2, 3]"
        2,plain text
        """,
    )
    ins = inspect_csv(csv_path)
    maybe = ins.column("maybe")
    assert maybe is not None
    assert maybe.inferred_type == "xsd:string"


# ----------------------------------------------------------------------------
# BOM tolerance (trap T2)
# ----------------------------------------------------------------------------


def test_bom_is_stripped(tmp_path: Path) -> None:
    """UTF-8 BOM in the first cell of the header must not corrupt column names."""
    csv_path = _write_csv_with_bom(
        tmp_path / "bom.csv",
        """
        SID,DOI
        1,10.1234/abc
        2,10.5678/def
        """,
    )
    ins = inspect_csv(csv_path)
    # The first column must be "SID" — NOT "﻿SID".
    assert ins.columns[0].name == "SID"


# ----------------------------------------------------------------------------
# Uniqueness statistics (trap T1)
# ----------------------------------------------------------------------------


def test_single_column_unique(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "u.csv",
        """
        id,name
        1,a
        2,b
        3,c
        """,
    )
    ins = inspect_csv(csv_path)
    # `id` should be detected as ID candidate and tested for uniqueness.
    reports = {r.key: r for r in ins.uniqueness_reports}
    assert ("id",) in reports
    assert reports[("id",)].is_unique


def test_starrydata_style_collision(tmp_path: Path) -> None:
    """Reproduce the Phase 1 trap: `sample_id` is not globally unique, but
    `(SID, sample_id)` is. Two papers reuse sample_id=10."""
    csv_path = _write_csv(
        tmp_path / "samples.csv",
        """
        SID,sample_id,composition
        1,10,Bi2Te3
        1,11,PbTe
        2,10,SnSe
        2,12,Sb2Te3
        """,
    )
    ins = inspect_csv(csv_path, fk_hint_columns=["SID"])
    reports = {r.key: r for r in ins.uniqueness_reports}
    # sample_id alone collides (10 appears in SID=1 AND SID=2)
    assert ("sample_id",) in reports
    assert not reports[("sample_id",)].is_unique
    assert reports[("sample_id",)].collision_count == 1
    # (SID, sample_id) is unique
    composite_key = tuple(sorted(("SID", "sample_id")))
    assert composite_key in {tuple(sorted(k)) for k in reports}
    composite = next(r for r in ins.uniqueness_reports if set(r.key) == {"SID", "sample_id"})
    assert composite.is_unique


def test_starrydata_three_way_composite(tmp_path: Path) -> None:
    """Curves trap: (SID, figure_id) is NOT enough; (SID, figure_id, sample_id) is.
    Same figure_id in same paper holds curves from multiple samples."""
    csv_path = _write_csv(
        tmp_path / "curves.csv",
        """
        SID,figure_id,sample_id,property_y
        1,7,10,Seebeck
        1,7,11,Resistivity
        2,7,12,Seebeck
        """,
    )
    ins = inspect_csv(csv_path, fk_hint_columns=["SID", "figure_id", "sample_id"])
    # 2-column composites should collide (SID=1, figure_id=7 has two samples)
    two_way = next(
        r
        for r in ins.uniqueness_reports
        if set(r.key) == {"SID", "figure_id"}
    )
    assert not two_way.is_unique
    # 3-column composite resolves it
    three_way = next(
        r
        for r in ins.uniqueness_reports
        if set(r.key) == {"SID", "figure_id", "sample_id"}
    )
    assert three_way.is_unique
    assert three_way.collision_count == 0


def test_uniqueness_handles_empty_keys(tmp_path: Path) -> None:
    """Rows with an empty key column are dropped from analysis."""
    csv_path = _write_csv(
        tmp_path / "partial.csv",
        """
        id,name
        1,a
        ,b
        2,c
        """,
    )
    ins = inspect_csv(csv_path)
    # id alone (2 distinct, 0 collision after dropping empties)
    rep = next(r for r in ins.uniqueness_reports if r.key == ("id",))
    assert rep.total_rows_considered == 2
    assert rep.distinct_tuples == 2
    assert rep.is_unique


# ----------------------------------------------------------------------------
# Cross-CSV foreign key candidates
# ----------------------------------------------------------------------------


def test_foreign_key_candidate(tmp_path: Path) -> None:
    papers = _write_csv(
        tmp_path / "papers.csv",
        """
        SID,title
        1,Foo
        2,Bar
        3,Baz
        """,
    )
    samples = _write_csv(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        1,11
        2,12
        """,
    )
    _insps, fks = inspect_csv_set([papers, samples], fk_hint_columns=["SID"])
    # Both CSVs have a "SID" column with overlapping values {1, 2}
    sid_fks = [f for f in fks if f.from_column == "SID"]
    assert len(sid_fks) == 1
    assert sid_fks[0].overlap_count == 2  # {1, 2} overlap


# ----------------------------------------------------------------------------
# Markdown rendering smoke test
# ----------------------------------------------------------------------------


def test_render_markdown_contains_expected_sections(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "smoke.csv",
        """
        id,label,xs
        1,a,"[1, 2]"
        2,b,"[3, 4]"
        """,
    )
    ins = inspect_csv(csv_path)
    md = render_markdown([ins])
    assert "## CSV: smoke.csv" in md
    assert "### Columns" in md
    assert "### JSON columns" in md
    assert "### Uniqueness" in md
    assert "trap T1" in md
    assert "`xs`" in md
    assert "json-array" in md


def test_render_markdown_renders_collisions(tmp_path: Path) -> None:
    """Collision counts must appear in the Markdown so the LLM can see them."""
    csv_path = _write_csv(
        tmp_path / "coll.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    ins = inspect_csv(csv_path, fk_hint_columns=["SID"])
    md = render_markdown([ins])
    # The single-column uniqueness test should show 1 collision
    assert "✗" in md  # at least one row marked non-unique
    assert "✓" in md  # composite key should also appear in the table
