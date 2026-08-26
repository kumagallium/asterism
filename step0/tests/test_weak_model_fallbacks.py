"""What happens when the model cannot finish — the deterministic floor.

Every case here is a real weak-model failure mode from the kantan audit: a
skeleton that is not JSON, a per-map answer that truncates, a §1-8 write-up the
model runs out of tokens on, a §7 with one bad quote, a §8 sketch missing a
keyword. Before these fixes each one ended the run (or silently emptied an
entity) and handed the person a stop card whose only exit was asking the same
model again. The assertions below are that the machine finishes the job instead,
using only facts it already holds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("yaml")

import yaml

from asterism_step0.llm import (
    LLMCancelledError,
    LLMEmptyOutputError,
    LLMTruncatedError,
    error_code,
)
from asterism_step0.staged_propose import (
    DOCUMENT_SYSTEM_PROMPT,
    PERMAP_LABELFILL_SYSTEM_PROMPT,
    PERMAP_SYSTEM_PROMPT,
    default_property_table,
    default_skeleton,
    menu_columns,
    propose_from_skeleton,
    propose_skeleton,
)

IR_BLOCK = """\
version: 1
prefixes:
  ex: "https://example.org/datasets/demo/ontology#"
  exr: "https://example.org/datasets/demo/resource/"
maps:
  - name: thing
    source: data.csv
    subject:
      template: "exr:thing/{id}"
      classes: [ex:Thing]
    properties:
      - predicate: ex:name
        column: name
"""

_SKELETON = {
    "version": 1,
    "prefixes": {
        "ex": "https://example.org/datasets/demo/ontology#",
        "exr": "https://example.org/datasets/demo/resource/",
    },
    "maps": [
        {
            "name": "thing",
            "source": "data.csv",
            "subject": {"template": "exr:thing/{id}", "classes": ["ex:Thing"]},
        },
        {
            "name": "part",
            "source": "parts.csv",
            "subject": {"template": "exr:part/{pid}", "classes": ["ex:Part"]},
        },
    ],
}

_MENU = (
    "── Reference (closed menu) ──\n"
    "Source files (use the filename EXACTLY as written):\n"
    "  • data.csv — columns: id, name, Resistivity(Ohm m)\n"
    "  • parts.csv — columns: pid, label\n"
)


def _proposal(*, sections: str) -> str:
    return (
        f"# Schema proposal\n\n{sections}\n"
        f"### 9. Declarative mapping spec\n\n```yaml\n{IR_BLOCK}```\n"
    )


class _Mock:
    """A scripted client with the guided attribute, per stage."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.response_schema = None
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str):
        self.calls.append((system_prompt, user_message))
        return self._handler(system_prompt, user_message)


# ---------------------------------------------------------------------------
# materialize: §6/§7/§8 synthesized from §9 (WEAK-MODEL-05/06/11)
# ---------------------------------------------------------------------------


def test_truncated_proposal_completes_from_the_mapping_spec(tmp_path: Path) -> None:
    """§9 present, §6/§7/§8 missing: complete, no blocking warning, notes explain."""
    pytest.importorskip("asterism.functions")
    from asterism_step0.materialize import materialize_schema

    result = materialize_schema(_proposal(sections=""), tmp_path, "demo", write=False)
    assert result.complete is True
    assert result.warnings == []
    assert len(result.notes) == 3
    assert result.rdf_config_model and "ex:Thing" in result.rdf_config_model
    assert result.ingester_py and "utf-8-sig" in result.ingester_py
    assert "BNode(" not in result.ingester_py
    mie = yaml.safe_load(result.mie_yaml or "")
    assert len(mie["schema_info"]["keywords"]) >= 5
    assert mie["schema_info"]["categories"] == ["dataset"]
    # Never a fabricated example row (T6's whole point).
    assert "sample_rdf_entries" not in mie


def test_broken_mie_is_rebuilt_not_reported_three_times(tmp_path: Path) -> None:
    pytest.importorskip("asterism.functions")
    from asterism_step0.materialize import materialize_schema

    broken = '### 7. MIE YAML extras\n\n```yaml\nschema_info:\n  title: "unterminated\n```\n\n'
    result = materialize_schema(_proposal(sections=broken), tmp_path, "demo", write=False)
    assert result.warnings == []
    assert any("not valid YAML" in n for n in result.notes)
    assert yaml.safe_load(result.mie_yaml or "")["schema_info"]["title"]


