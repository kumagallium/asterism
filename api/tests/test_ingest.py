"""Tests for the human-gated substrate ingest (Phase 5 #15).

``POST /api/datasets/{id}/ingest`` runs the dataset's persisted RML through
Morph-KGC and streams the result straight into the dataset's canonical named graph
(staged, not yet citable) as a background job (202 + job_id; progress + done over
SSE). Promotion then flips a control-graph flag — no MOVE (memory-bounded promote).
We monkeypatch the Morph-KGC file-output step (``substrate.materialize_to_nt_file``)
so the tests need neither the optional ``morph-kgc`` extra nor real CSVs, and use
the MockTransport Oxigraph client so the chunked ``/store`` POSTs (and the graph
``DROP`` / control writes) are observable.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from asterism import substrate
from asterism.documents import pandoc_version
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient
from watchfiles import Change

from asterism_api import registry
from asterism_api.main import Settings, _append_watch_loop, _sanitize_document_name, build_app

_NO_PANDOC = pandoc_version() is None

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


class _RecordingOxi:
    """OxigraphClient backed by a transport that records /store + /update calls."""

    def __init__(self) -> None:
        self.store_calls: list[str | None] = []
        self.updates: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store":
                self.store_calls.append(request.url.params.get("graph"))
                return httpx.Response(204)
            if request.url.path == "/update":
                self.updates.append(request.content.decode())
                return httpx.Response(204)
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


# ---- background-job ingest helpers (the endpoint returns 202 + job_id, then
# materialize + chunked upload run as a job whose SSE stream carries progress +
# the done result; see ADR scalable-declarative-ingestion.md / jobs.py) --------


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE response body into ``[(event_name, data_dict), ...]``."""
    out: list[tuple[str, dict]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            out.append((name, json.loads(line[len("data:") :].strip())))
    return out


def _fake_nt_materializer(*, triples: int = 1):
    """A stand-in for ``substrate.materialize_to_nt_file`` that writes N triples.

    Avoids needing morph-kgc / real CSVs; the streaming load then runs for real
    against the recording client.
    """

    def _materialize(rml_ttl, csv_dir, *, udfs_path=None, work_dir=None, run_id=None) -> Path:
        out = Path(work_dir) / "out.nt"
        out.write_bytes(
            b"".join(
                f"<https://ex/paper/{i}> <https://schema.org/name> \"p{i}\" .\n".encode()
                for i in range(triples)
            )
        )
        return out

    return _materialize


def _drive_ingest(client, dataset_id: str, files=None) -> tuple[int, list[tuple[str, dict]]]:
    """POST the ingest, then (if 202) drain its SSE stream. Returns (status, events)."""
    r = client.post(f"/api/datasets/{dataset_id}/ingest", files=files or None)
    if r.status_code != 202:
        return r.status_code, []
    job_id = r.json()["job_id"]
    events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
    return 202, events


_RML = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    '<#M> a rr:TriplesMap ;\n'
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] .\n'
)


