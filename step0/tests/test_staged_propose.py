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
    PERMAP_LABELFILL_SYSTEM_PROMPT,
    PERMAP_SYSTEM_PROMPT,
    SKELETON_SYSTEM_PROMPT,
    apply_data_facts,
    apply_numeric_datatypes,
    assemble_mapping_ir,
    build_permap_user,
    drop_borrowed_properties,
    fill_mapping_spec_block,
    generate_map_properties,
    generate_skeleton,
    mapping_ir_to_yaml,
    merge_label_fill,
    missing_label_rows,
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
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        raise AssertionError("unexpected system prompt")

    return handler


def test_propose_skeleton_inspects_and_normalizes_namespaces(tmp_path) -> None:
    (tmp_path / "data.csv").write_text("id,name,date\n1,a,2020\n2,b,2021\n", encoding="utf-8")
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)
    llm = GuidedMock(lambda s, u: json.dumps(skeleton_obj))
    res = propose_skeleton([tmp_path / "data.csv"], "# domain", llm=llm, function_names=FN_NAMES)
    # The model's example.org mints (FULL_IR's ex:/exr:) come back repaired
    # (ADR K13): canonical shape under the instance base, prefix pair derived
    # from the slug (first source's stem when nothing recognizable was minted),
    # CURIEs renamed in lockstep. Reused vocabularies pass through.
    assert res.skeleton["prefixes"] == {
        "data": "https://asterism.invalid/datasets/data/ontology#",
        "datar": "https://asterism.invalid/datasets/data/resource/",
        "schema": "https://schema.org/",
    }
    assert res.skeleton["maps"][0]["subject"] == {
        "template": "datar:thing/{id}",
        "classes": ["data:Thing"],
    }
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
    # per-map + document phases were emitted in order (the label-fill round
    # may emit extra events under the same per-map phase — order is the contract)
    assert list(dict.fromkeys(seen)) == ["map:thing", "map:part", "document"]
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
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
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
    # every LLM call was recorded: thing initial + thing repair + thing
    # label-fill + part + part label-fill + document (both final tables lack
    # labels, so each map gets its one targeted fill round)
    assert records == ["propose"] * 6


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
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
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
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
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
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
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


# ---------------------------------------------------------------------------
# ADR column-ownership-and-growth G6: the gate's ownership verdict reaches
# generation — named in the prompt, and enforced on the way out.
# ---------------------------------------------------------------------------


def test_owned_columns_are_named_in_the_permap_prompt() -> None:
    """The system prompt states the rule in general; the per-call message names
    the actual columns, because a weak model broke the general rule in real
    dogfood (13 instrument columns written onto both maps)."""
    user = build_permap_user(
        "peak",
        {"source": "card.txt", "subject": {"template": "xr:peak/{No}/{(hkl)}", "classes": []}},
        "ctx",
        "menu",
        owned_elsewhere={"Name": "sample", "Cell": "sample"},
    )
    assert "Columns owned by ANOTHER map" in user
    assert "`Cell` → owned by map 'sample'" in user
    assert "`Name` → owned by map 'sample'" in user
    # …and nothing appears when there is no verdict (byte-identical to before).
    assert "Columns owned by ANOTHER map" not in build_permap_user(
        "peak",
        {"source": "card.txt", "subject": {"template": "xr:peak/{No}", "classes": []}},
        "ctx",
        "menu",
    )


def test_borrowed_plain_properties_are_dropped_but_links_survive() -> None:
    """Asking is a request; this is the guarantee. A borrowed column copied as a
    plain property is removed, while the join that USES it (object_template) and
    a genuine multi-input derivation stay untouched."""
    result = {
        "properties": [
            {"predicate": "xo:name", "column": "Name"},  # borrowed → dropped
            {"predicate": "xo:twoTheta", "column": "2theta"},  # its own → kept
            {"predicate": "xo:ofSample", "object_template": "xr:sample/{No}"},  # join → kept
            {"predicate": "xo:combined", "columns": ["Name", "2theta"], "function": "concat"},
        ]
    }
    cleaned, dropped = drop_borrowed_properties(result, {"Name": "sample", "No": "sample"})
    assert dropped == ["Name"]
    predicates = [p["predicate"] for p in cleaned["properties"]]
    assert predicates == ["xo:twoTheta", "xo:ofSample", "xo:combined"]
    # No verdict → the result is returned unchanged.
    assert drop_borrowed_properties(result, None) == (result, [])


