"""Unit tests for the Phase 2b staged proposal (skeleton -> per-map -> document).

The pure pieces (assembly, serialization, the skeleton<->full-IR split, the §9
splice) are tested without any LLM; the generation wrappers and the two
orchestrators are driven by a scripted mock client. The headline test is
EQUIVALENCE: a full IR split into a skeleton + per-map tables and reassembled
must reproduce the exact same IR (ADR mapping-ir-phase2b-skeleton-wizard §10.1).
"""
# このファイルの散文は日本語。全角の括弧・記号は意図したもので、ASCII の
# 見間違いではない（id_move.py / describe.py と同じ流儀）。
# ruff: noqa: RUF002, RUF003
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("yaml")
jsonschema = pytest.importorskip("jsonschema")

from asterism_step0.inspect import ColumnSummary, SourceInspection  # noqa: E402
from asterism_step0.mapping_ir import parse_mapping_ir  # noqa: E402
from asterism_step0.mapping_ir_schema import (  # noqa: E402
    column_meanings_json_schema,
    permap_json_schema,
    skeleton_json_schema,
)
from asterism_step0.staged_propose import (  # noqa: E402
    COLUMN_MEANINGS_SYSTEM_PROMPT,
    DOCUMENT_SYSTEM_PROMPT,
    PERMAP_LABELFILL_SYSTEM_PROMPT,
    PERMAP_SYSTEM_PROMPT,
    SKELETON_SYSTEM_PROMPT,
    apply_column_meanings,
    apply_data_facts,
    apply_numeric_datatypes,
    assemble_mapping_ir,
    build_permap_user,
    build_skeleton_user,
    drop_borrowed_properties,
    ensure_same_source_links,
    ensure_value_catalog_labels,
    fill_mapping_spec_block,
    generate_column_meanings,
    generate_map_properties,
    generate_skeleton,
    mapping_ir_to_yaml,
    merge_label_fill,
    missing_label_rows,
    normalize_column_meanings,
    propose_from_skeleton,
    propose_skeleton,
    render_columns_for_meanings,
    render_standard_class_names,
    skeleton_from_full_ir,
    standard_class_names,
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


# ---- 同じマップの中の二重記録(2026-08-27 実データ: XRD reference file)--------


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
            # 述語だけ違う = 同じ値が 2 つの名前で保存される(本当の害)
            {"predicate": "xrd:d", "column": "d", "unit": "Å", "datatype": "xsd:double",
             "label": "d spacing (重複)"},
        ]
    }
    out, dropped = drop_duplicate_properties(table)
    assert sorted(dropped) == ["2theta", "d"]
    assert [p["predicate"] for p in out["properties"]] == ["xrd:dSpacing", "xrd:twoTheta"]
    # 先に書かれた行の答えが残る(後から来た行が上書きしない)
    assert out["properties"][0]["label"] == "格子間隔 d"
    assert out["properties"][1]["label"] == "2θ角"


def test_drop_duplicate_properties_keeps_a_genuinely_different_view() -> None:
    """読み方(function / datatype)が違えば、同じ列の 2 行目は別の事実。落とさない。"""
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
    """§9 を丸ごと書き直すラウンドの後でも、二重記録は毎回落ちる(N6 と同じ理由)。"""
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
    # 完成した IR 側は据え置き(あとから足した規則で、保存済みの設計を読めなくしない)
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
    # 名前が揃っていれば何も足さない(冪等)
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
            # 鍵が違えば別の粒度(親と行)— 一緒にしない
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


def test_skeleton_retry_never_loses_kinds(tmp_path: Path) -> None:
    """差し戻しは「抜けを足して」と頼むもの。減らして返ってきたら前の答えを採る。

    実測 2026-08-27: 重複を「keep one」と伝えたら、モデルは重複した 3 つの
    まとまりを全部落として map を 1 つにし、前置きの 14 列が 47 行すべてに写った。
    """
    from asterism_step0.staged_propose import propose_skeleton

    csv = tmp_path / "a.csv"
    csv.write_text("id,name\n1,x\n2,y\n", encoding="utf-8")

    full = {
        "version": 1,
        "prefixes": {"ao": "https://ns.invalid/o#", "ar": "https://ns.invalid/r/"},
        "maps": [
            # 名前が無い = 差し戻しの対象
            {"name": "row", "source": "a.csv", "subject": {"template": "ar:row/{id}"}},
            {"name": "thing", "source": "a.csv",
             "subject": {"template": "ar:thing/{name}", "classes": ["ao:Thing"]}},
        ],
    }
    shrunk = {**full, "maps": [dict(full["maps"][0], subject={
        "template": "ar:row/{id}", "classes": ["ao:Row"]})]}

    answers = [json.dumps(full), json.dumps(shrunk), json.dumps(shrunk)]

    class _LLM:
        model = "mock"
        response_schema = None

        def complete(self, system: str, user: str) -> str:
            return answers.pop(0) if answers else json.dumps(shrunk)

    out = propose_skeleton([csv], "", llm=_LLM())
    assert len(out.skeleton["maps"]) == 2, out.skeleton["maps"]


