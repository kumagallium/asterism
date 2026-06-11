"""Tests for asterism_step0.coverage (Track C — Tier 0 coverage measurement).

The analyzer is a pure function over (proposal Markdown, inspection, allowed
IRI set), so these tests need neither an LLM/API key nor the ingest package —
they pass a synthetic allowed set, mirroring rml_check's testable design.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from asterism_step0.coverage import (
    DEFAULT_RAW_RATE_GATE,
    DatasetCoverage,
    _categorize_column,
    _is_raw_predicate,
    aggregate,
    analyze_proposal,
    build_report,
    prepare_prompts,
    render_report_md,
    report_to_dict,
)
from asterism_step0.inspect import ColumnSummary, inspect_source_set

pytest.importorskip("rdflib")

FN = "https://kumagallium.github.io/asterism/fn/"
ALLOWED = {FN + n for n in ("date_iso", "iri_safe", "slug", "float_array_max")}


def _proposal(turtle_body: str) -> str:
    """Wrap an RML turtle body in a minimal proposal Markdown (header + fence)."""
    return f"### 9. RML declarative mapping\n\n```turtle\n{dedent(turtle_body).strip()}\n```\n"


_PREFIXES = """
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix rmlf: <http://w3id.org/rml/> .
@prefix fn:   <https://kumagallium.github.io/asterism/fn/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix sd:   <https://example.org/o/> .
"""


def _col(name: str, t: str, samples: list[str], *, unique: int | None = None) -> ColumnSummary:
    return ColumnSummary(
        name=name,
        inferred_type=t,
        non_null_count=len(samples),
        total_rows=len(samples),
        unique_count=unique if unique is not None else len(set(samples)),
        sample_values=samples[:3],
    )


# ----------------------------------------------------------------------------
# _is_raw_predicate
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iri", "expected"),
    [
        ("https://example.org/o/authorsRaw", True),
        ("https://example.org/o/published_raw", True),
        ("https://example.org/o/drawTool", False),  # 'draw' is not a Raw suffix
        ("https://example.org/o/rawScore", False),  # prefix, not suffix
        ("https://example.org/o/title", False),
    ],
)
def test_is_raw_predicate(iri: str, expected: bool) -> None:
    assert _is_raw_predicate(iri) is expected


# ----------------------------------------------------------------------------
# analyze_proposal — function / raw / direct classification + the gate metric
# ----------------------------------------------------------------------------


def test_analyze_counts_function_raw_and_direct() -> None:
    proposal = _proposal(
        _PREFIXES
        + """
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "x.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://ex/{id}" ] ;
          rr:predicateObjectMap [ rr:predicate sd:name ;
            rr:objectMap [ rml:reference "name" ] ] ;
          rr:predicateObjectMap [ rr:predicate sd:issued ; rr:objectMap [
            rmlf:functionExecution [ rmlf:function fn:date_iso ;
              rmlf:input [ rmlf:parameter fn:p_value ;
                rmlf:inputValueMap [ rml:reference "when" ] ] ] ;
            rr:datatype xsd:date ] ] ;
          # fallback: tags not expanded
          rr:predicateObjectMap [ rr:predicate sd:tagsRaw ;
            rr:objectMap [ rml:reference "tags" ] ] .
        """
    )
    cov = analyze_proposal("x", proposal, inspections=[], allowed_fn_iris=ALLOWED)
    assert cov.has_rml is True
    assert (cov.function_maps, cov.raw_fallbacks, cov.direct_maps) == (1, 1, 1)
    assert cov.computed_columns == 2
    assert cov.raw_rate == 0.5
    assert dict(cov.function_usage) == {"date_iso": 1}
    assert cov.fallback_comments == 1
    assert dict(cov.t9_misses) == {}


def test_analyze_flags_t9_miss_for_out_of_set_function() -> None:
    proposal = _proposal(
        _PREFIXES
        + """
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "x.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://ex/{id}" ] ;
          rr:predicateObjectMap [ rr:predicate sd:v ; rr:objectMap [
            rmlf:functionExecution [ rmlf:function fn:number_clean ;
              rmlf:input [ rmlf:parameter fn:p_value ;
                rmlf:inputValueMap [ rml:reference "v" ] ] ] ] ] .
        """
    )
    cov = analyze_proposal("x", proposal, inspections=[], allowed_fn_iris=ALLOWED)
    assert dict(cov.t9_misses) == {FN + "number_clean": 1}
    # An out-of-set reference is NOT counted as a vetted-function usage.
    assert dict(cov.function_usage) == {}
    assert cov.t9_checked is True


def test_analyze_skips_t9_when_allowed_set_unavailable() -> None:
    proposal = _proposal(
        _PREFIXES
        + """
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "x.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://ex/{id}" ] ;
          rr:predicateObjectMap [ rr:predicate sd:v ; rr:objectMap [
            rmlf:functionExecution [ rmlf:function fn:date_iso ;
              rmlf:input [ rmlf:parameter fn:p_value ;
                rmlf:inputValueMap [ rml:reference "v" ] ] ] ] ] .
        """
    )
    cov = analyze_proposal("x", proposal, inspections=[], allowed_fn_iris=None)
    assert cov.t9_checked is False
    assert dict(cov.t9_misses) == {}
    # Without an allowed set we still count usage (can't tell miss from valid).
    assert dict(cov.function_usage) == {"date_iso": 1}


def test_analyze_counts_function_in_subject_template() -> None:
    proposal = _proposal(
        _PREFIXES
        + """
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "x.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [
            rmlf:functionExecution [ rmlf:function fn:slug ;
              rmlf:input [ rmlf:parameter fn:p_value ;
                rmlf:inputValueMap [ rml:reference "name" ] ] ] ] ;
          rr:predicateObjectMap [ rr:predicate sd:name ;
            rr:objectMap [ rml:reference "name" ] ] .
        """
    )
    cov = analyze_proposal("x", proposal, inspections=[], allowed_fn_iris=ALLOWED)
    # slug is used in the subject (not an object map) — it still counts as usage,
    # but does not inflate the function-objectMap / computed-column count.
    assert dict(cov.function_usage) == {"slug": 1}
    assert cov.function_maps == 0
    assert cov.computed_columns == 0


def test_analyze_handles_proposal_without_rml() -> None:
    cov = analyze_proposal("x", "# just prose, no turtle block", [], ALLOWED)
    assert cov.has_rml is False
    assert cov.raw_rate is None
    assert any("no RML" in w for w in cov.warnings)


# ----------------------------------------------------------------------------
# Demand sniffer
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("col", "expected"),
    [
        (_col("Release Date", "xsd:string", ["Jun 12 1998", "Jan 01 1971", "Jun 02 1995"]),
         "messy_date"),
        (_col("date", "xsd:string", ["Jan 1 2000", "Aug 1 2000"]), "messy_date"),
        (_col("time", "xsd:integer", ["1517966773840", "1517945795710"]), "epoch_millis"),
        (_col("DOI", "xsd:string", ["10.1007/978-3-658", "10.1016/b978-0"]), "doi"),
        (_col("url", "xsd:string", ["https://example.org/a", "https://example.org/b"]), "url"),
        (_col("Body Mass (g)", "xsd:integer", ["3750", "3650"]), "value_with_unit_name"),
        (_col("author", "json-array", ['[{"x":1}]']), "multivalue_or_json"),
        (_col("Sex", "xsd:string", ["MALE", "FEMALE"], unique=2), "boolean"),
        (_col("temp", "xsd:string", ["300 K", "350 K"]), "value_with_unit"),
        (_col("span", "xsd:string", ["10-20", "30-45"]), "numeric_range"),
        (_col("share", "xsd:string", ["12%", "3.5%"]), "percent"),
    ],
)
def test_categorize_column_positive(col: ColumnSummary, expected: str) -> None:
    assert _categorize_column(col) == expected


@pytest.mark.parametrize(
    "col",
    [
        _col("id", "xsd:string", ["1000chmg", "1000chrs", "37868143"]),  # ID, not value+unit
        _col("temp_max", "xsd:double", ["12.8", "3.3"]),  # clean number
        _col("name", "xsd:string", ["Thigpen", "Col. Dyke"]),  # free text
        _col("Date", "xsd:date", ["1958-03-01", "1959-02-01"]),  # already ISO
        _col("count", "xsd:integer", ["430", "420"]),  # plain int, not epoch
    ],
)
def test_categorize_column_negative(col: ColumnSummary) -> None:
    assert _categorize_column(col) is None


def test_demand_cross_references_handling() -> None:
    """A function-mapped messy-date column is reported as 'satisfied'."""
    proposal = _proposal(
        _PREFIXES
        + """
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "s.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://ex/{symbol}" ] ;
          rr:predicateObjectMap [ rr:predicate sd:date ; rr:objectMap [
            rmlf:functionExecution [ rmlf:function fn:date_iso ;
              rmlf:input [ rmlf:parameter fn:p_value ;
                rmlf:inputValueMap [ rml:reference "date" ] ] ] ] ] .
        """
    )
    insp, _ = inspect_source_set([_tiny_csv()])
    cov = analyze_proposal("stocks", proposal, insp, ALLOWED)
    by_col = {h.column: h.handled_as for h in cov.demand}
    assert by_col.get("date") == "function:date_iso"


def _tiny_csv() -> Path:
    import tempfile

    p = Path(tempfile.mkdtemp()) / "s.csv"
    p.write_text(
        "symbol,date,price\nMSFT,Jan 1 2000,39.81\nMSFT,Aug 1 2000,28.4\n", encoding="utf-8"
    )
    return p


# ----------------------------------------------------------------------------
# aggregate / gate
# ----------------------------------------------------------------------------


def test_aggregate_pools_raw_rate_and_applies_gate() -> None:
    a = DatasetCoverage(dataset="a", has_rml=True, function_maps=1, raw_fallbacks=0)
    b = DatasetCoverage(dataset="b", has_rml=True, function_maps=2, raw_fallbacks=2)
    report = aggregate([a, b], raw_rate_gate=0.5)
    # pooled: 2 raw / 5 computed = 0.4 < 0.5 → pass
    assert report.total_computed_columns == 5
    assert report.total_raw_fallbacks == 2
    assert report.corpus_raw_rate == pytest.approx(0.4)
    assert report.gate_passes is True


def test_gate_default_value() -> None:
    # Tightened 0.15 → 0.05 after tabularize made native-JSON nested arrays
    # reducible (corpus …Raw 11.1% → 0.0%); see native-json-denormalization.md.
    assert DEFAULT_RAW_RATE_GATE == 0.05


def test_gate_none_when_no_computed_columns() -> None:
    report = aggregate([DatasetCoverage(dataset="a", has_rml=True)])
    assert report.corpus_raw_rate is None
    assert report.gate_passes is None


def test_report_renders_and_serializes() -> None:
    a = DatasetCoverage(dataset="a", has_rml=True, function_maps=1, raw_fallbacks=3)
    a.function_usage["date_iso"] = 1
    report = aggregate([a], raw_rate_gate=0.15)
    md = render_report_md(report)
    assert "Tier 0 coverage report" in md
    assert "FAIL" in md  # 3/4 = 75% ≥ 15%
    d = report_to_dict(report)
    assert d["corpus_raw_rate"] == pytest.approx(0.75)
    assert d["gate_passes"] is False
    assert d["function_usage"] == {"date_iso": 1}


# ----------------------------------------------------------------------------
# Driver wiring — prepare_prompts + build_report on a tiny temp corpus
# ----------------------------------------------------------------------------


def test_prepare_and_build_report_end_to_end(tmp_path: Path) -> None:
    ds = tmp_path / "corpus" / "datasets" / "demo"
    (ds / "source").mkdir(parents=True)
    (ds / "source" / "demo.csv").write_text(
        "id,when,tags\n1,Jan 1 2000,a;b\n2,Feb 2 2001,c\n", encoding="utf-8"
    )
    (ds / "domain.md").write_text("## Domain context\n\n- Dataset: demo\n", encoding="utf-8")

    runs = tmp_path / "runs"
    prepared = prepare_prompts(tmp_path / "corpus", runs)
    assert prepared == ["demo"]
    assert (runs / "demo" / "inspection.md").is_file()
    prompt = (runs / "demo" / "prompt.md").read_text()
    assert "SYSTEM PROMPT" in prompt and "USER MESSAGE" in prompt and "demo.csv" in prompt

    # Author a proposal that cleans `when` via fn:date_iso and falls back on `tags`.
    (runs / "demo" / "proposal.md").write_text(
        _proposal(
            _PREFIXES
            + """
            <#M> a rr:TriplesMap ;
              rml:logicalSource [ rml:source "demo.csv" ; rml:referenceFormulation ql:CSV ] ;
              rr:subjectMap [ rr:template "https://ex/{id}" ] ;
              rr:predicateObjectMap [ rr:predicate sd:when ; rr:objectMap [
                rmlf:functionExecution [ rmlf:function fn:date_iso ;
                  rmlf:input [ rmlf:parameter fn:p_value ;
                    rmlf:inputValueMap [ rml:reference "when" ] ] ] ] ] ;
              # fallback: tags not expanded
              rr:predicateObjectMap [ rr:predicate sd:tagsRaw ;
                rr:objectMap [ rml:reference "tags" ] ] .
            """
        ),
        encoding="utf-8",
    )
    report = build_report(tmp_path / "corpus", runs, allowed_fn_iris=ALLOWED)
    assert len(report.datasets_with_rml) == 1
    cov = report.datasets[0]
    assert cov.function_maps == 1 and cov.raw_fallbacks == 1
    assert report.corpus_raw_rate == pytest.approx(0.5)
