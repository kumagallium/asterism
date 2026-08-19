"""``X-Asterism-Preamble`` on POST /api/inspect (ADR source-dialect.md — the

wizard "read check" screen, S2, is where a preamble line first becomes
visible to a human, and the only moment before ``preamble_1`` — a name the
person never wrote — gets baked into the design, the IRI, and the published
item name. This header answers "what would each dropped-preamble line
become if I choose to record it", per source, so S2 can offer a per-line
rename instead of silently letting the machine name win.

Reuses the same read + :func:`asterism_step0.dialect.read_preamble_origins`
call that ``GET /api/datasets/{id}/source-samples``'s ``origins`` answer
uses (:func:`asterism_api.main._read_source_preamble_origins`) — these tests
exercise the wiring through the real endpoint, not a mock of that call.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api.main import Settings, _sanitize_tabular_name, build_app

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


def _healthy_client():
    import httpx
    from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

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


# A real cp932/CRLF/tab instrument export whose first line is a BARE preamble
# line (no ``key: value`` shape) — the ADR's "装置ファイルの前置き" case.
_CP932_BARE_PREAMBLE = (
    "Al3V_bulk\r\n2θ (deg)\t強度 (cps)\r\n"
    + "".join(f"{10 + i}.0\t{100 + i}\r\n" for i in range(6))
).encode("cp932")

# Same shape but the preamble line IS a ``key: value`` label the file itself
# wrote (a majority-``key:``-line block detects as "keyvalue").
_CP932_KEYVALUE_PREAMBLE = (
    "サンプル名: 試料A\r\n2θ (deg)\t強度 (cps)\r\n"
    + "".join(f"{10 + i}.0\t{100 + i}\r\n" for i in range(6))
).encode("cp932")

_CLEAN_CSV = b"SID,composition\n1,Bi2Te3\n2,PbTe\n"


def test_preamble_header_names_bare_line_unnamed(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    canonical = _sanitize_tabular_name("xrd.txt")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect", files={"files": ("xrd.txt", _CP932_BARE_PREAMBLE, "text/plain")}
        )
    assert r.status_code == 200, r.text
    preamble = json.loads(r.headers["X-Asterism-Preamble"])
    assert preamble[canonical] == [
        {"name": "preamble_1", "line": 1, "text": "Al3V_bulk", "named": False}
    ]


def test_preamble_header_names_keyvalue_line_named(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    canonical = _sanitize_tabular_name("xrd.txt")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect", files={"files": ("xrd.txt", _CP932_KEYVALUE_PREAMBLE, "text/plain")}
        )
    assert r.status_code == 200, r.text
    preamble = json.loads(r.headers["X-Asterism-Preamble"])
    entries = preamble[canonical]
    assert len(entries) == 1
    assert entries[0]["named"] is True
    assert entries[0]["line"] == 1
    assert entries[0]["name"] == "サンプル名"
    assert entries[0]["text"] == "サンプル名: 試料A"


def test_preamble_header_absent_for_clean_csv(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/inspect", files={"files": ("clean.csv", _CLEAN_CSV, "text/csv")})
    assert r.status_code == 200, r.text
    preamble = json.loads(r.headers["X-Asterism-Preamble"])
    assert preamble == {}


def test_preamble_header_only_for_sources_with_a_preamble(tmp_path: Path) -> None:
    """Two sources at once — only the one with a still-dropped preamble is a key."""
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    canonical = _sanitize_tabular_name("xrd.txt")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            files=[
                ("files", ("xrd.txt", _CP932_BARE_PREAMBLE, "text/plain")),
                ("files", ("clean.csv", _CLEAN_CSV, "text/csv")),
            ],
        )
    assert r.status_code == 200, r.text
    preamble = json.loads(r.headers["X-Asterism-Preamble"])
    assert set(preamble.keys()) == {canonical}
    assert preamble[canonical][0]["text"] == "Al3V_bulk"


def test_preamble_header_over_budget_degrades_without_breaking_response(tmp_path: Path) -> None:
    """A device header of a hundred bare preamble lines pushes the serialized
    header past the samples-header budget — the response must not fail to
    send; the header degrades to ``{}`` (mirrors ``X-Asterism-Samples``)."""
    n_preamble = 100
    lines = (
        f"legacy-instrument-header-line-{i:03d}-with-some-length-padding" for i in range(n_preamble)
    )
    body = (
        "\r\n".join(lines)
        + "\r\n"
        + "2θ (deg)\t強度 (cps)\r\n"
        + "".join(f"{10 + i}.0\t{100 + i}\r\n" for i in range(6))
    ).encode("cp932")
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/inspect", files={"files": ("xrd.txt", body, "text/plain")})
    assert r.status_code == 200, r.text
    # The header must be present, well-formed JSON, and small — proof the
    # response did not choke on a hundred preamble lines even though it
    # degraded rather than describing all of them.
    preamble = json.loads(r.headers["X-Asterism-Preamble"])
    assert preamble == {}