def test_normalize_key_separators_puts_a_slash_between_key_columns() -> None:
    """`{a}_{b}` は 1 つの区間に融合して住所を曖昧にする。`/` に揃える。"""
    from asterism_step0.staged_propose import normalize_key_separators

    out, changed = normalize_key_separators({
        "maps": [
            {"name": "peak", "subject": {"template": "xr:peak/{No}_{2theta}"}},
            {"name": "fused", "subject": {"template": "xr:x/{a}{b}"}},
            # 意図した経路は触らない
            {"name": "path", "subject": {"template": "xr:sample/{id}/measurement/{t}"}},
            {"name": "one", "subject": {"template": "xr:one/{id}"}},
        ]
    })
    assert changed == ["peak", "fused"]
    assert [m["subject"]["template"] for m in out["maps"]] == [
        "xr:peak/{No}/{2theta}",
        "xr:x/{a}/{b}",
        "xr:sample/{id}/measurement/{t}",
        "xr:one/{id}",
    ]


# --- propose_from_skeleton(deterministic=True): §9 は判断から機械で組む（2026-09-02） ---


def test_propose_from_skeleton_deterministic_uses_no_llm() -> None:
    """かんたん経路の設計の続き（ADR deterministic-design-assembly）: 性質表も
    文書も決定論で組み、LLM を一切呼ばない。今日までの失敗（ファイル名・列名・
    dialects・IRI 化の発明）は全部この段の自由度から来た。"""

    class Boom:
        def complete(self, *args: object, **kwargs: object) -> str:
            raise AssertionError("deterministic path must not call the LLM")

    skeleton = {
        "version": 1,
        "prefixes": {
            "el": "https://example.org/datasets/elements/ontology#",
            "elr": "https://example.org/datasets/elements/resource/",
        },
        "maps": [
            {
                "name": "record",
                "source": "elements.csv",
                "subject": {"template": "elr:record/{name}", "classes": ["el:Record"]},
                "owns": ["atomic_mass"],
            },
            {
                "name": "symbol",
                "source": "elements.csv",
                "subject": {"template": "elr:symbol/{symbol}", "classes": ["el:Symbol"]},
                "owns": ["symbol"],
            },
        ],
    }
    md = propose_from_skeleton(
        skeleton,
        "",
        "",
        llm=Boom(),  # type: ignore[arg-type]
        deterministic=True,
        map_columns={
            "record": ["name", "atomic_mass", "symbol", "junk"],
            "symbol": ["name", "atomic_mass", "symbol", "junk"],
        },
        column_owners={
            "record": {"symbol": "symbol"},
            "symbol": {"name": "record", "atomic_mass": "record"},
        },
        column_types={"record": {"atomic_mass": "xsd:double"}},
        column_meanings=[
            {"source": "elements.csv", "column": "atomic_mass", "label": "原子量", "unit": "u"}
        ],
        excluded_columns={"elements.csv": ["junk"]},
        dataset_name="elements",
    )
    from asterism_step0.materialize import materialize_schema

    ir_yaml = materialize_schema(md, ".", "t", write=False).mapping_ir_yaml
    assert ir_yaml is not None
    import yaml

    ir = yaml.safe_load(ir_yaml)
    by_name = {m["name"]: m for m in ir["maps"]}
    rec_props = {p["column"]: p for p in by_name["record"]["properties"]}
    # 除外列は載らない・他の持ち物（symbol）も載らない
    assert "junk" not in rec_props and "symbol" not in rec_props
    # 型は検査から・意味は人の確定が勝つ
    assert rec_props["atomic_mass"]["datatype"] == "xsd:double"
    assert rec_props["atomic_mass"]["label"] == "原子量"
    assert rec_props["atomic_mass"]["unit"] == "u"
    # 受け口は自分の値のリテラル（ラベル保証パス）を持つ
    sym_props = [p for p in by_name["symbol"]["properties"] if p.get("column") == "symbol"]
    assert any(p.get("predicate") == "rdfs:label" or "label" in p for p in sym_props)
    # 文書は合成（§ 見出しがある = doc_synth 経路）
    assert "### 9." in md


# --- ensure_value_catalog_labels: 受け口は自分の値をリテラルで持つ（2026-09-02） ---


def test_value_catalog_gets_its_label_literal() -> None:
    """空の入れ物への助言（行の列を足せ）は持ち物の強制と板挟みでモデルには
    実行できない（実測: 元素表 JSON の Number が 4 ラウンド不動）。書くべき直しは
    ラベルのリテラル化で、決定論で書く。IRI 化された自分の値はリテラルと
    数えない。"""
    # ⭐実配線と同じ形: owns は assemble_mapping_ir(_clean_map) で IR から落ちる
    #   [parse_mapping_ir が unknown field を拒む] ので、骨格側から owns_by_map で
    #   渡す。最初の実装は IR の owns を読んで no-op だった [実測 2026-09-02]。
    skeleton = {
        "version": 1,
        "prefixes": {"x": "https://example.org/x#"},
        "maps": [
            {
                "name": "number",
                "source": "elements.csv",
                "subject": {"template": "xr:number/{number}"},
                "owns": ["number"],
            },
            {
                "name": "record",
                "source": "elements.csv",
                "subject": {"template": "xr:record/{name}"},
            },
        ],
    }
    permaps = {
        "number": {
            "properties": [
                {"column": "number", "predicate": "x:sameAs", "object_type": "iri"}
            ]
        },
        "record": {"properties": [{"column": "name", "predicate": "rdfs:label"}]},
    }
    ir = assemble_mapping_ir(skeleton, permaps)
    assert "owns" not in ir["maps"][0]  # _clean_map が落とす — この前提が破れたら設計変更
    owns_by_map = {m["name"]: list(m.get("owns") or []) for m in skeleton["maps"]}
    out, added = ensure_value_catalog_labels(ir, owns_by_map)
    assert added == ["number"]
    number = next(m for m in out["maps"] if m["name"] == "number")
    assert {"column": "number", "predicate": "rdfs:label"} in number["properties"]
    assert out["prefixes"]["rdfs"] == "http://www.w3.org/2000/01/rdf-schema#"
    # record [カタログでない — name 直参照のリテラルを持つ] は触らない・冪等
    out2, added2 = ensure_value_catalog_labels(out, owns_by_map)
    assert added2 == []
    assert out2 == out
    # owns の無い空シェル [LLM/旧経路] も救済: リテラル皆無の単独キー map
    shell = {
        "version": 1,
        "prefixes": {},
        "maps": [
            {
                "name": "Phase",
                "source": "elements.csv",
                "subject": {"template": "xr:phase/{phase}"},
                "properties": [
                    {"predicate": "x:of", "object_template": "xr:record/{name}"}
                ],
            }
        ],
    }
    _out3, added3 = ensure_value_catalog_labels(shell)
    assert added3 == ["Phase"]


