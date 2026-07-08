"""Unit tests for the Phase 2b staged proposal (skeleton -> per-map -> document).

The pure pieces (assembly, serialization, the skeleton<->full-IR split, the §9
splice) are tested without any LLM; the generation wrappers and the two
orchestrators are driven by a scripted mock client. The headline test is
EQUIVALENCE: a full IR split into a skeleton + per-map tables and reassembled
must reproduce the exact same IR (ADR mapping-ir-phase2b-skeleton-wizard §10.1).
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("yaml")
jsonschema = pytest.importorskip("jsonschema")

from asterism_step0.mapping_ir import parse_mapping_ir  # noqa: E402
from asterism_step0.mapping_ir_schema import permap_json_schema, skeleton_json_schema  # noqa: E402
from asterism_step0.staged_propose import (  # noqa: E402
    DOCUMENT_SYSTEM_PROMPT,
    PERMAP_SYSTEM_PROMPT,
    SKELETON_SYSTEM_PROMPT,
    assemble_mapping_ir,
    fill_mapping_spec_block,
    generate_map_properties,
    generate_skeleton,
    mapping_ir_to_yaml,
    propose_from_skeleton,
    propose_skeleton,
    skeleton_from_full_ir,
)

FN_NAMES = ["date_iso", "iri_safe", "slug", "split"]

# A representative full IR: two linked maps, a Tier-0 function, a composite key,
# an object_template link, an extra prefix only a property uses (schema:).
FULL_IR: dict = {
    "version": 1,
    "prefixes": {
        "ex": "https://example.org/ns#",
        "exr": "https://example.org/r/",
        "schema": "https://schema.org/",
    },
    "maps": [
        {
            "name": "thing",
            "source": "data.csv",
            "subject": {"template": "exr:thing/{id}", "classes": ["ex:Thing"]},
            "properties": [
                {"predicate": "schema:name", "column": "name"},
                {
                    "predicate": "ex:when",
                    "column": "date",
                    "function": "date_iso",
                    "datatype": "xsd:date",
                },
            ],
        },
        {
            "name": "part",
            "source": "parts.csv",
            "subject": {"template": "exr:part/{id}-{pid}", "classes": ["ex:Part"]},
            "properties": [
                {"predicate": "ex:ofThing", "object_template": "exr:thing/{id}"},
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Schemas (guided-decoding contract)
# ---------------------------------------------------------------------------


def test_skeleton_schema_accepts_subject_only_map_and_rejects_properties() -> None:
    schema = skeleton_json_schema(FN_NAMES)
    v = jsonschema.Draft202012Validator(schema)
    skeleton, _ = skeleton_from_full_ir(FULL_IR)
    assert list(v.iter_errors(skeleton)) == []
    # A map carrying a property table is off-contract for the skeleton step.
    with_props = json.loads(json.dumps(skeleton))
    with_props["maps"][0]["properties"] = [{"predicate": "schema:name", "column": "name"}]
    assert list(v.iter_errors(with_props)) != []


def test_skeleton_schema_allows_note_and_iterator() -> None:
    v = jsonschema.Draft202012Validator(skeleton_json_schema(FN_NAMES))
    doc = {
        "version": 1,
        "prefixes": {"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},
        "maps": [
            {
                "name": "sec",
                "source": "paper.xml",
                "iterator": "/article/body/sec",
                "subject": {"constant": "exr:paper/1", "classes": ["ex:Section"]},
                "note": "per-document; @id keys the section",
            }
        ],
    }
    assert list(v.iter_errors(doc)) == []


def test_permap_schema_accepts_properties_and_optional_prefixes() -> None:
    v = jsonschema.Draft202012Validator(permap_json_schema(FN_NAMES))

    def errors(doc: dict) -> list:
        return list(v.iter_errors(doc))

    assert errors({"properties": [{"predicate": "schema:name", "column": "name"}]}) == []
    assert (
        errors(
            {
                "properties": [{"predicate": "ex:when", "column": "d", "function": "date_iso"}],
                "prefixes": {"qudt": "http://qudt.org/schema/qudt/"},
            }
        )
        == []
    )
    # off-menu function cannot even be represented (closed enum)
    assert errors({"properties": [{"predicate": "p", "column": "c", "function": "str"}]}) != []
    # unknown top-level key rejected
    assert errors({"properties": [], "subject": {}}) != []


# ---------------------------------------------------------------------------
# Pure assembly + the equivalence round-trip (the headline)
# ---------------------------------------------------------------------------


def test_skeleton_split_reassembles_to_the_same_ir() -> None:
    skeleton, permaps = skeleton_from_full_ir(FULL_IR)
    # skeleton maps carry no properties; permaps hold exactly the property tables
    assert all("properties" not in m for m in skeleton["maps"])
    assert permaps["thing"]["properties"] == FULL_IR["maps"][0]["properties"]
    # reassembly reproduces the original IR byte-for-byte (dict equality)
    assert assemble_mapping_ir(skeleton, permaps) == FULL_IR


def test_assemble_unions_prefixes_and_drops_note() -> None:
    skeleton = {
        "version": 1,
        "prefixes": {"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},
        "maps": [
            {
                "name": "thing",
                "source": "data.csv",
                "subject": {"template": "exr:thing/{id}", "classes": ["ex:Thing"]},
                "note": "dropped",
            }
        ],
    }
    permaps = {
        "thing": {
            "properties": [{"predicate": "qudt:unit", "column": "u"}],
            "prefixes": {"qudt": "http://qudt.org/schema/qudt/"},
        }
    }
    ir = assemble_mapping_ir(skeleton, permaps)
    assert ir["prefixes"]["qudt"] == "http://qudt.org/schema/qudt/"
    assert "note" not in ir["maps"][0]
    assert ir["maps"][0]["properties"] == [{"predicate": "qudt:unit", "column": "u"}]


def test_assemble_missing_permap_yields_empty_properties() -> None:
    skeleton, _ = skeleton_from_full_ir(FULL_IR)
    ir = assemble_mapping_ir(skeleton, {})  # no per-map results at all
    assert [m["properties"] for m in ir["maps"]] == [[], []]


def test_skeleton_prefix_wins_over_permap_on_conflict() -> None:
    skeleton = {
        "version": 1,
        "prefixes": {"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},
        "maps": [
            {
                "name": "t",
                "source": "d.csv",
                "subject": {"template": "exr:t/{id}", "classes": ["ex:T"]},
            }
        ],
    }
    permaps = {
        "t": {"properties": [{"predicate": "ex:p", "column": "c"}], "prefixes": {"ex": "https://EVIL/"}}
    }
    ir = assemble_mapping_ir(skeleton, permaps)
    assert ir["prefixes"]["ex"] == "https://example.org/ns#"


def test_assembled_ir_yaml_parses_and_validates() -> None:
    skeleton, permaps = skeleton_from_full_ir(FULL_IR)
    ir_yaml = mapping_ir_to_yaml(assemble_mapping_ir(skeleton, permaps))
    parsed = parse_mapping_ir(ir_yaml)  # raises MappingIRParseError on any problem
    assert [m.name for m in parsed.maps] == ["thing", "part"]
    assert parsed.prefixes["schema"] == "https://schema.org/"
    (thing, part) = parsed.maps
    assert thing.subject.template == "exr:thing/{id}"
    assert part.properties[0].object_template == "exr:thing/{id}"


# ---------------------------------------------------------------------------
# §9 splice: replace an existing block, or append when absent
# ---------------------------------------------------------------------------

_DOC_WITH_STUB_9 = """\
### 1. Class hierarchy

