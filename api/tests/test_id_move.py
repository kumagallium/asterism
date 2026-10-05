"""ID の引っ越し — 公開したあとの再設計で、前の ID を生かす（ADR id-move-after-publish.md）。

ここで確かめるのは「配ってしまった引用が、いま何を返すか」の一点に尽きる。
Oxigraph 側は httpx.MockTransport の台本: 転送台帳グラフ・公開スコープ・説明クエリに
それぞれ答えるので、実ストアなしで **本物のクエリの組み立て** を固定できる。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

_CANONICAL = "https://kumagallium.github.io/asterism/graph/canonical/dataset-x/v2"
_ONTOLOGY = "https://kumagallium.github.io/asterism/graph/ontology/dataset-x"
_LEDGER = "https://kumagallium.github.io/asterism/graph/moved/dataset-x"
_NS = "https://data.lab.jp/asterism/datasets/zem/resource/"
_OLD = f"{_NS}sample/1"  # 論文単位で数えていた頃の ID（配ってしまった引用）
_NEW = f"{_NS}sample/1-10"
_NEW2 = f"{_NS}sample/1-11"
_GONE = f"{_NS}sample/9"  # 台帳が知っているが、行き先が公開されていない
_STRANGER = f"{_NS}sample/never-existed"


_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}


def _settings(tmp: Path) -> Settings:
    s = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": "http://test",
            "CSV2RDF_SETTLE_S": "0.0",
        }
    )
    s.api_token = _TEST_TOKEN  # write routes are fail-closed without one
    return s


def _select_json(rows: list[dict[str, dict[str, str]]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"head": {"vars": []}, "results": {"bindings": rows}},
        headers={"Content-Type": "application/sparql-results+json"},
    )


def _uri(v: str) -> dict[str, str]:
    return {"type": "uri", "value": v}


def _lit(v: str) -> dict[str, str]:
    return {"type": "literal", "value": v}


def _mock_client(
    *,
    ledger: dict[str, list[str]] | None = None,
    published: set[str] | None = None,
) -> tuple[OxigraphClient, list[str]]:
    """A scripted store: ``ledger`` is the forwarding table, ``published`` the ids
    the canonical scope actually describes."""
    ledger = ledger or {}
    published = published if published is not None else {_NEW, _NEW2}
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        queries.append(q)
        if "promoted" in q:  # canonical_graphs
            return _select_json([{"g": _uri(_CANONICAL)}])
        # 台帳の 1 ホップは台帳グラフ名も含むので、グラフ列挙より先に判定する。
        if substrate.ID_MOVED_PREDICATE in q:  # one forwarding hop
            out: list[dict[str, dict[str, str]]] = []
            for subject, targets in ledger.items():
                if f"<{subject}>" in q:
                    out.extend({"new": _uri(t)} for t in targets)
            return _select_json(out)
        if substrate.MOVED_GRAPH_BASE in q:  # moved_graphs
            return _select_json([{"g": _uri(_LEDGER)}] if ledger else [])
        if "GRAPH ?g {}" in q:  # ontology_graphs
            return _select_json([{"g": _uri(_ONTOLOGY)}])
        if "SELECT DISTINCT ?s" in q:  # presence of candidates in the published scope
            return _select_json(
                [{"s": _uri(i)} for i in sorted(published) if f"<{i}>" in q]
            )
        if q.lstrip().startswith("CONSTRUCT"):
            for iri in published:
                if f"<{iri}> ?p ?o" in q:
                    return httpx.Response(
                        200,
                        text=f'<{iri}> <https://schema.org/name> "Bi2Te3" .\n',
                        headers={"Content-Type": "text/turtle"},
                    )
            return httpx.Response(200, text="", headers={"Content-Type": "text/turtle"})
        if "VALUES ?lp" in q:  # label lookup
            return _select_json([])
        for iri in published:  # description outbound
            if f"<{iri}> ?p ?o" in q:
                return _select_json(
                    [
                        {
                            "p": _uri("http://www.w3.org/2000/01/rdf-schema#label"),
                            "o": _lit(f"試料 {iri.rsplit('/', 1)[-1]}"),
                            "g": _uri(_CANONICAL),
                        }
                    ]
                )
        return _select_json([])

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner), queries


def _app_client(tmp: Path, oxi: OxigraphClient) -> TestClient:
    return TestClient(build_app(_settings(tmp), oxigraph_client=oxi, start_watcher=False))


# ---------------------------------------------------------------------------
# 配ってしまった引用が、いま何を返すか
# ---------------------------------------------------------------------------


def test_moved_id_shows_the_new_content_and_says_so(tmp_path: Path) -> None:
    """行き先が 1 つなら、中身をそのまま見せる —— ただし黙って転送はしない。"""
    oxi, _ = _mock_client(ledger={_OLD: [_NEW]})
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _OLD})
    assert res.status_code == 200
    assert "引っ越しました" in res.text
    assert _OLD in res.text  # たどってきた前の ID を必ず出す
    assert "試料 1-10" in res.text


def test_split_id_lists_every_destination(tmp_path: Path) -> None:
    """1 行 = 1 試料に数え直すと、1 つの ID が複数に分かれる。どれか 1 つを
    勝手に選ばず、全部見せる（ここが owl:sameAs を採らなかった理由そのもの）。"""
    oxi, _ = _mock_client(ledger={_OLD: [_NEW, _NEW2]})
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _OLD})
    assert res.status_code == 200
    assert "分かれました" in res.text
    assert _NEW in res.text
    assert _NEW2 in res.text


def test_chain_is_followed_through_a_dead_middle_hop(tmp_path: Path) -> None:
    """v1→v2→v3。途中の v2 はもうどのグラフにも無いが、v1 の引用は生きる。"""
    mid = f"{_NS}sample/1-mid"
    oxi, _ = _mock_client(ledger={_OLD: [mid], mid: [_NEW]}, published={_NEW})
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _OLD})
    assert res.status_code == 200
    assert "試料 1-10" in res.text
    assert _OLD in res.text


def test_moved_but_unpublished_destination_is_not_a_plain_404(tmp_path: Path) -> None:
    """行き先が未公開なら「引っ越したが、まだ公開されていない」と言う。"""
    oxi, _ = _mock_client(ledger={_GONE: [f"{_NS}sample/9-1"]}, published=set())
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _GONE})
    assert res.status_code == 200
    assert "公開されていません" in res.text


def test_unknown_id_is_still_an_honest_404(tmp_path: Path) -> None:
    """台帳が何も知らない ID は、これまで通り 404。引っ越しの仕掛けが
    「見つからない」を曖昧にしてはいけない。"""
    oxi, _ = _mock_client(ledger={_OLD: [_NEW]})
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _STRANGER})
    assert res.status_code == 404


def test_no_ledger_at_all_keeps_the_old_behaviour(tmp_path: Path) -> None:
    oxi, _ = _mock_client(ledger={})
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _OLD})
    assert res.status_code == 404


def test_turtle_answers_a_moved_id_with_is_replaced_by(tmp_path: Path) -> None:
    """機械にも同じ答えを返す —— 追えるように。"""
    oxi, _ = _mock_client(ledger={_OLD: [_NEW, _NEW2]})
    with _app_client(tmp_path, oxi) as client:
        res = client.get(
            "/describe", params={"iri": _OLD}, headers={"Accept": "text/turtle"}
        )
    assert res.status_code == 200
    assert substrate.ID_MOVED_PREDICATE in res.text
    assert f"<{_NEW}>" in res.text and f"<{_NEW2}>" in res.text


def test_a_live_id_never_consults_the_ledger(tmp_path: Path) -> None:
    """公開中の ID は台帳を引かずに答える（引っ越しは「見つからない」ときだけの道）。"""
    oxi, queries = _mock_client(ledger={_OLD: [_NEW]})
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/describe", params={"iri": _NEW})
    assert res.status_code == 200
    assert not any(substrate.ID_MOVED_PREDICATE in q for q in queries)


# ---------------------------------------------------------------------------
# 公開の直前に人へ見せる材料
# ---------------------------------------------------------------------------


def _seed_dataset(tmp: Path, meta_extra: dict) -> Path:
    root = tmp / "registry"
    (root / "dataset-x").mkdir(parents=True)
    meta = {"id": "dataset-x", "name": "ZEM", **meta_extra}
    (root / "dataset-x" / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_id_move_endpoint_reports_the_recorded_plan(tmp_path: Path) -> None:
    _seed_dataset(
        tmp_path,
        {
            "promoted": True,
            "id_move": {
                "changes_ids": True,
                "fully_movable": True,
                "forwarded": 1204,
                "moved": [{"name": "sample"}],
                "blocked": [],
            },
        },
    )
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/api/datasets/dataset-x/id-move")
    assert res.status_code == 200
    body = res.json()
    assert body["changes_ids"] is True
    assert body["forwarded"] == 1204


def test_id_move_endpoint_says_no_when_nothing_moves(tmp_path: Path) -> None:
    """初公開・意味だけの修正・未公開データ —— どれも「住所は動かない」。"""
    _seed_dataset(tmp_path, {"promoted": False})
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        res = client.get("/api/datasets/dataset-x/id-move")
    assert res.status_code == 200
    assert res.json()["changes_ids"] is False


# ---------------------------------------------------------------------------
# 「公開した時点の ID の作り方」の記録
# ---------------------------------------------------------------------------


def test_backfill_only_fills_a_promoted_dataset_once(tmp_path: Path) -> None:
    """公開済みで未記録のときだけ書く。あとから来た再設計が自分の subject を
    「公開したもの」として滑り込ませられてはならない。"""
    root = _seed_dataset(tmp_path, {"promoted": True})
    first = [{"name": "sample", "source": "s.csv", "template": "https://e/s/{a}"}]
    second = [{"name": "sample", "source": "s.csv", "template": "https://e/s/{a}-{b}"}]
    registry.backfill_published_subjects(root, "dataset-x", first)
    registry.backfill_published_subjects(root, "dataset-x", second)
    meta = json.loads((root / "dataset-x" / "meta.json").read_text(encoding="utf-8"))
    assert meta["published_subjects"] == first


def test_backfill_skips_a_dataset_that_was_never_published(tmp_path: Path) -> None:
    root = _seed_dataset(tmp_path, {"promoted": False})
    registry.backfill_published_subjects(
        root, "dataset-x", [{"name": "s", "source": "s.csv", "template": "https://e/{a}"}]
    )
    meta = json.loads((root / "dataset-x" / "meta.json").read_text(encoding="utf-8"))
    assert "published_subjects" not in meta


def test_record_id_move_clears_a_stale_record(tmp_path: Path) -> None:
    """住所を動かさなかった再取り込みのあとに、前回の「1204 件動きます」が
    残っていてはいけない。"""
    root = _seed_dataset(tmp_path, {"promoted": True, "id_move": {"changes_ids": True}})
    registry.record_id_move(root, "dataset-x", None)
    meta = json.loads((root / "dataset-x" / "meta.json").read_text(encoding="utf-8"))
    assert meta["id_move"] is None


# ---------------------------------------------------------------------------
# 公開後に「データの数えかた」へ戻るための材料
# ---------------------------------------------------------------------------

_SPEC = """version: 1
prefixes:
  ex: "https://e.invalid/ns#"
  exr: "https://e.invalid/r/"
