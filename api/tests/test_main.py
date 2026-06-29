"""FastAPI surface tests.

We start the app with ``start_watcher=False`` and an injected mock
OxigraphClient so the test stays inside a single process and doesn't touch
the filesystem outside ``tmp_path``.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app

# Mutating + raw-SPARQL routes are token-gated (fail-closed). The integration
# tests configure a token and send it by default via TestClient(app, headers=_AUTH).
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
    # The raw-SPARQL escape now defaults CLOSED (safe-by-default for a sensitive
    # store). These integration tests exercise the relay, so enable it
    # explicitly here; the default-closed behaviour is unit-tested in
    # ingest/tests/test_exposure.py and test_sparql_disabled_returns_403.
    s.expose_raw_sparql = True
    # Token-gated routes are fail-closed when this is None; configure it so the
    # integration tests (which send _AUTH) can reach the handlers.
    s.api_token = _TEST_TOKEN
    return s


def _mock_client(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


@pytest.fixture
def healthy_client() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        # Used by /health (ASK) and POST /store; both succeed.
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    return _mock_client(handler)


@pytest.fixture
def unreachable_client() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    return _mock_client(handler)


def test_health_ok(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["oxigraph"] is True


def test_health_degraded_when_oxigraph_unreachable(
    tmp_path: Path, unreachable_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=unreachable_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["oxigraph"] is False


def test_upload_writes_to_drop_dir(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("hello.csv", b"SID,DOI\n1,10.x\n", "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "papers"
        assert body["queued"] is True

    dest = tmp_path / "csv" / "papers" / "hello.csv"
    assert dest.exists()
    assert dest.read_bytes() == b"SID,DOI\n1,10.x\n"
    # No leftover .tmp
    assert not dest.with_suffix(".csv.tmp").exists()


def test_upload_rejects_bad_kind(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/upload/junk",
            files={"file": ("x.csv", b"a,b\n", "text/csv")},
        )
        assert r.status_code == 400


def test_upload_rejects_unsafe_filename(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("../etc/passwd.csv", b"x\n", "text/csv")},
        )
        assert r.status_code == 400


def test_upload_rejects_non_csv_suffix(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("notes.txt", b"x\n", "text/plain")},
        )
        assert r.status_code == 400


def test_upload_enforces_byte_cap(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch
) -> None:
    """A file larger than the cap is rejected with 413 and leaves no partial."""
    import asterism_api.main as main_mod

    monkeypatch.setattr(main_mod, "_MAX_UPLOAD_BYTES", 16)
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("big.csv", b"x" * 64, "text/csv")},
        )
        assert r.status_code == 413
    papers = tmp_path / "csv" / "papers"
    assert not (papers / "big.csv").exists()
    assert not (papers / "big.csv.tmp").exists()  # partial cleaned up


def test_jobs_returns_recent(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    s = _settings(tmp_path)
    s.jobs_log.parent.mkdir(parents=True, exist_ok=True)
    s.jobs_log.write_text(
        json.dumps({"kind": "papers", "status": "ok", "rows_in": 1}) + "\n"
        + json.dumps({"kind": "samples", "status": "ok", "rows_in": 2}) + "\n",
        encoding="utf-8",
    )
    app = build_app(s, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/jobs", params={"limit": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["jobs"][0]["kind"] == "samples"


def test_jobs_rejects_invalid_limit(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/jobs", params={"limit": 0})
        assert r.status_code == 400


# ----------------------------------------------------------------------------
# Phase 4 (M0): /api/inspect — step0 structure inspection, no LLM
# ----------------------------------------------------------------------------


def test_inspect_returns_markdown(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            files={"files": ("samples.csv", b"SID,sample_id\n1,10\n1,11\n2,10\n", "text/csv")},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        body = r.text
        assert "## CSV: samples.csv" in body
        assert "sample_id" in body


def test_inspect_json_returns_markdown(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """#19: /api/inspect dispatches a .json upload to the JSON inspector."""
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            files={
                "files": (
                    "mp.json",
                    b'[{"mp_id":"mp-1","structure":{"spacegroup":"Fm-3m"}}]',
                    "application/json",
                )
            },
        )
        assert r.status_code == 200, r.text
        body = r.text
        assert "## JSON: mp.json" in body
        assert "iterator `$[*]`" in body
        # nested object surfaces as a dot-path leaf usable as an rml:reference
        assert "`structure.spacegroup`" in body


