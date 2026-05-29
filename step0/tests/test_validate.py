"""Tests for csv2rdf_step0.validate (8-trap validator)."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from csv2rdf_step0.validate import (
    SchemaBundle,
    _check_t1_uniqueness,
    _check_t2_bom,
    _check_t3_bnode_free,
    _check_t4_keywords,
    _check_t5_mermaid_escape,
    _check_t6_fake_iri,
    _check_t7_rationale,
    _check_t8_hallucination,
    _extract_composite_keys,
    render_report,
    validate_schema,
)

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def _write_bytes(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


# A minimal MIE template — tests Edit specific sections.
_BASE_MIE = """
schema_info:
  title: Test
  description: Test dataset.
  base_uri: https://example.com/test/
  keywords:
    - one
    - two
    - three
    - four
    - five
  categories:
    - test
shape_expressions: |
  PREFIX sdr: <https://example.com/test/resource/>
  <SampleShape> {
    dcterms:identifier xsd:string ;
  }
sample_rdf_entries: []
architectural_notes: |
  Decision: use composite keys.
  Why: SID alone has 28 collisions in papers.csv.
  Alternatives: UUIDs (loses upstream link).
  Trade-offs: longer IRIs.
"""


# ----------------------------------------------------------------------------
# T1 helpers
# ----------------------------------------------------------------------------


def test_extract_composite_keys_finds_two_way() -> None:
    text = "sdr:sample/{SID}-{sample_id}"
    keys = _extract_composite_keys(text)
    assert ("SID", "sample_id") in keys


def test_extract_composite_keys_finds_three_way() -> None:
    text = "sdr:curve/{SID}-{figure_id}-{sample_id} and sdr:sample/{SID}-{sample_id}"
    keys = _extract_composite_keys(text)
    assert ("SID", "figure_id", "sample_id") in keys
    assert ("SID", "sample_id") in keys


def test_extract_composite_keys_ignores_non_sdr_iris() -> None:
    text = "sd:Curve and dcterms:identifier and sample/{SID}"
    keys = _extract_composite_keys(text)
    assert keys == []


# ----------------------------------------------------------------------------
# T1: uniqueness check
# ----------------------------------------------------------------------------


def test_t1_passes_when_composite_key_unique(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id,composition
        1,10,Bi2Te3
        1,11,PbTe
        2,10,SnSe
        """,
    )
    mie = _write(
        tmp_path / "mie.yaml",
        "shape_expressions: |\n  sdr:sample/{SID}-{sample_id}\n",
    )
    res = _check_t1_uniqueness(
        SchemaBundle(mie_yaml=mie, source_csvs=[csv], fk_hint_columns=["SID"])
    )
    assert res.status == "pass", res.detail


def test_t1_fails_when_composite_key_collides(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "papers.csv",
        """
        SID,DOI
        1,10.1/a
        1,10.1/b
        """,
    )
    # If MIE says sdr:paper/{SID} but SID collides...
    mie = _write(
        tmp_path / "mie.yaml",
        "shape_expressions: |\n  sdr:paper/{SID}\n",
    )
    res = _check_t1_uniqueness(SchemaBundle(mie_yaml=mie, source_csvs=[csv]))
    assert res.status == "fail"
    assert "collide" in res.detail.lower() or "collision" in res.evidence[0].lower()


def test_t1_skipped_without_inputs() -> None:
    res = _check_t1_uniqueness(SchemaBundle())
    assert res.status == "skip"


def test_t1_ignores_negative_iri_in_anti_patterns(tmp_path: Path) -> None:
    """★ dogfood Finding 3: an IRI template documented in anti_patterns as a
    BAD example must NOT trigger a T1 failure. Here the schema correctly uses
    the composite (SID, sample_id) in shape_expressions, and anti_patterns
    explicitly warns against the single-key form."""
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    mie = _write(
        tmp_path / "mie.yaml",
        """
        shape_expressions: |
          sdr:sample/{SID}-{sample_id}
        anti_patterns: |
          Do NOT mint sample IRIs as sdr:sample/{sample_id}. sample_id is
          paper-scoped and collides across SIDs; use the composite key.
        """,
    )
    res = _check_t1_uniqueness(
        SchemaBundle(mie_yaml=mie, source_csvs=[csv], fk_hint_columns=["SID"])
    )
    # (SID, sample_id) is unique → pass. The single-key anti-pattern example
    # must be excluded from the scan.
    assert res.status == "pass", res.detail
    # And the bad single-key tuple must not appear among the tested keys.
    assert all("sample_id" not in r or "SID" in r for r in res.evidence)


