"""上位構造 PR 3 の api 側 — ``questions.json`` / ``upper.json`` / handles の ``via`` /
列の ``fit`` / ⑥ の staged 実行 / 公開での消費 / 予約名 / 「ことばへ写す」
（ADR upper-structure-shared-terms.md §2.3・§2.5.2・§2.5.3・契約 handoff §5.3・§5.5）。

ストアは本物の ``pyoxigraph.Store``（``test_cards_api`` と同じ型）なので、問いの SPARQL・線の
書き込み・FROM-merge を使う公開（再公開を含む）が実際の SPARQL エンジンで動く。
"""

# ruff: noqa: RUF001, F811
from __future__ import annotations

import json
from pathlib import Path

import pyoxigraph as ox
import pytest
import yaml
from asterism import shared_vocab, substrate
from fastapi.testclient import TestClient

from asterism_api import questions_routes, registry
from asterism_api.handles import handle_slots, load_handles
from asterism_api.main import build_app
from tests.test_crosswalk_api import _AUTH, _settings
from tests.test_main import _FIX_RECIPE_MD, healthy_client  # noqa: F401

EX = "https://example.org/onto#"
EXR = "https://example.org/resource/"
SV = shared_vocab.SV

_IR = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
maps:
  - name: SampleMap
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
      label: "試料"
    properties:
      - predicate: ex:price
        column: price
        label: "価格"
        unit: "円"
      - predicate: ex:weight
        column: weight
        label: "重さ"
