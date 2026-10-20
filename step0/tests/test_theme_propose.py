"""AI theme draft v2: the closed JSON the model may fill, and the gate every answer passes.

The model is faked (no network). What these pin: the shape is closed (no place for SPARQL,
an IRI or code), data never reaches the prompt, an off-shape answer is retried once and
then refused, the guided-decoding schema is set for the call and put back after it, and
``reason`` / ``wanted_data`` are checked for length only (cut, never refused).
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy
import json

import pytest

from asterism_step0.theme_propose import (
    DATA_OPS,
    KNOWLEDGE_OPS,
    RELATIONS,
    THEME_DRAFT_SYSTEM_PROMPT,
    THEME_EXPAND_SYSTEM_PROMPT,
    ThemeDraftError,
    propose_theme_draft,
    propose_theme_expand,
    theme_draft_json_schema,
    theme_draft_user_message,
    theme_expand_user_message,
    validate_theme_draft,
)

Q = "熱伝導率を下げる要素になり得るのは？"
GOOD = {
    "theme_label": "熱電材料",
    "concepts": [
        {
            "key": "c1",
            "label_ja": "熱伝導率",
            "label_en": "Thermal Conductivity",
            "aliases": ["kappa"],
            "kind": "property",
            "use": "standard:quantitykind:ThermalConductivity",
            "reason": "問いの対象",
        },
        {
            "key": "c2",
            "label_ja": "格子熱伝導率",
            "label_en": "lattice thermal conductivity",
            "aliases": [],
            "kind": "property",
            "use": "new",
            "reason": "フォノンが運ぶ分",
        },
        {
            "key": "c3",
            "label_ja": "粒界散乱",
            "label_en": "grain boundary scattering",
            "aliases": [],
            "kind": "class",
            "use": "new",
            "reason": "",
        },
    ],
    "relations": [
        {"from": "c2", "rel": "part", "to": "c1", "reason": "熱伝導率 = 格子 + 電子"},
        {"from": "c3", "rel": "lowers", "to": "c2", "reason": "粒界でフォノンが散乱される"},
    ],
    "answers": [
        {
            "question": Q,
            "mode": "knowledge",
            "op": "factors",
            "target": "c1",
            "direction": "lowers",
            "kind": None,
            "property": None,
            "wanted_data": "試料ごとの 粒径・熱伝導率 の表",
            "reason": "要因を知識で並べる",
        }
    ],
    "suggested_questions": ["組成ごとの熱伝導率の最小値は？"],
}


class _FakeLLM:
    """OpenAI-compatible-shaped: it has a ``response_schema`` the caller may set."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, object]] = []
        self.response_schema: dict | None = None

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message, self.response_schema))
        return self.responses.pop(0)


def _bad(**patch: object) -> dict:
    out = copy.deepcopy(GOOD)
    out.update(patch)
    return out


def _ans(**over: object) -> dict:
    return {**GOOD["answers"][0], **over}


def test_a_good_answer_passes_and_is_normalised() -> None:
    out = validate_theme_draft(_bad(suggested_questions=["  組成ごとの  最小値は？ "]))
    assert out["suggested_questions"] == ["組成ごとの 最小値は？"]
    assert set(out) == {"theme_label", "concepts", "relations", "answers", "suggested_questions"}
    assert out["answers"][0]["question"] == Q  # the person's own text
    assert out["answers"][0]["mode"] == "knowledge" and out["answers"][0]["direction"] == "lowers"
    assert out["relations"][1] == {
        "from": "c3",
        "rel": "lowers",
        "to": "c2",
        "reason": "粒界でフォノンが散乱される",
    }


def test_the_vocabulary_is_the_closed_one_of_the_design() -> None:
    assert RELATIONS == ("sub", "eq", "quantity", "part", "lowers", "raises", "affects")
    assert KNOWLEDGE_OPS == ("factors", "parts", "kinds")
    assert DATA_OPS == ("count", "range", "top")


def test_unknown_keys_are_dropped_not_read() -> None:
    raw = copy.deepcopy(GOOD)
    raw["concepts"][0]["sparql"] = "SELECT * WHERE { ?s ?p ?o }"
    raw["concepts"][0]["iri"] = "https://example.org/x"
    raw["answers"][0]["query"] = "DROP ALL"
    raw["query"] = "DROP ALL"
    out = validate_theme_draft(raw)
    assert "sparql" not in out["concepts"][0] and "iri" not in out["concepts"][0]
    assert "query" not in out["answers"][0] and "query" not in out