def test_t1_still_fails_on_real_single_key_declaration(tmp_path: Path) -> None:
    """Regression guard: excluding anti_patterns must NOT mask a genuinely
    bad single-key IRI declared in shape_expressions."""
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    mie = _write(
        tmp_path / "mie.yaml",
        "shape_expressions: |\n  sdr:sample/{sample_id}\n",  # genuinely wrong
    )
    res = _check_t1_uniqueness(
        SchemaBundle(mie_yaml=mie, source_csvs=[csv], fk_hint_columns=["SID"])
    )
    assert res.status == "fail"


# ----------------------------------------------------------------------------
# T2: BOM handling
# ----------------------------------------------------------------------------


def test_t2_passes_when_ingester_uses_utf8_sig(tmp_path: Path) -> None:
    ing = _write(
        tmp_path / "ingest.py",
        'with open(p, encoding="utf-8-sig") as f: ...\n',
    )
    res = _check_t2_bom(SchemaBundle(ingester_py=ing))
    assert res.status == "pass"


def test_t2_fails_when_ingester_uses_plain_utf8(tmp_path: Path) -> None:
    ing = _write(
        tmp_path / "ingest.py",
        'with open(p, encoding="utf-8") as f: ...\n',
    )
    res = _check_t2_bom(SchemaBundle(ingester_py=ing))
    assert res.status == "fail"


def test_t2_flags_csv_with_bom(tmp_path: Path) -> None:
    csv = _write_bytes(tmp_path / "bom.csv", b"\xef\xbb\xbfSID,a\n1,x\n")
    ing = _write(tmp_path / "ingest.py", 'open(p, encoding="utf-8-sig")\n')
    res = _check_t2_bom(SchemaBundle(ingester_py=ing, source_csvs=[csv]))
    # Should still pass (ingester strips BOM) but evidence notes it
    assert res.status == "pass"
    assert any("BOM" in e for e in res.evidence)


# ----------------------------------------------------------------------------
# T3: bnode-free
# ----------------------------------------------------------------------------


def test_t3_passes_with_clean_tbox(tmp_path: Path) -> None:
    ttl = _write(
        tmp_path / "schema.ttl",
        """
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix sd:  <https://example.com/o#> .
        sd:Paper a owl:Class .
        """,
    )
    res = _check_t3_bnode_free(SchemaBundle(tbox_ttl=ttl))
    assert res.status == "pass"


def test_t3_fails_with_bnodes_in_tbox(tmp_path: Path) -> None:
    # owl:Restriction blank node — LinkML-style
    ttl = _write(
        tmp_path / "schema.ttl",
        """
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix sd:  <https://example.com/o#> .
        sd:Paper a owl:Class ;
            rdfs:subClassOf [ a owl:Restriction ; owl:onProperty sd:title ; owl:maxCardinality 1 ] .
        """,
    )
    res = _check_t3_bnode_free(SchemaBundle(tbox_ttl=ttl))
    assert res.status == "fail"


def test_t3_fails_when_ingester_uses_bnode(tmp_path: Path) -> None:
    ing = _write(
        tmp_path / "ingest.py",
        "from rdflib import BNode\nx = BNode()\n",
    )
    res = _check_t3_bnode_free(SchemaBundle(ingester_py=ing))
    assert res.status == "fail"


# ----------------------------------------------------------------------------
# T4: MIE keywords / categories
# ----------------------------------------------------------------------------


def test_t4_passes_with_enough_keywords(tmp_path: Path) -> None:
    mie = _write(tmp_path / "mie.yaml", _BASE_MIE)
    res = _check_t4_keywords(SchemaBundle(mie_yaml=mie))
    assert res.status == "pass"


def test_t4_fails_with_too_few_keywords(tmp_path: Path) -> None:
    mie = _write(
        tmp_path / "mie.yaml",
        """
        schema_info:
          title: Tiny
          keywords:
            - one
            - two
          categories:
            - x
        """,
    )
    res = _check_t4_keywords(SchemaBundle(mie_yaml=mie))
    assert res.status == "fail"
    assert "keywords" in res.detail