def test_short_keyword_list_is_stamped_keeping_the_author_s_words(tmp_path: Path) -> None:
    pytest.importorskip("asterism.functions")
    from asterism_step0.materialize import materialize_schema

    thin = (
        "### 7. MIE YAML extras\n\n```yaml\nschema_info:\n  title: things\n"
        "  keywords: [ceramics]\nanti_patterns: keep me\n```\n\n"
    )
    result = materialize_schema(_proposal(sections=thin), tmp_path, "demo", write=False)
    mie = yaml.safe_load(result.mie_yaml or "")
    assert mie["schema_info"]["keywords"][0] == "ceramics"  # verbatim, first
    assert len(mie["schema_info"]["keywords"]) >= 5
    assert mie["schema_info"]["title"] == "things"  # the author's title survives
    assert mie["anti_patterns"] == "keep me"  # other §7 keys are preserved
    assert result.warnings == []


def test_unrunnable_example_query_is_replaced_working_ones_are_not(tmp_path: Path) -> None:
    pytest.importorskip("asterism.functions")
    pytest.importorskip("pyoxigraph")
    from asterism_step0.materialize import materialize_schema

    good = "SELECT ?s WHERE { ?s ?p ?o } LIMIT 5"
    mie = (
        "### 7. MIE YAML extras\n\n```yaml\nschema_info:\n  title: things\n"
        "  keywords: [a, b, c, d, e]\n  categories: [dataset]\n"
        "sparql_query_examples:\n"
        "  - title: broken\n    query: SELEKT nonsense {{{\n"
        f"  - title: fine\n    query: {good}\n```\n\n"
    )
    result = materialize_schema(_proposal(sections=mie), tmp_path, "demo", write=False)
    examples = yaml.safe_load(result.mie_yaml or "")["sparql_query_examples"]
    assert examples[0]["query"] != "SELEKT nonsense {{{"
    assert examples[1]["query"] == good  # a query that parses is left alone
    assert any("example search" in n for n in result.notes)
    assert result.warnings == []


def test_synthesized_bundle_clears_the_traps_that_stop_the_wizard(tmp_path: Path) -> None:
    """The point of the whole cluster: no stop card is left behind.

    A design that arrives as §9 alone is materialized, then run through the REAL
    trap validator over the REAL files — the same call ``/api/materialize``
    makes. Any blocking failure here would be an S5 stop card whose only exit is
    another LLM round, which is exactly what the synthesis exists to prevent.
    """
    pytest.importorskip("asterism.functions")
    pytest.importorskip("pyoxigraph")
    from asterism_step0.materialize import materialize_schema
    from asterism_step0.validate import SchemaBundle, validate_schema

    out = tmp_path / "out"
    result = materialize_schema(_proposal(sections=""), out, "demo", write=True)
    paths = {k: Path(v) for k, v in result.written_paths.items()}
    report = validate_schema(
        SchemaBundle(
            diagram_md=paths.get("mermaid"),
            mie_yaml=paths.get("mie_yaml"),
            ingester_py=paths.get("ingester_py"),
            rml_ttl=paths.get("rml_ttl"),
            mapping_ir_yaml=paths.get("mapping_ir"),
        )
    )
    assert [r.trap_id for r in report.blocking_failures] == []


# ---------------------------------------------------------------------------
# validate: the §8 sketch is documentation on the declarative path (T2/T3)
# ---------------------------------------------------------------------------


def _bundle(tmp_path: Path, *, ingester: str, declarative: bool):
    from asterism_step0.validate import SchemaBundle

    py = tmp_path / "demo.py"
    py.write_text(ingester, encoding="utf-8")
    csv = tmp_path / "data.csv"
    csv.write_text("id,name\n1,a\n", encoding="utf-8")
    spec = None
    if declarative:
        spec = tmp_path / "demo-mapping.yaml"
        spec.write_text(IR_BLOCK, encoding="utf-8")
    return SchemaBundle(ingester_py=py, source_csvs=[csv], mapping_ir_yaml=spec)