def _save_dataset_with_rml(tmp: Path, rml: str = _RML) -> str:
    """Persist a dataset carrying an RML mapping; return its id."""
    return registry.save_dataset(
        tmp / "registry",
        "demo",
        {
            "diagram.md": "classDiagram\n  class Paper",
            "model.yaml": "- Paper:",
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": rml,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-03T00:00:00+00:00",
    )["id"]


def test_save_dataset_persists_rml_and_flags_has_rml(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    loaded = registry.load_dataset(tmp_path / "registry", dataset_id)
    assert loaded is not None
    assert "rr:TriplesMap" in loaded["artifacts"]["mapping.rml.ttl"]
    assert loaded["meta"]["has_rml"] is True
    assert loaded["meta"]["ingested"] is False


def test_ingest_happy_path_streams_canonical_with_progress(tmp_path: Path, monkeypatch) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    # part5: the first ingest streams into a fresh version graph v1 (staged, not yet
    # citable — the live graph, if any, is untouched, so there is no DROP).
    graph_iri = substrate.versioned_graph_iri(dataset_id, 1)
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(
            client, dataset_id, {"files": ("papers.csv", b"SID\n1\n", "text/csv")}
        )
        assert status == 202
        assert "done" in [n for n, _ in events], events
        result = next(d for n, d in events if n == "done")["result"]
        assert result["graph_kind"] == "staged"
        assert result["graph_iri"] == graph_iri
        assert result["triple_count"] == 1
        assert result["dataset"]["ingested"] is True
        assert result["dataset"]["promoted"] is False  # staged, awaits a promote gate
        # progress frames: a materialize phase then an upload phase reaching total
        running = [d for n, d in events if n == "running"]
        assert any(d.get("phase") == "materialize" for d in running)
        assert any(
            d.get("phase") == "upload" and d.get("done") == d.get("total") for d in running
        )
    # chunk(s) POSTed to the canonical named graph; meta on disk updated.
    assert oxi.store_calls == [graph_iri]
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["ingested"] is True
    assert meta["triple_count"] == 1


def test_ingest_missing_column_returns_422_with_issues(tmp_path: Path) -> None:
    # Design validation runs SYNCHRONOUSLY (no background job): an RML that
    # references a column the persisted CSV lacks gets a clear 422 whose body
    # carries a structured `issues` list — never an opaque Morph-KGC crash.
    rml = (
        "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
        '<#M> a rr:TriplesMap ;\n'
        '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <https://ex/p> ;\n'
        '    rr:objectMap [ rml:reference "project_slug" ] ] .\n'
    )
    dataset_id = _save_dataset_with_rml(tmp_path, rml=rml)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        # Persist a CSV whose header is SID,project_names (no project_slug).
        assert (
            client.post(
                f"/api/datasets/{dataset_id}/source",
                files={"files": ("papers.csv", b"SID,project_names\n1,p\n", "text/csv")},
            ).status_code
            == 200
        )
        r = client.post(f"/api/datasets/{dataset_id}/ingest")
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert isinstance(detail["issues"], list)
        assert any("project_slug" in m for m in detail["issues"])
        # The "did you mean" suggestion surfaces the real, similar column.
        assert any("project_names" in m for m in detail["issues"])
    # No streaming POST happened — the request was rejected before the job started.
    assert oxi.store_calls == []


_JATS_DOC = (
    '<?xml version="1.0"?><article><front><article-meta>'
    '<article-id pub-id-type="pmcid">PMC-DEMO</article-id>'
    "<title-group><article-title>Demo</article-title></title-group>"
    "</article-meta></front><body>"
    '<sec id="s1"><title>1. Introduction</title><p>One sentence here. And another.</p></sec>'
    '<sec id="s2"><title>2. Methods</title><p>Measured under argon. Cited verbatim.</p></sec>'
    "</body></article>"
)


def _save_document_dataset(tmp: Path, name: str = "docdemo") -> str:
    """Persist a DOCUMENT (JATS) dataset: source_kind=xml, an .xml source, no RML."""
    dataset_id = registry.save_dataset(
        tmp / "registry",
        name,
        {"diagram.md": "classDiagram\n  class Document"},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-12T00:00:00+00:00",
    )["id"]
    sdir = registry.source_dir(tmp / "registry", dataset_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "paper.xml").write_text(_JATS_DOC, encoding="utf-8")
    registry.mark_source_saved(tmp / "registry", dataset_id, ["paper.xml"])
    return dataset_id


def test_ingest_document_path_structures_to_sentences(tmp_path: Path) -> None:
    # A JATS document dataset (source_kind=xml) ingests via the deterministic
    # structurer (asterism.documents) — NO RML, NO morph-kgc — and streams a
    # sentence-level doco/nif graph into the staged version graph.
    dataset_id = _save_document_dataset(tmp_path)
    assert registry.load_dataset(tmp_path / "registry", dataset_id)["meta"]["source_kind"] == "xml"
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    graph_iri = substrate.versioned_graph_iri(dataset_id, 1)
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(client, dataset_id)  # no upload — reuse persisted source
        assert status == 202, events
        result = next(d for n, d in events if n == "done")["result"]
        assert result["graph_iri"] == graph_iri
        # paper + 2 sections + 2 paragraphs + 4 sentences + context + parse activity …
        assert result["triple_count"] > 10
    assert oxi.store_calls == [graph_iri]


def test_ingest_document_without_rml_is_ok(tmp_path: Path) -> None:
    # The "no RML mapping" 400 must NOT fire for a document dataset (the RML
    # requirement is CSV/JSON-only; documents take the structurer path).
    dataset_id = _save_document_dataset(tmp_path, "docnoml")
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        status, _ = _drive_ingest(client, dataset_id)
        assert status == 202


def test_create_document_dataset_attaches_tools_and_ingests(tmp_path: Path) -> None:
    # POST /api/documents creates a document dataset from an uploaded JATS (no
    # schema design), auto-attaches the recall tools, and is then ingestable.
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "My Paper"},
            files={"files": ("paper.xml", _JATS_DOC.encode(), "application/xml")},
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["dataset_id"]
        assert r.json()["dataset"]["source_kind"] == "xml"
        # the recall tools were auto-attached (queryable from the catalog tools tab)
        tools = client.get(f"/api/datasets/{dataset_id}/tools").json()["tools"]
        assert {t["name"] for t in tools} >= {"search_text", "quote_with_citation"}
        # and the document ingests through the structurer (no RML)
        status, events = _drive_ingest(client, dataset_id)
        assert status == 202, events
        assert next(d for n, d in events if n == "done")["result"]["triple_count"] > 10


def test_create_document_rolls_back_on_non_http_error(tmp_path: Path, monkeypatch) -> None:
    # A failure DURING source persistence must leave NO orphan dataset — even when it
    # is NOT an HTTPException (a client disconnect mid-upload, an OSError on a full
    # disk). The old narrow `except HTTPException` let those escape, orphaning the
    # just-created (source-less) record for a re-upload to duplicate.
    import asterism_api.main as main_mod

    async def _boom(*_a, **_k):
        raise OSError("disk full")  # a non-HTTPException raised mid-upload

    monkeypatch.setattr(main_mod, "_persist_source_uploads", _boom)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client, pytest.raises(OSError):
        client.post(
            "/api/documents",
            data={"name": "Doomed"},
            files={"files": ("paper.xml", _JATS_DOC.encode(), "application/xml")},
        )
    # The just-created record was rolled back — no source-less orphan remains.
    assert registry.list_datasets(tmp_path / "registry") == []


def test_create_document_rolls_back_on_http_error(tmp_path: Path, monkeypatch) -> None:
    # The existing HTTPException rollback still fires (now via the finally guard): a
    # persistence failure surfaced as an HTTPException also leaves no orphan.
    from fastapi import HTTPException

    import asterism_api.main as main_mod

    async def _boom(*_a, **_k):
        raise HTTPException(400, "bad source")

    monkeypatch.setattr(main_mod, "_persist_source_uploads", _boom)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "Doomed"},
            files={"files": ("paper.xml", _JATS_DOC.encode(), "application/xml")},
        )
        assert r.status_code == 400
    assert registry.list_datasets(tmp_path / "registry") == []


# ---- PDF (Docling sidecar) path -------------------------------------------------
# A .pdf is persisted RAW and converted by the Docling sidecar at INGEST (the slow ML
# step lives in the async job). The sidecar is mocked here so the tests need no torch.
_PDF_BYTES = b"%PDF-1.7\n%minimal test pdf\n"


def _settings_with_docling(tmp: Path, url: str | None) -> Settings:
    s = _settings(tmp)
    s.docling_url = url
    return s


def _save_document_dataset_pdf(tmp: Path, name: str = "pdfdemo") -> str:
    """Persist a DOCUMENT dataset whose source is a RAW .pdf (source_kind=xml)."""
    dataset_id = registry.save_dataset(
        tmp / "registry",
        name,
        {"diagram.md": "classDiagram\n  class Document"},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-15T00:00:00+00:00",
    )["id"]
    sdir = registry.source_dir(tmp / "registry", dataset_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "paper.pdf").write_bytes(_PDF_BYTES)
    registry.mark_source_saved(tmp / "registry", dataset_id, ["paper.pdf"])
    return dataset_id


def test_create_document_dataset_from_pdf_persists_raw(tmp_path: Path) -> None:
    # POST /api/documents with a .pdf: the RAW PDF is persisted (NOT converted at create
    # — the slow Docling step happens in the async ingest job), source_kind=xml.
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "My PDF"},
            files={"files": ("paper.pdf", _PDF_BYTES, "application/pdf")},
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["dataset_id"]
        assert r.json()["dataset"]["source_kind"] == "xml"
    sdir = registry.source_dir(tmp_path / "registry", dataset_id)
    assert (sdir / "paper.pdf").read_bytes() == _PDF_BYTES  # raw, not converted at create


