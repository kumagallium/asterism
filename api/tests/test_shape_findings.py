"""Data shape findings on the api surface (ADR docs/architecture/data-shape-checks.md).

The check logic itself is covered in ingest/tests/test_shapes*.py (including
against a real SPARQL engine). What matters here is the wiring:

* findings recorded at ingest reach the reader through the EXISTING advisories
  channel — no second endpoint, no second UI surface (ADR §D5);
* a re-check overwrites rather than accumulates, so fixed data stops being
  reported;
* the SHACL export is well-formed Turtle and 404s on an unknown dataset.
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

_MATERIALIZE_MD = """
## §9 mapping spec

```yaml
version: 1
prefixes:
  ex: https://example.org/v/
  exr: https://example.org/r/
maps:
  - name: Sample
    source: data.csv
    subject:
      template: https://example.org/r/sample/{SID}
      classes: [ex:Sample]
    properties:
      - predicate: ex:composition
        column: composition
```
"""


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


def _new_dataset(client: TestClient) -> str:
    made = client.post(
        "/api/materialize",
        json={"proposal_md": _MATERIALIZE_MD, "dataset_name": "shapes-fixture"},
    )
    assert made.status_code == 200, made.text
    return made.json()["dataset"]["id"]


def test_recorded_findings_ride_the_existing_advisories_channel(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """A finding stored at ingest comes back from /validate-design alongside the
    design advisories — the one list the catalog already renders."""
    settings = _settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        ds_id = _new_dataset(client)
        finding = (
            "Sample.composition is declared but MISSING in the ingested data: "
            "the mapping declares this predicate and instances of Sample exist, "
            "but not one carries it."
        )
        registry.record_shape_findings(settings.registry_root, ds_id, [finding])

        body = client.get(f"/api/datasets/{ds_id}/validate-design").json()
        assert finding in body["advisories"]


def test_a_clean_recheck_clears_previous_findings(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """Stale advice is worse than none: a re-ingest that comes back clean must
    remove the previous round's findings, not leave them showing."""
    settings = _settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        ds_id = _new_dataset(client)
        registry.record_shape_findings(
            settings.registry_root, ds_id, ["Sample.x is a DANGLING reference: …"]
        )
        assert client.get(f"/api/datasets/{ds_id}/validate-design").json()["advisories"]

        registry.record_shape_findings(settings.registry_root, ds_id, [])
        after = client.get(f"/api/datasets/{ds_id}/validate-design").json()
        assert not [a for a in after["advisories"] if "DANGLING" in a]


_RML_WITH_LINK = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix ex:  <https://example.org/v/> .\n"
    "<#Sample> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/sample/{SID}" ; rr:class ex:Sample ] ;\n'
    "  rr:predicateObjectMap [\n"
    "    rr:predicate ex:hasMeasurement ;\n"
    '    rr:objectMap [ rr:template "https://ex/measurement/{SID}" ]\n'
    "  ] .\n"
    "<#Measurement> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/measurement/{SID}" ; rr:class ex:Measurement ] .\n'
)


def test_ingest_records_findings_from_the_graph_it_just_built(
    tmp_path: Path, monkeypatch
) -> None:
    """The wiring that matters: a real ingest runs the checks against the graph
    it just staged and persists what it found, so the catalog can show it without
    querying the store on every page view."""
    from asterism import substrate

    from asterism_api import registry as reg

    settings = _settings(tmp_path)
    ds_id = reg.save_dataset(
        settings.registry_root,
        "linked",
        {
            "diagram.md": "classDiagram\n  class Sample",
            "model.yaml": "- Sample:",
            "mie.yaml": "schema_info:\n  title: x",
            "mapping.rml.ttl": _RML_WITH_LINK,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-08-14T00:00:00+00:00",
    )["id"]

    def _fake_materialize(
        rml_ttl, csv_dir, *, udfs_path=None, work_dir=None, run_id=None, should_cancel=None
    ) -> Path:
        out = Path(work_dir) / "out.nt"
        out.write_bytes(b'<https://ex/sample/1> <https://schema.org/name> "s" .\n')
        return out

    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_materialize)

    # A store where the classes exist and every link dangles.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            query = (request.content or b"").decode() + str(request.url.params)
            if query.lstrip().upper().startswith("ASK") or "ASK%20" in query:
                payload: dict = {"head": {}, "boolean": True}
            elif "anyp" in query:
                payload = {
                    "head": {"vars": ["o"]},
                    "results": {
                        "bindings": [{"o": {"type": "uri", "value": "https://ex/measurement/1"}}]
                    },
                }
            else:
                payload = {"head": {"vars": ["o"]}, "results": {"bindings": []}}
            return httpx.Response(
                200,
                text=json.dumps(payload),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    oxi = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)
    app = build_app(settings, oxigraph_client=oxi, start_watcher=False)

    with TestClient(app, headers=_AUTH) as client:
        started = client.post(
            f"/api/datasets/{ds_id}/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert started.status_code == 202, started.text
        # Drain the job stream so the background work completes before we assert.
        client.get(f"/api/jobs/{started.json()['job_id']}/stream")

        body = client.get(f"/api/datasets/{ds_id}/validate-design").json()
        assert any("DANGLING reference" in a for a in body["advisories"]), body


def test_shapes_ttl_export_is_parseable_shacl(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    rdflib = pytest.importorskip("rdflib")
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        ds_id = _new_dataset(client)
        res = client.get(f"/api/datasets/{ds_id}/shapes.ttl")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/turtle")

        graph = rdflib.Graph()
        graph.parse(data=res.text, format="turtle")
        shapes = set(
            graph.subjects(
                rdflib.RDF.type, rdflib.URIRef("http://www.w3.org/ns/shacl#NodeShape")
            )
        )
        assert len(shapes) == 1
        assert "https://example.org/v/Sample" in res.text


def test_shapes_ttl_404s_on_unknown_dataset(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/datasets/nope/shapes.ttl").status_code == 404