def test_owned_columns_reach_generation_and_are_enforced() -> None:
    """End to end through the staged path: the constraint rides the prompt, and a
    model that ignores it still cannot write the borrowed column into §9."""
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            if "This map: 'part'" in user:
                # The model transcribes the owned column anyway.
                return json.dumps(
                    {
                        "properties": [
                            {"predicate": "schema:name", "column": "name"},
                            {"predicate": "ex:ofThing", "object_template": "exr:thing/{id}"},
                        ]
                    }
                )
            return json.dumps({"properties": [{"predicate": "schema:name", "column": "name"}]})
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(design)\n"
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        raise AssertionError("unexpected system prompt")

    llm = GuidedMock(handler)
    md = propose_from_skeleton(
        skeleton_obj,
        "# insp",
        "# dom",
        llm=llm,
        menu="menu",
        function_names=FN_NAMES,
        column_owners={"part": {"name": "thing"}},
    )
    part_user = next(
        u for (s, u, _sch) in llm.calls if s == PERMAP_SYSTEM_PROMPT and "This map: 'part'" in u
    )
    assert "`name` → owned by map 'thing'" in part_user
    spec = parse_mapping_ir(_extract_spec(md))
    part = next(m for m in spec.maps if m.name == "part")
    assert [p.predicate for p in part.properties] == ["ex:ofThing"]  # transcription removed
    # The owner keeps it.
    thing = next(m for m in spec.maps if m.name == "thing")
    assert [p.column for p in thing.properties] == ["name"]


# ---------------------------------------------------------------------------
# Numeric datatypes: an untyped number compares as TEXT in SPARQL.
# ---------------------------------------------------------------------------


def test_numeric_columns_get_their_datatype_stamped() -> None:
    """Observed live: "which angle has the highest intensity?" answered 77.47°
    (intensity 9.4) instead of 40.07° (intensity 100.0), because "9.4" sorts
    above "100.0" as text. The inspector knows the column is numeric, so the
    machine stamps the type instead of hoping the model remembered."""
    result = {
        "properties": [
            {"predicate": "xo:intensity", "column": "intensity"},
            {"predicate": "xo:hkl", "column": "(hkl)"},  # not numeric — untouched
            {"predicate": "xo:d", "column": "d", "datatype": "xsd:decimal"},  # explicit wins
            {"predicate": "xo:when", "column": "date", "function": "date_iso"},  # fn owns it
            {"predicate": "xo:src", "column": "url", "object_type": "iri"},  # not a literal
        ]
    }
    types = dict.fromkeys(["intensity", "d", "date", "url"], "xsd:double")
    typed_result, typed = apply_numeric_datatypes(result, types)
    assert typed == ["intensity"]
    rows = {p["predicate"]: p for p in typed_result["properties"]}
    assert rows["xo:intensity"]["datatype"] == "xsd:double"
    assert "datatype" not in rows["xo:hkl"]
    assert rows["xo:d"]["datatype"] == "xsd:decimal"  # the model's explicit choice stands
    assert "datatype" not in rows["xo:when"]
    assert "datatype" not in rows["xo:src"]
    # No verdict -> unchanged.
    assert apply_numeric_datatypes(result, None) == (result, [])


def test_numeric_datatypes_reach_generation() -> None:
    """End to end: a model that omits the datatype still produces a typed §9."""
    skeleton_obj, _ = skeleton_from_full_ir(FULL_IR)

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            return json.dumps({"properties": [{"predicate": "schema:name", "column": "name"}]})
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(design)\n"
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return json.dumps({"labels": []})  # 空振り (挙動中立)
        raise AssertionError("unexpected system prompt")

    md = propose_from_skeleton(
        skeleton_obj,
        "# insp",
        "# dom",
        llm=GuidedMock(handler),
        menu="menu",
        function_names=FN_NAMES,
        column_types={"thing": {"name": "xsd:double"}},
    )
    spec = parse_mapping_ir(_extract_spec(md))
    thing = next(m for m in spec.maps if m.name == "thing")
    assert thing.properties[0].datatype == "xsd:double"
    # The other map had no verdict — untouched.
    part = next(m for m in spec.maps if m.name == "part")
    assert all(p.datatype is None for p in part.properties)


