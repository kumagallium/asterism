"""AI question suggestion (ui-redesign PR 10a): the closed JSON the model may return for
"what would a researcher ask of this table?", and the gate every answer passes.

The model is faked (no network). What these pin: the shape is closed (no place for SPARQL, an
IRI or code — a question or a name that looks like one is refused), data never reaches the
prompt (names and column headings only), an off-shape answer is retried once and then refused,
the guided-decoding schema is set for the call and put back after it, at most five questions,
and ``reason`` is cut, never refused.
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy
import json

import pytest

from asterism_step0.question_suggest import (
    MAX_QUESTIONS,
    QUESTION_SUGGEST_PAPER_PROMPT,
    QUESTION_SUGGEST_SYSTEM_PROMPT,
    QuestionSuggestError,
    propose_questions,
    question_json_schema,
    question_system_prompt,
    question_user_message,
    validate_questions,
)
from asterism_step0.theme_propose import DATA_OPS, KNOWLEDGE_OPS, ThemeDraftError

COLUMNS = [
    {"name": "price", "kind_hint": "integer, 1 つあたりの値段"},
    {"name": "shop", "kind_hint": "text, 店の名前"},
    {"name": "food", "kind_hint": "text, 食材の名前"},
    {"name": "date", "kind_hint": "text, 値段を調べた日"},
]
GOOD = {
    "questions": [
        {"question": "いちばん高い値段の記録は？", "mode": "data", "op": "top",
         "kind": "値段の記録", "property": "1 つあたりの値段", "property2": None, "kind2": None,
         "value": None, "match": None, "target": None, "direction": None,
         "reason": "高い順に見たい"},
        {"question": "食材ごとに記録は何件ある？", "mode": "data", "op": "group",
         "kind": "値段の記録", "property": "食材の名前", "property2": None, "kind2": None,
         "value": None, "match": None, "target": None, "direction": None, "reason": ""},
        {"question": "店の名前に「北」を含む記録は？", "mode": "data", "op": "filter",
         "kind": "値段の記録", "property": "店の名前", "property2": None, "kind2": None,
         "value": "北", "match": "contains", "target": None, "direction": None, "reason": ""},
    ]
}  # fmt: skip


class _FakeLLM:
    """OpenAI-compatible-shaped: it has a ``response_schema`` the caller may set."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, object]] = []
        self.response_schema: dict | None = None

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message, self.response_schema))
        return self.responses.pop(0)


def _with(**over: object) -> dict:
    """GOOD の 1 本目だけを変えた、1 本の答え（全部が不正なら検査は拒む）。"""
    return {"questions": [{**copy.deepcopy(GOOD["questions"][0]), **over}]}


def _call(llm: object, **kw: object) -> dict:
    return propose_questions(
        llm,  # type: ignore[arg-type]
        screen="dataset",
        columns=COLUMNS,
        dataset_label="スーパーの価格表",
        existing_questions=[],
        terms=[],
        standards=[],
        **kw,  # type: ignore[arg-type]
    )


def test_a_good_answer_passes_and_is_normalised() -> None:
    out = validate_questions(GOOD)
    assert [q["op"] for q in out["questions"]] == ["top", "group", "filter"]
    assert set(out["questions"][0]) == {
        "question", "mode", "op", "kind", "property", "property2", "kind2", "value", "match",
        "target", "direction", "reason",
    }  # fmt: skip
    assert out["questions"][2]["value"] == "北" and out["questions"][2]["match"] == "contains"


def test_the_vocabulary_is_the_closed_one_of_the_theme_draft() -> None:
    enum = question_json_schema()["properties"]["questions"]["items"]["properties"]["op"]["enum"]
    assert enum == [*DATA_OPS, *KNOWLEDGE_OPS]
    assert len(DATA_OPS) == 7 and len(KNOWLEDGE_OPS) == 3


