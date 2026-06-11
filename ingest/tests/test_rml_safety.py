"""Runtime RML safety gate (asterism.rml_safety.assert_rml_safe).

These guard the「生成コードを実行しない」trust boundary: a mapping that names a
non-Tier-0 function, a SQL/query source, or a file outside the dataset's source
dir must be rejected fail-closed *before* Morph-KGC ever sees it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from asterism.functions import REGISTRY
from asterism.rml_safety import RmlSafetyError, assert_rml_safe

_RML_PREFIX = "@prefix rml: <http://w3id.org/rml/> .\n"


def _a_tier0_function_iri() -> str:
    return next(iter(REGISTRY)).fun_id


def test_relative_csv_source_passes(tmp_path: Path) -> None:
    rml = _RML_PREFIX + '<#M> rml:source "papers.csv" .\n'
    # No raise — a plain confined CSV source is the happy path. The file need not
    # exist yet; containment is a path property, not an existence check.
    assert_rml_safe(rml, tmp_path)


def test_tier0_function_passes(tmp_path: Path) -> None:
    rml = (
        _RML_PREFIX
        + f'<#fn> rml:function <{_a_tier0_function_iri()}> .\n'
        + '<#M> rml:source "papers.csv" .\n'
    )
    assert_rml_safe(rml, tmp_path)


def test_non_tier0_function_rejected(tmp_path: Path) -> None:
    # A GREL built-in such as controls_if (eval-bearing) is NOT in the Tier 0 set.
    rml = _RML_PREFIX + '<#fn> rml:function <http://example.org/grel#controls_if> .\n'
    with pytest.raises(RmlSafetyError, match="Tier 0"):
        assert_rml_safe(rml, tmp_path)


def test_sql_query_source_rejected(tmp_path: Path) -> None:
    rml = _RML_PREFIX + '<#M> rml:query "SELECT * FROM read_text(\'/etc/passwd\')" .\n'
    with pytest.raises(RmlSafetyError, match="SQL/query"):
        assert_rml_safe(rml, tmp_path)


def test_absolute_source_rejected(tmp_path: Path) -> None:
    rml = _RML_PREFIX + '<#M> rml:source "/etc/passwd" .\n'
    with pytest.raises(RmlSafetyError, match="relative"):
        assert_rml_safe(rml, tmp_path)


def test_parent_traversal_source_rejected(tmp_path: Path) -> None:
    rml = _RML_PREFIX + '<#M> rml:source "../../secret.csv" .\n'
    with pytest.raises(RmlSafetyError, match=r"\.\."):
        assert_rml_safe(rml, tmp_path)


def test_url_source_rejected(tmp_path: Path) -> None:
    rml = _RML_PREFIX + '<#M> rml:source "http://169.254.169.254/latest/meta-data" .\n'
    with pytest.raises(RmlSafetyError, match="URL"):
        assert_rml_safe(rml, tmp_path)


def test_relative_xml_source_passes(tmp_path: Path) -> None:
    # The document-ontology layer (JATS/TEI) reads XML declaratively via ql:XPath;
    # .xml is a vetted source extension. It still passes every other gate.
    rml = _RML_PREFIX + '<#M> rml:source "paper.xml" .\n'
    assert_rml_safe(rml, tmp_path)


def test_disallowed_extension_rejected(tmp_path: Path) -> None:
    rml = _RML_PREFIX + '<#M> rml:source "data.parquet" .\n'
    with pytest.raises(RmlSafetyError, match="csv"):
        assert_rml_safe(rml, tmp_path)


def test_unparseable_turtle_rejected(tmp_path: Path) -> None:
    with pytest.raises(RmlSafetyError, match="parseable"):
        assert_rml_safe('rml:source "p.csv"', tmp_path)  # no prefix/subject


def test_legacy_namespace_source_is_also_checked(tmp_path: Path) -> None:
    # Old mmlab rml: namespace must be validated too, not silently skipped.
    rml = (
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        + '<#M> rml:source "/etc/shadow" .\n'
    )
    with pytest.raises(RmlSafetyError, match="relative"):
        assert_rml_safe(rml, tmp_path)
