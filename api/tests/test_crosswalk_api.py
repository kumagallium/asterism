"""Crosswalk hub auto-rebuild wiring (ADR incremental-ingest.md §7 / crosswalk-hub.md #2).

The hub is a derived projection over the canonical scope; an append grows that scope,
so a debounced rebuilder refreshes it. These tests cover the reusable rebuild (read
observations from live graphs → build → PUT/replace → flag promoted), the debounce
loop, the manual endpoint, and the append→dirty signal — all without a real Oxigraph
(httpx.MockTransport) or morph-kgc.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.main import (
    CROSSWALK_HUB_ID,
    Settings,
    _crosswalk_rebuild_loop,
    _rebuild_crosswalk_hub,
    build_app,
)

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}

_CONFIG = """
min_datasets: 2
concepts:
  - name: composition
    class_iri: https://kumagallium.github.io/asterism/crosswalk/ontology#Composition
    link_predicate: https://kumagallium.github.io/asterism/crosswalk/ontology#hasComposition
    normalizer: composition
    rules:
      - dataset: starrydata
        predicate: https://ex/sd#comp
      - dataset: materials_project
        predicate: https://ex/mp#formula
"""

# Two promoted live graphs that share the composition "Bi2Te3".
_SD_GRAPH = "https://kumagallium.github.io/asterism/graph/canonical/starrydata-aaaa/v1"
_MP_GRAPH = "https://kumagallium.github.io/asterism/graph/canonical/materials_project-bbbb/v1"
_GRAPH_ROWS = {
    _SD_GRAPH: [("https://ex/sd/s1", "Bi2Te3"), ("https://ex/sd/s2", "PbTe")],
    _MP_GRAPH: [("https://ex/mp/m1", "Bi2Te3")],
}


class _CrosswalkOxi:
    """Fake Oxigraph: answers canonical_graphs + per-graph observation SELECTs, and
    records the hub PUT + the promote /update."""

    def __init__(self, graph_rows: dict[str, list[tuple[str, str]]] | None = None) -> None:
        self.graph_rows = graph_rows if graph_rows is not None else dict(_GRAPH_ROWS)
        self.puts: list[tuple[str | None, bytes]] = []
        self.updates: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store" and request.method == "PUT":
                self.puts.append((request.url.params.get("graph"), bytes(request.content)))
                return httpx.Response(204)
            if request.url.path == "/store":
                return httpx.Response(204)
            if request.url.path == "/update":
                self.updates.append(request.content.decode())
                return httpx.Response(204)
            q = request.content.decode()
            rows: list[dict] = []
            if '"promoted"' in q:  # canonical_graphs: the promoted-flag enumeration
                rows = [{"g": {"type": "uri", "value": g}} for g in self.graph_rows]
            else:  # observation SELECT: GRAPH <g> { ?e <pred> ?v }
                for g, pairs in self.graph_rows.items():
                    if f"<{g}>" in q:
                        rows = [
                            {
                                "e": {"type": "uri", "value": e},
                                "v": {"type": "literal", "value": v},
                            }
                            for e, v in pairs
                        ]
                        break
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _write_config(tmp: Path, text: str = _CONFIG) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "crosswalk.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_rebuild_crosswalk_hub_projects_shared_value(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    oxi = _CrosswalkOxi()
    result = asyncio.run(
        _rebuild_crosswalk_hub(oxi.client, cfg_path, built_at="2026-06-11T00:00:00+00:00")
    )
    hub_graph = substrate.canonical_graph_iri(CROSSWALK_HUB_ID)
    assert result["built"] is True
    assert result["graph"] == hub_graph
    assert result["shared"] == {"composition": 1}  # only Bi2Te3 is shared
    # The hub was PUT (replace) into its graph with both datasets' links.
    assert len(oxi.puts) == 1
    graph_param, body = oxi.puts[0]
    assert graph_param == hub_graph
    turtle = body.decode()
    assert "Bi2Te3" in turtle
    assert "hasComposition" in turtle
    assert "<https://ex/sd/s1>" in turtle and "<https://ex/mp/m1>" in turtle
    assert "PbTe" not in turtle  # singleton (starrydata only) is not minted
    # The hub graph was flagged promoted so the FROM-merge read includes it.
    assert any('"promoted"' in u and hub_graph in u for u in oxi.updates)


def test_rebuild_crosswalk_hub_no_config_is_noop(tmp_path: Path) -> None:
    oxi = _CrosswalkOxi()
    result = asyncio.run(
        _rebuild_crosswalk_hub(
            oxi.client, tmp_path / "absent.yaml", built_at="2026-06-11T00:00:00+00:00"
        )
    )
    assert result["built"] is False
    assert oxi.puts == []  # nothing written when crosswalk is not configured


def test_crosswalk_rebuild_loop_debounces_then_rebuilds(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    oxi = _CrosswalkOxi()
    settings = _settings(tmp_path)
    settings.crosswalk_config = cfg_path
    settings.crosswalk_debounce_s = 0.05

    async def drive() -> None:
        dirty = asyncio.Event()
        stop = asyncio.Event()
        task = asyncio.create_task(
            _crosswalk_rebuild_loop(settings, oxi.client, dirty, stop, debounce_s=0.05)
        )
        dirty.set()  # an append happened
        # wait until the debounced rebuild lands (a PUT to the hub graph)
        for _ in range(100):
            if oxi.puts:
                break
            await asyncio.sleep(0.02)
        stop.set()
        dirty.set()  # unblock the loop so it observes stop
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(drive())
    assert len(oxi.puts) >= 1  # the burst of appends produced (at least) one rebuild
    assert oxi.puts[0][0] == substrate.canonical_graph_iri(CROSSWALK_HUB_ID)


# ---- endpoint + append→dirty signal (through build_app) ----------------------


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


def test_rebuild_endpoint_requires_auth(tmp_path: Path) -> None:
    _write_config(tmp_path / "registry")  # default config path = registry_root/crosswalk.yaml
    oxi = _CrosswalkOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:  # token set but no auth header -> 401
        assert client.post("/api/crosswalk/rebuild").status_code == 401
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/crosswalk/rebuild")
        assert r.status_code == 200, r.text
        assert r.json()["built"] is True
        assert r.json()["shared"] == {"composition": 1}
