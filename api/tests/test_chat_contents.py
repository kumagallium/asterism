"""点の中身（ABox）— ui-redesign PR 11k。``GET /api/chat/contents``。

確かめること: 鍵・指定が 1 つだけ（無い・2 つは 400）・IRI の形（400）・話／データセットが無い
（404）・
kind（表 1 つ・行と ``total``）・term（class は種類の表・property は項目を持つ種類の表・
引けない用語は ``tables: []`` と ``note``）・dataset（公開済み・取り込み済みで未公開は
版グラフを読む・
取り込み前は ``note``）・session（添えた表あり／なし／戻した手番は数えない）・``tables`` は最大 3 で
残りが ``more``・``limit`` は ``MAX_ROWS`` が上限。AI は呼ばない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

from pathlib import Path

import pyoxigraph as ox
from asterism import chat_sessions, crosswalk_runtime, substrate
from fastapi.testclient import TestClient

from asterism_api import chat_tables
from asterism_api.main import build_app
from tests.test_chat_api import _new
from tests.test_chat_point import _published
from tests.test_crosswalk_api import _AUTH, _settings
from tests.test_themes_api import KEY
from tests.test_upper_questions import EX, EXR, SV, _dataset, _OxClient, _stage

RDF_TYPE = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")


def _get(client: TestClient, **params: object):
    return client.get("/api/chat/contents", params=params)


def _add_samples(client: TestClient, dsid: str, kind: str, n: int, seq: int = 1) -> None:
    graph = ox.NamedNode(substrate.versioned_graph_iri(dsid, seq))
    store = client.app.state.client.store
    for i in range(n):
        s = ox.NamedNode(f"{EXR}{kind.rsplit('/', 1)[-1]}/x{i:02d}")
        store.add(ox.Quad(s, RDF_TYPE, ox.NamedNode(kind), graph))


def _unpublished(tmp_path: Path, *, ingested: bool = True):
    """公開しない（取り込み済みだけ）の見本。返り = (client, dataset id)。"""
    dsid = _dataset(tmp_path)
    store = ox.Store()
    app = build_app(_settings(tmp_path), oxigraph_client=_OxClient(store), start_watcher=False)
    if ingested:
        _stage(tmp_path, store, dsid)
    client = TestClient(app, headers={**_AUTH, **KEY})
    client.__enter__()
    return client, dsid


def _attach(tmp_path: Path, sid: str, dsid: str, state: str = "proposed") -> None:
    root = tmp_path / "registry"
    session = chat_sessions.load_session(root, sid)
    session["turns"].append(
        {"id": chat_sessions.new_turn_id(session), "state": state,
         "attachment": {"dataset_id": dsid, "name": "試料表", "kinds": []}}
    )  # fmt: skip
    chat_sessions.save_session(root, session)


# ---- 入口の検査 ---------------------------------------------------------------------------


def test_needs_the_token_exactly_one_key_and_a_valid_form(tmp_path: Path) -> None:
    client, _dsid = _published(tmp_path)
    with client:
        with TestClient(client.app) as anon:
            assert anon.get("/api/chat/contents", params={"kind": EX + "Sample"}).status_code in (
                401,
                503,
            )
        assert _get(client).status_code == 400
        assert _get(client, kind=EX + "Sample", term=SV + "specimen").status_code == 400
        assert _get(client, kind="not an iri").status_code == 400
        assert _get(client, term="not an iri").status_code == 400
        assert _get(client, dataset="nothing-here").status_code == 404
        assert _get(client, session="s-nothing").status_code == 404
        assert _get(client, kind=EX + "Sample", limit=0).status_code == 422


# ---- kind ---------------------------------------------------------------------------------


def test_a_kind_gives_its_table(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        r = _get(client, kind=EX + "Sample")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["more"] == 0 and body["note"] is None
        (item,) = body["tables"]
        assert item["dataset"]["id"] == dsid and item["dataset"]["label"]
        table = item["table"]
        assert table["kind"]["iri"] == EX + "Sample" and table["total"] == 3
        assert len(table["rows"]) == 3 and table["truncated"] is False
        assert body["title"] == table["kind"]["label"]
        # 知らない種類は空の表（落ちない）
        empty = _get(client, kind=EX + "Nothing").json()
        assert empty["tables"][0]["table"]["total"] == 0


def test_limit_is_capped_at_max_rows(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        _add_samples(client, dsid, EX + "Sample", 30)
        small = _get(client, kind=EX + "Sample", limit=2).json()["tables"][0]["table"]
        assert len(small["rows"]) == 2 and small["total"] == 33 and small["truncated"] is True
        big = _get(client, kind=EX + "Sample", limit=999).json()["tables"][0]["table"]
        assert len(big["rows"]) == chat_tables.MAX_ROWS == 20


# ---- term ---------------------------------------------------------------------------------


def test_a_class_term_gives_the_tables_of_its_kinds_per_dataset(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        body = _get(client, term=SV + "specimen").json()
        assert body["title"] == "試料" and body["note"] is None
        (item,) = body["tables"]
        assert item["dataset"]["id"] == dsid
        assert item["table"]["kind"]["iri"] == EX + "Sample" and item["table"]["total"] == 3
        en = _get(client, term=SV + "specimen", lang="en").json()
        assert en["title"]  # 英語名が無ければ日本語名
        # 星図に無い用語は空＋note
        none = _get(client, term=SV + "nothing").json()
        assert none["tables"] == [] and none["more"] == 0 and none["note"]


def test_a_property_term_finds_the_kind_that_has_the_property(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        r = client.post(
            "/api/vocab/shared",
            json={"slug": "cost", "kind": "property", "label_ja": "費用",
                  "cqs": [{"title": "費用は？", "op": "values"}]},
        )  # fmt: skip
        assert r.status_code == 201, r.text
        import asyncio

        asyncio.run(
            crosswalk_runtime.assert_alignment(
                client.app.state.client, EX + "price", SV + "cost", "equivalentProperty",
                at="2026-10-22T00:00:00+00:00",
            )
        )  # fmt: skip
        sky = client.get("/api/sky").json()
        term = next(t for t in sky["terms"] if t["iri"] == SV + "cost")
        assert [m["iri"] for m in term["members"]] == [EX + "price"]
        body = _get(client, term=SV + "cost").json()
        assert body["title"] == "費用"
        (item,) = body["tables"]
        assert item["dataset"]["id"] == dsid and item["table"]["kind"]["iri"] == EX + "Sample"
        # 用語に結ばれたものが無ければ空＋note
        r = client.post(
            "/api/vocab/shared",
            json={"slug": "lonely", "kind": "class", "label_ja": "孤立",
                  "cqs": [{"title": "孤立は？", "op": "count"}]},
        )  # fmt: skip
        assert r.status_code == 201, r.text
        lonely = _get(client, term=SV + "lonely").json()
        assert lonely["tables"] == [] and lonely["note"]


# ---- dataset ------------------------------------------------------------------------------


def test_a_published_dataset_gives_its_kinds_and_more_counts_the_rest(tmp_path: Path) -> None:
    client, dsid = _published(tmp_path)
    with client:
        for name in ("Alpha", "Beta", "Gamma"):
            _add_samples(client, dsid, EX + name, 2)
        body = _get(client, dataset=dsid).json()
        assert body["title"]
        assert len(body["tables"]) == 3 and body["more"] == 1  # 種類は 4（Sample＋3）
        assert body["tables"][0]["table"]["kind"]["iri"] == EX + "Sample"  # 個体の多い順
        assert all(t["dataset"]["id"] == dsid for t in body["tables"])


def test_an_unpublished_dataset_reads_its_staged_graph(tmp_path: Path) -> None:
    client, dsid = _unpublished(tmp_path)
    with client:
        body = _get(client, dataset=dsid).json()
        assert body["note"] is None
        (item,) = body["tables"]
        assert item["table"]["kind"]["iri"] == EX + "Sample" and item["table"]["total"] == 3
        assert len(item["table"]["rows"]) == 3
        # 公開前は、種類で引く公開済みの表には出ない
        assert _get(client, kind=EX + "Sample").json()["tables"][0]["table"]["total"] == 0


def test_a_dataset_not_yet_ingested_says_so(tmp_path: Path) -> None:
    client, dsid = _unpublished(tmp_path, ingested=False)
    with client:
        body = _get(client, dataset=dsid).json()
        assert body["tables"] == [] and body["more"] == 0 and body["note"]


# ---- session ------------------------------------------------------------------------------


def test_session_gives_the_last_attached_table_or_nothing(tmp_path: Path) -> None:
    client, dsid = _unpublished(tmp_path)
    with client:
        sid = _new(client)
        empty = _get(client, session=sid).json()
        assert empty["tables"] == [] and empty["more"] == 0
        _attach(tmp_path, sid, dsid)
        body = _get(client, session=sid).json()
        (item,) = body["tables"]
        assert item["dataset"]["id"] == dsid and item["table"]["total"] == 3
        # 戻した手番は数えない
        sid2 = _new(client)
        _attach(tmp_path, sid2, dsid, state="reverted")
        assert _get(client, session=sid2).json()["tables"] == []
        # 添えた表が消えていたら空（落ちない）
        sid3 = _new(client)
        _attach(tmp_path, sid3, "gone-dataset")
        assert _get(client, session=sid3).json()["tables"] == []
