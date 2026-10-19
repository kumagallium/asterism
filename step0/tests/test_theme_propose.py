"""AI theme draft: the closed JSON the model may fill, and the gate every answer passes.

The model is faked (no network). What these pin: the shape is closed (no place for SPARQL,
an IRI or code), data never reaches the prompt, an off-shape answer is retried once and
then refused, and the guided-decoding schema is set for the call and put back after it.
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy
import json

import pytest

from asterism_step0.theme_propose import (
    THEME_DRAFT_SYSTEM_PROMPT,
    ThemeDraftError,
    propose_theme_draft,
    theme_draft_json_schema,
    theme_draft_user_message,
    validate_theme_draft,
)

GOOD = {
    "terms": [
        {
            "label_ja": "電池セル",
            "label_en": "battery cell",
            "aliases": ["cell"],
            "kind": "class",
            "use": "new",
            "reason": "答えに要る",
        },
        {
            "label_ja": "容量",
            "label_en": "capacity",
            "aliases": [],
            "kind": "property",
            "use": "standard:quantitykind:Capacity",
            "reason": "",
        },
    ],
    "relations": [{"subject": "容量", "object": "quantitykind:Capacity", "relation": "quantity"}],
    "selections": [
        {
            "question": "容量 > 3 のセルは？",
            "op": "filter",
            "kind": "電池セル",
            "property": "容量",
            "value": None,
            "fits": False,
            "reason": "",
        }
    ],
    "suggested_questions": ["正極ごとの平均容量は？"],
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


def test_a_good_answer_passes_and_is_normalised() -> None:
    out = validate_theme_draft(_bad(suggested_questions=["  正極ごとの  平均容量は？ "]))
    assert out["suggested_questions"] == ["正極ごとの 平均容量は？"]
    assert set(out) == {"terms", "relations", "selections", "suggested_questions"}
    assert out["selections"][0]["question"] == "容量 > 3 のセルは？"  # the person's own text


def test_unknown_keys_are_dropped_not_read() -> None:
    raw = copy.deepcopy(GOOD)
    raw["terms"][0]["sparql"] = "SELECT * WHERE { ?s ?p ?o }"
    raw["terms"][0]["iri"] = "https://example.org/x"
    raw["query"] = "DROP ALL"
    out = validate_theme_draft(raw)
    assert "sparql" not in out["terms"][0] and "iri" not in out["terms"][0]
    assert "query" not in out


@pytest.mark.parametrize(
    "patch",
    [
        {"terms": "nope"},
        {"terms": [{**GOOD["terms"][0], "kind": "individual"}]},
        {"terms": [{**GOOD["terms"][0], "use": "https://example.org/x"}]},
        {"terms": [{**GOOD["terms"][0], "use": "existing:Bad Slug"}]},
        {"terms": [{**GOOD["terms"][0], "label_ja": ""}]},
        {"terms": [{**GOOD["terms"][0], "label_en": "SELECT ?s WHERE"}]},
        {"terms": [{**GOOD["terms"][0], "label_en": "<script>"}]},
        {"terms": [{**GOOD["terms"][0], "aliases": ["see https://example.org/x"]}]},
        {"terms": [{**GOOD["terms"][0], "label_ja": "あ" * 81}]},
        {"relations": [{"subject": "a", "object": "b", "relation": "partOf"}]},
        {"relations": [{"subject": "a", "object": "http://example.org/x", "relation": "sub"}]},
        {"selections": [{**GOOD["selections"][0], "op": "delete"}]},
        {"selections": [{**GOOD["selections"][0], "fits": "yes"}]},
        {"selections": [{**GOOD["selections"][0], "kind": "{ ?s ?p ?o }"}]},
        {"suggested_questions": ["a"] * 6},
    ],
)
def test_an_off_shape_answer_is_refused(patch: dict) -> None:
    with pytest.raises(ThemeDraftError):
        validate_theme_draft(_bad(**patch))


def test_not_an_object_is_refused() -> None:
    for raw in ([], "x", None, 3):
        with pytest.raises(ThemeDraftError):
            validate_theme_draft(raw)


def test_the_json_schema_is_closed() -> None:
    schema = theme_draft_json_schema()
    assert schema["additionalProperties"] is False
    for part in ("terms", "relations", "selections"):
        item = schema["properties"][part]["items"]
        assert item["additionalProperties"] is False
        assert set(item["required"]) <= set(item["properties"])
    assert schema["properties"]["terms"]["items"]["properties"]["kind"]["enum"] == [
        "class",
        "property",
    ]
    # no free-form string without a bound
    props = schema["properties"]["terms"]["items"]["properties"]
    assert props["label_ja"]["maxLength"] > 0 and props["reason"]["maxLength"] > 0


def test_the_prompt_carries_names_only_and_forbids_code() -> None:
    msg = theme_draft_user_message(
        ["容量が最大のセルは？"],
        [{"slug": "sample", "label_ja": "試料", "label_en": "sample", "kind": "class"}],
        [{"curie": "cmso:Material", "label": "Material", "kind": "class"}],
        language="ja",
    )
    assert "容量が最大のセルは？" in msg
    assert "sample | 試料 | sample | class" in msg
    assert "cmso:Material | Material | class" in msg
    assert "Japanese" in msg  # the output-language block
    assert "Do NOT write SPARQL" in THEME_DRAFT_SYSTEM_PROMPT
    assert "NEVER invent" in THEME_DRAFT_SYSTEM_PROMPT


def test_propose_sets_the_schema_for_the_call_and_puts_it_back() -> None:
    llm = _FakeLLM(json.dumps(GOOD))
    out = propose_theme_draft(
        llm, questions=["q"], known_terms=[], known_standards=[], language="ja"
    )
    assert out["terms"][0]["label_ja"] == "電池セル"
    assert len(llm.calls) == 1
    assert llm.calls[0][2] == theme_draft_json_schema()
    assert llm.response_schema is None


def test_propose_reads_a_fenced_answer() -> None:
    llm = _FakeLLM("結果です\n```json\n" + json.dumps(GOOD) + "\n```")
    out = propose_theme_draft(llm, questions=["q"], known_terms=[], known_standards=[])
    assert len(out["terms"]) == 2


def test_propose_retries_once_with_the_reason_then_succeeds() -> None:
    bad = json.dumps(_bad(terms=[{**GOOD["terms"][0], "kind": "individual"}]))
    llm = _FakeLLM(bad, json.dumps(GOOD))
    out = propose_theme_draft(llm, questions=["q"], known_terms=[], known_standards=[])
    assert len(out["terms"]) == 2 and len(llm.calls) == 2
    assert "refused" in llm.calls[1][1] and "terms[0].kind" in llm.calls[1][1]


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
    assert out["suggested_questions"] == ["正極ごとの平均容量は？"]
