"""名前だけの公開 — ``GET …/published-names`` と ``POST …/publish-names``。

項目の意味を直して保存すると、書き換わるのは保存済みの設計だけで、ストアの名前
（オントロジーの graph の ``rdfs:label``）は公開のときにしか変わらない。意味だけを
直したときは下書きができず、その「公開」が来ない — 公開済みの ID を開いたページは
前の名前のまま残った（実機 2026-10-05）。ここは、取り込み直さずに名前だけを公開側へ
出す入口の検査（ADR ontology-canonical-lifecycle.md §3.2）。

Oxigraph は MockTransport（``test_trial_queries.py`` と同じ型）。``/query`` には
公開されている名前を返し、``/update`` に届いた更新を控える。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}
_EX = "https://example.org/onto#"
_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

_RML = """\
@prefix rr:  <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
@prefix ex:  <https://example.org/onto#> .

<#SampleMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sample/{sid}" ;
    rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:price ;
    rr:objectMap [ rml:reference "price" ] ] .
"""


def _mapping_ir(*, price: str = "税込価格", kind: str = "価格表 の 1 行", extra: str = "") -> str:
    """設計（Mapping IR）。``price`` が「値段」の列に、``kind`` が種類に付けた、いまの名前。"""
    return f"""\
version: 1
prefixes:
  ex: "{_EX}"
  exr: "https://example.org/resource/"
