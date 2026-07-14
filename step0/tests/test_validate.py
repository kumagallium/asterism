"""Tests for asterism_step0.validate (trap validator T1-T9)."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from asterism_step0.validate import (
    SchemaBundle,
    _check_t1_uniqueness,
    _check_t2_bom,
    _check_t3_bnode_free,
    _check_t4_keywords,
    _check_t5_mermaid_escape,
    _check_t6_fake_iri,
    _check_t7_rationale,
    _check_t8_hallucination,
    _check_t9_rml_closed_set,
    _extract_composite_keys,
    _lint_classdiagram,
    render_report,
    validate_schema,
)

FIXTURES = Path(__file__).parent / "fixtures"

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
# T1: ingester-builder safety net (dogfood Round 3 follow-up)
# ----------------------------------------------------------------------------


_COMPOSITE_INGESTER = """
SDR = None
def sample_iri(sid, sample_id):
    return SDR[f"sample/{sid}-{sample_id}"]
def emit(row, g):
    return sample_iri(row["SID"], row["sample_id"])
"""


def test_t1_ingester_promotes_warn_to_pass(tmp_path: Path) -> None:
    """★ Round 3 scenario: MIE carries no composite ``{}`` template (so the old
    T1 only warned), but the ingester correctly mints the composite key. The
    enhanced T1 reads the ingester and verifies (SID, sample_id) → pass."""
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    mie = _write(  # no sdr:<entity>/{...} template anywhere
        tmp_path / "mie.yaml",
        "schema_info:\n  title: T\nsample_rdf_entries: []\n",
    )
    ing = _write(tmp_path / "ingest.py", _COMPOSITE_INGESTER)
    res = _check_t1_uniqueness(
        SchemaBundle(mie_yaml=mie, ingester_py=ing, source_csvs=[csv])
    )
    assert res.status == "pass", res.detail
    assert any("ingester" in e for e in res.evidence)


def test_t1_ingester_catches_wrong_single_key_when_mie_looks_clean(tmp_path: Path) -> None:
    """★ The safety net: the MIE shows a correct composite key, but the ingester
    actually mints a single-key IRI. Full-CSV validate must FAIL on the ingester
    key even though the MIE key passes."""
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    mie = _write(  # MIE looks correct (composite)
        tmp_path / "mie.yaml",
        "shape_expressions: |\n  sdr:sample/{SID}-{sample_id}\n",
    )
    ing = _write(  # but the ingester is wrong (single key)
        tmp_path / "ingest.py",
        """
        SDR = None
        def emit(row, g):
            sample_id = row.get("sample_id", "").strip()
            return SDR[f"sample/{sample_id}"]
        """,
    )
    res = _check_t1_uniqueness(
        SchemaBundle(mie_yaml=mie, ingester_py=ing, source_csvs=[csv])
    )
    assert res.status == "fail", res.detail
    # The failing line is the single-key ingester one; the composite MIE one passes.
    assert any("collisions" in e and "sample_id)" in e for e in res.evidence)


def test_t1_ingester_only_bundle(tmp_path: Path) -> None:
    """No MIE at all — T1 still verifies the ingester's composite key."""
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    ing = _write(tmp_path / "ingest.py", _COMPOSITE_INGESTER)
    res = _check_t1_uniqueness(SchemaBundle(ingester_py=ing, source_csvs=[csv]))
    assert res.status == "pass", res.detail