def test_t2_missing_utf8_sig_in_sketch_is_a_warning_on_the_rml_path(tmp_path: Path) -> None:
    from asterism_step0.validate import _check_t2_bom

    sketch = "def read(path):\n    open(path, encoding='utf-8')\n"
    assert _check_t2_bom(_bundle(tmp_path, ingester=sketch, declarative=True)).status == "warn"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert _check_t2_bom(_bundle(legacy, ingester=sketch, declarative=False)).status == "fail"


def test_t2_fails_when_the_spec_itself_reads_a_bom_file_unsafely(tmp_path: Path) -> None:
    from asterism_step0.validate import SchemaBundle, _check_t2_bom

    csv = tmp_path / "data.csv"
    csv.write_bytes(b"\xef\xbb\xbfid,name\n1,a\n")
    spec = tmp_path / "demo-mapping.yaml"
    spec.write_text(IR_BLOCK + 'dialects:\n  data.csv:\n    encoding: "cp932"\n', encoding="utf-8")
    py = tmp_path / "demo.py"
    py.write_text('open(path, encoding="utf-8-sig")\n', encoding="utf-8")
    result = _check_t2_bom(SchemaBundle(ingester_py=py, source_csvs=[csv], mapping_ir_yaml=spec))
    assert result.status == "fail"
    assert "cp932" in " ".join(result.evidence)


def test_t3_bnode_in_the_sketch_is_a_warning_on_the_rml_path(tmp_path: Path) -> None:
    from asterism_step0.validate import _check_t3_bnode_free

    sketch = "from rdflib import BNode\n\ndef f():\n    return BNode()\n"
    rml = _check_t3_bnode_free(_bundle(tmp_path, ingester=sketch, declarative=True))
    assert rml.status == "warn"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert (
        _check_t3_bnode_free(_bundle(legacy, ingester=sketch, declarative=False)).status == "fail"
    )


# ---------------------------------------------------------------------------
# staged_propose: deterministic defaults (WEAK-MODEL-01/03/04/05)
# ---------------------------------------------------------------------------


def test_menu_columns_recovers_the_real_headers() -> None:
    assert menu_columns(_MENU) == {
        "data.csv": ["id", "name", "Resistivity(Ohm m)"],
        "parts.csv": ["pid", "label"],
    }


def test_default_property_table_carries_columns_units_and_types() -> None:
    table = default_property_table(
        ["id", "name", "Resistivity(Ohm m)", "shared"],
        ontology_prefix="ex",
        owned_elsewhere={"shared": "other"},
        column_types={"Resistivity(Ohm m)": "xsd:double"},
    )
    predicates = [p["predicate"] for p in table["properties"]]
    assert predicates == ["ex:id", "ex:name", "ex:resistivityOhmM"]  # borrowed one dropped
    rho = table["properties"][2]
    assert rho["column"] == "Resistivity(Ohm m)"
    assert rho["datatype"] == "xsd:double"
    assert rho["unit"] == "Ohm m"
    assert rho["label"] == "Resistivity"


def test_default_property_table_keeps_columns_that_slug_alike() -> None:
    """Punctuation-only differences must not silently cost a column."""
    table = default_property_table(["sample id", "sample_id"], ontology_prefix="ex")
    assert [(p["predicate"], p["column"]) for p in table["properties"]] == [
        ("ex:sampleId", "sample id"),
        ("ex:sampleId2", "sample_id"),
    ]


def test_fallback_is_skipped_when_the_design_has_no_namespace_of_its_own() -> None:
    """No minted prefix means no home for a predicate — invent nothing."""
    skeleton = {
        "version": 1,
        "prefixes": {},
        "maps": [{"name": "thing", "source": "data.csv", "subject": {"template": "x/{id}"}}],
    }

    def handler(system: str, user: str) -> str:
        if system == PERMAP_SYSTEM_PROMPT:
            raise LLMTruncatedError("truncated")
        return "### 1. Class hierarchy\n\n(the design)\n"

    md = propose_from_skeleton(skeleton, "# insp", "demo", llm=_Mock(handler), menu=_MENU)
    spec = yaml.safe_load(md.split("```yaml")[-1].split("```")[0])
    assert spec["maps"][0]["properties"] == []


