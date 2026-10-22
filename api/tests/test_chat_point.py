"""点の 1 枚・「見せて」の表・Ask・確認済みの戻す — ui-redesign PR 11d。

確かめること: ``GET /api/chat/point``（✓ のあとの用語の来歴 = ``drafted_by: ai``・話・手番・戻せる／
線 1 本の来歴／標準用語／未確認の札／公開した種類 = ``dataset_id`` と ``members``）・
表（見本のデータで
20 行以内・項目の名前・``total``）・振り分け ``classify``（12 例）・Ask（偽の demo-agent = ``httpx.
MockTransport``。URL 無し・失敗も）・確認済みの戻す（線と用語が消え、ほかの問いが使う用語は
``kept``）・
見せて／Ask は LLM を呼ばない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pyoxigraph as ox
import pytest
from asterism import chat_sessions, crosswalk_runtime, shared_vocab, themes
from fastapi.testclient import TestClient

from asterism_api import chat_intent
from asterism_api.main import build_app
from tests.test_chat_api import _ai_client, _new, _say
from tests.test_crosswalk_api import _AUTH, _settings
from tests.test_themes_api import _LLM, AI_JSON, KEY, Q_LOWER
from tests.test_upper_questions import EX, EXR, SV, _dataset, _OxClient, _stage

FORBIDDEN = ("そう", "線", "ことば", "下書き", "置く")


def _labels(client: TestClient) -> dict[str, str]:
    terms = asyncio.run(shared_vocab._read_terms(client.app.state.client))
    return {t.label: t.iri for t in terms}


def _point(client: TestClient, **params: str):
    return client.get("/api/chat/point", params=params)


def _confirmed_theme(tmp_path: Path):
    """AI が全部提案 → ✓。返り = (client, llm, sid, turn id)。"""
    client, llm = _ai_client(tmp_path, json.dumps(AI_JSON))
    return client, llm


# ---- 点の 1 枚 ---------------------------------------------------------------------------


def test_the_point_routes_need_the_token(tmp_path: Path) -> None:
    client, _ = _ai_client(tmp_path)
    with TestClient(client.app) as anon:
        assert anon.get("/api/chat/point", params={"iri": "https://x.example/a"}).status_code in (
            401,
            503,
        )
    with client:
        assert client.get("/api/chat/point").status_code == 422
        assert _point(client, iri="not an iri").status_code == 422
        assert _point(client, iri="https://x.example/a").status_code == 404
        assert client.get("/api/chat/point", params={"relation": "lowers"}).status_code == 422
        assert _point(client, iri="draft:dnone:c1").status_code == 404


def test_a_term_after_the_check_says_who_added_it_and_where_to_go_back(tmp_path: Path) -> None:
    client, _ = _confirmed_theme(tmp_path)
    with client:
        sid = _new(client)
        t1 = _say(client, sid, Q_LOWER)["turn"]
        c = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={})
        assert c.json()["commit"] is not None
        iris = _labels(client)
        r = _point(client, iri=iris["格子熱伝導率"])
        assert r.status_code == 200, r.text
        card = r.json()
        assert card["kind"] == "term" and card["label"] == "格子熱伝導率"
        assert card["term_kind"] == "property" and card["iri"] == iris["格子熱伝導率"]
        prov = card["provenance"]
        assert prov["drafted_by"] == "ai" and prov["at"]
        assert prov["session_id"] == sid and prov["turn_id"] == t1["id"]
        assert prov["theme_id"] == c.json()["commit"]["theme_id"]
        assert card["can_revert"] is True
        assert card["revert"] == {"session_id": sid, "turn_id": t1["id"]}
        # 文 3 行: 熱伝導率の一部・粒界散乱と点欠陥散乱が下げる
        texts = [s["text"] for s in card["sentences"]]
        assert len(texts) == 3 and "格子熱伝導率 は 熱伝導率 の一部" in texts
        assert all(s["drafted_by"] == "ai" and s["session_id"] == sid for s in card["sentences"])
        batch = f"{sid}:{t1['id']}"
        assert all(s["turn_id"] == t1["id"] and s["batch"] == batch for s in card["sentences"])
        assert not any(w in json.dumps(texts, ensure_ascii=False) for w in FORBIDDEN)
        assert card["questions"] == [] and card["members"] == []  # 公開したデータは無い
        # 英語
        en = _point(client, iri=iris["格子熱伝導率"], lang="en").json()
        assert en["label"] == "lattice thermal conductivity"
        assert any("is a part of" in s["text"] for s in en["sentences"])
        # 問いの対象の用語
        q = _point(client, iri=iris["熱伝導率"]).json()
        assert [x["mode"] for x in q["questions"]] == ["knowledge"]


def test_a_line_and_a_standard_term_and_an_unconfirmed_card(tmp_path: Path) -> None:
    client, _ = _confirmed_theme(tmp_path)
    with client:
        sid = _new(client)
        t1 = _say(client, sid, Q_LOWER)["turn"]
        draft_id = chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"]
        # 未確認の札（draft:<id>:<key>）
        card = _point(client, iri=f"draft:{draft_id}:c4").json()
        assert card["kind"] == "draft" and card["label"] == "粒界散乱"
        assert card["provenance"]["drafted_by"] == "ai"
        assert card["provenance"]["session_id"] == sid and card["provenance"]["turn_id"] == t1["id"]
        assert [s["text"] for s in card["sentences"]] == ["粒界散乱 は 格子熱伝導率 を下げる"]
        assert card["can_revert"] is True and card["revert"]["turn_id"] == t1["id"]
        assert _point(client, iri=f"draft:{draft_id}:zz").status_code == 404
        client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={})
        assert _point(client, iri=f"draft:{draft_id}:c4").status_code == 404  # 確認で消えた
        iris = _labels(client)
        a, b = iris["粒界散乱"], iris["格子熱伝導率"]
        line = _point(client, **{"from": a, "relation": "lowers", "to": b}).json()
        assert line["kind"] == "line" and line["label"] == "粒界散乱 は 格子熱伝導率 を下げる"
        assert line["provenance"]["drafted_by"] == "ai"
        assert line["provenance"]["session_id"] == sid and line["provenance"]["turn_id"] == t1["id"]
        assert line["provenance"]["theme_id"] and line["can_revert"] is True
        assert line["sentences"][0]["reason"] == "粒界でフォノンが散乱される"
        assert _point(client, **{"from": a, "relation": "raises", "to": b}).status_code == 404
        # 標準の語
        std = "http://qudt.org/vocab/quantitykind/ThermalConductivity"
        card = _point(client, iri=std).json()
        assert card["kind"] == "std" and card["label"]
        assert any(s["target"] == std for s in card["sentences"])


# ---- 公開したデータ: 種類・表・Ask --------------------------------------------------------


def _published(tmp_path: Path, llm: _LLM | None = None, *, apply: bool = True):
    """公開済みの見本（試料 3 件・価格と重さ）と、名前の一致で結んだ用語「試料」。"""
    dsid = _dataset(tmp_path)
    store = ox.Store()
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_OxClient(store),
        start_watcher=False,
        llm_factory=(lambda key: llm) if llm is not None else None,
    )
    _stage(tmp_path, store, dsid)
    client = TestClient(app, headers={**_AUTH, **KEY})
    client.__enter__()
    r = client.post(
        "/api/vocab/shared",
        json={"slug": "specimen", "kind": "class", "label_ja": "試料",
              "cqs": [{"title": "試料は、どのデータセットに何件あるか", "op": "count"}]},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200
    (sug,) = [s for s in client.get("/api/sky/suggestions").json()["suggestions"]
              if s["relation"] == "equivalentClass"]  # fmt: skip
    item = {k: sug[k] for k in ("subject", "term", "relation")}
    if apply:
        res = client.post("/api/sky/suggestions/apply", json={"items": [item]})
        assert res.json()["applied"] == 1
    return client, dsid


def test_a_published_kind_and_its_term(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        sky = client.get("/api/sky").json()
        (kind,) = [k for d in sky["datasets"] for k in d["kinds"] if k["iri"] == EX + "Sample"]
        card = _point(client, iri=kind["iri"]).json()
        assert card["kind"] == "kind" and card["dataset_id"] == dsid
        assert card["label"] == kind["label"] and card["term_kind"] == "class"
        assert [m["iri"] for m in card["members"]] == [SV + "specimen"]
        assert card["members"][0]["label"] == "試料"
        term = _point(client, iri=SV + "specimen").json()
        assert term["kind"] == "term"
        assert [m["iri"] for m in term["members"]] == [EX + "Sample"]
        assert term["members"][0]["dataset_label"]
        assert term["members"][0]["dataset_id"] == dsid
        # 機械の名前の一致の線: 来歴は machine・話は無い（batch は話の形でない）
        (s,) = [x for x in term["sentences"] if x["relation"] == "equivalentClass"]
        assert s["drafted_by"] == "machine" and s["session_id"] is None
        assert term["can_revert"] is False
        assert term["provenance"]["drafted_by"] == "human"


def test_show_makes_a_table_without_the_llm(tmp_path: Path) -> None:
    llm = _LLM()
    client, _dsid = _published(tmp_path, llm)
    with client:
        sid = _new(client)
        out = _say(client, sid, "試料を見せて")
        turn = out["turn"]
        assert turn["by"] == "machine" and turn["state"] == "none"
        table = turn["reply"]["table"]
        assert table["kind"]["iri"] == EX + "Sample" and table["total"] == 3
        assert len(table["rows"]) == 3 and table["truncated"] is False
        names = {c["iri"]: c["label"] for c in table["columns"]}
        assert names[EX + "price"] == "価格" and names[EX + "weight"] == "重さ"
        by_iri = {r["iri"]: r for r in table["rows"]}
        row = by_iri[EXR + "sample/s2"]
        assert row["cells"][EX + "price"] == "30" and row["cells"][EX + "weight"] == "7"
        assert turn["reply"]["answer"] is None and turn["reply"]["notes"] == []
        assert out["draft"] is None  # 図の提案ではない
        assert chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"] is None
        # 表の値は LLM に渡らない（LLM はそもそも呼ばれない）
        assert llm.calls == []
        # 「表」「一覧」の言い方でも
        for said in ("試料の一覧", "試料の表を見せて", "試料 一覧"):
            assert _say(client, sid, said)["turn"]["reply"]["table"]["total"] == 3
        assert llm.calls == []


def test_show_a_term_makes_the_table_of_its_kind(tmp_path: Path) -> None:
    client, _dsid = _published(tmp_path)
    with client:
        # 同じ名前の種類が無い用語「検体」を、試料の種類に結ぶ
        r = client.post(
            "/api/vocab/shared",
            json={"slug": "ktai", "kind": "class", "label_ja": "検体",
                  "cqs": [{"title": "検体は何件？", "op": "count"}]},
        )  # fmt: skip
        assert r.status_code == 201, r.text
        line = crosswalk_runtime.assert_alignment(
            client.app.state.client, EX + "Sample", SV + "ktai", "subClassOf",
            at="2026-10-22T00:00:00+00:00",
        )  # fmt: skip
        asyncio.run(line)
        sid = _new(client)
        turn = _say(client, sid, "検体を見せて")["turn"]
        assert turn["reply"]["table"]["kind"]["iri"] == EX + "Sample"
        (n,) = turn["reply"]["notes"]
        assert n["kind"] == "info" and "用語「検体」に入っている種類" in n["text"]
        # 公開した種類にも用語にも当たらない名前は表にならず、図の提案へ
        assert _say(client, sid, "存在しない物を見せて")["turn"]["reply"]["table"] is None


def test_the_table_is_at_most_20_rows(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        store = client.app.state.client.store
        from asterism import substrate

        graph = ox.NamedNode(substrate.versioned_graph_iri(dsid, 1))
        rdf_type = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        for i in range(30):
            s = ox.NamedNode(f"{EXR}sample/x{i:02d}")
            store.add(ox.Quad(s, rdf_type, ox.NamedNode(EX + "Sample"), graph))
            store.add(ox.Quad(s, ox.NamedNode(EX + "price"), ox.Literal(str(i)), graph))
        sid = _new(client)
        table = _say(client, sid, "試料を見せて")["turn"]["reply"]["table"]
        assert table["total"] == 33 and len(table["rows"]) == 20 and table["truncated"] is True


# ---- 振り分け -----------------------------------------------------------------------------

NAMES = {"試料": EX + "Sample", "値段の記録": EX + "Price", "格子熱伝導率": SV + "lattice"}


@pytest.mark.parametrize(
    ("text", "draft_open", "expected"),
    [
        ("さっきのを戻して", False, "undo"),
        ("undo", True, "undo"),
        ("値段の記録を見せて", False, "show"),
        ("値段の記録の一覧", True, "show"),
        ("試料の表を見せて", False, "show"),
        ("スーパーの価格表には何件ある？ 答えて", False, "ask"),
        ("値段の記録は何件？", False, "ask"),
        ("格子熱伝導率を下げるものを教えて", True, "ask"),
        ("格子熱伝導率を下げるのは？", True, "graph"),  # 下書きが開いている ？ は図
        ("熱電材料について？", False, "graph"),  # 名前に当たらない ？ は図
        ("粒界散乱を前の位置に戻して", False, "graph"),
        ("試料を図に足して", False, "graph"),
        ("値段の記録を見せて、図に足して", False, "graph"),
        ("試料を外して 答えて", False, "graph"),  # 図を直す語が勝つ
        ("粒界散乱は格子熱伝導率を下げる", False, "graph"),
    ],
)
def test_classify(text: str, draft_open: bool, expected: str) -> None:
    assert chat_intent.classify(text, names=NAMES, draft_open=draft_open) == expected


def test_the_longest_name_wins_and_one_letter_names_do_not_match_a_question() -> None:
    names = {"価格": "a", "価格表": "b"}
    assert chat_intent.show_target("価格表を見せて", names) == "価格表"
    assert chat_intent.show_target("価格を見せて", names) == "価格"
    assert chat_intent.classify("国は？", names={"国": "c"}, draft_open=False) == "graph"
    assert chat_intent.classify("国を見せて", names={"国": "c"}, draft_open=False) == "show"


# ---- Ask ----------------------------------------------------------------------------------


def _mock(calls: list[dict], reply: dict | int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "headers": dict(request.headers),
                      "body": json.loads(request.content)})  # fmt: skip
        if isinstance(reply, int):
            return httpx.Response(reply, text="boom")
        return httpx.Response(200, json=reply)

    return httpx.MockTransport(handler)


ANSWER = {
    "answer": "試料は 3 件です。",
    "citations": [{"iri": EXR + "sample/s1"}, {"iri": "https://x.example/none"}],
    "notes": ["型付きツールで答えました"],
    "sparql": [],
    "verified_tools": [{"dataset": "samples", "name": "count_kind", "title": "試料の件数"}],
    "unverified_sparql": False,
    "warnings": [{"message": "外れ値を除きました"}],
    "answered": True,
}


def test_ask_goes_to_the_demo_agent_with_headers_and_history(tmp_path: Path) -> None:
    llm = _LLM()
    client, dsid = _published(tmp_path, llm)
    with client:
        calls: list[dict] = []
        client.app.state.demo_agent_url = "http://demo.test"
        client.app.state.ask_transport = _mock(calls, ANSWER)
        sid = _new(client)
        out = _say(client, sid, "スーパーの価格表には何件ある？ 答えて")
        turn = out["turn"]
        assert turn["state"] == "none" and turn["by"] == "ai" and out["draft"] is None
        assert turn["user"]["text"] == "スーパーの価格表には何件ある？ 答えて"
        a = turn["reply"]["answer"]
        assert turn["reply"]["text"] == a["text"] == "試料は 3 件です。"
        c1, c2 = a["citations"]
        assert c1["iri"] == EXR + "sample/s1" and c1["dataset_id"] == dsid
        assert c1["label"] == "s1" and c1["dataset_label"]  # 名前が無い個体は IRI の末尾
        assert c2 == {"iri": "https://x.example/none", "label": "none", "dataset_id": "",
                      "dataset_label": ""}  # fmt: skip
        assert a["verified_tools"] == ["試料の件数"] and a["notes"] == ["型付きツールで答えました"]
        assert a["warnings"] == [{"message": "外れ値を除きました"}]
        assert a["unverified_sparql"] is False and a["answered"] is True
        (call,) = calls
        assert call["url"] == "http://demo.test/demo/ask" and call["body"]["history"] == []
        assert call["headers"]["x-api-key"] == "test-key"
        # 2 問目: 話の Ask の手番だけが history に入る（図の手番は入らない）
        _say(client, sid, "試料を見せて")
        _say(client, sid, "もう一度 教えて", **{})
        h = calls[1]["body"]["history"]
        assert h == [
            {"role": "user", "content": "スーパーの価格表には何件ある？ 答えて"},
            {"role": "assistant", "content": "試料は 3 件です。"},
        ]
        assert llm.calls == []  # Ask も LLM は demo-agent の側（この api は呼ばない）
        # 何も保存しない: 下書きもテーマも無い
        assert themes.list_drafts(tmp_path / "registry") == []
        sess = chat_sessions.load_session(tmp_path / "registry", sid)
        assert sess["draft_id"] is None and sess["theme_id"] is None


def test_ask_without_an_url_or_when_it_fails(tmp_path: Path) -> None:
    client, _ = _ai_client(tmp_path)
    with client:
        sid = _new(client)
        t = _say(client, sid, "試料はいくつ？ 答えて")["turn"]
        (n,) = t["reply"]["notes"]
        assert n["kind"] == "ask_unavailable" and n["text"] == "質問の答えはこの環境では出せません"
        assert t["state"] == "none" and t["reply"]["answer"] is None
        calls: list[dict] = []
        client.app.state.demo_agent_url = "http://demo.test"
        for reply in (500, {"nope": 1}):
            client.app.state.ask_transport = _mock(calls, reply)
            t = _say(client, sid, "試料はいくつ？ 答えて")["turn"]
            (n,) = t["reply"]["notes"]
            assert n["kind"] == "ask_failed" and "boom" not in json.dumps(t, ensure_ascii=False)
            assert t["reply"]["answer"] is None and t["state"] == "none"
        # つながらない
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        client.app.state.ask_transport = httpx.MockTransport(down)
        t = _say(client, sid, "試料はいくつ？ 答えて")["turn"]
        assert t["reply"]["notes"][0]["kind"] == "ask_failed"
        said = json.dumps([x["reply"]["text"] for x in [t]], ensure_ascii=False)
        assert not any(w in said for w in FORBIDDEN)


def test_a_question_with_an_open_draft_stays_a_draft_unless_asked_to_answer(tmp_path: Path) -> None:
    client, llm = _ai_client(tmp_path, json.dumps(AI_JSON))
    with client:
        calls: list[dict] = []
        client.app.state.demo_agent_url = "http://demo.test"
        client.app.state.ask_transport = _mock(calls, ANSWER)
        sid = _new(client)
        assert _say(client, sid, Q_LOWER)["turn"]["by"] == "ai"  # 図の提案（LLM を 1 回）
        assert len(llm.calls) == 1 and calls == []
        t = _say(client, sid, "熱伝導率を下げるものを教えて")["turn"]
        assert t["reply"]["answer"] is not None and len(calls) == 1 and len(llm.calls) == 1


# ---- 確認済みの戻す -----------------------------------------------------------------------


def test_revert_of_a_confirmed_turn_takes_back_lines_terms_and_questions(tmp_path: Path) -> None:
    client, _ = _confirmed_theme(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid = _new(client)
        t1 = _say(client, sid, Q_LOWER)["turn"]
        commit = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={}).json()[
            "commit"
        ]
        assert commit["new_questions"] and commit["minted"]
        iris = _labels(client)
        theme_id = commit["theme_id"]
        # ほかの問い（別のテーマ）が「点欠陥散乱」を使っている
        slug = shared_vocab.slug_of(iris["点欠陥散乱"])
        data = themes.load_themes_file(root)
        sel = {"kq_x": {"mode": "knowledge", "target_slug": slug}}
        other = {"id": "other", "label": "別", "created_at": "x", "questions": [],
                 "support_questions": [], "memo_questions": [], "wanted": {}, "minted": [],
                 "uses": [], "relations": [], "selections": sel}  # fmt: skip
        data["themes"].append(other)
        themes._save_themes(root, data)

        r = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/revert", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        undone = body["undone"]
        assert undone["lines"] == 6 and undone["questions"] >= 1  # 5 つの関係 + 標準の量へ 1 本
        assert undone["terms"] == len(commit["minted"]) - 1
        assert undone["kept"] == [{"slug": slug, "reason": "問いが使っています"}]
        assert body["turn"]["state"] == "reverted" and body["reverted"] == [t1["id"]]
        assert body["session"]["theme_id"] == theme_id  # テーマは残す
        assert list(_labels(client)) == ["点欠陥散乱"]
        lines = asyncio.run(crosswalk_runtime.list_alignments(client.app.state.client))
        assert [ln for ln in lines if ln.get("batch") == commit["batch"]] == []
        theme = next(t for t in themes.load_themes_file(root)["themes"] if t["id"] == theme_id)
        assert (theme["questions"], theme["memo_questions"], theme["relations"]) == ([], [], [])
        assert theme["minted"] == [slug]
        assert _point(client, iri=iris["粒界散乱"]).status_code == 404
        # 2 度目は 409
        assert client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/revert").status_code == 409
        # 戻したあとの文は、テーマがまだあるので続きの図になる
        sess = chat_sessions.load_session(root, sid)
        assert [t["state"] for t in sess["turns"]] == ["reverted"]


def test_published_data_cannot_be_reverted(tmp_path: Path) -> None:
    client, _ = _confirmed_theme(tmp_path)
    with client:
        sid = _new(client)
        s = chat_sessions.load_session(tmp_path / "registry", sid)
        s["turns"].append({"id": "t1", "at": "x", "user": {"text": "", "attachments": []},
                           "reply": {"text": "", "ops": [], "sentences": [], "questions": [],
                                     "bindings": [], "notes": [], "answer": None, "table": None},
                           "batch": f"{sid}:t1", "state": "confirmed", "before": None,
                           "commit": {"dataset_id": "d"}, "by": "machine",
                           "attachment": {"dataset_id": "d"}})  # fmt: skip
        chat_sessions.save_session(tmp_path / "registry", s)
        r = client.post(f"/api/chat/sessions/{sid}/turns/t1/revert")
        assert r.status_code == 409 and "公開したデータは戻せません（データの削除で）" in r.text


def test_reverting_a_confirmed_machine_turn_removes_its_lines_and_dismisses(
    tmp_path: Path,
) -> None:
    client, _dsid = _published(tmp_path, apply=False)
    with client:
        sid = _new(client)
        (cand,) = client.get("/api/sky/suggestions").json()["suggestions"]
        item = {k: cand[k] for k in ("subject", "term", "relation")}
        s = chat_sessions.load_session(tmp_path / "registry", sid)
        reply = {"text": "", "ops": [], "sentences": [], "questions": [], "bindings": [],
                 "notes": [], "answer": None, "table": None, "suggestions": [item]}  # fmt: skip
        s["turns"].append({"id": "t1", "at": "x", "user": None, "reply": reply,
                           "batch": f"{sid}:t1", "state": "proposed", "before": None,
                           "commit": None, "by": "machine"})  # fmt: skip
        chat_sessions.save_session(tmp_path / "registry", s)
        c = client.post(f"/api/chat/sessions/{sid}/turns/t1/confirm", json={})
        assert c.status_code == 200, c.text
        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert any(ln["batch"] == f"{sid}:t1" for ln in lines)
        r = client.post(f"/api/chat/sessions/{sid}/turns/t1/revert")
        assert r.status_code == 200, r.text
        assert r.json()["turn"]["state"] == "reverted" and r.json()["undone"]["lines"] == 1
        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert not any(ln["batch"] == f"{sid}:t1" for ln in lines)
        # 次から出さない
        assert client.get("/api/sky/suggestions").json()["suggestions"] == []