def test_ingest_pdf_without_sidecar_is_422(tmp_path: Path) -> None:
    # A .pdf source with no ASTERISM_DOCLING_URL fails fast with a clear 422 (graceful
    # degrade, like absent pandoc) — synchronously, before any background job starts.
    dataset_id = _save_document_dataset_pdf(tmp_path, "pdfnosc")
    oxi = _RecordingOxi()
    app = build_app(
        _settings_with_docling(tmp_path, None), oxigraph_client=oxi.client, start_watcher=False
    )
    with TestClient(app, headers=_AUTH) as client:
        status, _ = _drive_ingest(client, dataset_id)
        assert status == 422


def test_ingest_pdf_converts_via_sidecar(tmp_path: Path, monkeypatch) -> None:
    # With the sidecar configured, the ingest job converts the PDF (sidecar mocked),
    # emits a "converting" progress phase, structures to sentences, and discloses the
    # conversion (a .conversion sidecar so a snapshot re-ingest reproduces provenance, A7).
    def _fake_convert(pdf_bytes, *, sidecar_url, timeout=600.0):
        assert pdf_bytes == _PDF_BYTES and sidecar_url == "http://docling-test"
        return _JATS_DOC, "docling/2.x (test; ocr=off)"

    monkeypatch.setattr("asterism.documents.convert_pdf_to_jats", _fake_convert)
    dataset_id = _save_document_dataset_pdf(tmp_path, "pdfok")
    oxi = _RecordingOxi()
    app = build_app(
        _settings_with_docling(tmp_path, "http://docling-test"),
        oxigraph_client=oxi.client,
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(client, dataset_id)
        assert status == 202, events
        running = [d for n, d in events if n == "running"]
        assert any(d.get("phase") == "converting" for d in running), running
        assert next(d for n, d in events if n == "done")["result"]["triple_count"] > 10
    sdir = registry.source_dir(tmp_path / "registry", dataset_id)
    conv = json.loads((sdir / "paper.pdf.conversion").read_text(encoding="utf-8"))
    assert conv["sourceFormat"] == "pdf" and conv["converter"].startswith("docling/")


def test_sanitize_document_name_accepts_any_filename() -> None:
    san = _sanitize_document_name
    assert san("ma11040649.pdf") == "ma11040649.pdf"  # already safe — unchanged
    assert san("10+3390__ma11040649.pdf") == "10-3390__ma11040649.pdf"  # '+' → '-'
    assert san("Report (final v2).docx") == "Report-final-v2.docx"  # spaces / parens
    assert san("paper.PDF").endswith(".pdf")  # extension lowercased
    # An all-non-ASCII stem is disambiguated by a deterministic hash of the original
    # name: distinct uploads stay distinct, the same upload stays idempotent.
    a, b = san("会議メモ.pdf"), san("議事録.pdf")
    assert a != b and a == san("会議メモ.pdf")
    # Path traversal / separators never survive — always one safe component.
    for n in ["a/b/c.pdf", "../../etc/passwd.pdf", "   .pdf", "日本語.docx"]:
        assert re.fullmatch(r"[A-Za-z0-9._-]+\.(pdf|docx|xml)", san(n)), n


def test_create_document_accepts_messy_filename(tmp_path: Path) -> None:
    # The friend uploads a document with a human filename (spaces / parens / '+') — it is
    # slugified, not rejected (documents are not RML-referenced). CSV/JSON stay strict.
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "Messy name"},
            files={"files": ("10+3390 (v2).xml", _JATS_DOC.encode(), "application/xml")},
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["dataset_id"]
        assert r.json()["dataset"]["source_kind"] == "xml"
    sdir = registry.source_dir(tmp_path / "registry", dataset_id)
    names = [p.name for p in sdir.iterdir() if p.is_file()]
    assert names and all(
        re.fullmatch(r"[A-Za-z0-9._-]+\.(xml|docx|pdf)(\.conversion)?", n) for n in names
    ), names


def test_create_document_still_rejects_unknown_extension(tmp_path: Path) -> None:
    # Sanitization relaxes the NAME, not the KIND — a non-document extension is still 400.
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "x"},
            files={"files": ("notes.txt", b"hello", "text/plain")},
        )
        assert r.status_code == 400


def test_create_document_dataset_multiple_files(tmp_path: Path) -> None:
    # Select MORE than one document at once → ONE dataset holding both; ingest structures
    # every source (the accumulating "定例ミーティング" model).
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "Two docs"},
            files=[
                ("files", ("a.xml", _JATS_DOC.encode(), "application/xml")),
                ("files", ("b.xml", _JATS_DOC2.encode(), "application/xml")),
            ],
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["dataset_id"]
        assert len(r.json()["source_files"]) == 2
        status, events = _drive_ingest(client, dataset_id)
        assert status == 202, events
        result = next(d for n, d in events if n == "done")["result"]
        assert result["triple_count"] > 20  # both documents structured into one graph


def test_rename_dataset_changes_display_name_only(tmp_path: Path) -> None:
    # Rename touches only the human label; the id (IRI seed / data identity) is immutable.
    dataset_id = _save_document_dataset(tmp_path, "old name")
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dataset_id}/rename", json={"name": "Brand New Name"})
        assert r.status_code == 200, r.text
        assert r.json()["dataset"]["name"] == "Brand New Name"
        assert r.json()["dataset"]["id"] == dataset_id  # id unchanged
        blank = client.post(f"/api/datasets/{dataset_id}/rename", json={"name": "  "})
        assert blank.status_code == 400  # empty name rejected
        missing = client.post("/api/datasets/does-not-exist/rename", json={"name": "x"})
        assert missing.status_code == 404  # unknown dataset
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["name"] == "Brand New Name"


_JATS_DOC2 = (
    '<?xml version="1.0"?><article><front><article-meta>'
    '<article-id pub-id-type="pmcid">PMC-DEMO2</article-id>'
    "<title-group><article-title>Demo Two</article-title></title-group>"
    "</article-meta></front><body>"
    '<sec id="s1"><title>1. Notes</title><p>Second document sentence. Another one here.</p></sec>'
    "</body></article>"
)


