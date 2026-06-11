"""Tests for :mod:`asterism.tabularize` — flatten a native-JSON source into the
JSON-string-cell tabular shape the Tier 0 exploders consume, then prove (gated on
the optional morph-kgc extra) that the three nested-array shapes the coverage
report flagged as irreducible now materialize *linked to their parent row*.

Decision of record: ``docs/architecture/native-json-denormalization.md``.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from asterism.tabularize import (
    RESERVED_COLUMNS,
    flatten_record,
    safe_col,
    sanitize_csv_columns,
    tabularize_json_to_csv,
    tabularize_records,
)


def _morph_kgc_installed() -> bool:
    try:
        import morph_kgc  # noqa: F401

        return True
    except ImportError:
        return False


# ---- pure flattening --------------------------------------------------------


def test_scalar_and_dotted_object() -> None:
    row = flatten_record({"id": "r1", "owner": {"login": "octocat", "type": "User"}})
    assert row == {"id": "r1", "owner.login": "octocat", "owner.type": "User"}


def test_list_leaf_kept_as_json_string() -> None:
    row = flatten_record({"id": "r1", "topics": ["ai", "ml"]})
    # the array is preserved as a JSON STRING cell — the shape json_array explodes
    assert row["topics"] == '["ai", "ml"]'
    assert json.loads(row["topics"]) == ["ai", "ml"]


def test_object_array_leaf_kept_as_json_string() -> None:
    row = flatten_record({"author": [{"family": "Adams"}, {"family": "Brown"}]})
    assert json.loads(row["author"]) == [{"family": "Adams"}, {"family": "Brown"}]


def test_bool_lowercased_and_null_blank() -> None:
    row = flatten_record({"archived": True, "forked": False, "lang": None})
    assert row == {"archived": "true", "forked": "false", "lang": ""}


def test_non_ascii_preserved_in_array_cell() -> None:
    row = flatten_record({"subject": ["Chimie", "Éxito"]})
    assert json.loads(row["subject_"]) == ["Chimie", "Éxito"]


def test_non_object_record_uses_value_column() -> None:
    assert flatten_record("solo") == {"value": "solo"}
    assert flatten_record(["a", "b"]) == {"value": '["a", "b"]'}


# ---- reserved-column collision (the spike's bonus finding) -------------------


def test_safe_col_renames_only_reserved() -> None:
    assert {"subject", "predicate"} == RESERVED_COLUMNS
    assert safe_col("subject") == "subject_"
    assert safe_col("predicate") == "predicate_"
    # object / graph are NOT reserved by Morph-KGC, nor is anything else
    assert safe_col("object") == "object"
    assert safe_col("graph") == "graph"
    assert safe_col("subjects") == "subjects"
    assert safe_col("SUBJECT") == "SUBJECT"


def test_sanitize_csv_columns_renames_only_when_reserved(tmp_path: Path) -> None:
    # a CSV with a reserved header → rewritten with the header renamed
    src = tmp_path / "ol.csv"
    src.write_text('id,subject,note\nb1,"[""x""]",ok\n', encoding="utf-8")
    dest = tmp_path / "ol.out.csv"
    assert sanitize_csv_columns(src, dest) is True
    with dest.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert "subject" not in rows[0] and rows[0]["subject_"] == '["x"]'
    assert rows[0]["id"] == "b1" and rows[0]["note"] == "ok"  # other columns intact


def test_sanitize_csv_columns_noop_without_reserved(tmp_path: Path) -> None:
    src = tmp_path / "plain.csv"
    src.write_text("id,name\n1,a\n", encoding="utf-8")
    dest = tmp_path / "plain.out.csv"
    assert sanitize_csv_columns(src, dest) is False  # no reserved column → no copy
    assert not dest.exists()


def test_reserved_column_renamed_in_flatten() -> None:
    row = flatten_record({"subject": ["x"], "predicate": "p", "nested": {"subject": 1}})
    assert "subject" not in row and "subject_" in row
    assert "predicate" not in row and "predicate_" in row
    # a *nested* path "nested.subject" is not the reserved bare name → unchanged
    assert row["nested.subject"] == "1"


# ---- CSV writer -------------------------------------------------------------


def test_tabularize_json_to_csv_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    src.write_text(
        json.dumps([{"id": "a", "topics": ["x", "y"]}, {"id": "b", "topics": []}]),
        encoding="utf-8",
    )
    dest = tmp_path / "out.csv"
    cols = tabularize_json_to_csv(src, dest)
    assert cols == ["id", "topics"]
    with dest.open(encoding="utf-8", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert read[0]["id"] == "a"
    assert json.loads(read[0]["topics"]) == ["x", "y"]
    assert read[1]["topics"] == "[]"


def test_tabularize_records_sparse_columns() -> None:
    rows = tabularize_records([{"a": 1}, {"b": 2}])
    assert rows == [{"a": "1"}, {"b": "2"}]


def test_record_path_selects_inner_array(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"docs": [{"id": "a"}, {"id": "b"}]}), encoding="utf-8")
    dest = tmp_path / "out.csv"
    tabularize_json_to_csv(src, dest, record_path="docs")
    with dest.open(encoding="utf-8", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert [r["id"] for r in read] == ["a", "b"]


def test_wrapped_array_auto_detected_without_record_path(tmp_path: Path) -> None:
    """The common API-response shape `{"docs": [...]}` (OpenLibrary, etc.) is
    auto-detected as the record array even when no record_path is passed — so the
    substrate's auto-tabularize (which has no record_path) handles wrapped arrays."""
    src = tmp_path / "src.json"
    src.write_text(
        json.dumps({"numFound": 2, "docs": [{"id": "a"}, {"id": "b"}]}),
        encoding="utf-8",
    )
    dest = tmp_path / "out.csv"
    tabularize_json_to_csv(src, dest)  # no record_path
    with dest.open(encoding="utf-8", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert [r["id"] for r in read] == ["a", "b"]  # docs[] exploded, not the wrapper


def test_plain_object_without_record_array_is_one_row(tmp_path: Path) -> None:
    """A dict with no array-of-objects value is still a single record (no regression
    for genuine single-object documents)."""
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"id": "solo", "tags": ["x", "y"]}), encoding="utf-8")
    dest = tmp_path / "out.csv"
    tabularize_json_to_csv(src, dest)
    with dest.open(encoding="utf-8", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert len(read) == 1 and read[0]["id"] == "solo"
    assert json.loads(read[0]["tags"]) == ["x", "y"]  # scalar array stays a cell


# ---- end-to-end: nested arrays explode and link to the parent ---------------
#
# These are the three coverage `…Raw` shapes. Gated on the optional morph-kgc
# extra: they prove tabularize + the EXISTING Tier 0 exploders (json_pluck /
# json_array) close them — no new function, the T9 closed set untouched.

_PREFIXES = """
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix rmlf: <http://w3id.org/rml/> .
@prefix fn:   <https://kumagallium.github.io/asterism/fn/> .
@prefix ex:   <https://ex/> .
"""


def _materialize(rml: str, csv_dir: Path) -> set[tuple[str, str, str]]:
    from asterism.substrate import materialize_to_graph

    graph = materialize_to_graph(rml, csv_dir)
    return {(str(s), str(p), str(o)) for s, p, o in graph}


def test_e2e_object_array_plucks_subfield_linked_to_parent(tmp_path: Path) -> None:
    """crossref `author` shape: an object array → json_pluck a sub-field per element."""
    if not _morph_kgc_installed():
        pytest.skip("morph-kgc not installed; this exercises the real materialize")
    src = tmp_path / "cr.json"
    src.write_text(
        json.dumps(
            [
                {"id": "w1", "author": [{"family": "Adams"}, {"family": "Brown"}]},
                {"id": "w2", "author": [{"family": "Clark"}]},
            ]
        ),
        encoding="utf-8",
    )
    tabularize_json_to_csv(src, tmp_path / "cr.csv")
    rml = _PREFIXES + """
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "cr.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/w/{id}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:authorFamily ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_pluck ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "author" ] ] ;
      rmlf:input [ rmlf:parameter fn:p_field ;
        rmlf:inputValueMap [ rmlf:constant "family" ] ] ] ] ] .
"""
    triples = _materialize(rml, tmp_path)
    assert ("https://ex/w/w1", "https://ex/authorFamily", "Adams") in triples
    assert ("https://ex/w/w1", "https://ex/authorFamily", "Brown") in triples
    assert ("https://ex/w/w2", "https://ex/authorFamily", "Clark") in triples


def test_e2e_scalar_array_explodes_linked_to_parent(tmp_path: Path) -> None:
    """github `topics` shape: a scalar array → json_array explodes each element."""
    if not _morph_kgc_installed():
        pytest.skip("morph-kgc not installed; this exercises the real materialize")
    src = tmp_path / "gh.json"
    src.write_text(
        json.dumps([{"id": "r1", "topics": ["ai", "ml"]}, {"id": "r2", "topics": []}]),
        encoding="utf-8",
    )
    tabularize_json_to_csv(src, tmp_path / "gh.csv")
    rml = _PREFIXES + """
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "gh.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/r/{id}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:topic ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_array ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "topics" ] ] ] ] ] .
"""
    triples = _materialize(rml, tmp_path)
    assert ("https://ex/r/r1", "https://ex/topic", "ai") in triples
    assert ("https://ex/r/r1", "https://ex/topic", "ml") in triples
    # an empty array yields no triple for r2
    assert not any(s == "https://ex/r/r2" and p == "https://ex/topic" for s, p, _ in triples)


def test_e2e_reserved_subject_column_renamed_then_explodes(tmp_path: Path) -> None:
    """openlibrary `subject` shape: the reserved column name is renamed to
    `subject_` by tabularize, so the explode links to the parent instead of
    silently producing 0 triples (the collision the spike uncovered)."""
    if not _morph_kgc_installed():
        pytest.skip("morph-kgc not installed; this exercises the real materialize")
    src = tmp_path / "ol.json"
    src.write_text(
        json.dumps([{"id": "b1", "subject": ["Math", "Physics"]}]),
        encoding="utf-8",
    )
    cols = tabularize_json_to_csv(src, tmp_path / "ol.csv")
    assert "subject" not in cols and "subject_" in cols  # renamed at the boundary
    rml = _PREFIXES + """
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "ol.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/b/{id}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:subject ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_array ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "subject_" ] ] ] ] ] .
"""
    triples = _materialize(rml, tmp_path)
    assert ("https://ex/b/b1", "https://ex/subject", "Math") in triples
    assert ("https://ex/b/b1", "https://ex/subject", "Physics") in triples


# ---- substrate wiring: a JSON source is tabularized to CSV at the boundary ----


def test_tabularize_json_sources_rewrites_absent_csv_backed_by_json(tmp_path: Path) -> None:
    """A mapping referencing `x.csv` (absent) backed by a sibling `x.json` is
    rewritten to a derived CSV; a real CSV and a native-JSON reference are left
    untouched."""
    from asterism.substrate import tabularize_json_sources

    (tmp_path / "x.json").write_text(json.dumps([{"id": "a", "t": ["p"]}]), encoding="utf-8")
    (tmp_path / "real.csv").write_text("id\na\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    rml = (
        'rml:source "x.csv" .\n'      # absent CSV, json sibling present → derive
        'rml:source "real.csv" .\n'   # real CSV present → unchanged
        'rml:source "x.json" .\n'     # native JSON reference → unchanged
    )
    out = tabularize_json_sources(rml, tmp_path, work)
    assert f'rml:source "{work / "x.csv"}"' in out  # rewritten to derived absolute path
    assert (work / "x.csv").exists()                 # and the CSV was actually written
    assert 'rml:source "real.csv"' in out            # real CSV untouched
    assert 'rml:source "x.json"' in out              # native JSON untouched


def test_e2e_substrate_auto_tabularizes_json_source(tmp_path: Path) -> None:
    """End-to-end: the caller persists only a `.json` source and a CSV mapping that
    references `<name>.csv`; substrate derives the tabularized CSV transparently so
    the nested object array explodes linked to its parent. This is the wiring the
    propose step targets — no native JSONPath, the source stays JSON on disk."""
    if not _morph_kgc_installed():
        pytest.skip("morph-kgc not installed; this exercises the real materialize")
    (tmp_path / "cr.json").write_text(
        json.dumps([{"id": "w1", "author": [{"family": "Adams"}, {"family": "Brown"}]}]),
        encoding="utf-8",
    )
    # NB: the mapping references cr.CSV (ql:CSV), never cr.json — substrate derives it.
    rml = _PREFIXES + """
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "cr.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/w/{id}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:authorFamily ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_pluck ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "author" ] ] ;
      rmlf:input [ rmlf:parameter fn:p_field ;
        rmlf:inputValueMap [ rmlf:constant "family" ] ] ] ] ] .
"""
    triples = _materialize(rml, tmp_path)
    assert ("https://ex/w/w1", "https://ex/authorFamily", "Adams") in triples
    assert ("https://ex/w/w1", "https://ex/authorFamily", "Brown") in triples


def test_e2e_substrate_sanitizes_direct_csv_reserved_column(tmp_path: Path) -> None:
    """A direct CSV (not JSON) with a reserved `subject` column materializes once
    substrate renames the header to `subject_` — without the guard the function
    input would read the generated IRI and yield 0 triples."""
    if not _morph_kgc_installed():
        pytest.skip("morph-kgc not installed; this exercises the real materialize")
    (tmp_path / "ol.csv").write_text(
        'id,subject\nb1,"[""Math"", ""Physics""]"\n', encoding="utf-8"
    )
    # the mapping references the sanitized selector `subject_` (what inspect/propose emit)
    rml = _PREFIXES + """
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "ol.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/b/{id}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:subject ; rr:objectMap [
    rmlf:functionExecution [ rmlf:function fn:json_array ;
      rmlf:input [ rmlf:parameter fn:p_value ;
        rmlf:inputValueMap [ rml:reference "subject_" ] ] ] ] ] .
"""
    triples = _materialize(rml, tmp_path)
    assert ("https://ex/b/b1", "https://ex/subject", "Math") in triples
    assert ("https://ex/b/b1", "https://ex/subject", "Physics") in triples