def _write_sources(tmp_path: Path) -> list[Path]:
    a = tmp_path / "data.csv"
    a.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")
    b = tmp_path / "parts.csv"
    b.write_text("pid,label\np1,x\np2,y\n", encoding="utf-8")
    return [a, b]


def test_default_skeleton_keys_each_source_by_a_proven_column(tmp_path: Path) -> None:
    from asterism_step0.inspect import inspect_source_set

    inspections, _ = inspect_source_set(_write_sources(tmp_path))
    skeleton = default_skeleton(inspections, iri_base="https://x.test", dataset_name="demo")
    names = [m["name"] for m in skeleton["maps"]]
    assert names == ["data", "parts"]
    assert skeleton["maps"][0]["subject"]["template"] == "demor:data/{id}"
    assert skeleton["maps"][0]["subject"]["classes"] == ["demo:Data"]
    assert skeleton["prefixes"]["demo"] == "https://x.test/datasets/demo/ontology#"


def test_unreadable_skeleton_retries_then_falls_back(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path)
    llm = _Mock(lambda s, u: "not json at all: [")
    proposal = propose_skeleton(list(paths), "demo", llm=llm, iri_base="https://x.test")
    assert len(llm.calls) == 2  # one retry, then the deterministic floor
    assert "fix ONLY this" in llm.calls[1][1]
    assert proposal.metadata["fallback"] is True
    assert [m["name"] for m in proposal.skeleton["maps"]] == ["data", "parts"]


def test_skeleton_retry_that_parses_is_used(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path)
    replies = iter(["}{ broken", json.dumps(_SKELETON)])
    llm = _Mock(lambda s, u: next(replies))
    proposal = propose_skeleton(list(paths), "demo", llm=llm, iri_base="https://x.test")
    assert "fallback" not in proposal.metadata
    assert [m["name"] for m in proposal.skeleton["maps"]] == ["thing", "part"]


def test_provider_failure_is_never_turned_into_a_design(tmp_path: Path) -> None:
    """A dead AI must surface as a failure, not as a machine-written design.

    The deterministic floor stands in for an unusable ANSWER. A bad key or an
    unreachable endpoint means no model ran at all: falling back there would
    hide the one problem the person can actually fix (and would hand them a
    design they believe an AI made).
    """
    paths = _write_sources(tmp_path)

    class AuthenticationError(Exception):
        pass

    def handler(system: str, user: str) -> str:
        raise AuthenticationError("invalid api key")

    with pytest.raises(AuthenticationError):
        propose_skeleton(list(paths), "demo", llm=_Mock(handler), iri_base="https://x.test")


def test_provider_failure_during_a_map_is_not_a_default_table() -> None:
    class APIConnectionError(Exception):
        pass

    def handler(system: str, user: str) -> str:
        if system == PERMAP_SYSTEM_PROMPT:
            raise APIConnectionError("connection reset")
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        raise AssertionError(system)

    with pytest.raises(APIConnectionError):
        propose_from_skeleton(_SKELETON, "# insp", "demo", llm=_Mock(handler), menu=_MENU)


def test_cancel_during_the_skeleton_is_never_turned_into_a_design(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path)

    def handler(system: str, user: str) -> str:
        raise LLMCancelledError("cancelled")

    with pytest.raises(LLMCancelledError):
        propose_skeleton(list(paths), "demo", llm=_Mock(handler), iri_base="https://x.test")


