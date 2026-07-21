"""Excel (.xlsx) entrance tests (kantan-mode K6).

An .xlsx upload is converted to CSV at the api's tabular entrances (openpyxl,
1 sheet = 1 CSV, deterministic — ``asterism.tabularize.xlsx_to_csvs``), so
everything downstream — inspect, the design loop, ``rml:source``, ingest and
append — only ever sees the derived ``.csv`` names (the rml_safety source
allow-list is unchanged). Source-attach keeps the original workbook alongside
the derived CSVs with a ``.conversion`` sidecar + meta record disclosing the
conversion (the docx model). Append accepts a SINGLE-sheet workbook only — a
multi-sheet batch is ambiguous and refused with a clear 400.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, _sanitize_tabular_name, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def _healthy_client() -> OxigraphClient:
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


def _xlsx_bytes(sheets: dict[str, list[list[object]]]) -> bytes:
    """Build a small in-memory workbook (no binary fixture committed)."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title=title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_RML = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] .\n'
)


def _save_dataset_with_rml(tmp: Path) -> str:
    return registry.save_dataset(
        tmp / "registry",
        "demo",
        {
            "diagram.md": "classDiagram\n  class Paper",
            "model.yaml": "- Paper:",
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": _RML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-21T00:00:00+00:00",
    )["id"]


# ---- filename sanitizer: .xlsx is a tabular extension now ---------------------


def test_sanitize_tabular_name_accepts_xlsx() -> None:
    assert _sanitize_tabular_name("book.xlsx") == "book.xlsx"
    slugged = _sanitize_tabular_name("実験データ.xlsx")
    assert slugged.endswith(".xlsx")
    assert slugged == _sanitize_tabular_name("実験データ.xlsx")  # deterministic


# ---- /api/inspect: an .xlsx expands to derived CSV names ----------------------


def test_inspect_xlsx_multi_sheet_returns_derived_csv_names(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    data = _xlsx_bytes(
        {"Papers": [["SID", "name"], [1, "a"], [2, "b"]], "Samples": [["sid"], [7]]}
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/inspect", files={"files": ("book.xlsx", data, _XLSX_MIME)})
    assert r.status_code == 200, r.text
    # The client learns the CSV names the design's rml:source must use — never .xlsx.
    assert r.headers["X-Asterism-Source-Names"] == "book__Papers.csv,book__Samples.csv"
    assert "## CSV: book__Papers.csv" in r.text
    assert "## CSV: book__Samples.csv" in r.text
    # Derived CSVs are clean UTF-8 comma CSV → no dialect surfaces.
    assert r.headers["X-Asterism-Dialects"] == "{}"


def test_inspect_xlsx_single_sheet_keeps_stem(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    data = _xlsx_bytes({"Sheet1": [["SID"], [1]]})
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/inspect", files={"files": ("papers.xlsx", data, _XLSX_MIME)})
    assert r.status_code == 200, r.text
    assert r.headers["X-Asterism-Source-Names"] == "papers.csv"


def test_inspect_corrupt_xlsx_is_readable_422(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect", files={"files": ("bad.xlsx", b"this is not a zip", _XLSX_MIME)}
        )
    assert r.status_code == 422
    assert "Excel" in r.json()["detail"]


# ---- source attach: derived CSVs persisted + original kept + conversion -------


def test_attach_source_xlsx_persists_csv_original_and_sidecar(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    data = _xlsx_bytes({"Sheet1": [["SID"], [1], [2]]})
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("papers.xlsx", data, _XLSX_MIME)},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # The persisted SOURCE (what the RML maps) is the derived CSV, not the workbook.
    assert body["source_files"] == ["papers.csv"]
    meta = body["dataset"]
    assert meta["has_source"] is True
    assert meta["source_kind"] == "csv"  # never misclassified as xml/json
    assert meta["conversion"]["tool"] == "openpyxl"
    assert meta["conversion"]["from"] == "papers.xlsx"
    assert meta["conversion"]["version"]  # the installed openpyxl version is pinned
    sdir = tmp_path / "registry" / dataset_id / "source"
    assert (sdir / "papers.csv").read_text().splitlines() == ["SID", "1", "2"]
    assert (sdir / "papers.xlsx").read_bytes() == data  # original kept alongside
    sidecar = json.loads((sdir / "papers.csv.conversion").read_text())
    assert sidecar["tool"] == "openpyxl" and sidecar["from"] == "papers.xlsx"
    # Both the derived CSV and the original are listed; the sidecar never is.
    listed = [p.name for p in registry.list_source_files(tmp_path / "registry", dataset_id)]
    assert listed == ["papers.csv", "papers.xlsx"]


def test_attach_source_xlsx_multi_sheet_persists_every_csv(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    data = _xlsx_bytes({"Papers": [["SID"], [1]], "Samples": [["sid"], [2]]})
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("book.xlsx", data, _XLSX_MIME)},
        )
    assert r.status_code == 200, r.text
    assert r.json()["source_files"] == ["book__Papers.csv", "book__Samples.csv"]
    sdir = tmp_path / "registry" / dataset_id / "source"
    for name in ("book__Papers.csv", "book__Samples.csv", "book.xlsx"):
        assert (sdir / name).is_file(), name


# ---- append: single-sheet appends as its CSV, multi-sheet is refused ----------


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
    """A dataset ingested + promoted (a live feed to append to); see test_ingest.py."""
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
        ingested_at="2026-07-21T00:00:00+00:00",
        data_seq=1,
    )
    registry.mark_promoted(
        tmp / "registry",
        dataset_id,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-07-21T00:01:00+00:00",
        canonical_graph=substrate.canonical_graph_iri(dataset_id),
        live_graph=live,
    )
    return dataset_id, live


def _fake_nt_materializer(*, triples: int = 1):
    def _materialize(
        rml_ttl, csv_dir, *, udfs_path=None, work_dir=None, run_id=None, should_cancel=None
    ) -> Path:
        out = Path(work_dir) / "out.nt"
        out.write_bytes(
            b"".join(
                f'<https://ex/paper/{i}> <https://schema.org/name> "p{i}" .\n'.encode()
                for i in range(triples)
            )
        )
        return out

    return _materialize


def test_append_multi_sheet_xlsx_refused_400(tmp_path: Path) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    data = _xlsx_bytes({"Papers": [["SID"], [2]], "Extra": [["X"], [9]]})
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.xlsx", data, _XLSX_MIME)},
        )
    assert r.status_code == 400
    assert "シートを 1 つ" in r.json()["detail"]
    assert oxi.stores == []  # nothing was merged into the live graph
    # The persisted source was not touched by the refused batch.
    src = (tmp_path / "registry" / dataset_id / "source" / "papers.csv").read_text()
    assert src.splitlines() == ["SID", "1"]


def test_append_single_sheet_xlsx_appends_as_derived_csv(tmp_path: Path, monkeypatch) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    data = _xlsx_bytes({"Sheet1": [["SID"], [2], [3]]})
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.xlsx", data, _XLSX_MIME)},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["live_graph"] == live and live in oxi.stores
    assert body["triples_in_batch"] == 2
    # The batch is recorded (and accumulated) under its DERIVED csv name — the
    # single-sheet workbook converts to exactly the rml:source the design pinned.
    assert body["dataset"]["appends"][0]["batch_files"] == ["papers.csv"]
    src = (tmp_path / "registry" / dataset_id / "source" / "papers.csv").read_text()
    assert src.splitlines() == ["SID", "1", "2", "3"]