def test_t1_loop_index_resource_does_not_false_fail(tmp_path: Path) -> None:
    """A descriptor keyed by a loop index is only partially resolvable; it must
    be reported in evidence but never cause a failure."""
    csv = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        """,
    )
    ing = _write(
        tmp_path / "ingest.py",
        """
        SDR = None
        def emit(row, g):
            sample_id = row.get("sample_id", "").strip()
            paper_sid = row.get("SID", "").strip()
            sample_key = f"{paper_sid}-{sample_id}"
            sample = SDR[f"sample/{sample_key}"]
            for i, d in enumerate(items):
                descriptor = SDR[f"descriptor/{sample_key}/{i}"]
        """,
    )
    res = _check_t1_uniqueness(SchemaBundle(ingester_py=ing, source_csvs=[csv]))
    assert res.status == "pass", res.detail
    assert any("descriptor" in e and "skipped" in e for e in res.evidence)


def test_t1_entity_routes_key_to_matching_csv(tmp_path: Path) -> None:
    """Regression: a single-column paper key (SID) is unique in papers.csv but
    repeats by design in samples.csv. The entity must route it to papers.csv so
    it PASSES — checking it against samples.csv would be a false positive."""
    papers = _write(
        tmp_path / "papers.csv",
        """
        SID,DOI
        1,10.1/a
        2,10.2/b
        3,10.3/c
        """,
    )
    samples = _write(
        tmp_path / "samples.csv",
        """
        SID,sample_id
        1,10
        2,10
        3,11
        """,
    )
    ing = _write(
        tmp_path / "ingest.py",
        """
        SDR = None
        def emit_paper(row):
            sid = row.get("SID", "").strip()
            return SDR[f"paper/{sid}"]
        def emit_sample(row):
            sid = row.get("SID", "").strip()
            sample_id = row.get("sample_id", "").strip()
            return SDR[f"sample/{sid}-{sample_id}"]
        """,
    )
    res = _check_t1_uniqueness(
        SchemaBundle(ingester_py=ing, source_csvs=[samples, papers])
    )
    assert res.status == "pass", res.detail
    assert any("papers.csv" in e and "sdr:paper" in e for e in res.evidence)


def test_t1_skipped_with_csv_but_no_schema(tmp_path: Path) -> None:
    csv = _write(tmp_path / "samples.csv", "SID,sample_id\n1,10\n")
    res = _check_t1_uniqueness(SchemaBundle(source_csvs=[csv]))
    assert res.status == "skip"


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


def test_t5_passes_on_rich_classdiagram_with_members_and_notes(tmp_path: Path) -> None:
    """The full ttl2mermaid shape (member blocks + note-for) must stay clean —
    guards against the new lint false-flagging valid output."""
    md = _write(
        tmp_path / "diagram.md",
        """
        ```mermaid
        classDiagram
            direction LR
            class Curve {
                +hasXUnit xsd_string
                +hasYUnit xsd_string
            }
            class Sample {
                +composition xsd_string
            }
            Sample --> Curve : measured
            note for Curve "subClassOf prov_Entity"
        ```
        """,
    )
    res = _check_t5_mermaid_escape(SchemaBundle(diagram_md=md))
    assert res.status == "pass", res.detail


# ---- classDiagram lint (warn-level, best-effort) --------------------------


def test_lint_clean_classdiagram_has_no_issues() -> None:
    block = (
        "classDiagram\n"
        "    direction LR\n"
        "    class Paper\n"
        "    class Sample\n"
        "    Sample --> Paper : fromPaper\n"
    )
    assert _lint_classdiagram(block) == []


def test_lint_flags_illegal_class_name_char() -> None:
    issues = _lint_classdiagram("classDiagram\n    class Thermal-Conductivity\n")
    assert any("Thermal-Conductivity" in i and "reject" in i for i in issues)


def test_lint_flags_invalid_relation_arrow() -> None:
    issues = _lint_classdiagram("classDiagram\n    A -> B\n")
    assert any("'->'" in i and "arrow" in i for i in issues)
    issues2 = _lint_classdiagram("classDiagram\n    A ==> B\n")
    assert any("'==>'" in i for i in issues2)


def test_lint_flags_member_colon() -> None:
    block = "classDiagram\n    class Sample {\n        +value: Float\n    }\n"
    issues = _lint_classdiagram(block)
    assert any("member" in i and "':'" in i for i in issues)


def test_lint_flags_unquoted_paren_label() -> None:
    issues = _lint_classdiagram("classDiagram\n    A --> B : has (mW/mK)\n")
    assert any("paren" in i or "bracket" in i for i in issues)


def test_lint_flags_missing_diagram_header() -> None:
    issues = _lint_classdiagram("    A --> B\n    C --> D\n")
    assert any("diagram header" in i for i in issues)


def test_lint_ignores_foreign_diagram_type() -> None:
    # A flowchart is a different grammar — the classDiagram lint stays quiet.
    assert _lint_classdiagram("flowchart TD\n    A --> B\n") == []


def test_lint_allows_cardinality_and_generics() -> None:
    block = (
        'classDiagram\n'
        '    Paper "1" --> "*" Sample : has\n'
        "    class Box~T~\n"
    )
    assert _lint_classdiagram(block) == []


def test_t5_warns_on_broken_fixture_diagram() -> None:
    """★ 2026-07-08 dogfood regression: an AI-generated diagram.md that Mermaid 11
    rejects (bomb icons in the UI) must surface as a non-blocking WARN, listing
    the specific breakage, without failing CI (ingest/promote are unaffected)."""
    md = FIXTURES / "mermaid_invalid" / "diagram.md"
    res = _check_t5_mermaid_escape(SchemaBundle(diagram_md=md))
    assert res.status == "warn", res.detail
    ev = "\n".join(res.evidence)
    assert "Thermal-Conductivity" in ev  # illegal class-name char
    assert "'->'" in ev  # malformed arrow
    assert "member" in ev  # +value: Float
    # warn is non-blocking: it must not push the whole report to a failure.
    report = validate_schema(SchemaBundle(diagram_md=md))
    assert report.exit_code() == 0


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


def test_validate_schema_returns_9_results(tmp_path: Path) -> None:
    bundle = SchemaBundle()  # everything skips
    report = validate_schema(bundle)
    assert len(report.results) == 9
    assert {r.trap_id for r in report.results} == {f"T{i}" for i in range(1, 10)}
    assert report.exit_code() == 0  # all skips, no failures


def test_validate_schema_exits_1_on_failure(tmp_path: Path) -> None:
    ing = _write(tmp_path / "ingest.py", 'open(p, encoding="utf-8")\n')  # T2 fails
    report = validate_schema(SchemaBundle(ingester_py=ing))
    assert report.exit_code() == 1
    assert any(r.trap_id == "T2" and r.status == "fail" for r in report.results)


# ----------------------------------------------------------------------------
# Trap T9: RML closed-set (declarative substrate). allowed_fn_iris is injected
# so these tests don't need the asterism (ingest) package installed.
# ----------------------------------------------------------------------------

_FN = "https://kumagallium.github.io/asterism/fn/"

_VALID_RML = (
    "@prefix rr:   <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix rmlf: <http://w3id.org/rml/> .\n"
    "@prefix fn:   <https://kumagallium.github.io/asterism/fn/> .\n"
    '<#M> a rr:TriplesMap ;\n'
    '  rml:logicalSource [ rml:source "c.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/{id}" ] ;\n'
    "  rr:predicateObjectMap [ rr:predicate <https://ex/m> ; rr:objectMap [\n"
    "      rmlf:functionExecution [ rmlf:function fn:float_array_max ;\n"
    '        rmlf:input [ rmlf:parameter fn:p_value ;\n'
    '          rmlf:inputValueMap [ rml:reference "y" ] ] ] ] ] .\n'
)


def test_t9_skips_without_rml() -> None:
    assert _check_t9_rml_closed_set(SchemaBundle()).status == "skip"


def test_t9_passes_when_all_functions_vetted(tmp_path: Path) -> None:
    rml = _write(tmp_path / "m.rml.ttl", _VALID_RML)
    allowed = {_FN + "float_array_max", _FN + "date_iso"}
    r = _check_t9_rml_closed_set(SchemaBundle(rml_ttl=rml), allowed_fn_iris=allowed)
    assert r.status == "pass", r.detail


def test_t9_fails_on_out_of_set_function(tmp_path: Path) -> None:
    rogue = _VALID_RML.replace("fn:float_array_max", "fn:run_shell")
    rml = _write(tmp_path / "m.rml.ttl", rogue)
    allowed = {_FN + "float_array_max"}
    r = _check_t9_rml_closed_set(SchemaBundle(rml_ttl=rml), allowed_fn_iris=allowed)
    assert r.status == "fail"
    assert any("run_shell" in e for e in r.evidence)


def test_t9_fails_on_malformed_turtle(tmp_path: Path) -> None:
    rml = _write(tmp_path / "bad.rml.ttl", "<<<< definitely not turtle")
    r = _check_t9_rml_closed_set(SchemaBundle(rml_ttl=rml), allowed_fn_iris=set())
    assert r.status == "fail"
    assert "parse" in r.detail.lower()


def test_t9_fails_when_rml_file_missing(tmp_path: Path) -> None:
    r = _check_t9_rml_closed_set(SchemaBundle(rml_ttl=tmp_path / "nope.ttl"))
    assert r.status == "fail"


def test_t9_flows_through_validate_schema(tmp_path: Path) -> None:
    rml = _write(tmp_path / "m.rml.ttl", _VALID_RML)
    report = validate_schema(
        SchemaBundle(rml_ttl=rml), allowed_fn_iris={_FN + "float_array_max"}
    )
    t9 = next(r for r in report.results if r.trap_id == "T9")
    assert t9.status == "pass"
    assert report.exit_code() == 0


def test_render_report_includes_glyphs_and_summary(tmp_path: Path) -> None:
    mie = _write(tmp_path / "mie.yaml", _BASE_MIE)
    report = validate_schema(SchemaBundle(mie_yaml=mie))
    md = render_report(report)
    assert "# Schema validation report" in md
    assert "**Summary**" in md
    # Glyph for at least one passed trap should appear
    assert "✓" in md or "·" in md


# ---------------------------------------------------------------------------
# broken (LLM-drafted) MIE YAML is a FINDING, never a crash
# ---------------------------------------------------------------------------


def test_unparseable_mie_yaml_is_a_fail_not_a_crash(tmp_path: Path) -> None:
    # The exact live failure: a weak model emitted a sparql_query_examples list
    # whose item breaks the YAML grammar; validate_schema inside /api/materialize
    # crashed with ParserError -> HTTP 500. Every MIE-reading trap check must
    # report it as a fixable finding instead.

    mie = tmp_path / "broken.mie.yaml"
    mie.write_text(
        "schema_info:\n"
        "  keywords: [a, b, c, d, e]\n"
        "sparql_query_examples:\n"
        '  - "Find the composition with the\n'  # unterminated quoted scalar
        "  - broken item\n",
        encoding="utf-8",
    )
    csv = _write(tmp_path / "d.csv", "SID\n1\n")
    bundle = SchemaBundle(mie_yaml=mie, source_csvs=[csv])

    for check in (_check_t4_keywords, _check_t6_fake_iri, _check_t7_rationale):
        res = check(bundle)  # must NOT raise
        assert res.status == "fail", (check.__name__, res.status)
        assert "not parseable YAML" in res.detail
        assert "§7" in res.detail  # tells the reviewer WHERE to fix


# ---------------------------------------------------------------------------
# Fix recipes: a failing trap must hand the AI/user a deterministic recipe —
# WHERE (section + YAML path), WHAT SHAPE, and a paste-ready example derived
# from the design itself (2026-07-14 live incident: the one-click AI fix looped
# forever on T4 because the comment carried only the symptom).
# ---------------------------------------------------------------------------


def _parse_t4_fix_yaml(fix: str) -> dict:
    """Parse the paste-ready YAML block that terminates a T4 fix recipe."""
    import yaml

    idx = fix.index("\nschema_info:")  # block starts at column 0
    return yaml.safe_load(fix[idx + 1 :])


# A T4-failing MIE (1 keyword, 0 categories) with fields worth preserving.
_T4_POOR_MIE = """
schema_info:
  title: Powder diffraction cards
  description: Example measurement set.
  base_uri: https://example.com/xr/
  keywords:
    - card
  categories: []
