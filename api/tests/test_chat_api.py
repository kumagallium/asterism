"""話（チャット）の api — ui-redesign PR 11a。

ストアは本物のインメモリ ``rdflib.Dataset``（``test_themes_api`` と同じ差し替え口）。AI は呼ばない
（11a）。確かめること: 鍵が無ければ断る・文の手番で下書きができて「提案」・手で足す手番の検査
（循環は落ちて notes に残る）・戻す（前の下書き・最初の手番なら下書きごと消える）・✓ で
themes.json にテーマ・用語が鋳造され来歴の batch が ``<話>:<手番>``・✓ のあとの文は ``extend``
つきの下書き・名前を直すとテーマ名も変わる・捨てると下書きは消えテーマは残る・
``GET /api/sky`` の ``proposed`` は鍵つきのときだけ。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

from asterism import chat_sessions, themes
from fastapi.testclient import TestClient

from asterism_api import chat_turns
from tests.test_crosswalk_api import _AUTH
from tests.test_themes_api import Q_LOWER, _app

Q2 = "格子熱伝導率を下げる要素は？"

#: 手で足す 3 つの差分（熱伝導率・粒界散乱・「粒界散乱は熱伝導率を下げる」）
CONCEPT_A = {"op": "add_concept", "key": "h1", "label_ja": "熱伝導率", "label_en": "",
             "aliases": [], "kind": "property", "use": "new", "reason": ""}  # fmt: skip
CONCEPT_B = {"op": "add_concept", "key": "h2", "label_ja": "粒界散乱", "label_en": "",
             "aliases": [], "kind": "class", "use": "new", "reason": ""}  # fmt: skip
REL_AB = {"op": "add_relation", "from": "h2", "rel": "lowers", "to": "h1", "reason": "粒界で散乱"}
#: 答え方（これが無いと ✓ は「答え方が決まった問いが無い」で断られる）
ASK = {"op": "add_question", "question": "熱伝導率を下げるものは？", "mode": "knowledge",
       "op_kind": "factors", "target": "h1", "direction": "lowers", "kind": None,
       "property": None, "wanted_data": "", "reason": ""}  # fmt: skip


def _client(tmp_path: Path):
    app, ds = _app(tmp_path)
    return TestClient(app, headers=_AUTH), ds


def _new(client: TestClient, title: str = "") -> str:
    r = client.post("/api/chat/sessions", json={"title": title} if title else {})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _say(client: TestClient, sid: str, text: str, **extra: object) -> dict:
    r = client.post(f"/api/chat/sessions/{sid}/turns", json={"text": text, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _ops(client: TestClient, sid: str, ops: list[dict]) -> dict:
    r = client.post(f"/api/chat/sessions/{sid}/turns", json={"ops": ops})
    assert r.status_code == 201, r.text
    return r.json()


def _draft_ids(tmp_path: Path) -> list[str]:
    return [d["id"] for d in themes.list_drafts(tmp_path / "registry")]


def test_every_route_needs_the_token(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as anon:
        calls = [
            anon.get("/api/chat/sessions"),
            anon.post("/api/chat/sessions", json={}),
            anon.get("/api/chat/sessions/cabcdefghij"),
            anon.patch("/api/chat/sessions/cabcdefghij", json={"title": "x"}),
            anon.delete("/api/chat/sessions/cabcdefghij"),
            anon.post("/api/chat/sessions/cabcdefghij/turns", json={"text": "x"}),
            anon.post("/api/chat/sessions/cabcdefghij/turns/t1/confirm", json={}),
            anon.post("/api/chat/sessions/cabcdefghij/turns/t1/revert"),
        ]
    assert [c.status_code in (401, 503) for c in calls] == [True] * len(calls)


def test_list_create_get_and_unknown(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/api/chat/sessions").json() == {"sessions": []}
        sid = _new(client)
        (row,) = client.get("/api/chat/sessions").json()["sessions"]
        assert row["id"] == sid and row["title"] == ""
        assert (row["turns"], row["proposed"]) == (0, 0)
        got = client.get(f"/api/chat/sessions/{sid}").json()
        assert got["turns"] == [] and got["theme_id"] is None and got["draft_id"] is None
        assert client.get("/api/chat/sessions/cnosuchone0").status_code == 404
        assert client.get("/api/chat/sessions/..%2Fx").status_code == 404
        assert client.post("/api/chat/sessions", json={"title": "a\nb"}).status_code == 422


def test_text_turn_makes_a_proposed_draft(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        out = _say(client, sid, Q_LOWER)
        turn, session, draft = out["turn"], out["session"], out["draft"]
        assert turn["id"] == "t1" and turn["state"] == "proposed" and turn["by"] == "human"
        assert turn["before"] is None  # 下書きを作った手番
        assert turn["batch"] == f"{sid}:t1"
        assert turn["user"] == {"text": Q_LOWER, "attachments": []}
        reply = turn["reply"]
        assert set(reply) == {"text", "ops", "sentences", "questions", "bindings", "notes",
                              "answer", "table"}  # fmt: skip
        assert "覚えました" in reply["text"] and Q_LOWER in reply["text"]
        assert reply["ops"] == [] and reply["answer"] is None and reply["table"] is None
        (note,) = reply["notes"]
        assert note["kind"] == "ai_unset" and note["choice"] is None
        assert session["draft_id"] == draft["draft_id"] and session["title"] == Q_LOWER[:20]
        assert draft["questions"] == [Q_LOWER] and draft["concepts"] == []
        assert draft["session_id"] == sid
        stored = themes.load_draft(tmp_path / "registry", draft["draft_id"])
        assert stored["questions"] == [Q_LOWER] and stored["step"] == 2
        (ans,) = stored["answers"]
        assert ans == chat_turns.blank_answer(Q_LOWER)
        (row,) = client.get("/api/chat/sessions").json()["sessions"]
        assert (row["turns"], row["proposed"]) == (1, 1)


def test_text_turn_input_checks(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        url = f"/api/chat/sessions/{sid}/turns"
        assert client.post(url, json={}).status_code == 422
        assert client.post(url, json={"text": "a", "ops": [CONCEPT_A]}).status_code == 422
        assert client.post(url, json={"text": "   "}).status_code == 422
        assert client.post(url, json={"text": "あ" * 2001}).status_code == 422
        assert client.post(url, json={"ops": []}).status_code == 422
        # 下書きが無いうちは手で足せない
        r = client.post(url, json={"ops": [CONCEPT_A]})
        assert r.status_code == 422 and "先に問いを書いてください" in r.text
        assert client.post(url, json={"text": "a", "lang": "fr"}).status_code == 422
        assert client.get(f"/api/chat/sessions/{sid}").json()["turns"] == []


def test_second_text_adds_a_question(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        first = _say(client, sid, Q_LOWER)
        second = _say(client, sid, Q2)
        assert second["draft"]["draft_id"] == first["draft"]["draft_id"]  # 下書きは 1 つ
        assert second["draft"]["questions"] == [Q_LOWER, Q2]
        t = second["turn"]
        assert t["id"] == "t2" and t["state"] == "proposed"
        assert t["before"]["questions"] == [Q_LOWER]  # 手番の前の控え
        stored = themes.load_draft(tmp_path / "registry", second["draft"]["draft_id"])
        assert [a["question"] for a in stored["answers"]] == [Q_LOWER, Q2]
        # 同じ問いをもう一度 — 変わらないので提案にならない
        again = _say(client, sid, Q2)["turn"]
        assert again["state"] == "none" and again["before"] is None
        (row,) = client.get("/api/chat/sessions").json()["sessions"]
        assert (row["turns"], row["proposed"]) == (3, 2)
        en = _say(client, sid, "What raises it?", lang="en")["turn"]["reply"]["text"]
        assert en.startswith("Noted the question")


def test_ops_turn_changes_the_draft_and_returns_sentences(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        out = _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])
        turn = out["turn"]
        assert turn["user"] is None and turn["by"] == "human" and turn["state"] == "proposed"
        assert turn["before"]["concepts"] == []
        reply = turn["reply"]
        assert [o["op"] for o in reply["ops"]] == ["add_concept", "add_concept", "add_relation"]
        assert len(reply["sentences"]) == 3
        assert reply["text"] == "・".join(reply["sentences"])
        assert "粒界散乱" in reply["sentences"][2] and "熱伝導率" in reply["sentences"][2]
        assert reply["notes"] == []
        d = out["draft"]
        assert {c["label"] for c in d["concepts"]} == {"熱伝導率", "粒界散乱"}
        assert d["relations"] == [{"from": "h2", "to": "h1", "rel": "lowers"}]
        stored = themes.load_draft(tmp_path / "registry", d["draft_id"])
        assert all(c["by"] == "human" for c in stored["concepts"])
        # 送り直し（もう当たっている）は変わらない
        dup = _ops(client, sid, [CONCEPT_A, REL_AB])["turn"]
        assert dup["state"] == "none" and dup["before"] is None
        assert [n["kind"] for n in dup["reply"]["notes"]] == ["dropped", "dropped"]
        assert all("すでにあります" in n["text"] for n in dup["reply"]["notes"])


def test_cycle_and_unknown_are_dropped_with_notes(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        c3 = {**CONCEPT_B, "key": "h3", "label_ja": "結晶粒"}
        c4 = {**CONCEPT_B, "key": "h4", "label_ja": "多結晶"}
        sub = {"op": "add_relation", "from": "h3", "rel": "sub", "to": "h4", "reason": ""}
        back = {"op": "add_relation", "from": "h4", "rel": "sub", "to": "h3", "reason": ""}
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, c3, c4, sub])
        # 一種の向きが輪になる関係は落ちて、理由が notes に残る（ほかは当たる）
        out = _ops(client, sid, [back, {**c3, "key": "h5", "label_ja": "単結晶"}])["turn"]
        assert out["state"] == "proposed" and len(out["reply"]["ops"]) == 1
        (note,) = out["reply"]["notes"]
        assert note["kind"] == "dropped" and "輪になります" in note["text"]
        # 項目が種類を下げる（種類と項目の混ざり）は落ちる — 何も変わらないので提案にならない
        loop = {"op": "add_relation", "from": "h1", "rel": "lowers", "to": "h2", "reason": ""}
        bad = _ops(client, sid, [loop])["turn"]
        assert bad["state"] == "none" and bad["reply"]["ops"] == []
        assert [n["kind"] for n in bad["reply"]["notes"]] == ["dropped"]
        assert bad["reply"]["text"]  # 文は空にしない
        # 知らない key を指す差分は形の検査で 422（何も変わらない）
        ghost = {"op": "add_relation", "from": "zz", "rel": "lowers", "to": "h1", "reason": ""}
        r = client.post(f"/api/chat/sessions/{sid}/turns", json={"ops": [ghost]})
        assert r.status_code == 422
        # SPARQL や IRI のような名前は閉じた検査が断る
        iri = {**CONCEPT_A, "key": "h9", "label_ja": "http://example.org/x"}
        r = client.post(f"/api/chat/sessions/{sid}/turns", json={"ops": [iri]})
        assert r.status_code == 422


def test_revert_goes_back_to_the_previous_draft(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        t1 = _say(client, sid, Q_LOWER)
        t2 = _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])
        _say(client, sid, Q2)
        did = t1["draft"]["draft_id"]
        # t2 を戻す = t2 以降の未確認（t3 も）が一緒に取り消され、t1 の直後の下書きに戻る
        r = client.post(f"/api/chat/sessions/{sid}/turns/{t2['turn']['id']}/revert")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["reverted"] == ["t2", "t3"] and out["turn"]["state"] == "reverted"
        assert out["draft"]["questions"] == [Q_LOWER] and out["draft"]["concepts"] == []
        assert out["session"]["draft_id"] == did
        got = client.get(f"/api/chat/sessions/{sid}").json()
        assert [t["state"] for t in got["turns"]] == ["proposed", "reverted", "reverted"]
        assert all(t["before"] is None for t in got["turns"][1:])
        # 戻した手番は戻せない・確認済みでないものも戻せない
        r = client.post(f"/api/chat/sessions/{sid}/turns/t2/revert")
        assert r.status_code == 409
        assert client.post(f"/api/chat/sessions/{sid}/turns/t9/revert").status_code == 404
        # 最初の手番を戻すと下書きごと消える
        r = client.post(f"/api/chat/sessions/{sid}/turns/t1/revert")
        assert r.json()["reverted"] == ["t1"] and r.json()["draft"] is None
        assert r.json()["session"]["draft_id"] is None
        assert _draft_ids(tmp_path) == []


def test_confirm_places_the_theme_and_the_next_text_extends_it(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        did = chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"]
        t3 = _say(client, sid, Q2)["turn"]
        r = client.post(f"/api/chat/sessions/{sid}/turns/{t3['id']}/confirm", json={})
        assert r.status_code == 200, r.text
        out = r.json()
        commit = out["commit"]
        assert commit is not None, out["turn"]["reply"]["notes"]
        assert commit["batch"] == f"{sid}:t3" and commit["theme_id"] == did
        assert set(commit["minted"]) and commit["lines"] >= 1
        assert out["confirmed"] == ["t1", "t2", "t3"]  # 未確認の返事は全部まとめて
        assert out["session"]["theme_id"] == did and out["session"]["draft_id"] is None
        got = client.get(f"/api/chat/sessions/{sid}").json()
        assert [t["state"] for t in got["turns"]] == ["confirmed"] * 3
        for t in got["turns"]:
            assert t["commit"]["batch"] == f"{sid}:t3" and t["before"] is None
        # themes.json にテーマ・ストアに用語と、来歴の batch つきの線
        stored = themes.load_themes_file(tmp_path / "registry")
        theme = next(t for t in stored["themes"] if t["id"] == did)
        assert len(theme["questions"]) == 1  # 答え方の決まった問い（道具の名前）
        assert len(theme["memo_questions"]) == 2  # 決まっていない問いも捨てない
        assert _draft_ids(tmp_path) == []
        a = client.get(f"/api/themes/{did}/answers").json()
        knowledge = next(x for x in a["answers"] if x["mode"] == "knowledge")
        (item,) = knowledge["knowledge"]["items"]
        assert item["label"] == "粒界散乱" and item["batch"] == f"{sid}:t3"
        assert item["drafted_by"] == "human"
        # ✓ のあとの次の文は extend 付きの新しい下書き
        nxt = _say(client, sid, "電子熱伝導率は？")
        assert nxt["turn"]["state"] == "proposed" and nxt["turn"]["before"] is None
        stored_draft = themes.load_draft(tmp_path / "registry", nxt["draft"]["draft_id"])
        assert stored_draft["extend"] == did and nxt["draft"]["draft_id"] != did
        # 確認済みの返事は ✓ も戻すも 409
        assert client.post(f"/api/chat/sessions/{sid}/turns/t1/confirm").status_code == 409
        assert client.post(f"/api/chat/sessions/{sid}/turns/t1/revert").status_code == 409


def test_confirm_refusal_is_a_note_not_an_error(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        t2 = _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])["turn"]  # 答え方が無い
        r = client.post(f"/api/chat/sessions/{sid}/turns/{t2['id']}/confirm", json={"lang": "ja"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["commit"] is None and out["confirmed"] == []
        (note,) = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "commit_error"]
        assert note["text"]
        got = client.get(f"/api/chat/sessions/{sid}").json()
        assert [t["state"] for t in got["turns"]] == ["proposed", "proposed"]  # 何も変わらない
        assert got["draft_id"] is not None and got["theme_id"] is None
        stored = themes.load_themes_file(tmp_path / "registry") or {"themes": []}
        assert got["draft_id"] not in {t["id"] for t in stored["themes"]}  # 何も置かれていない
        # 2 度目も note は 1 つのまま（積み上がらない）
        again = client.post(f"/api/chat/sessions/{sid}/turns/{t2['id']}/confirm").json()
        assert len([n for n in again["turn"]["reply"]["notes"] if n["kind"] == "commit_error"]) == 1


def test_replies_avoid_the_words_the_screen_must_not_show(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        turns = [_say(client, sid, Q_LOWER), _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])]
        turns.append(_ops(client, sid, [CONCEPT_A, REL_AB]))  # 落ちる理由の文も
        turns.append(_say(client, sid, Q2, lang="en"))
        said = json.dumps([t["turn"]["reply"] for t in turns], ensure_ascii=False)
        for word in ("そう", "線", "ことば", "下書き", "置く"):
            assert word not in said, word


def test_commit_body_is_the_ui_build_commit_body() -> None:
    draft = {
        "id": "d1", "label": " ", "questions": ["Q?"], "extend": "t0",
        "concepts": [
            {"key": "a", "label_ja": "あ", "kind": "class", "use": "existing:x", "slug": "x",
             "by": "ai"},
            {"key": "b", "label_ja": "い", "kind": "property", "use": "new", "slug": "bb"},
        ],
        "relations": [
            {"from": "a", "rel": "lowers", "to": "b", "reason": "r", "by": "ai", "problem": None},
            {"from": "a", "rel": "sub", "to": "b", "problem": "cycle"},
        ],
        "answers": [
            {**chat_turns.blank_answer("Q?"), "mode": "knowledge", "op": "factors",
             "target": "zz", "direction": "lowers"},
        ],
    }  # fmt: skip
    body = chat_turns.commit_body(draft, "en")
    assert body["id"] == "d1" and body["extend"] == "t0" and body["lang"] == "en"
    assert body["label"] == "Q?" and body["ai_drafted"] is True
    assert [c["slug"] for c in body["concepts"]] == ["", "bb"]  # existing: は slug を送らない
    assert [r["rel"] for r in body["relations"]] == ["lowers"]  # problem のある関係は送らない
    (ans,) = body["answers"]  # 対象が図に無い答え方はメモの問いになる
    assert ans["mode"] == "memo" and ans["memo"] == "none"
    done = {**draft, "answers": [{**draft["answers"][0], "target": "b"}]}
    (ok,) = chat_turns.commit_body(done, "ja")["answers"]
    assert ok["mode"] == "knowledge" and ok["memo"] is None


def test_rename_changes_the_theme_and_the_draft(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        r = client.patch(f"/api/chat/sessions/{sid}", json={"title": "熱電材料"})
        assert r.status_code == 200 and r.json()["title"] == "熱電材料"
        did = r.json()["draft_id"]
        assert themes.load_draft(tmp_path / "registry", did)["label"] == "熱電材料"
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        assert client.post(f"/api/chat/sessions/{sid}/turns/t2/confirm").json()["commit"]
        r = client.patch(f"/api/chat/sessions/{sid}", json={"title": "熱電の話"})
        assert r.status_code == 200
        theme = next(t for t in themes.load_themes_file(tmp_path / "registry")["themes"]
                     if t["id"] == did)  # fmt: skip
        assert theme["label"] == "熱電の話"
        assert client.get("/api/chat/sessions").json()["sessions"][0]["title"] == "熱電の話"
        for bad in ("", "  ", "a\nb", "あ" * 81):
            r = client.patch(f"/api/chat/sessions/{sid}", json={"title": bad})
            assert r.status_code == 422
        r = client.patch("/api/chat/sessions/cnosuchone0", json={"title": "x"})
        assert r.status_code == 404


def test_delete_drops_the_draft_and_keeps_the_theme(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        # 未確認だけの話 — 下書きも消える
        s1 = _new(client)
        _say(client, s1, Q_LOWER)
        assert len(_draft_ids(tmp_path)) == 1
        assert client.delete(f"/api/chat/sessions/{s1}").status_code == 204
        assert _draft_ids(tmp_path) == []
        assert client.get(f"/api/chat/sessions/{s1}").status_code == 404
        assert client.delete(f"/api/chat/sessions/{s1}").status_code == 404
        # ✓ したテーマは残る
        s2 = _new(client)
        _say(client, s2, Q_LOWER)
        _ops(client, s2, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        tid = client.post(f"/api/chat/sessions/{s2}/turns/t2/confirm").json()["commit"]["theme_id"]
        _say(client, s2, Q2)
        assert len(_draft_ids(tmp_path)) == 1
        assert client.delete(f"/api/chat/sessions/{s2}").status_code == 204
        assert _draft_ids(tmp_path) == []
        assert tid in {t["id"] for t in themes.load_themes_file(tmp_path / "registry")["themes"]}
        assert client.get("/api/chat/sessions").json() == {"sessions": []}


def test_sky_proposed_only_with_the_key(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    app = client.app
    with client, TestClient(app) as anon:
        sid = _new(client)
        assert client.get("/api/sky").json()["proposed"] == []  # 鍵つき・下書き無し
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])
        mine = client.get("/api/sky").json()
        (card,) = mine["proposed"]
        assert card["session_id"] == sid and card["theme_label"] == Q_LOWER[:20]
        assert {c["label"] for c in card["concepts"]} == {"熱伝導率", "粒界散乱"}
        assert card["relations"] == [{"from": "h2", "to": "h1", "rel": "lowers"}]
        en = client.get("/api/sky", params={"lang": "en"}).json()["proposed"][0]
        assert en["session_id"] == sid
        # 鍵なし・違う鍵なら欄ごと無し（公開済みの事実だけ）
        assert "proposed" not in anon.get("/api/sky").json()
        assert "proposed" not in anon.get("/api/sky", headers={"X-Asterism-Token": "no"}).json()
        token = _AUTH["X-Asterism-Token"]
        bearer = anon.get("/api/sky", headers={"Authorization": f"Bearer {token}"})
        assert "proposed" in bearer.json()
        assert "熱伝導率" not in json.dumps(anon.get("/api/sky").json(), ensure_ascii=False)
        # 概念が 1 つも無い下書き（問いだけ）は札にしない
        s2 = _new(client)
        _say(client, s2, "別の問い")
        assert len(client.get("/api/sky").json()["proposed"]) == 1
        # 話の無い下書きでも落ちない
        chat_sessions.delete_session(tmp_path / "registry", sid)
        (orphan,) = client.get("/api/sky").json()["proposed"]
        assert orphan["session_id"] is None


def _set(client: TestClient, sid: str, **answer: object):
    return client.post(f"/api/chat/sessions/{sid}/turns", json={"set_answer": answer})


def test_set_answer_decides_a_question_and_the_check_passes(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])
        mine = {"question": Q_LOWER, "mode": "knowledge", "op": "factors", "direction": "lowers"}
        # 存在しない問い・無い概念・種別違い・排他は 422
        nope = _set(client, sid, **{**mine, "question": "ない問い", "target": "h1"})
        assert nope.status_code == 422 and "この話にありません" in nope.text
        assert _set(client, sid, **{**mine, "target": "zz"}).status_code == 422
        bad = _set(client, sid, question=Q_LOWER, mode="data", op="count", property="h1")
        assert bad.status_code == 422 and "答え方が決まっていません" in bad.text
        both = client.post(
            f"/api/chat/sessions/{sid}/turns",
            json={"text": "x", "set_answer": {**mine, "target": "h1"}},
        )
        assert both.status_code == 422
        # data: kind に property の key は無効（count は kind=class）
        wrong = _set(client, sid, question=Q_LOWER, mode="data", op="count", kind="h1")
        assert wrong.status_code == 422
        ok = _set(client, sid, **{**mine, "target": "h1"})
        assert ok.status_code == 201, ok.text
        turn = ok.json()["turn"]
        assert turn["state"] == "proposed" and turn["user"] is None and turn["before"]
        assert "熱伝導率" in turn["reply"]["text"] and turn["reply"]["ops"] == []
        draft = themes.load_draft(tmp_path / "registry", ok.json()["draft"]["draft_id"])
        (row,) = draft["answers"]
        assert row["mode"] == "knowledge" and row["target"] == "h1" and row["by"] == "human"
        # ✓ が通る
        c = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={})
        assert c.status_code == 200, c.text
        assert c.json()["commit"] is not None and c.json()["commit"]["theme_id"]


def test_set_answer_without_a_draft_and_revert(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        r = _set(client, sid, question=Q_LOWER, mode="knowledge", op="parts", target="h1")
        assert r.status_code == 422 and "先に問いを書いてください" in r.text
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A])
        t = _set(client, sid, question=Q_LOWER, mode="data", op="range", property="h1").json()
        reply = t["turn"]["reply"]["text"]
        assert "熱伝導率" in reply
        r = client.post(f"/api/chat/sessions/{sid}/turns/{t['turn']['id']}/revert")
        assert r.status_code == 200, r.text
        draft = themes.load_draft(tmp_path / "registry", r.json()["draft"]["draft_id"])
        assert draft["answers"][0]["mode"] == "memo"
        for word in ("そう", "線", "ことば", "下書き", "置く", "pending"):
            assert word not in reply


# ---- checker の指摘（S1〜S6・N3〜N8）の固定 -------------------------------------------------


def test_question_length_300_ok_301_refused(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        url = f"/api/chat/sessions/{sid}/turns"
        r = client.post(url, json={"text": "あ" * 301})
        assert r.status_code == 422 and "問いは 300 字までです。短く書いてください" in r.text
        r = client.post(url, json={"text": "a" * 301, "lang": "en"})
        assert r.status_code == 422 and "300 characters" in r.text
        assert client.get(f"/api/chat/sessions/{sid}").json()["turns"] == []
        assert client.post(url, json={"text": "あ" * 300}).status_code == 201
        # 201 字でも通る
        assert client.post(url, json={"text": "い" * 201}).status_code == 201


def test_question_with_iri_or_code_is_refused(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        url = f"/api/chat/sessions/{sid}/turns"
        for bad in ("http://example.org/x を調べて", "SELECT ?s WHERE { ?s ?p ?o }"):
            r = client.post(url, json={"text": bad})
            assert r.status_code == 422 and "問いに IRI やコードは入れられません" in r.text
        r = client.post(url, json={"text": "see https://x.test", "lang": "en"})
        assert r.status_code == 422 and "IRI or code" in r.text
        assert _draft_ids(tmp_path) == []


def test_confirm_only_the_last_proposed_turn(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        _say(client, sid, Q2)
        base = f"/api/chat/sessions/{sid}/turns"
        r = client.post(f"{base}/t1/confirm", json={})
        assert r.status_code == 409 and "最後の提案に ✓ してください" in r.text
        r = client.post(f"{base}/t1/confirm", json={"lang": "en"})
        assert r.status_code == 409 and "latest proposal" in r.text
        r = client.post(f"{base}/t3/confirm", json={})
        assert r.status_code == 200 and r.json()["confirmed"] == ["t1", "t2", "t3"]
        assert r.json()["commit"]["batch"] == f"{sid}:t3"


def test_confirm_or_revert_a_none_turn_is_409(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        dup = _say(client, sid, Q_LOWER)["turn"]
        assert dup["state"] == "none"
        base = f"/api/chat/sessions/{sid}/turns/{dup['id']}"
        assert client.post(f"{base}/confirm", json={}).status_code == 409
        assert client.post(f"{base}/revert").status_code == 409


def test_revert_keeps_the_renamed_title(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _say(client, sid, Q2)
        _say(client, sid, "電子熱伝導率は？")
        url = f"/api/chat/sessions/{sid}"
        assert client.patch(url, json={"title": "私の名前"}).status_code == 200
        r = client.post(f"/api/chat/sessions/{sid}/turns/t2/revert")
        assert r.status_code == 200, r.text
        assert r.json()["draft"]["theme_label"] == "私の名前"
        got = client.get(f"/api/chat/sessions/{sid}").json()
        draft = themes.load_draft(tmp_path / "registry", got["draft_id"])
        assert draft["label"] == "私の名前" and got["title"] == "私の名前"


def test_save_failure_after_confirm_is_retried_once(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        real = chat_sessions.save_session
        calls = {"n": 0}

        def flaky(root, session):
            calls["n"] += 1
            if calls["n"] <= 1:
                raise OSError("disk")
            return real(root, session)

        monkeypatch.setattr(chat_sessions, "save_session", flaky)
        r = client.post(f"/api/chat/sessions/{sid}/turns/t2/confirm", json={})
        assert r.status_code == 200, r.text
        assert r.json()["confirmed"] == ["t1", "t2"] and calls["n"] == 2

        # 2 度とも落ちたら 500・テーマは保存されている
        s2 = _new(client)
        _say(client, s2, "別の問い")
        _ops(client, s2, [{**CONCEPT_A, "key": "h1", "label_ja": "電気伝導率"}, ASK])
        monkeypatch.setattr(
            chat_sessions, "save_session", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
        )
        r = client.post(f"/api/chat/sessions/{s2}/turns/t2/confirm", json={})
        assert r.status_code == 500
        assert "話を保存できませんでした（テーマ " in r.json()["detail"]
        assert "は保存されています）" in r.json()["detail"]


def test_revert_after_a_confirm_that_lost_its_save_is_409(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        monkeypatch.setattr(
            chat_sessions, "save_session", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
        )
        assert client.post(f"/api/chat/sessions/{sid}/turns/t2/confirm", json={}).status_code == 500
        monkeypatch.undo()
        # 話はまだ proposed のまま・下書きは確認で消えている
        r = client.post(f"/api/chat/sessions/{sid}/turns/t2/revert")
        assert r.status_code == 409 and "この提案は確認済みです" in r.text
        assert _draft_ids(tmp_path) == []


def test_text_turn_after_the_theme_vanished_starts_a_new_one(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB, ASK])
        assert client.post(f"/api/chat/sessions/{sid}/turns/t2/confirm", json={}).status_code == 200
        data = themes.load_themes_file(tmp_path / "registry")
        data["themes"] = []
        themes._save_themes(tmp_path / "registry", data)
        out = _say(client, sid, "電子熱伝導率は？")
        notes = out["turn"]["reply"]["notes"]
        assert any(n["kind"] == "info" and "前のテーマは消えています" in n["text"] for n in notes)
        assert out["session"]["theme_id"] is None
        assert themes.load_draft(tmp_path / "registry", out["draft"]["draft_id"]).get("extend") in (
            None,
            "",
        )


def test_turn_on_a_missing_session_is_404_before_the_store(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        r = client.post("/api/chat/sessions/cabcdefghi/turns", json={"text": "a"})
        assert r.status_code == 404
        assert client.get("/api/chat/sessions/cabcdefghi%0A").status_code in (404, 422)


def test_sky_with_proposed_is_not_cached(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client, TestClient(client.app) as anon:
        r = client.get("/api/sky")
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["vary"] == "X-Asterism-Token, Authorization"
        assert "no-store" not in anon.get("/api/sky").headers.get("cache-control", "")


def test_sky_proposed_absent_for_empty_credentials_and_unset_token(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client, TestClient(client.app) as anon:
        assert "proposed" not in anon.get("/api/sky", headers={"X-Asterism-Token": ""}).json()
        assert "proposed" not in anon.get("/api/sky", headers={"Authorization": "Bearer "}).json()
    import rdflib

    from asterism_api.main import build_app
    from tests.test_crosswalk_api import _DatasetClient, _settings

    cfg = _settings(tmp_path / "x")
    cfg.api_token = ""
    app = build_app(cfg, oxigraph_client=_DatasetClient(rdflib.Dataset()), start_watcher=False)
    with TestClient(app) as open_client:
        assert "proposed" not in open_client.get("/api/sky").json()
        hdr = {"X-Asterism-Token": ""}
        assert "proposed" not in open_client.get("/api/sky", headers=hdr).json()


def test_name_length_80_ok_81_refused(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        assert client.post("/api/chat/sessions", json={"title": "あ" * 80}).status_code == 201
        assert client.post("/api/chat/sessions", json={"title": "あ" * 81}).status_code == 422
        sid = _new(client)
        url = f"/api/chat/sessions/{sid}"
        assert client.patch(url, json={"title": "い" * 80}).status_code == 200
        assert client.patch(url, json={"title": "い" * 81}).status_code == 422


def test_english_ai_unset_and_dropped_notes(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        t1 = _say(client, sid, Q_LOWER, lang="en")["turn"]
        (unset,) = [n for n in t1["reply"]["notes"] if n["kind"] == "ai_unset"]
        assert "AI is not set up" in unset["text"]
        loop = {"op": "add_relation", "from": "h1", "rel": "lowers", "to": "h2", "reason": ""}
        _ops(client, sid, [CONCEPT_A, CONCEPT_B])
        r = client.post(
            f"/api/chat/sessions/{sid}/turns", json={"ops": [loop], "lang": "en"}
        ).json()["turn"]
        (note,) = r["reply"]["notes"]
        assert note["kind"] == "dropped" and "the other end must be an item" in note["text"]


def test_bind_column_by_hand_is_dropped_for_a_theme(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
        _ops(client, sid, [CONCEPT_A])
        bind = {"op": "bind_column", "column": "price", "to": "h1", "rel": "eq", "reason": ""}
        out = _ops(client, sid, [bind])["turn"]
        (note,) = out["reply"]["notes"]
        assert note["kind"] == "dropped" and "表の列だけに使えます" in note["text"]
        assert out["state"] == "none"
