"""Design validation for declarative RML (asterism.rml_validate.validate_rml_design).

These cover the two malformed-design classes that otherwise surface only as a
cryptic Morph-KGC crash: a column reference to a column the CSV does not have, and
an FnO function execution with a wrong / missing parameter IRI. The validator only
parses RML + reads CSV headers, so these run WITHOUT the Morph-KGC engine.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_unreadable_source_is_skipped_not_flagged(tmp_path: Path) -> None:
    # When the CSV is absent (no header to check) we cannot verify columns; we must
    # NOT invent a missing-column issue (the safety/containment gate owns that).
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "absent.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "whatever" ] ] .\n'
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