"""

_T4_MAPPING_IR = """
version: 1
prefixes:
  xr:  "https://example.com/xr#"
  xrr: "https://example.com/xr/resource/"
maps:
  - name: card
    source: cards.csv
    subject:
      template: "xrr:card/{card_id}"
      classes: [xr:Card]
    properties:
      - predicate: xr:peakAngle
        column: two_theta
      - predicate: xr:intensity
        column: intensity
"""


def test_t4_fail_fix_yaml_satisfies_thresholds_when_derivation_is_rich(
    tmp_path: Path,
) -> None:
    """★ The recipe's YAML block must ITSELF pass T4 when the design offers
    enough terms (title + Mermaid classes + §9 mapping IR): parse the block out
    of the fix and check the same thresholds the trap enforces."""
    mie = _write(tmp_path / "mie.yaml", _T4_POOR_MIE)
    diagram = _write(
        tmp_path / "diagram.md",
        """
        ```mermaid
        classDiagram
            class Card
            class Peak
            Peak --> Card : fromCard
        ```
        """,
    )
    ir = _write(tmp_path / "mapping.yaml", _T4_MAPPING_IR)
    res = _check_t4_keywords(
        SchemaBundle(mie_yaml=mie, diagram_md=diagram, mapping_ir_yaml=ir)
    )
    assert res.status == "fail"
    assert res.fix, "a T4 fail must carry a fix recipe"
    assert "§7" in res.fix  # WHERE: the section
    assert "keywords" in res.fix  # WHERE: the YAML path

    info = _parse_t4_fix_yaml(res.fix)["schema_info"]
    assert len(info["keywords"]) >= 5, info["keywords"]
    assert len(info["categories"]) >= 1, info["categories"]
    assert len(info["keywords"]) <= 8  # bounded, not a dump of everything
    # Paste-ready means lossless: the existing schema_info fields survive.
    assert info["title"] == "Powder diffraction cards"
    assert info["description"] == "Example measurement set."
    assert info["base_uri"] == "https://example.com/xr/"
    # No invention: every keyword traces back to the design's own text.
    design_text = (
        _T4_POOR_MIE + diagram.read_text(encoding="utf-8") + _T4_MAPPING_IR
    ).lower()
    for kw in info["keywords"]:
        assert str(kw).lower() in design_text, kw


def test_t4_fix_degenerate_states_thresholds_and_shortfall(tmp_path: Path) -> None:
    """Derivation-poor bundle (no title / diagram / IR / CSVs): the recipe must
    still say the thresholds and exactly how many terms the author must add —
    and must NOT invent keywords to pad the list."""
    mie = _write(
        tmp_path / "mie.yaml",
        """
        schema_info:
          keywords:
            - one
            - two
        """,
    )
    res = _check_t4_keywords(SchemaBundle(mie_yaml=mie))
    assert res.status == "fail"
    assert res.fix
    assert "5" in res.fix  # the keyword threshold, stated
    assert "add 3 more" in res.fix  # the exact shortfall instruction
    info = _parse_t4_fix_yaml(res.fix)["schema_info"]
    # What IS known is kept; nothing invented beyond the generic category slot.
    assert info["keywords"] == ["one", "two"]
    assert len(info["categories"]) >= 1


def test_t4_pass_has_empty_fix(tmp_path: Path) -> None:
    mie = _write(tmp_path / "mie.yaml", _BASE_MIE)
    res = _check_t4_keywords(SchemaBundle(mie_yaml=mie))
    assert res.status == "pass"
    assert res.fix == ""


def test_t4_fix_derives_from_rml_when_no_mapping_ir(tmp_path: Path) -> None:
    """Legacy bundles carry compiled/raw RML but no §9 IR — rr:class and
    rml:reference local names still feed the keyword pool."""
    mie = _write(tmp_path / "mie.yaml", "schema_info:\n  keywords: []\n")
    rml = _write(
        tmp_path / "m.rml.ttl",
        """
        @prefix rr:  <http://www.w3.org/ns/r2rml#> .
        @prefix rml: <http://semweb.mmlab.be/ns/rml#> .
        @prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
        @prefix ex:  <https://example.com/sn#> .
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "readings.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://ex/reading/{reading_id}" ;
            rr:class ex:Measurement ] ;
          rr:predicateObjectMap [ rr:predicate ex:temperature ;
            rr:objectMap [ rml:reference "temperature" ] ] .
        """,
    )
    res = _check_t4_keywords(SchemaBundle(mie_yaml=mie, rml_ttl=rml))
    assert res.status == "fail"
    keywords = [str(k).lower() for k in _parse_t4_fix_yaml(res.fix)["schema_info"]["keywords"]]
    assert "measurement" in keywords  # rr:class local name
    assert "temperature" in keywords  # rml:reference column


def test_t4_fix_derives_from_source_csv_headers(tmp_path: Path) -> None:
    """CSV file stems + header columns are the last-resort derivation source."""
    mie = _write(tmp_path / "mie.yaml", "schema_info:\n  keywords: []\n")
    csv = _write(tmp_path / "spectra.csv", "wavelength,absorbance,run_label\n1,2,x\n")
    res = _check_t4_keywords(SchemaBundle(mie_yaml=mie, source_csvs=[csv]))
    assert res.status == "fail"
    keywords = [str(k).lower() for k in _parse_t4_fix_yaml(res.fix)["schema_info"]["keywords"]]
    for term in ("spectra", "wavelength", "absorbance", "run_label"):
        assert term in keywords


def test_t1_fail_fix_names_entity_key_and_template_path(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "papers.csv",
        """
        SID,DOI
        1,10.1/a
        1,10.1/b
        """,
    )
    mie = _write(tmp_path / "mie.yaml", "shape_expressions: |\n  sdr:paper/{SID}\n")
    res = _check_t1_uniqueness(SchemaBundle(mie_yaml=mie, source_csvs=[csv]))
    assert res.status == "fail"
    assert "subject.template" in res.fix  # WHAT to edit
    assert "§9" in res.fix  # WHERE
    assert "paper" in res.fix  # the colliding entity, named
    assert "SID" in res.fix  # the current (insufficient) key, named


def test_t2_fail_fix_prescribes_utf8_sig(tmp_path: Path) -> None:
    ing = _write(tmp_path / "ingest.py", 'open(p, encoding="utf-8")\n')
    res = _check_t2_bom(SchemaBundle(ingester_py=ing))
    assert res.status == "fail"
    assert "utf-8-sig" in res.fix
    assert "§8" in res.fix


def test_t3_fail_fix_prescribes_iri_templates(tmp_path: Path) -> None:
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
    assert res.fix
    assert "template" in res.fix.lower()


def test_t5_fail_fix_points_to_diagram_labels(tmp_path: Path) -> None:
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
    assert "§1" in res.fix
    assert "colon" in res.fix.lower() or "':'" in res.fix


def test_t5_warn_lint_fix_points_to_diagram(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "diagram.md",
        "```mermaid\nclassDiagram\n    class Thermal-Conductivity\n```\n",
    )
    res = _check_t5_mermaid_escape(SchemaBundle(diagram_md=md))
    assert res.status == "warn"
    assert "§1" in res.fix


def test_t6_fail_fix_prescribes_verbatim_row_values(tmp_path: Path) -> None:
    csv = _write(tmp_path / "papers.csv", "SID,title\n6,Real\n")
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
    assert "sample_rdf_entries" in res.fix
    assert "§7" in res.fix


def test_t7_warn_fix_lists_missing_subsections(tmp_path: Path) -> None:
    mie = _write(
        tmp_path / "mie.yaml",
        """
        architectural_notes: |
          We chose composite keys. Why: collisions.
        """,
    )
    res = _check_t7_rationale(SchemaBundle(mie_yaml=mie))
    assert res.status == "warn"
    assert "architectural_notes" in res.fix
    # The recipe names the specific missing subsections from the trap's finding.
    assert "Alternatives" in res.fix and "Trade-offs" in res.fix


def test_t9_fail_fix_prescribes_tier0_closed_set(tmp_path: Path) -> None:
    rogue = _VALID_RML.replace("fn:float_array_max", "fn:run_shell")
    rml = _write(tmp_path / "m.rml.ttl", rogue)
    res = _check_t9_rml_closed_set(
        SchemaBundle(rml_ttl=rml), allowed_fn_iris={_FN + "float_array_max"}
    )
    assert res.status == "fail"
    assert "Tier 0" in res.fix
    assert "§9" in res.fix


def test_broken_mie_fix_prescribes_yaml_repair(tmp_path: Path) -> None:
    mie = tmp_path / "broken.mie.yaml"
    mie.write_text(
        'sparql_query_examples:\n  - "unterminated\n  - broken\n', encoding="utf-8"
    )
    csv = _write(tmp_path / "d.csv", "SID\n1\n")  # T6 needs a source to run at all
    bundle = SchemaBundle(mie_yaml=mie, source_csvs=[csv])
    for check in (_check_t4_keywords, _check_t6_fake_iri, _check_t7_rationale):
        res = check(bundle)
        assert res.status == "fail"
        assert "§7" in res.fix, check.__name__
        assert "YAML" in res.fix, check.__name__


def test_passing_traps_carry_no_fix(tmp_path: Path) -> None:
    """fix is a repair recipe — pass/skip results must leave it empty."""
    mie = _write(tmp_path / "mie.yaml", _BASE_MIE)
    report = validate_schema(SchemaBundle(mie_yaml=mie))
    for r in report.results:
        if r.status in {"pass", "skip"}:
            assert r.fix == "", (r.trap_id, r.status)


def test_render_report_appends_fix_sections(tmp_path: Path) -> None:
    """The CLI report surfaces each recipe in its own trailing section (the trap
    table row keeps only `detail`, so existing consumers are unaffected)."""
    mie = _write(
        tmp_path / "mie.yaml",
        "schema_info:\n  title: Tiny\n  keywords: [one]\n  categories: []\n",
    )
    report = validate_schema(SchemaBundle(mie_yaml=mie))
    md = render_report(report)
    assert "suggested fix" in md
    assert "schema_info:" in md
