"""Design validation for declarative RML (asterism.rml_validate.validate_rml_design).

These cover the three malformed-design classes that otherwise surface only as a
cryptic Morph-KGC crash: a column reference to a column the CSV does not have, an
FnO function execution with a wrong / missing parameter IRI, and an `rml:source`
naming a file the data dir does not have. The validator only parses RML + reads CSV
headers / dir listings, so these run WITHOUT the Morph-KGC engine.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from asterism.dialect import SourceDialect
from asterism.rml_validate import (
    RmlValidationError,
    read_csv_header,
    validate_rml_design,
)

_PREFIXES = (
    "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql: <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix rmlf: <http://w3id.org/rml/> .\n"
    "@prefix fn: <https://kumagallium.github.io/asterism/fn/> .\n"
)


def _write_csv(dir_: Path, name: str, header: str, *, bom: bool = False) -> None:
    text = header + "\n1,x,y\n"
    data = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
    (dir_ / name).write_bytes(data)


# ---- column-reference check -------------------------------------------------


def test_missing_column_reference_is_flagged_with_suggestion(tmp_path: Path) -> None:
    # The CSV column is `project_names`; the RML references `project_slug`.
    _write_csv(tmp_path, "papers.csv", "SID,project_names,title")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/p> ;\n'
        '    rr:objectMap [ rml:reference "project_slug" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    issues = exc.value.issues
    assert any("project_slug" in m and "papers.csv" in m for m in issues)
    # The "did you mean" suggestion points at the real, similar column.
    assert any("project_names" in m for m in issues)


def test_missing_template_column_is_flagged(tmp_path: Path) -> None:
    # A {column} placeholder in a template names a column the CSV does not have.
    _write_csv(tmp_path, "papers.csv", "SID,title")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{paper_uid}" ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("paper_uid" in m for m in exc.value.issues)


def test_column_present_in_other_source_does_not_mask_a_typo(tmp_path: Path) -> None:
    # `extra` exists in samples.csv but NOT in papers.csv; the papers map referencing
    # it must still be flagged (per-source check, not a global column pool).
    _write_csv(tmp_path, "papers.csv", "SID,title")
    _write_csv(tmp_path, "samples.csv", "SID,extra")
    rml = _PREFIXES + (
        '<#P> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/e> ;\n'
        '    rr:objectMap [ rml:reference "extra" ] ] .\n'
        '<#S> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("extra" in m and "papers.csv" in m for m in exc.value.issues)


def test_bom_header_does_not_false_flag_first_column(tmp_path: Path) -> None:
    # A UTF-8 BOM must not make the first column read as `﻿SID`; the reference
    # to `SID` is valid and must NOT be flagged.
    _write_csv(tmp_path, "papers.csv", "SID,title", bom=True)
    assert read_csv_header(tmp_path / "papers.csv")[0] == "SID"
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "title" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


# ---- function-parameter check -----------------------------------------------


def test_wrong_function_parameter_is_flagged(tmp_path: Path) -> None:
    # json_pluck's field param is `p_field`; the RML supplies `p_field1`. Both the
    # unaccepted-param and the missing-required-param issues must be collected.
    _write_csv(tmp_path, "papers.csv", "SID,blob")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/p> ; rr:objectMap [\n'
        '    rmlf:functionExecution [ rmlf:function fn:json_pluck ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_value ;\n'
        '        rmlf:inputValueMap [ rml:reference "blob" ] ] ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_field1 ;\n'
        '        rmlf:inputValueMap [ rmlf:constant "name" ] ] ] ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    issues = exc.value.issues
    assert any("json_pluck" in m and "p_field1" in m and "accept" in m for m in issues)
    assert any("json_pluck" in m and "missing required parameter 'p_field'" in m for m in issues)


def test_missing_required_function_parameter_is_flagged(tmp_path: Path) -> None:
    # json_pluck supplies only p_value; p_field is required and absent.
    _write_csv(tmp_path, "papers.csv", "SID,blob")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/p> ; rr:objectMap [\n'
        '    rmlf:functionExecution [ rmlf:function fn:json_pluck ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_value ;\n'
        '        rmlf:inputValueMap [ rml:reference "blob" ] ] ] ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("missing required parameter 'p_field'" in m for m in exc.value.issues)


def test_optional_function_parameter_omission_is_ok(tmp_path: Path) -> None:
    # `template` requires only `p_template`; field1..field4 are optional, so a call
    # that supplies just the template (a constant) must NOT be flagged.
    _write_csv(tmp_path, "papers.csv", "SID,title")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/p> ; rr:objectMap [\n'
        '    rmlf:functionExecution [ rmlf:function fn:template ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_template ;\n'
        '        rmlf:inputValueMap [ rmlf:constant "lit" ] ] ] ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


# ---- valid mapping ----------------------------------------------------------


def test_valid_mapping_passes(tmp_path: Path) -> None:
    # Every reference and every function parameter is correct; no issue is raised.
    _write_csv(tmp_path, "papers.csv", "SID,title,issued,blob")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "title" ] ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/d> ; rr:objectMap [\n'
        '    rmlf:functionExecution [ rmlf:function fn:date_iso ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_value ;\n'
        '        rmlf:inputValueMap [ rml:reference "issued" ] ] ] ] ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/f> ; rr:objectMap [\n'
        '    rmlf:functionExecution [ rmlf:function fn:json_pluck ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_value ;\n'
        '        rmlf:inputValueMap [ rml:reference "blob" ] ] ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_field ;\n'
        '        rmlf:inputValueMap [ rmlf:constant "name" ] ] ] ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_run_id_placeholder_already_substituted_is_not_flagged(tmp_path: Path) -> None:
    # `__run_id__` is substituted away by substitute_run_id BEFORE validation runs,
    # so the post-substitution template carries no `{__run_id__}` reference and the
    # value is a constant IRI — nothing to flag. (We validate the substituted form.)
    _write_csv(tmp_path, "papers.csv", "SID,title")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/a> ;\n'
        '    rr:objectMap [ rr:constant <http://x/activity/ingest/run-20260626> ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


# ---- source-file check ------------------------------------------------------


def test_missing_source_file_is_flagged_with_suggestion(tmp_path: Path) -> None:
    # The real file is `papers.csv`; the RML invents `papers_preprocessed.csv`. The
    # source check flags it (so it never reaches Morph-KGC as a FileNotFoundError),
    # with a "did you mean" pointing at the real file.
    _write_csv(tmp_path, "papers.csv", "SID,title")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers_preprocessed.csv" ; '
        'rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "title" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    issues = exc.value.issues
    assert any("papers_preprocessed.csv" in m and "does not exist" in m for m in issues)
    assert any("papers.csv" in m for m in issues)  # did-you-mean / available list
    # The absent source has no header, so the column reference is NOT also flagged
    # (we report the single root cause — the missing file — not a phantom column).
    assert not any("title" in m and "is not in" in m for m in issues)


def test_missing_source_lists_available_files_when_no_close_match(tmp_path: Path) -> None:
    # When no real filename is similar, the available files are listed so the AI can
    # pick the right one rather than guessing again.
    _write_csv(tmp_path, "measurements.csv", "SID,value")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "totally_different.csv" ; '
        'rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("Available files: measurements.csv" in m for m in exc.value.issues)


def test_present_but_empty_source_is_not_flagged(tmp_path: Path) -> None:
    # A source file that exists but has no header row is present (not missing), so the
    # source check passes; the column check cannot read a header and skips it — no
    # missing-source and no phantom-column issue.
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "empty.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "whatever" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_absolute_source_path_that_exists_is_not_flagged(tmp_path: Path) -> None:
    # The substrate rewrites sources to absolute paths before validation; an absolute
    # path to a real file (e.g. a work-dir copy) must pass the source check.
    _write_csv(tmp_path, "papers.csv", "SID,title")
    abspath = str((tmp_path / "papers.csv").resolve())
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        f'  rml:logicalSource [ rml:source "{abspath}" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "title" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_json_source_references_are_not_checked_as_columns(tmp_path: Path) -> None:
    # A native JSON source (ql:JSONPath + iterator) references JSONPath FIELDS, not
    # CSV columns; there is no flat header, so `{mp_id}` must NOT be flagged even
    # though the .json file's first line is not a CSV header.
    (tmp_path / "mp.json").write_text('[{"mp_id":"mp-1"}]', encoding="utf-8")
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "mp.json" ; rml:referenceFormulation '
        'rml:JSONPath ; rml:iterator "$[*]" ] ;\n'
        '  rr:subjectMap [ rr:template "https://ex/mat/{mp_id}" ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_tsv_header_is_read_tab_delimited(tmp_path: Path) -> None:
    # A .tsv source's header is tab-delimited; a reference to a real tab column is OK,
    # and a typo is flagged (proving the header parsed as columns, not one big cell).
    (tmp_path / "d.tsv").write_text("SID\ttitle\n1\tx\n", encoding="utf-8")
    assert read_csv_header(tmp_path / "d.tsv") == ["SID", "title"]
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "d.tsv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "titlee" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("titlee" in m for m in exc.value.issues)


def test_unparseable_rml_is_left_to_the_safety_gate(tmp_path: Path) -> None:
    # rml_safety owns the parse-error rejection; here we just return without raising
    # a design error (so we never produce a confusing second message for bad Turtle).
    validate_rml_design("this is not turtle {{{", tmp_path)  # no raise


# ---- source-dialect header reads (ADR source-dialect.md) ---------------------


def _write_cp932_xrd(dir_: Path) -> Path:
    # The audited legacy shape: CP932, CRLF, tab-separated, one preamble line.
    src = dir_ / "xrd_measurement.txt"
    lines = ["サンプル名: 試料A", "angle\tsample", "10.5\t試料A"]
    src.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")
    return src


def test_read_csv_header_with_dialect(tmp_path: Path) -> None:
    # A pinned dialect reads the header through the SAME rules normalization uses
    # (encoding + skip_rows + delimiter), so the columns match what Morph-KGC sees.
    src = _write_cp932_xrd(tmp_path)
    dialect = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    assert read_csv_header(src, dialect) == ["angle", "sample"]
    # A default dialect keeps today's plain utf-8-sig read (the all-defaults
    # gate) — which cannot even decode this file: the pre-dialect barrier.
    with pytest.raises(UnicodeDecodeError):
        read_csv_header(src, SourceDialect())


def test_read_csv_header_with_dialect_undecodable_returns_empty(tmp_path: Path) -> None:
    # "cannot check" (skip), never a crash — the ingest boundary raises the loud error.
    src = tmp_path / "d.txt"
    src.write_bytes(b"a\tb\n\xff\xff\t1\n")
    assert read_csv_header(src, SourceDialect(encoding="ascii", delimiter="\t")) == []


_DIALECT_LS = (
    '  rml:logicalSource [ rml:source "xrd_measurement.txt" ;\n'
    "    rml:referenceFormulation ql:CSV ;\n"
    '    ast:sourceEncoding "cp932" ;\n'
    '    ast:sourceDelimiter "\\t" ;\n'
    "    ast:sourceSkipRows 1 ] ;\n"
)
_AST_PREFIX = "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"


def test_dialected_source_columns_pass_when_real(tmp_path: Path) -> None:
    # Un-prepared RML with dialect annotations: the column check reads the .txt
    # source through its pinned dialect, so real columns are NOT flagged.
    _write_cp932_xrd(tmp_path)
    rml = _PREFIXES + _AST_PREFIX + (
        "<#M> a rr:TriplesMap ;\n"
        + _DIALECT_LS
        + '  rr:subjectMap [ rr:template "http://x/{angle}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/s> ;\n'
        '    rr:objectMap [ rml:reference "sample" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_dialected_source_column_typo_is_flagged(tmp_path: Path) -> None:
    # ... and a typo IS flagged (proving the dialected .txt source is checked as a
    # tabular source instead of skipped for its extension).
    _write_cp932_xrd(tmp_path)
    rml = _PREFIXES + _AST_PREFIX + (
        "<#M> a rr:TriplesMap ;\n"
        + _DIALECT_LS
        + '  rr:subjectMap [ rr:template "http://x/{angle}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/s> ;\n'
        '    rr:objectMap [ rml:reference "samplee" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("samplee" in m and "xrd_measurement.txt" in m for m in exc.value.issues)
    assert any("sample" in m for m in exc.value.issues)  # did-you-mean hits the real column


# ---------------------------------------------------------------------------
# design advisories: entity connectivity (schema-agnostic graph shape)
# ---------------------------------------------------------------------------

from asterism.rml_validate import design_advisories  # noqa: E402

_ADV_PREFIXES = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ex: <https://ex/v#> .
"""