# --- ensure_same_source_links: 1 ファイルの種類は 1 つにつながる（2026-08-27） ---

def _xrd_islands() -> dict:
    """The XRD reference card as a weak model leaves it: a row kind, the card
    it came from, and the registry code — none of them linked."""
    return {
        "version": 1,
        "prefixes": {"xrd": "https://ex.invalid/o#", "xrdr": "https://ex.invalid/r/"},
        "maps": [
            {
                "name": "pattern",
                "source": "card.txt",
                "subject": {"template": "xrdr:pattern/{No}", "classes": ["xrd:Pattern"]},
                "properties": [{"predicate": "xrd:csdId", "column": "CSD"}],
            },
            {
                "name": "peak",
                "source": "card.txt",
                "subject": {"template": "xrdr:peak/{No}/{2theta}", "classes": ["xrd:Peak"]},
                "properties": [{"predicate": "xrd:twoTheta", "column": "2theta"}],
            },
            {
                "name": "csd",
                "source": "card.txt",
                "subject": {"template": "xrdr:csd/{CSD}", "classes": ["xrd:Csd"]},
                "properties": [],
            },
        ],
    }


def test_links_are_added_in_both_provable_ways() -> None:
    out, added = ensure_same_source_links(_xrd_islands(), ontology_prefix="xrd")
    assert added == ["peak → pattern", "pattern → csd"]
    by_name = {m["name"]: m for m in out["maps"]}
    # (1) containment: the child carries the link, never the parent.
    assert {
        "predicate": "dcterms:isPartOf",
        "object_template": "xrdr:pattern/{No}",
    } in by_name["peak"]["properties"]
    # (2) a key column carried as a value: the literal row STAYS, the link is
    # added next to it — nothing the human confirmed disappears.
    assert {"predicate": "xrd:csdId", "column": "CSD"} in by_name["pattern"]["properties"]
    assert {
        "predicate": "xrd:csd",
        "object_template": "xrdr:csd/{CSD}",
    } in by_name["pattern"]["properties"]
    # A predicate under an undeclared prefix would not compile.
    assert out["prefixes"]["dcterms"] == "http://purl.org/dc/terms/"


def test_links_are_idempotent_and_quiet_when_already_connected() -> None:
    once, _ = ensure_same_source_links(_xrd_islands(), ontology_prefix="xrd")
    twice, added_again = ensure_same_source_links(once, ontology_prefix="xrd")
    assert added_again == []
    assert twice["maps"] == once["maps"]


def test_links_stay_silent_without_evidence() -> None:
    """Two file-scoped kinds sharing no key and no column: a relationship here
    would be invented, and the ingest-side connectivity advisory is the right
    place for a design the machine cannot repair."""
    ir = {
        "version": 1,
        "prefixes": {"o": "https://ex.invalid/o#"},
        "maps": [
            {"name": "a", "source": "c", "subject": {"template": "r:a/{k}"}, "properties": []},
            {"name": "b", "source": "c", "subject": {"template": "r:b/{j}"}, "properties": []},
        ],
    }
    out, added = ensure_same_source_links(ir, ontology_prefix="o")
    assert added == []
    assert out["maps"] == ir["maps"]


def test_links_never_cross_sources() -> None:
    """Two unrelated files are allowed to stay two pieces (propose.py's own
    rule: one connected component 'unless the sources are truly unrelated')."""
    ir = {
        "version": 1,
        "prefixes": {"o": "https://ex.invalid/o#"},
        "maps": [
            {"name": "a", "source": "one.csv",
             "subject": {"template": "r:a/{k}"}, "properties": []},
            {"name": "b", "source": "two.csv",
             "subject": {"template": "r:b/{k}/{j}"}, "properties": []},
        ],
    }
    _out, added = ensure_same_source_links(ir, ontology_prefix="o")
    assert added == []


