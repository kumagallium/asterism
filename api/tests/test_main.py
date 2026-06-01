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
from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from csv2rdf_api.main import Settings, build_app


def _settings(tmp: Path) -> Settings:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    return Settings(env)


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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("notes.txt", b"x\n", "text/plain")},
        )
        assert r.status_code == 400


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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
        r = client.post(
            "/api/inspect",
            files={"files": ("samples.csv", b"SID,sample_id\n1,10\n1,11\n2,10\n", "text/csv")},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        body = r.text
        assert "## CSV: samples.csv" in body
        assert "sample_id" in body


def test_inspect_multi_csv_with_fk(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
    # D7: key reached the client; the comment is in the user message.
    assert captured["key"] == "sk-user-test"
    assert "composite" in str(captured["user"])


def test_refine_rejects_empty_comments(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/refine",
            json={"schema_md": "## x", "comments": ["  "]},
        )
        assert r.status_code == 400


def test_job_stream_unknown_id(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        events = _parse_sse(client.get("/api/jobs/job-999/stream").text)
        assert events and events[0][0] == "error"