# Two entities, NO join between them — the live failure shape (a measurement
# entity with no edge to its material entity).
_DISCONNECTED = _ADV_PREFIXES + """
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class ex:MeasurementCurve ] ;
  rr:predicateObjectMap [ rr:predicate ex:propertyY ; rr:objectMap [ rml:reference "p" ] ] .
<#Samples> rml:logicalSource [ rml:source "samples.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{sid}" ; rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:composition ; rr:objectMap [ rml:reference "c" ] ] .
"""

_JOINED_BY_PARENT = _ADV_PREFIXES + """
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class ex:MeasurementCurve ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rr:parentTriplesMap <#Samples> ] ] .
<#Samples> rml:logicalSource [ rml:source "samples.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{sid}" ; rr:class ex:Sample ] .
"""

_JOINED_BY_TEMPLATE = _ADV_PREFIXES + """
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class ex:MeasurementCurve ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rr:template "https://ex/sample/{sid}" ] ] .
<#Samples> rml:logicalSource [ rml:source "samples.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{sid}" ; rr:class ex:Sample ] .
"""


def test_disconnected_entities_are_flagged_with_labels() -> None:
    advisories = design_advisories(_DISCONNECTED)
    assert len(advisories) == 1
    msg = advisories[0]
    assert "DISCONNECTED" in msg
    assert "MeasurementCurve" in msg and "Sample" in msg  # class labels, actionable
    assert "object property" in msg  # says HOW to fix


