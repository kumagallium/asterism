"""Tests for the ingest-rules transparency surface.

Three layers, all read-only for the user-facing half:

- registry: ``mapping.yaml`` (the reviewed §9 Mapping IR spec) persists like the
  other artifacts, and a redesign snapshots the PREVIOUS artifact set under
  ``history/`` before overwriting in place.
- ``GET /api/datasets/{id}/rules``: the deterministic human-readable projection
  of the persisted RML (+ model.yaml labels).
- ``GET /api/datasets/{id}/history[/{snapshot_id}]``: the redesign audit trail,
  with server-side unified diffs against the current artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}


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

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


_RML = """\
@prefix rr:  <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
@prefix ex:  <https://example.org/onto#> .

<#SampleMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sample/{sid}" ;
    rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:label ;
    rr:objectMap [ rml:reference "name" ] ] .
"""

_MODEL = """\
- Sample <https://example.org/resource/sample/s1>:
    - a: ex:Sample
    - ex:label:
        - name: "pellet A"
"""

# model.yaml CURIEs resolve against prefixes extracted from the RML/MIE text.
_MIE = "# prefixes\n# @prefix ex: <https://example.org/onto#> .\n"

# A VALID Mapping IR mirroring _RML — the rules endpoint parses it to merge
# reviewer-facing label/unit onto the projected rows (kantan-mode ADR K8).
_MAPPING_IR = """\
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
    properties:
      - predicate: ex:label
        column: name
        label: "試料名"
        unit: "µV/K"
"""

_ARTIFACTS = {
    "diagram.md": "```mermaid\nclassDiagram\n  class Sample\n```\n",
    "model.yaml": _MODEL,
    "mie.yaml": _MIE,
    "ingester.py": "",
    "mapping.rml.ttl": _RML,
    "mapping.yaml": _MAPPING_IR,
}


def _save(tmp: Path) -> dict:
    return registry.save_dataset(
        tmp / "registry",
        "Samples",
        _ARTIFACTS,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )


# ---------------------------------------------------------------------------
# registry layer
# ---------------------------------------------------------------------------


def test_mapping_ir_is_persisted_and_flagged(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    assert (root / meta["id"] / "mapping.yaml").read_text() == _ARTIFACTS["mapping.yaml"]
    assert meta["has_mapping_ir"] is True

    data = registry.load_dataset(root, meta["id"])
    assert data is not None
    assert data["artifacts"]["mapping.yaml"] == _ARTIFACTS["mapping.yaml"]


def test_redesign_snapshots_previous_artifacts(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"

    # Same content → no snapshot (idempotent re-save must not pile up history).
    registry.update_dataset_artifacts(
        root, meta["id"], _ARTIFACTS,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v1\n",
    )
    assert registry.list_dataset_history(root, meta["id"]) == []

    changed = dict(_ARTIFACTS, **{"mapping.rml.ttl": _RML.replace('"name"', '"label"')})
    new_meta = registry.update_dataset_artifacts(
        root, meta["id"], changed,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v2\n",
    )
    assert new_meta is not None and new_meta["has_mapping_ir"] is True

    snapshots = registry.list_dataset_history(root, meta["id"])
    assert len(snapshots) == 1
    snap = registry.load_dataset_history(root, meta["id"], snapshots[0]["id"])
    assert snap is not None
    # The snapshot holds the PREVIOUS (v1) content, not the new one.
    assert '"name"' in snap["artifacts"]["mapping.rml.ttl"]
    assert snap["artifacts"]["proposal.md"] == "# design v1\n"
    # Empty artifacts (ingester.py) are not stored as empty files.
    assert "ingester.py" not in snap["artifacts"]


def test_history_ids_are_validated(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    assert registry.load_dataset_history(root, meta["id"], "../escape") is None
    assert registry.load_dataset_history(root, "no-such", "20260711T000000Z") is None
    assert registry.list_dataset_history(root, "../escape") == []


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------


def test_rules_endpoint_projects_mapping(tmp_path: Path, healthy_client) -> None:
    meta = _save(tmp_path)
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["warnings"] == []
        assert len(body["maps"]) == 1
        m = body["maps"][0]
        assert m["id"] == "SampleMap"
        assert m["source"] == "samples.csv"
        assert m["subject"]["classes"] == ["ex:Sample"]
        rows = {row["predicate"]: row for row in m["properties"]}
        assert rows["ex:label"]["reference"] == "name"
        # Mapping IR display metadata is merged by expanded predicate IRI
        # (kantan-mode ADR K8).
        assert rows["ex:label"]["label"] == "試料名"
        assert rows["ex:label"]["unit"] == "µV/K"
        # model.yaml labels ride along, keyed by full IRI.
        assert body["labels"].get("https://example.org/onto#Sample") == "Sample"

        r404 = client.get("/api/datasets/nope/rules")
        assert r404.status_code == 404


def test_rules_endpoint_autocompletes_bracketed_unit(tmp_path: Path, healthy_client) -> None:
    """A single-column property with no authored unit but a bracketed column name
    ("Resistivity(Ohm m)") gets its display unit filled deterministically in the
    projection (task #10) — so an IR saved without the unit still shows it."""
    rml = _RML.replace('rml:reference "name"', 'rml:reference "Resistivity(Ohm m)"')
    ir = """\
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
    properties:
      - predicate: ex:label
        column: "Resistivity(Ohm m)"
"""
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "Samples",
        dict(_ARTIFACTS, **{"mapping.rml.ttl": rml, "mapping.yaml": ir}),
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        rows = {row["predicate"]: row for row in r.json()["maps"][0]["properties"]}
        assert rows["ex:label"]["unit"] == "Ohm m"


def test_rules_endpoint_warns_on_unparsable_ir(tmp_path: Path, healthy_client) -> None:
    """A broken mapping.yaml must degrade to a warning, never fail the
    read-only projection (and never invent label/unit)."""
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "Samples",
        dict(_ARTIFACTS, **{"mapping.yaml": "version: 1\nmaps:\n  - id: nope\n"}),
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
        assert any("mapping.yaml" in w for w in body["warnings"])
        rows = {row["predicate"]: row for row in body["maps"][0]["properties"]}
        assert "label" not in rows["ex:label"]


def test_history_endpoints_list_and_diff(tmp_path: Path, healthy_client) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    changed = dict(_ARTIFACTS, **{"mapping.rml.ttl": _RML.replace('"name"', '"label"')})
    registry.update_dataset_artifacts(
        root, meta["id"], changed,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v2\n",
    )

    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        listing = client.get(f"/api/datasets/{meta['id']}/history")
        assert listing.status_code == 200
        body = listing.json()
        assert body["count"] == 1
        snap_id = body["snapshots"][0]["id"]
        assert "mapping.rml.ttl" in body["snapshots"][0]["artifacts"]

        detail = client.get(f"/api/datasets/{meta['id']}/history/{snap_id}")
        assert detail.status_code == 200
        d = detail.json()
        # Only genuinely changed files carry a diff; direction is snapshot → current.
        assert set(d["diffs"]) == {"mapping.rml.ttl", "proposal.md"}
        diff = d["diffs"]["mapping.rml.ttl"]
        assert '-  rr:predicateObjectMap [ rr:predicate ex:label ;' not in diff
        assert '-    rr:objectMap [ rml:reference "name" ] ] .' in diff
        assert '+    rr:objectMap [ rml:reference "label" ] ] .' in diff

        assert client.get(f"/api/datasets/{meta['id']}/history/does-not-exist").status_code == 404
        assert client.get("/api/datasets/nope/history").status_code == 404
