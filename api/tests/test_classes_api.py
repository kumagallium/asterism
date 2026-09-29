"""Tests for ``register_classes`` (契約メモ contract_pr_f9.md §2.1, 担当 api).

``main.py``'s ``build_app`` already wires this router (unlike the
parallel-段 routers other sibling test files register by hand), so these
tests call plain ``build_app`` and hit the route directly — same shape as
``test_dataset_summary_api.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asterism import crosswalk_runtime
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings

pyoxigraph = pytest.importorskip("pyoxigraph")

EX_LIB = "https://ex/library#"
BOOK_CLASS = EX_LIB + "Book"
LIB_DATASET = "library-catalogue-dddd"
LIB_GRAPH = canonical_graph_iri(LIB_DATASET) + "/v1"
_LIB_TTL = f"""
@prefix ex: <{EX_LIB}> .

<https://ex/library/book/1> a ex:Book .
<https://ex/library/book/2> a ex:Book .
"""

DRAFT_DATASET = "draft-only-eeee"


def _pyoxi_client(graphs: dict[str, str]):
    """Same shape as ``test_dataset_summary_api.py``'s helper of the same
    name (established convention: each test file keeps its own copy)."""
    store = pyoxigraph.Store()
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
        if giri.startswith(CANONICAL_GRAPH_BASE):
            store.add(
                pyoxigraph.Quad(
                    pyoxigraph.NamedNode(giri),
                    pyoxigraph.NamedNode(STATUS_PREDICATE),
                    pyoxigraph.Literal(STATUS_PROMOTED),
                    pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
                )
            )

    class _C:
        async def sparql_select(self, query: str) -> dict:
            result = store.query(query)
            names = [v.value for v in result.variables]
            bindings = []
            for solution in result:
                row = {}
                for name in names:
                    term = solution[name]
                    if term is None:
                        continue
                    if isinstance(term, pyoxigraph.NamedNode):
                        row[name] = {"type": "uri", "value": term.value}
                    elif isinstance(term, pyoxigraph.Literal):
                        cell = {"type": "literal", "value": term.value}
                        if term.language:
                            cell["xml:lang"] = term.language
                        elif term.datatype:
                            cell["datatype"] = term.datatype.value
                        row[name] = cell
                bindings.append(row)
            return {"results": {"bindings": bindings}}

        async def sparql_update(self, update: str) -> None:
            store.update(update)

        async def ping(self) -> bool:
            return True

        async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
            return 0

        async def aclose(self) -> None:
            return None

    return _C()


def _write_registry(registry_root: Path) -> None:
    dest = registry_root / LIB_DATASET
    dest.mkdir(parents=True)
    meta = {
        "id": LIB_DATASET,
        "name": "貸し出し目録",
        "origin": "own",
        "promoted": True,
        "promoted_at": "2024-01-01",
        "version": 1,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    draft = registry_root / DRAFT_DATASET
    draft.mkdir(parents=True)
    (draft / "meta.json").write_text(
        json.dumps({"id": DRAFT_DATASET, "name": "下書き中"}), encoding="utf-8"
    )


def _client(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def test_classes_json_shape(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/classes")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"classes"}
        by_iri = {c["class_iri"]: c for c in body["classes"]}
        assert set(by_iri) == {BOOK_CLASS}
        book = by_iri[BOOK_CLASS]
        assert book["count"] == 2
        assert book["dataset_id"] == LIB_DATASET
        assert book["dataset_label"] == "貸し出し目録"
        assert book["is_demo"] is False
        assert set(book) == {
            "class_iri",
            "label",
            "count",
            "dataset_id",
            "dataset_label",
            "is_demo",
            "properties",
            "with_label",
            "with_unit",
        }
        # 未 promote のデータセットは混ざらない。
        assert all(c["dataset_id"] != DRAFT_DATASET for c in body["classes"])


# ---------------------------------------------------------------------------
# ハブの行の表示名（契約メモ contract_b_hub_names.md）— dataset_label（R1）・
# label（R3）は、ハブ graph の生の rdfs:label（実装の語 "(crosswalk)" が付いた
# まま出ていたバグ）ではなく、読むたびに参加している項目の表示名から組み立て
# た値を使う。
# ---------------------------------------------------------------------------

HUB_PERSPECTIVE_ID = "shelf-view"
HUB_DATASET_ID = crosswalk_runtime.crosswalk_registry_id(HUB_PERSPECTIVE_ID)
HUB_GRAPH = crosswalk_runtime.crosswalk_graph_iri(HUB_PERSPECTIVE_ID)
SHELF_CLASS = "https://ex/shelf#Item"
_HUB_TTL = f"""
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<{SHELF_CLASS}> a owl:Class ; rdfs:label "shelf_item_name (crosswalk)" .
<https://ex/shelf/item/1> a <{SHELF_CLASS}> .
"""


def _write_hub(registry_root: Path, *, meta_name: str) -> None:
    dest = registry_root / HUB_DATASET_ID
    dest.mkdir(parents=True)
    meta = {
        "id": HUB_DATASET_ID,
        "name": meta_name,
        "promoted": True,
        "crosswalk_perspective_id": HUB_PERSPECTIVE_ID,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    config = crosswalk_runtime.parse_config(
        {
            "concepts": [
                {
                    "name": "shelf_item_name",
                    "class_iri": SHELF_CLASS,
                    "participants": [
                        {"dataset_id": LIB_DATASET, "predicate": "https://ex/library#name"}
                    ],
                }
            ]
        }
    )
    crosswalk_runtime.save_config(registry_root, config, HUB_PERSPECTIVE_ID)


def test_classes_hub_row_falls_back_to_concept_key_when_name_is_machine_made(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    # meta の name が concept のキーそのもの — R1 の「人が付けた名前ではない」
    # 判定に当たる（機械付け）。
    _write_hub(settings.registry_root, meta_name="shelf_item_name")
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL, HUB_GRAPH: _HUB_TTL})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/classes")
        assert r.status_code == 200, r.text
        by_iri = {c["class_iri"]: c for c in r.json()["classes"]}
        hub = by_iri[SHELF_CLASS]
        assert hub["is_hub"] is True
        # 参加データセットに設計（mapping.yaml）が無いので R2 は項目の表示名を
        # 引けず、概念のキーを人向けに直したものに落ちる。
        assert hub["dataset_label"] == "shelf item name"
        # ttl の rdfs:label（"(crosswalk)" 付き）ではなく R2 の結果を使う。
        assert hub["label"] == "shelf item name"


def test_classes_hub_row_keeps_a_human_given_perspective_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    _write_hub(settings.registry_root, meta_name="たなの品名")
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL, HUB_GRAPH: _HUB_TTL})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/classes")
        assert r.status_code == 200, r.text
        by_iri = {c["class_iri"]: c for c in r.json()["classes"]}
        hub = by_iri[SHELF_CLASS]
        # 人が付けた名前はそのまま（R1 の 1）。
        assert hub["dataset_label"] == "たなの品名"
        # 種類の表示名（R3）は concept 由来のまま — perspective の名前とは別軸。
        assert hub["label"] == "shelf item name"


def test_classes_empty_when_no_datasets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store_client = _pyoxi_client({})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/classes")
        assert r.status_code == 200, r.text
        assert r.json() == {"classes": []}
