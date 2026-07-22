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


def test_document_prompt_nudges_t4_categories() -> None:
    """kantan mode goes through the staged path, so a categories-blind §7 prompt is
    the direct cause of T4 failing with categories=0 (task #8 ①). Both keywords
    AND categories must be nudged."""
    assert "keywords" in DOCUMENT_SYSTEM_PROMPT
    assert "categories" in DOCUMENT_SYSTEM_PROMPT
    assert "T4" in DOCUMENT_SYSTEM_PROMPT


def test_permap_prompt_warns_against_transform_nesting() -> None:
    """The per-map prompt must carry the anti-pattern for the observed weak-model
    breakage: nesting function/args inside transform (task #8 ②)."""
    assert "NEVER nest" in PERMAP_SYSTEM_PROMPT
    assert "single-input function" in PERMAP_SYSTEM_PROMPT
    # every row must be told it needs one object form directly under predicate
    assert "no object form is rejected" in PERMAP_SYSTEM_PROMPT


def test_propose_from_skeleton_repairs_structural_permap() -> None:
    """A per-map result whose rows are structurally broken (object form nested in
    `transform:`) is regenerated with the issues fed back, and the repaired, clean
    table lands in §9 (ADR phase2b §4 — per-map self-correction, wired here)."""
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    broken = {
        "properties": [
            {"predicate": "schema:name", "transform": {"function": "slug", "args": {}}}
        ]
    }
    fixed = {"properties": [{"predicate": "schema:name", "column": "name"}]}
    thing_calls = 0

    def handler(system: str, user: str) -> str:
        nonlocal thing_calls
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            if "This map: 'thing'" in user:
                thing_calls += 1
                # first shot broken; the retry (issues fed back) is clean
                return json.dumps(fixed if "Issues to fix" in user else broken)
            # 'part' is clean on the first shot
            return json.dumps(
                {"properties": [{"predicate": "ex:ofThing", "object_template": "exr:thing/{id}"}]}
            )
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(design)\n"
        raise AssertionError("unexpected system prompt")

    llm = GuidedMock(handler)
    records: list[str] = []
    md = propose_from_skeleton(
        skeleton_obj,
        "# insp",
        "# dom",
        llm=llm,
        menu="menu",
        function_names=FN_NAMES,
        on_llm_call=records.append,
    )
    # 'thing' was generated twice: initial (broken) + one repair (issues fed back)
    assert thing_calls == 2
    # the repair round carried the structural issues back to the model
    thing_users = [
        u for (s, u, _sch) in llm.calls if s == PERMAP_SYSTEM_PROMPT and "This map: 'thing'" in u
    ]
    assert any("Issues to fix" in u for u in thing_users)
    # the repaired, clean row (not the broken transform) landed in the final §9
    spec = parse_mapping_ir(_extract_spec(md))
    thing = next(m for m in spec.maps if m.name == "thing")
    assert thing.properties[0].column == "name"
    # every LLM call was recorded: thing initial + thing repair + part + document
    assert records == ["propose", "propose", "propose", "propose"]


def test_propose_from_skeleton_permap_repair_stops_on_no_progress() -> None:
    """A map the model cannot improve (identical broken output on retry) must stop
    the moment a round fails to reduce the structural issue count — no thrashing
    through the whole round budget, no crash. The gap is then left for the
    assembly-stage validation / §9 surgical repair, as before."""
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    broken = {
        "properties": [
            {"predicate": "schema:name", "transform": {"function": "slug", "args": {}}}
        ]
    }
    thing_calls = 0

    def handler(system: str, user: str) -> str:
        nonlocal thing_calls
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            if "This map: 'thing'" in user:
                thing_calls += 1
                return json.dumps(broken)  # never improves
            return json.dumps({"properties": [{"predicate": "ex:ofThing", "column": "id"}]})
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(design)\n"
        raise AssertionError("unexpected system prompt")

    llm = GuidedMock(handler)
    md = propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=llm, menu="menu", function_names=FN_NAMES
    )
    # initial + exactly ONE repair round: it made no progress, so the loop stops
    # immediately rather than burning the remaining round budget (anti-thrash).
    assert thing_calls == 2
    # the run still completed and produced a document (the broken map's gap is left
    # for the assembly-stage parse / §9 surgical repair, not this per-map gate).
    assert "### 9. Declarative mapping spec" in md