@pytest.mark.parametrize(
    "patch",
    [
        {"concepts": "nope"},
        {"concepts": [{**GOOD["concepts"][0], "kind": "individual"}]},
        {"concepts": [{**GOOD["concepts"][0], "use": "https://example.org/x"}]},
        {"concepts": [{**GOOD["concepts"][0], "use": "existing:Bad Slug"}]},
        {"concepts": [{**GOOD["concepts"][0], "label_ja": ""}]},
        {"concepts": [{**GOOD["concepts"][0], "key": ""}]},
        {"concepts": [{**GOOD["concepts"][0], "key": "1c"}]},
        {"concepts": [{**GOOD["concepts"][0], "key": "https://x"}]},
        {"concepts": [GOOD["concepts"][0], GOOD["concepts"][0]]},  # a key used twice
        {"concepts": [{**GOOD["concepts"][0], "label_en": "SELECT ?s WHERE"}]},
        {"concepts": [{**GOOD["concepts"][0], "label_en": "<script>"}]},
        {"concepts": [{**GOOD["concepts"][0], "aliases": ["see https://example.org/x"]}]},
        {"concepts": [{**GOOD["concepts"][0], "label_ja": "あ" * 81}]},
        {"relations": [{"from": "a", "to": "b", "rel": "partOf", "reason": ""}]},
        {"relations": [{"from": "a", "to": "http://example.org/x", "rel": "sub", "reason": ""}]},
        {"relations": [{"from": "", "to": "b", "rel": "sub", "reason": ""}]},
        {"answers": [_ans(mode="guess")]},
        {"answers": [_ans(op="delete")]},
        {"answers": [_ans(direction="sideways")]},
        {"answers": [_ans(target="{ ?s ?p ?o }")]},
        {"suggested_questions": ["a"] * 6},
    ],
)
def test_an_off_shape_answer_is_refused(patch: dict) -> None:
    with pytest.raises(ThemeDraftError):
        validate_theme_draft(_bad(**patch))


def test_reason_and_wanted_data_are_cut_to_the_length_and_never_refused() -> None:
    """A long line, or one that quotes a bracket or a URL as prose, must not fail a draft."""
    long = "あ" * 500
    raw = copy.deepcopy(GOOD)
    raw["concepts"][0]["reason"] = long
    raw["relations"][0]["reason"] = "see https://example.org/x { a } SELECT"
    raw["answers"][0]["reason"] = long
    raw["answers"][0]["wanted_data"] = long + "\n" + "x"
    out = validate_theme_draft(raw)
    assert len(out["concepts"][0]["reason"]) == 200
    assert out["relations"][0]["reason"] == "see https://example.org/x { a } SELECT"
    assert len(out["answers"][0]["reason"]) == 200
    assert len(out["answers"][0]["wanted_data"]) == 200
    # a missing or null line is simply empty
    raw["answers"][0]["wanted_data"] = None
    del raw["relations"][1]["reason"]
    out = validate_theme_draft(raw)
    assert out["answers"][0]["wanted_data"] == "" and out["relations"][1]["reason"] == ""


@pytest.mark.parametrize("op", ["filter", "group", "trend", "join"])
def test_ops_that_cannot_run_yet_are_accepted_and_folded_into_memo(op: str) -> None:
    raw = _bad(answers=[_ans(mode="data", op=op, target=None, kind="c3", property="c2")])
    a = validate_theme_draft(raw)["answers"][0]
    assert a["mode"] == "memo" and a["op"] == op


def test_a_memo_answer_may_have_no_op() -> None:
    a = validate_theme_draft(_bad(answers=[_ans(mode="memo", op=None, direction=None)]))
    assert a["answers"][0]["op"] is None and a["answers"][0]["mode"] == "memo"


def test_not_an_object_is_refused() -> None:
    for raw in ([], "x", None, 3):
        with pytest.raises(ThemeDraftError):
            validate_theme_draft(raw)


def test_the_limits_of_a_draft_and_of_an_addition() -> None:
    many = [
        {**GOOD["concepts"][0], "key": f"k{i}", "label_ja": f"語{i}"} for i in range(41)
    ]
    with pytest.raises(ThemeDraftError):
        validate_theme_draft(_bad(concepts=many))
    assert len(validate_theme_draft(_bad(concepts=many[:40]))["concepts"]) == 40
    # an addition: 10 concepts / 20 relations at most, no answers taken
    add = {"concepts": many[:11], "relations": [], "answers": [], "suggested_questions": []}
    with pytest.raises(ThemeDraftError):
        validate_theme_draft(add, expand=True)
    add["concepts"] = many[:10]
    add["relations"] = [{"from": "k0", "to": "k1", "rel": "lowers", "reason": ""}] * 21
    with pytest.raises(ThemeDraftError):
        validate_theme_draft(add, expand=True)
    add["relations"] = add["relations"][:20]
    add["answers"] = GOOD["answers"]
    out = validate_theme_draft(add, expand=True)
    assert out["answers"] == [] and out["theme_label"] == ""