def test_parent_triples_map_join_connects() -> None:
    assert design_advisories(_JOINED_BY_PARENT) == []


def test_shared_subject_template_as_object_connects() -> None:
    assert design_advisories(_JOINED_BY_TEMPLATE) == []


def test_single_entity_never_flagged() -> None:
    single = _ADV_PREFIXES + """
<#Only> rml:logicalSource [ rml:source "a.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/x/{id}" ; rr:class ex:Thing ] .
"""
    assert design_advisories(single) == []


def test_unparseable_rml_degrades_to_no_advisories() -> None:
    assert design_advisories("@prefix broken") == []


# ---------------------------------------------------------------------------
# cross-source link-direction hint (a link declared on the wrong side)
# ---------------------------------------------------------------------------


def test_wrong_side_link_gets_cross_source_direction_hint(tmp_path: Path) -> None:
    # The exact live failure: the PAPER map references the child's key
    # (sample_id lives in samples.csv, not papers.csv). The column error must
    # now carry the directional fix — declare the link on the child's map.
    (tmp_path / "papers.csv").write_text("DOI,title\nx,y\n", encoding="utf-8")
    (tmp_path / "samples.csv").write_text("sample_id,DOI\n1,x\n", encoding="utf-8")
    rml = _PREFIXES + (
        '<#Papers> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/paper/{DOI}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/hasSample> ;\n'
        '    rr:objectMap [ rr:template "http://x/sample/{sample_id}" ] ] .\n'
        '<#Samples> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/sample/{sample_id}" ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    msg = "\n".join(exc.value.issues)
    assert "'sample_id'" in msg and "papers.csv" in msg
    assert "DOES exist in samples.csv" in msg  # names the carrying source
    assert "declare it on the TriplesMap whose source carries the key" in msg


def test_plain_typo_gets_no_cross_source_note(tmp_path: Path) -> None:
    # A column that exists NOWHERE stays a plain did-you-mean — no misleading
    # link-direction advice.
    (tmp_path / "papers.csv").write_text("DOI,title\nx,y\n", encoding="utf-8")
    rml = _PREFIXES + (
        '<#Papers> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/paper/{DOI}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "titel" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    msg = "\n".join(exc.value.issues)
    assert "titel" in msg and "Did you mean" in msg
    assert "DOES exist in" not in msg


def test_disconnected_advisory_names_join_key_candidates(tmp_path: Path) -> None:
    # The live oscillation: "link them" alone lets the model delete references
    # instead. With the real headers the advisory must enumerate the concrete
    # join keys and the side that declares the link.
    (tmp_path / "papers.csv").write_text("SID,DOI,title\n1,x,t\n", encoding="utf-8")
    (tmp_path / "samples.csv").write_text("sample_id,SID,DOI\n7,1,x\n", encoding="utf-8")
    (tmp_path / "curves.csv").write_text("sample_id,figure_id,y\n7,f1,0.1\n", encoding="utf-8")
    rml = _ADV_PREFIXES + """
<#Papers> rml:logicalSource [ rml:source "papers.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/paper/{DOI}" ; rr:class ex:Paper ] .
<#Samples> rml:logicalSource [ rml:source "samples.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{sample_id}" ; rr:class ex:MaterialSample ] .
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{sample_id}-{figure_id}" ;
    rr:class ex:MeasurementCurve ] .
"""
    advisories = design_advisories(rml, tmp_path)
    conn = [a for a in advisories if "DISCONNECTED" in a]
    assert len(conn) == 1
    msg = conn[0]
    assert "LINK-KEY CANDIDATES" in msg
    assert "papers.csv <-> samples.csv share column(s): DOI, SID" in msg
    assert "curves.csv <-> samples.csv share column(s): sample_id" in msg
    assert "CHILD map" in msg and "VERBATIM" in msg
    assert "Do NOT fix this by deleting references" in msg


def test_disconnected_advisory_without_csv_dir_keeps_generic_text(tmp_path: Path) -> None:
    # Backward compatible: no csv_dir -> diagnosis + direction, no candidates.
    advisories = design_advisories(_DISCONNECTED)
    assert len(advisories) == 1
    assert "LINK-KEY CANDIDATES" not in advisories[0]
    assert "DISCONNECTED" in advisories[0]


# ---------------------------------------------------------------------------
# rr:constant containing a {placeholder} (crashes Morph-KGC at ingest)
# ---------------------------------------------------------------------------


def test_constant_with_invented_placeholder_is_flagged(tmp_path: Path) -> None:
    # The live failure: prov:wasGeneratedBy got rr:constant
    # "sdr:activity/{ingest_run_id}" — never substituted, Morph-KGC treats it as
    # a template and dies with pandas KeyError: 'ingest_run_id'. The gate must
    # reject it BEFORE ingest with the fix in the message.
    (tmp_path / "papers.csv").write_text("DOI\nx\n", encoding="utf-8")
    rml = _PREFIXES + (
        '<#Papers> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/paper/{DOI}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://www.w3.org/ns/prov#wasGeneratedBy> ;\n'
        '    rr:objectMap [ rr:constant "sdr:activity/{ingest_run_id}" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    msg = "\n".join(exc.value.issues)
    assert "'{ingest_run_id}'" in msg
    assert "never template-expanded" in msg
    assert "'{__run_id__}'" in msg  # names the one legal runtime placeholder


def test_constant_run_id_placeholder_is_allowed(tmp_path: Path) -> None:
    # {__run_id__} inside a constant IS substituted (substitute_run_id resolves
    # the token everywhere since the fix/run-id-substitute-everywhere change) —
    # the raw form must not be flagged.
    (tmp_path / "papers.csv").write_text("DOI\nx\n", encoding="utf-8")
    rml = _PREFIXES + (
        '<#Papers> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/paper/{DOI}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/run> ;\n'
        '    rr:objectMap [ rr:constant "http://x/ingest/{__run_id__}" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_plain_constants_never_flagged(tmp_path: Path) -> None:
    (tmp_path / "papers.csv").write_text("DOI\nx\n", encoding="utf-8")
    rml = _PREFIXES + (
        '<#Papers> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/paper/{DOI}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/kind> ;\n'
        '    rr:objectMap [ rr:constant "thermoelectric" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


def test_unmapped_label_column_gets_advisory(tmp_path: Path) -> None:
    # The live failure shape: prop_x is mapped, prop_y (the label column that
    # says WHAT each curve measures) is not — the data ingests fine but "which
    # rows measure X" becomes unanswerable.
    (tmp_path / "curves.csv").write_text(
        "id,prop_x,prop_y,y\n1,temperature,zt,0.5\n", encoding="utf-8"
    )
    rml = _ADV_PREFIXES + """
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class ex:Curve ] ;
  rr:predicateObjectMap [ rr:predicate ex:xProp ; rr:objectMap [ rml:reference "prop_x" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:value ; rr:objectMap [ rml:reference "y" ] ] .
"""
    from asterism.rml_validate import design_review_notes

    unmapped = [a for a in design_review_notes(rml, tmp_path) if "never uses" in a]
    assert len(unmapped) == 1
    assert "prop_y" in unmapped[0]
    assert "unqueryable" in unmapped[0]
    assert "§5" in unmapped[0]  # deliberate exclusions have a documented out


def test_fully_mapped_source_gets_no_unmapped_advisory(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,v\n1,2\n", encoding="utf-8")
    rml = _ADV_PREFIXES + """
<#A> rml:logicalSource [ rml:source "a.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/a/{id}" ; rr:class ex:Thing ] ;
  rr:predicateObjectMap [ rr:predicate ex:v ; rr:objectMap [ rml:reference "v" ] ] .
"""
    from asterism.rml_validate import design_review_notes

    assert [a for a in design_review_notes(rml, tmp_path) if "never uses" in a] == []
    # and the loop-facing advisories never carry unmapped-column notes at all
    assert [a for a in design_advisories(rml, tmp_path) if "never uses" in a] == []