(diagram)

### 9. Declarative mapping spec

```yaml
version: 1
prefixes: {}
maps: []
```
"""

_DOC_WITHOUT_9 = """\
### 1. Class hierarchy

(diagram)

### 8. Ingester sketch

def load(): ...
"""


def _extract_spec(md: str) -> str:
    from asterism_step0.materialize import materialize_schema

    return materialize_schema(md, ".", "x", write=False).mapping_ir_yaml


def test_fill_replaces_existing_block_with_assembled_ir() -> None:
    ir_yaml = mapping_ir_to_yaml(FULL_IR)
    out = fill_mapping_spec_block(_DOC_WITH_STUB_9, ir_yaml)
    # the stub §9 is gone, replaced by our exact IR; §1 heading preserved
    assert "### 1. Class hierarchy" in out
    assert parse_mapping_ir(_extract_spec(out)).maps[0].name == "thing"


def test_fill_appends_section_when_absent() -> None:
    ir_yaml = mapping_ir_to_yaml(FULL_IR)
    out = fill_mapping_spec_block(_DOC_WITHOUT_9, ir_yaml)
    assert "### 9. Declarative mapping spec" in out
    assert "### 8. Ingester sketch" in out  # existing content untouched
    assert parse_mapping_ir(_extract_spec(out)).maps[0].name == "thing"


# ---------------------------------------------------------------------------
# Generation wrappers with a scripted mock client
# ---------------------------------------------------------------------------


class GuidedMock:
    """A scripted LLMClient that supports the guided ``response_schema`` attribute.
    Records the schema in force at each call so set/restore can be asserted."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.response_schema = None
        self.calls: list[tuple[str, str, object]] = []

    def complete(self, system_prompt: str, user_message: str):
        self.calls.append((system_prompt, user_message, self.response_schema))
        return self._handler(system_prompt, user_message)


