"""``GET /api/relations``（関係の目録）と、api・step0 の写しの見張り。

目録（``asterism.relation_catalog``）が正。step0 の写し（step0 は ingest を import しない）が
ずれたら落ちる。api の写し（``chat_paper._CLASS_BOTH`` など）は R0b で目録の導出にした
（結果は ``test_relation_copies_snapshot.py`` が固定している）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asterism import relation_catalog as rc
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _settings, healthy_client  # noqa: F401 (fixture)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.


def test_relations_route_shape_and_no_auth(tmp_path: Path, healthy_client) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:  # トークンなし
        r = client.get("/api/relations")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["relations", "predicates"]
    assert len(body["relations"]) == 12 and len(body["predicates"]) == 15
    assert [x["name"] for x in body["relations"]] == list(rc.relation_kinds())
    assert [p["name"] for p in body["predicates"]] == [p.name for p in rc.PREDICATES]
    assert body == rc.catalog_to_json()
    rel = {x["name"]: x for x in body["relations"]}
    assert [(p["name"], p["when"]) for p in rel["measures"]["predicates"]] == [
        ("measuresProperty", ["to_kind", "property"]),
        ("measuresMaterial", ["to_kind", "class"]),
    ]
    assert rel["competes"]["symmetric_draft"] is True
    assert rel["eq"]["symmetric_draft"] is False and rel["eq"]["symmetric_pair"] is True
    assert set(rel["sub"]["label"]) == {"ja", "en"}
    pred = {p["name"]: p for p in body["predicates"]}
    assert pred["competesWith"]["own"]["date"] == "2026-11-17"
    assert pred["isPartOf"]["same_shape"] is True and pred["isPartOf"]["subsumption"] is True


def test_the_route_is_read_only(tmp_path: Path, healthy_client) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:
        assert client.post("/api/relations", json={}).status_code in (404, 405)
        assert client.put("/api/relations", json={}).status_code in (404, 405)


# --- step0 の写し（step0 は ingest を import しない。api の環境は両方を入れている） ---------


def test_step0_copies_match_the_catalog() -> None:
    from asterism_step0 import pair_propose, paper_propose, theme_propose

    without_quantity = tuple(r for r in rc.relation_kinds() if r != "quantity")
    without_eq = tuple(r for r in without_quantity if r != "eq")
    assert len(rc.relation_kinds()) == 12 and len(without_eq) == 10
    assert tuple(theme_propose.RELATIONS) == rc.relation_kinds()
    assert tuple(pair_propose.PAIR_RELS) == without_quantity
    assert tuple(pair_propose.SYMMETRIC_RELS) == rc.symmetric_pair_relations()
    # PAPER_RELS は並びが違う（part が先頭）。値の集合で見る。目録の ``paper`` が正
    assert len(paper_propose.PAPER_RELS) == 10
    assert set(paper_propose.PAPER_RELS) == set(rc.paper_relations())
    assert set(rc.paper_relations()) == set(without_eq)


@pytest.mark.parametrize("name", ["sub", "measures", "competes"])
def test_catalog_names_are_in_the_step0_copies(name: str) -> None:
    from asterism_step0 import theme_propose

    assert name in theme_propose.RELATIONS
