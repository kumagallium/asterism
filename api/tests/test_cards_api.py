"""Tests for ``register_cards`` (object-cards-ui.md §3/§5 / 契約メモ §3.4, §5).

Per §0.1: this parallel-段 test calls ``build_app(settings, ...)`` then its
OWN ``register_cards(app, settings)`` before creating the ``TestClient`` —
main.py does not wire this router yet (that is the integrator's job).

Backed by a real ``pyoxigraph.Store`` (same contract as
``ingest/tests/test_subject_tools.py``'s ``_pyoxi_client``), wrapped in a
tiny fake that exposes only the async methods ``build_app``'s lifespan and
these routes actually call — real end-to-end SPARQL, no httpx body-matching
per query shape. Fixture data spans two unrelated fictional domains (§0).
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

from asterism_api import cards_routes
from asterism_api.cards_routes import register_cards
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str]):
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
            if isinstance(result, bool):  # ASK query (e.g. substrate.graph_has_triples)
                return {"head": {}, "boolean": result}
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


# ---------------------------------------------------------------------------
# Fixture data — a lending-library checkout log (unrelated to any materials
# science domain, per §0).
# ---------------------------------------------------------------------------

EX_LIB = "https://ex/library#"
CHECKOUT_CLASS = EX_LIB + "CheckoutRecord"
BORROWER_PRED = EX_LIB + "borrower"
BRANCH_PRED = EX_LIB + "branch"
OVERDUE_PRED = EX_LIB + "overdueDays"

LIB_DATASET = "library-checkouts"
LIB_GRAPH = canonical_graph_iri(LIB_DATASET) + "/v1"

CHECKOUT_1 = "https://ex/library/resource/checkout-1"
CHECKOUT_2 = "https://ex/library/resource/checkout-2"
BORROWER_A = "https://ex/library/resource/person-a"
NOWHERE = "https://ex/library/resource/does-not-exist"

_LIB_TTL = f"""
@prefix ex: <{EX_LIB}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{CHECKOUT_1}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout One" ;
    ex:borrower <{BORROWER_A}> ;
    ex:branch "north" ;
    ex:overdueDays "3"^^xsd:integer .

<{CHECKOUT_2}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout Two" ;
    ex:borrower <{BORROWER_A}> ;
    ex:branch "south" ;
    ex:overdueDays "7"^^xsd:integer .

