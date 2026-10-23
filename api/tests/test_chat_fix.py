"""11n — 指摘・訂正の文／聞き返しの禁止／外す指示の決定論／形の誤りの注記。"""

# ruff: noqa: RUF001
from __future__ import annotations

import json

import pytest

from asterism_api import chat_intent, chat_route, chat_turns

DRAFT = {
    "concepts": [
        {"key": "a", "label_ja": "空間群", "label_en": "space group", "aliases": []},
        {"key": "b", "label_ja": "三斜晶系", "label_en": "triclinic", "aliases": ["三斜"]},
        {"key": "c", "label_ja": "熱伝導率", "label_en": "", "aliases": []},
    ],
    "relations": [
        {"from": "b", "rel": "sub", "to": "a"},
        {"from": "b", "rel": "part", "to": "c"},
    ],
}


@pytest.mark.parametrize(
    "text",
    [
        "空間群から結晶系の種類が伸びているので間違いっぽい",
        "これは違う",
        "おかしい",
        "That is wrong",
        "it should be a part of X",
    ],
)
def test_fix_words_hit(text: str) -> None:
    assert chat_intent.has_fix_words(text)


@pytest.mark.parametrize(
    "text", ["空間群の種類は？", "これは間違い？", "is this wrong?", "熱伝導率を下げるものは", ""]
)
def test_fix_words_miss(text: str) -> None:
    assert not chat_intent.has_fix_words(text)


class _Stub:
    def __init__(self, out: str) -> None:
        self.out = out
        self.seen: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        return self.out


def _route(out: str, **over):
    kw = dict(text="ありがとう", history=[], lang="ja", theme_label=None, has_draft=False,
              n_terms=0)  # fmt: skip
    kw.update(over)
    stub = _Stub(out)
    return chat_route.route_message(stub, **kw), stub


def test_draft_in_progress_adds_the_note_unless_a_question() -> None:
    out = json.dumps({"mode": "propose"})
    _, stub = _route(out, has_draft=True, text="粒界の方が大事")
    assert "Draft in progress: yes" in stub.seen[0][1] and chat_route.DRAFT_NOTE in stub.seen[0][1]
    _, stub = _route(out, has_draft=True, text="これでいい？")
    assert chat_route.DRAFT_NOTE not in stub.seen[0][1]
    _, stub = _route(out, has_draft=False, text="粒界の方が大事")
    assert chat_route.DRAFT_NOTE not in stub.seen[0][1]
    assert "Never ask the person to explain more" in chat_route.ROUTER_SYSTEM


@pytest.mark.parametrize(
    "reply",
    [
        "もう少し詳しく教えていただけますか？",
        "具体的にどの部分ですか",
        "Could you give more details?",
    ],
)
def test_asking_back_reply_is_not_a_chat(reply: str) -> None:
    with pytest.raises(chat_route.RouteError):
        _route(json.dumps({"mode": "chat", "reply": reply}))


def test_normal_chat_reply_still_passes() -> None:
    r, _ = _route(json.dumps({"mode": "chat", "reply": "どういたしまして。"}))
    assert r.reply == "どういたしまして。"


def test_parse_remove_by_sentence() -> None:
    ops = chat_intent.parse_remove("「三斜晶系 は 空間群 の一種」を外して", DRAFT)
    assert ops == [
        {"op": "remove", "target": "relation", "from": "b", "rel": "sub", "to": "a", "reason": ""}
    ]
    ops = chat_intent.parse_remove("triclinic is a part of 熱伝導率 を外して", DRAFT)
    assert ops and ops[0]["rel"] == "part" and ops[0]["to"] == "c"


def test_parse_remove_not_a_kind() -> None:
    ops = chat_intent.parse_remove("三斜晶系は空間群の一種ではない", DRAFT)
    assert ops and (ops[0]["from"], ops[0]["rel"], ops[0]["to"]) == ("b", "sub", "a")
    ops = chat_intent.parse_remove("triclinic is not a part of 熱伝導率", DRAFT)
    assert ops and ops[0]["rel"] == "part"
    assert chat_intent.parse_remove("空間群は三斜晶系の一種ではない", DRAFT) is None


def test_parse_remove_by_name() -> None:
    assert chat_intent.parse_remove("三斜を外して", DRAFT) == [
        {"op": "remove", "target": "concept", "key": "b", "reason": ""}
    ]


def test_parse_remove_no_match_goes_to_the_ai() -> None:
    assert chat_intent.parse_remove("存在しない語を外して", DRAFT) is None
    assert chat_intent.parse_remove("粒界を広げて", DRAFT) is None
    assert chat_intent.parse_remove("三斜を外して", None) is None


def test_shape_note_has_reason_and_next_step() -> None:
    raw = "ops[2].to: 'c9' is not a key on the graph (add the concept first)"
    ja = chat_turns._shape_note("AI の答えが決まった形になりませんでした", raw, "ja")
    assert 'ops[2].to: 知らない鍵 "c9"' in ja and "対象を 1 つに絞って言い直して" in ja
    en = chat_turns._shape_note("The AI's answer did not take the expected shape", raw, "en")
    assert "ops[2].to" in en and "Narrow it to one target" in en
    long = chat_turns._shape_why("x: " + "y" * 500, "en")
    assert len(long) == 120
