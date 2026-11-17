"""関係の閉じた集合の拡張と役割の札の api（PR 14a・契約書 §2・§4・§6）。

ストアは本物のインメモリ ``rdflib.Dataset``（``test_vocab_api`` と同じ差し替え口）。語は作り物
（Mg2Si・アーク溶解・熱伝導率 など）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

from pathlib import Path

from asterism import shared_vocab
from fastapi.testclient import TestClient

from tests.test_crosswalk_api import _AUTH
from tests.test_themes_api import _cell_body, _draft_and_commit
from tests.test_vocab_api import _align, _app, _mint

SV = shared_vocab.SV


def _terms(client: TestClient) -> dict[str, dict]:
    return {t["slug"]: t for t in client.get("/api/vocab/shared").json()["terms"]}


def _seed_three(client: TestClient) -> None:
    """材料（Mg2Si・Mg2Si_b）・方法（アーク溶解）・札なしの語（X 線回折）。"""
    _mint(client, slug="mg2si", label_ja="Mg2Si", role="material")
    _mint(client, slug="mg2si_b", label_ja="Mg2Si 薄膜", role="material")
    _mint(client, slug="arc_melting", label_ja="アーク溶解", role="method")
    _mint(client, slug="xrd", label_ja="X 線回折")


# ---------------------------------------------------------------------------
# /api/crosswalk/align
# ---------------------------------------------------------------------------


def test_align_refuses_a_pair_whose_roles_do_not_fit_with_422(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        # 「作る」は 材料 以外 → 材料。材料から材料への makes は合わない
        bad = _align(client, SV + "mg2si", SV + "mg2si_b", "makes")
        assert bad.status_code == 422 and "役割" in bad.json()["detail"], bad.text
        # 方法 → 材料 は通る・札の無い端は通す
        assert _align(client, SV + "arc_melting", SV + "mg2si", "makes").status_code == 200
        assert _align(client, SV + "xrd", SV + "mg2si", "makes").status_code == 200
        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert len(lines) == 2  # 拒まれた線は 1 本も書かれていない


def test_align_refuses_a_term_joined_to_itself_with_422(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        r = _align(client, SV + "mg2si", SV + "mg2si", "competesWith")
        assert r.status_code == 422 and "自分自身" in r.json()["detail"], r.text


def test_competes_with_is_one_line_whichever_way_it_is_put_and_removed(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        a, b = SV + "mg2si", SV + "mg2si_b"
        assert _align(client, a, b, "competesWith").status_code == 200
        assert _align(client, b, a, "competesWith").status_code == 200
        assert len(client.get("/api/crosswalk/alignments").json()["alignments"]) == 1
        # 逆向きで外せる
        rm = _align(client, b, a, "competesWith", remove=True)
        assert rm.status_code == 200, rm.text
        assert client.get("/api/crosswalk/alignments").json()["alignments"] == []


# ---------------------------------------------------------------------------
# PUT /api/vocab/terms/{slug}/role
# ---------------------------------------------------------------------------


def test_mint_with_a_role_and_the_list_shows_it(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        terms = _terms(client)
        assert (terms["mg2si"]["role"], terms["mg2si"]["role_by"]) == ("material", "human")
        assert not terms["xrd"].get("role")
        assert _mint(client, slug="bad_role", role="alloy").status_code == 422


def test_role_put_sets_replaces_and_clears(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        r = client.put("/api/vocab/terms/xrd/role", json={"role": "method"})
        assert r.status_code == 200, r.text
        assert r.json()["term"]["role"] == "method"
        assert _terms(client)["xrd"]["role"] == "method"

        r = client.put("/api/vocab/terms/xrd/role", json={"role": "concept"})
        assert r.json()["term"]["role"] == "concept"
        assert _terms(client)["xrd"]["role"] == "concept"

        r = client.put("/api/vocab/terms/xrd/role", json={"role": None})
        assert r.status_code == 200, r.text
        assert not r.json()["term"].get("role")
        assert not _terms(client)["xrd"].get("role")


def test_role_put_refusals(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        _mint(client, slug="thermal_conductivity", kind="property", cqs=[
            {"title": "熱伝導率は、どのデータセットにいくつあるか", "op": "values"}
        ])  # fmt: skip
        r = client.put("/api/vocab/terms/thermal_conductivity/role", json={"role": "material"})
        assert r.status_code == 422, r.text  # 項目の語に札は付かない
        assert client.put("/api/vocab/terms/ghost/role", json={"role": "method"}).status_code == 404
        r = client.put("/api/vocab/terms/xrd/role", json={"role": "alloy"})
        assert r.status_code == 422, r.text
        assert not _terms(client)["xrd"].get("role")


def test_role_put_needs_the_write_token(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
    with TestClient(app) as anon:
        r = anon.put("/api/vocab/terms/xrd/role", json={"role": "method"})
        assert r.status_code in (401, 403, 503), r.text


# ---------------------------------------------------------------------------
# テーマの確定
# ---------------------------------------------------------------------------


def _makes_body(**over: object) -> dict:
    return _cell_body(
        label="合成",
        questions=["Mg2Si は何件？"],
        concepts=[
            {"key": "t1", "label_ja": "Mg2Si", "label_en": "Mg2Si", "aliases": [],
             "kind": "class", "use": "new", "slug": "mg2si", "role": "material"},
            {"key": "t2", "label_ja": "アーク溶解", "label_en": "arc melting", "aliases": [],
             "kind": "class", "use": "new", "slug": "arc_melting", "role": "method"},
        ],
        relations=[{"from": "t2", "rel": "makes", "to": "t1"}],
        answers=[{"question": "Mg2Si は何件？", "mode": "data", "op": "count", "kind": "t1"}],
        **over,
    )  # fmt: skip


def test_commit_keeps_the_concept_role_on_the_new_term(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _d, r = _draft_and_commit(client, _makes_body())
        assert r.status_code == 201, r.text
        terms = _terms(client)
        assert terms["mg2si"]["role"] == "material"
        assert terms["arc_melting"]["role"] == "method"
        al = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert [x["relation"] for x in al] == ["makes"]


def test_commit_with_a_relation_whose_roles_do_not_fit_is_422(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        client.get("/api/themes")
        bad = _makes_body()
        bad["concepts"].append(
            {"key": "t3", "label_ja": "Mg2Si 薄膜", "label_en": "Mg2Si film", "aliases": [],
             "kind": "class", "use": "new", "slug": "mg2si_film", "role": "material"}
        )  # fmt: skip
        bad["relations"] = [{"from": "t3", "rel": "makes", "to": "t1"}]  # 材料が材料を作る
        d = client.post("/api/themes/drafts", json={"label": "合成", "questions": []}).json()
        r = client.post(f"/api/themes/{d['id']}/commit", json=bad)
        assert r.status_code == 422, r.text
        assert "役割" in r.json()["detail"]
        assert client.get("/api/vocab/shared").json()["terms"] == []


# ---------------------------------------------------------------------------
# 新しい関係の文（PRED_TO_REL／REL_SENTENCE を使う口）
# ---------------------------------------------------------------------------


def test_the_point_route_says_a_new_relation_as_a_sentence(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _seed_three(client)
        a, b = SV + "arc_melting", SV + "mg2si"
        assert _align(client, a, b, "makes").status_code == 200
        r = client.get("/api/chat/point", params={"from": a, "relation": "makes", "to": b})
        assert r.status_code == 200, r.text
        assert r.json()["label"] == "アーク溶解 で Mg2Si を作る"


def test_a_new_relation_has_a_sentence_and_one_figure_name(tmp_path: Path) -> None:
    from asterism import graph_diff

    assert graph_diff.REL_SENTENCE["ja"]["makes"].format(s="アーク溶解", o="Mg2Si") == (
        "アーク溶解 で Mg2Si を作る"
    )
    assert graph_diff.PRED_TO_REL["measuresMaterial"] == "measures"