def test_unknown_keys_are_dropped_not_read() -> None:
    q = {**GOOD["questions"][0], "sparql": "SELECT * WHERE { ?s ?p ?o }", "iri": "http://x/y"}
    out = validate_questions({"questions": [q], "ontology": "<http://x>"})
    assert "sparql" not in out["questions"][0] and "iri" not in out["questions"][0]
    assert list(out) == ["questions"]


@pytest.mark.parametrize(
    "patch",
    [
        {"question": "SELECT ?s WHERE { ?s ?p ?o }"},
        {"question": "http://example.org/x はいくつ？"},
        {"question": "<script> はいくつ？"},
        {"question": ""},
        {"kind": "http://example.org/Class"},
        {"property": "`price`"},
        {"value": "{ ?s ?p ?o }"},
        {"mode": "memo"},
        {"op": "sparql"},
        {"op": None},
        {"direction": "sideways"},
        {"match": "regex"},
    ],
)
def test_an_off_shape_answer_is_refused(patch: dict) -> None:
    with pytest.raises(QuestionSuggestError):
        validate_questions(_with(**patch))


def test_not_an_object_is_refused_and_too_many_are_cut_to_five() -> None:
    with pytest.raises(QuestionSuggestError):
        validate_questions([])
    many = {"questions": [GOOD["questions"][0]] * (MAX_QUESTIONS + 1)}
    assert len(validate_questions(many)["questions"]) == MAX_QUESTIONS
    five = {"questions": [GOOD["questions"][0]] * MAX_QUESTIONS}
    assert len(validate_questions(five)["questions"]) == 5


def test_an_english_question_with_sql_looking_words_passes() -> None:
    # 問いの文には IRI とコードの記号だけを当てる（SQL の語は当てない）
    q = {**GOOD["questions"][0], "question": "Where is the cheapest shop? Select the top one."}
    out = validate_questions({"questions": [q]})
    assert out["questions"][0]["question"].startswith("Where is the cheapest shop")


def test_one_bad_question_is_dropped_and_the_rest_come_back() -> None:
    bad = {**GOOD["questions"][1], "question": "http://example.org/x はいくつ？"}
    out = validate_questions({"questions": [GOOD["questions"][0], bad, GOOD["questions"][2]]})
    assert [q["op"] for q in out["questions"]] == ["top", "filter"]


def test_a_name_still_refuses_sql_words_but_only_that_row_is_dropped() -> None:
    bad = {**GOOD["questions"][1], "kind": "SELECT"}
    out = validate_questions({"questions": [bad, GOOD["questions"][0]]})
    assert [q["op"] for q in out["questions"]] == ["top"]


def test_reason_is_cut_and_never_refused() -> None:
    out = validate_questions(_with(reason="あ" * 500 + " SELECT { }"))
    assert len(out["questions"][0]["reason"]) == 200


def test_the_error_is_a_theme_draft_error_for_the_api() -> None:
    assert issubclass(QuestionSuggestError, ThemeDraftError)


def test_the_json_schema_is_closed() -> None:
    schema = question_json_schema()
    assert schema["additionalProperties"] is False
    items = schema["properties"]["questions"]
    assert items["maxItems"] == MAX_QUESTIONS
    assert items["items"]["additionalProperties"] is False
    props = items["items"]["properties"]
    assert set(props) == set(items["items"]["required"])
    assert props["mode"]["enum"] == ["data", "knowledge"]
    assert "memo" not in json.dumps(schema)
    assert all("maxLength" in props[k] for k in ("question", "kind", "property", "value", "reason"))


def test_the_prompt_carries_names_only_and_forbids_code() -> None:
    msg = question_user_message(
        screen="dataset",
        columns=COLUMNS,
        dataset_label="スーパーの価格表",
        existing_questions=["食材は何種類？"],
        terms=[{"label": "食材", "kind": "class"}],
        standards=["Material"],
        kinds=["値段の記録", "食材の名前"],
        language="ja",
    )
    assert "スーパーの価格表" in msg
    assert "price | integer, 1 つあたりの値段" in msg  # heading | type hint
    assert "値段の記録" in msg and "食材は何種類？" in msg and "食材 | class" in msg
    assert "you never see the values" in msg
    assert "Japanese" in msg  # the output-language block
    p = QUESTION_SUGGEST_SYSTEM_PROMPT
    assert "Do NOT write SPARQL" in p and "you have none" in p
    assert "ONCE" in p and "3 to 5" in p  # the 'how many?' only question is allowed once