maps:
  - name: SampleMap
    source: samples.csv
    subject:
      template: "exr:sample/{{sid}}"
      classes: [ex:Sample]
      label: {json.dumps(kind, ensure_ascii=False)}
    properties:
      - predicate: ex:price
        column: price
        label: {json.dumps(price, ensure_ascii=False)}
        unit: "円"
      - predicate: ex:shop
        column: shop
        label: "店名"
{extra}"""


def _settings(tmp: Path) -> Settings:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    s = Settings(env)
    s.api_token = _TEST_TOKEN
    return s


def _save(tmp: Path, mapping_ir: str) -> dict:
    return registry.save_dataset(
        tmp / "registry",
        "Prices",
        {
            "diagram.md": "```mermaid\nclassDiagram\n  class Sample\n```\n",
            "model.yaml": "",
            "mie.yaml": "",
            "mapping.rml.ttl": _RML,
            "mapping.yaml": mapping_ir,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-10-05T00:00:00+00:00",
        proposal_md="# design v1\n",
    )


def _publish(tmp: Path, dataset_id: str) -> str:
    """取り込んで公開した状態にする（レジストリの印だけ。ストアはモック）。"""
    root = tmp / "registry"
    live = registry.mark_ingested(
        root,
        dataset_id,
        graph_iri=f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}/v1",
        triple_count=42,
        ingested_at="2026-10-05T00:00:00+00:00",
        data_seq=1,
    )["graph_iri"]
    registry.mark_promoted(
        root,
        dataset_id,
        triples_promoted=42,
        alignment={"predicates": {"reuse": [], "new": []}, "classes": {"reuse": [], "new": []}},
        promoted_at="2026-10-05T01:00:00+00:00",
        canonical_graph=f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}",
        live_graph=live,
    )
    return live


class _Store:
    """公開されている名前を返し、届いた更新を控えるモックの Oxigraph。"""

    def __init__(
        self,
        published: dict[str, str | list[str]],
        *,
        down: bool = False,
        refuse_updates: bool = False,
    ) -> None:
        self.published = published
        self.down = down
        self.refuse_updates = refuse_updates
        self.updates: list[str] = []
        self.queries: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        if request.url.path == "/update":
            self.updates.append(body)
            if self.refuse_updates:
                return httpx.Response(400, text="parse error")
            return httpx.Response(204)
        if request.url.path != "/query":
            return httpx.Response(204)
        self.queries.append(body)
        if f"<{_LABEL}> ?l" in body and "SELECT ?t ?l" in body:
            if self.down:
                return httpx.Response(500, text="boom")
            rows = [
                {"t": {"type": "uri", "value": iri}, "l": {"type": "literal", "value": name}}
                for iri, names in sorted(self.published.items())
                for name in ([names] if isinstance(names, str) else names)
            ]
            return _sparql_json(rows)
        return _sparql_json([])

    def client(self) -> OxigraphClient:
        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url="http://test"
        )
        return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _sparql_json(rows: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        text=json.dumps({"results": {"bindings": rows}}),
        headers={"content-type": "application/sparql-results+json"},
    )


# 公開したときの名前: 「値段」の列は、まだ「売値」と呼ばれていた。
_PUBLISHED: dict[str, str | list[str]] = {
    f"{_EX}Sample": "価格表 の 1 行",
    f"{_EX}price": "売値",
    f"{_EX}shop": "店名",
}


@contextmanager
def _started(tmp: Path, store: _Store, *, token: bool = True) -> Iterator[TestClient]:
    """起動し終えたアプリ。起動時の補完が出す問い合わせ・更新は、数える前に捨てる。"""
    app = build_app(_settings(tmp), oxigraph_client=store.client(), start_watcher=False)
    with TestClient(app, headers=_AUTH if token else None) as client:
        store.queries.clear()
        store.updates.clear()
        yield client


def test_published_names_lists_what_differs(tmp_path: Path) -> None:
    """意味を直して保存したあと: 公開側は「売値」のまま、設計は「税込価格」。"""
    meta = _save(tmp_path, _mapping_ir(price="税込価格"))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        r = client.get(f"/api/datasets/{meta['id']}/published-names")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert body["changes"] == [
            {"iri": f"{_EX}price", "kind": "property", "published": "売値", "design": "税込価格"}
        ]
        # 読むのは、このデータセットのオントロジーの graph だけ。
        onto = substrate.ontology_graph_iri(meta["id"])
        asked = [q for q in store.queries if "SELECT ?t ?l" in q]
        assert asked and all(f"GRAPH <{onto}>" in q for q in asked)
        assert store.updates == []  # 読むだけ


def test_published_names_is_empty_when_nothing_differs(tmp_path: Path) -> None:
    meta = _save(tmp_path, _mapping_ir(price="売値"))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        body = client.get(f"/api/datasets/{meta['id']}/published-names").json()
        assert body == {"dataset_id": meta["id"], "available": True, "changes": []}


def test_published_names_ignores_terms_that_are_not_published_yet(tmp_path: Path) -> None:
    """設計にしか無い項目は、名前の違いではない — まだ公開していない構造。"""
    extra = '      - predicate: ex:origin\n        column: origin\n        label: "産地"\n'
    meta = _save(tmp_path, _mapping_ir(price="税込価格", extra=extra))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        changes = client.get(f"/api/datasets/{meta['id']}/published-names").json()["changes"]
        assert [c["iri"] for c in changes] == [f"{_EX}price"]

        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 200, r.text
        assert len(store.updates) == 1
        assert f"{_EX}origin" not in store.updates[0]  # 構造はこの入口から公開されない
        assert "産地" not in store.updates[0]


def test_published_names_is_unavailable_without_a_published_version(tmp_path: Path) -> None:
    """下書きだけ／下書きが残っている: 名前はその公開（promote）で一緒に出る。"""
    meta = _save(tmp_path, _mapping_ir())
    root = tmp_path / "registry"
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        # 設計だけ
        body = client.get(f"/api/datasets/{meta['id']}/published-names").json()
        assert body["available"] is False and body["changes"] == []
        # 公開 → もう一度取り込んだ（新しい下書きが残っている）
        live = _publish(tmp_path, meta["id"])
        registry.mark_ingested(
            root,
            meta["id"],
            graph_iri=live.removesuffix("/v1") + "/v2",
            triple_count=10,
            ingested_at="2026-10-05T02:00:00+00:00",
            data_seq=2,
        )
        store.queries.clear()
        body = client.get(f"/api/datasets/{meta['id']}/published-names").json()
        assert body["available"] is False
        assert not [q for q in store.queries if "SELECT ?t ?l" in q]  # ストアにも聞かない

        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "dataset.names_not_published"
        assert store.updates == []

        assert client.get("/api/datasets/nope/published-names").status_code == 404
        assert client.post("/api/datasets/nope/publish-names").status_code == 404


def test_published_names_degrades_when_the_store_is_down(tmp_path: Path) -> None:
    meta = _save(tmp_path, _mapping_ir())
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED), down=True)
    with _started(tmp_path, store) as client:
        r = client.get(f"/api/datasets/{meta['id']}/published-names")
        assert r.status_code == 200, r.text  # 「ためす」の画面を止めない
        assert r.json()["available"] is False

        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "dataset.names_unreadable"
        assert store.updates == []


def test_publish_names_rewrites_only_the_differing_names(tmp_path: Path) -> None:
    meta = _save(tmp_path, _mapping_ir(price="税込価格"))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated"] == 1
        assert body["changes"][0]["design"] == "税込価格"

        assert len(store.updates) == 1  # 1 回の更新要求にまとめる
        update = store.updates[0]
        onto = substrate.ontology_graph_iri(meta["id"])
        names = f"GRAPH <{onto}> {{ <{_EX}price> <{_LABEL}>"
        assert f"DELETE WHERE {{ {names} ?l }} }}" in update
        assert f'INSERT DATA {{ {names} "税込価格" }} }}' in update
        # 名前が同じ用語・データの graph・graph ごとの作り直しには触れない。
        assert f"{_EX}shop" not in update and f"{_EX}Sample" not in update
        assert "DROP" not in update and "/graph/canonical/" not in update

        # 公開の状態は変わらない（取り込み直していない）。いつ名前を出したかだけ残す。
        after = registry.load_dataset(tmp_path / "registry", meta["id"])["meta"]
        assert after["promoted"] is True and after["ingested"] is False
        assert after["names_published_at"]

        # 名前を題に持つ型つきツールも登録し直す — 公開（promote）と同じ手順で、
        # 「ためす」の問いをもう一度走らせて作る。
        assert any("?s a ?class" in q for q in store.queries)


@pytest.mark.parametrize(
    ("name", "written"),
    [
        ('税込"価格', r'"税込\"価格"'),
        ("back\\slash", r'"back\\slash"'),
        ("2 行の\n名前", r'"2 行の\n名前"'),
        # rdflib の n3() が閉じの引用符を逃がし損ねる形（改行を含み、\\" で終わる）。
        ('x\n\\"', r'"x\n\\\""'),
        ("}; DROP ALL ;", '"}; DROP ALL ;"'),
    ],
)
def test_publish_names_writes_any_name_as_one_string(
    tmp_path: Path, name: str, written: str
) -> None:
    """名前は 1 つの文字列として書く。引用符・逆斜線・改行で更新の文を壊さない。"""
    meta = _save(tmp_path, _mapping_ir(price=name))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 200, r.text
        assert r.json()["changes"][0]["design"] == name
        (update,) = store.updates
        assert update.endswith(f"<{_EX}price> <{_LABEL}> {written} }} }}")
        assert update.count("DELETE WHERE") == 1 and update.count("INSERT DATA") == 1


def test_publish_names_covers_kind_names_too(tmp_path: Path) -> None:
    """種類の名前も同じ一覧に出る。項目が先、種類があと。"""
    meta = _save(tmp_path, _mapping_ir(price="税込価格", kind="値段の記録"))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        changes = client.get(f"/api/datasets/{meta['id']}/published-names").json()["changes"]
        assert [(c["kind"], c["published"], c["design"]) for c in changes] == [
            ("property", "売値", "税込価格"),
            ("class", "価格表 の 1 行", "値段の記録"),
        ]
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.json()["updated"] == 2
        (update,) = store.updates
        assert f'<{_EX}Sample> <{_LABEL}> "値段の記録"' in update


def test_published_names_with_several_published_names_for_one_term(tmp_path: Path) -> None:
    """公開側に名前が複数あるときは、設計と違うものを見せる（「A → A」と言わない）。"""
    meta = _save(tmp_path, _mapping_ir(price="税込価格"))
    _publish(tmp_path, meta["id"])
    store = _Store({**_PUBLISHED, f"{_EX}price": ["税込価格", "売値"]})
    with _started(tmp_path, store) as client:
        changes = client.get(f"/api/datasets/{meta['id']}/published-names").json()["changes"]
        assert changes == [
            {"iri": f"{_EX}price", "kind": "property", "published": "売値", "design": "税込価格"}
        ]


def test_publish_names_is_refused_while_retracted(tmp_path: Path) -> None:
    """公開をやめているあいだは、名前も出さない（もう一度公開してから）。"""
    meta = _save(tmp_path, _mapping_ir(price="税込価格"))
    _publish(tmp_path, meta["id"])
    root = tmp_path / "registry"
    registry.mark_retracted(root, meta["id"], retracted_at="2026-10-05T02:00:00+00:00")
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        body = client.get(f"/api/datasets/{meta['id']}/published-names").json()
        assert body["available"] is False
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 409, r.text
        assert store.updates == []

        registry.mark_reinstated(root, meta["id"], reinstated_at="2026-10-05T03:00:00+00:00")
        body = client.get(f"/api/datasets/{meta['id']}/published-names").json()
        assert body["available"] is True and len(body["changes"]) == 1


def test_publish_names_records_nothing_when_the_store_refuses(tmp_path: Path) -> None:
    """ストアが更新を断ったら、公開したことにしない。"""
    meta = _save(tmp_path, _mapping_ir(price="税込価格"))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        store.refuse_updates = True  # 起動時の補完は通し、名前の更新だけを断る
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 502, r.text
        assert r.json()["detail"]["code"] == "dataset.names_write_failed"
        assert len(store.updates) == 1
        after = registry.load_dataset(tmp_path / "registry", meta["id"])["meta"]
        assert "names_published_at" not in after


def test_publish_names_writes_nothing_when_nothing_differs(tmp_path: Path) -> None:
    meta = _save(tmp_path, _mapping_ir(price="売値"))
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store) as client:
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == 0
        assert store.updates == []
        after = registry.load_dataset(tmp_path / "registry", meta["id"])["meta"]
        assert "names_published_at" not in after


def test_publish_names_requires_the_write_token(tmp_path: Path) -> None:
    meta = _save(tmp_path, _mapping_ir())
    _publish(tmp_path, meta["id"])
    store = _Store(dict(_PUBLISHED))
    with _started(tmp_path, store, token=False) as client:  # 合言葉なし
        r = client.post(f"/api/datasets/{meta['id']}/publish-names")
        assert r.status_code in (401, 403), r.text
        assert store.updates == []