def _add_xml_source(tmp: Path, dataset_id: str, filename: str, xml: str) -> None:
    sdir = registry.source_dir(tmp / "registry", dataset_id)
    (sdir / filename).write_text(xml, encoding="utf-8")
    files = sorted(p.name for p in registry.list_source_files(tmp / "registry", dataset_id))
    registry.mark_source_saved(tmp / "registry", dataset_id, files)


def test_ingest_document_multi_source_structures_every_doc(tmp_path: Path) -> None:
    # A document dataset can hold MORE THAN ONE document (a "定例ミーティング" of
    # accumulated minutes); ingest must structure EVERY .xml source so a snapshot
    # re-ingest reproduces the whole feed (consistent with incremental append).
    two_doc = _save_document_dataset(tmp_path, "twodoc")
    _add_xml_source(tmp_path, two_doc, "paper2.xml", _JATS_DOC2)
    solo = _save_document_dataset(tmp_path, "solo")  # single-document baseline
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        _, e1 = _drive_ingest(client, solo)
        solo_triples = next(d for n, d in e1 if n == "done")["result"]["triple_count"]
        status, events = _drive_ingest(client, two_doc)
        assert status == 202, events
        two_triples = next(d for n, d in events if n == "done")["result"]["triple_count"]
    # both documents were structured into the one staged graph → strictly more triples
    assert two_triples > solo_triples


def test_append_document_requires_promoted(tmp_path: Path) -> None:
    # Append grows an already-citable feed: a not-yet-promoted document dataset is 409.
    dataset_id = _save_document_dataset(tmp_path, "notpromoted")
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/documents",
            files={"file": ("paper2.xml", _JATS_DOC2.encode(), "application/xml")},
        )
        assert r.status_code == 409, r.text
    assert oxi.store_calls == []  # nothing merged before the gate


def test_append_document_rejects_non_document_dataset(tmp_path: Path) -> None:
    # A CSV/RML dataset is not a document dataset — appending a document is 400.
    dataset_id = _save_dataset_with_rml(tmp_path)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/documents",
            files={"file": ("paper.xml", _JATS_DOC.encode(), "application/xml")},
        )
        assert r.status_code == 400, r.text


def test_append_document_404_for_missing_dataset(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/does-not-exist/documents",
            files={"file": ("paper.xml", _JATS_DOC.encode(), "application/xml")},
        )
        assert r.status_code == 404, r.text


def test_create_document_rejects_csv(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "nope"},
            files={"files": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert r.status_code == 400


@pytest.mark.skipif(_NO_PANDOC, reason="pandoc not installed")
def test_create_document_dataset_from_word(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    docx = (Path(__file__).resolve().parent / "fixtures" / "sample.docx").read_bytes()
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/documents",
            data={"name": "Sample Agreement"},
            files={"files": ("sample.docx", docx, "application/octet-stream")},
        )
        assert r.status_code == 201, r.text
        meta = r.json()["dataset"]
        assert meta["source_kind"] == "xml"  # converted to JATS
        assert meta["conversion"]["sourceFormat"] == "docx"


@pytest.mark.skipif(_NO_PANDOC, reason="pandoc not installed")
def test_attach_word_docx_converts_to_jats_source(tmp_path: Path) -> None:
    # Attaching a Word .docx converts it to JATS (pandoc), and the resulting .xml
    # becomes the persisted source (source_kind=xml) with the conversion disclosed.
    dataset_id = registry.save_dataset(
        tmp_path / "registry", "worddoc",
        {"diagram.md": "classDiagram\n  class Document"},
        complete=True, warnings=[], traps=[], exit_code=0, created_at="2026-06-12T00:00:00+00:00",
    )["id"]
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    docx = (Path(__file__).resolve().parent / "fixtures" / "sample.docx").read_bytes()
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("sample.docx", docx, "application/octet-stream")},
        )
        assert r.status_code == 200, r.text
        # the converted document then ingests via the structurer (no RML)
        status, events = _drive_ingest(client, dataset_id)
        assert status == 202, events
        result = next(d for n, d in events if n == "done")["result"]
        assert result["triple_count"] > 5
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["source_kind"] == "xml"
    assert meta["conversion"]["sourceFormat"] == "docx"
    assert any(f.endswith(".jats.xml") for f in meta["source_files"])


def test_ingest_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/does-not-exist/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 404


def test_ingest_dataset_without_rml_400(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path, rml="   ")  # blank RML
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 400
        assert "no declarative RML" in r.json()["detail"]
    assert oxi.store_calls == []  # nothing loaded


def test_ingest_without_morph_kgc_errors_in_job(tmp_path: Path, monkeypatch) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)

    def _raise(*_a, **_k):
        raise RuntimeError("morph-kgc is required for substrate ingestion; install ...")

    monkeypatch.setattr(substrate, "materialize_to_nt_file", _raise)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(
            client, dataset_id, {"files": ("papers.csv", b"SID\n1\n", "text/csv")}
        )
        assert status == 202
        err = next((d for n, d in events if n == "error"), None)
        assert err is not None and "morph-kgc" in err["message"]
    assert oxi.store_calls == []  # nothing loaded


# ---- design-time source persistence (Task E) --------------------------------


