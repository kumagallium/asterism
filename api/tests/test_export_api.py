"""Tests for ``register_export`` / ``POST /api/subjects/export`` (契約メモ
contract_pr_d.md §3 — D1-export).

Per §0.1: this parallel-段 test calls ``build_app(settings, ...)`` then its
OWN ``register_export(app, settings)`` before creating the ``TestClient`` —
main.py does not wire this router yet (that is the integrator's job).

Backed by a real ``pyoxigraph.Store`` (same contract as
``test_cards_api.py``'s ``_pyoxi_client``), with the part5 control-graph
triples set up explicitly (``promoted`` + ``liveGraph``) so the exported
bundle's ``facts/control.trig`` has a real pointer. Fixture data spans two
unrelated fictional domains (library checkouts / a field log) — no
materials-science noun anywhere (§0).
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from asterism.substrate import (
    CONTROL_GRAPH_IRI,
    LIVE_GRAPH_PREDICATE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)
from fastapi.testclient import TestClient

from asterism_api.export_routes import register_export
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str], *, promote: dict[str, str]):
    store = pyoxigraph.Store()
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
    for dataset_id, version_graph in promote.items():
        key = canonical_graph_iri(dataset_id)
        store.add(
            pyoxigraph.Quad(
                pyoxigraph.NamedNode(key),
                pyoxigraph.NamedNode(STATUS_PREDICATE),
                pyoxigraph.Literal(STATUS_PROMOTED),
                pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
            )
        )
        store.add(
            pyoxigraph.Quad(
                pyoxigraph.NamedNode(key),
                pyoxigraph.NamedNode(LIVE_GRAPH_PREDICATE),
                pyoxigraph.NamedNode(version_graph),
                pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
            )
        )

    class _C:
        async def sparql_select(self, query: str) -> dict:
            result = store.query(query)
            if isinstance(result, bool):
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
# Fixture data — a lending-library checkout log (§0: unrelated to any
# materials-science domain).
# ---------------------------------------------------------------------------

EX_LIB = "https://ex/library#"
CHECKOUT_CLASS = EX_LIB + "CheckoutRecord"
BORROWER_PRED = EX_LIB + "borrower"

LIB_DATASET = "library-checkouts"
LIB_GRAPH = canonical_graph_iri(LIB_DATASET) + "/v1"

CHECKOUT_1 = "https://ex/library/resource/checkout-1"
BORROWER_A = "https://ex/library/resource/person-a"

_LIB_TTL = f"""
@prefix ex: <{EX_LIB}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<{CHECKOUT_1}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout One" ;
    ex:borrower <{BORROWER_A}> .