def test_the_json_schema_is_closed() -> None:
    schema = theme_draft_json_schema()
    assert schema["additionalProperties"] is False
    for part in ("concepts", "relations", "answers"):
        item = schema["properties"][part]["items"]
        assert item["additionalProperties"] is False
        assert set(item["required"]) <= set(item["properties"])
    cprops = schema["properties"]["concepts"]["items"]["properties"]
    assert cprops["kind"]["enum"] == ["class", "property"]
    assert cprops["label_ja"]["maxLength"] > 0 and cprops["reason"]["maxLength"] > 0
    rprops = schema["properties"]["relations"]["items"]["properties"]
    assert rprops["rel"]["enum"] == list(RELATIONS)
    aprops = schema["properties"]["answers"]["items"]["properties"]
    assert aprops["mode"]["enum"] == ["knowledge", "data", "memo"]
    assert aprops["wanted_data"]["maxLength"] > 0
    ex = theme_draft_json_schema(expand=True)
    assert ex["properties"]["concepts"]["maxItems"] == 10
    assert ex["properties"]["relations"]["maxItems"] == 20


def test_the_prompt_carries_names_only_and_forbids_code() -> None:
    msg = theme_draft_user_message(
        ["熱伝導率を下げるものは？"],
        [{"slug": "sample", "label_ja": "試料", "label_en": "sample", "kind": "class"}],
        [{"curie": "cmso:Material", "label": "Material", "kind": "class"}],
        language="ja",
    )
    assert "熱伝導率を下げるものは？" in msg
    assert "sample | 試料 | sample | class" in msg
    assert "cmso:Material | Material | class" in msg
    assert "Japanese" in msg  # the output-language block
    assert "fill theme_label" in msg  # no theme name given
    assert "Do NOT write SPARQL" in THEME_DRAFT_SYSTEM_PROMPT
    assert "NEVER invent" in THEME_DRAFT_SYSTEM_PROMPT


def test_the_prompt_asks_for_the_knowledge_when_factors_or_parts_are_asked() -> None:
    p = THEME_DRAFT_SYSTEM_PROMPT
    assert "FACTORS" in p and "PARTS" in p and "thermal conductivity" in p
    assert "lowers" in p and "raises" in p and "part" in p
    # the same prompt hands over no value of a data file
    assert "you have none" in p


def test_a_given_theme_name_is_passed_and_not_asked_for_again() -> None:
    msg = theme_draft_user_message(["q"], [], [], label="熱電材料")
    assert "熱電材料" in msg and "leave theme_label empty" in msg


def test_propose_sets_the_schema_for_the_call_and_puts_it_back() -> None:
    llm = _FakeLLM(json.dumps(GOOD))
    out = propose_theme_draft(
        llm, questions=["q"], known_terms=[], known_standards=[], language="ja"
    )
    assert out["concepts"][0]["label_ja"] == "熱伝導率"
    assert len(llm.calls) == 1
    assert llm.calls[0][2] == theme_draft_json_schema()
    assert llm.response_schema is None


def test_propose_reads_a_fenced_answer() -> None:
    llm = _FakeLLM("結果です\n```json\n" + json.dumps(GOOD) + "\n```")
    out = propose_theme_draft(llm, questions=["q"], known_terms=[], known_standards=[])
    assert len(out["concepts"]) == 3


def test_propose_retries_once_with_the_reason_then_succeeds() -> None:
    bad = json.dumps(_bad(concepts=[{**GOOD["concepts"][0], "kind": "individual"}]))
    llm = _FakeLLM(bad, json.dumps(GOOD))
    out = propose_theme_draft(llm, questions=["q"], known_terms=[], known_standards=[])
    assert len(out["concepts"]) == 3 and len(llm.calls) == 2
    assert "refused" in llm.calls[1][1] and "concepts[0].kind" in llm.calls[1][1]


