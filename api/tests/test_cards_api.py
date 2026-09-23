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


def test_subjects_resolve_not_found(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/api/subjects/resolve", params={"iri": NOWHERE})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is False
        assert body["dataset_label"] is None
        assert body["dataset_labels"] == []
        assert body["dataset_ids"] == []


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
        assert r.json() == {"items": []}


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
