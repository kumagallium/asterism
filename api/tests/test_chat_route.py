"""chat_route（11m）— 振り分けの純関数。"""

from __future__ import annotations

import json

import pytest

from asterism_api import chat_route


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


def test_three_modes() -> None:
    r, stub = _route(json.dumps({"mode": "chat", "reply": "どうも。"}))
    assert (r.mode, r.reply) == ("chat", "どうも。")
    assert stub.seen[0][0].startswith("# Asterism chat router")
    r, _ = _route(json.dumps({"mode": "ask", "reply": "ignored"}))
    assert (r.mode, r.reply) == ("ask", "")
    r, _ = _route(json.dumps({"mode": "propose"}))
    assert (r.mode, r.reply) == ("propose", "")


def test_fenced_json_is_read() -> None:
    r, _ = _route('```json\n{"mode": "chat", "reply": "はい"}\n```')
    assert r.reply == "はい"


@pytest.mark.parametrize(
    "out",
    [
        "not json",
        "[1]",
        json.dumps({"mode": "other", "reply": "x"}),
        json.dumps({"reply": "x"}),
        json.dumps({"mode": "chat", "reply": "  "}),
        json.dumps({"mode": "chat"}),
        json.dumps({"mode": "chat", "reply": 3}),
    ],
)
def test_broken_shapes_raise(out: str) -> None:
    with pytest.raises(chat_route.RouteError):
        _route(out)


def test_user_message_carries_context_and_language() -> None:
    hist = [{"user": "u1", "assistant": "a1"}]
    _, stub = _route(
        json.dumps({"mode": "propose"}),
        history=hist, lang="en", theme_label="熱電", has_draft=True, n_terms=3, n_datasets=2,
    )  # fmt: skip
    user = stub.seen[0][1]
    wants = ("English", "Theme: 熱電", "Draft in progress: yes", "Approved terms: 3")
    wants += ("Datasets: 2", "person: u1", "assistant: a1", "# Message\nありがとう")
    for want in wants:
        assert want in user


def test_history_is_last_six_turns_and_clips_reply() -> None:
    turns = [
        {"state": "none", "user": {"text": f"u{i}"}, "reply": {"text": "r" * 300}}
        for i in range(9)
    ]
    turns.insert(8, {"state": "reverted", "user": {"text": "gone"}, "reply": {"text": "x"}})
    h = chat_route.history_of({"turns": turns})
    assert len(h) == 6 and all(len(x["assistant"]) == 200 for x in h)
    assert "gone" not in [x["user"] for x in h]
