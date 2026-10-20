"""AI graph diff (ui-redesign PR 7a): the closed diff the model may return in the workspace
chat, and the gate every answer passes.

The model is faked (no network). What these pin: the shape is closed (seven operations, no
place for SPARQL / an IRI / code), data never reaches the prompt (names and column headings
only), references must be keys on the graph (or added first), an off-shape answer is retried
once and then refused, the guided-decoding schema is set for the call and put back after it,
and ``reason`` is cut, never refused.
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy
import json

import pytest

from asterism_step0.graph_propose import (
    BIND_RELATIONS,
    GRAPH_DIFF_SYSTEM_PROMPT,
    OP_NAMES,
    GraphDiffError,
    graph_diff_json_schema,
    graph_diff_user_message,
    propose_graph_diff,
    validate_graph_diff,
)

GRAPH = {
    "concepts": [
        {"key": "c1", "label_ja": "熱伝導率", "label_en": "thermal conductivity",
         "kind": "property", "use": "new"},
        {"key": "c2", "label_ja": "格子熱伝導率", "label_en": "lattice thermal conductivity",
         "kind": "property", "use": "new"},
        {"key": "c3", "label_ja": "粒界散乱", "label_en": "grain boundary scattering",
         "kind": "class", "use": "new"},
    ],
    "relations": [
        {"from": "c2", "rel": "part", "to": "c1"},
        {"from": "c3", "rel": "lowers", "to": "c1"},
    ],
    "questions": ["熱伝導率を下げる要素は？"],
}  # fmt: skip
KEYS = ["c1", "c2", "c3"]

RETARGET = {
    "reply": "粒界散乱が直接下げるのは格子の分です。",
    "ops": [
        {"op": "retarget", "from": "c3", "rel": "lowers", "to": "c1", "new_to": "c2",
         "reason": "フォノンの散乱だから"},
    ],
}  # fmt: skip


def test_the_shape_has_seven_operations() -> None:
    assert OP_NAMES == (
        "add_concept", "add_relation", "retarget", "remove", "rename", "add_question",
        "bind_column",
    )  # fmt: skip
    assert BIND_RELATIONS == ("eq", "sub", "quantity")


def test_a_retarget_passes_and_is_normalised() -> None:
    out = validate_graph_diff(RETARGET, known_keys=KEYS)
    assert out["reply"] == "粒界散乱が直接下げるのは格子の分です。"
    assert out["ops"] == [
        {"op": "retarget", "from": "c3", "rel": "lowers", "to": "c1", "new_to": "c2",
         "reason": "フォノンの散乱だから"},
    ]  # fmt: skip


def test_every_operation_kind_passes() -> None:
    raw = {
        "reply": "",
        "ops": [
            {"op": "add_concept", "key": "n1", "label_ja": "点欠陥散乱", "label_en": "point defect",
             "aliases": ["PD"], "kind": "class", "use": "new", "reason": "合金化"},
            {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "c2", "reason": "r"},
            {"op": "add_relation", "from": "c1", "rel": "quantity",
             "to": "quantitykind:ThermalConductivity", "reason": "r"},
            {"op": "remove", "target": "relation", "from": "c2", "rel": "part", "to": "c1",
             "reason": "r"},
            {"op": "remove", "target": "concept", "key": "c3", "reason": "r"},
            {"op": "rename", "key": "c1", "label_ja": "熱伝導度", "label_en": "kappa",
             "aliases": [], "reason": "r"},
            {"op": "add_question", "question": "粒界散乱が下げるものは？", "mode": "knowledge",
             "op_kind": "factors", "target": "c2", "direction": "lowers", "kind": None,
             "property": None, "wanted_data": "粒径と熱伝導率の表", "reason": "r"},
            {"op": "bind_column", "column": "kappa (W/mK)", "to": "c1", "rel": "eq",
             "reason": "r"},
        ],
    }  # fmt: skip
    out = validate_graph_diff(raw, known_keys=KEYS)
    assert [o["op"] for o in out["ops"]] == [
        "add_concept", "add_relation", "add_relation", "remove", "remove", "rename",
        "add_question", "bind_column",
    ]  # fmt: skip
    q = out["ops"][6]
    assert (q["op_kind"], q["target"], q["direction"], q["kind"]) == (
        "factors", "c2", "lowers", None,
    )  # fmt: skip


def _bad(op: dict, **kw) -> None:
    with pytest.raises(GraphDiffError):
        validate_graph_diff({"reply": "", "ops": [op]}, **{"known_keys": KEYS, **kw})


def test_a_key_that_is_not_on_the_graph_is_refused_unless_added_first() -> None:
    _bad({"op": "add_relation", "from": "n1", "rel": "lowers", "to": "c2", "reason": ""})
    # the same relation passes when the diff adds n1 (even after its first use)
    raw = {
        "reply": "",
        "ops": [
            {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "c2", "reason": ""},
            {"op": "add_concept", "key": "n1", "label_ja": "X", "label_en": "", "aliases": [],
             "kind": "class", "use": "new", "reason": ""},
        ],
    }  # fmt: skip
    assert len(validate_graph_diff(raw, known_keys=KEYS)["ops"]) == 2


def test_a_taken_or_repeated_new_key_is_refused() -> None:
    add = {"op": "add_concept", "label_ja": "X", "label_en": "", "aliases": [], "kind": "class",
           "use": "new", "reason": ""}  # fmt: skip
    _bad({**add, "key": "c1"})  # already on the graph
    with pytest.raises(GraphDiffError):
        validate_graph_diff({"ops": [{**add, "key": "n1"}, {**add, "key": "n1"}]}, known_keys=KEYS)
    _bad({**add, "key": "has space"})


def test_names_aliases_questions_and_columns_may_not_look_like_code() -> None:
    add = {"op": "add_concept", "key": "n1", "label_en": "", "aliases": [], "kind": "class",
           "use": "new", "reason": ""}  # fmt: skip
    _bad({**add, "label_ja": "SELECT * WHERE"})
    _bad({**add, "label_ja": "X", "aliases": ["http://example.org/x"]})
    _bad({"op": "add_question", "question": "<script>", "mode": "data", "op_kind": "count",
          "kind": "c3", "reason": ""})  # fmt: skip
    _bad({"op": "bind_column", "column": "{col}", "to": "c1", "rel": "eq", "reason": ""})
    # a "to" that is an IRI is not a key and not a curie
    _bad({"op": "add_relation", "from": "c1", "rel": "eq", "to": "http://x.org/a", "reason": ""})


def test_relations_and_modes_are_closed_sets() -> None:
    _bad({"op": "add_relation", "from": "c1", "rel": "causes", "to": "c2", "reason": ""})
    _bad({"op": "bind_column", "column": "k", "to": "c1", "rel": "part", "reason": ""})
    _bad({"op": "add_question", "question": "q", "mode": "memo", "op_kind": "count",
          "kind": "c3", "reason": ""})  # fmt: skip
    _bad({"op": "add_question", "question": "q", "mode": "data", "op_kind": "join",
          "reason": ""})  # fmt: skip
    _bad({"op": "sparql", "query": "SELECT *"})
    _bad({"op": "remove", "target": "everything", "reason": ""})


def test_a_curie_is_allowed_only_where_a_standard_term_belongs() -> None:
    _bad({"op": "add_relation", "from": "quantitykind:Temperature", "rel": "eq", "to": "c1",
          "reason": ""})  # fmt: skip
    _bad({"op": "add_question", "question": "q", "mode": "knowledge", "op_kind": "parts",
          "target": "cmso:Thing", "reason": ""})  # fmt: skip


def test_at_most_thirty_operations_and_a_long_reason_is_cut() -> None:
    op = {"op": "remove", "target": "concept", "key": "c1", "reason": "x" * 500}
    out = validate_graph_diff({"reply": "y" * 1000, "ops": [op]}, known_keys=KEYS)
    assert len(out["ops"][0]["reason"]) == 200
    assert len(out["reply"]) == 600
    with pytest.raises(GraphDiffError):
        validate_graph_diff({"ops": [op] * 31}, known_keys=KEYS)


def test_unknown_fields_are_dropped_not_read() -> None:
    op = {**RETARGET["ops"][0], "sparql": "DELETE WHERE {}", "iri": "http://x.org/a"}
    out = validate_graph_diff({"reply": "", "ops": [op], "extra": 1}, known_keys=KEYS)
    assert set(out) == {"reply", "ops"}
    assert set(out["ops"][0]) == {"op", "from", "rel", "to", "new_to", "reason"}


def test_expand_takes_only_additions() -> None:
    with pytest.raises(GraphDiffError):
        validate_graph_diff(RETARGET, known_keys=KEYS, expand=True)
    add = {"op": "add_relation", "from": "c3", "rel": "lowers", "to": "c2", "reason": ""}
    assert validate_graph_diff({"ops": [add]}, known_keys=KEYS, expand=True)["ops"]


def test_the_schema_is_closed_and_flat() -> None:
    schema = graph_diff_json_schema()
    assert schema["additionalProperties"] is False
    item = schema["properties"]["ops"]["items"]
    assert item["additionalProperties"] is False
    assert item["properties"]["op"]["enum"] == list(OP_NAMES)
    assert schema["properties"]["ops"]["maxItems"] == 30
    assert graph_diff_json_schema(expand=True)["properties"]["ops"]["maxItems"] == 10
    json.dumps(schema)  # serialisable


class _LLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.responses.pop(0)


def _propose(llm, **kw):
    args = dict(
        screen="theme", graph=GRAPH, existing_terms=[], standards=[], columns=None,
        messages=[], message="散乱因子は格子熱伝導率に紐づくはず", lang="ja",
    )  # fmt: skip
    return propose_graph_diff(llm, **{**args, **kw})


def test_propose_returns_the_validated_diff_and_sees_names_only() -> None:
    llm = _LLM(json.dumps(RETARGET))
    out = _propose(
        llm,
        existing_terms=[{"slug": "capacity", "label_ja": "容量", "label_en": "capacity",
                         "kind": "property"}],
        standards=[{"curie": "cmso:Thing", "label": "Thing", "kind": "class"}],
        messages=[
            {"role": "user", "content": "前の発言"},
            {"role": "assistant", "content": "前の返事"},
        ],
    )  # fmt: skip
    assert out["ops"][0]["new_to"] == "c2"
    system, user = llm.calls[0]
    assert "Do NOT write SPARQL" in system and "never see" not in user
    assert "c3 | 粒界散乱 | grain boundary scattering | class | new" in user
    assert "c3 -[lowers]-> c1" in user
    assert "capacity | 容量" in user and "cmso:Thing" in user
    assert "Person: 前の発言" in user and "You: 前の返事" in user
    assert "# The person now says\n散乱因子は格子熱伝導率に紐づくはず" in user


def test_only_the_last_twelve_turns_ride_the_call() -> None:
    msgs = [{"role": "user", "content": f"turn-{i:02d}"} for i in range(20)]
    user = graph_diff_user_message(
        screen="theme", graph=GRAPH, existing_terms=[], standards=[], columns=None,
        messages=msgs, message="now", lang=None,
    )  # fmt: skip
    assert "turn-07" not in user and "turn-08" in user and "turn-19" in user


def test_a_dataset_prompt_carries_column_headings_and_no_values() -> None:
    llm = _LLM(
        json.dumps(
            {"reply": "", "ops": [{"op": "bind_column", "column": "kappa", "to": "thermal",
                                   "rel": "eq", "reason": "熱伝導率の列"}]}
        )
    )  # fmt: skip
    out = _propose(
        llm,
        screen="dataset",
        graph={"concepts": [], "relations": [], "questions": []},
        existing_terms=[
            {"slug": "thermal", "label_ja": "熱伝導率", "label_en": "", "kind": "property"}
        ],
        columns=[
            {"name": "kappa", "kind_hint": "number"},
            {"name": "sample", "kind_hint": "text"},
        ],
    )  # fmt: skip
    # on the dataset screen a term slug is a key
    assert out["ops"][0]["to"] == "thermal"
    system, user = llm.calls[0]
    assert "# Column headings" in user and "kappa | number" in user
    assert "bind_column" in system and "COLUMN HEADINGS" in system
    assert "3.14" not in user


def test_an_off_shape_answer_is_retried_once_with_the_reason() -> None:
    bad = {"reply": "", "ops": [{"op": "add_relation", "from": "zz", "rel": "eq", "to": "c1",
                                 "reason": ""}]}  # fmt: skip
    llm = _LLM(json.dumps(bad), json.dumps(RETARGET))
    out = _propose(llm)
    assert out["ops"][0]["op"] == "retarget"
    assert len(llm.calls) == 2
    assert "refused" in llm.calls[1][1] and "zz" in llm.calls[1][1]
    assert "refused" not in llm.calls[0][1]


def test_two_refusals_raise() -> None:
    llm = _LLM("not json", json.dumps({"ops": [{"op": "nope"}]}))
    with pytest.raises(GraphDiffError):
        _propose(llm)
    assert len(llm.calls) == 2


def test_the_guided_schema_is_set_for_the_call_and_put_back() -> None:
    seen: list = []

    class Guided(_LLM):
        response_schema = None

        def complete(self, system_prompt: str, user_message: str) -> str:
            seen.append(self.response_schema)
            return super().complete(system_prompt, user_message)

    llm = Guided(json.dumps(RETARGET))
    _propose(llm)
    assert seen[0]["properties"]["ops"]["maxItems"] == 30
    assert llm.response_schema is None


def test_the_home_prompt_forbids_new_concepts_and_the_prompt_never_mentions_data_values() -> None:
    llm = _LLM(json.dumps({"reply": "同じです。", "ops": []}))
    out = _propose(llm, screen="home", message="これらは同じもの？")
    assert out == {"reply": "同じです。", "ops": []}
    system, _user = llm.calls[0]
    assert "Never add a" in system
    assert "SPARQL" in GRAPH_DIFF_SYSTEM_PROMPT


def test_validation_does_not_mutate_its_input() -> None:
    raw = copy.deepcopy(RETARGET)
    validate_graph_diff(raw, known_keys=KEYS)
    assert raw == RETARGET