def test_propose_from_skeleton_permap_repair_is_bounded_when_improving() -> None:
    """When each retry STRICTLY improves but never reaches clean, the loop is still
    bounded by _PERMAP_STRUCTURAL_ROUNDS (no unbounded regeneration)."""
    from asterism_step0.staged_propose import _PERMAP_STRUCTURAL_ROUNDS

    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    # Three broken rows; each retry drops one but leaves the row(s) still broken,
    # so structural issues strictly shrink round to round without ever hitting 0.
    broken_row = {"predicate": "schema:name", "transform": {"function": "slug", "args": {}}}
    ladder = [
        {"properties": [dict(broken_row), dict(broken_row), dict(broken_row)]},
        {"properties": [dict(broken_row), dict(broken_row)]},
        {"properties": [dict(broken_row)]},
    ]
    thing_calls = 0

    def handler(system: str, user: str) -> str:
        nonlocal thing_calls
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            if "This map: 'thing'" in user:
                reply = ladder[min(thing_calls, len(ladder) - 1)]
                thing_calls += 1
                return json.dumps(reply)
            return json.dumps({"properties": [{"predicate": "ex:ofThing", "column": "id"}]})
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(design)\n"
        raise AssertionError("unexpected system prompt")

    llm = GuidedMock(handler)
    propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=llm, menu="menu", function_names=FN_NAMES
    )
    # initial + at most _PERMAP_STRUCTURAL_ROUNDS retries, never more
    assert thing_calls == 1 + _PERMAP_STRUCTURAL_ROUNDS


def test_propose_from_skeleton_degrades_on_unparseable_permap() -> None:
    """A per-map call returning truncated/invalid JSON must NOT crash the run
    (observed live with gpt-oss-120b): that map degrades to no properties and the
    others are intact, so the assembled IR surfaces the gap to validation."""
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            if "This map: 'part'" in user:
                return '{"properties": [{"predicate": "ex:ofThing", "object_templa'  # truncated
            return json.dumps(permaps["thing"])
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(design)\n"
        raise AssertionError("unexpected system prompt")

    llm = GuidedMock(handler)
    warnings: list[str] = []
    md = propose_from_skeleton(
        skeleton_obj,
        "# insp",
        "# dom",
        llm=llm,
        menu="menu",
        function_names=FN_NAMES,
        on_progress=lambda **d: warnings.append(str(d.get("message", ""))),
    )
    spec_yaml = _extract_spec(md)  # the run completed — no exception propagated
    assert "predicate: schema:name" in spec_yaml  # the good 'thing' map survived
    assert "name: part" in spec_yaml  # the bad map is present, degraded
    assert any("失敗" in w for w in warnings)  # a per-map failure was surfaced


# ----------------------------------------------------------------------------
# Instance IRI base (ADR instance-iri-base.md)
# ----------------------------------------------------------------------------


def test_skeleton_user_message_pins_instance_namespace() -> None:
    from asterism_step0.staged_propose import build_skeleton_user

    msg = build_skeleton_user("# insp", "# domain", iri_base="https://data.lab.jp/asterism")
    assert "https://data.lab.jp/asterism/datasets/<slug>/ontology#" in msg
    assert "https://data.lab.jp/asterism/datasets/<slug>/resource/" in msg
    # Unset -> the .invalid default rides in, so generation is never base-less.
    assert "https://asterism.invalid/datasets/<slug>/ontology#" in build_skeleton_user(
        "# insp", "# domain"
    )


def test_skeleton_system_prompt_stays_frozen_without_a_base() -> None:
    # The base rides the user message (#244 pattern); the cacheable system
    # prompt must not embed a per-instance value.
    assert "asterism.invalid" not in SKELETON_SYSTEM_PROMPT


def test_skeleton_context_lists_settled_prefixes() -> None:
    from asterism_step0.staged_propose import render_skeleton_context

    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    ctx = render_skeleton_context(skeleton_obj)
    for name, iri in FULL_IR["prefixes"].items():
        assert f"prefix {name}: <{iri}>" in ctx