def test_owned_single_var_column_gets_its_link_deterministically() -> None:
    """K33: when a column's owner is a value catalog (its subject IS that
    column), dropping the plain property must not sever the edge — the link is
    appended deterministically, next to nothing the human confirmed."""
    from asterism_step0.staged_propose import _generate_map_properties_gated

    calls: list[str] = []

    class OneShot:
        def complete(self, *a, **k):
            raise AssertionError("not used")

    def fake_generate(*a, **k):
        return {
            "properties": [
                {"predicate": "xr:twoTheta", "column": "2theta", "label": "2θ"},
                # plain copy → dropped (G6); the label keeps the label-fill
                # round from firing (this test has no LLM to answer it)
                {"predicate": "xr:hkl", "column": "(hkl)", "label": "ミラー指数"},
            ],
            "prefixes": {},
        }

    import asterism_step0.staged_propose as sp

    original = sp.generate_map_properties
    sp.generate_map_properties = fake_generate  # type: ignore[assignment]
    try:
        result = _generate_map_properties_gated(
            "peak",
            {"name": "peak", "source": "c.txt", "subject": {"template": "xr:peak/{No}/{2theta}"}},
            "ctx",
            "menu",
            llm=OneShot(),  # type: ignore[arg-type]
            function_names=None,
            language=None,
            index=0,
            total=1,
            emit=lambda **k: calls.append(str(k.get("message"))),
            record=lambda: None,
            owned_elsewhere={"(hkl)": "hkl"},
            owner_subjects={"hkl": "xr:hkl/{(hkl)}", "peak": "xr:peak/{No}/{2theta}"},
            ontology_prefix="xr",
        )
    finally:
        sp.generate_map_properties = original  # type: ignore[assignment]

    props = result["properties"]
    assert {"predicate": "xr:twoTheta", "column": "2theta", "label": "2θ"} in props
    assert all(p.get("column") != "(hkl)" for p in props)  # plain copy dropped (G6)
    assert {"predicate": "xr:hkl", "object_template": "xr:hkl/{(hkl)}", "label": "hkl"} in props
    # Idempotence: a model that DID link gets no duplicate edge.
    def fake_generate_linked(*a, **k):
        return {
            "properties": [
                {"predicate": "xr:hkl", "object_template": "xr:hkl/{(hkl)}", "label": "ミラー指数"},
            ],
            "prefixes": {},
        }

    sp.generate_map_properties = fake_generate_linked  # type: ignore[assignment]
    try:
        again = _generate_map_properties_gated(
            "peak",
            {"name": "peak", "source": "c.txt", "subject": {"template": "xr:peak/{No}/{2theta}"}},
            "ctx",
            "menu",
            llm=OneShot(),  # type: ignore[arg-type]
            function_names=None,
            language=None,
            index=0,
            total=1,
            emit=lambda **k: None,
            record=lambda: None,
            owned_elsewhere={"(hkl)": "hkl"},
            owner_subjects={"hkl": "xr:hkl/{(hkl)}"},
            ontology_prefix="xr",
        )
    finally:
        sp.generate_map_properties = original  # type: ignore[assignment]
    targets = [p.get("object_template") for p in again["properties"]]
    assert targets.count("xr:hkl/{(hkl)}") == 1



# ---------------------------------------------------------------------------
# Column meanings — the stage that runs BEFORE any design exists
# (ADR meaning-before-identity.md §7-1).
# ---------------------------------------------------------------------------


def _column(name: str, inferred_type: str, samples: list[str]) -> ColumnSummary:
    return ColumnSummary(
        name=name,
        inferred_type=inferred_type,
        non_null_count=len(samples),
        total_rows=len(samples),
        unique_count=len(set(samples)),
        sample_values=samples,
    )


def _xrd_inspection() -> SourceInspection:
    return SourceInspection(
        path="/tmp/xrd.txt",
        name="xrd.txt",
        total_rows=47,
        columns=[
            _column("Sample", "xsd:string", ["Al3V"]),
            _column("2theta", "xsd:double", ["10.00", "10.02", "10.04"]),
            _column("(hkl)", "xsd:string", ["(110)", "(200)"]),
        ],
        uniqueness_reports=[],
    )


def test_render_columns_for_meanings_shows_values_not_design_material() -> None:
    """The meaning stage reads headers, types and samples — nothing that would
    make it start designing (keys, foreign keys, ingest strategy)."""
    md = render_columns_for_meanings([_xrd_inspection()])
    assert "## xrd.txt (47 rows)" in md
    assert "| `2theta` | xsd:double | `10.00`, `10.02`, `10.04` |" in md
    assert "| `(hkl)` | xsd:string | `(110)`, `(200)` |" in md
    # no uniqueness / FK / JSON-ingest material
    assert "unique" not in md.lower()
    assert "iterator" not in md.lower()


def test_render_columns_for_meanings_separates_preamble_from_body() -> None:
    """A column broadcast from the file's header block holds ONE value for the
    whole file. Naming it well needs that fact, and it is deterministic."""
    md = render_columns_for_meanings(
        [_xrd_inspection()], preamble_columns={"xrd.txt": ["Sample"]}
    )
    head, _, body = md.partition("### Table columns (one value per row)")
    assert "File-level metadata" in head
    assert "`Sample`" in head and "`2theta`" not in head
    assert "`2theta`" in body and "`(hkl)`" in body


def test_render_columns_for_meanings_skips_sources_with_no_columns() -> None:
    """XML/JATS has no tabular column to name — it is skipped, not rendered empty."""
    xml = SourceInspection(
        path="/tmp/a.xml", name="a.xml", total_rows=1, columns=[],
        uniqueness_reports=[], source_kind="xml",
    )
    assert render_columns_for_meanings([xml]) == ""
    md = render_columns_for_meanings([xml, _xrd_inspection()])
    assert "a.xml" not in md and "xrd.txt" in md


def test_generate_column_meanings_is_guided_and_needs_no_skeleton() -> None:
    """One guided call with the meanings schema in force, restored afterwards.
    The ask carries the columns and the domain — no skeleton, no Tier-0 menu."""
    answer = {
        "columns": [
            {"source": "xrd.txt", "column": "2theta", "label": "回折角", "unit": "deg"},
        ]
    }
    llm = GuidedMock(lambda s, u: json.dumps(answer))
    out = generate_column_meanings(
        render_columns_for_meanings([_xrd_inspection()]), "XRD card", llm=llm
    )
    assert out == answer
    (system, user, schema_at_call) = llm.calls[0]
    assert system == COLUMN_MEANINGS_SYSTEM_PROMPT
    assert schema_at_call == column_meanings_json_schema()
    assert llm.response_schema is None
    assert "`2theta`" in user and "XRD card" in user
    assert "subject" not in user and "menu" not in user.lower()