def test_inspect_rejects_unsupported_source_extension(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            files={"files": ("notes.txt", b"hello\n", "text/plain")},
        )
        assert r.status_code == 400


def test_inspect_multi_csv_with_fk(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            params={"fk": "SID"},
            files=[
                ("files", ("papers.csv", b"SID,DOI\n1,10.1/a\n2,10.2/b\n", "text/csv")),
                ("files", ("samples.csv", b"SID,sample_id\n1,10\n2,11\n", "text/csv")),
            ],
        )
        assert r.status_code == 200
        body = r.text
        assert "## CSV: papers.csv" in body
        assert "## CSV: samples.csv" in body


def test_inspect_rejects_unsafe_filename(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            files={"files": ("../../etc/passwd.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert r.status_code == 400


# ----------------------------------------------------------------------------
# Phase 4 (M1a): /api/propose + SSE job stream — mock LLM, no API key
# ----------------------------------------------------------------------------


class _MockLLM:
    """Records the prompts and returns canned proposal text (no network)."""

    def __init__(self, captured: dict[str, object], key: str | None) -> None:
        self.captured = captured
        self.key = key

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.captured["system"] = system_prompt
        self.captured["user"] = user_message
        self.captured["key"] = self.key
        return "## Proposed schema\n\nMOCK PROPOSAL for the uploaded CSV."


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE response body into (event_name, data_dict) pairs."""
    events: list[tuple[str, dict]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = line[len("data:"):].strip()
            events.append((name, json.loads(payload) if payload else {}))
    return events


def test_propose_starts_job_and_streams_done(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    captured: dict[str, object] = {}
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: _MockLLM(captured, key),
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/propose",
            params={"fk": "SID"},
            data={"domain": "thermoelectric measurement curves; PROV-O; no bnodes"},
            files={"files": ("samples.csv", b"SID,sample_id\n1,10\n1,11\n2,10\n", "text/csv")},
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        assert job_id

        stream = client.get(f"/api/jobs/{job_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(stream.text)
        names = [n for n, _ in events]
        assert "started" in names
        assert "done" in names
        done_payload = next(d for n, d in events if n == "done")
        assert "MOCK PROPOSAL" in done_payload["result"]["proposal_md"]

    # D7: the user-brought key reached the LLM client for this run...
    assert captured["key"] == "sk-user-test"
    # ...and the inspection Markdown was assembled into the user message.
    assert "sample_id" in str(captured["user"])


def test_propose_without_domain_hint(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """Domain hint is optional (案 A): propose must run with no `domain` field."""
    captured: dict[str, object] = {}
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: _MockLLM(captured, key),
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/propose",
            files={"files": ("s.csv", b"SID,sample_id\n1,10\n2,11\n", "text/csv")},
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        assert "done" in [n for n, _ in events]


def test_propose_error_surfaces_as_error_event(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    class _BoomLLM:
        def __init__(self, key: str | None) -> None:
            pass

        def complete(self, system_prompt: str, user_message: str) -> str:
            raise RuntimeError("boom from LLM")

    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: _BoomLLM(key),
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/propose",
            data={"domain": "x"},
            files={"files": ("s.csv", b"SID,sample_id\n1,10\n", "text/csv")},
        )
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        names = [n for n, _ in events]
        assert "error" in names
        err = next(d for n, d in events if n == "error")
        assert "boom from LLM" in err["message"]


def test_refine_starts_job_and_streams_done(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """M1c: POST /api/refine applies comments via the LLM and streams done."""
    captured: dict[str, object] = {}
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_factory=lambda key: _MockLLM(captured, key),
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/refine",
            json={
                "schema_md": "## Proposed schema\n\nSample IRI = sdr:sample/{sample_id}",
                "comments": ["use a composite (SID, sample_id) key"],
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        names = [n for n, _ in events]
        assert "done" in names
        done = next(d for n, d in events if n == "done")
        assert "refined_md" in done["result"]
        # Truncation guard fields are surfaced; a prose-only schema loses no
        # artifacts, so the refine is complete with no warnings.
        assert done["result"]["complete"] is True
        assert done["result"]["warnings"] == []
        assert "effective_schema_md" in done["result"]
    # D7: key reached the client; the comment is in the user message.
    assert captured["key"] == "sk-user-test"
    assert "composite" in str(captured["user"])


def test_refine_rejects_empty_comments(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/refine",
            json={"schema_md": "## x", "comments": ["  "]},
        )
        assert r.status_code == 400


_MATERIALIZE_MD = """## Schema proposal

### Class diagram
```mermaid
classDiagram
    class Sample
    class Paper
    Sample --> Paper : fromPaper
```

### MIE
```yaml
schema_info:
  title: Demo
  keywords: [thermoelectric, seebeck, zt, sample, paper]
  categories: [materials]
```

### Ingester
```python
import csv
def emit(path):
    open(path, encoding="utf-8-sig")
```
"""


def test_materialize_extracts_artifacts_and_validates(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """M1d: /api/materialize splits the proposal and runs the 8-trap validator."""
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD, "dataset_name": "demo"},
        )
        assert r.status_code == 200
        body = r.json()
        # diagram / mie / ingester extracted (no rdf-config model block here)
        assert body["artifacts"]["diagram.md"]
        assert body["artifacts"]["mie.yaml"]
        assert body["artifacts"]["ingester.py"]
        trap = {t["id"]: t["status"] for t in body["traps"]}
        assert trap["T2"] == "pass"  # utf-8-sig in ingester
        assert trap["T4"] == "pass"  # >=5 keywords
        assert trap["T1"] == "skip"  # no source CSV attached
        assert "exit_code" in body


def test_materialize_rejects_empty(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/materialize", json={"proposal_md": "   "})
        assert r.status_code == 400


def test_materialize_persists_and_lists_dataset(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """V1: materialize persists the bundle so it shows in the Gallery listing."""
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        # Registry starts empty.
        assert client.get("/api/datasets").json() == {"count": 0, "datasets": []}

        r = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD, "dataset_name": "thermo"},
        )
        assert r.status_code == 200
        meta = r.json()["dataset"]
        assert meta["name"] == "thermo"
        assert meta["id"].startswith("thermo-")
        # class list extracted from the mermaid diagram
        assert set(meta["classes"]) == {"Sample", "Paper"}
        assert meta["class_count"] == 2

        listing = client.get("/api/datasets").json()
        assert listing["count"] == 1
        assert listing["datasets"][0]["id"] == meta["id"]

        # Detail returns the saved artifacts.
        detail = client.get(f"/api/datasets/{meta['id']}").json()
        assert detail["meta"]["id"] == meta["id"]
        assert detail["artifacts"]["mie.yaml"]
        assert "classDiagram" in detail["artifacts"]["diagram.md"]


def test_materialize_persist_false_skips_registry(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD, "persist": False},
        )
        assert r.status_code == 200
        assert "dataset" not in r.json()
        assert client.get("/api/datasets").json()["count"] == 0


def test_materialize_persists_proposal_for_redesign(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """Redesign: materialize stores the design markdown so it can be reopened."""
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD, "dataset_name": "thermo"},
        )
        meta = r.json()["dataset"]
        assert meta["has_proposal"] is True

        # The stored design round-trips via the read-only proposal endpoint.
        prop = client.get(f"/api/datasets/{meta['id']}/proposal").json()
        assert prop["dataset_id"] == meta["id"]
        assert prop["dataset_name"] == "thermo"
        assert prop["has_proposal"] is True
        assert prop["proposal_md"] == _MATERIALIZE_MD


def test_proposal_unknown_returns_404(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/datasets/nope-12345678/proposal").status_code == 404


def test_redesign_re_materialize_updates_in_place(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """Re-materializing with dataset_id overwrites the SAME dataset (no duplicate)."""
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        first = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD, "dataset_name": "thermo"},
        ).json()["dataset"]
        ds_id = first["id"]
        assert client.get("/api/datasets").json()["count"] == 1

        # Re-design: a tweaked proposal (drop the Paper class) re-materialized in place.
        redesigned_md = _MATERIALIZE_MD.replace("class Paper\n", "")
        again = client.post(
            "/api/materialize",
            json={
                "proposal_md": redesigned_md,
                "dataset_name": "thermo",
                "dataset_id": ds_id,
            },
        ).json()["dataset"]

        # SAME id, NOT a duplicate; the design-derived meta reflects the new design.
        assert again["id"] == ds_id
        assert client.get("/api/datasets").json()["count"] == 1
        assert set(again["classes"]) == {"Sample"}

        # The reopened design now returns the redesigned markdown.
        prop = client.get(f"/api/datasets/{ds_id}/proposal").json()
        assert prop["proposal_md"] == redesigned_md


def test_redesign_unknown_dataset_id_404(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={
                "proposal_md": _MATERIALIZE_MD,
                "dataset_name": "thermo",
                "dataset_id": "nope-12345678",
            },
        )
        assert r.status_code == 404


def test_get_dataset_unknown_returns_404(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/datasets/nope-12345678").status_code == 404
        # Path-traversal-ish id is rejected as not-found, never escapes root.
        assert client.get("/api/datasets/..%2f..%2fetc").status_code == 404


def test_sparql_select_relays_results(tmp_path: Path) -> None:
    """M3: /api/sparql forwards a read-only query and returns the JSON."""
    rows = {
        "head": {"vars": ["s"]},
        "results": {"bindings": [{"s": {"type": "uri", "value": "urn:x"}}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=json.dumps(rows),
            headers={"content-type": "application/sparql-results+json"},
        )

    app = build_app(
        _settings(tmp_path), oxigraph_client=_mock_client(handler), start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/sparql", json={"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"})
        assert r.status_code == 200
        assert r.json()["results"]["bindings"][0]["s"]["value"] == "urn:x"


def test_sparql_disabled_returns_403_when_exposure_off(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """Exposure profile = typed-only: the raw SPARQL relay is withheld (ADR)."""
    s = _settings(tmp_path)
    s.expose_raw_sparql = False  # topology B / sensitive deployment
    app = build_app(s, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/sparql", json={"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"}
        )
        assert r.status_code == 403


def test_write_routes_fail_closed_without_token(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """No ASTERISM_API_TOKEN -> mutating routes are DISABLED (503), reads stay open."""
    s = _settings(tmp_path)
    s.api_token = None  # operator did not configure a token
    app = build_app(s, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:  # deliberately no auth header
        # A mutating route is fail-closed, not anonymously open.
        assert client.delete("/api/datasets/whatever-00000000").status_code == 503
        assert (
            client.post("/api/sparql", json={"query": "ASK {}"}).status_code == 503
        )
        # Read-only catalog + health stay open.
        assert client.get("/api/datasets").status_code == 200
        assert client.get("/health").status_code in (200, 503)  # 503 only if oxigraph down


def test_write_routes_require_valid_token_when_configured(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """With a token set, mutating routes require it (Bearer or X-Asterism-Token)."""
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:  # _settings configures _TEST_TOKEN
        # Absent / wrong token -> 401 (not 503: the route IS configured).
        assert client.delete("/api/datasets/whatever-00000000").status_code == 401
        assert (
            client.delete(
                "/api/datasets/whatever-00000000",
                headers={"X-Asterism-Token": "wrong"},
            ).status_code
            == 401
        )
        # Correct token passes auth and reaches the handler (404 not-found).
        assert (
            client.delete("/api/datasets/whatever-00000000", headers=_AUTH).status_code
            == 404
        )
        # Bearer form is accepted too.
        assert (
            client.delete(
                "/api/datasets/whatever-00000000",
                headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
            ).status_code
            == 404
        )


def test_sparql_rejects_update_and_empty(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        assert client.post("/api/sparql", json={"query": "   "}).status_code == 400
        # Update forms are rejected even though /query is read-only anyway.
        assert (
            client.post(
                "/api/sparql",
                json={"query": "INSERT DATA { <urn:a> <urn:b> <urn:c> }"},
            ).status_code
            == 400
        )


def test_sparql_injects_from_merge_when_canonical_graphs_exist(tmp_path: Path) -> None:
    """#20: a plain /api/sparql query is rewritten to read the canonical FROM-merge."""
    from asterism.substrate import canonical_graph_iri

    g = canonical_graph_iri("a")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        if "SELECT DISTINCT ?g" in q and '"promoted"' in q:  # canonical enumeration
            rows = [{"g": {"type": "uri", "value": g}}]
        elif "COUNT" in q and "GRAPH" not in q:  # startup migration default-count
            rows = [{"c": {"value": "0"}}]
        elif q.strip().startswith("ASK"):  # startup legacy-has-data probe -> empty
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": False}),
                headers={"content-type": "application/sparql-results+json"},
            )
        else:
            seen["relay"] = q  # the rewritten relay query
            rows = []
        return httpx.Response(
            200,
            text=json.dumps({"head": {"vars": ["g", "c"]}, "results": {"bindings": rows}}),
            headers={"content-type": "application/sparql-results+json"},
        )

    app = build_app(
        _settings(tmp_path), oxigraph_client=_mock_client(handler), start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/sparql", json={"query": "SELECT ?s WHERE { ?s ?p ?o }"})
        assert r.status_code == 200
    assert f"FROM <{g}>" in seen["relay"]  # cross-dataset scope injected


def test_startup_migrates_default_into_canonical_legacy(tmp_path: Path) -> None:
    """#20: pre-existing default-graph data is moved into canonical/legacy on boot."""
    from asterism.substrate import canonical_graph_iri

    updates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/update":
            updates.append(request.content.decode())
            return httpx.Response(204)
        q = request.content.decode()
        # migration default-count -> non-empty so it triggers ADD + CLEAR
        rows = [{"c": {"value": "76"}}] if ("COUNT" in q and "GRAPH" not in q) else []
        return httpx.Response(
            200,
            text=json.dumps({"head": {"vars": ["c"]}, "results": {"bindings": rows}}),
            headers={"content-type": "application/sparql-results+json"},
        )

    app = build_app(
        _settings(tmp_path), oxigraph_client=_mock_client(handler), start_watcher=False
    )
    with TestClient(app, headers=_AUTH):
        pass  # lifespan startup runs the migration
    legacy = canonical_graph_iri("legacy")
    # Migrate default -> canonical/legacy, then flag legacy promoted (citability is
    # now flag-gated, so migrated legacy data must carry the promoted flag).
    assert updates[:2] == [f"ADD DEFAULT TO GRAPH <{legacy}>", "CLEAR DEFAULT"]
    assert any(
        "INSERT DATA" in u and '"promoted"' in u and legacy in u for u in updates[2:]
    )


def test_job_stream_unknown_id(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        events = _parse_sse(client.get("/api/jobs/job-999/stream").text)
        assert events and events[0][0] == "error"


# A proposal whose §RML block references a column the source CSV does NOT have
# (`comp` vs the real `composition`). Materialize succeeds (the design is saved);
# the advisory design validation reports the bad column so the user can fix it at
# review time — before ingest — via the one-click "ask AI to fix".
_MATERIALIZE_MD_BAD_COLUMN = """## Schema proposal

### Class diagram
```mermaid
classDiagram
    class Sample
```

### MIE
```yaml
schema_info:
  title: Demo
  keywords: [thermoelectric, seebeck, zt, sample, composition]
  categories: [materials]
```

### Ingester
```python
import csv
def emit(path):
    open(path, encoding="utf-8-sig")
```

### RML
```turtle
@prefix rr:  <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "data.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/sample/{SID}" ] ;
  rr:predicateObjectMap [ rr:predicate <https://ex/hasComposition> ;
    rr:objectMap [ rml:reference "comp" ] ] .
```
"""


def test_materialize_reports_advisory_validation_issues_when_source_present(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """Advisory design validation runs AT MATERIALIZE against the persisted source.

    A brand-new design has no source yet (it is attached after materialize), so the
    first materialize reports no advisory issues. After the source is attached, a
    re-materialize in place (the redesign path, `dataset_id` set) validates the RML
    against the REAL CSV header — a bad column reference surfaces in
    ``validation_issues`` — and materialize STILL succeeds (200): the issues are
    advisory, the design is saved regardless.
    """
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        # 1) First materialize (brand-new): no source persisted yet → no advisory
        #    issues even though the RML references a column the CSV will lack.
        first = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD_BAD_COLUMN, "dataset_name": "thermo"},
        )
        assert first.status_code == 200
        body = first.json()
        assert body["validation_issues"] == []
        ds_id = body["dataset"]["id"]

        # 2) Attach a source whose header has `composition` (not `comp`).
        assert (
            client.post(
                f"/api/datasets/{ds_id}/source",
                files={"files": ("data.csv", b"SID,composition\n1,Bi2Te3\n", "text/csv")},
            ).status_code
            == 200
        )

        # 3) Re-materialize in place (redesign path) — now the source is available, so
        #    the advisory validation flags the bad `comp` column; materialize SUCCEEDS.
        again = client.post(
            "/api/materialize",
            json={
                "proposal_md": _MATERIALIZE_MD_BAD_COLUMN,
                "dataset_name": "thermo",
                "dataset_id": ds_id,
            },
        )
        assert again.status_code == 200, again.text
        issues = again.json()["validation_issues"]
        assert isinstance(issues, list)
        assert any("comp" in m for m in issues), issues
        # The "did you mean" suggestion surfaces the real, similar column.
        assert any("composition" in m for m in issues), issues
        # Materialize still persisted the design (advisory, not a gate).
        assert client.get(f"/api/datasets/{ds_id}").status_code == 200
