"""Tests for ``register_dataset_summary`` (契約メモ contract_pr_f2.md §3.1/§3.4,
担当 api).

``main.py``'s ``build_app`` already wires this router (unlike the parallel-段
routers in sibling test files — this one lands after those, so the
integration line already exists), so these tests call plain ``build_app``
and hit the routes directly.

Backed by the same lightweight ``pyoxigraph``-in-a-fake-client shape
``test_cards_api.py`` uses (real SPARQL, no per-query body matching).
Fixture data is a fictional seed catalogue (§0: no materials-science domain
vocabulary).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
    dataset_iri,
    ontology_graph_iri,
)
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings

pyoxigraph = pytest.importorskip("pyoxigraph")

EX_SEED = "https://ex/seed#"
SEED_CLASS = EX_SEED + "Seed"
BATCH_CLASS = EX_SEED + "Batch"
VARIETY_PRED = EX_SEED + "variety"
MASS_PRED = EX_SEED + "massG"

SEED_DATASET = "seed-catalogue-cccc"
SEED_GRAPH = canonical_graph_iri(SEED_DATASET) + "/v1"

DRAFT_DATASET = "seed-catalogue-draft"

_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/seed#"
  exr: "https://ex/seed/resource/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: seed
    source: seeds.csv
    subject:
      template: "exr:seed/{code}"
      classes: [ex:Seed]
    properties:
      - predicate: ex:variety
        column: variety
        label: "品種"
      - predicate: ex:massG
        column: mass_g
        datatype: xsd:double
        unit: "g"
        label: "重さ"
"""

_MIE_YAML = """
schema_info:
  title: "種苗カタログ (見本)"
  license: "CC-BY-4.0"
"""

PROV = "http://www.w3.org/ns/prov#"
INGESTION_CLASS = EX_SEED + "IngestionActivity"

_SEED_TTL = f"""
@prefix ex: <{EX_SEED}> .
@prefix prov: <{PROV}> .

<https://ex/seed/resource/seed/1> a <{SEED_CLASS}> ; <{VARIETY_PRED}> "Alpha" .
<https://ex/seed/resource/seed/2> a <{SEED_CLASS}> ; <{VARIETY_PRED}> "Beta" .
<https://ex/seed/resource/batch/1> a <{BATCH_CLASS}> .
<https://ex/seed/resource/batch/2> a <{BATCH_CLASS}> .
<https://ex/seed/resource/batch/3> a <{BATCH_CLASS}> .
<https://ex/seed/activity/1> a prov:Activity .
<https://ex/seed/activity/2> a <{INGESTION_CLASS}>, prov:Activity .
"""

# データセットの ontology が「取り込み活動」を prov:Activity のサブクラスとして
# 宣言している場合 — その実例が classes に混ざらないことを確かめる（統合時の
# 所見 #1）。
_ONTOLOGY_TTL = f"""
@prefix ex: <{EX_SEED}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix prov: <{PROV}> .

ex:IngestionActivity rdfs:subClassOf prov:Activity .
"""


def _pyoxi_client(graphs: dict[str, str]):
    """Same shape as ``test_cards_api.py``'s helper of the same name — each
    test file in this codebase keeps its own copy (established convention,
    see ``asterism.subjects``'s own docstring notes on duplicated helpers)."""
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
    dest = registry_root / SEED_DATASET
    dest.mkdir(parents=True)
    meta = {
        "id": SEED_DATASET,
        "name": "種苗カタログ",
        "origin": "own",
        "promoted": True,
        "promoted_at": "2024-01-01",
        "version": 1,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "mapping.yaml").write_text(_MAPPING_YAML, encoding="utf-8")
    (dest / "mie.yaml").write_text(_MIE_YAML, encoding="utf-8")
    # dcterms:title (§3.1 の最優先の名前解決) — 実運用ではこのファイルは
    # materialize/promote が投影する。ここでは優先順位そのものを検証する
    # ため直接置く（meta.json の "name" とわざと違う値にする）。
    (dest / "metadata.ttl").write_text(
        "\n".join(
            [
                "@prefix dcterms: <http://purl.org/dc/terms/> .",
                f'<{dataset_iri(SEED_DATASET)}> dcterms:title "種苗カタログ (見本)" .',
            ]
        ),
        encoding="utf-8",
    )

    draft = registry_root / DRAFT_DATASET
    draft.mkdir(parents=True)
    draft_meta = {"id": DRAFT_DATASET, "name": "下書き中"}
    (draft / "meta.json").write_text(json.dumps(draft_meta), encoding="utf-8")


