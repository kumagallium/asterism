"""点の 1 枚 — ui-redesign PR 11d（``GET /api/chat/point``）。

確かめること: ✓ のあとの用語の来歴（``drafted_by: ai``・話・手番・戻せる）・線 1 本の来歴・
標準用語・未確認の札・公開した種類（``dataset_id`` と ``members``）・機械の名前の一致の線。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pyoxigraph as ox
from asterism import chat_sessions, shared_vocab
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_chat_api import _ai_client, _new, _say
from tests.test_crosswalk_api import _AUTH, _settings
from tests.test_themes_api import _LLM, AI_JSON, KEY, Q_LOWER
from tests.test_upper_questions import EX, SV, _dataset, _OxClient, _stage

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