def test_attach_source_persists_csv_and_flags_meta(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_files"] == ["papers.csv"]
        assert body["dataset"]["has_source"] is True
        assert body["dataset"]["source_files"] == ["papers.csv"]
    # Persisted under <id>/source/ with the exact bytes.
    src = tmp_path / "registry" / dataset_id / "source" / "papers.csv"
    assert src.is_file()
    assert src.read_bytes() == b"SID\n1\n"


def test_source_kind_of_classifies_by_extension() -> None:
    assert registry.source_kind_of(["papers.csv", "samples.csv"]) == "csv"
    assert registry.source_kind_of(["mp.json"]) == "json"
    assert registry.source_kind_of(["a.csv", "b.json"]) == "json"  # any JSON ⇒ json
    assert registry.source_kind_of(["PMC5951533.xml"]) == "xml"  # JATS document source
    assert registry.source_kind_of(["a.csv", "p.xml"]) == "xml"  # any XML ⇒ xml
    assert registry.source_kind_of(["paper.pdf"]) == "xml"  # a PDF is a document source
    assert registry.source_kind_of([]) == "csv"


def test_attach_source_persists_json_and_sets_source_kind(tmp_path: Path) -> None:
    """#19: a JSON source persists and the meta records source_kind="json"."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("mp.json", b'[{"mp_id":"mp-1"}]', "application/json")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_files"] == ["mp.json"]
        assert body["dataset"]["has_source"] is True
        assert body["dataset"]["source_kind"] == "json"
    src = tmp_path / "registry" / dataset_id / "source" / "mp.json"
    assert src.is_file()
    assert src.read_bytes() == b'[{"mp_id":"mp-1"}]'


def test_attach_source_rejects_unsupported_extension(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("data.txt", b"x\n", "text/plain")},
        )
        assert r.status_code == 400


def test_ingest_uses_persisted_json_source(tmp_path: Path, monkeypatch) -> None:
    """#19: a JSON-source dataset ingests from its persisted source (list_source_files
    must pick up .json, not only .csv)."""
    rml = (
        "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
        "<#M> a rr:TriplesMap ;\n"
        '  rml:logicalSource [ rml:source "mp.json" ;'
        ' rml:referenceFormulation ql:JSONPath ; rml:iterator "$[*]" ] ;\n'
        '  rr:subjectMap [ rr:template "https://ex/mat/{mp_id}" ] .\n'
    )
    dataset_id = _save_dataset_with_rml(tmp_path, rml=rml)
    captured: dict[str, Path] = {}

    def _materialize(rml_ttl, source_dir, *, udfs_path=None, work_dir=None) -> Path:
        captured["source_dir"] = Path(source_dir)
        out = Path(work_dir) / "out.nt"
        out.write_bytes(b'<https://ex/mat/mp-1> <https://ex/p> "x" .\n')
        return out

    monkeypatch.setattr(substrate, "materialize_to_nt_file", _materialize)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        assert (
            client.post(
                f"/api/datasets/{dataset_id}/source",
                files={"files": ("mp.json", b'[{"mp_id":"mp-1"}]', "application/json")},
            ).status_code
            == 200
        )
        status, events = _drive_ingest(client, dataset_id)
        assert status == 202
        result = next(d for n, d in events if n == "done")["result"]
        assert result["triple_count"] == 1
        assert result["graph_kind"] == "staged"
    # the persisted JSON resolved as the source dir handed to morph-kgc
    assert (captured["source_dir"] / "mp.json").is_file()


def test_attach_source_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/nope-00000000/source",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 404


def test_ingest_uses_persisted_source_when_no_upload(tmp_path: Path, monkeypatch) -> None:
    """Task E: a design-stage dataset with persisted source ingests with no re-attach."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        assert (
            client.post(
                f"/api/datasets/{dataset_id}/source",
                files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
            ).status_code
            == 200
        )
        # Ingest with NO files uploaded — the persisted source is used.
        status, events = _drive_ingest(client, dataset_id)
        assert status == 202
        result = next(d for n, d in events if n == "done")["result"]
        assert result["triple_count"] == 1
        assert result["graph_kind"] == "staged"
    assert oxi.store_calls == [substrate.versioned_graph_iri(dataset_id, 1)]


def test_ingest_upload_persists_source_for_reuse(tmp_path: Path, monkeypatch) -> None:
    """An ingest WITH an upload also persists that CSV as the dataset's source."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(
            client, dataset_id, {"files": ("papers.csv", b"SID\n1\n", "text/csv")}
        )
        assert status == 202
        assert "done" in [n for n, _ in events], events
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["has_source"] is True
    assert meta["source_files"] == ["papers.csv"]
    assert (tmp_path / "registry" / dataset_id / "source" / "papers.csv").is_file()


def test_ingest_without_upload_or_source_400(tmp_path: Path) -> None:
    """No upload and no persisted source -> 400 (nothing to ingest from)."""
    dataset_id = _save_dataset_with_rml(tmp_path)  # RML present, no source attached
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dataset_id}/ingest")
        assert r.status_code == 400
        assert "CSV" in r.json()["detail"]
    assert oxi.store_calls == []


def test_ingest_stream_failure_errors_and_drops_staged(tmp_path: Path, monkeypatch) -> None:
    """A streaming-load failure surfaces as a job error and drops the partial graph (D6)."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=3))

    async def _boom(*_a, **_k):
        raise httpx.ConnectError("oxigraph down")

    monkeypatch.setattr(substrate, "stream_nt_file_to_oxigraph", _boom)
    # D6 reclaims the partial via a memory-safe chunked delete; spy on it (the
    # recording client can't model the ASK-until-empty loop).
    reclaimed: list[str] = []

    async def _spy_chunked(_client, graph_iri, **_k):
        reclaimed.append(graph_iri)
        return 0

    monkeypatch.setattr(substrate, "chunked_drop_graph", _spy_chunked)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    graph_iri = substrate.versioned_graph_iri(dataset_id, 1)
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(
            client, dataset_id, {"files": ("papers.csv", b"SID\n1\n", "text/csv")}
        )
        assert status == 202
        err = next((d for n, d in events if n == "error"), None)
        assert err is not None
        assert "ConnectError" in err["message"] or "oxigraph down" in err["message"]
    # D6: the partial staged version graph was reclaimed (it was never live).
    assert reclaimed == [graph_iri]


# ---- promotion: draft -> canonical (#15 S4) ---------------------------------


class _PromoteOxi:
    """Oxigraph fake for promote/alignment: canned SELECTs + records /update."""

    def __init__(self) -> None:
        self.updates: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/update":
                body = request.content.decode()
                self.updates.append(body)
                return httpx.Response(204)
            # /query: alignment SELECTs and the promote COUNT
            q = request.content.decode()
            if "COUNT" in q and "GRAPH" not in q:
                # The #20 FROM-merge startup migration's default-graph count.
                # Default graph is empty in these tests -> migration is a no-op.
                rows = [{"c": {"value": "0"}}]
            elif "COUNT" in q:
                rows = [{"c": {"value": "1640"}}]
            elif "?__cg" in q:  # canonical-scope side (default + canonical/*) — empty
                rows = []
            elif "GRAPH <" in q:  # draft side names a graph literally
                rows = [{"x": {"type": "uri", "value": "https://ex#draftProp"}}]
            else:
                rows = []
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _ingested_dataset(tmp: Path) -> str:
    dataset_id = _save_dataset_with_rml(tmp)
    # part5: ingest stages into a fresh version graph (v1 for the first ingest).
    registry.mark_ingested(
        tmp / "registry",
        dataset_id,
        graph_iri=substrate.versioned_graph_iri(dataset_id, 1),
        triple_count=1640,
        ingested_at="2026-06-03T00:10:00+00:00",
        data_seq=1,
    )
    return dataset_id