def test_apply_data_facts_reasserts_ownership_and_types_on_a_whole_ir() -> None:
    """The live failure: round-0 typed the numbers and dropped the borrowed
    columns, then an autocorrect round rewrote §9 from memory — 0 datatypes and
    the parent's columns back on the child. Re-asserting on the assembled IR is
    what makes the machine's facts survive any later LLM round."""
    ir = {
        "version": 1,
        "prefixes": {"xo": "https://x/#", "xr": "https://x/r/"},
        "maps": [
            {
                "name": "sample",
                "source": "card.txt",
                "subject": {"template": "xr:sample/{No}", "classes": ["xo:Material"]},
                "properties": [
                    {"predicate": "xo:volume", "column": "Volume"},  # numeric, untyped
                    {"predicate": "xo:name", "column": "Name"},
                ],
            },
            {
                "name": "peak",
                "source": "card.txt",
                "subject": {"template": "xr:peak/{No}/{(hkl)}", "classes": ["xo:Peak"]},
                "properties": [
                    {"predicate": "xo:intensity", "column": "I"},  # numeric, untyped
                    {"predicate": "xo:name", "column": "Name"},  # borrowed — came back
                    {"predicate": "xo:ofSample", "object_template": "xr:sample/{No}"},
                ],
            },
        ],
    }
    out, changed = apply_data_facts(
        ir,
        column_owners={"peak": {"Name": "sample"}},
        column_types={"sample": {"Volume": "xsd:double"}, "peak": {"I": "xsd:double"}},
    )
    assert changed == {"sample": ["Volume"], "peak": ["Name", "I"]}
    sample, peak = out["maps"]
    assert {p["column"]: p.get("datatype") for p in sample["properties"]} == {
        "Volume": "xsd:double",
        "Name": None,
    }
    assert [p.get("column") or p.get("object_template") for p in peak["properties"]] == [
        "I",
        "xr:sample/{No}",  # the join survives; the transcription is gone
    ]
    assert peak["properties"][0]["datatype"] == "xsd:double"
    # Idempotent: a second pass changes nothing.
    again = apply_data_facts(
        out,
        column_owners={"peak": {"Name": "sample"}},
        column_types={"sample": {"Volume": "xsd:double"}, "peak": {"I": "xsd:double"}},
    )
    assert again == (out, {})
    # No verdicts → untouched.
    assert apply_data_facts(ir) == (ir, {})


# ---------------------------------------------------------------------------
# Label-fill round (④「読み取った意味」が出たり出なかったりするブレの対策):
# 構造は完璧でも label を落とす弱いモデルに、欠けた label だけを 1 回だけ
# 狙い撃ちで聞き直す。行そのものは再生成しないので binding は壊れない。
# ---------------------------------------------------------------------------


def test_missing_labels_get_one_targeted_fill_round() -> None:
    """Rows that came back without a label get ONE focused re-ask, and the
    returned labels land in the final §9. The fill user message names the row's
    binding so the model can infer the meaning from the data, not the name."""
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)
    fill_calls: list[str] = []

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            for name, pm in permaps.items():
                if f"This map: '{name}'" in user:
                    return json.dumps(pm)
            raise AssertionError("per-map call for an unknown map")
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            fill_calls.append(user)
            if "# Map: 'thing'" in user:
                return json.dumps(
                    {
                        "labels": [
                            {"predicate": "schema:name", "label": "試料の名前"},
                            {"predicate": "ex:when", "label": "測定日"},
                        ]
                    }
                )
            return json.dumps(
                {"labels": [{"predicate": "ex:ofThing", "label": "この部品が属するもの"}]}
            )
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError("unexpected system prompt")

    md = propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=GuidedMock(handler),
        menu="menu", function_names=FN_NAMES,
    )
    # one fill round per map with missing labels — no more
    assert len(fill_calls) == 2
    # the ask names the binding (meaning inferred from data, not the name alone)
    thing_ask = next(u for u in fill_calls if "# Map: 'thing'" in u)
    assert "predicate: schema:name  (column: name)" in thing_ask
    assert "predicate: ex:when  (column: date)" in thing_ask
    # the filled labels are in the final spec; bindings are untouched
    spec = parse_mapping_ir(_extract_spec(md))
    thing = next(m for m in spec.maps if m.name == "thing")
    by_pred = {p.predicate: p for p in thing.properties}
    assert by_pred["schema:name"].label == "試料の名前"
    assert by_pred["schema:name"].column == "name"
    assert by_pred["ex:when"].label == "測定日"
    part = next(m for m in spec.maps if m.name == "part")
    assert part.properties[0].label == "この部品が属するもの"