def test_column_meanings_schema_accepts_a_unitless_row_and_rejects_extras() -> None:
    schema = column_meanings_json_schema()
    jsonschema.validate(
        {"columns": [{"source": "a.csv", "column": "note", "label": "備考"}]}, schema
    )
    with pytest.raises(jsonschema.ValidationError):  # a predicate is not this stage's business
        jsonschema.validate(
            {"columns": [{"source": "a.csv", "column": "n", "label": "x", "predicate": "ex:n"}]},
            schema,
        )
    with pytest.raises(jsonschema.ValidationError):  # source/column are how it is filed
        jsonschema.validate({"columns": [{"column": "n", "label": "x"}]}, schema)


def test_normalize_column_meanings_rules() -> None:
    """Keeps what exists, rejects inventions and echoes, and lets a unit survive
    a rejected label (the two halves of the answer are independent)."""
    kept, rejected = normalize_column_meanings(
        {
            "columns": [
                {"source": "xrd.txt", "column": "2theta", "label": "回折角", "unit": "deg"},
                {"source": "xrd.txt", "column": "Sample", "label": " 試料名 "},
                {"source": "xrd.txt", "column": "(hkl)", "label": "hkl", "unit": ""},
                {"source": "xrd.txt", "column": "Temp", "label": "temp", "unit": "K"},
                {"source": "xrd.txt", "column": "2theta", "label": "二度目"},
                {"source": "xrd.txt", "column": "invented", "label": "無い列"},
                {"source": "other.csv", "column": "2theta", "label": "別ファイル"},
            ]
        },
        {"xrd.txt": ["Sample", "2theta", "(hkl)", "Temp"]},
    )
    assert kept == [
        {"source": "xrd.txt", "column": "2theta", "label": "回折角", "unit": "deg"},
        {"source": "xrd.txt", "column": "Sample", "label": "試料名"},
        # 列名の言い直しは意味ではない。単位だけが残る (K22)
        {"source": "xrd.txt", "column": "Temp", "unit": "K"},
    ]
    # `(hkl)` は言い直しのうえ単位も無い → 行ごと落ちる
    assert "xrd.txt:(hkl) (label restates the column)" in rejected
    assert "xrd.txt:2theta (duplicate)" in rejected
    assert "xrd.txt:invented (unknown column)" in rejected
    assert "other.csv:2theta (unknown source)" in rejected


def test_settled_meanings_win_over_the_per_map_label() -> None:
    """束ね終わった表に、先に決めた意味を決定論で写す。per-map が別の言葉を
    書いていても、意味を決めるのは生成ラウンドではない（ADR §6 / N6）。"""
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)
    labeled = {
        name: {
            "properties": [{**prop, "label": "AI が書いた意味"} for prop in pm["properties"]]
        }
        for name, pm in permaps.items()
    }
    frames: list[dict] = []

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            for name, pm in labeled.items():
                if f"This map: '{name}'" in user:
                    return json.dumps(pm)
            raise AssertionError("per-map call for an unknown map")
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError("unexpected system prompt")

    md = propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=GuidedMock(handler),
        menu="menu", function_names=FN_NAMES,
        on_progress=lambda **d: frames.append(d),
        column_meanings=[
            {"source": "data.csv", "column": "name", "label": "試料の名前", "unit": ""},
            {"source": "data.csv", "column": "date", "label": "測定日"},
        ],
    )
    thing = next(m for m in parse_mapping_ir(_extract_spec(md)).maps if m.name == "thing")
    by_pred = {p.predicate: p for p in thing.properties}
    assert by_pred["schema:name"].label == "試料の名前"
    assert by_pred["schema:name"].column == "name"  # binding untouched
    assert by_pred["ex:when"].label == "測定日"
    # 別のマップ（同じ列名を持たない）は AI の label のまま
    part = next(m for m in parse_mapping_ir(_extract_spec(md)).maps if m.name == "part")
    assert part.properties[0].label == "AI が書いた意味"
    assert any(f.get("phase") == "meaning" for f in frames)


def test_no_settled_meanings_changes_nothing() -> None:
    """意味をまだ誰も決めていないときは、今日と同じ結果でなければならない。"""
    assert apply_column_meanings(FULL_IR, [])[0] == dict(FULL_IR)
    assert apply_column_meanings(FULL_IR, [{"source": "data.csv", "column": "nope"}])[1] == []


