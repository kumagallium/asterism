# ruff: noqa: E501
"""人が関係を足す 4 つの口（R3a）— 形・認証・403/404/409・GET の既定が 0 件で今と同じ。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asterism import relation_catalog as rc
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client, unreachable_client  # noqa: F401
from tests.test_relations_store_api import _StoreClient


@pytest.fixture(autouse=True)
def _reset_human():
    """足した関係は module の入れ物に入る。テストの外へ漏らさない。"""
    rc.set_human((), ())
    yield
    rc.set_human((), ())


BODY = {
    "label": {"ja": "支える", "en": "supports"},
    "definition": {"ja": "A が B を力学的に支える"},
    "symmetric": False,
    "to_property_only": True,
}


def _app(tmp_path: Path):
    store = _StoreClient()
    app = build_app(_settings(tmp_path), oxigraph_client=store, start_watcher=False)  # type: ignore[arg-type]
    return app, store


def test_GET_の既定は足した関係_0_件のとき今と同じ(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/relations").json() == rc.catalog_to_json()
        assert client.get("/api/relations", params={"usage": 1}).json() == rc.catalog_to_json()


def test_認証が無いと書けない(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:  # トークンなし
        assert client.post("/api/relations", json=BODY).status_code == 401
        assert client.patch("/api/relations/u_00000000", json={}).status_code == 401
        assert client.post("/api/relations/u_00000000/retire").status_code == 401
        assert client.delete("/api/relations/u_00000000").status_code == 401
        assert client.get("/api/relations").status_code == 200  # 読みは認証なし
    assert rc.human_relations() == ()


def test_足す_直す_やめる_消す(tmp_path: Path) -> None:
    app, _store = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/relations", json=BODY)
        assert r.status_code == 201
        rel = r.json()["relation"]
        name = rel["name"]
        assert name.startswith("u_") and rel["builtin"] is False and rel["retired"] is False
        assert rel["added_by"] == "human" and rel["definition"] == {"ja": "A が B を力学的に支える"}
        assert rel["to_property_only"] is True and rel["symmetric_pair"] is False
        # 目録に重なる（GET の既定・store の両方）
        names = [x["name"] for x in client.get("/api/relations").json()["relations"]]
        assert names == [*rc.relation_kinds()[:12], name] == list(rc.relation_kinds())
        assert name in [x["name"] for x in client.get("/api/relations", params={"source": "store"}).json()["relations"]]
        # 直す（表示名・文・説明・exact_match）
        p = client.patch(f"/api/relations/{name}", json={"label": {"ja": "支持する"}, "exact_match": "https://example.org/x"})
        assert p.status_code == 200 and p.json()["relation"]["label"] == {"ja": "支持する"}
        assert p.json()["relation"]["exact_match"] == "https://example.org/x" and p.json()["relation"]["name"] == name
        assert client.patch(f"/api/relations/{name}", json={"symmetric": True}).status_code == 422  # 直せない欄
        # 使われ方は ?usage=1 のときだけ
        assert "usage" not in client.get("/api/relations").json()["relations"][-1]
        assert client.get("/api/relations", params={"usage": 1}).json()["relations"][-1]["usage"] == {"lines": 0, "ledger": 0, "drafts": 0}
        # やめる（何度でも同じ）
        r1 = client.post(f"/api/relations/{name}/retire")
        r2 = client.post(f"/api/relations/{name}/retire")
        assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
        assert r1.json()["relation"]["retired"] is True and name not in rc.assignable_relation_kinds()
        # 消す（どれも使われていない）
        d = client.delete(f"/api/relations/{name}")
        assert d.status_code == 200 and d.json() == {"deleted": name}
        assert client.get("/api/relations").json() == rc.catalog_to_json()
    assert rc.human_relations() == ()


def test_検査と衝突と組み込みと無い名前(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        assert client.post("/api/relations", json={**BODY, "label": {"ja": "<x>"}}).status_code == 400
        assert client.post("/api/relations", json={**BODY, "definition": {}}).status_code == 400
        assert client.post("/api/relations", json={**BODY, "label": {"ja": "競合する"}}).status_code == 409
        assert client.post("/api/relations", json={**BODY, "extra": 1}).status_code == 422
        assert client.post("/api/relations", json=BODY).status_code == 201
        dup = client.post("/api/relations", json=BODY)
        assert dup.status_code == 409 and "既にある関係" in dup.json()["detail"]
        for call in (
            lambda: client.patch("/api/relations/sub", json={"label": {"ja": "x"}}),
            lambda: client.post("/api/relations/sub/retire"),
            lambda: client.delete("/api/relations/sub"),
        ):
            assert call().status_code == 403
        assert client.patch("/api/relations/u_00000000", json={}).status_code == 404
        assert client.post("/api/relations/u_00000000/retire").status_code == 404
        assert client.delete("/api/relations/u_00000000").status_code == 404


def test_使われていれば削除は_409_と数(tmp_path: Path) -> None:
    app, _store = _app(tmp_path)
    reg = tmp_path / "registry" / "vocab-shared" / "knowledge"
    reg.mkdir(parents=True)
    with TestClient(app, headers=_AUTH) as client:
        name = client.post("/api/relations", json=BODY).json()["relation"]["name"]
        (reg / "lab.json").write_text(json.dumps({"pairs": {"a|b": {"rel": name}}}))
        d = client.delete(f"/api/relations/{name}")
        assert d.status_code == 409
        assert d.json()["detail"] == {"detail": "relation is in use", "usage": {"lines": 0, "ledger": 1, "drafts": 0}}
        assert client.get("/api/relations", params={"usage": 1}).json()["relations"][-1]["usage"]["ledger"] == 1
        assert client.post(f"/api/relations/{name}/retire").status_code == 200  # 「使うのをやめる」は通る


def test_起動で足した関係がストアから読まれる(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        name = client.post("/api/relations", json=BODY).json()["relation"]["name"]
    rc.set_human((), ())  # プロセスを替えた想定
    app2 = build_app(_settings(tmp_path), oxigraph_client=store, start_watcher=False)  # type: ignore[arg-type]
    with TestClient(app2) as client:
        assert [r.name for r in rc.human_relations()] == [name]
        assert client.get("/api/relations").json()["relations"][-1]["name"] == name


def test_足した関係を付ける口で付けられる_やめたら_400(tmp_path: Path) -> None:
    pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")
    import dataclasses

    from asterism import pair_relations as pr
    from asterism import relation_store as rs

    from tests.test_knowledge_review import _first, _ready, _rows

    env, client = _ready(tmp_path)  # 偽の Oxigraph（書いても残らない）なので、重ねた目録へ直接入れる
    url = "/api/knowledge-copies/lab/relations"
    rel, pred = rs._build(
        "u_0000abcd", label={"ja": "支える"}, sentence=rs.default_sentence({"ja": "支える"}, symmetric=False),
        definition={"ja": "A が B を支える"}, symmetric=False, to_property_only=False,
        exact_match=None, unstated_key=None, added_at="2026-11-20T00:00:00Z",
    )
    with client:  # 入るたびに起動の読み込みが走る（偽のストアは空）ので、入ってから重ねる
        rc.set_human([rel], [pred])
        led = pr.load_ledger(env.root, "lab")
        key = next(k for k, e in led["pairs"].items() if e["state"] == "relation" and e["rel"] == "eq")
        client.post(f"{url}/review", json={"action": "reject", "keys": [key]})
        client.post(f"{url}/review", json={"action": "unconfirm", "keys": [key]})
        ok = client.post(f"{url}/assign", json={"key": key, "rel": rel.name, "subject": "a"})
        assert ok.status_code == 200, ok.text
        row = _rows(client, "relation")[key]
        assert row["rel"] == rel.name and row["by"] == "user" and row["confirmed"] is True
        # やめた関係は、別の候補に新しくは付けられない（400）。付いた組は読める
        rc.set_human([dataclasses.replace(rel, retired=True)], [pred])
        none = _first(_rows(client), state="none")
        bad = client.post(f"{url}/assign", json={"key": none["key"], "rel": rel.name, "subject": "a"})
        assert bad.status_code == 400 and bad.json()["reason"] == "retired"
        assert _rows(client, "relation")[key]["rel"] == rel.name


def test_壊れた項目があっても_GET_と_usage_は落ちず_DELETE_で消せる(tmp_path: Path) -> None:
    from asterism import relation_store as rs

    app, store = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        ok = client.post("/api/relations", json=BODY).json()["relation"]["name"]
        bad = client.post("/api/relations", json={**BODY, "label": {"ja": "別の"}}).json()["relation"]["name"]
        store.store.update(
            f"DELETE WHERE {{ GRAPH <{rs.CATALOG_GRAPH}> {{ <{rs.predicate_iri('u' + bad[2:])}> ?p ?o }} }}"
        )
        client.post(f"/api/relations/{ok}/retire")  # 書き込み後の読み直しでも壊れた項目は捨てられる
        names = [r["name"] for r in client.get("/api/relations").json()["relations"]]
        assert ok in names and bad not in names
        got = client.get("/api/relations", params={"source": "store", "usage": 1})
        assert got.status_code == 200
        assert [r["name"] for r in got.json()["relations"] if r["name"] == bad]
        assert client.delete(f"/api/relations/{bad}").status_code == 200
        assert bad not in [r["name"] for r in client.get("/api/relations", params={"source": "store"}).json()["relations"]]


def test_上限を超えると_409(tmp_path: Path, monkeypatch) -> None:
    from asterism import relation_store as rs

    monkeypatch.setattr(rs, "MAX_HUMAN_RELATIONS", 1)
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        assert client.post("/api/relations", json=BODY).status_code == 201
        assert client.post("/api/relations", json={**BODY, "label": {"ja": "二つ目"}}).status_code == 409
