"""公開のあとの「結べそうな線」— ui-redesign PR 7c・契約 B1（``/api/sky/suggestions*``）。

ストアは本物の ``pyoxigraph.Store``（公開・ontology graph の投影・線の書き込みが実際の SPARQL で
動く）。AI は使わない（機械が名前の完全一致だけで計算する）。確かめること: 公開の後処理が候補を
置く・公開をやり直しても増えない・鍵が要る・まとめて確定は来歴 ``machine``（batch・reason）つきで
線にする・候補に無いものは線にしない・外した候補は公開をやり直しても戻らない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

from pathlib import Path

from asterism import substrate, suggested_lines
from fastapi.testclient import TestClient

from tests.test_crosswalk_api import _AUTH
from tests.test_upper_questions import EX, SV, _app, _dataset, _stage


def _mint(client: TestClient, slug: str, label: str, kind: str) -> None:
    cq = (
        {"title": f"{label}は、どのデータセットに何件あるか", "op": "count"}
        if kind == "class"
        else {"title": f"{label}の値は、どのデータセットにいくつあるか", "op": "values"}
    )
    r = client.post(
        "/api/vocab/shared", json={"slug": slug, "kind": kind, "label_ja": label, "cqs": [cq]}
    )
    assert r.status_code == 201, r.text


def _got(client: TestClient) -> list[dict]:
    r = client.get("/api/sky/suggestions")
    assert r.status_code == 200, r.text
    return r.json()["suggestions"]


def test_publish_places_exact_name_candidates_once_and_a_person_confirms_them(
    tmp_path: Path,
) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "specimen", "試料", "class")
        _mint(client, "cost", "価格", "property")
        _mint(client, "mass", "質量", "property")  # 名前が違う — 候補にならない
        assert _got(client) == []  # 公開の前には何も無い
        assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200

        got = _got(client)
        pairs = {(s["subject"], s["term"], s["relation"]) for s in got}
        assert pairs == {
            (EX + "Sample", SV + "specimen", "equivalentClass"),
            (EX + "price", SV + "cost", "equivalentProperty"),
        }
        first = got[0]
        assert first["dataset_id"] == dsid and first["version"] and first["at"]
        assert {s["subject_label"] for s in got} == {"試料", "価格"}
        assert {s["term_label"] for s in got} == {"試料", "価格"}

        # 公開をやり直しても増えない（データセット × 版 × subject × term）
        stored = len(suggested_lines.load_suggestions(tmp_path / "registry"))
        _stage(tmp_path, store, dsid, seq=1)
        assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200
        assert len(suggested_lines.load_suggestions(tmp_path / "registry")) == stored
        # 新しい版は旧版の行を置き換える（版が 1 つだけ残る）
        kept = suggested_lines.load_suggestions(tmp_path / "registry")
        assert len({r["version"] for r in kept}) == 1
        assert len(_got(client)) == 2

        # 「まとめて確定」— 候補にあるものだけが線になる。作り物の候補は not_found
        forged = {"subject": EX + "Sample", "term": SV + "mass", "relation": "equivalentClass"}
        items = [
            {k: s[k] for k in ("subject", "term", "relation")} for s in got
        ]
        res = client.post(
            "/api/sky/suggestions/apply", json={"items": [*items, forged], "batch": "b-sug"}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["applied"] == 2 and body["batch"] == "b-sug"
        assert body["dropped"] == [{"index": 2, "reason": "not_found"}]
        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert len(lines) == 2
        assert {(a["drafted_by"], a["batch"]) for a in lines} == {("machine", "b-sug")}
        assert {a["reason"] for a in lines} == {"名前の完全一致（試料）", "名前の完全一致（価格）"}
        assert _got(client) == []  # もう候補に出ない
        # 同じ候補をもう一度送っても線は増えない（もう候補に無い）
        again = client.post("/api/sky/suggestions/apply", json={"items": items}).json()
        assert again["applied"] == 0
        assert [d["reason"] for d in again["dropped"]] == ["not_found", "not_found"]


def test_a_dismissed_candidate_does_not_come_back_after_a_republish(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "specimen", "試料", "class")
        client.post(f"/api/datasets/{dsid}/promote")
        (only,) = _got(client)
        item = {k: only[k] for k in ("subject", "term", "relation")}
        r = client.post("/api/sky/suggestions/dismiss", json={"items": [item]})
        assert r.status_code == 200 and r.json()["dismissed"] == 2  # 関係なし + 関係つき
        assert _got(client) == []
        assert client.post("/api/sky/suggestions/dismiss", json={"items": [item]}).json() == {
            "dismissed": 0
        }  # 冪等
        # 新しい版で公開し直しても、外したものは戻らない
        _stage(tmp_path, store, dsid, seq=2)
        client.post(f"/api/datasets/{dsid}/promote")
        assert _got(client) == []


def test_the_routes_need_the_write_token_and_a_forged_subject_is_refused(tmp_path: Path) -> None:
    app, _store = _app(tmp_path)
    item = {"subject": EX + "Sample", "term": SV + "x", "relation": "equivalentClass"}
    with TestClient(app) as anon:  # 鍵なし
        assert anon.get("/api/sky/suggestions").status_code in (401, 403, 503)
        for path in ("apply", "dismiss"):
            r = anon.post(f"/api/sky/suggestions/{path}", json={"items": [item]})
            assert r.status_code in (401, 403, 503)
    with TestClient(app, headers=_AUTH) as client:
        bad = {**item, "subject": "not an iri\nline"}
        assert client.post("/api/sky/suggestions/dismiss", json={"items": [bad]}).status_code == 422
        too_long = {"items": [item], "batch": "x" * 101}
        assert client.post("/api/sky/suggestions/apply", json=too_long).status_code == 422


def test_a_kind_that_vanished_from_the_ontology_is_neither_listed_nor_applied(
    tmp_path: Path,
) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "specimen", "試料", "class")
        client.post(f"/api/datasets/{dsid}/promote")
        (only,) = _got(client)
        item = {k: only[k] for k in ("subject", "term", "relation")}
        # 設計を直して、種類が公開済みの ontology から消えた
        graph = substrate.ontology_graph_iri(dsid)
        store.update(
            f"WITH <{graph}> DELETE {{ ?s ?p ?o }} WHERE {{ ?s ?p ?o . FILTER(?s = <{EX}Sample>) }}"
        )
        assert _got(client) == []
        res = client.post("/api/sky/suggestions/apply", json={"items": [item]}).json()
        assert res["applied"] == 0 and res["dropped"] == [{"index": 0, "reason": "not_found"}]
        assert client.get("/api/crosswalk/alignments").json()["alignments"] == []