<{BORROWER_A}> rdfs:label "Borrower A" .
"""

_LIB_TOOLS_YAML = """
tools:
  - name: overdue_days
    title: "延滞日数"
    output_kind: quantity
    parameters:
      - name: checkout
        type: iri
        required: true
    query: |
      SELECT ?v WHERE { BIND({{checkout}} AS ?s) ?s <https://ex/library#overdueDays> ?v } LIMIT 1
    result:
      item:
        value: {var: v, number: true, role: value}
"""


def _write_registry(registry_root: Path) -> None:
    dest = registry_root / LIB_DATASET
    dest.mkdir(parents=True)
    meta = {"id": LIB_DATASET, "name": "貸出記録", "promoted": True, "promoted_at": "2024-01-01"}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "query_tools.yaml").write_text(_LIB_TOOLS_YAML, encoding="utf-8")


def _client(tmp_path: Path, *, with_registry: bool = True) -> TestClient:
    settings = _settings(tmp_path)
    if with_registry:
        _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    return TestClient(app, headers=_AUTH)


# ---------------------------------------------------------------------------
# POST /api/cards/run
# ---------------------------------------------------------------------------


def test_cards_run_subject_facts(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_facts"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["output_kind"] == "facts"
        assert body["count"] >= 1
        # registry の meta.json に origin/license を書いていない（既定の
        # フィクスチャ）ので、出どころ・ライセンスとも「不明」側に倒れて
        # shareable は real な False になる（保守側・契約メモ §2）— None は
        # もう返らない。
        assert body["shareable"] is False
        assert body["shareable_reasons"] == ["unknown_origin", "unknown_license"]


def test_cards_run_shareable_true_when_all_materials_are_open_and_redistributable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "open")
    monkeypatch.setattr(
        "asterism.materials._lookup_license",
        lambda root, dataset_id: ("CC-BY-4.0", True),
    )
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_facts"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["shareable"] is True
        assert body["shareable_reasons"] == []
        assert body["materials"] == [
            {
                "kind": "open",
                "dataset_id": LIB_DATASET,
                "dataset_label": "貸出記録",
                "snapshot": "v1",
                "license": "CC-BY-4.0",
                "redistributable": True,
                "count": body["materials"][0]["count"],
            }
        ]


def test_cards_run_shareable_false_reasons_when_material_is_own_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "own")
    monkeypatch.setattr("asterism.materials._lookup_license", lambda root, dataset_id: None)
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_facts"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["shareable"] is False
        assert body["shareable_reasons"] == ["own_data", "unknown_license"]


def test_cards_run_subject_sources(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_sources"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["output_kind"] == "breakdown"
        assert body["items"][0]["category"] == "貸出記録 (v1)"


def test_cards_run_subject_flow_not_found_with_no_prov_edges(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_flow"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["found"] is False


def test_cards_run_subject_flow_materials_are_material_shaped(tmp_path: Path) -> None:
    """subject_flow の ``materials`` は他の built-in と同じ Material 形
    （dataset_label/kind/license/redistributable/count 込み）でなければなら
    ない — prov_graph の生の ``{dataset_id, snapshot, graph}`` のままだと ui
    の材料タブが flow カードだけ壊れる。"""
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_flow"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["materials"] == [
            {
                "kind": "unknown",
                "dataset_id": LIB_DATASET,
                "dataset_label": "貸出記録",
                "snapshot": "v1",
                "license": None,
                "redistributable": None,
                "count": body["materials"][0]["count"],
            }
        ]
        assert isinstance(body["shareable"], bool)
        assert isinstance(body["shareable_reasons"], list)


def test_cards_run_declared_tool_binds_the_iri(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={
                "subject": {"kind": "individual", "iri": CHECKOUT_1},
                "tool": f"{LIB_DATASET}/overdue_days",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["items"] == [{"value": 3.0}]


def test_cards_run_set_members(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={
                "subject": {"kind": "set", "spec": {"class": CHECKOUT_CLASS, "where": []}},
                "tool": "set_members",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 2


def test_cards_run_set_count(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={
                "subject": {
                    "kind": "set",
                    "spec": {
                        "class": CHECKOUT_CLASS,
                        "where": [{"property": BRANCH_PRED, "op": "eq", "value": "north"}],
                    },
                },
                "tool": "set_count",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["items"] == [{"value": 1}]


def test_cards_run_unknown_tool_is_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "no_such_tool"},
        )
        assert r.status_code == 404


def test_cards_run_kind_mismatch_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "set_members"},
        )
        assert r.status_code == 400


def test_cards_run_at_clause_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={
                "subject": {
                    "kind": "set",
                    "spec": {
                        "class": CHECKOUT_CLASS,
                        "where": [
                            {
                                "property": OVERDUE_PRED,
                                "op": "gt",
                                "value": 1,
                                "at": {"property": "x", "value": 1},
                            }
                        ],
                    },
                },
                "tool": "set_members",
            },
        )
        assert r.status_code == 400


def test_cards_run_malformed_subject_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run", json={"subject": {"kind": "nonsense"}, "tool": "subject_facts"}
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Unsafe (SPARQL-IRIREF-breaking) IRIs must be rejected with 400, never a
# raw store-syntax-error 500 (checker finding: a weak IRI check upstream let
# `"` / `}` reach a query built with a raw f-string ``<{iri}>``).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
def test_cards_run_subject_iri_with_unsafe_sparql_chars_is_400(
    tmp_path: Path, bad_iri: str
) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": bad_iri}, "tool": "subject_facts"},
        )
        assert r.status_code == 400, r.text


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
def test_cards_run_set_members_where_property_with_unsafe_chars_is_400(
    tmp_path: Path, bad_iri: str
) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={
                "subject": {
                    "kind": "set",
                    "spec": {
                        "class": CHECKOUT_CLASS,
                        "where": [{"property": bad_iri, "op": "eq", "value": "a"}],
                    },
                },
                "tool": "set_members",
            },
        )
        assert r.status_code == 400, r.text


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
def test_cards_run_set_breakdown_property_param_with_unsafe_chars_is_400(
    tmp_path: Path, bad_iri: str
) -> None:
    with _client(tmp_path) as client:
        r = client.post(
            "/api/cards/run",
            json={
                "subject": {"kind": "set", "spec": {"class": CHECKOUT_CLASS, "where": []}},
                "tool": "set_breakdown",
                "params": {"property": bad_iri},
            },
        )
        assert r.status_code == 400, r.text


def test_cards_run_is_unauthenticated(tmp_path: Path) -> None:
    """read-only — no token required, unlike the appdata write routes below."""
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app) as client:  # NO _AUTH headers
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "subject_facts"},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# GET /api/subjects/resolve / search
# ---------------------------------------------------------------------------


def test_subjects_resolve_found(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["label"] == "Checkout One"
        assert body["class_iri"] == CHECKOUT_CLASS
        assert body["dataset_id"] == LIB_DATASET
        assert body["snapshot"] == "v1"
        # §4: registry の meta.name（K4: ui は id を人に見せない）。
        assert body["dataset_label"] == "貸出記録"
        assert body["dataset_labels"] == ["貸出記録"]
        assert body["dataset_ids"] == [LIB_DATASET]
        # 契約メモ contract_pr_f16.md §1.4: ハブ関連なし（ingest の
        # hub_of_subject がまだ無い並列期間・並列期間の外でも通常の主語なら
        # 常にこの形）。
        assert body["is_hub"] is False
        assert body["hub"] is None
        assert body["hub_of"] is None


def test_subjects_resolve_not_found(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/resolve", params={"iri": NOWHERE})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is False
        assert body["dataset_label"] is None
        assert body["dataset_labels"] == []
        assert body["dataset_ids"] == []
        assert body["is_hub"] is False
        assert body["hub"] is None
        assert body["hub_of"] is None


def test_subjects_resolve_picks_dataset_with_more_triples_when_iri_is_shared(
    tmp_path: Path,
) -> None:
    # 実機所見の再現: 「置く」で棚とつながった 1 件は 2 つのデータセット
    # （棚のオープンデータ・自分が置いたデータ）の両方の版グラフに同じ IRI で
    # 載る。三つ組が最も多い方を主に、両方を dataset_labels/dataset_ids に
    # 出す（§resolve）。
    own_dataset = "own-shelf"
    own_graph = canonical_graph_iri(own_dataset) + "/v1"
    own_ttl = f"""
    @prefix ex: <{EX_LIB}> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

    <{CHECKOUT_1}> a <{CHECKOUT_CLASS}> ;
        rdfs:label "Checkout One" .
    """
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    own_dest = settings.registry_root / own_dataset
    own_dest.mkdir(parents=True)
    own_meta = {
        "id": own_dataset,
        "name": "自分の棚",
        "promoted": True,
        "promoted_at": "2024-01-02",
    }
    (own_dest / "meta.json").write_text(json.dumps(own_meta), encoding="utf-8")
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL, own_graph: own_ttl})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["dataset_id"] == LIB_DATASET  # 三つ組が多い方 (5 件 > 2 件)
        assert body["snapshot"] == "v1"
        assert body["dataset_label"] == "貸出記録"
        assert body["dataset_labels"] == ["貸出記録", "自分の棚"]
        assert body["dataset_ids"] == [LIB_DATASET, own_dataset]


def test_subjects_resolve_uses_schema_name_when_no_rdfs_label(tmp_path: Path) -> None:
    # 実機所見の再現: rdfs:label が無く schema:name だけの資源は、その値を
    # label として返す（IRI 末尾のローカル名に落ちない）。
    only_name_iri = "https://ex/library/resource/checkout-3"
    ttl = f"""
    @prefix ex: <{EX_LIB}> .
    <{only_name_iri}> a <{CHECKOUT_CLASS}> ;
        <http://schema.org/name> "Checkout Three" .
    """
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: ttl})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/subjects/resolve", params={"iri": only_name_iri})
        assert r.status_code == 200, r.text
        assert r.json()["label"] == "Checkout Three"


def test_subjects_resolve_bad_iri_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/resolve", params={"iri": "not-an-iri"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/subjects/resolve — ハブ／ハブを指す実体（契約メモ
# contract_pr_f16.md §1.4）。ingest 側の ``hub_of_subject`` はまだ無いので
# （§0 並列中の仮置き）テストでスタブ差し替えする。
# ---------------------------------------------------------------------------

PERSPECTIVE_ID = "shared-items"
HUB_IRI = "https://ex/shared/resource/hub-1"
OTHER_DATASET = "other-shelf"
OTHER_GRAPH = canonical_graph_iri(OTHER_DATASET) + "/v1"
OTHER_MEMBER = "https://ex/other/resource/item-1"

_OTHER_TTL = f"""
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<{OTHER_MEMBER}> rdfs:label "Other Item" .
"""


def _hub_graph_ttl(hub_iri: str, members: list[str]) -> str:
    links = "\n".join(f"<{m}> <{EX_LIB}linksTo> <{hub_iri}> ." for m in members)
    return links


def _hub_registry_meta(registry_root: Path, *, name: str = "共有たな") -> None:
    dest = registry_root / crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE_ID)
    dest.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE_ID),
        "name": name,
        "promoted": True,
        "promoted_at": "2024-01-03",
        "crosswalk_perspective_id": PERSPECTIVE_ID,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _hub_client(tmp_path: Path, *, members: list[str]) -> TestClient:
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    other_dest = settings.registry_root / OTHER_DATASET
    other_dest.mkdir(parents=True)
    other_meta = {
        "id": OTHER_DATASET,
        "name": "別のたな",
        "promoted": True,
        "promoted_at": "2024-01-02",
    }
    (other_dest / "meta.json").write_text(json.dumps(other_meta), encoding="utf-8")
    _hub_registry_meta(settings.registry_root)
    hub_graph = crosswalk_runtime.crosswalk_graph_iri(PERSPECTIVE_ID)
    store_client = _pyoxi_client(
        {
            LIB_GRAPH: _LIB_TTL,
            OTHER_GRAPH: _OTHER_TTL,
            hub_graph: _hub_graph_ttl(HUB_IRI, members),
        }
    )
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    return TestClient(app, headers=_AUTH)


def test_subjects_resolve_hub_subject_lists_members_from_both_datasets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_hub_of_subject(client, iri):
        if iri != HUB_IRI:
            return None
        return {
            "hub_iri": HUB_IRI,
            "graph": crosswalk_runtime.crosswalk_graph_iri(PERSPECTIVE_ID),
            "perspective_id": PERSPECTIVE_ID,
        }

    monkeypatch.setattr(
        cards_routes.subject_tools, "hub_of_subject", fake_hub_of_subject, raising=False
    )
    with _hub_client(tmp_path, members=[CHECKOUT_1, OTHER_MEMBER]) as client:
        r = client.get("/api/subjects/resolve", params={"iri": HUB_IRI})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["is_hub"] is True
        # §1.4: dataset_label は perspective の名前（registry meta の name）。
        assert body["dataset_label"] == "共有たな"
        assert body["hub"]["perspective_id"] == PERSPECTIVE_ID
        assert body["hub"]["name"] == "共有たな"
        member_iris = {m["iri"] for m in body["hub"]["members"]}
        assert member_iris == {CHECKOUT_1, OTHER_MEMBER}
        by_iri = {m["iri"]: m for m in body["hub"]["members"]}
        assert by_iri[CHECKOUT_1]["label"] == "Checkout One"
        assert by_iri[CHECKOUT_1]["dataset_id"] == LIB_DATASET
        assert by_iri[CHECKOUT_1]["dataset_label"] == "貸出記録"
        assert by_iri[OTHER_MEMBER]["label"] == "Other Item"
        assert by_iri[OTHER_MEMBER]["dataset_id"] == OTHER_DATASET
        assert by_iri[OTHER_MEMBER]["dataset_label"] == "別のたな"
        assert body["hub_of"] is None


def test_subjects_resolve_member_subject_gets_hub_of_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_hub_of_subject(client, iri):
        if iri != CHECKOUT_1:
            return None
        return {
            "hub_iri": HUB_IRI,
            "hub_label": "共有アイテム",
            "perspective_id": PERSPECTIVE_ID,
            "via_parent": None,
        }

    monkeypatch.setattr(
        cards_routes.subject_tools, "hub_of_subject", fake_hub_of_subject, raising=False
    )
    with _hub_client(tmp_path, members=[CHECKOUT_1, OTHER_MEMBER]) as client:
        r = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["is_hub"] is False
        assert body["hub"] is None
        hub_of = body["hub_of"]
        assert hub_of["iri"] == HUB_IRI
        assert hub_of["label"] == "共有アイテム"
        assert hub_of["perspective_name"] == "共有たな"
        assert hub_of["member_count"] == 2
        assert set(hub_of["dataset_labels"]) == {"貸出記録", "別のたな"}


def test_subjects_resolve_hub_of_is_none_when_hub_of_subject_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_hub_of_subject(client, iri):
        return None

    monkeypatch.setattr(
        cards_routes.subject_tools, "hub_of_subject", fake_hub_of_subject, raising=False
    )
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_hub"] is False
        assert body["hub"] is None
        assert body["hub_of"] is None


def test_subjects_resolve_hub_wiring_with_the_real_hub_of_subject(tmp_path: Path) -> None:
    """スタブなしの結線確認 — 実 ``asterism.subject_tools.hub_of_subject``
    が付いた実データで、is_hub 側（主語自身がハブ）と hub_of 側（メンバー
    → ハブの 1 段・親経由の 2 段）の両方がこのモジュール自身の実装で正しく
    引けること。

    ``CHECKOUT_1`` はハブへ直接リンク（1 段）。``CHECKOUT_2`` は
    ``CHECKOUT_1`` へリンクし、ハブへは ``CHECKOUT_1`` 経由（2 段）。
    """
    hub_ttl = f"""
    @prefix ex: <{EX_LIB}> .
    <{HUB_IRI}> a ex:SharedThing .
    <{CHECKOUT_1}> ex:linksTo <{HUB_IRI}> .
    <{OTHER_MEMBER}> ex:linksTo <{HUB_IRI}> .
    """
    lib_ttl = _LIB_TTL + f"\n<{CHECKOUT_2}> ex:derivedFrom <{CHECKOUT_1}> .\n"
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    other_dest = settings.registry_root / OTHER_DATASET
    other_dest.mkdir(parents=True)
    (other_dest / "meta.json").write_text(
        json.dumps({"id": OTHER_DATASET, "name": "別のたな", "promoted": True}),
        encoding="utf-8",
    )
    _hub_registry_meta(settings.registry_root)
    hub_graph = crosswalk_runtime.crosswalk_graph_iri(PERSPECTIVE_ID)
    store_client = _pyoxi_client({LIB_GRAPH: lib_ttl, OTHER_GRAPH: _OTHER_TTL, hub_graph: hub_ttl})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        hub_body = client.get("/api/subjects/resolve", params={"iri": HUB_IRI}).json()
        assert hub_body["is_hub"] is True
        assert hub_body["hub"]["perspective_id"] == PERSPECTIVE_ID
        assert {m["iri"] for m in hub_body["hub"]["members"]} == {CHECKOUT_1, OTHER_MEMBER}

        # hub_of・1 段（メンバーが直接ハブを指す）。
        one_hop = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_1}).json()
        assert one_hop["is_hub"] is False
        assert one_hop["hub_of"]["iri"] == HUB_IRI
        assert one_hop["hub_of"]["perspective_name"] == "共有たな"
        assert one_hop["hub_of"]["member_count"] == 2
        assert set(one_hop["hub_of"]["dataset_labels"]) == {"貸出記録", "別のたな"}

        # hub_of・親経由の 2 段（CHECKOUT_2 → CHECKOUT_1 → ハブ）。
        two_hop = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_2}).json()
        assert two_hop["is_hub"] is False
        assert two_hop["hub_of"]["iri"] == HUB_IRI
        assert two_hop["hub_of"]["member_count"] == 2


# ---------------------------------------------------------------------------
# GET /api/subjects/linking-kinds（契約メモ contract_pr_f4.md §1-3）— この
# ファイルは「呼ぶだけ」（担当 tool の asterism.subject_tools.linking_kinds を
# そのまま呼んで {"kinds": ...} に包む）なので、ここでは配線だけを固定する:
# 呼ばれる・iri を渡す・戻り値をそのまま運ぶ・不正な iri は 400。
# ---------------------------------------------------------------------------


def test_subjects_linking_kinds_calls_the_tool_and_wraps_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, str]] = []

    async def fake_linking_kinds(client, iri, *, registry_root=None):
        calls.append((client, iri))
        return [{"class": CHECKOUT_CLASS, "property": BORROWER_PRED}]

    monkeypatch.setattr(cards_routes.subject_tools, "linking_kinds", fake_linking_kinds)
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/linking-kinds", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        assert r.json() == {"kinds": [{"class": CHECKOUT_CLASS, "property": BORROWER_PRED}]}
        assert calls and calls[0][1] == CHECKOUT_1


def test_subjects_linking_kinds_bad_iri_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/linking-kinds", params={"iri": "not-an-iri"})
        assert r.status_code == 400


def test_subjects_linking_kinds_passes_through_the_neighborhood_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契約メモ contract_pr_f14.md §1.1 — このルートは ``linking_kinds`` の
    戻り値をそのまま運ぶだけなので、近傍の新フィールド（``hops``・
    ``path_kind``・``anchor_*``・``via``・``where``）も無加工で通ることを
    確かめる（この module 自身は新フィールドを一切知らない）。"""
    sibling_row = {
        "class_iri": CHECKOUT_CLASS,
        "class_label": "貸出",
        "property": BORROWER_PRED,
        "property_label": "借り手",
        "count": 2,
        "hops": 2,
        "path_kind": "sibling",
        "anchor_iri": "https://ex/lib/resource/borrower-1",
        "anchor_label": "利用者 1",
        "anchor_class_label": "利用者",
        "anchor_property": BORROWER_PRED,
        "anchor_property_label": "借り手",
        "via": None,
        "where": [{"property": BORROWER_PRED, "iri": "https://ex/lib/resource/borrower-1"}],
    }

    async def fake_linking_kinds(client, iri, *, registry_root=None):
        return [sibling_row]

    monkeypatch.setattr(cards_routes.subject_tools, "linking_kinds", fake_linking_kinds)
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/linking-kinds", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        assert r.json() == {"kinds": [sibling_row]}


def test_subjects_resolve_store_syntax_error_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """checker finding: ``_store_query_syntax_error_to_400`` was only wired to
    ``cards_run`` — every other read route (here: ``subjects_resolve``) could
    leak a store-rejected-query ``SyntaxError`` as an opaque 500."""

    async def boom(client, iri):
        raise SyntaxError("bad query")

    monkeypatch.setattr(cards_routes.describe, "fetch_description", boom)
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/resolve", params={"iri": CHECKOUT_1})
        assert r.status_code == 400, r.text


def test_sets_resolve_store_syntax_error_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(client, registry_root, class_iri):
        raise SyntaxError("bad query")

    monkeypatch.setattr(cards_routes.class_schema_mod, "class_label", boom)
    with _client(tmp_path) as client:
        r = client.post("/api/sets/resolve", json={"spec": {"class": CHECKOUT_CLASS, "where": []}})
        assert r.status_code == 400, r.text


def test_subjects_search_matches_label(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"q": "checkout"})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert {i["iri"] for i in items} == {CHECKOUT_1, CHECKOUT_2}
        assert all(i["dataset_id"] == LIB_DATASET for i in items)


