"""14e-2 入口 1 — 機械が作った問いを押す（``ask_ledger``）と、AI も demo-agent も呼ばずに台帳の走者が
答える。入口 2 の demo-agent の返事の ``ledger`` が通る。例は作り物。"""

# ruff: noqa: RUF001, E501
from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism import ledger_answer as la
from asterism import pair_relations as pr
from fastapi.testclient import TestClient

from tests.test_chat_attach import KEY, _app, _new
from tests.test_crosswalk_api import _AUTH

_AT = "2026-11-18T00:00:00+00:00"
_TERMS = [
    {"id": "a", "name": "甲", "kind": "class", "role": None},
    {"id": "b", "name": "乙", "kind": "class", "role": None},
    {"id": "c", "name": "丙", "kind": "class", "role": None},
]


def _install(root: Path, source: str = "lab", dataset_id: str = "ds-lab") -> None:
    d = root / dataset_id
    (d / "source").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": dataset_id, "status": "active"}), encoding="utf-8")
    (d / "source" / "terms.json").write_text(json.dumps(_TERMS, ensure_ascii=False), encoding="utf-8")
    (d / "source" / "claims.json").write_text(
        json.dumps([{"id": "k1", "iri": "https://x/k1", "title": "甲の知見", "source_url": "https://s/1"}]),
        encoding="utf-8",
    )
    ld = pr.empty_ledger(source)
    ld["dataset_id"] = dataset_id
    ld["pairs"] = {
        "a|b": {"key": "a|b", "a": "a", "b": "b", "state": "relation", "rel": "sub", "from": "a",
                "to": "b", "evidence": ["k1"], "claims": ["k1"], "claims_total": 2,
                "confirmed": {"at": _AT}},
        "b|c": {"key": "b|c", "a": "b", "b": "c", "state": "relation", "rel": "sub", "from": "c",
                "to": "b", "evidence": [], "claims": [], "claims_total": 0},
    }
    pr.save_ledger(root, ld, at=_AT)


def _name(root: Path) -> str:
    loaded = la.load_source(root, "lab")
    assert loaded is not None
    return next(q["name"] for q in la.ledger_questions(*loaded) if q["op"] == "kinds")


