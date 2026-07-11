"""JSON Schema for the Mapping IR (guided decoding) + surgical spec repair.

The schema must ACCEPT every committed IR fixture (goldens + live dogfood
results) and REJECT the invention families observed live — those become
unrepresentable at generation time on guided servers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("yaml")
jsonschema = pytest.importorskip("jsonschema")
import yaml  # noqa: E402

from asterism_step0.mapping_ir_schema import mapping_ir_json_schema  # noqa: E402
from asterism_step0.spec_repair import (  # noqa: E402
    parse_spec_json,
    replace_mapping_spec_block,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "mapping_ir"

MENU = ["date_iso", "trim_collapse", "iri_safe", "slug", "split", "json_pluck",
        "json_array", "float_array_min", "float_array_max", "float_array_count",
        "datetime_iso", "structural_slug", "qudt_quantity", "qudt_unit", "lookup"]


def _validate(doc: dict, names=None) -> list[str]:
    schema = mapping_ir_json_schema(names)
    v = jsonschema.Draft202012Validator(schema)
    return [e.message for e in v.iter_errors(doc)]


def _fixture_docs():
    """The e2e golden + every CONVERGED live dogfood spec (the ones that
    compiled — a sibling .mapping.rml.ttl exists). Non-converged saved specs
    (gpt-oss run2 kept its optional:/cardinality inventions) are the schema's
    REJECTION fixtures instead — see the dedicated test below."""
    docs = [("e2e.yaml", yaml.safe_load((FIXTURES / "e2e.yaml").read_text(encoding="utf-8")))]
    results = REPO_ROOT / "experiments" / "mapping-ir-weakmodel-dogfood" / "results"
    for f in sorted(results.glob("*.mapping.yaml")) if results.exists() else []:
        if f.with_suffix("").with_suffix(".rml.ttl").exists() or (
            f.parent / (f.name.replace(".mapping.yaml", ".mapping.rml.ttl"))
        ).exists():
            docs.append((f.name, yaml.safe_load(f.read_text(encoding="utf-8"))))
    return docs


def test_schema_accepts_all_committed_ir_fixtures() -> None:
    for name, doc in _fixture_docs():
        errors = _validate(doc)  # registry-agnostic form (function as string)
        assert not errors, f"{name}: {errors[:3]}"


def test_schema_rejects_the_non_converged_live_spec() -> None:
    """gpt-oss run2's saved best design still carries optional: fields and
    cardinality-marked predicates — under guided decoding it could not even
    have been GENERATED. The strongest live evidence for Phase 2."""
    broken = (
        REPO_ROOT / "experiments" / "mapping-ir-weakmodel-dogfood" / "results"
        / "dogfood-gptoss-run2.mapping.yaml"
    )
    if not broken.exists():
        pytest.skip("dogfood results not present")
    errors = _validate(yaml.safe_load(broken.read_text(encoding="utf-8")))
    assert any("optional" in e for e in errors)
    assert any("does not match" in e for e in errors)  # schema:author* etc.


def test_schema_accepts_live_dogfood_specs_under_menu_enum() -> None:
    pytest.importorskip("asterism.functions")
    from asterism_step0.mapping_ir import catalog_from_registry

    names = catalog_from_registry().names()
    for name, doc in _fixture_docs():
        errors = _validate(doc, names)
        assert not errors, f"{name}: {errors[:3]}"


def _minimal(**prop) -> dict:
    row = {"predicate": "ex:name", **prop}
    return {
        "version": 1,
        "prefixes": {"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},
        "maps": [{
            "name": "thing", "source": "data.csv",
            "subject": {"template": "exr:thing/{id}", "classes": ["ex:Thing"]},
            "properties": [row],
        }],
    }


def test_schema_rejects_the_live_invention_families() -> None:
    # unknown field (optional:) — live report #1
    assert _validate(_minimal(column="name", optional=True))
    # type-cast pseudo-function — live report #2 (menu enum)
    assert _validate(_minimal(column="name", function="str"), MENU)
    # cardinality-marked predicate — live report #0
    doc = _minimal(column="name")
    doc["maps"][0]["properties"][0]["predicate"] = "schema:author*"
    assert _validate(doc)
    # clean row passes both forms
    assert not _validate(_minimal(column="name"))
    assert not _validate(_minimal(column="name", function="trim_collapse"), MENU)


# ---------------------------------------------------------------------------
# spec repair pieces
# ---------------------------------------------------------------------------


def test_parse_spec_json_accepts_bare_fenced_and_yaml() -> None:
    doc = {"version": 1, "prefixes": {}, "maps": []}
    bare = json.dumps(doc)
    assert "version: 1" in parse_spec_json(bare)
    fenced = f"```json\n{bare}\n```"
    assert "version: 1" in parse_spec_json(fenced)
    as_yaml = "version: 1\nprefixes: {}\nmaps: []\n"
    assert "version: 1" in parse_spec_json(as_yaml)
    with pytest.raises(ValueError):
        parse_spec_json("sorry, here is prose without structure: [")
    with pytest.raises(ValueError):
        parse_spec_json('"just a string"')


def test_replace_mapping_spec_block_splices_only_section_nine() -> None:
    doc = (
        "# Title\n\n### 1. Class hierarchy\n\n```mermaid\nclassDiagram\n```\n\n"
        "### 9. Declarative mapping spec\n\n```yaml\nversion: 1\nprefixes: {}\n"
        "maps: []\n```\n\n### tail\nprose stays\n"
    )
    out = replace_mapping_spec_block(doc, "version: 1\nprefixes: {}\nmaps: [1]\n")
    assert "maps: [1]" in out
    assert "maps: []" not in out
    assert out.startswith("# Title")
    assert "prose stays" in out
    with pytest.raises(ValueError):
        replace_mapping_spec_block("# no spec here\n", "x: 1")


def test_schema_accepts_dialects_and_stays_propertynames_free() -> None:
    doc = _minimal(column="name")
    doc["maps"][0]["source"] = "xrd.txt"
    doc["dialects"] = {"xrd.txt": {"encoding": "cp932", "delimiter": "\t", "skip_rows": 1}}
    assert not _validate(doc)
    # off-spec dialect fields cannot be generated
    doc["dialects"]["xrd.txt"]["skip_rows"] = -1
    assert _validate(doc)
    doc["dialects"]["xrd.txt"] = {"codepage": "cp932"}
    assert _validate(doc)
    # the guided-decoding constraint holds everywhere (Sakura vLLM)
    assert "propertyNames" not in json.dumps(mapping_ir_json_schema())