maps:
  - name: sample
    source: s.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:name
        column: name
"""


def _seed_with_design(tmp: Path, *, spec: str | None = _SPEC, source: bool = True) -> Path:
    root = _seed_dataset(tmp, {"promoted": True})
    dest = root / "dataset-x"
    if spec is not None:
        (dest / "mapping.yaml").write_text(spec, encoding="utf-8")
    if source:
        sdir = dest / "source"
        sdir.mkdir()
        (sdir / "s.csv").write_text("sid,name\n1,Bi2Te3\n", encoding="utf-8")
    return root


def test_recount_hands_back_the_skeleton_and_a_readable_source(tmp_path: Path) -> None:
    """見直しは元ファイルを手放して S6 から始まる。数えかたへ戻るには、
    サーバが持っている骨格と元ファイルを渡し直すしかない。"""
    _seed_with_design(tmp_path)
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        res = client.post("/api/datasets/dataset-x/recount", headers=_AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["skeleton"]["maps"][0]["subject"]["template"] == "exr:sample/{sid}"
    # 骨格は subject だけ —— 数えかたのゲートが問うのは ID の作り方であって
    # 項目ではない（properties を持ち込むと S4 が別の画面になる）。
    assert "properties" not in body["skeleton"]["maps"][0]
    assert body["staging_id"]
    assert body["sources"] == [{"name": "s.csv", "size": len(b"sid,name\n1,Bi2Te3\n")}]


_SPEC_WITH_CATALOG = """version: 1
prefixes:
  ex: "https://e.invalid/ns#"
  exr: "https://e.invalid/r/"