def test_subjects_search_empty_query_returns_empty(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"q": "   "})
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/subjects/search?dataset_id=... (契約メモ contract_pr_f2.md §3.2)
# ---------------------------------------------------------------------------


def test_subjects_search_dataset_id_matching_dataset_still_finds_results(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"q": "checkout", "dataset_id": LIB_DATASET})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert {i["iri"] for i in items} == {CHECKOUT_1, CHECKOUT_2}


def test_subjects_search_dataset_id_scoped_to_a_different_dataset_finds_nothing(
    tmp_path: Path,
) -> None:
    # 形は正しいが、この checkout データはそのデータセットの版グラフに無い
    # （§3.2: dataset_id 指定時は FROM をそのデータセットに限定する）。
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search",
            params={"q": "checkout", "dataset_id": "some-other-dataset"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0


def test_subjects_search_malformed_dataset_id_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search",
            params={"q": "checkout", "dataset_id": "Not/A-Valid Id"},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/subjects/search?class_iri=... (契約メモ contract_pr_f9.md §2.2)
# ---------------------------------------------------------------------------


def test_subjects_search_class_iri_scopes_to_that_class(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search", params={"q": "checkout", "class_iri": CHECKOUT_CLASS}
        )
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert {i["iri"] for i in items} == {CHECKOUT_1, CHECKOUT_2}


def test_subjects_search_class_iri_excludes_other_classes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search",
            params={"q": "borrower", "class_iri": CHECKOUT_CLASS},
        )
        assert r.status_code == 200, r.text
        # "Borrower A" (rdfs:label に "borrower" を含む) は CHECKOUT_CLASS の
        # 実例ではない（型を持たない）ので、class_iri の限定で出ない。
        assert r.json()["items"] == []