_TBOX_RML = (
    "@prefix sd: <https://kumagallium.github.io/asterism/starrydata/ontology#> .\n"
    "@prefix sdr: <https://kumagallium.github.io/asterism/starrydata/resource/> .\n"
    "@prefix schema: <https://schema.org/> .\n"
)
_TBOX_MODEL = (
    "- Paper <https://ex/paper/1>:\n"
    "    - a: sd:Paper\n"
    "    - schema:name?:\n"
    "        - title: \"t\"\n"
    "- Sample <https://ex/sample/1>:\n"
    "    - a: sd:Sample\n"
    "    - sd:fromPaper:\n"
    "        - sp: Paper\n"
)


class _ProjectOxi:
    """Promote fake that also records /store POSTs (for ontology projection)."""

    def __init__(self) -> None:
        self.updates: list[str] = []
        self.stores: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/update":
                self.updates.append(request.content.decode())
                return httpx.Response(204)
            if request.url.path == "/store":
                self.stores.append(request.url.params.get("graph"))
                return httpx.Response(204)
            q = request.content.decode()
            if "COUNT" in q and "GRAPH" not in q:
                rows = [{"c": {"value": "0"}}]  # migration default-count
            elif "COUNT" in q:
                rows = [{"c": {"value": "1640"}}]
            else:
                rows = []
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def test_promote_projects_tbox_into_ontology_graph(tmp_path: Path) -> None:
    # A dataset with a real model.yaml -> promote projects RDFS into ontology/{id}.
    dataset_id = registry.save_dataset(
        tmp_path / "registry",
        "tbox",
        {
            "diagram.md": "classDiagram\n  class Paper",
            "model.yaml": _TBOX_MODEL,
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": _TBOX_RML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-05T00:00:00+00:00",
    )["id"]
    registry.mark_ingested(
        tmp_path / "registry",
        dataset_id,
        graph_iri=substrate.versioned_graph_iri(dataset_id, 1),
        triple_count=1640,
        ingested_at="2026-06-05T00:10:00+00:00",
        data_seq=1,
    )
    oxi = _ProjectOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.post(f"/api/datasets/{dataset_id}/promote").json()
    ontology_iri = f"https://kumagallium.github.io/asterism/graph/ontology/{dataset_id}"
    assert body["ontology_graph"] == ontology_iri
    assert body["ontology_triples"] > 0  # TBox projected
    # The ontology graph was replaced (DROP) then loaded (POST /store?graph=...).
    assert any("DROP" in u and ontology_iri in u for u in oxi.updates)
    assert ontology_iri in oxi.stores


def test_alignment_preview_classifies_draft(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{dataset_id}/alignment")
        assert r.status_code == 200, r.text
        al = r.json()["alignment"]
        # draft uses a predicate not in (empty) canonical -> New
        assert al["predicates"]["new"] == ["https://ex#draftProp"]
        assert al["predicates"]["reuse"] == []


def test_promote_flags_canonical_and_marks_meta(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    expected_canon = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}"
    expected_live = f"{expected_canon}/v1"
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dataset_id}/promote")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["promoted"] is True
        assert body["canonical_graph"] == expected_canon
        # part5: the live version graph holding the data.
        assert body["live_graph"] == expected_live
        # Triple count is read from the ingest meta (not a COUNT of the graph).
        assert body["triples_promoted"] == 1640
        assert body["dataset"]["promoted"] is True
        assert body["dataset"]["ingested"] is False
    # Memory-bounded + part5: control-graph writes only — NEVER a MOVE/DROP of data.
    assert not any("MOVE GRAPH" in u for u in oxi.updates)
    assert any(
        "INSERT DATA" in u and '"promoted"' in u and expected_canon in u for u in oxi.updates
    )
    # The live pointer is set to the version graph.
    assert any(
        "INSERT DATA" in u and "liveGraph" in u and expected_live in u for u in oxi.updates
    )
    # Meta on disk reflects promotion.
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["promoted"] is True
    assert meta["triples_promoted"] == 1640
    assert meta["canonical_graph"] == expected_canon
    assert meta["live_graph"] == expected_live
    # #20 P3: first promotion is version 1 with one entry in the version log.
    assert meta["version"] == 1
    assert len(meta["versions"]) == 1
    assert meta["versions"][0]["version"] == 1
    assert meta["versions"][0]["triples_promoted"] == 1640


def test_mark_promoted_bumps_version_on_repromote(tmp_path: Path) -> None:
    """#20 P3: re-promoting the same dataset bumps a monotonic version + logs it."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    root = tmp_path / "registry"
    align = {"predicates": {"reuse": [], "new": []}, "classes": {"reuse": [], "new": []}}

    m1 = registry.mark_promoted(
        root, dataset_id, triples_promoted=100, alignment=align, promoted_at="2026-01-01T00:00:00"
    )
    assert m1 is not None and m1["version"] == 1 and len(m1["versions"]) == 1

    m2 = registry.mark_promoted(
        root, dataset_id, triples_promoted=120, alignment=align, promoted_at="2026-01-02T00:00:00"
    )
    assert m2 is not None and m2["version"] == 2
    # Append-only log keeps both promotions, in order.
    assert [v["version"] for v in m2["versions"]] == [1, 2]
    assert m2["versions"][1]["triples_promoted"] == 120


def test_promote_requires_ingested_draft(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)  # has RML but never ingested
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dataset_id}/promote")
        assert r.status_code == 400
    assert oxi.updates == []  # nothing moved


def test_ingest_morph_kgc_error_surfaces_in_job(tmp_path: Path, monkeypatch) -> None:
    # A Morph-KGC failure on malformed/unsupported RML must surface as a job error
    # event with the cause — not an opaque crash.
    dataset_id = _save_dataset_with_rml(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("Morph-KGC materialization failed (exit 1): KeyError")

    monkeypatch.setattr(substrate, "materialize_to_nt_file", _boom)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        status, events = _drive_ingest(
            client, dataset_id, {"files": ("papers.csv", b"SID\n1\n", "text/csv")}
        )
        assert status == 202
        err = next((d for n, d in events if n == "error"), None)
        assert err is not None and "Morph-KGC materialization failed" in err["message"]
    assert oxi.store_calls == []  # nothing loaded


def test_retract_then_reinstate_roundtrip(tmp_path: Path) -> None:
    """#20 P3 step3: retract tombstones the canonical graph; reinstate clears it."""
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    canon = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}"
    with TestClient(app, headers=_AUTH) as client:
        assert client.post(f"/api/datasets/{dataset_id}/promote").status_code == 200
        r = client.post(f"/api/datasets/{dataset_id}/retract")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "retracted"
        assert r.json()["dataset"]["status"] == "retracted"
        r2 = client.post(f"/api/datasets/{dataset_id}/reinstate")
        assert r2.status_code == 200
        assert r2.json()["status"] == "active"
    # retract wrote a control-graph tombstone for the canonical graph...
    assert any("INSERT DATA" in u and '"retracted"' in u and canon in u for u in oxi.updates)
    # ...and meta ends active again after reinstate.
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["status"] == "active"