def test_label_fill_skips_when_labels_complete() -> None:
    """A table whose rows all carry labels asks NOTHING extra — the fill round
    costs a call only when a label is actually missing."""
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)
    labeled = {
        name: {
            "properties": [
                {**prop, "label": f"意味 {i}"} for i, prop in enumerate(pm["properties"])
            ]
        }
        for name, pm in permaps.items()
    }
    fill_calls = 0

    def handler(system: str, user: str) -> str:
        nonlocal fill_calls
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            for name, pm in labeled.items():
                if f"This map: '{name}'" in user:
                    return json.dumps(pm)
            raise AssertionError("per-map call for an unknown map")
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            fill_calls += 1
            return json.dumps({"labels": []})
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError("unexpected system prompt")

    propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=GuidedMock(handler),
        menu="menu", function_names=FN_NAMES,
    )
    assert fill_calls == 0


def test_label_fill_failure_keeps_blanks_and_run_continues() -> None:
    """An unusable fill answer (unreadable JSON) never kills the run: the rows
    keep their blank labels — S6's human gate stays the safety net — and the
    document stage still happens."""
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            for name, pm in permaps.items():
                if f"This map: '{name}'" in user:
                    return json.dumps(pm)
            raise AssertionError("per-map call for an unknown map")
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            return "sorry, not json ["
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError("unexpected system prompt")

    md = propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=GuidedMock(handler),
        menu="menu", function_names=FN_NAMES,
    )
    spec = parse_mapping_ir(_extract_spec(md))
    thing = next(m for m in spec.maps if m.name == "thing")
    assert all(not p.label for p in thing.properties)  # blanks stay, run completed


def test_merge_label_fill_rules() -> None:
    """The merge fills EMPTY labels only, ignores unknown predicates, and rejects
    a label that merely restates the identifier (predicate local name or column
    header) — a machine-written restatement would hide the blank instead of
    resolving it (that choice is S6's human button)."""
    table = {
        "properties": [
            {"predicate": "ex:kept", "column": "temp", "label": "既にある意味"},
            {"predicate": "ex:blank", "column": "Chemical Formula"},
            {"predicate": "ex:acronym", "column": "CSD"},
            {"predicate": "ex:restate", "column": "Space Group"},
            {"predicate": "ex:localEcho", "column": "z"},
        ]
    }
    merged, filled = merge_label_fill(
        table,
        {
            "labels": [
                {"predicate": "ex:kept", "label": "上書きしようとする意味"},
                {"predicate": "ex:blank", "label": "化学式"},
                {"predicate": "ex:acronym", "label": "CSD 収載コード"},
                {"predicate": "ex:restate", "label": "space group"},  # 列名の言い直し
                {"predicate": "ex:localEcho", "label": "Local echo"},  # 述語の言い直し
                {"predicate": "ex:unknown", "label": "どこにも無い行"},
            ]
        },
    )
    props = {p["predicate"]: p for p in merged["properties"]}
    assert props["ex:kept"]["label"] == "既にある意味"  # no overwrite
    assert props["ex:blank"]["label"] == "化学式"
    # 列の頭字語を含む日本語 label は言い直しではない(ASCII だけ残す正規化だと
    # "CSD 収載コード"→"csd" となり棄却された — 実 e2e 2026-08-26 の退行固定)
    assert props["ex:acronym"]["label"] == "CSD 収載コード"
    assert "label" not in props["ex:restate"]  # 列名の言い直しは意味ではない
    assert "label" not in props["ex:localEcho"]  # 述語 localEcho の言い直し
    assert filled == ["ex:blank", "ex:acronym"]
    # missing_label_rows: label の無い実 binding 行だけを拾う
    rows = missing_label_rows(table["properties"])
    assert [r["predicate"] for r in rows] == [
        "ex:blank",
        "ex:acronym",
        "ex:restate",
        "ex:localEcho",
    ]