def test_settled_columns_are_not_asked_about_twice() -> None:
    """意味が決まっている列は per-map プロンプトで「決定済み」と伝え、
    意味を埋め直すラウンドの対象からも外す（ADR §7-3）。"""
    skeleton_obj, permaps = skeleton_from_full_ir(FULL_IR)
    permap_asks: list[str] = []
    fill_calls: list[str] = []

    def handler(system: str, user: str) -> str:
        if system == SKELETON_SYSTEM_PROMPT:
            return json.dumps(skeleton_obj)
        if system == PERMAP_SYSTEM_PROMPT:
            permap_asks.append(user)
            for name, pm in permaps.items():
                if f"This map: '{name}'" in user:
                    return json.dumps(pm)
            raise AssertionError("per-map call for an unknown map")
        if system == PERMAP_LABELFILL_SYSTEM_PROMPT:
            fill_calls.append(user)
            return json.dumps({"labels": []})
        if system == DOCUMENT_SYSTEM_PROMPT:
            return "### 1. Class hierarchy\n\n(the design)\n"
        raise AssertionError("unexpected system prompt")

    # data.csv の 3 列すべての意味が決まっている（'thing' マップの全行）
    meanings = [
        {"source": "data.csv", "column": "name", "label": "試料の名前"},
        {"source": "data.csv", "column": "date", "label": "測定日"},
        {"source": "data.csv", "column": "id", "label": "試料 ID"},
    ]
    propose_from_skeleton(
        skeleton_obj, "# insp", "# dom", llm=GuidedMock(handler),
        menu="menu", function_names=FN_NAMES, column_meanings=meanings,
    )
    thing_ask = next(u for u in permap_asks if "This map: 'thing'" in u)
    assert "ALREADY settled" in thing_ask
    assert "- `name` → 試料の名前" in thing_ask
    # 'thing' は全列が決まっているので聞き直しは走らない。'part' は別ファイルで
    # 意味が渡っていないので今までどおり 1 回走る。
    assert len(fill_calls) == 1
    assert "# Map: 'part'" in fill_calls[0]


def test_without_settled_meanings_the_permap_ask_is_unchanged() -> None:
    """意味をまだ誰も決めていない設計は、今日と 1 バイトも変わらない。"""
    skeleton_obj, _permaps = skeleton_from_full_ir(FULL_IR)
    thing = skeleton_obj["maps"][0]
    plain = build_permap_user("thing", thing, "ctx", "menu")
    with_empty = build_permap_user("thing", thing, "ctx", "menu", settled_meanings=[])
    assert plain == with_empty
    assert "ALREADY settled" not in plain


def test_missing_label_rows_skips_settled_columns() -> None:
    rows = [
        {"predicate": "ex:a", "column": "settled"},
        {"predicate": "ex:b", "column": "open"},
        {"predicate": "ex:c", "column": "has", "label": "既にある"},
        {"predicate": "ex:link", "object_template": "ex:other/{k}"},
    ]
    assert [r["predicate"] for r in missing_label_rows(rows)] == ["ex:a", "ex:b", "ex:link"]
    # 意味が決まっているなら、列を 1 つ読まない行（リンク・定数・複数列）も対象外。
    # 実測 2026-08-28: 機械が足したリンク 1 本のためにラウンドが 1 回走っていた。
    assert [r["predicate"] for r in missing_label_rows(rows, ["settled"])] == ["ex:b"]
# ---------------------------------------------------------------------------
# S4「AI にもう一度考えさせる」= 作り直しではなく修理 (2026-08-27)
# ---------------------------------------------------------------------------
def _rethink_llm(answer: dict) -> object:
    """答えを 1 つ返すだけの LLM。渡された user message を ``seen`` に貯める。"""
    seen: list[str] = []
    class _LLM:
        model = "mock"
        response_schema = None
        def __init__(self) -> None:
            self.seen = seen
        def complete(self, system: str, user: str) -> str:
            seen.append(user)
            return json.dumps(answer)
    return _LLM()
def test_human_pinned_edits_is_the_difference_between_ai_and_screen() -> None:
    """AI が返したものと、画面にあるものの差 = 人が打った値。"""
    from asterism_step0.staged_propose import human_pinned_edits
    ai = {"maps": [
        {"name": "peak", "subject": {"template": "r:peak/{No}", "classes": ["o:Peak"]}},
        {"name": "sample", "subject": {"template": "r:sample/{No}", "classes": ["o:Sample"]}},
    ]}
    screen = {"maps": [
        # 人が種類名も ID も打ち直した
        {"name": "peak", "subject": {"template": "r:peak/{No}/{(hkl)}", "classes": ["o:XrdPeak"]}},
        # 触っていない
        {"name": "sample", "subject": {"template": "r:sample/{No}", "classes": ["o:Sample"]}},
        # 人が S4 で切り出した (baseline に無い) = まるごと人の値
        {"name": "phase", "subject": {"template": "r:phase/{Phase}", "classes": ["o:Phase"]}},
    ]}
    pinned = human_pinned_edits(ai, screen)
    assert set(pinned) == {"peak", "phase"}
    assert pinned["peak"]["classes"] == ["o:XrdPeak"]
    assert pinned["peak"]["subject_id"] == ("template", "r:peak/{No}/{(hkl)}")
def test_human_pinned_edits_claims_nothing_without_a_baseline() -> None:
    """控えが無いなら、人が打った値だと主張しない(証明できないほうに倒す)。"""
    from asterism_step0.staged_propose import human_pinned_edits
    screen = {"maps": [{"name": "peak", "subject": {"template": "r:p/{a}", "classes": ["o:P"]}}]}
    assert human_pinned_edits(None, screen) == {}