def test_truncated_map_keeps_the_run_and_the_other_maps() -> None:
    """One map truncating used to end the whole continue job."""
    fallbacks: list[str] = []

    def handler(system: str, user: str) -> str:
        if system == PERMAP_SYSTEM_PROMPT:
            if "This map: 'thing'" in user:
                raise LLMTruncatedError("output was still truncated")
            return json.dumps({"properties": [{"predicate": "ex:label", "column": "label"}]})
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError(system)

    md = propose_from_skeleton(
        _SKELETON,
        "# insp",
        "demo",
        llm=_Mock(handler),
        menu=_MENU,
        column_types={"thing": {"id": "xsd:integer"}},
        on_fallback=fallbacks.append,
    )
    assert fallbacks == ["thing"]
    spec = yaml.safe_load(md.split("```yaml")[-1].split("```")[0])
    thing = next(m for m in spec["maps"] if m["name"] == "thing")
    part = next(m for m in spec["maps"] if m["name"] == "part")
    # the failed map keeps every column, sourced and typed
    assert [p["column"] for p in thing["properties"]] == ["id", "name", "Resistivity(Ohm m)"]
    assert thing["properties"][0]["datatype"] == "xsd:integer"
    # …and the map that DID generate is untouched
    assert [p["predicate"] for p in part["properties"]] == ["ex:label"]


def test_empty_answer_for_a_map_also_falls_back() -> None:
    def handler(system: str, user: str) -> str:
        if system == PERMAP_SYSTEM_PROMPT:
            raise LLMEmptyOutputError("only reasoning")
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        raise AssertionError(system)

    md = propose_from_skeleton(_SKELETON, "# insp", "demo", llm=_Mock(handler), menu=_MENU)
    spec = yaml.safe_load(md.split("```yaml")[-1].split("```")[0])
    assert all(m["properties"] for m in spec["maps"])


def test_document_truncation_is_written_from_the_spec() -> None:
    pytest.importorskip("asterism.functions")

    def handler(system: str, user: str) -> str:
        if system == PERMAP_SYSTEM_PROMPT:
            return json.dumps({"properties": [{"predicate": "ex:name", "column": "name"}]})
        if system == DOCUMENT_SYSTEM_PROMPT:
            raise LLMTruncatedError("output was still truncated")
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        raise AssertionError(system)

    fallbacks: list[str] = []
    md = propose_from_skeleton(
        _SKELETON,
        "# insp",
        "demo",
        llm=_Mock(handler),
        menu=_MENU,
        dataset_name="demo",
        on_fallback=fallbacks.append,
    )
    assert fallbacks == ["document"]
    for heading in ("### 1.", "### 6.", "### 7.", "### 8.", "### 9."):
        assert heading in md
    # …and the synthesized document materializes into all four core artifacts.
    from asterism_step0.materialize import materialize_schema

    result = materialize_schema(md, ".", "demo", write=False)
    assert result.complete is True
    assert result.warnings == []


# ---------------------------------------------------------------------------
# skeleton gate: a misspelled key column is fixable in one tap (WEAK-MODEL-33)
# ---------------------------------------------------------------------------


def test_missing_key_column_offers_the_near_name_and_the_proven_keys(tmp_path: Path) -> None:
    from asterism_step0.skeleton_annotate import annotate_skeleton

    source = tmp_path / "data.csv"
    source.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    skeleton = {
        "version": 1,
        "prefixes": {"exr": "https://example.org/datasets/demo/resource/"},
        "maps": [
            {
                "name": "sample",
                "source": "data.csv",
                "subject": {"template": "exr:sample/{sample_ID}", "classes": ["ex:Sample"]},
            }
        ],
    }
    ann = annotate_skeleton(skeleton, [source])["maps"]["sample"]
    assert ann["reason"] == "missing-columns"
    assert ann["column_suggestions"] == [{"column": "sample_ID", "suggestions": ["sample_id"]}]
    assert ["sample_id"] in [c["columns"] for c in ann["key_candidates"]]


# ---------------------------------------------------------------------------
# llm: a code the UI can translate, instead of English substring matching
# ---------------------------------------------------------------------------


def test_error_code_classifies_every_llm_failure() -> None:
    assert error_code(LLMTruncatedError("x")) == "llm.truncated"
    assert error_code(LLMEmptyOutputError("x")) == "llm.empty"
    assert error_code(LLMCancelledError("x")) == "llm.cancelled"
    reasoning = LLMEmptyOutputError("only reasoning")
    reasoning.reasoning_only = True
    assert error_code(reasoning) == "llm.reasoning_only"
    assert error_code(ValueError("x")) is None

    class RateLimitError(Exception):
        pass

    class ProviderRateLimit(RateLimitError):
        pass

    assert error_code(ProviderRateLimit()) == "llm.rate_limit"  # matched through the MRO