def _client(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    store_client = _pyoxi_client(
        {SEED_GRAPH: _SEED_TTL, ontology_graph_iri(SEED_DATASET): _ONTOLOGY_TTL}
    )
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


# ---------------------------------------------------------------------------
# GET /api/datasets/{dataset_id}/summary
# ---------------------------------------------------------------------------


def test_dataset_summary_shape_for_a_promoted_dataset(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get(f"/api/datasets/{SEED_DATASET}/summary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dataset_id"] == SEED_DATASET
        assert body["label"] == "種苗カタログ (見本)"  # metadata.ttl の dcterms:title 優先
        assert body["origin"] == "own"
        assert body["stage"] == "promoted"
        assert body["license"] == "CC-BY-4.0"
        assert body["snapshot"] == "v1"
        assert body["source_note"] is None
        assert body["is_demo"] is False

        classes = {c["class_iri"]: c for c in body["classes"]}
        # prov:Activity そのもの・データセットの ontology がそのサブクラスと
        # 宣言した「取り込み活動」は、どちらも「この中のもの」ではないので
        # classes に出ない（統合時の所見 #1）。
        assert set(classes) == {SEED_CLASS, BATCH_CLASS}
        seed = classes[SEED_CLASS]
        assert seed["count"] == 2
        assert seed["properties"] == 2
        assert seed["with_label"] == 2
        assert seed["with_unit"] == 1
        batch = classes[BATCH_CLASS]
        assert batch["count"] == 3
        # Batch はどのマッピング/オントロジーにも宣言が無い (class_schema は
        # None を返す) — それでも件数は数え、properties は 0 になる。
        assert batch["properties"] == 0
        assert batch["with_unit"] == 0


def test_dataset_summary_unknown_dataset_is_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/datasets/does-not-exist/summary")
        assert r.status_code == 404


def test_dataset_summary_malformed_dataset_id_is_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        for bad_id in ("Has-Upper-Case", "has/slash", "has space"):
            r = client.get(f"/api/datasets/{bad_id}/summary")
            assert r.status_code == 404, (bad_id, r.status_code)


def test_dataset_summary_non_promoted_dataset_has_no_classes(tmp_path: Path) -> None:
    # 版グラフがまだ引用可能スコープに無い(未 promote)ので classes は空。
    # stage で「未取り込み」が判定できる（空リストだけでは判別できない）。
    with _client(tmp_path) as client:
        r = client.get(f"/api/datasets/{DRAFT_DATASET}/summary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stage"] == "design"
        assert body["classes"] == []
        assert body["snapshot"] is None
        assert body["origin"] == "unknown"


# ---------------------------------------------------------------------------
# GET /api/datasets (§3.4: origin/is_demo/stage を足す)
# ---------------------------------------------------------------------------


def test_list_datasets_adds_origin_stage_is_demo_without_dropping_existing_fields(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/datasets")
        assert r.status_code == 200, r.text
        body = r.json()
        by_id = {d["id"]: d for d in body["datasets"]}
        assert set(by_id) == {SEED_DATASET, DRAFT_DATASET}

        seed = by_id[SEED_DATASET]
        assert seed["name"] == "種苗カタログ"  # 既存フィールドは残る
        assert seed["origin"] == "own"
        assert seed["stage"] == "promoted"
        assert seed["is_demo"] is False

        draft = by_id[DRAFT_DATASET]
        assert draft["origin"] == "unknown"
        assert draft["stage"] == "design"
        assert draft["is_demo"] is False


# ---------------------------------------------------------------------------
# GET /api/classes/schema (契約メモ §3.3: dataset_label を足す)
# ---------------------------------------------------------------------------


def test_classes_schema_includes_dataset_label(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/classes/schema", params={"class_iri": SEED_CLASS})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dataset_id"] == SEED_DATASET
        assert body["dataset_label"] == "種苗カタログ (見本)"