def test_subjects_search_empty_q_with_class_iri_lists_first_limit_by_name(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"class_iri": CHECKOUT_CLASS})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        # 名前順: "Checkout One" < "Checkout Two"。
        assert [i["iri"] for i in items] == [CHECKOUT_1, CHECKOUT_2]


def test_subjects_search_empty_q_with_class_iri_respects_limit(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"class_iri": CHECKOUT_CLASS, "limit": 1})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [i["iri"] for i in items] == [CHECKOUT_1]


def test_subjects_search_empty_q_without_class_iri_is_still_empty(tmp_path: Path) -> None:
    # class_iri が無い空 q は、契約が「その種類の一覧」を保証しない従来どおり
    # の空振り。
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0


def test_subjects_search_empty_q_offset_returns_continuation(tmp_path: Path) -> None:
    # 契約メモ contract_pr_f10.md §2: offset で「もっと見る」の続きが取れる。
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search",
            params={"class_iri": CHECKOUT_CLASS, "limit": 1, "offset": 1},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert [i["iri"] for i in body["items"]] == [CHECKOUT_2]
        assert body["total"] == 2
        assert body["offset"] == 1
        assert body["limit"] == 1


def test_subjects_search_empty_q_total_independent_of_limit(tmp_path: Path) -> None:
    # total は limit に依らず全体の件数（ここでは 2 件とも見出しを持つ）。
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"class_iri": CHECKOUT_CLASS, "limit": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 1
        assert body["total"] == 2