def test_reassert_puts_the_persons_own_words_back() -> None:
    """注文は「ここを直して」であって「私が付けた名前も書き換えて」ではない。"""
    from asterism_step0.staged_propose import human_pinned_edits, reassert_human_edits
    ai = {"maps": [{"name": "peak", "subject": {"template": "r:peak/{No}", "classes": ["o:Peak"]}}]}
    screen = {"maps": [
        {"name": "peak", "subject": {"template": "r:peak/{No}/{(hkl)}", "classes": ["o:XrdPeak"]}},
    ]}
    pinned = human_pinned_edits(ai, screen)
    # モデルは注文どおり map を足したが、ついでに人の名前と ID を書き戻した
    answer = {"maps": [
        {"name": "peak", "subject": {"template": "r:peak/{No}", "classes": ["o:Peak"]}},
        {"name": "phase", "subject": {"template": "r:phase/{P}", "classes": ["o:Phase"]}},
    ]}
    out, restored = reassert_human_edits(answer, screen, pinned)
    peak = next(m for m in out["maps"] if m["name"] == "peak")
    assert peak["subject"]["classes"] == ["o:XrdPeak"]
    assert peak["subject"]["template"] == "r:peak/{No}/{(hkl)}"
    # 注文で増えた種類はそのまま残る
    assert {m["name"] for m in out["maps"]} == {"peak", "phase"}
    # 黙って直さない。報せは種類の名前で言う (map id は生の識別子・K4)
    assert restored == [{"map": "peak", "kind": "o:XrdPeak"}]
def test_reassert_brings_back_a_pinned_kind_the_model_dropped() -> None:
    """人が触った種類が答えから消えていたら戻す(差し戻しは足すためだけに使う)。"""
    from asterism_step0.staged_propose import reassert_human_edits
    screen = {"maps": [
        {"name": "peak", "subject": {"template": "r:peak/{No}", "classes": ["o:Peak"]}},
        {"name": "phase", "subject": {"template": "r:phase/{P}", "classes": ["o:Phase"]}},
    ]}
    pinned = {"phase": {"classes": ["o:Phase"]}}
    answer = {"maps": [{"name": "peak", "subject": {"template": "r:peak/{No}",
                                                    "classes": ["o:Peak"]}}]}
    out, restored = reassert_human_edits(answer, screen, pinned)
    assert {m["name"] for m in out["maps"]} == {"peak", "phase"}
    assert restored == [{"map": "phase", "kind": "o:Phase"}]
def test_reassert_leaves_untouched_maps_to_the_model() -> None:
    """人が触っていない種類は、モデルの作り直しに任せる(それが注文の中身)。"""
    from asterism_step0.staged_propose import reassert_human_edits
    screen = {"maps": [{"name": "row", "subject": {"template": "r:row/{id}",
                                                   "classes": ["o:Row"]}}]}
    answer = {"maps": [
        {"name": "sample", "subject": {"template": "r:sample/{id}", "classes": ["o:Sample"]}},
        {"name": "meas", "subject": {"template": "r:meas/{id}/{t}", "classes": ["o:Meas"]}},
    ]}
    out, restored = reassert_human_edits(answer, screen, {})
    assert {m["name"] for m in out["maps"]} == {"sample", "meas"}
    assert restored == []
def test_rethink_request_hands_over_the_design_and_says_keep_it() -> None:
    """渡すのは画面にある骨格。文面は「残せ」としか言わない(減らす指示は上限が無い)。"""
    from asterism_step0.staged_propose import render_rethink_request
    text = render_rethink_request(
        {"maps": [{"name": "peak", "subject": {"template": "r:peak/{No}"}}]},
        "試料と測定値を別の種類に分けて",
        {"peak": {"classes": ["o:XrdPeak"], "subject_id": ("template", "r:peak/{No}")}},
    )
    assert "r:peak/{No}" in text  # 設計そのものが渡る
    assert "Keep EVERY map above" in text
    assert "試料と測定値を別の種類に分けて" in text
    assert "o:XrdPeak" in text  # 人が打った値は名指しで守らせる
    assert render_rethink_request(None, "何か", None) == ""
def test_rethink_keeps_the_edits_a_person_made(tmp_path: Path) -> None:
    """S4 の編集(種類名・ID・削除・切り出し)は、もう一度考えさせても残る。
    2026-08-27 まで onRethink は setSkeleton(null) で S3 から作り直しており、
    S4 の編集は保存先が無いので全部消えていた。
    """
    from asterism_step0.staged_propose import propose_skeleton
    csv = tmp_path / "a.csv"
    csv.write_text("id,name\n1,x\n2,y\n", encoding="utf-8")
    ns = {"ao": "https://ns.invalid/datasets/a/ontology#",
          "ar": "https://ns.invalid/datasets/a/resource/"}
    ai = {"version": 1, "prefixes": ns, "maps": [
        {"name": "row", "source": "a.csv",
         "subject": {"template": "ar:row/{id}", "classes": ["ao:Row"]}},
    ]}
    screen = {"version": 1, "prefixes": ns, "maps": [
        # 人が種類名を打ち直した
        {"name": "row", "source": "a.csv",
         "subject": {"template": "ar:row/{id}", "classes": ["ao:Specimen"]}},
        # 人が列を切り出して作った種類
        {"name": "thing", "source": "a.csv",
         "subject": {"template": "ar:thing/{name}", "classes": ["ao:Thing"]}},
    ]}
    # モデルは「人の名前を元に戻し、切り出した種類を落とす」答えを返す
    answer = {"version": 1, "prefixes": ns, "maps": [
        {"name": "row", "source": "a.csv",
         "subject": {"template": "ar:row/{id}", "classes": ["ao:Row"]}},
        {"name": "extra", "source": "a.csv",
         "subject": {"template": "ar:extra/{id}", "classes": ["ao:Extra"]}},
    ]}
    llm = _rethink_llm(answer)
    out = propose_skeleton(
        [csv], "", llm=llm,
        current_skeleton=screen, baseline_skeleton=ai,
        request="名前ごとに 1 件の種類も作って",
    )
    names = {m["name"] for m in out.skeleton["maps"]}
    assert "thing" in names, names  # 人が切り出した種類は消えない
    row = next(m for m in out.skeleton["maps"] if m["name"] == "row")
    # 人が打った名前が勝つ (prefix は K13 で slug から機械導出されるので local name で見る)
    assert row["subject"]["classes"][0].endswith(":Specimen"), row["subject"]
    assert out.metadata.get("kept_human_edits")  # 直したことは報せる
    assert out.metadata.get("rethink") is True
    # 画面にあった設計そのものがモデルへ渡っている
    assert "ar:thing/{name}" in llm.seen[0]