# ---- 同じマップの中の二重記録（2026-08-27 実データ: XRD reference file）--------


def test_drop_duplicate_properties_removes_the_second_record_of_one_cell() -> None:
    """同じ列・同じ読み方の 2 行目は落ちる。述語が違っても「二重に書いた」ことは同じ。"""
    from asterism_step0.staged_propose import drop_duplicate_properties

    table = {
        "properties": [
            {"predicate": "xrd:dSpacing", "column": "d", "unit": "Å", "datatype": "xsd:double",
             "label": "格子間隔 d"},
            {"predicate": "xrd:twoTheta", "column": "2theta", "unit": "°",
             "datatype": "xsd:double", "label": "2θ角"},
            # 述語まで同じ = そのまま同じトリプル
            {"predicate": "xrd:twoTheta", "column": "2theta", "unit": "°",
             "datatype": "xsd:double", "label": "2θ (重複)"},
            # 述語だけ違う = 同じ値が 2 つの名前で保存される（本当の害）
            {"predicate": "xrd:d", "column": "d", "unit": "Å", "datatype": "xsd:double",
             "label": "d spacing (重複)"},
        ]
    }
    out, dropped = drop_duplicate_properties(table)
    assert sorted(dropped) == ["2theta", "d"]
    assert [p["predicate"] for p in out["properties"]] == ["xrd:dSpacing", "xrd:twoTheta"]
    # 先に書かれた行の答えが残る（後から来た行が上書きしない）
    assert out["properties"][0]["label"] == "格子間隔 d"
    assert out["properties"][1]["label"] == "2θ角"


def test_drop_duplicate_properties_keeps_a_genuinely_different_view() -> None:
    """読み方（function / datatype）が違えば、同じ列の 2 行目は別の事実。落とさない。"""
    from asterism_step0.staged_propose import drop_duplicate_properties

    table = {
        "properties": [
            {"predicate": "ex:raw", "column": "d"},
            {"predicate": "ex:num", "column": "d", "function": "number_clean",
             "datatype": "xsd:double"},
            # リンクと計算値は転記ではないので、そもそも対象外
            {"predicate": "prov:wasDerivedFrom", "object_template": "exr:sample/{No}"},
            {"predicate": "ex:both", "columns": ["d", "I"], "function": "join_nonempty"},
        ]
    }
    out, dropped = drop_duplicate_properties(table)
    assert dropped == []
    assert len(out["properties"]) == 4


def test_drop_duplicate_properties_fills_a_blank_meaning_from_its_twin() -> None:
    """勝った行に意味・単位が無いときだけ、落とす行から借りる。"""
    from asterism_step0.staged_propose import drop_duplicate_properties

    table = {
        "properties": [
            {"predicate": "xrd:intensity", "column": "I", "datatype": "xsd:double"},
            {"predicate": "xrd:I", "column": "I", "datatype": "xsd:double",
             "label": "強度", "unit": "cps"},
        ]
    }
    out, dropped = drop_duplicate_properties(table)
    assert dropped == ["I"]
    assert out["properties"] == [
        {"predicate": "xrd:intensity", "column": "I", "datatype": "xsd:double",
         "label": "強度", "unit": "cps"}
    ]


