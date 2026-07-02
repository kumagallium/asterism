"""Per-dataset query-tools store (P1: "grow verified tools").

A registry (workbench-onboarded) dataset can carry its own human-vetted typed
tools at ``registry/<id>/query_tools.yaml`` — the SAME shape + loader as the repo
example datasets — so a saved tool becomes a verified Ask/MCP tool with no repo PR.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.query_tools import load_all_query_tools
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
VALID_TOOL = {
    "name": "find_by_label",
    "title": "Find by label",
    "description": "Find resources whose rdfs:label contains a substring.",
    "parameters": [
        {"name": "q", "type": "string", "required": True, "description": "label substring"}
    ],
    "query": (
        f"SELECT ?s ?l WHERE {{ ?s <{_RDFS_LABEL}> ?l . "
        "FILTER(CONTAINS(STR(?l), {{q}})) } LIMIT 50"
    ),
    "result": {"item": {"iri": "s", "label": "l"}},
}


# Mutating routes are token-gated (fail-closed); tests send _AUTH by default.
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
    s.api_token = _TEST_TOKEN
    return s


@pytest.fixture
def healthy_client() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _seed_dataset(reg_root: Path, name: str = "lab") -> str:
    reg_root.mkdir(parents=True, exist_ok=True)
    meta = registry.save_dataset(
        reg_root,
        name,
        {"diagram.md": "classDiagram\n  class Thing", "model.yaml": "", "mie.yaml": ""},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-09T00:00:00Z",
    )
    return meta["id"]


def _client(tmp_path: Path, healthy_client: OxigraphClient) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


# --- registry store unit (no HTTP) -----------------------------------------


def test_store_upsert_list_delete(tmp_path: Path) -> None:
    reg = tmp_path / "registry"
    ds = _seed_dataset(reg)
    assert registry.list_query_tools(reg, ds) == []
    registry.save_query_tool(reg, ds, VALID_TOOL)
    tools = registry.list_query_tools(reg, ds)
    assert [t["name"] for t in tools] == ["find_by_label"]
    # upsert by name (no duplicate)
    registry.save_query_tool(reg, ds, {**VALID_TOOL, "title": "renamed"})
    tools = registry.list_query_tools(reg, ds)
    assert len(tools) == 1 and tools[0]["title"] == "renamed"
    assert registry.delete_query_tool(reg, ds, "find_by_label") is True
    assert registry.list_query_tools(reg, ds) == []
    assert registry.delete_query_tool(reg, ds, "nope") is False


def test_store_loads_via_engine(tmp_path: Path) -> None:
    # The saved tool is read by the SAME engine the Ask/MCP layers use, keyed by
    # dataset id — so it routes as a verified tool (the whole point of P1).
    reg = tmp_path / "registry"
    ds = _seed_dataset(reg)
    registry.save_query_tool(reg, ds, VALID_TOOL)
    loaded = load_all_query_tools(reg)
    assert ds in loaded
    assert [t.name for t in loaded[ds]] == ["find_by_label"]


# --- HTTP endpoints ---------------------------------------------------------


def test_save_then_list_tool(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    client = _client(tmp_path, healthy_client)
    ds = _seed_dataset(tmp_path / "registry")
    r = client.post(f"/api/datasets/{ds}/tools", json=VALID_TOOL)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == "find_by_label"
    got = client.get(f"/api/datasets/{ds}/tools").json()
    assert [t["name"] for t in got["tools"]] == ["find_by_label"]


def test_invalid_tool_is_400_and_not_saved(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    client = _client(tmp_path, healthy_client)
    ds = _seed_dataset(tmp_path / "registry")
    # An update-form query is rejected by parse_query_tools (read-only gate).
    bad = {"name": "bad", "query": "DELETE WHERE { ?s ?p ?o }"}
    r = client.post(f"/api/datasets/{ds}/tools", json=bad)
    assert r.status_code == 400
    assert client.get(f"/api/datasets/{ds}/tools").json()["tools"] == []


def test_delete_tool(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    client = _client(tmp_path, healthy_client)
    ds = _seed_dataset(tmp_path / "registry")
    client.post(f"/api/datasets/{ds}/tools", json=VALID_TOOL)
    assert client.delete(f"/api/datasets/{ds}/tools/find_by_label").status_code == 200
    assert client.get(f"/api/datasets/{ds}/tools").json()["tools"] == []
    assert client.delete(f"/api/datasets/{ds}/tools/find_by_label").status_code == 404


def test_tools_unknown_dataset_404(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    client = _client(tmp_path, healthy_client)
    (tmp_path / "registry").mkdir(parents=True, exist_ok=True)
    assert client.get("/api/datasets/nope-00000000/tools").status_code == 404
    assert client.post("/api/datasets/nope-00000000/tools", json=VALID_TOOL).status_code == 404


# --- P2: AI-assisted draft (key-gated, human-reviewed) ----------------------

_VALID_DRAFT = (
    '{"name":"drafted","title":"Drafted","query":"SELECT ?s WHERE { ?s ?p ?o } LIMIT 5",'
    '"parameters":[],"result":{}}'
)


class _DraftLLM:
    def __init__(self, response: str, captured: dict | None = None) -> None:
        self.response = response
        self.captured = captured if captured is not None else {}

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.captured["user"] = user_message
        return self.response


def _client_with_llm(tmp_path: Path, healthy_client: OxigraphClient, llm) -> TestClient:
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: llm,
    )
    return TestClient(app, headers=_AUTH)


def test_propose_returns_valid_draft_unsaved(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    cap: dict = {}
    client = _client_with_llm(tmp_path, healthy_client, _DraftLLM(_VALID_DRAFT, cap))
    ds = _seed_dataset(tmp_path / "registry")
    r = client.post(
        f"/api/datasets/{ds}/tools/propose", json={"intent": "list all triples"},
        headers={"X-API-Key": "sk"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["name"] == "drafted" and body["valid"] is True
    assert "list all triples" in cap["user"]  # intent reached the model
    # the draft is NOT auto-saved — a human reviews + saves it (the vet gate)
    assert client.get(f"/api/datasets/{ds}/tools").json()["tools"] == []


def test_propose_grounds_in_dataset_rml(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    # The endpoint must feed the dataset's persisted mapping.rml.ttl to the LLM so a
    # seed dataset with a stub model.yaml still drafts against the REAL vocabulary
    # (the namespaces/predicates live in the RML, not the stub model).
    cap: dict = {}
    client = _client_with_llm(tmp_path, healthy_client, _DraftLLM(_VALID_DRAFT, cap))
    reg = tmp_path / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    rml = (
        "@prefix sd: <https://kumagallium.github.io/asterism/starrydata/ontology#> .\n"
        "<#M> rr:class sd:Curve ."
    )
    meta = registry.save_dataset(
        reg,
        "lab",
        {"diagram.md": "classDiagram\n  class Thing", "model.yaml": "", "mie.yaml": "",
         "mapping.rml.ttl": rml},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-09T00:00:00Z",
    )
    r = client.post(
        f"/api/datasets/{meta['id']}/tools/propose",
        json={"intent": "x"},
        headers={"X-API-Key": "sk"},
    )
    assert r.status_code == 200, r.text
    assert "sd:Curve" in cap["user"] and "starrydata/ontology#" in cap["user"]


def test_propose_flags_invalid_draft(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    llm = _DraftLLM('{"name":"bad","query":"DELETE WHERE { ?s ?p ?o }"}')
    client = _client_with_llm(tmp_path, healthy_client, llm)
    ds = _seed_dataset(tmp_path / "registry")
    body = client.post(
        f"/api/datasets/{ds}/tools/propose", json={"intent": "x"}, headers={"X-API-Key": "sk"}
    ).json()
    assert body["valid"] is False and body["error"]


def test_propose_forwards_language_to_llm(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    # body.language reaches the LLM's USER message (the draft's title/description
    # prose follows the UI language); absent = no directive (legacy English).
    cap: dict = {}
    client = _client_with_llm(tmp_path, healthy_client, _DraftLLM(_VALID_DRAFT, cap))
    ds = _seed_dataset(tmp_path / "registry")
    r = client.post(
        f"/api/datasets/{ds}/tools/propose",
        json={"intent": "x", "language": "ja"},
        headers={"X-API-Key": "sk"},
    )
    assert r.status_code == 200, r.text
    assert "# Output language" in cap["user"] and "Japanese (日本語)" in cap["user"]

    r2 = client.post(
        f"/api/datasets/{ds}/tools/propose", json={"intent": "x"}, headers={"X-API-Key": "sk"}
    )
    assert r2.status_code == 200, r2.text
    assert "# Output language" not in cap["user"]


def test_propose_requires_key(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    client = _client_with_llm(tmp_path, healthy_client, _DraftLLM(_VALID_DRAFT))
    ds = _seed_dataset(tmp_path / "registry")
    assert client.post(f"/api/datasets/{ds}/tools/propose", json={"intent": "x"}).status_code == 400


# --- run a saved tool (deterministic, typed, read-only, KEY-FREE) -----------


def _run_client(tmp_path: Path, healthy_client: OxigraphClient) -> TestClient:
    # The run endpoint reads app.state.client (set in the lifespan); these tests do
    # not enter the lifespan, so wire the oxigraph client onto state directly.
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    app.state.client = healthy_client
    return TestClient(app, headers=_AUTH)


def test_run_tool_binds_args_and_runs_readonly(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    client = _run_client(tmp_path, healthy_client)
    ds = _seed_dataset(tmp_path / "registry")
    client.post(f"/api/datasets/{ds}/tools", json=VALID_TOOL)
    # No API key — running a vetted tool is deterministic and needs no LLM.
    r = client.post(f"/api/datasets/{ds}/tools/find_by_label/run", json={"args": {"q": "foo"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool"] == "find_by_label"
    assert isinstance(body["items"], list)
    # the arg was bound SAFELY into the read-only query (escaped string literal)
    assert '"foo"' in body["sparql"]


def test_run_tool_missing_required_arg_is_400(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    client = _run_client(tmp_path, healthy_client)
    ds = _seed_dataset(tmp_path / "registry")
    client.post(f"/api/datasets/{ds}/tools", json=VALID_TOOL)
    r = client.post(f"/api/datasets/{ds}/tools/find_by_label/run", json={"args": {}})
    assert r.status_code == 400


def test_run_unknown_tool_is_404(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    client = _run_client(tmp_path, healthy_client)
    ds = _seed_dataset(tmp_path / "registry")
    assert (
        client.post(f"/api/datasets/{ds}/tools/nope/run", json={"args": {}}).status_code == 404
    )
