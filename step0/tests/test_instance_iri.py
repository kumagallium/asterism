"""Unit tests for the instance IRI base (ADR instance-iri-base.md).

Pure functions — no LLM, no environment. The design-prompt injection and the
parse-time guard are covered where they live (test_staged_propose /
test_propose / test_mapping_ir); this file pins the primitives.
"""
from __future__ import annotations

from asterism_step0.instance_iri import (
    DEFAULT_IRI_BASE,
    dataset_namespace_block,
    normalize_iri_base,
    placeholder_prefix_issue,
)


def test_normalize_falls_back_to_invalid_default() -> None:
    assert normalize_iri_base(None) == DEFAULT_IRI_BASE
    assert normalize_iri_base("") == DEFAULT_IRI_BASE
    assert normalize_iri_base("   ") == DEFAULT_IRI_BASE
    assert DEFAULT_IRI_BASE.endswith(".invalid")  # RFC 2606: never resolves


def test_normalize_strips_trailing_slash() -> None:
    assert normalize_iri_base("https://data.lab.jp/asterism/") == "https://data.lab.jp/asterism"
    assert normalize_iri_base("https://data.lab.jp") == "https://data.lab.jp"


def test_namespace_block_pins_base_and_shape() -> None:
    block = dataset_namespace_block("https://data.lab.jp/asterism/")
    assert "https://data.lab.jp/asterism/datasets/<slug>/ontology#" in block
    assert "https://data.lab.jp/asterism/datasets/<slug>/resource/" in block
    assert "example.org" in block  # the explicit NEVER rule


def test_namespace_block_unset_uses_default() -> None:
    block = dataset_namespace_block(None)
    assert f"{DEFAULT_IRI_BASE}/datasets/<slug>/ontology#" in block


def test_placeholder_detects_example_domains_and_localhost() -> None:
    assert placeholder_prefix_issue("sd", "https://example.org/xrd-ontology#")
    assert placeholder_prefix_issue("sdr", "http://www.example.com/resource/")
    assert placeholder_prefix_issue("x", "https://sub.example.net/ns#")
    assert placeholder_prefix_issue("x", "http://localhost:8080/ns#")


def test_placeholder_allows_real_and_invalid_namespaces() -> None:
    # Real namespaces pass.
    assert placeholder_prefix_issue("sd", "https://kumagallium.github.io/asterism/x#") is None
    assert placeholder_prefix_issue("schema", "https://schema.org/") is None
    # The unconfigured-instance default is deliberate, not a placeholder.
    assert placeholder_prefix_issue("sd", f"{DEFAULT_IRI_BASE}/datasets/x/ontology#") is None
    # A host that merely CONTAINS the word keeps working (example ≠ example.org).
    assert placeholder_prefix_issue("x", "https://exampleuniversity.edu/ns#") is None


def test_placeholder_ignores_unparseable_iris() -> None:
    # Structural checks own malformed IRIs; the guard must not throw or fire.
    assert placeholder_prefix_issue("x", "http://[bad") is None