# ----------------------------------------------------------------------------
# T5: Mermaid colon escape
# ----------------------------------------------------------------------------


def test_t5_passes_with_clean_labels(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "diagram.md",
        """
        ```mermaid
        classDiagram
            Paper "1" --> "*" Sample : has
            Sample --> Curve : measured
        ```
        """,
    )
    res = _check_t5_mermaid_escape(SchemaBundle(diagram_md=md))
    assert res.status == "pass"


def test_t5_fails_when_label_contains_colon(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "diagram.md",
        """
        ```mermaid
        classDiagram
            Paper --> Sample : schema:author
        ```
        """,
    )
    res = _check_t5_mermaid_escape(SchemaBundle(diagram_md=md))
    assert res.status == "fail"
    assert "schema:author" in str(res.evidence)


# ----------------------------------------------------------------------------
# T6: fake sample_rdf_entries
# ----------------------------------------------------------------------------


def test_t6_passes_when_iris_match_csv(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "papers.csv",
        """
        SID,title
        6,Paper Six
        """,
    )
    mie = _write(
        tmp_path / "mie.yaml",
        """
        sample_rdf_entries:
          - title: Example
            rdf: |
              sdr:paper/6 a sd:Paper .
        """,
    )
    res = _check_t6_fake_iri(SchemaBundle(mie_yaml=mie, source_csvs=[csv]))
    assert res.status == "pass"


def test_t6_fails_with_invented_iri(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "papers.csv",
        """
        SID,title
        6,Real
        """,
    )
    mie = _write(
        tmp_path / "mie.yaml",
        """
        sample_rdf_entries:
          - title: Hallucinated
            rdf: |
              sdr:paper/999999 a sd:Paper .
        """,
    )
    res = _check_t6_fake_iri(SchemaBundle(mie_yaml=mie, source_csvs=[csv]))
    assert res.status == "fail"


# ----------------------------------------------------------------------------
# T7: Why / Alternatives / Trade-offs
# ----------------------------------------------------------------------------


def test_t7_passes_with_all_three_keywords(tmp_path: Path) -> None:
    mie = _write(tmp_path / "mie.yaml", _BASE_MIE)
    res = _check_t7_rationale(SchemaBundle(mie_yaml=mie))
    assert res.status == "pass"


def test_t7_warns_when_missing_keyword(tmp_path: Path) -> None:
    mie = _write(
        tmp_path / "mie.yaml",
        """
        architectural_notes: |
          We chose composite keys. Why: collisions.
        """,
    )
    res = _check_t7_rationale(SchemaBundle(mie_yaml=mie))
    assert res.status == "warn"
    assert "Alternatives" in res.detail or "Trade-offs" in res.detail


# ----------------------------------------------------------------------------
# T8: hallucination test (placeholder)
# ----------------------------------------------------------------------------


def test_t8_skipped_without_llm() -> None:
    res = _check_t8_hallucination(SchemaBundle())
    assert res.status == "skip"


# ----------------------------------------------------------------------------
# End-to-end: validate_schema + report
# ----------------------------------------------------------------------------


def test_validate_schema_returns_8_results(tmp_path: Path) -> None:
    bundle = SchemaBundle()  # everything skips
    report = validate_schema(bundle)
    assert len(report.results) == 8
    assert {r.trap_id for r in report.results} == {f"T{i}" for i in range(1, 9)}
    assert report.exit_code() == 0  # all skips, no failures


def test_validate_schema_exits_1_on_failure(tmp_path: Path) -> None:
    ing = _write(tmp_path / "ingest.py", 'open(p, encoding="utf-8")\n')  # T2 fails
    report = validate_schema(SchemaBundle(ingester_py=ing))
    assert report.exit_code() == 1
    assert any(r.trap_id == "T2" and r.status == "fail" for r in report.results)


def test_render_report_includes_glyphs_and_summary(tmp_path: Path) -> None:
    mie = _write(tmp_path / "mie.yaml", _BASE_MIE)
    report = validate_schema(SchemaBundle(mie_yaml=mie))
    md = render_report(report)
    assert "# Schema validation report" in md
    assert "**Summary**" in md
    # Glyph for at least one passed trap should appear
    assert "✓" in md or "·" in md