def test_propose_gives_up_after_the_second_bad_answer() -> None:
    llm = _FakeLLM("not json at all", "{still not")
    with pytest.raises(ThemeDraftError):
        propose_theme_draft(llm, questions=["q"], known_terms=[], known_standards=[])
    assert len(llm.calls) == 2


def test_a_client_without_a_schema_slot_is_simply_called() -> None:
    class Plain:
        def complete(self, system_prompt: str, user_message: str) -> str:
            return json.dumps(GOOD)

    out = propose_theme_draft(Plain(), questions=["q"], known_terms=[], known_standards=[])
    assert out["suggested_questions"] == ["組成ごとの熱伝導率の最小値は？"]
    assert out["theme_label"] == "熱電材料"


# ---- 「ここを広げる」 -------------------------------------------------------------

ADD = {
    "theme_label": "",
    "concepts": [
        {
            "key": "n1",
            "label_ja": "ナノ析出物",
            "label_en": "nano precipitate",
            "aliases": [],
            "kind": "class",
            "use": "new",
            "reason": "中周波フォノンを散乱",
        }
    ],
    "relations": [{"from": "n1", "rel": "lowers", "to": "c2", "reason": "界面散乱"}],
    "answers": [],
    "suggested_questions": [],
}
ON_MAP = [
    {"key": "c1", "label_ja": "熱伝導率", "label_en": "Thermal Conductivity", "kind": "property"},
    {"key": "c2", "label_ja": "格子熱伝導率", "label_en": "lattice", "kind": "property"},
]


def test_expand_passes_the_map_names_and_the_focus_and_no_data() -> None:
    msg = theme_expand_user_message("c2", ON_MAP, [Q], [], [], language="ja")
    assert "c2 | 格子熱伝導率 | lattice | property" in msg
    assert "# Concept to expand (key)\nc2" in msg
    assert Q in msg
    assert "ONE concept" in THEME_EXPAND_SYSTEM_PROMPT
    assert "Do NOT write SPARQL" in THEME_EXPAND_SYSTEM_PROMPT


def test_expand_returns_the_addition_validated_with_the_small_limits() -> None:
    llm = _FakeLLM(json.dumps(ADD))
    out = propose_theme_expand(
        llm, focus_key="c2", concepts=ON_MAP, questions=[Q], existing=[], standards=[]
    )
    assert [c["key"] for c in out["concepts"]] == ["n1"]
    assert out["relations"][0]["to"] == "c2"  # a key already on the map is kept
    assert out["answers"] == []
    assert llm.calls[0][2] == theme_draft_json_schema(expand=True)
    assert llm.calls[0][0] == THEME_EXPAND_SYSTEM_PROMPT
    assert llm.response_schema is None


def test_expand_refuses_a_focus_that_is_not_on_the_map_without_calling() -> None:
    llm = _FakeLLM(json.dumps(ADD))
    with pytest.raises(ThemeDraftError):
        propose_theme_expand(
            llm, focus_key="zz", concepts=ON_MAP, questions=[], existing=[], standards=[]
        )
    assert llm.calls == []


def test_expand_retries_once_then_gives_up() -> None:
    bad = json.dumps({**ADD, "concepts": [{**ADD["concepts"][0], "kind": "x"}]})
    llm = _FakeLLM(bad, json.dumps(ADD))
    out = propose_theme_expand(
        llm, focus_key="c1", concepts=ON_MAP, questions=[], existing=[], standards=[]
    )
    assert len(out["concepts"]) == 1 and len(llm.calls) == 2
    llm2 = _FakeLLM("no", "no")
    with pytest.raises(ThemeDraftError):
        propose_theme_expand(
            llm2, focus_key="c1", concepts=ON_MAP, questions=[], existing=[], standards=[]
        )


def test_an_addition_never_reuses_a_key_already_on_the_map() -> None:
    clash = {**ADD, "concepts": [{**ADD["concepts"][0], "key": "c2"}]}
    with pytest.raises(ThemeDraftError, match="used twice"):
        validate_theme_draft(clash, expand=True, existing_keys=["c1", "c2"])
    assert validate_theme_draft(clash, expand=True)["concepts"][0]["key"] == "c2"  # 既知の鍵なし
    # propose_theme_expand は地図の鍵を渡す: 衝突する答えは 1 回やり直し、2 回目が通れば返す
    llm = _FakeLLM(json.dumps(clash), json.dumps(ADD))
    out = propose_theme_expand(
        llm, focus_key="c1", concepts=ON_MAP, questions=[], existing=[], standards=[]
    )
    assert [c["key"] for c in out["concepts"]] == ["n1"] and len(llm.calls) == 2