def _setup(tmp_path: Path):
    app, _ = _app(tmp_path)
    root = tmp_path / "registry"
    _install(root)
    calls: list[httpx.Request] = []

    def boom(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(500)

    app.state.demo_agent_url = "http://demo.test"
    app.state.ask_transport = httpx.MockTransport(boom)
    return TestClient(app, headers=_AUTH), root, calls


def _post(client: TestClient, sid: str, body: dict, **kw):
    return client.post(f"/api/chat/sessions/{sid}/turns", json=body, **kw)


def test_pressing_a_question_answers_without_the_ai_or_demo_agent(tmp_path: Path) -> None:
    client, root, calls = _setup(tmp_path)
    with client:
        sid = _new(client)
        name = _name(root)
        r = _post(client, sid, {"ask_ledger": {"source": "lab", "name": name}})  # AI の鍵なし
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert calls == []  # demo-agent は呼ばない
        assert turn["user"]["text"] == "乙 の一種" and turn["by"] == "machine"
        assert turn["state"] == "none" and turn["route"] == "ask"
        ans = turn["reply"]["answer"]
        assert ans["text"] == "「乙 の一種」として、次の 2 件が記録されています。"
        assert ans["answered"] is True and ans["verified_tools"] == [] and ans["citations"] == []
        led = ans["ledger"]
        assert led["questions"] == [{"source": "lab", "name": name, "text": "乙 の一種", "count": 2,
                                  "shown": 2}]
        assert [i["label"] for i in led["items"]] == ["丙", "甲"]
        by = {i["label"]: i for i in led["items"]}
        assert by["甲"]["confirmed"] is True and by["丙"]["confirmed"] is False
        assert by["甲"]["evidence"] == [{"title": "甲の知見", "source_url": "https://s/1"}]
        assert by["甲"]["evidence_total"] == 2
        assert led["unconfirmed"] == [{"source": "lab", "key": "b|c"}]
        assert led["unconfirmed_count"] == 1 and led["confirmed_count"] == 1
        # 話に残る・履歴は text だけ
        saved = client.get(f"/api/chat/sessions/{sid}").json()["turns"][-1]
        assert saved["reply"]["answer"]["ledger"]["unconfirmed_count"] == 1


def test_a_vanished_question_or_source_is_answered_in_words_not_an_error(tmp_path: Path) -> None:
    client, root, calls = _setup(tmp_path)
    with client:
        sid = _new(client)
        gone = _post(client, sid, {"ask_ledger": {"source": "lab", "name": "lq_kinds_zzz", "text": "丙 の一種"}})
        assert gone.status_code == 201
        t = gone.json()["turn"]
        assert t["user"]["text"] == "丙 の一種" and t["reply"]["answer"] is None
        assert "見つかりません" in t["reply"]["text"]
        assert t["reply"]["notes"][0]["kind"] == "ask_ledger_declined"
        nosrc = _post(client, sid, {"ask_ledger": {"source": "nope", "name": _name(root)}, "lang": "en"})
        assert nosrc.status_code == 201
        assert "Could not read" in nosrc.json()["turn"]["reply"]["text"]
        assert calls == []


def test_exactly_one_structured_turn_and_a_safe_name(tmp_path: Path) -> None:
    client, root, _ = _setup(tmp_path)
    with client:
        sid = _new(client)
        both = _post(client, sid, {"text": "x", "ask_ledger": {"source": "lab", "name": _name(root)}})
        assert both.status_code == 422
        assert _post(client, sid, {"ask_ledger": {"source": "lab", "name": "kinds_b"}}).status_code == 422
        assert _post(client, sid, {"ask_ledger": {"source": "lab", "name": "lq_../x"}}).status_code == 422


def test_the_ledger_from_demo_agent_is_carried_to_the_answer(tmp_path: Path) -> None:
    client, root, _ = _setup(tmp_path)
    loaded = la.load_source(root, "lab")
    assert loaded is not None
    q = next(q for q in la.ledger_questions(*loaded) if q["op"] == "kinds")
    block = la.reply_block([la.answer(*loaded, q)])
    sent = {"answer": "甲と丙です。", "answered": True, "citations": [], "ledger": block}
    with client:
        client.app.state.ask_transport = httpx.MockTransport(lambda req: httpx.Response(200, json=sent))
        sid = _new(client)
        r = _post(client, sid, {"text": "乙の一種は？", "intent": "ask"}, headers=KEY)
        assert r.status_code == 201, r.text
        ans = r.json()["turn"]["reply"]["answer"]
        assert ans["ledger"] == block and ans["verified_tools"] == []
        # 形が違えば印は付けない・欄が無ければ null
        sent["ledger"] = {"items": "x"}
        bad = _post(client, _new(client), {"text": "乙の一種は？", "intent": "ask"}, headers=KEY)
        assert bad.json()["turn"]["reply"]["answer"]["ledger"] is None
        del sent["ledger"]
        none = _post(client, _new(client), {"text": "乙の一種は？", "intent": "ask"}, headers=KEY)
        assert none.json()["turn"]["reply"]["answer"]["ledger"] is None


def test_it_answers_from_a_copy_without_claims(tmp_path: Path) -> None:
    client, root, _ = _setup(tmp_path)
    (root / "ds-lab" / "source" / "claims.json").unlink()  # 根拠の知見が無くても答える
    with client:
        sid = _new(client)
        r = _post(client, sid, {"ask_ledger": {"source": "lab", "name": _name(root)}})
        led = r.json()["turn"]["reply"]["answer"]["ledger"]
        assert led["items"][0]["evidence"] == [] and led["unconfirmed_count"] == 1


def test_the_pressed_question_becomes_the_title_unless_the_user_named_it(tmp_path: Path) -> None:
    client, root, _ = _setup(tmp_path)
    with client:
        name = _name(root)
        sid = _new(client)
        assert _post(client, sid, {"ask_ledger": {"source": "lab", "name": name}}).status_code == 201
        assert client.get(f"/api/chat/sessions/{sid}").json()["title"] == "乙 の一種"
        named = _new(client)
        client.patch(f"/api/chat/sessions/{named}", json={"title": "自分の題"})
        _post(client, named, {"ask_ledger": {"source": "lab", "name": name}})
        assert client.get(f"/api/chat/sessions/{named}").json()["title"] == "自分の題"


def test_a_broken_ledger_body_is_a_refusal_not_a_500(tmp_path: Path) -> None:
    client, root, _ = _setup(tmp_path)
    ld = pr.load_ledger(root, "lab")
    ld["pairs"]["x|y"] = {"key": "x|y", "state": "relation", "rel": "sub"}  # from/to が無い
    ld["origin"] = ["junk", {"from": "a"}]
    pr.save_ledger(root, ld, at=_AT)
    with client:
        sid = _new(client)
        r = _post(client, sid, {"ask_ledger": {"source": "lab", "name": "lq_kinds_b", "text": "乙 の一種"}})
        assert r.status_code == 201, r.text
        assert r.json()["turn"]["reply"]["answer"]["ledger"]["items_total"] == 2  # 壊れた件だけ落として答える
        import unittest.mock as um

        with um.patch.object(la, "find_question", side_effect=RuntimeError("boom")):
            bad = _post(client, sid, {"ask_ledger": {"source": "lab", "name": "lq_kinds_b"}})
        assert bad.status_code == 201 and bad.json()["turn"]["reply"]["answer"] is None


def test_a_refusal_without_any_question_text_has_no_user_side(tmp_path: Path) -> None:
    client, _, _ = _setup(tmp_path)
    with client:
        sid = _new(client)
        t = _post(client, sid, {"ask_ledger": {"source": "lab", "name": "lq_kinds_zzz"}}).json()["turn"]
        assert t["user"] is None and "lq_" not in json.dumps(t, ensure_ascii=False)
        assert client.get(f"/api/chat/sessions/{sid}").json()["title"] == ""  # 題は付けない
        assert "見つかりません" in t["reply"]["text"]


def test_history_never_carries_the_text_of_an_answer_that_used_unconfirmed_relations(tmp_path: Path) -> None:
    from asterism_api import chat_turns

    def turn(text: str, ledger: dict | None) -> dict:
        return {"user": {"text": "問い"}, "state": "none",
                "reply": {"answer": {"text": text, "ledger": ledger}}}

    session = {"turns": [
        turn("丙は乙の一種です。", {"unconfirmed_count": 1, "conflict_count": 0, "items": [], "questions": []}),
        turn("競合する関係があります。", {"unconfirmed_count": 0, "conflict_count": 2}),
        turn("甲は乙の一種です。", {"unconfirmed_count": 0, "conflict_count": 0}),
        turn("台帳を使わない答え。", None),
        turn("Gamma is a kind of B.", {"unconfirmed_count": 2}),
    ]}
    said = [m["content"] for m in chat_turns.ask_history(session) if m["role"] == "assistant"]
    assert said[0] == said[1] == chat_turns._ASK_UNCONFIRMED_SAID["ja"]
    assert said[2:4] == ["甲は乙の一種です。", "台帳を使わない答え。"]
    assert said[4] == chat_turns._ASK_UNCONFIRMED_SAID["en"]
    assert "丙" not in json.dumps(said, ensure_ascii=False)


def test_ledger_of_reads_an_old_shape_and_ignores_unknown_fields() -> None:
    from asterism_api import chat_turns

    old = {"questions": [], "items": [{"label": "x"}], "unconfirmed": [{"source": "s", "key": "k"}],
           "unconfirmed_count": 1, "confirmed_count": 0, "future_field": 1}
    got = chat_turns._ledger_of(old)
    assert got is not None and got["conflicts"] == [] and got["conflict_count"] == 0
    assert got["items_total"] == 1 and got["future_field"] == 1
    assert chat_turns._ledger_of({"items": 1, "questions": []}) is None