def test_apply_data_facts_removes_duplicates_after_a_rewriting_round() -> None:
    """§9 を丸ごと書き直すラウンドの後でも、二重記録は毎回落ちる（N6 と同じ理由）。"""
    from asterism_step0.staged_propose import apply_data_facts

    ir = {
        "version": 1,
        "prefixes": {},
        "maps": [
            {
                "name": "peak",
                "source": "xrd.txt",
                "subject": {"template": "xr:peak/{No}", "classes": ["xo:Peak"]},
                "properties": [
                    {"predicate": "xo:dSpacing", "column": "d", "label": "格子間隔 d"},
                    {"predicate": "xo:d", "column": "d", "label": "d spacing (重複)"},
                ],
            }
        ],
    }
    out, changed = apply_data_facts(ir)
    assert changed == {"peak": ["d"]}
    assert [p["predicate"] for p in out["maps"][0]["properties"]] == ["xo:dSpacing"]
    # 冪等: もう一度かけても変わらない
    again, changed2 = apply_data_facts(out)
    assert changed2 == {}
    assert again["maps"][0]["properties"] == out["maps"][0]["properties"]


def test_skeleton_schema_requires_a_kind_name() -> None:
    """骨格の contract は「1 件が表すもの」を必ず持たせる — 名前を付けるのが
    いちばん安いのは、列がまだ目の前にある骨格の段だから。"""
    from asterism_step0.mapping_ir_schema import mapping_ir_json_schema, skeleton_json_schema

    subject = skeleton_json_schema()["properties"]["maps"]["items"]["properties"]["subject"]
    assert subject["properties"]["classes"]["minItems"] == 1
    # 完成した IR 側は据え置き（あとから足した規則で、保存済みの設計を読めなくしない）
    full = mapping_ir_json_schema()["properties"]["maps"]["items"]["properties"]["subject"]
    assert "minItems" not in full["properties"]["classes"]


def test_name_unnamed_kinds_uses_the_models_own_map_name() -> None:
    """「1 件が表すもの」が空のまま返ったら、機械がモデル自身の map 名から置く。"""
    from asterism_step0.staged_propose import name_unnamed_kinds

    skeleton = {
        "version": 1,
        "prefixes": {"xo": "https://ns.invalid/o#", "xr": "https://ns.invalid/r/"},
        "maps": [
            {"name": "peak", "source": "a.csv",
             "subject": {"template": "xr:peak/{No}", "classes": []}},
            {"name": "xrd_dataset", "source": "a.csv", "subject": {"constant": "xr:d"}},
            {"name": "sample", "source": "a.csv",
             "subject": {"template": "xr:sample/{No}", "classes": ["xo:試料"]}},
        ],
    }
    out, named = name_unnamed_kinds(skeleton, ontology_prefix="xo")
    assert named == ["peak", "xrd_dataset"]
    assert [m["subject"]["classes"] for m in out["maps"]] == [
        ["xo:Peak"], ["xo:XrdDataset"], ["xo:試料"],
    ]
    # 名前が揃っていれば何も足さない（冪等）
    again, named2 = name_unnamed_kinds(out, ontology_prefix="xo")
    assert named2 == []
    assert again["maps"] == out["maps"]


def test_twin_maps_finds_one_row_type_described_twice() -> None:
    """同じソースを同じ鍵で数える 2 つの map は、1 つの種類を二度書いたもの。"""
    from asterism_step0.staged_propose import twin_maps

    skeleton = {
        "maps": [
            {"name": "dataset", "source": "xrd.txt", "subject": {"template": "xr:dataset/{No}"}},
            {"name": "sample", "source": "xrd.txt", "subject": {"template": "xr:sample/{No}"}},
            # 鍵が違えば別の粒度（親と行）— 一緒にしない
            {"name": "peak", "source": "xrd.txt",
             "subject": {"template": "xr:peak/{No}/{(hkl)}"}},
            # ソースが違えば別
            {"name": "other", "source": "b.txt", "subject": {"template": "xr:other/{No}"}},
        ]
    }
    assert twin_maps(skeleton) == [["dataset", "sample"]]


def test_twin_maps_is_quiet_on_a_healthy_skeleton() -> None:
    from asterism_step0.staged_propose import twin_maps

    assert twin_maps({"maps": [
        {"name": "sample", "source": "a.csv", "subject": {"template": "r:sample/{id}"}},
        {"name": "meas", "source": "a.csv", "subject": {"template": "r:meas/{id}/{t}"}},
    ]}) == []