def test_subjects_search_query_total_independent_of_limit(tmp_path: Path) -> None:
    # 検索中も total は limit に依らず件数を数える(COUNT(DISTINCT ?s))。
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"q": "checkout", "limit": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 1
        assert body["total"] == 2


def test_subjects_search_query_offset_returns_continuation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"q": "checkout", "limit": 1, "offset": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 1
        assert body["total"] == 2
        assert body["offset"] == 1


def _widget_ttl(count: int) -> str:
    # 検索対象の主語が「1 主語につき複数のラベル述語が一致する」データ
    # （下のバグ再現テスト参照）。ライブラリ貸出記録とは無関係な架空ドメイン
    # （§0）。
    lines = [
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix schema: <http://schema.org/> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
    ]
    for i in range(count):
        iri = f"https://ex/widgets/resource/widget-{i:04d}"
        label = f"Widget {i:04d}"
        lines.append(
            f'<{iri}> rdfs:label "{label}" ; schema:name "{label}" ; dcterms:title "{label}" .'
        )
    return "\n".join(lines)


def test_subjects_search_query_offset_beyond_overfetch_cap_still_returns_items(
    tmp_path: Path,
) -> None:
    # 契約メモ contract_pr_f10.md §2 のバグ修正（レビュー指摘）: 検索中の
    # over-fetch 内部上限（raw_limit）が offset に依らず固定 400 行だと、
    # 1 主語に複数のラベル述語が一致するデータで offset が進んだとき
    # （「もっと見る」を繰り返す）に 400 行へすぐ頭打ちし、total は正確な
    # まま items だけ増えなくなる（もっと見るボタンが効かなくなる）。
    # 250 件・各 3 つのラベル述語が一致する主語を用意し、offset=120/
    # limit=60（offset+limit=180 → 旧コードの (180)*4=720 は 400 に丸めら
    # れ、400 行 ÷ 3 行/主語 ≈ 133 主語しか読めず 180 に届かない）で再現する。
    dataset_id = "widget-catalog"
    graph = canonical_graph_iri(dataset_id) + "/v1"
    count = 250
    store_client = _pyoxi_client({graph: _widget_ttl(count)})
    settings = _settings(tmp_path)
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(
            "/api/subjects/search",
            params={"q": "widget", "limit": 60, "offset": 120},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == count
        assert len(body["items"]) == 60
        assert body.get("total_is_lower_bound") is None


def test_subjects_search_response_keeps_existing_item_shape(tmp_path: Path) -> None:
    # 既存の items の形（iri/label/class_iri/class_label/dataset_id）は不変。
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/search", params={"q": "checkout"})
        assert r.status_code == 200, r.text
        item = r.json()["items"][0]
        assert set(item.keys()) == {"iri", "label", "class_iri", "class_label", "dataset_id"}


def test_subjects_search_malformed_class_iri_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search",
            params={"q": "checkout", "class_iri": "not an iri"},
        )
        assert r.status_code == 400


