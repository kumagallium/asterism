"""Tests for trap T10 (MIE example queries parse — and run against the draft).

Structure-stage cases (missing section, malformed entries, broken YAML) are
deterministic without pyoxigraph. Parse/execution cases importorskip it — the
step0 CI job installs the ingest package, which carries pyoxigraph, so they run
in CI; a minimal local venv skips them honestly.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from asterism_step0.validate import SchemaBundle, _check_t10_query_examples

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

_PREFIX = "https://example.com/t10/ontology#"

_DRAFT_TTL = f"""\
@prefix ex: <{_PREFIX}> .
ex:r1 a ex:Record ; ex:value "1.5" ; ex:label "one" .
ex:r2 a ex:Record ; ex:value "2.5" ; ex:label "two" .
"""


def _mie(tmp_path: Path, examples_yaml: str | None) -> Path:
    body = "schema_info:\n  title: T10 test\n"
    if examples_yaml is not None:
        body += "sparql_query_examples:\n" + dedent(examples_yaml)
    path = tmp_path / "mie.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _draft(tmp_path: Path) -> Path:
    path = tmp_path / "draft.ttl"
    path.write_text(_DRAFT_TTL, encoding="utf-8")
    return path


def _entry(query: str, title: str = "Q", key: str = "query") -> str:
    indented = "\n".join("      " + line for line in dedent(query).strip().splitlines())
    return f"  - title: {title}\n    {key}: |\n{indented}\n"


# ----------------------------------------------------------------------------
# Structure stage (no pyoxigraph needed)
# ----------------------------------------------------------------------------


def test_no_mie_skips(tmp_path: Path) -> None:
    result = _check_t10_query_examples(SchemaBundle())
    assert result.status == "skip"


def test_missing_section_warns_with_recipe(tmp_path: Path) -> None:
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=_mie(tmp_path, None)))
    assert result.status == "warn"
    assert "sparql_query_examples" in result.detail
    assert "SELECT ?s ?type" in result.fix  # paste-ready starter


def test_empty_section_warns(tmp_path: Path) -> None:
    mie = tmp_path / "mie.yaml"
    mie.write_text("schema_info:\n  title: x\nsparql_query_examples: []\n", encoding="utf-8")
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie))
    assert result.status == "warn"


def test_entry_without_query_string_fails(tmp_path: Path) -> None:
    mie = _mie(tmp_path, "  - title: broken\n    description: no query key\n")
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie))
    assert result.status == "fail"
    assert any("has no `query:`/`sparql:`" in line for line in result.evidence)


def test_broken_yaml_fails_as_finding(tmp_path: Path) -> None:
    mie = tmp_path / "mie.yaml"
    mie.write_text("schema_info: [unclosed\n", encoding="utf-8")
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie))
    assert result.status == "fail"
    assert "not parseable YAML" in result.detail


# ----------------------------------------------------------------------------
# Parse + execution stages (pyoxigraph — present in CI via the ingest install)
# ----------------------------------------------------------------------------


def test_syntax_error_fails_with_recipe(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    mie = _mie(tmp_path, _entry("SELECT ?s WHERE { ?s ?p", title="unclosed"))
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie))
    assert result.status == "fail"
    assert any("parse error" in line for line in result.evidence)
    assert "REPLACE each failed entry's" in result.fix


def test_valid_syntax_without_draft_passes_syntax_only(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    mie = _mie(tmp_path, _entry("SELECT ?s ?o WHERE { ?s a ?o } LIMIT 5"))
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie))
    assert result.status == "pass"
    assert "--draft-ttl" in result.detail  # discloses execution was not attempted


def test_rows_against_draft_pass(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    mie = _mie(
        tmp_path,
        _entry(f"SELECT ?s ?v WHERE {{ ?s <{_PREFIX}value> ?v }} LIMIT 5"),
    )
    bundle = SchemaBundle(mie_yaml=mie, draft_ttl=_draft(tmp_path))
    result = _check_t10_query_examples(bundle)
    assert result.status == "pass"
    assert "return rows" in result.detail


def test_zero_rows_against_draft_fails_with_proven_predicate(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    mie = _mie(
        tmp_path,
        _entry(f"SELECT ?s WHERE {{ ?s <{_PREFIX}doesNotExist> ?v }} LIMIT 5"),
    )
    bundle = SchemaBundle(mie_yaml=mie, draft_ttl=_draft(tmp_path))
    result = _check_t10_query_examples(bundle)
    assert result.status == "fail"
    assert any("returned no rows" in line for line in result.evidence)
    # The recipe names a predicate the draft actually contains.
    assert _PREFIX in result.fix


def test_graph_scoped_query_is_not_a_false_negative(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    graph = "https://example.com/t10/graph/canonical"
    mie = _mie(
        tmp_path,
        _entry(
            f"SELECT ?s WHERE {{ GRAPH <{graph}> {{ ?s a <{_PREFIX}Record> }} }} LIMIT 5"
        ),
    )
    bundle = SchemaBundle(mie_yaml=mie, draft_ttl=_draft(tmp_path))
    result = _check_t10_query_examples(bundle)
    assert result.status == "pass", result.evidence


def test_ask_false_is_a_failure_and_true_passes(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    good = _entry(f"ASK {{ ?s a <{_PREFIX}Record> }}", title="present")
    bad = _entry(f"ASK {{ ?s a <{_PREFIX}Missing> }}", title="absent")
    mie = _mie(tmp_path, good + bad)
    bundle = SchemaBundle(mie_yaml=mie, draft_ttl=_draft(tmp_path))
    result = _check_t10_query_examples(bundle)
    assert result.status == "fail"
    assert any("absent" in line for line in result.evidence)
    assert not any("present" in line for line in result.evidence)


def test_togomcp_style_sparql_key_is_accepted(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    mie = _mie(
        tmp_path,
        _entry("SELECT ?s WHERE { ?s ?p ?o } LIMIT 5", key="sparql"),
    )
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie))
    assert result.status == "pass"


def test_unloadable_draft_fails_with_rematerialize_recipe(tmp_path: Path) -> None:
    pytest.importorskip("pyoxigraph")
    mie = _mie(tmp_path, _entry("SELECT ?s WHERE { ?s ?p ?o } LIMIT 5"))
    broken = tmp_path / "draft.ttl"
    broken.write_text("@prefix broken turtle", encoding="utf-8")
    result = _check_t10_query_examples(SchemaBundle(mie_yaml=mie, draft_ttl=broken))
    assert result.status == "fail"
    assert "could not be loaded" in result.detail