class PlainMock:
    """A scripted client WITHOUT the guided attribute (the Anthropic path)."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str):
        self.calls.append((system_prompt, user_message))
        return self._handler(system_prompt, user_message)


def test_generate_skeleton_parses_and_sets_then_restores_schema() -> None:
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    llm = GuidedMock(lambda s, u: json.dumps(skeleton_obj))
    out = generate_skeleton("# insp", "# domain", llm=llm, function_names=FN_NAMES)
    assert out == skeleton_obj
    # the skeleton schema was in force during the call, then restored to None
    (_, _, schema_at_call) = llm.calls[0]
    assert schema_at_call == skeleton_json_schema(FN_NAMES)
    assert llm.response_schema is None


def test_generate_map_properties_guided_and_plain() -> None:
    permap = {"properties": [{"predicate": "schema:name", "column": "name"}]}
    guided = GuidedMock(lambda s, u: json.dumps(permap))
    out = generate_map_properties(
        "thing", FULL_IR["maps"][0], "ctx", "menu", llm=guided, function_names=FN_NAMES
    )
    assert out == permap
    assert guided.calls[0][2] == permap_json_schema(FN_NAMES)
    # a client without the attribute still works (prompt-contract path)
    plain = PlainMock(lambda s, u: json.dumps(permap))
    assert generate_map_properties("thing", FULL_IR["maps"][0], "ctx", "menu", llm=plain) == permap


def test_bad_model_output_raises_loop_feedable_error() -> None:
    llm = GuidedMock(lambda s, u: "not json at all: [")
    with pytest.raises(ValueError):
        generate_skeleton("# insp", "# domain", llm=llm, function_names=FN_NAMES)


# ---------------------------------------------------------------------------
# Orchestrators (scripted mock routing on the frozen system prompts)
# ---------------------------------------------------------------------------


def _router(skeleton_obj, permaps):
    """Route each stage's frozen prompt to the right scripted reply."""

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            # dispatch on the unique "This map: '<name>'" header (the skeleton
            # context lists every map, so a bare name would be ambiguous)
            for name, pm in permaps.items():
                if f"This map: '{name}'" in user:
                    return json.dumps(pm)
            raise AssertionError("per-map call for an unknown map")
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError("unexpected system prompt")

    return handler


def test_propose_skeleton_inspects_and_returns_skeleton(tmp_path) -> None:
    (tmp_path / "data.csv").write_text("id,name,date\n1,a,2020\n2,b,2021\n", encoding="utf-8")
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    llm = GuidedMock(lambda s, u: json.dumps(skeleton_obj))
    res = propose_skeleton([tmp_path / "data.csv"], "# domain", llm=llm, function_names=FN_NAMES)
    assert res.skeleton == skeleton_obj
    assert "data.csv" in res.csv_inspection_md
    assert res.metadata["llm_class"] == "GuidedMock"


def test_propose_from_skeleton_equivalence_and_progress() -> None:
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)
    llm = GuidedMock(_router(skeleton_obj, permaps))
    seen: list[str] = []
    md = propose_from_skeleton(
        skeleton_obj,
        "# inspection",
        "# domain",
        llm=llm,
        menu="menu",
        function_names=FN_NAMES,
        on_progress=lambda **d: seen.append(d["phase"]),
    )
    # per-map + document phases were emitted in order
    assert seen == ["map:thing", "map:part", "document"]
    # the produced §9 is exactly the reassembled IR == the original single-shot IR
    spec = parse_mapping_ir(_extract_spec(md))
    original = parse_mapping_ir(mapping_ir_to_yaml(FULL_IR))
    assert spec == original