def test_subjects_search_class_iri_and_dataset_id_can_combine(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get(
            "/api/subjects/search",
            params={
                "q": "checkout",
                "class_iri": CHECKOUT_CLASS,
                "dataset_id": LIB_DATASET,
            },
        )
        assert r.status_code == 200, r.text
        assert {i["iri"] for i in r.json()["items"]} == {CHECKOUT_1, CHECKOUT_2}


# ---------------------------------------------------------------------------
# GET /api/subjects/default-cards / GET /api/sets/default-cards
# ---------------------------------------------------------------------------


def test_subjects_default_cards_is_a_bare_list(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/default-cards", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        assert [c["tool"] for c in body][:2] == ["subject_facts", "subject_sources"]


def test_sets_default_cards_is_a_bare_list(tmp_path: Path) -> None:
    spec = json.dumps({"class": CHECKOUT_CLASS, "where": []})
    with _client(tmp_path) as client:
        r = client.get("/api/sets/default-cards", params={"spec": spec})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [c["tool"] for c in body] == ["set_members", "set_count"]


def _client_with_declared_tool_class_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """A client whose ``_load_class_schema`` seam is patched to report
    ``overdue_days`` as ``CHECKOUT_CLASS``'s one declared, iri-bound tool —
    same fixture-driven fake ``ingest/tests/test_subject_tools.py`` uses,
    isolating the dispatch contract from ``class_schema``'s own (parallel-
    authored) mapping.yaml-matching logic."""
    import asterism.subject_tools as subject_tools_mod
    from asterism.query_tools import load_query_tools

    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    tools = load_query_tools(LIB_DATASET, root=settings.registry_root)
    raw_tools = [
        {
            "name": t.name,
            "title": t.title,
            "output_kind": t.output_kind,
            "parameters": [{"name": p.name, "type": p.type} for p in t.params],
        }
        for t in tools
    ]

    async def _fake_class_schema(client, registry_root, class_iri):
        return {"class_iri": class_iri, "dataset_id": LIB_DATASET, "tools": raw_tools}

    monkeypatch.setattr(subject_tools_mod, "_load_class_schema", lambda: _fake_class_schema)

    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    return TestClient(app, headers=_AUTH)


def test_subjects_default_cards_tool_name_works_as_is_in_cards_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """default-cards が返す ``tool`` は、加工なしで cards/run にそのまま
    渡せる（§ dispatch contract）— 宣言ツールのカードは dataset_id 接頭つき
    で返り、その文字列がそのまま cards/run を通る。"""
    with _client_with_declared_tool_class_schema(tmp_path, monkeypatch) as client:
        r = client.get("/api/subjects/default-cards", params={"iri": CHECKOUT_1})
        assert r.status_code == 200, r.text
        cards = r.json()
        declared = next(
            c
            for c in cards
            if c["tool"] not in {"subject_facts", "subject_sources", "subject_flow"}
        )
        assert declared["tool"] == f"{LIB_DATASET}/overdue_days"

        run = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": declared["tool"]},
        )
        assert run.status_code == 200, run.text
        assert run.json()["items"] == [{"value": 3.0}]


def test_cards_run_resolves_bare_declared_tool_name_for_back_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接頭 (``dataset_id/``) の付かない宣言ツール名でも、その主語自身の
    クラスから解決できれば通る（§ dispatch back-compat）。"""
    with _client_with_declared_tool_class_schema(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/cards/run",
            json={"subject": {"kind": "individual", "iri": CHECKOUT_1}, "tool": "overdue_days"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["items"] == [{"value": 3.0}]


def test_sets_default_cards_bad_json_is_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/sets/default-cards", params={"spec": "{not json"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/sets/resolve
# ---------------------------------------------------------------------------


def test_sets_resolve_returns_set_id_and_title(tmp_path: Path) -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": BRANCH_PRED, "op": "eq", "value": "north"}],
    }
    with _client(tmp_path) as client:
        r = client.post("/api/sets/resolve", json={"spec": spec})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["set_id"].startswith("set-")
        # No registry mapping.yaml declares this class, so class_schema finds
        # no properties here; property_label falls back to the local name
        # (K4 — never the raw IRI).
        assert body["title"]["clauses"] == [
            {"property_label": "branch", "op": "eq", "value": "north", "unit": None}
        ]
        # 契約メモ contract_pr_f2.md §3.3: 既存フィールドは削らず dataset_label
        # を足す。この fixture の CHECKOUT_CLASS はどの mapping.yaml にも
        # 宣言が無いので class_schema は所有データセットを見つけられず null
        # （値そのものの解決は ingest/tests/test_subjects.py が担う）。
        assert body["dataset_label"] is None


def test_sets_resolve_class_label_uses_model_yaml_over_ontology_local_name(
    tmp_path: Path,
) -> None:
    """実機所見: 絞り込みページの見出しが「Country」（オントロジー投影の
    rdfs:label はローカル名）になっていた。``class_schema_mod.class_label``
    は既に呼ばれているが、それが最優先で読む ``model.yaml`` の
    ``classes.<curie>.label`` を宣言した種類なら、その日本語ラベルを返す
    （物の domain もどちらでもよい — ここでは stall だけの架空データ、§0）。
    """
    stall_class = "https://ex/market#Stall"
    dataset_id = "market-fair"
    settings = _settings(tmp_path)
    dest = settings.registry_root / dataset_id
    dest.mkdir(parents=True)
    meta = {"id": dataset_id, "name": "青空市", "promoted": True, "promoted_at": "2024-01-01"}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "model.yaml").write_text(
        f'classes:\n  "{stall_class}":\n    label: "屋台"\n', encoding="utf-8"
    )
    store_client = _pyoxi_client({})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/sets/resolve", json={"spec": {"class": stall_class, "where": []}})
        assert r.status_code == 200, r.text
        assert r.json()["title"]["class_label"] == "屋台"


def test_sets_resolve_at_clause_is_400(tmp_path: Path) -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [
            {
                "property": BRANCH_PRED,
                "op": "eq",
                "value": "x",
                "at": {"property": "y", "value": 1},
            }
        ],
    }
    with _client(tmp_path) as client:
        r = client.post("/api/sets/resolve", json={"spec": spec})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# §5 — appdata subjects
# ---------------------------------------------------------------------------


def _single_user_settings(tmp_path: Path) -> object:
    s = _settings(tmp_path)
    s.single_user = True
    s.appdata_root = tmp_path / "appdata"
    return s


def _appdata_client(tmp_path: Path, *, single_user: bool = True) -> TestClient:
    settings = _single_user_settings(tmp_path) if single_user else _settings(tmp_path)
    store_client = _pyoxi_client({})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    return TestClient(app, headers=_AUTH)


def test_appdata_subjects_404_without_single_user(tmp_path: Path) -> None:
    with _appdata_client(tmp_path, single_user=False) as client:
        assert client.get("/api/appdata/subjects").status_code == 404
        sid = "11111111-1111-4111-8111-111111111111"
        assert client.put(f"/api/appdata/subjects/{sid}", json={}).status_code == 404
        assert client.delete(f"/api/appdata/subjects/{sid}").status_code == 404


def test_appdata_subjects_round_trip(tmp_path: Path) -> None:
    with _appdata_client(tmp_path) as client:
        sid = "22222222-2222-4222-8222-222222222222"
        payload = {"kind": "individual", "id": CHECKOUT_1, "label": "Checkout One", "source": "own"}
        r = client.put(f"/api/appdata/subjects/{sid}", json=payload)
        assert r.status_code == 200, r.text

        r = client.get("/api/appdata/subjects")
        assert r.status_code == 200
        assert r.json()["subjects"] == [payload]

        r = client.delete(f"/api/appdata/subjects/{sid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        assert client.get("/api/appdata/subjects").json()["subjects"] == []


def test_appdata_subjects_put_requires_write_auth(tmp_path: Path) -> None:
    settings = _single_user_settings(tmp_path)
    store_client = _pyoxi_client({})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_cards(app, settings)
    with TestClient(app) as client:  # NO auth headers
        sid = "33333333-3333-4333-8333-333333333333"
        r = client.put(f"/api/appdata/subjects/{sid}", json={"id": "x"})
        assert r.status_code == 401


def test_appdata_subjects_invalid_id_is_400(tmp_path: Path) -> None:
    with _appdata_client(tmp_path) as client:
        r = client.put("/api/appdata/subjects/not-a-uuid", json={"id": "x"})
        assert r.status_code == 400