"""


class _OxClient:
    """``pyoxigraph.Store`` を ``OxigraphClient`` の代わりに差し込む最小のスタブ。"""

    def __init__(self, store: ox.Store) -> None:
        self.store = store

    async def sparql_select(self, query: str) -> dict:
        result = self.store.query(query)
        if isinstance(result, bool):  # ASK
            return {"head": {}, "boolean": result}
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                if isinstance(term, ox.NamedNode):
                    row[name] = {"type": "uri", "value": term.value}
                elif isinstance(term, ox.Literal):
                    cell = {"type": "literal", "value": term.value}
                    if term.language:
                        cell["xml:lang"] = term.language
                    elif term.datatype.value != "http://www.w3.org/2001/XMLSchema#string":
                        cell["datatype"] = term.datatype.value
                    row[name] = cell
                else:
                    row[name] = {"type": "bnode", "value": str(term)}
            bindings.append(row)
        return {"head": {"vars": names}, "results": {"bindings": bindings}}

    async def sparql_update(self, update: str) -> None:
        self.store.update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        target = ox.NamedNode(graph_iri) if graph_iri else ox.DefaultGraph()
        self.store.load(payload, mime_type="text/turtle", to_graph=target)
        return len(payload)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _app(tmp_path: Path):
    store = ox.Store()
    app = build_app(_settings(tmp_path), oxigraph_client=_OxClient(store), start_watcher=False)
    return app, store


def _dataset(tmp_path: Path, **artifacts: str) -> str:
    meta = registry.save_dataset(
        tmp_path / "registry",
        "samples",
        {"mapping.yaml": _IR, **artifacts},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-10-09T00:00:00+00:00",
    )
    return meta["id"]


def _stage(tmp_path: Path, store: ox.Store, dsid: str, *, seq: int = 1) -> str:
    """取り込んだ状態にする: staged の版 graph に 3 件の試料を置き、印を付ける。"""
    graph = substrate.versioned_graph_iri(dsid, seq)
    g = ox.NamedNode(graph)
    rdf_type = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    n = 0
    for sid, price, weight in (("s1", "10", "5"), ("s2", "30", "7"), ("s3", "20", "6")):
        s = ox.NamedNode(f"{EXR}sample/{sid}")
        for p, o in (
            (rdf_type, ox.NamedNode(EX + "Sample")),
            (ox.NamedNode(EX + "price"), ox.Literal(price)),
            (ox.NamedNode(EX + "weight"), ox.Literal(weight)),
        ):
            store.add(ox.Quad(s, p, o, g))
            n += 1
    registry.mark_ingested(
        tmp_path / "registry",
        dsid,
        graph_iri=graph,
        triple_count=n,
        ingested_at="2026-10-09T00:00:00+00:00",
        data_seq=seq,
    )
    return graph


def _mint(client, slug, kind="class"):
    cq = (
        {"title": "試料は、どのデータセットに何件あるか", "op": "count"}
        if kind == "class"
        else {"title": "価格の値は、どのデータセットにいくつあるか", "op": "values"}
    )
    r = client.post(
        "/api/vocab/shared",
        json={"slug": slug, "kind": kind, "label_ja": slug, "cqs": [cq]},
    )
    assert r.status_code == 201, r.text


def _tools(tmp_path: Path, dsid: str) -> list[dict]:
    path = tmp_path / "registry" / dsid / "query_tools.yaml"
    if not path.is_file():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8"))["tools"]


Q_COUNT = {"id": "q1", "title": "試料は何件？", "op": "count", "kind_iri": EX + "Sample"}
Q_RANGE = {"id": "q2", "title": "価格の範囲は？", "op": "range", "property_iri": EX + "price"}
Q_TOP = {"id": "q3", "title": "いちばん高い試料は？", "op": "top", "property_iri": EX + "price"}


# ---------------------------------------------------------------------------
# 1. artifacts の往復
# ---------------------------------------------------------------------------


def test_questions_and_upper_are_artifacts_and_survive_a_partial_update(tmp_path: Path) -> None:
    """handles.json と同じ運び方: 省略したキーは update_dataset_artifacts が触らない。"""
    dsid = _dataset(tmp_path)
    root = tmp_path / "registry"
    q_text = questions_routes.dump_questions([Q_COUNT])
    registry.write_artifact(root, dsid, "questions.json", q_text)
    registry.write_artifact(root, dsid, "upper.json", '{"version": 1, "upper": []}')
    registry.update_dataset_artifacts(
        root,
        dsid,
        {"mapping.yaml": _IR},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
    )
    assert (root / dsid / "questions.json").read_text(encoding="utf-8") == q_text
    assert (root / dsid / "upper.json").read_text(encoding="utf-8") == '{"version": 1, "upper": []}'
    assert {"questions.json", "upper.json"} <= registry.artifact_names()
    assert not registry.write_artifact(root, "no-such-dataset", "upper.json", "{}")
    assert not registry.write_artifact(root, dsid, "meta.json", "{}")  # 許可された artifact だけ


def test_questions_put_get_round_trip_and_validation(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        url = f"/api/datasets/{dsid}/questions"
        assert client.get(url).json() == {"questions": []}
        r = client.put(url, json={"questions": [Q_COUNT, Q_RANGE, Q_TOP]})
        assert r.status_code == 200, r.text
        assert client.get(url).json() == {"questions": [Q_COUNT, Q_RANGE, Q_TOP]}
        # 空の PUT は置換 = 全部消す
        assert client.put(url, json={"questions": []}).json() == {"questions": []}

        bad = [
            {**Q_COUNT, "kind_iri": None},  # 件数に種類が無い
            {**Q_RANGE, "property_iri": None},  # 範囲に項目が無い
            {**Q_COUNT, "kind_iri": "http://x.example/<a>"},  # 区切り文字入りの IRI
            {**Q_COUNT, "kind_iri": "urn:not-http"},
            {**Q_COUNT, "title": "  "},
            {**Q_COUNT, "op": "values"},  # 閉じた選択の外
        ]
        for item in bad:
            assert client.put(url, json={"questions": [item]}).status_code == 422, item
        dup = client.put(url, json={"questions": [Q_COUNT, {**Q_RANGE, "id": "q1"}]})
        assert dup.status_code == 422
        # 失敗した PUT は何も書かない
        assert client.get(url).json() == {"questions": []}
        assert client.get("/api/datasets/nope/questions").status_code == 404
        assert client.put("/api/datasets/nope/questions", json={"questions": []}).status_code == 404
    with TestClient(app) as anonymous:  # 認証無し
        assert anonymous.put(url, json={"questions": []}).status_code == 401


def test_a_lint_error_sent_by_the_client_is_not_believed(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(
            f"/api/datasets/{dsid}/questions",
            json={"questions": [{**Q_COUNT, "lint_error": "嘘"}]},
        )
        assert "lint_error" not in r.json()["questions"][0]


def test_upper_put_get_and_applied_at_is_server_owned(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    item = {"subject": EX + "price", "term": SV + "intensity", "relation": "subPropertyOf"}
    with TestClient(app, headers=_AUTH) as client:
        url = f"/api/datasets/{dsid}/upper"
        assert client.get(url).json() == {"upper": []}
        r = client.put(url, json={"upper": [{**item, "applied_at": "2020-01-01T00:00:00Z"}]})
        assert r.status_code == 200, r.text
        # 送られた applied_at は信じない（未消費で保存）
        assert r.json()["upper"] == [{**item, "applied_at": None}]

        # サーバが消費済みにしたあと、同じ線を送り直しても未消費に戻らない
        path = tmp_path / "registry" / dsid / "upper.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["upper"][0]["applied_at"] = "2026-10-09T00:00:00+00:00"
        path.write_text(json.dumps(stored), encoding="utf-8")
        again = client.put(url, json={"upper": [item]}).json()["upper"]
        assert again[0]["applied_at"] == "2026-10-09T00:00:00+00:00"
        # 一覧から外せば消え、入れ直せば新しい（未消費の）項目になる
        assert client.put(url, json={"upper": []}).json() == {"upper": []}
        assert client.put(url, json={"upper": [item]}).json()["upper"][0]["applied_at"] is None

        for bad in (
            {**item, "relation": "hasQuantityKind"},  # 当てはめで引けない線
            {**item, "term": "not-an-iri"},
            {**item, "subject": "http://x.example/<a>"},
        ):
            assert client.put(url, json={"upper": [bad]}).status_code == 422
        assert client.get("/api/datasets/nope/upper").status_code == 404


# ---------------------------------------------------------------------------
# 2. handles の via
# ---------------------------------------------------------------------------


def test_handles_keep_via_and_term_and_legacy_files_still_load(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    handles = [
        {"source": "samples.csv", "column": "price", "via": "fit", "term": SV + "price"},
        {"source": "samples.csv", "column": "weight", "via": "tick"},
        {"source": "samples.csv", "column": "sid"},  # 旧形式 = tick
    ]
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(f"/api/datasets/{dsid}/handles", json={"handles": handles})
        assert r.status_code == 200, r.text
        assert r.json() == {"handles": handles}
        assert client.get(f"/api/datasets/{dsid}/handles").json() == {"handles": handles}
        bad = client.put(
            f"/api/datasets/{dsid}/handles",
            json={"handles": [{"source": "a", "column": "b", "via": "machine"}]},
        )
        assert bad.status_code == 422
    # term は fit のときだけ意味がある
    assert load_handles(
        {"handles.json": json.dumps({"handles": [{"source": "a", "column": "b", "term": "t"}]})}
    ) == [{"source": "a", "column": "b"}]
    # autolink（handle_slots）は via を無視して従来どおり
    plain = [{"source": h["source"], "column": h["column"]} for h in handles]
    assert handle_slots(_IR, handles) == handle_slots(_IR, plain) != []


# ---------------------------------------------------------------------------
# 3. 列の当てはめ fit / materialize の受け口
# ---------------------------------------------------------------------------

_FIT = {"term": SV + "price", "kind": "shared", "matched_by": "label"}


def test_materialize_carries_handles_via_upper_and_questions_and_keeps_them(
    tmp_path: Path, healthy_client
) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    handles = [{"source": "readings.csv", "column": "channel", "via": "fit", "term": SV + "ch"}]
    upper = [{"subject": EX + "channel", "term": SV + "ch", "relation": "equivalentProperty"}]
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={
                "proposal_md": _FIX_RECIPE_MD,
                "dataset_name": "sensor",
                "handles": handles,
                "upper": upper,
                "questions": [Q_COUNT, {"id": "bad", "op": "count", "title": "x"}],
            },
        )
        assert r.status_code == 200, r.text
        dsid = r.json()["dataset"]["id"]
        root = tmp_path / "registry"
        assert client.get(f"/api/datasets/{dsid}/handles").json() == {"handles": handles}
        assert client.get(f"/api/datasets/{dsid}/questions").json() == {"questions": [Q_COUNT]}
        assert client.get(f"/api/datasets/{dsid}/upper").json()["upper"][0]["applied_at"] is None

        # 消費済みにして、設計を作り直す。upper を送り直しても消費済みのまま・省略なら全部保つ
        path = root / dsid / "upper.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["upper"][0]["applied_at"] = "2026-10-09T00:00:00+00:00"
        path.write_text(json.dumps(stored), encoding="utf-8")
        body = {"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor", "dataset_id": dsid}
        assert client.post("/api/materialize", json=body).status_code == 200
        assert client.get(f"/api/datasets/{dsid}/handles").json() == {"handles": handles}
        assert client.get(f"/api/datasets/{dsid}/questions").json() == {"questions": [Q_COUNT]}
        kept = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        assert kept[0]["applied_at"] == "2026-10-09T00:00:00+00:00"
        resent = client.post("/api/materialize", json={**body, "upper": upper})
        assert resent.status_code == 200, resent.text
        again = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        assert again[0]["applied_at"] == "2026-10-09T00:00:00+00:00"


def test_column_meaning_fit_round_trips_and_clears(tmp_path: Path, healthy_client) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        dsid = client.post(
            "/api/materialize", json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor"}
        ).json()["dataset"]["id"]
        url = f"/api/datasets/{dsid}/column-meanings"
        row = {"source": "readings.csv", "column": "channel"}
        # fit だけの行も保管される（label / unit が無くても）
        assert client.post(url, json={"meanings": [{**row, "fit": _FIT}]}).status_code == 200
        got = client.get(url).json()["meanings"]
        assert {"source": "readings.csv", "column": "channel", "fit": _FIT} in got
        stored = json.loads((tmp_path / "registry" / dsid / "column-meanings.json").read_text())
        assert stored["meanings"][0]["fit"] == _FIT
        # label だけ送っても fit はそのまま・null で外れる
        client.post(url, json={"meanings": [{**row, "label": "チャンネル"}]})
        kept = next(m for m in client.get(url).json()["meanings"] if m["column"] == "channel")
        assert kept["fit"] == _FIT and kept["label"] == "チャンネル"
        client.post(url, json={"meanings": [{**row, "fit": None}]})
        cleared = next(m for m in client.get(url).json()["meanings"] if m["column"] == "channel")
        assert "fit" not in cleared and cleared["label"] == "チャンネル"
        # 形が違えば 422
        bad = client.post(url, json={"meanings": [{**row, "fit": {**_FIT, "kind": "other"}}]})
        assert bad.status_code == 422


# ---------------------------------------------------------------------------
# 4. ⑥ の staged 実行
# ---------------------------------------------------------------------------


def test_run_a_question_against_the_staged_graph(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    staged = _stage(tmp_path, store, dsid)
    with TestClient(app) as client:  # 読むだけ — 書き込み認証は要らない
        url = f"/api/datasets/{dsid}/trial-queries/run"
        count = client.post(url, json={"op": "count", "kind_iri": EX + "Sample"}).json()
        assert count["available"] is True and count["read_from"] == "draft"
        assert count["classes"][0]["n"] == 3 and count["classes"][0]["label"] == "試料"
        assert f"GRAPH <{staged}>" in count["count_sparql"]
        assert count["range"] is None and count["top"] is None

        rng = client.post(url, json={"op": "range", "property_iri": EX + "price"}).json()["range"]
        assert (rng["n"], float(rng["min"]), float(rng["max"])) == (3, 10.0, 30.0)
        assert rng["min_subject_iri"] == EXR + "sample/s1"
        assert rng["max_subject_iri"] == EXR + "sample/s2"
        assert rng["label"] == "価格" and rng["unit"] == "円"

        top = client.post(url, json={"op": "top", "property_iri": EX + "price"}).json()["top"]
        assert float(top["value"]) == 30.0 and top["subject_iri"] == EXR + "sample/s2"
        assert {d["predicate_iri"] for d in top["subject_details"]} == {EX + "weight"}

        # 種類で絞る（当たらない種類は 0 件）
        none = client.post(
            url, json={"op": "range", "kind_iri": EX + "Other", "property_iri": EX + "price"}
        ).json()["range"]
        assert none["n"] == 0 and none["min"] is None

        for bad in (
            {"op": "count"},
            {"op": "top"},
            {"op": "count", "kind_iri": "http://x.example/> } DROP ALL {<"},
            {"op": "values", "kind_iri": EX + "Sample"},
        ):
            assert client.post(url, json=bad).status_code == 422, bad
        assert client.post(
            "/api/datasets/nope/trial-queries/run", json={"op": "count"}
        ).status_code in (404, 422)


def test_run_before_ingest_is_unavailable_not_an_error(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            f"/api/datasets/{dsid}/trial-queries/run",
            json={"op": "count", "kind_iri": EX + "Sample"},
        )
        assert r.status_code == 200 and r.json()["available"] is False


def test_question_sparql_is_a_closed_template(tmp_path: Path) -> None:
    for q in (Q_COUNT, Q_RANGE, Q_TOP):
        q_sparql = questions_routes.build_question_sparql(q)
        assert "GRAPH" not in q_sparql  # 宣言ツールは canonical の FROM-merge を読む
        assert "GRAPH <g:x>" not in q_sparql
        staged = questions_routes.build_question_sparql(q, graph="https://g.example/v1")
        assert "GRAPH <https://g.example/v1>" in staged
    with pytest.raises(ValueError):
        questions_routes.build_question_sparql({**Q_COUNT, "kind_iri": "http://x/ > } #"})


# ---------------------------------------------------------------------------
# 5. 公開での消費
# ---------------------------------------------------------------------------


def test_promote_consumes_upper_once_and_a_removed_line_does_not_come_back(
    tmp_path: Path,
) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    line = {"subject": EX + "Sample", "term": SV + "specimen", "relation": "subClassOf"}
    missing = {"subject": EX + "Ghost", "term": SV + "specimen", "relation": "subClassOf"}
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "specimen")
        assert (
            client.put(f"/api/datasets/{dsid}/upper", json={"upper": [line, missing]}).status_code
            == 200
        )

        r = client.post(f"/api/datasets/{dsid}/promote")
        assert r.status_code == 200, r.text
        report = r.json()["upper_questions"]
        assert report["upper"]["applied"] == 1
        assert report["upper"]["skipped"][0]["reason"] == "subject missing"
        up = {u["subject"]: u for u in client.get(f"/api/datasets/{dsid}/upper").json()["upper"]}
        assert up[line["subject"]]["applied_at"]
        assert up[missing["subject"]]["applied_at"] is None
        assert up[missing["subject"]]["skipped"] == "subject missing"
        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert [(a["source"], a["target"]) for a in lines] == [(line["subject"], line["term"])]

        # 人が「ことば」で線を取り消す → 再公開しても復活しない
        rm = client.post(
            "/api/crosswalk/align",
            json={
                "source": line["subject"],
                "target": line["term"],
                "relation": "subClassOf",
                "remove": True,
            },
        )
        assert rm.status_code == 200, rm.text
        assert client.get("/api/crosswalk/alignments").json()["alignments"] == []
        _stage(tmp_path, store, dsid, seq=2)
        again = client.post(f"/api/datasets/{dsid}/promote")
        assert again.status_code == 200, again.text
        assert again.json()["upper_questions"]["upper"]["applied"] == 0
        assert client.get("/api/crosswalk/alignments").json()["alignments"] == []


def test_a_refused_line_is_reported_not_fatal(tmp_path: Path) -> None:
    """未鋳造の共有語・種類と項目の取り違えは assert_alignment が断る → skipped・公開は通る。"""
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        client.put(
            f"/api/datasets/{dsid}/upper",
            json={
                "upper": [
                    {
                        "subject": EX + "Sample",
                        "term": SV + "never_minted",
                        "relation": "subClassOf",
                    }
                ]
            },
        )
        r = client.post(f"/api/datasets/{dsid}/promote")
        assert r.status_code == 200, r.text
        skipped = r.json()["upper_questions"]["upper"]["skipped"]
        assert skipped and skipped[0]["reason"] == "unminted shared term"
        (item,) = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        assert item["applied_at"] is None  # 次の公開でもう一度試す


def test_promote_turns_questions_into_q_tools_and_removal_removes_them(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        human = {
            "name": "find_by_label",
            "title": "人のツール",
            "description": "d",
            "parameters": [],
            "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1",
            "result": {"item": {"iri": "s"}},
        }
        assert client.post(f"/api/datasets/{dsid}/tools", json=human).status_code == 200
        client.put(f"/api/datasets/{dsid}/questions", json={"questions": [Q_COUNT, Q_RANGE, Q_TOP]})
        # 公開までは query_tools.yaml に q_ は無い（下書きだけ）
        assert not [t for t in _tools(tmp_path, dsid) if t["name"].startswith("q_")]

        r = client.post(f"/api/datasets/{dsid}/promote")
        assert r.status_code == 200, r.text
        names = {
            q["id"]: questions_routes.question_tool_name(q["id"]) for q in (Q_COUNT, Q_RANGE, Q_TOP)
        }
        assert r.json()["upper_questions"]["questions"]["written"] == list(names.values())
        tools = {t["name"]: t for t in _tools(tmp_path, dsid)}
        assert set(names.values()) <= set(tools)
        assert "find_by_label" in tools  # 人のツールは触らない
        assert tools[names["q1"]]["title"] == Q_COUNT["title"]
        assert tools[names["q1"]]["query"].startswith("SELECT ?class")
        assert "GRAPH" not in tools[names["q1"]]["query"]
        # 自動の 3 本（予約名）は従来どおり
        assert {"counts_by_kind", "value_range", "top_value"} <= set(tools)

        # 公開後の ⑥ 実行は公開した版を読む
        run = client.post(
            f"/api/datasets/{dsid}/trial-queries/run",
            json={"op": "count", "kind_iri": EX + "Sample"},
        ).json()
        assert run["read_from"] == "published" and run["classes"][0]["n"] == 3

        # 問いを 1 本だけ残して再公開 → ほかの q_ は消え、人のツールと予約 3 本は残る
        client.put(f"/api/datasets/{dsid}/questions", json={"questions": [Q_RANGE]})
        _stage(tmp_path, store, dsid, seq=2)
        assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200
        after = {t["name"] for t in _tools(tmp_path, dsid)}
        assert names["q2"] in after and names["q1"] not in after and names["q3"] not in after
        assert "find_by_label" in after and "counts_by_kind" in after


def test_a_question_that_fails_lint_is_marked_and_not_made_a_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dsid = _dataset(tmp_path)
    root = tmp_path / "registry"
    registry.write_artifact(
        root, dsid, "questions.json", questions_routes.dump_questions([Q_COUNT, Q_RANGE])
    )
    real = questions_routes.lint_query_tool

    def fake(tool, *a, **k):
        if tool.title == Q_COUNT["title"]:
            return type("L", (), {"errors": ("SPARQL syntax error: boom",), "warnings": ()})()
        return real(tool, *a, **k)

    monkeypatch.setattr(questions_routes, "lint_query_tool", fake)
    report = questions_routes.apply_questions(root, dsid)
    assert [e["id"] for e in report["lint_errors"]] == ["q1"]
    assert report["written"] == [questions_routes.question_tool_name("q2")]
    saved = {
        q["id"]: q
        for q in questions_routes.load_questions(registry.load_dataset(root, dsid)["artifacts"])
    }
    assert "boom" in saved["q1"]["lint_error"] and "lint_error" not in saved["q2"]


def test_apply_questions_does_not_create_an_empty_tools_file(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    report = questions_routes.apply_questions(tmp_path / "registry", dsid)
    assert report == {"written": [], "lint_errors": [], "warnings": []}
    assert not (tmp_path / "registry" / dsid / "query_tools.yaml").exists()


def test_apply_questions_leaves_an_unreadable_tools_file_alone(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    root = tmp_path / "registry"
    registry.write_artifact(
        root, dsid, "questions.json", questions_routes.dump_questions([Q_COUNT])
    )
    broken = root / dsid / "query_tools.yaml"
    broken.write_text("tools: [unclosed", encoding="utf-8")
    report = questions_routes.apply_questions(root, dsid)
    assert report["written"] == [] and report["warnings"]
    assert broken.read_text(encoding="utf-8") == "tools: [unclosed"


def test_names_only_publish_also_consumes_upper_and_questions(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    line = {"subject": EX + "price", "term": SV + "amount", "relation": "equivalentProperty"}
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "amount", kind="property")
        assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200
        # 公開したあとで、当てはめと問いを足す → 名前の違いが無くても名前だけの公開が消費する
        client.put(f"/api/datasets/{dsid}/upper", json={"upper": [line]})
        client.put(f"/api/datasets/{dsid}/questions", json={"questions": [Q_TOP]})
        r = client.post(f"/api/datasets/{dsid}/publish-names")
        assert r.status_code == 200, r.text
        report = r.json()["upper_questions"]
        assert report["upper"]["applied"] == 1
        assert report["questions"]["written"] == [questions_routes.question_tool_name("q3")]
        assert any(t["name"].startswith("q_") for t in _tools(tmp_path, dsid))
        (item,) = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        assert item["applied_at"]
        # もう一度押しても線は増えない（1 回だけ消費）
        again = client.post(f"/api/datasets/{dsid}/publish-names").json()["upper_questions"]
        assert again["upper"]["applied"] == 0


# ---------------------------------------------------------------------------
# 6. 予約名
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["counts_by_kind", "value_range", "top_value", "q_deadbeef", "q_x"]
)
def test_reserved_tool_names_are_422_on_the_human_save_path(tmp_path: Path, name: str) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    tool = {
        "name": name,
        "title": "t",
        "description": "d",
        "parameters": [],
        "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1",
        "result": {"item": {"iri": "s"}},
    }
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dsid}/tools", json=tool)
        assert r.status_code == 422, r.text
        assert (
            client.post(
                f"/api/datasets/{dsid}/tools", json={**tool, "name": "quantity_q"}
            ).status_code
            == 200
        )
        assert [t["name"] for t in _tools(tmp_path, dsid)] == ["quantity_q"]


# ---------------------------------------------------------------------------
# 7. ことばへ写す
# ---------------------------------------------------------------------------


def test_copy_a_question_to_the_shared_term_above_it(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "specimen")
        _mint(client, "amount", kind="property")
        client.put(f"/api/datasets/{dsid}/questions", json={"questions": [Q_COUNT, Q_RANGE]})
        url = "/api/vocab/shared/specimen/cq"
        body = {"from_dataset": dsid, "question_id": "q1"}

        # 上位がまだ無い → 409 と案内
        r = client.post(url, json=body)
        assert r.status_code == 409 and "線を引いて" in r.json()["detail"]

        # 線を引く（種類 → 語）
        align = client.post(
            "/api/crosswalk/align",
            json={"source": EX + "Sample", "target": SV + "specimen", "relation": "subClassOf"},
        )
        assert align.status_code == 200, align.text
        r = client.post(url, json=body)
        assert r.status_code == 201, r.text
        assert r.json()["cq"]["tool_name"] == "cq_specimen_count_2"
        assert r.json()["cq"]["title"] == Q_COUNT["title"]  # 題を省くと元の問いの題
        assert r.json()["from"] == {"dataset_id": dsid, "question_id": "q1"}
        (term,) = [
            t for t in client.get("/api/vocab/shared").json()["terms"] if t["slug"] == "specimen"
        ]
        assert len(term["cqs"]) == 2
        # 元は残る（コピー）
        assert client.get(f"/api/datasets/{dsid}/questions").json()["questions"][0]["id"] == "q1"

        # 項目の問い（範囲）は項目の語へ。線が無い・別の語は 409
        assert (
            client.post(
                "/api/vocab/shared/amount/cq", json={"from_dataset": dsid, "question_id": "q2"}
            ).status_code
            == 409
        )
        assert (
            client.post(url, json={"from_dataset": dsid, "question_id": "q2"}).status_code == 409
        )  # 種類の語に項目の問い
        # 存在しない問い・データセット・片方だけ
        assert (
            client.post(url, json={"from_dataset": dsid, "question_id": "nope"}).status_code == 404
        )
        assert (
            client.post(url, json={"from_dataset": "nope", "question_id": "q1"}).status_code == 404
        )
        assert client.post(url, json={"from_dataset": dsid}).status_code == 422
        # 通常の足し方は title / op が要る
        assert client.post(url, json={}).status_code == 422


# ---------------------------------------------------------------------------
# 8. PR 3 の追加: 項目の線（property:<map>/<列>）・q_ の誤削除・写すの重複・handles の atomic
# ---------------------------------------------------------------------------


def test_property_ref_item_becomes_a_subproperty_line_on_publish(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    ref = "property:SampleMap/price"
    ghost_col = "property:SampleMap/ghost"
    ghost_map = "property:NoSuchMap/price"
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "amount", kind="property")
        upper = [
            {"subject": ref, "term": SV + "amount", "relation": "subPropertyOf"},
            {"subject": ghost_col, "term": SV + "amount", "relation": "subPropertyOf"},
            {"subject": ghost_map, "term": SV + "amount", "relation": "subPropertyOf"},
        ]
        put = client.put(f"/api/datasets/{dsid}/upper", json={"upper": upper})
        assert put.status_code == 200, put.text  # property: は subject として通る
        r = client.post(f"/api/datasets/{dsid}/promote")
        assert r.status_code == 200, r.text
        report = r.json()["upper_questions"]["upper"]
        assert report["applied"] == 1
        assert {s["subject"]: s["reason"] for s in report["skipped"]} == {
            ghost_col: "predicate missing",
            ghost_map: "predicate missing",
        }
        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert [(a["source"], a["target"]) for a in lines] == [(EX + "price", SV + "amount")]
        rows = {u["subject"]: u for u in client.get(f"/api/datasets/{dsid}/upper").json()["upper"]}
        assert rows[ref]["applied_at"] and rows[ghost_col]["applied_at"] is None
        # 1 回だけ消費
        _stage(tmp_path, store, dsid, seq=2)
        again = client.post(f"/api/datasets/{dsid}/promote").json()["upper_questions"]["upper"]
        assert again["applied"] == 0
        # 形の悪い property: は 422
        bad = {"subject": "property:nomap", "term": SV + "amount", "relation": "subPropertyOf"}
        assert client.put(f"/api/datasets/{dsid}/upper", json={"upper": [bad]}).status_code == 422


def test_resolve_property_ref_expands_the_prefix_and_reads_columns() -> None:
    from asterism.mapping_ir_read import read_mapping_ir

    from asterism_api import upper_routes

    view = read_mapping_ir(_IR)
    assert upper_routes.resolve_property_ref(view, "property:SampleMap/weight") == EX + "weight"
    assert upper_routes.resolve_property_ref(view, "property:SampleMap/nope") is None
    assert upper_routes.resolve_property_ref(view, "https://example.org/onto#weight") is None


def test_republish_keeps_a_human_saved_q_tool_but_drops_a_question_tool(tmp_path: Path) -> None:
    dsid = _dataset(tmp_path)
    root = tmp_path / "registry"
    # 人が以前に保存した q_ 名のツール（origin の印なし）
    human = {
        "name": "q_legacy",
        "title": "人が保存した",
        "description": "d",
        "parameters": [],
        "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1",
        "result": {"item": {"iri": "s"}},
    }
    (root / dsid / "query_tools.yaml").write_text(
        yaml.safe_dump({"tools": [human]}, allow_unicode=True), encoding="utf-8"
    )
    registry.write_artifact(
        root, dsid, "questions.json", questions_routes.dump_questions([Q_COUNT])
    )
    report = questions_routes.apply_questions(root, dsid)
    qname = questions_routes.question_tool_name("q1")
    assert report["written"] == [qname]
    tools = {t["name"]: t for t in _tools(tmp_path, dsid)}
    assert tools[qname]["origin"] == "question" and "origin" not in tools["q_legacy"]
    # 問いを空にして再公開 → 問いのツールだけ消え、人の q_legacy は残る
    registry.write_artifact(root, dsid, "questions.json", questions_routes.dump_questions([]))
    questions_routes.apply_questions(root, dsid)
    assert {t["name"] for t in _tools(tmp_path, dsid)} == {"q_legacy"}


def test_copy_to_term_records_source_and_a_second_copy_returns_the_existing(
    tmp_path: Path,
) -> None:
    dsid = _dataset(tmp_path)
    app, store = _app(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "specimen")
        client.put(f"/api/datasets/{dsid}/questions", json={"questions": [Q_COUNT]})
        client.post(
            "/api/crosswalk/align",
            json={"source": EX + "Sample", "target": SV + "specimen", "relation": "subClassOf"},
        )
        url = "/api/vocab/shared/specimen/cq"
        body = {"from_dataset": dsid, "question_id": "q1"}
        first = client.post(url, json=body)
        assert first.status_code == 201, first.text
        second = client.post(url, json=body)
        assert second.status_code == 200, second.text
        assert second.json()["cq"] == first.json()["cq"]
        (term,) = [
            t for t in client.get("/api/vocab/shared").json()["terms"] if t["slug"] == "specimen"
        ]
        assert len(term["cqs"]) == 2  # 鋳造時の 1 本 + 写した 1 本（増えていない）
        tools = yaml.safe_load(
            (tmp_path / "registry" / shared_vocab.REGISTRY_ID / "query_tools.yaml").read_text(
                encoding="utf-8"
            )
        )["tools"]
        copied = [t for t in tools if t["name"] == first.json()["cq"]["tool_name"]]
        assert copied[0]["source"] == {"dataset": dsid, "question_id": "q1"}


def test_put_handles_writes_through_the_atomic_artifact_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dsid = _dataset(tmp_path)
    app, _ = _app(tmp_path)
    calls: list[tuple[str, str]] = []
    real = registry.write_artifact

    def spy(root, dataset_id, key, text):
        calls.append((dataset_id, key))
        return real(root, dataset_id, key, text)

    monkeypatch.setattr(registry, "write_artifact", spy)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(
            f"/api/datasets/{dsid}/handles",
            json={"handles": [{"source": "samples.csv", "column": "sid"}]},
        )
        assert r.status_code == 200, r.text
    assert (dsid, "handles.json") in calls
    assert load_handles(registry.load_dataset(tmp_path / "registry", dsid)["artifacts"]) == [
        {"source": "samples.csv", "column": "sid"}
    ]
    assert not list((tmp_path / "registry" / dsid).glob("*.tmp"))
