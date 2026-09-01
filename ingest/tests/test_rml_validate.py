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
    duplicate_column_findings,
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


# ---- lookup seed-table check -------------------------------------------------


def _lookup_rml(table: str) -> str:
    return _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "d.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/p> ; rr:objectMap [\n'
        '    rmlf:functionExecution [ rmlf:function fn:lookup ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_value ;\n'
        '        rmlf:inputValueMap [ rml:reference "flag" ] ] ;\n'
        '      rmlf:input [ rmlf:parameter fn:p_table ;\n'
        f'        rmlf:inputValueMap [ rmlf:constant "{table}" ] ] ] ] ] .\n'
    )


def test_unknown_lookup_table_is_flagged_with_suggestion(tmp_path: Path) -> None:
    """A table name is a CONSTANT, so a typo costs every row its value.

    Before this check the mapping validated, Morph-KGC ran, and the column simply
    was not in the output — a successful run that quietly dropped the data.
    """
    _write_csv(tmp_path, "d.csv", "SID,flag")
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(_lookup_rml("booleans"), tmp_path)
    issues = exc.value.issues
    assert any("booleans" in m and "Did you mean: bool" in m for m in issues)


def test_unknown_lookup_table_message_lists_tables_when_no_close_match(
    tmp_path: Path,
) -> None:
    _write_csv(tmp_path, "d.csv", "SID,flag")
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(_lookup_rml("zzzzzz"), tmp_path)
    assert any("Available tables:" in m and "unit_alias" in m for m in exc.value.issues)


def test_shipped_lookup_table_passes(tmp_path: Path) -> None:
    _write_csv(tmp_path, "d.csv", "SID,flag")
    validate_rml_design(_lookup_rml("bool"), tmp_path)  # no raise


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


def test_csv_reference_backed_by_sibling_json_is_not_flagged(tmp_path: Path) -> None:
    """A ``.csv`` source backed only by a sibling ``.json`` is legal: ingest
    tabularizes it on the fly (``substrate.tabularize_json_sources``). Flagging
    it made the design-time check contradict the compiler's own demand for the
    tabularized name, so a JSON design's repair loop could never converge
    (live 2026-09-01, periodic-table JSON)."""
    (tmp_path / "elements.json").write_text(
        '[{"name": "H", "atomic_mass": 1.008}]', encoding="utf-8"
    )
    rml = _PREFIXES + (
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "elements.csv" ; '
        'rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{name}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <http://x/n> ;\n'
        '    rr:objectMap [ rml:reference "atomic_mass" ] ] .\n'
    )
    validate_rml_design(rml, tmp_path)  # no raise


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
    # A default dialect on a legacy suffix reads under the DEFAULT rules
    # (extension-based normalization) — undecodable ⇒ "cannot check", not a crash.
    assert read_csv_header(src, SourceDialect()) == []


def test_read_csv_header_legacy_txt_default_read(tmp_path: Path) -> None:
    # C13: a clean comma .txt reads its header with NO dialect argument at all —
    # the extension routes it through the default dialect rules (strip + reserved
    # rename), matching the normalized copy Morph-KGC gets.
    src = tmp_path / "clean.txt"
    src.write_text("angle , subject \n1,a\n", encoding="utf-8")
    assert read_csv_header(src) == ["angle", "subject_"]


def test_read_csv_header_dialect_renames_reserved_columns(tmp_path: Path) -> None:
    # C6: the header check must see the SAME names the normalized copy carries.
    src = tmp_path / "d.txt"
    src.write_text("subject\tpredicate\tvalue\na\tb\t1\n", encoding="utf-8")
    dialect = SourceDialect(delimiter="\t")
    assert read_csv_header(src, dialect) == ["subject_", "predicate_", "value"]


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