def test_retract_requires_promoted(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)  # designed but never promoted
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dataset_id}/retract")
        assert r.status_code == 400
    assert oxi.updates == []  # nothing tombstoned


def test_delete_staged_only_dataset_no_force(tmp_path: Path) -> None:
    """A never-promoted (design/staged) dataset deletes freely; registry dir removed."""
    dataset_id = _ingested_dataset(tmp_path)  # ingested (staged), not promoted
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    staged = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}/v1"
    with TestClient(app, headers=_AUTH) as client:
        r = client.delete(f"/api/datasets/{dataset_id}")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True
        assert r.json()["was_promoted"] is False
    # part5: the staged version graph is ENQUEUED for a background drop (off the
    # request path), not dropped synchronously; the registry dir is gone now.
    assert any(
        "INSERT DATA" in u and "pendingDrop" in u and staged in u for u in oxi.updates
    )
    assert not (tmp_path / "registry" / dataset_id).exists()


def test_delete_promoted_requires_force(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        assert client.post(f"/api/datasets/{dataset_id}/promote").status_code == 200
        r = client.delete(f"/api/datasets/{dataset_id}")  # no force
        assert r.status_code == 409
    # nothing dropped, registry dir still present
    assert not any("DROP SILENT GRAPH" in u for u in oxi.updates)
    assert (tmp_path / "registry" / dataset_id).exists()


def test_delete_promoted_with_force_enqueues_drop_and_tombstones(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    canon = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}"
    with TestClient(app, headers=_AUTH) as client:
        assert client.post(f"/api/datasets/{dataset_id}/promote").status_code == 200
        r = client.delete(f"/api/datasets/{dataset_id}?force=true")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True and r.json()["was_promoted"] is True
    # part5: the data graph is ENQUEUED for a background drop (no synchronous DROP on
    # the request path); a deleted tombstone is left for dangling citations.
    assert any("INSERT DATA" in u and "pendingDrop" in u for u in oxi.updates)
    assert any("INSERT DATA" in u and '"deleted"' in u and canon in u for u in oxi.updates)
    assert not (tmp_path / "registry" / dataset_id).exists()


def test_delete_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        assert client.delete("/api/datasets/nope-00000000").status_code == 404


# ---- incremental append: grow a live feed (ADR incremental-ingest.md) --------


class _FeedOxi:
    """Oxigraph fake for append: records /store POSTs and answers the liveGraph
    SELECT with a fixed pointer, so the append resolves and targets the live graph."""

    def __init__(self, live_graph: str) -> None:
        self.stores: list[str | None] = []
        self._live = live_graph

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store":
                self.stores.append(request.url.params.get("graph"))
                return httpx.Response(204)
            if request.url.path == "/update":
                return httpx.Response(204)
            q = request.content.decode()
            rows = (
                [{"o": {"type": "uri", "value": self._live}}] if "liveGraph" in q else []
            )
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _promoted_feed_dataset(tmp: Path) -> tuple[str, str]:
    """A dataset ingested + promoted (a live feed to append to). Returns (id, live_graph).

    Its design-time source (papers.csv) is persisted so the append can accumulate the
    batch into it (the snapshot-reproducibility path, A7).
    """
    dataset_id = _save_dataset_with_rml(tmp)
    live = substrate.versioned_graph_iri(dataset_id, 1)
    sdir = tmp / "registry" / dataset_id / "source"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "papers.csv").write_bytes(b"SID\n1\n")
    registry.mark_source_saved(tmp / "registry", dataset_id, ["papers.csv"])
    registry.mark_ingested(
        tmp / "registry",
        dataset_id,
        graph_iri=live,
        triple_count=1,
        ingested_at="2026-06-10T00:00:00+00:00",
        data_seq=1,
    )
    registry.mark_promoted(
        tmp / "registry",
        dataset_id,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-06-10T00:01:00+00:00",
        canonical_graph=substrate.canonical_graph_iri(dataset_id),
        live_graph=live,
    )
    return dataset_id, live


def test_append_grows_live_feed_and_records_meta(tmp_path: Path, monkeypatch) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.csv", b"SID\n2\n3\n", "text/csv")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # The batch was POST-merged into the LIVE graph — not a new version graph.
    assert body["live_graph"] == live
    assert live in oxi.stores
    assert substrate.versioned_graph_iri(dataset_id, 2) not in oxi.stores
    assert body["triples_in_batch"] == 2
    assert body["append_seq"] == 1
    # This dataset is not a crosswalk participant, so the hub is NOT stale (the flag is
    # participation-accurate now, not hardcoded). A participant case is covered in
    # test_crosswalk_api.py (append -> crosswalk_stale True + a debounced rebuild).
    assert body["crosswalk_stale"] is False
    meta = body["dataset"]
    assert meta["feed"] is True
    assert meta["append_seq"] == 1
    assert meta["appends"][0]["seq"] == 1
    assert meta["appends"][0]["triples_in_batch"] == 2
    assert meta["appends"][0]["batch_files"] == ["papers.csv"]
    # triple_count advances by the batch (1 promoted + 2 appended); promoted untouched.
    assert meta["triple_count"] == 3
    assert meta["promoted"] is True
    # The batch was accumulated into the persisted source (header deduped) so a later
    # snapshot re-ingest reproduces the whole feed.
    src = (tmp_path / "registry" / dataset_id / "source" / "papers.csv").read_text()
    assert src.splitlines() == ["SID", "1", "2", "3"]