maps:
  - name: sample
    source: s.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:name
        column: name
  - name: composition
    source: s.csv
    subject:
      template: "exr:composition/{name}"
      classes: [ex:Composition]
    properties:
      - predicate: ex:composition
        column: name
      - predicate: ex:sample
        object_template: "exr:sample/{sid}"
"""


def test_recount_gives_a_value_catalog_its_owns_back(tmp_path: Path) -> None:
    """⑤（数えかたのゲート）は owns == キー列 で「値のカタログ」を見分け、同じ値の
    行がまとまることを事故として言わない（K33）。見直しで組み直した骨格に owns が
    無いと、カタログ全部に「複数の行が同じ ID」が並ぶ（利用者報告 2026-09-03）。"""
    _seed_with_design(tmp_path, spec=_SPEC_WITH_CATALOG)
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        res = client.post("/api/datasets/dataset-x/recount", headers=_AUTH)
    assert res.status_code == 200, res.text
    by_name = {m["name"]: m for m in res.json()["skeleton"]["maps"]}
    assert by_name["composition"]["owns"] == ["name"]
    assert "owns" not in by_name["sample"]


def test_recount_refuses_a_design_with_no_mapping_spec(tmp_path: Path) -> None:
    """生 Turtle しか無い古い設計は、構造の作り直しが詳細モードの担当のまま。"""
    _seed_with_design(tmp_path, spec=None)
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        res = client.post("/api/datasets/dataset-x/recount", headers=_AUTH)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "dataset.no_mapping_spec"


def test_recount_refuses_when_no_source_is_kept(tmp_path: Path) -> None:
    """証拠を出せない数えかたの画面は開かない（S4 は実データで見せる画面）。"""
    _seed_with_design(tmp_path, source=False)
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        res = client.post("/api/datasets/dataset-x/recount", headers=_AUTH)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "dataset.no_source"


# ---------------------------------------------------------------------------
# 取り込みの経路 —— 記録は「いま下書きにある版」の話でなければならない
# ---------------------------------------------------------------------------
# 公開の前に出す知らせ（かんたんモードの S8・データセットの詳細の公開確認）は、
# ここで meta に残した記録をそのまま読む。記録が前の回のまま残ると、画面は
# 「たどれなくなります」を言い続けるか、言うべきときに黙る。


def _spec(template: str, name: str = "sample") -> str:
    return _SPEC.replace("exr:sample/{sid}", template).replace("name: sample", f"name: {name}")


class _IngestOxi:
    """取り込みと公開の両方に答える偽ストア（/store・/update を記録するだけ）。"""

    def __init__(self) -> None:
        self.store_calls: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store":
                self.store_calls.append(request.url.params.get("graph"))
                return httpx.Response(204)
            if request.url.path == "/update":
                return httpx.Response(204)
            q = request.content.decode()
            rows = [{"c": {"value": "0"}}] if "COUNT" in q else []
            return httpx.Response(
                200,
                text=json.dumps({"head": {"vars": []}, "results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _fake_materializer(rml_ttl, csv_dir, *, udfs_path=None, work_dir=None, run_id=None,
                       should_cancel=None) -> Path:
    """Morph-KGC の代わりに 1 行ぶんを書く（台帳の RML なら転送 1 件になる）。"""
    out = Path(work_dir) / "out.nt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f'<{_OLD}> <https://schema.org/name> "x" .\n', encoding="utf-8")
    return out


def _redesign(root: Path, spec: str) -> None:
    """設計を保存し直す（かんたんモードの見直しが materialize でやること）。"""
    from asterism_step0.mapping_ir import parse_mapping_ir
    from asterism_step0.rml_compile import compile_mapping_ir

    dest = root / "dataset-x"
    (dest / "mapping.yaml").write_text(spec, encoding="utf-8")
    (dest / "mapping.rml.ttl").write_text(
        compile_mapping_ir(parse_mapping_ir(spec)), encoding="utf-8"
    )


def _ingest(client: TestClient, csv: bytes) -> dict:
    res = client.post(
        "/api/datasets/dataset-x/ingest",
        files={"files": ("s.csv", csv, "text/csv")},
        headers=_AUTH,
    )
    assert res.status_code == 202, res.text
    stream = client.get(f"/api/jobs/{res.json()['job_id']}/stream").text
    done = [
        json.loads(line[len("data:") :])
        for line in stream.splitlines()
        if line.startswith("data:") and '"result"' in line
    ]
    assert done, stream
    return done[-1]["result"]


def _published_app(tmp: Path, monkeypatch) -> TestClient:
    """`exr:sample/{sid}` で 1 回公開済みのデータセットと、それを載せたアプリ。"""
    root = _seed_dataset(tmp, {"has_rml": True})
    (root / "dataset-x" / "source").mkdir()
    _redesign(root, _SPEC)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_materializer)
    client = TestClient(
        build_app(_settings(tmp), oxigraph_client=_IngestOxi().client, start_watcher=False)
    )
    client.__enter__()
    _ingest(client, b"sid,name\n1,Bi2Te3\n")
    assert client.post("/api/datasets/dataset-x/promote", headers=_AUTH).status_code == 200
    return client


def _meta(tmp: Path) -> dict:
    return json.loads(
        (tmp / "registry" / "dataset-x" / "meta.json").read_text(encoding="utf-8")
    )


def test_a_blocked_move_can_be_published_from_outside_the_wizard(
    tmp_path: Path, monkeypatch
) -> None:
    """設計を見直して取り込んだあと、ウィザードを離れても下書きは残る。詳細画面は
    その下書きに「公開を更新する」を出し、サーバは止めない（ADR §5）。だから
    「前の ID からたどれなくなります」は、公開を押せる**すべての画面**が読める
    場所に残っていなければならない。"""
    client = _published_app(tmp_path, monkeypatch)
    root = tmp_path / "registry"
    # 前の ID を綴っていた列 sid が、いまのファイルには無い。
    _redesign(root, _spec("exr:sample/{code}").replace("column: name", "column: code"))
    _ingest(client, b"code\nA1\n")

    meta = _meta(tmp_path)
    # 詳細画面が「公開を更新する」を出す条件（下書きがあり、公開したことがある）。
    assert meta["ingested"] is True and meta["version"] == 1
    move = client.get("/api/datasets/dataset-x/id-move").json()
    assert move["changes_ids"] is True and move["fully_movable"] is False
    assert move["blocked"][0]["reason"] == "missing_columns"
    assert move["blocked"][0]["missing_columns"] == ["sid"]
    # 公開そのものは通る —— 知らせるのは画面の仕事。
    assert client.post("/api/datasets/dataset-x/promote", headers=_AUTH).status_code == 200
    client.__exit__(None, None, None)


def test_putting_the_id_back_takes_the_warning_away(tmp_path: Path, monkeypatch) -> None:
    """知らせの戻り道は「ID の作り方を元に戻してください」。元に戻して取り込み
    直したのに警告が残るなら、戻り道は行き止まりである。"""
    client = _published_app(tmp_path, monkeypatch)
    root = tmp_path / "registry"
    _redesign(root, _spec("exr:sample/{code}").replace("column: name", "column: code"))
    _ingest(client, b"code\nA1\n")
    assert client.get("/api/datasets/dataset-x/id-move").json()["fully_movable"] is False

    _redesign(root, _SPEC)  # 元の ID の作り方に戻す
    _ingest(client, b"sid,name\n1,Bi2Te3\n")
    assert client.get("/api/datasets/dataset-x/id-move").json()["changes_ids"] is False
    client.__exit__(None, None, None)


def test_a_failed_redraft_keeps_the_warning_of_the_staged_draft(
    tmp_path: Path, monkeypatch
) -> None:
    """引き継げない下書きが残っている間は、その警告も残る。元に戻す取り込みが
    下書きの確定の手前で落ちたら、下書きは前のまま —— 記録だけ先に消してはいけない。"""
    client = _published_app(tmp_path, monkeypatch)
    root = tmp_path / "registry"
    _redesign(root, _spec("exr:sample/{code}").replace("column: name", "column: code"))
    _ingest(client, b"code\nA1\n")
    staged = _meta(tmp_path)["graph_iri"]

    async def _boom(*args, **kwargs) -> None:
        raise RuntimeError("store went away")

    monkeypatch.setattr(substrate, "set_staged_graph", _boom)
    _redesign(root, _SPEC)
    res = client.post(
        "/api/datasets/dataset-x/ingest",
        files={"files": ("s.csv", b"sid,name\n1,Bi2Te3\n", "text/csv")},
        headers=_AUTH,
    )
    assert res.status_code == 202
    stream = client.get(f"/api/jobs/{res.json()['job_id']}/stream").text
    assert "store went away" in stream
    assert _meta(tmp_path)["graph_iri"] == staged  # 下書きは前のまま
    assert client.get("/api/datasets/dataset-x/id-move").json()["fully_movable"] is False
    client.__exit__(None, None, None)


def test_a_second_draft_still_plans_the_move(tmp_path: Path, monkeypatch) -> None:
    """公開 → 意味だけ直して下書き → やっぱり ID も直して下書き。2 回めの取り込みの
    時点では meta.promoted はもう False だが、街に出ているのは最初の ID のまま。
    ここで計画を省くと、転送も知らせも無いまま前の ID が消える。"""
    client = _published_app(tmp_path, monkeypatch)
    root = tmp_path / "registry"
    _ingest(client, b"sid,name\n1,Bi2Te3\n")  # 住所を動かさない下書き
    assert client.get("/api/datasets/dataset-x/id-move").json()["changes_ids"] is False

    _redesign(root, _spec("exr:sample/{sid}-{name}"))
    result = _ingest(client, b"sid,name\n1,Bi2Te3\n")
    move = client.get("/api/datasets/dataset-x/id-move").json()
    assert move["changes_ids"] is True and move["fully_movable"] is True
    assert move["forwarded"] == 1
    assert result["id_move"]["forwarded"] == 1
    client.__exit__(None, None, None)


def test_a_published_move_is_not_announced_again(tmp_path: Path, monkeypatch) -> None:
    """ID を引っ越して公開し終えたあと、同じ設計でファイルだけ差し替える。
    この更新では住所は動かないので、前回の「N 件の ID が変わります」を
    もう一度出してはいけない。"""
    client = _published_app(tmp_path, monkeypatch)
    root = tmp_path / "registry"
    _redesign(root, _spec("exr:sample/{sid}-{name}"))
    _ingest(client, b"sid,name\n1,Bi2Te3\n")
    assert client.get("/api/datasets/dataset-x/id-move").json()["changes_ids"] is True
    assert client.post("/api/datasets/dataset-x/promote", headers=_AUTH).status_code == 200
    # 公開した時点で、知らせる下書きはもう無い。
    assert client.get("/api/datasets/dataset-x/id-move").json()["changes_ids"] is False

    _ingest(client, b"sid,name\n1,Bi2Te3\n2,PbTe\n")
    assert client.get("/api/datasets/dataset-x/id-move").json()["changes_ids"] is False
    client.__exit__(None, None, None)