def test_default_txt_source_column_typo_is_flagged(tmp_path: Path) -> None:
    # C13: a clean .txt source with NO annotations is still column-checked
    # (extension-based tabular gate — the same file gets normalized at ingest).
    (tmp_path / "clean.txt").write_text("angle,sample\n1,a\n", encoding="utf-8")
    rml = _PREFIXES + (
        "<#M> a rr:TriplesMap ;\n"
        '  rml:logicalSource [ rml:source "clean.txt" ;\n'
        "    rml:referenceFormulation ql:CSV ] ;\n"
        '  rr:subjectMap [ rr:template "http://x/{angle}" ] ;\n'
        "  rr:predicateObjectMap [ rr:predicate <http://x/s> ;\n"
        '    rr:objectMap [ rml:reference "samplee" ] ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("samplee" in m and "clean.txt" in m for m in exc.value.issues)


def test_bad_dialect_annotation_is_a_design_issue(tmp_path: Path) -> None:
    # G-D: the raw-RML save path runs design validation, so an out-of-contract
    # annotation value becomes a readable 422 issue (never a 500 at ingest).
    _write_cp932_xrd(tmp_path)
    rml = _PREFIXES + _AST_PREFIX + (
        "<#M> a rr:TriplesMap ;\n"
        + _DIALECT_LS.replace('"cp932"', '"base64"')
        + '  rr:subjectMap [ rr:template "http://x/{angle}" ] .\n'
    )
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(rml, tmp_path)
    assert any("text codec" in m and "base64" in m for m in exc.value.issues)


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

# The IR compiler's shape for a TRANSFORMED term map: fn:template with a
# p_template pattern constant (`…/{1}`) and numbered p_fieldN inputs whose value
# maps carry the (transform-nested) source column. Faithful reduction of the
# live ZEM x gpt-oss mapping whose links were present but transformed — the
# connectivity check saw no rr:template on either side and looped six AI-repair
# rounds on a false DISCONNECTED.
_FNO_PREFIXES = _ADV_PREFIXES + """
@prefix rmlf: <http://w3id.org/rml/> .
@prefix fn: <https://ex.example/fn/> .
"""

_FNO_SUBJECT = """
  rr:subjectMap [
    rmlf:functionExecution [
      rmlf:function fn:template ;
      rmlf:input [ rmlf:parameter <https://ex.example/fn/p_field1> ;
                   rmlf:inputValueMap [ rmlf:functionExecution [
              rmlf:function fn:url_canonical ;
              rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                           rmlf:inputValueMap [ rml:reference "sid" ] ] ] ] ] ;
      rmlf:input [ rmlf:parameter <https://ex.example/fn/p_template> ;
                   rmlf:inputValueMap [ rmlf:constant "https://ex/sample/{1}" ] ] ] ;
    rr:termType rr:IRI ; rr:class ex:Sample ]"""

_JOINED_BY_TRANSFORMED_TEMPLATES = _FNO_PREFIXES + """
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class ex:MeasurementCurve ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rmlf:functionExecution [
          rmlf:function fn:template ;
          rmlf:input [ rmlf:parameter <https://ex.example/fn/p_field1> ;
                       rmlf:inputValueMap [ rmlf:functionExecution [
                  rmlf:function fn:url_canonical ;
                  rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                               rmlf:inputValueMap [ rml:reference "sid" ] ] ] ] ] ;
          rmlf:input [ rmlf:parameter <https://ex.example/fn/p_template> ;
                       rmlf:inputValueMap [ rmlf:constant "https://ex/sample/{1}" ] ] ] ;
      rr:termType rr:IRI ] ] .
<#Samples> rml:logicalSource [ rml:source "samples.csv" ] ;""" + _FNO_SUBJECT + " ."

_TRANSFORMED_BUT_DISCONNECTED = _FNO_PREFIXES + """
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class ex:MeasurementCurve ] ;
  rr:predicateObjectMap [ rr:predicate ex:propertyY ; rr:objectMap [ rml:reference "p" ] ] .
<#Samples> rml:logicalSource [ rml:source "samples.csv" ] ;""" + _FNO_SUBJECT + " ."


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


def test_transformed_templates_connect() -> None:
    # The live ZEM x gpt-oss regression: subject AND link compiled to fn:template
    # (a transform wraps the column) — the effective templates match, so the
    # mapping is CONNECTED. Before the effective-template recovery this looped
    # forever as a false DISCONNECTED (the AI had added the link; the check
    # could not see it).
    assert design_advisories(_JOINED_BY_TRANSFORMED_TEMPLATES) == []


def test_transformed_subject_still_flagged_when_truly_disconnected() -> None:
    # Recovery must not blind the check: a transformed subject with NO link on
    # either side is still a real disconnect.
    advisories = design_advisories(_TRANSFORMED_BUT_DISCONNECTED)
    assert len(advisories) == 1 and "DISCONNECTED" in advisories[0]


def test_single_entity_never_flagged() -> None:
    single = _ADV_PREFIXES + """
<#Only> rml:logicalSource [ rml:source "a.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/x/{id}" ; rr:class ex:Thing ] .
"""
    assert design_advisories(single) == []


# ---------------------------------------------------------------------------
# design advisories: duplicate column assignment (one fact, one entity)
# ---------------------------------------------------------------------------

# The live ZEM shape (2026-07-23): a per-row readings map AND a constant-subject
# sample map over the SAME source, the per-map stage transcribing the same
# columns onto both. Joined via parentTriplesMap so the connectivity advisory
# stays silent and these fixtures isolate the duplicate-column concern.
_DUP_COLUMNS = _ADV_PREFIXES + """
<#Readings> rml:logicalSource [ rml:source "run.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/reading/{temp}" ; rr:class ex:Reading ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rr:parentTriplesMap <#Sample> ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:resistivity ;
    rr:objectMap [ rml:reference "resistivity" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:diameter ;
    rr:objectMap [ rml:reference "diameter" ] ] .
<#Sample> rml:logicalSource [ rml:source "run.csv" ] ;
  rr:subjectMap [ rr:constant <https://ex/sample/s1> ; rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:resistivityDup ;
    rr:objectMap [ rml:reference "resistivity" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:diameterDup ;
    rr:objectMap [ rml:reference "diameter" ] ] .
"""


def test_duplicate_column_flagged_and_adjudicated(tmp_path) -> None:
    # resistivity varies per row -> the per-row Reading owns it; diameter is
    # constant across the run -> the single Sample owns it (normalization).
    (tmp_path / "run.csv").write_text(
        "temp,resistivity,diameter\n300,1.0,5.0\n310,1.1,5.0\n320,1.2,5.0\n",
        encoding="utf-8",
    )
    # This fixture's columns are also numeric-and-untyped, so filter to the
    # duplicate-column verdicts this test is about (that advisory has its own).
    advisories = [
        a for a in design_advisories(_DUP_COLUMNS, tmp_path) if "plain datatype property by" in a
    ]
    assert len(advisories) == 2  # one per duplicated column, ordered by name
    dia, res = advisories
    assert dia.startswith("column 'diameter'") and "Reading + Sample" in dia
    assert "keep it ONLY on 'Sample' and DELETE it from: Reading" in dia
    assert res.startswith("column 'resistivity'")
    assert "keep it ONLY on 'Reading' and DELETE it from: Sample" in res
    # the verdicts carry their evidence (rows scanned, distinct values, subjects)
    assert "3 data rows" in res and "3 distinct" in res
    assert "1 distinct" in dia and "1 subject(s)" in dia


def test_duplicate_column_without_rows_states_defect_only() -> None:
    # No csv_dir -> the defect is still flagged, but no ownership claim is made
    # (no claim is better than a wrong one).
    advisories = design_advisories(_DUP_COLUMNS)
    assert len(advisories) == 2
    for msg in advisories:
        assert "duplicated fact" in msg
        assert "Adjudicated" not in msg


def test_duplicate_column_findings_carry_the_choice_a_human_must_make(tmp_path) -> None:
    # The advisory says WHAT is wrong in English, for a model. The finding says
    # WHO the candidates are, in a shape a person's tier can render: the column,
    # both maps under their handles, how many entities each mints, and the
    # verdict the rows recommend (ADR column-ownership G1 / kantan K2).
    (tmp_path / "run.csv").write_text(
        "temp,resistivity,diameter\n300,1.0,5.0\n310,1.1,5.0\n320,1.2,5.0\n",
        encoding="utf-8",
    )
    findings = duplicate_column_findings(_DUP_COLUMNS, tmp_path)
    assert [f["column"] for f in findings] == ["diameter", "resistivity"]
    dia, res = findings
    assert dia["source"] == "run.csv"
    assert dia["actionable"] is True
    assert [(m["map"], m["label"], m["entities"]) for m in dia["maps"]] == [
        ("Readings", "Reading", 3),
        ("Sample", "Sample", 1),
    ]
    # constant across the run -> the single Sample owns it; per row -> Reading
    assert dia["owner"] == "Sample"
    assert res["owner"] == "Readings"
    # one implementation: the finding carries the very sentence the model reads
    assert dia["text"] in design_advisories(_DUP_COLUMNS, tmp_path)


def test_duplicate_column_findings_without_rows_offer_no_owner() -> None:
    # No csv_dir -> the candidates are still named (the choice is a human's
    # anyway), but the machine makes no recommendation.
    findings = duplicate_column_findings(_DUP_COLUMNS)
    assert len(findings) == 2
    for f in findings:
        assert f["owner"] is None
        assert [m["entities"] for m in f["maps"]] == [None, None]


def test_duplicate_column_findings_on_an_anonymous_map_are_not_actionable(tmp_path) -> None:
    # A blank-node TriplesMap (legacy hand-written RML) has no handle to send
    # back, so the choice must not be offered under a name nothing can act on.
    rml = _ADV_PREFIXES + """
[] rml:logicalSource [ rml:source "run.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/reading/{temp}" ; rr:class ex:Reading ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rr:parentTriplesMap <#Sample> ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:diameter ;
    rr:objectMap [ rml:reference "diameter" ] ] .
<#Sample> rml:logicalSource [ rml:source "run.csv" ] ;
  rr:subjectMap [ rr:constant <https://ex/sample/s1> ; rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:diameterDup ;
    rr:objectMap [ rml:reference "diameter" ] ] .
"""
    (tmp_path / "run.csv").write_text(
        "temp,diameter\n300,5.0\n310,5.0\n", encoding="utf-8"
    )
    findings = duplicate_column_findings(rml, tmp_path)
    assert len(findings) == 1
    assert findings[0]["actionable"] is False


def test_key_carry_columns_exempt(tmp_path) -> None:
    # A map carrying ANOTHER map's subject-key column as a literal is how joins
    # are declared/kept queryable — the connectivity advisory owns that concern.
    rml = _ADV_PREFIXES + """
<#Child> rml:logicalSource [ rml:source "c.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/c/{cid}" ; rr:class ex:Child ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofParent ;
    rr:objectMap [ rr:template "https://ex/p/{sid}" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:sid ; rr:objectMap [ rml:reference "sid" ] ] .
<#Parent> rml:logicalSource [ rml:source "c.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/p/{sid}" ; rr:class ex:Parent ] ;
  rr:predicateObjectMap [ rr:predicate ex:sidLit ; rr:objectMap [ rml:reference "sid" ] ] .
"""
    (tmp_path / "c.csv").write_text("cid,sid\n1,a\n2,a\n", encoding="utf-8")
    assert design_advisories(rml, tmp_path) == []


def test_iri_valued_reference_not_a_duplicate_fact() -> None:
    # A reference object map typed rr:termType rr:IRI is a LINK (e.g. iri_safe
    # output), not a transcribed datatype fact — never flagged.
    rml = _ADV_PREFIXES + """
<#A> rml:logicalSource [ rml:source "u.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/a/{id}" ; rr:class ex:A ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofB ;
    rr:objectMap [ rr:parentTriplesMap <#B> ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:url ;
    rr:objectMap [ rml:reference "url" ; rr:termType rr:IRI ] ] .
<#B> rml:logicalSource [ rml:source "u.csv" ] ;
  rr:subjectMap [ rr:constant <https://ex/b/only> ; rr:class ex:B ] ;
  rr:predicateObjectMap [ rr:predicate ex:url ;
    rr:objectMap [ rml:reference "url" ; rr:termType rr:IRI ] ] .
"""
    assert design_advisories(rml) == []


def test_duplicate_column_through_single_arg_function_is_flagged(tmp_path) -> None:
    # The live XRD shape (2026-08-20): a per-preamble Sample map AND a per-row
    # SampleDetail map over the SAME source, BOTH transcribing "twotheta" and
    # "intensity" through a single-column function pipeline (number_clean).
    # rml:reference-only detection missed this entirely (0 advisories on the
    # real 3001-row file) because a function-wrapped column never showed up as
    # a "plain reference" — this is the regression test for that gap.
    rml = _FNO_PREFIXES + """
<#Sample> rml:logicalSource [ rml:source "xrd.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{preamble_1}" ; rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:twotheta ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:number_clean ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                     rmlf:inputValueMap [ rml:reference "twotheta" ] ] ] ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:intensity ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:number_clean ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                     rmlf:inputValueMap [ rml:reference "intensity" ] ] ] ] ] .
<#SampleDetail> rml:logicalSource [ rml:source "xrd.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample_detail/{preamble_1}/{row_id}" ;
    rr:class ex:SampleDetail ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rr:template "https://ex/sample/{preamble_1}" ; rr:termType rr:IRI ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:twotheta ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:number_clean ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                     rmlf:inputValueMap [ rml:reference "twotheta" ] ] ] ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:intensity ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:number_clean ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                     rmlf:inputValueMap [ rml:reference "intensity" ] ] ] ] ] .
"""
    (tmp_path / "xrd.csv").write_text(
        "preamble_1,row_id,twotheta,intensity\n"
        "p1,1,10.0,100\np1,2,20.0,200\np1,3,30.0,300\n",
        encoding="utf-8",
    )
    advisories = [
        a for a in design_advisories(rml, tmp_path) if "plain datatype property by" in a
    ]
    assert len(advisories) == 2  # twotheta + intensity, both function-wrapped
    labels = {a.split("'")[1] for a in advisories}
    assert labels == {"twotheta", "intensity"}
    for a in advisories:
        # twotheta/intensity vary per row -> preamble_1 alone cannot determine
        # them -> Sample (keyed only by preamble_1) cannot own it -> the
        # per-row SampleDetail (keyed by preamble_1+row_id) does.
        assert "keep it ONLY on 'SampleDetail' and DELETE it from: Sample" in a


def test_duplicate_column_flagged_when_one_side_is_a_direct_reference(tmp_path) -> None:
    # Same column, reached two different ways (plain reference vs. a function
    # pipeline) — still the SAME source cell duplicated onto two entities.
    rml = _FNO_PREFIXES + """
<#A> rml:logicalSource [ rml:source "d.csv" ] ;
  rr:subjectMap [ rr:constant <https://ex/a/only> ; rr:class ex:A ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofB ;
    rr:objectMap [ rr:parentTriplesMap <#B> ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:val ; rr:objectMap [ rml:reference "val" ] ] .
<#B> rml:logicalSource [ rml:source "d.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/b/{id}" ; rr:class ex:B ] ;
  rr:predicateObjectMap [ rr:predicate ex:val ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:number_clean ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                     rmlf:inputValueMap [ rml:reference "val" ] ] ] ] ] .
"""
    (tmp_path / "d.csv").write_text("id,val\n1,10\n2,20\n3,30\n", encoding="utf-8")
    advisories = [
        a for a in design_advisories(rml, tmp_path) if "plain datatype property by" in a
    ]
    assert len(advisories) == 1
    assert advisories[0].startswith("column 'val'")
    assert "A + B" in advisories[0]


def test_two_argument_function_is_not_a_transcription_of_either_column() -> None:
    # A function combining TWO columns produces a genuinely DERIVED value that
    # belongs to neither input alone — even if both maps run the same
    # combination, that is not a duplicated FACT and must stay silent.
    rml = _FNO_PREFIXES + """
<#A> rml:logicalSource [ rml:source "d.csv" ] ;
  rr:subjectMap [ rr:constant <https://ex/a/only> ; rr:class ex:A ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofB ;
    rr:objectMap [ rr:parentTriplesMap <#B> ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:combined ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:combine ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_field1> ;
                     rmlf:inputValueMap [ rml:reference "a" ] ] ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_field2> ;
                     rmlf:inputValueMap [ rml:reference "b" ] ] ] ] ] .
<#B> rml:logicalSource [ rml:source "d.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/b/{id}" ; rr:class ex:B ] ;
  rr:predicateObjectMap [ rr:predicate ex:combined ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:combine ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_field1> ;
                     rmlf:inputValueMap [ rml:reference "a" ] ] ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_field2> ;
                     rmlf:inputValueMap [ rml:reference "b" ] ] ] ] ] .
"""
    assert design_advisories(rml) == []


def test_key_carry_columns_exempt_through_function_too(tmp_path) -> None:
    # The subject-key exemption (join declaration) must still apply when the
    # carried key column is reached through a single-column function pipeline,
    # not only a direct rml:reference.
    rml = _FNO_PREFIXES + """
<#Child> rml:logicalSource [ rml:source "c.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/c/{cid}" ; rr:class ex:Child ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofParent ;
    rr:objectMap [ rr:template "https://ex/p/{sid}" ; rr:termType rr:IRI ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:sid ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:number_clean ;
        rmlf:input [ rmlf:parameter <https://ex.example/fn/p_value> ;
                     rmlf:inputValueMap [ rml:reference "sid" ] ] ] ] ] .
<#Parent> rml:logicalSource [ rml:source "c.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/p/{sid}" ; rr:class ex:Parent ] ;
  rr:predicateObjectMap [ rr:predicate ex:sidLit ; rr:objectMap [ rml:reference "sid" ] ] .
"""
    (tmp_path / "c.csv").write_text("cid,sid\n1,a\n2,a\n", encoding="utf-8")
    assert design_advisories(rml, tmp_path) == []


def test_unparseable_rml_degrades_to_no_advisories() -> None:
    assert design_advisories("@prefix broken") == []


# ---------------------------------------------------------------------------
# design advisories: empty shell (a per-row entity that carries no row value)
# ---------------------------------------------------------------------------

# The live XRD reference-card shape (2026-08-16): the skeleton gate confirmed a
# per-row map keyed {No}/{(hkl)}, then the per-map stage wrote ONE property on
# it — the link back to the sample — and stopped. 2theta / d / I went nowhere.
_EMPTY_SHELL = _ADV_PREFIXES + """
<#Sample> rml:logicalSource [ rml:source "card.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{No}" ; rr:class ex:Material ] ;
  rr:predicateObjectMap [ rr:predicate ex:name ; rr:objectMap [ rml:reference "Name" ] ] .
<#Peak> rml:logicalSource [ rml:source "card.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/peak/{No}/{hkl}" ; rr:class ex:Peak ] ;
  rr:predicateObjectMap [ rr:predicate ex:isPartOf ;
    rr:objectMap [ rr:template "https://ex/sample/{No}" ; rr:termType rr:IRI ] ] .
"""


def _write_card(tmp_path):
    (tmp_path / "card.csv").write_text(
        "No,Name,2theta,d,I,hkl\n"
        "A1,Aluminum,21.34,4.161,5.0,002\n"
        "A1,Aluminum,25.87,3.441,11.5,101\n"
        "A1,Aluminum,33.51,2.672,2.7,110\n",
        encoding="utf-8",
    )


def _shell_advisories(rml: str, csv_dir=None) -> list[str]:
    return [a for a in design_advisories(rml, csv_dir) if "empty shell" in a]


def test_empty_shell_flagged_with_the_dropped_columns(tmp_path) -> None:
    _write_card(tmp_path)
    advisories = _shell_advisories(_EMPTY_SHELL, tmp_path)
    assert len(advisories) == 1
    msg = advisories[0]
    assert msg.startswith("map 'Peak'")
    assert "keyed by No, hkl" in msg  # the declared per-row identity
    # The repair names the exact per-row values the mapping dropped — and NOT
    # the key (already bound) nor a metadata column (constant across the file).
    assert "3 column(s) vary across the file" in msg
    assert "2theta, d, I" in msg
    assert "Name" not in msg.split("From the real rows")[1]
    assert "Put them on 'Peak'" in msg
    # The parent is a real card (Name), so it is not a shell.
    assert not any(a.startswith("map 'Material'") for a in advisories)


def test_empty_shell_flags_values_parked_on_the_header_card(tmp_path) -> None:
    # The other way to lose a per-row value: the per-map stage put 2theta on the
    # SAMPLE (keyed {No}), where 3 rows' readings collapse onto one entity as
    # multi-values. Nothing is unbound, so only the ownership reading (G1: the
    # sample key does not determine 2theta) can say the shell is a defect.
    _write_card(tmp_path)
    name_pom = (
        'rr:predicateObjectMap [ rr:predicate ex:name ; rr:objectMap [ rml:reference "Name" ] ]'
    )
    parked = _EMPTY_SHELL.replace(
        name_pom + " .",
        name_pom + ' ;\n  rr:predicateObjectMap [ rr:predicate ex:twoTheta ;'
        ' rr:objectMap [ rml:reference "2theta" ] ] .',
    )
    advisories = _shell_advisories(parked, tmp_path)
    assert len(advisories) == 1
    msg = advisories[0]
    assert "2 column(s) vary across the file and are bound by no map" in msg  # d, I
    # (map labels are the rr:class local names, as everywhere in these advisories)
    assert "vary per row but sit on 'Material'" in msg
    assert "2theta" in msg.split("sit on 'Material'")[1]
    assert "MOVE them to 'Peak' and DELETE them from 'Material'" in msg
    # Name is determined by {No} → the Material's own, never in the MOVE list.
    assert "Name" not in msg.split("sit on 'Material'")[1]


def test_empty_shell_silent_when_the_key_is_the_whole_datum(tmp_path) -> None:
    # A per-row map with no values, but the file has nothing per-row beyond its
    # key: the entity is a legitimate identity anchor, not a lost measurement.
    (tmp_path / "card.csv").write_text(
        "No,Name,hkl\nA1,Aluminum,002\nA1,Aluminum,101\nA1,Aluminum,110\n",
        encoding="utf-8",
    )
    assert _shell_advisories(_EMPTY_SHELL, tmp_path) == []


def test_empty_shell_without_rows_states_the_defect_only() -> None:
    # No source: the structural reading fires (a sibling shares the source, so
    # this is the header+detail shape) but claims nothing about which columns.
    advisories = _shell_advisories(_EMPTY_SHELL)
    assert len(advisories) == 1
    assert "From the real rows" not in advisories[0]


def test_lone_per_row_map_without_rows_stays_quiet() -> None:
    # A single map over its own source with no properties (the connectivity
    # fixtures' shape): no data and no sibling → nothing to adjudicate against.
    assert _shell_advisories(_JOINED_BY_PARENT) == []


def test_per_row_map_with_a_value_is_not_a_shell(tmp_path) -> None:
    _write_card(tmp_path)
    link_om = 'rr:objectMap [ rr:template "https://ex/sample/{No}" ; rr:termType rr:IRI ] ]'
    fixed = _EMPTY_SHELL.replace(
        link_om + " .",
        link_om + ' ;\n  rr:predicateObjectMap [ rr:predicate ex:twoTheta ;'
        ' rr:objectMap [ rml:reference "2theta" ] ] .',
    )
    assert _shell_advisories(fixed, tmp_path) == []


def test_constant_subject_map_is_never_a_shell() -> None:
    # A file-scoped map with only a link (a header card that just points at a
    # run) mints ONE entity per file by design — not a per-row shell.
    rml = _ADV_PREFIXES + """
<#Run> rml:logicalSource [ rml:source "r.csv" ] ;
  rr:subjectMap [ rr:constant <https://ex/run/1> ; rr:class ex:Run ] ;
  rr:predicateObjectMap [ rr:predicate ex:instrument ;
    rr:objectMap [ rr:constant <https://ex/instr/x> ] ] .
"""
    assert _shell_advisories(rml) == []


def test_function_input_counts_as_an_own_value() -> None:
    # A per-row map whose only value goes through a Tier-0 function still
    # carries that row's value — the input column is bound on this map.
    rml = _ADV_PREFIXES + """
@prefix fnml: <http://w3id.org/rml/fnml/> .
@prefix fno: <https://w3id.org/function/ontology#> .
<#Peak> rml:logicalSource [ rml:source "card.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/peak/{No}/{hkl}" ; rr:class ex:Peak ] ;
  rr:predicateObjectMap [ rr:predicate ex:intensity ;
    rr:objectMap [ fnml:functionValue [
      rr:predicateObjectMap [ rr:predicate fno:executes ;
        rr:objectMap [ rr:constant <https://ex/fn/to_float> ] ] ;
      rr:predicateObjectMap [ rr:predicate <https://ex/fn/param> ;
        rr:objectMap [ rml:reference "I" ] ] ] ] ] .
"""
    assert _shell_advisories(rml) == []


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


_UNTYPED_NUMERIC = """\
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://w3id.org/rml/> .
@prefix ex: <https://example.org/ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<#Peak> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "peaks.csv" ] ;
  rr:subjectMap [ rr:template "https://example.org/r/peak/{hkl}" ; rr:class ex:Peak ] ;
  rr:predicateObjectMap [ rr:predicate ex:intensity ;
    rr:objectMap [ rml:reference "intensity" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:twoTheta ;
    rr:objectMap [ rml:reference "twoTheta" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:hkl ;
    rr:objectMap [ rml:reference "hkl" ] ] .
"""


def test_untyped_numeric_column_flagged(tmp_path) -> None:
    """The quiet defect: a number stored as an untyped literal compares as TEXT,
    so ORDER BY answers wrongly with no error (observed live: the highest
    intensity read as 9.4 instead of 100.0)."""
    (tmp_path / "peaks.csv").write_text(
        "intensity,twoTheta,hkl\n100.0,40.07,(1;1;2)\n9.4,77.47,(1;1;6)\n5.0,21.34,(0;0;2)\n",
        encoding="utf-8",
    )
    advisories = [
        a for a in design_advisories(_UNTYPED_NUMERIC, tmp_path) if "untyped literal" in a
    ]
    assert len(advisories) == 1
    assert advisories[0].startswith("column 'intensity'")
    assert "xsd:double" in advisories[0]
    # twoTheta already declares its datatype, and hkl is not numeric — silent.
    assert not any("twoTheta" in a or "hkl" in a for a in advisories)


def test_untyped_numeric_silent_on_mixed_column(tmp_path) -> None:
    """One non-numeric cell and the column is not a number column — stamping a
    datatype there would mint invalid literals, so the advisory stays silent."""
    (tmp_path / "peaks.csv").write_text(
        "intensity,twoTheta,hkl\n100.0,40.07,(1;1;2)\nn/a,77.47,(1;1;6)\n",
        encoding="utf-8",
    )
    assert not [a for a in design_advisories(_UNTYPED_NUMERIC, tmp_path) if "untyped literal" in a]