def test_append_second_batch_bumps_seq(tmp_path: Path, monkeypatch) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        b1 = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.csv", b"SID\n2\n", "text/csv")},
        ).json()
        b2 = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.csv", b"SID\n3\n", "text/csv")},
        ).json()
    assert b1["append_seq"] == 1
    assert b2["append_seq"] == 2
    assert [a["seq"] for a in b2["dataset"]["appends"]] == [1, 2]
    assert b2["dataset"]["triple_count"] == 3  # 1 promoted + 1 + 1


def test_append_same_batch_twice_is_idempotent_replay(tmp_path: Path, monkeypatch) -> None:
    """Server success → client timeout → retry with the SAME batch is a no-op: the
    persisted source is not double-accumulated (else a later snapshot re-ingest would
    re-materialize duplicate rows), the seq/counters do not bump, and no second
    appends-log entry is recorded (incremental-ingest §3 / A3). The response flags the
    replay so the caller can tell it apart from a fresh append."""
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    payload = {"files": ("papers.csv", b"SID\n2\n3\n", "text/csv")}
    with TestClient(app, headers=_AUTH) as client:
        first = client.post(f"/api/datasets/{dataset_id}/append", files=payload).json()
        second = client.post(f"/api/datasets/{dataset_id}/append", files=payload).json()

    assert first["idempotent_replay"] is False
    assert first["append_seq"] == 1
    # The retry is recognised as the same batch and short-circuited.
    assert second["idempotent_replay"] is True
    assert second["append_seq"] == 1  # NOT 2 — seq did not advance
    assert second["triples_in_batch"] == 2  # the original outcome is echoed back
    meta = second["dataset"]
    assert meta["append_seq"] == 1
    assert len(meta["appends"]) == 1  # exactly one recorded append, not two
    assert meta["triple_count"] == 3  # 1 promoted + 2 appended, not double-counted
    # The persisted source has the batch's rows exactly once (header deduped).
    src = (tmp_path / "registry" / dataset_id / "source" / "papers.csv").read_text()
    assert src.splitlines() == ["SID", "1", "2", "3"]


def test_accumulate_batch_sources_is_idempotent_via_marker(tmp_path: Path) -> None:
    """The source-accumulation guard (``_accumulate_batch_sources``): folding the SAME
    batch twice appends its rows once. Covers a FAILED attempt that accumulated the
    source before erroring — not short-circuited by the append log — where the retry
    must not double the rows a later snapshot re-ingest would read."""
    from asterism_api.main import _accumulate_batch_sources

    sdir = tmp_path / "source"
    sdir.mkdir()
    (sdir / "papers.csv").write_bytes(b"SID\n1\n")
    batch = [("papers.csv", b"SID\n2\n3\n")]
    batch_id = substrate.batch_fingerprint(batch)

    _accumulate_batch_sources(sdir, batch, batch_id)
    _accumulate_batch_sources(sdir, batch, batch_id)  # retry — must be a no-op
    assert (sdir / "papers.csv").read_text().splitlines() == ["SID", "1", "2", "3"]

    # The marker is recorded, and it lives in a hidden dir — not a *.csv/*.json source
    # file — so the source listing (suffix-filtered, non-recursive) never sees it.
    assert (sdir / ".applied_batches" / batch_id).exists()
    assert [p.name for p in sdir.iterdir() if p.is_file() and p.suffix == ".csv"] == [
        "papers.csv"
    ]

    # A genuinely different batch still accumulates.
    batch2 = [("papers.csv", b"SID\n4\n")]
    _accumulate_batch_sources(sdir, batch2, substrate.batch_fingerprint(batch2))
    assert (sdir / "papers.csv").read_text().splitlines() == ["SID", "1", "2", "3", "4"]


def test_append_requires_promoted_409(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)  # designed, never promoted
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.csv", b"SID\n2\n", "text/csv")},
        )
    assert r.status_code == 409
    assert "live canonical graph" in r.json()["detail"]


def test_append_rejects_mismatched_batch_name_400(tmp_path: Path) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("other.csv", b"X\n1\n", "text/csv")},
        )
    assert r.status_code == 400
    assert "does not match any rml:source" in r.json()["detail"]


def test_append_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/nope-00000000/append",
            files={"files": ("papers.csv", b"SID\n2\n", "text/csv")},
        )
    assert r.status_code == 404


# ---- per-dataset append watcher (drop a CSV -> grow the live feed, ADR §6) ----


async def _events_once(pairs):
    """Yield one watchfiles change set, then end (the loop returns when exhausted)."""
    yield set(pairs)


def test_append_watch_loop_consumes_drop_and_appends(tmp_path: Path, monkeypatch) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    oxi = _FeedOxi(live)
    settings = _settings(tmp_path)
    drop = settings.append_drop_root / dataset_id
    drop.mkdir(parents=True)
    f = drop / "papers.csv"
    f.write_bytes(b"SID\n2\n3\n")

    asyncio.run(
        _append_watch_loop(
            settings,
            oxi.client,
            asyncio.Event(),
            events_source=_events_once([(Change.added, str(f))]),
        )
    )

    # The drop file was consumed (deleted), the batch POST-merged into the live graph,
    # and the append recorded on the dataset meta.
    assert not f.exists()
    assert live in oxi.stores
    meta = registry.load_dataset(settings.registry_root, dataset_id)["meta"]
    assert meta["feed"] is True
    assert meta["append_seq"] == 1
    # An "append" ok entry was logged to the jobs log.
    rec = json.loads((tmp_path / "jobs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["kind"] == "append"
    assert rec["status"] == "ok"
    assert rec["triples_in_batch"] == 2


def test_append_watch_loop_quarantines_failed_drop(tmp_path: Path) -> None:
    # A designed-but-never-promoted dataset -> AppendError(409) -> the drop is moved
    # aside under .error/ (not deleted) and logged as an error.
    dataset_id = _save_dataset_with_rml(tmp_path)
    oxi = _RecordingOxi()
    settings = _settings(tmp_path)
    drop = settings.append_drop_root / dataset_id
    drop.mkdir(parents=True)
    f = drop / "papers.csv"
    f.write_bytes(b"SID\n2\n")

    asyncio.run(
        _append_watch_loop(
            settings,
            oxi.client,
            asyncio.Event(),
            events_source=_events_once([(Change.added, str(f))]),
        )
    )

    assert not f.exists()  # moved, not left in place
    assert (settings.append_drop_root / dataset_id / ".error" / "papers.csv").exists()
    rec = json.loads((tmp_path / "jobs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["kind"] == "append"
    assert rec["status"] == "error"