def test_rethink_falls_back_to_the_design_on_screen(tmp_path: Path) -> None:
    """答えが読めなかったとき、持ち帰る先は決定論の初期骨格ではなく人の設計。"""
    from asterism_step0.staged_propose import propose_skeleton
    csv = tmp_path / "a.csv"
    csv.write_text("id,name\n1,x\n", encoding="utf-8")
    ns = {"ao": "https://ns.invalid/datasets/a/ontology#",
          "ar": "https://ns.invalid/datasets/a/resource/"}
    screen = {"version": 1, "prefixes": ns, "maps": [
        {"name": "row", "source": "a.csv",
         "subject": {"template": "ar:row/{id}", "classes": ["ao:Specimen"]}},
    ]}
    class _Broken:
        model = "mock"
        response_schema = None
        def complete(self, system: str, user: str) -> str:
            return "not json at all"
    out = propose_skeleton([csv], "", llm=_Broken(), current_skeleton=screen, request="直して")
    assert out.skeleton["maps"][0]["subject"]["classes"][0].endswith(":Specimen")
    assert out.metadata.get("fallback") is None  # 決定論の初期骨格には落ちていない
def test_rethink_keeps_the_dataset_namespace(tmp_path: Path) -> None:
    """名前空間は rethink で変わらない — slug が変われば全 ID が別物になる。"""
    from asterism_step0.staged_propose import propose_skeleton
    csv = tmp_path / "a.csv"
    csv.write_text("id\n1\n", encoding="utf-8")
    ns = {"xr": "https://ns.invalid/datasets/xrd-reference/ontology#",
          "xrr": "https://ns.invalid/datasets/xrd-reference/resource/"}
    screen = {"version": 1, "prefixes": ns, "maps": [
        {"name": "row", "source": "a.csv",
         "subject": {"template": "xrr:row/{id}", "classes": ["xr:Row"]}},
    ]}
    # モデルが名前空間を勝手に打ち直して返す
    answer = {"version": 1, "prefixes": {
        "zz": "https://ns.invalid/datasets/something-else/ontology#",
        "zzr": "https://ns.invalid/datasets/something-else/resource/",
    }, "maps": [
        {"name": "row", "source": "a.csv",
         "subject": {"template": "zzr:row/{id}", "classes": ["zz:Row"]}},
    ]}
    out = propose_skeleton(
        [csv], "", llm=_rethink_llm(answer), current_skeleton=screen, request="直して"
    )
    values = list(out.skeleton["prefixes"].values())
    assert all("something-else" not in v for v in values), out.skeleton["prefixes"]
    assert any("xrd-reference" in v for v in values), out.skeleton["prefixes"]
    # 名前空間が二重にならない - 実測 2026-08-27: 画面の prefixes を足す形にしたら
    # 両方が canonical に見えて片方だけ直り、ゲートに生の `xr:Sample` が出た。
    minted = [v for v in values if "/datasets/xrd-reference/" in v]
    assert len(minted) == 2, out.skeleton["prefixes"]
    # CURIE も正規形の prefix を指している(かんたん層は縮約して表示する)
    onto = next(k for k, v in out.skeleton["prefixes"].items() if v.endswith("ontology#"))
    assert out.skeleton["maps"][0]["subject"]["classes"][0].startswith(f"{onto}:")


# ── 共通語彙の綴りを骨格プロンプトに渡す（利用者評価 2026-08-28） ──────────────


def test_standard_class_names_are_names_only() -> None:
    """CURIE も IRI も渡さない — 綴りの薦めであって、同一性の主張ではない。

    IRI を渡すと、モデルはそれを設計にそのまま書ける。書かれた時点で「この
    クラスは cmso:CrystalStructure と同じものだ」という、誰も検めていない主張が
    設計に入る。それを人が確かめるのが接地の段（S9）なので、ここで渡してよいのは
    言葉だけ。
    """
    names = dict(standard_class_names())
    assert names, "curated catalog is not empty"
    assert "CrystalStructure" in names
    block = render_standard_class_names()
    assert "`CrystalStructure`" in block
    assert ":" not in block.split("\n", 2)[0]  # 見出しに CURIE を混ぜない
    assert "http" not in block
    assert "cmso:" not in block


def test_standard_class_names_deduplicates_a_shared_spelling() -> None:
    """schema:Person と foaf:Person は綴りが同じなのだから 1 行。"""
    lines = [ln for ln in render_standard_class_names().split("\n") if ln.startswith("- ")]
    assert len(lines) == len(set(lines))
    assert lines.count("- `Person`") == 1


def test_build_skeleton_user_carries_the_spellings() -> None:
    msg = build_skeleton_user("## CSV: peaks.csv", "XRD peaks")
    assert "# Standard class names" in msg
    assert "`CrystalStructure`" in msg
    # 検査の本文より後ろに出さない（材料が先、薦めがあと）。
    assert msg.index("# Source inspection") < msg.index("# Standard class names")