<{BORROWER_A}> rdfs:label "Borrower A" .
"""


def _write_registry(registry_root: Path) -> None:
    dest = registry_root / LIB_DATASET
    dest.mkdir(parents=True)
    meta = {"id": LIB_DATASET, "name": "Library", "promoted": True, "classes": [CHECKOUT_CLASS]}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _client_app(tmp_path: Path, *, with_registry: bool = True) -> TestClient:
    settings = _settings(tmp_path)
    if with_registry:
        _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL}, promote={LIB_DATASET: LIB_GRAPH})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_export(app, settings)
    return TestClient(app, headers=_AUTH)


_CARD = {"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}


def _export_body(**overrides) -> dict:
    body = {
        "subject": {"kind": "individual", "iri": CHECKOUT_1},
        "cards": [_CARD],
        "share": "full",
        "lang": "ja",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_export_requires_write_auth(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL}, promote={LIB_DATASET: LIB_GRAPH})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_export(app, settings)
    with TestClient(app) as client:  # no _AUTH header
        r = client.post("/api/subjects/export", json=_export_body())
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# happy path — zip returned, header + contents
# ---------------------------------------------------------------------------


def test_export_returns_a_zip_with_the_required_files(tmp_path: Path) -> None:
    with _client_app(tmp_path) as client:
        r = client.post("/api/subjects/export", json=_export_body())
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert "attachment" in r.headers["content-disposition"]
        assert r.headers["content-disposition"].endswith('-agent.zip"')
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            names = set(zf.namelist())
        top = sorted(names)[0].split("/")[0]
        for required in (
            "AGENT.md",
            "mcp.json",
            "README.md",
            "materials.json",
            "facts/facts.trig",
            "facts/control.trig",
            "cards/card-1.json",
        ):
            assert f"{top}/{required}" in names, names


# ---------------------------------------------------------------------------
# validation -> 400
# ---------------------------------------------------------------------------


def test_export_bad_subject_is_400(tmp_path: Path) -> None:
    with _client_app(tmp_path) as client:
        r = client.post("/api/subjects/export", json=_export_body(subject={"kind": "bogus"}))
        assert r.status_code == 400


def test_export_empty_cards_is_400(tmp_path: Path) -> None:
    with _client_app(tmp_path) as client:
        r = client.post("/api/subjects/export", json=_export_body(cards=[]))
        assert r.status_code == 400


def test_export_bad_share_is_400(tmp_path: Path) -> None:
    with _client_app(tmp_path) as client:
        r = client.post("/api/subjects/export", json=_export_body(share="everyone"))
        assert r.status_code == 400


def test_export_unknown_tool_is_404(tmp_path: Path) -> None:
    with _client_app(tmp_path) as client:
        body = _export_body(cards=[{"card_id": "c", "tool": "no_such_tool", "params": {}}])
        r = client.post("/api/subjects/export", json=body)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# share="shareable" — own excluded; all-own -> 409 with reasons
# ---------------------------------------------------------------------------


def test_export_shareable_all_own_is_409_with_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "own")
    with _client_app(tmp_path) as client:
        r = client.post("/api/subjects/export", json=_export_body(share="shareable"))
        assert r.status_code == 409, r.text
        body = r.json()
        assert "no_materials" in body["detail"]["reasons"]


def test_export_shareable_all_open_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "open")
    monkeypatch.setattr(
        "asterism.materials._lookup_license", lambda root, dataset_id: ("CC-BY-4.0", True)
    )
    with _client_app(tmp_path) as client:
        r = client.post("/api/subjects/export", json=_export_body(share="shareable"))
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert any(n.endswith("cards/card-1.json") for n in names)


# ---------------------------------------------------------------------------
# 契約メモ contract_pr_f4.md §1-6 — appdata の「足したカード」がこの
# subject_key のとき自動で束に合流する（body の cards に列挙し忘れても
# 漏れない）。
# ---------------------------------------------------------------------------


def _client_app_single_user(tmp_path: Path) -> tuple[TestClient, Path]:
    settings = _settings(tmp_path)
    settings.single_user = True
    settings.appdata_root = tmp_path / "appdata"
    _write_registry(settings.registry_root)
    store_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL}, promote={LIB_DATASET: LIB_GRAPH})
    app = build_app(settings, oxigraph_client=store_client, start_watcher=False)
    register_export(app, settings)
    return TestClient(app, headers=_AUTH), settings.appdata_root


def test_export_merges_in_matching_appdata_card_not_listed_in_the_body(
    tmp_path: Path,
) -> None:
    from asterism_api import appdata

    client, appdata_root = _client_app_single_user(tmp_path)
    appdata.write_thread(
        appdata_root,
        "card-added00000001",
        {
            "card_id": "card-added00000001",
            "subject_key": f"i:{CHECKOUT_1}",
            "tool": "subject_facts",
            "params": {"iri": CHECKOUT_1},
            "title": "件数",
            "output_kind": "facts",
            "created_at": "2026-09-24T00:00:00Z",
        },
        namespace="cards",
    )
    with client:
        r = client.post("/api/subjects/export", json=_export_body())
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert any(n.endswith("cards/card-1.json") for n in names)
        assert any(n.endswith("cards/card-added00000001.json") for n in names)


def test_export_body_card_wins_over_appdata_card_with_same_id(tmp_path: Path) -> None:
    """同じ ``card_id`` が body と appdata の両方にあれば body 側を残す。"""
    from asterism_api import appdata

    shared_id = "card-00000000000000f1"
    client, appdata_root = _client_app_single_user(tmp_path)
    appdata.write_thread(
        appdata_root,
        shared_id,
        {
            "card_id": shared_id,
            "subject_key": f"i:{CHECKOUT_1}",
            "tool": "no_such_tool",  # body 側が勝てば、これは決して呼ばれない
            "params": {},
            "title": "x",
            "output_kind": "facts",
            "created_at": "2026-09-24T00:00:00Z",
        },
        namespace="cards",
    )
    with client:
        body = _export_body(
            cards=[{"card_id": shared_id, "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}]
        )
        r = client.post("/api/subjects/export", json=body)
        assert r.status_code == 200, r.text


def test_export_ignores_appdata_card_for_a_different_subject(tmp_path: Path) -> None:
    from asterism_api import appdata

    client, appdata_root = _client_app_single_user(tmp_path)
    appdata.write_thread(
        appdata_root,
        "card-0ff1ce00000002",
        {
            "card_id": "card-0ff1ce00000002",
            "subject_key": "i:https://ex/library/resource/checkout-2",
            "tool": "subject_facts",
            "params": {"iri": CHECKOUT_1},
            "title": "x",
            "output_kind": "facts",
            "created_at": "2026-09-24T00:00:00Z",
        },
        namespace="cards",
    )
    with client:
        r = client.post("/api/subjects/export", json=_export_body())
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert not any(n.endswith("cards/card-0ff1ce00000002.json") for n in names)


def test_view_field_keeps_only_well_formed_ai_views() -> None:
    """CardSpec.view（AI が書いた見せ方・F13）は形が正しいときだけ束へ運ぶ:
    lang は 3 つのどれか・source_card_id は文字列・本文は lang に応じて
    spec（vega-lite / table）か text（mermaid）。違えば None（元のカードだけ）。"""
    from asterism_api.export_routes import _view_field

    ok = _view_field({"lang": "vega-lite", "spec": {"mark": "area"}, "source_card_id": "card-1"})
    assert ok == {
        "lang": "vega-lite",
        "spec": {"mark": "area"},
        "source_card_id": "card-1",
        "custom": True,
    }
    mermaid = _view_field(
        {"lang": "mermaid", "text": "flowchart LR\n a --> b", "source_card_id": "card-1"}
    )
    assert mermaid is not None and mermaid["text"].startswith("flowchart")
    assert _view_field({"lang": "svg", "spec": {}, "source_card_id": "card-1"}) is None
    assert _view_field({"lang": "vega-lite", "spec": {}, "source_card_id": ""}) is None
    assert _view_field({"lang": "mermaid", "spec": "flowchart LR", "source_card_id": "c"}) is None
    assert _view_field("not a dict") is None


def test_normalize_cards_carries_the_view(tmp_path: Path) -> None:
    from asterism_api.export_routes import _normalize_cards

    cards = _normalize_cards(
        [
            {
                "card_id": "card-1",
                "tool": "set_measure",
                "params": {"shape": "series"},
                "view": {"lang": "table", "spec": {"columns": []}, "source_card_id": "card-0"},
            },
            {"card_id": "card-2", "tool": "subject_facts", "view": {"lang": "nope"}},
        ]
    )
    assert cards[0]["view"] == {
        "lang": "table",
        "spec": {"columns": []},
        "source_card_id": "card-0",
        "custom": True,
    }
    assert "view" not in cards[1]