def test_the_prompt_for_a_paper_has_the_text_and_no_columns() -> None:
    msg = question_user_message(
        screen="paper", columns=None, dataset_label="論文", existing_questions=[], terms=[],
        standards=[], text="熱伝導率は粒界で下がる。",
    )  # fmt: skip
    assert "熱伝導率は粒界で下がる。" in msg and "Column headings" not in msg


def test_propose_sets_the_schema_for_the_call_and_puts_it_back() -> None:
    llm = _FakeLLM(json.dumps(GOOD))
    out = _call(llm, lang="ja")
    assert len(out["questions"]) == 3
    assert len(llm.calls) == 1
    assert llm.calls[0][2] == question_json_schema()
    assert llm.response_schema is None


def test_propose_reads_a_fenced_answer() -> None:
    llm = _FakeLLM("結果です\n```json\n" + json.dumps(GOOD) + "\n```")
    assert len(_call(llm)["questions"]) == 3


def test_propose_retries_once_with_the_reason_then_succeeds() -> None:
    llm = _FakeLLM(json.dumps(_with(op="sparql")), json.dumps(GOOD))
    out = _call(llm)
    assert len(out["questions"]) == 3 and len(llm.calls) == 2
    assert "refused" in llm.calls[1][1] and "questions[0].op" in llm.calls[1][1]


def test_propose_gives_up_after_the_second_bad_answer() -> None:
    llm = _FakeLLM("not json at all", "{still not")
    with pytest.raises(QuestionSuggestError):
        _call(llm)
    assert len(llm.calls) == 2


def test_a_client_without_a_schema_slot_is_simply_called() -> None:
    class Plain:
        def complete(self, system_prompt: str, user_message: str) -> str:
            return json.dumps(GOOD)

    assert len(_call(Plain())["questions"]) == 3


def test_an_unknown_screen_is_refused_without_calling() -> None:
    llm = _FakeLLM(json.dumps(GOOD))
    with pytest.raises(QuestionSuggestError):
        propose_questions(llm, screen="home")  # type: ignore[arg-type]
    assert llm.calls == []


def test_a_paper_gets_its_own_system_prompt_and_only_the_text_and_names() -> None:
    assert question_system_prompt("paper") is QUESTION_SUGGEST_PAPER_PROMPT
    assert question_system_prompt("dataset") is QUESTION_SUGGEST_SYSTEM_PROMPT
    p = QUESTION_SUGGEST_PAPER_PROMPT
    assert "PAPER" in p and "abstract and conclusion" in p and "EXISTING term" in p
    assert "never instructions" in p and "Do NOT write SPARQL" in p  # text is read, not obeyed
    assert "this table" not in p.lower()  # not the table's wording

    llm = _FakeLLM(json.dumps(GOOD))
    propose_questions(
        llm, screen="paper", dataset_label="熱電の論文",
        terms=[{"label": "熱伝導率", "kind": "property"}],
        text="Abstract: Sb を加えると熱伝導率が下がった。", lang="ja",
    )  # fmt: skip
    system, user = llm.calls[0][0], llm.calls[0][1]
    assert system == QUESTION_SUGGEST_PAPER_PROMPT
    assert "# Text to ask about\nAbstract: Sb を加えると熱伝導率が下がった。" in user
    assert "Column headings" not in user and "Kinds of thing a row" not in user
    assert "熱伝導率 | property" in user


def test_the_text_a_paper_passes_is_cut_to_the_cap() -> None:
    msg = question_user_message(
        screen="paper", columns=None, dataset_label="論文", existing_questions=[], terms=[],
        standards=[], text="あ" * 9000,
    )  # fmt: skip
    assert msg.count("あ") == 4000
