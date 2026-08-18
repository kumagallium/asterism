"""What the wizard shows a person about their OWN file, and what they may fix.

Three kantan-tier gaps, all of them "the screen exists but has nothing in it":

* **KZ-A-08** — the persona's main format is Excel, which no browser can parse,
  so the "read check" screen showed a bare filename card and the meaning screen's
  example column stayed empty. The server has read the file; ``/api/inspect``
  now hands back what it saw.
* **KZ-A-09** — one workbook becomes one table per sheet, so the chart sheet and
  the notes sheet were handed to the AI as data (K6 says ask which to use).
* **KZ-B-25 / KZ-B-05** — the meaning screen's evidence came from a preview this
  browser happened to parse (blank on a review reopened from the catalog), and a
  wrong meaning or unit could only be fixed by an LLM rewrite of the whole design.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from asterism_api import staging
from asterism_api.main import build_app
from tests.test_main import (  # noqa: F401  (healthy_client is a fixture)
    _AUTH,
    _FIX_RECIPE_MD,
    _settings,
    healthy_client,
)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_READINGS = b"reading_id,channel,amplitude\nr1,A,1.5\nr2,B,2.5\nr3,C,3.5\n"


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def _xlsx_bytes(sheets: dict[str, list[list[object]]]) -> bytes:
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


def _samples(response) -> dict:
    return json.loads(response.headers["X-Asterism-Samples"])


# ---------------------------------------------------------------------------
# KZ-A-08 — the server's reading of a file the browser cannot open
# ---------------------------------------------------------------------------


def test_inspect_returns_example_values_per_column(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.post("/api/inspect", files={"files": ("readings.csv", _READINGS, "text/csv")})
        assert r.status_code == 200, r.text
        assert _samples(r) == {
            "readings.csv": {
                "reading_id": ["r1", "r2", "r3"],
                "channel": ["A", "B", "C"],
                "amplitude": ["1.5", "2.5", "3.5"],
            }
        }


def test_inspect_answers_for_an_xlsx_the_browser_cannot_parse(
    tmp_path: Path, healthy_client
) -> None:
    """The whole point of KZ-A-08: .xlsx has no client-side preview at all."""
    data = _xlsx_bytes({"Data": [["id", "zt"], ["s1", 0.9], ["s2", 1.2]]})
    with _client(tmp_path, healthy_client) as client:
        r = client.post("/api/inspect", files={"files": ("book.xlsx", data, _XLSX_MIME)})
        assert r.status_code == 200, r.text
        # The derived CSV name is what the design will reference.
        assert r.headers["X-Asterism-Source-Names"] == "book.csv"
        assert _samples(r)["book.csv"] == {"id": ["s1", "s2"], "zt": ["0.9", "1.2"]}
        # One sheet → nothing to ask about (K6 asks only when there is a choice).
        assert "X-Asterism-Sheets" not in r.headers


def test_sample_values_are_capped_so_the_header_stays_small(
    tmp_path: Path, healthy_client
) -> None:
    long_cell = "x" * 500
    csv = f"note\n{long_cell}\n".encode()
    with _client(tmp_path, healthy_client) as client:
        r = client.post("/api/inspect", files={"files": ("n.csv", csv, "text/csv")})
        (value,) = _samples(r)["n.csv"]["note"]
        assert len(value) < 100 and value.endswith("…")


# ---------------------------------------------------------------------------
# KZ-A-09 — "which sheet do you want to use?"
# ---------------------------------------------------------------------------


def test_multi_sheet_workbook_offers_its_sheets_by_their_real_titles(
    tmp_path: Path, healthy_client
) -> None:
    data = _xlsx_bytes(
        {
            "測定結果": [["id", "zt"], ["s1", 0.9]],
            "Chart": [["note"], ["ignore me"]],
        }
    )
    with _client(tmp_path, healthy_client) as client:
        sid = client.post(
            "/api/staging", files={"files": ("book.xlsx", data, _XLSX_MIME)}
        ).json()["staging_id"]
        r = client.post("/api/inspect", data={"staging_id": sid})
        sheets = json.loads(r.headers["X-Asterism-Sheets"])
        assert len(sheets) == 2
        # The hashed slug is unreadable; the worksheet title is the question.
        assert sorted(v["sheet"] for v in sheets.values()) == ["Chart", "測定結果"]
        assert all(v["from"] == "book.xlsx" for v in sheets.values())


def test_choosing_sheets_narrows_the_design_and_the_persisted_source(
    tmp_path: Path, healthy_client
) -> None:
    """The choice has to reach BOTH the design calls and the attach — a control
    that changes what the AI sees but not what is stored would be a lie."""
    data = _xlsx_bytes(
        {"Data": [["id", "zt"], ["s1", 0.9]], "Notes": [["memo"], ["scratch"]]}
    )
    with _client(tmp_path, healthy_client) as client:
        sid = client.post(
            "/api/staging", files={"files": ("book.xlsx", data, _XLSX_MIME)}
        ).json()["staging_id"]
        wanted = "book__Data.csv"
        r = client.post(f"/api/staging/{sid}/sources", json={"sources": [wanted]})
        assert r.status_code == 200, r.text
        assert r.json()["sources"] == [wanted]
        # Every later design call reads only the chosen table…
        r = client.post("/api/inspect", data={"staging_id": sid})
        assert r.headers["X-Asterism-Source-Names"] == wanted
        assert list(_samples(r)) == [wanted]
        # …and so does the save.
        ds_id = client.post(
            "/api/materialize", json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor"}
        ).json()["dataset"]["id"]
        saved = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid})
        assert saved.status_code == 200, saved.text
        assert saved.json()["source_files"] == [wanted]
        source = tmp_path / "registry" / ds_id / "source"
        assert not (source / "book__Notes.csv").exists()
        assert (source / "book.xlsx").is_file()  # the original is still kept


def test_an_unknown_or_empty_selection_never_empties_the_record(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = client.post(
            "/api/staging", files={"files": ("a.csv", _READINGS, "text/csv")}
        ).json()["staging_id"]
        assert client.post(f"/api/staging/{sid}/sources", json={"sources": []}).status_code == 422
        r = client.post(f"/api/staging/{sid}/sources", json={"sources": ["../etc/passwd"]})
        assert r.status_code == 422
        assert client.get(f"/api/staging/{sid}").json()["sources"] == ["a.csv"]


# ---------------------------------------------------------------------------
# KZ-B-25 — the meaning screen's evidence, from the dataset's own source
# ---------------------------------------------------------------------------


def _dataset_with_source(client: TestClient) -> str:
    ds_id = client.post(
        "/api/materialize", json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor"}
    ).json()["dataset"]["id"]
    r = client.post(
        f"/api/datasets/{ds_id}/source",
        files={"files": ("readings.csv", _READINGS, "text/csv")},
    )
    assert r.status_code == 200, r.text
    return ds_id


def test_source_samples_read_the_dataset_own_persisted_file(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _dataset_with_source(client)
        body = client.get(f"/api/datasets/{ds_id}/source-samples").json()
        assert body["columns"]["channel"] == ["A", "B", "C"]
        assert body["sources"]["readings.csv"]["amplitude"] == ["1.5", "2.5", "3.5"]


def test_source_samples_survive_the_original_xlsx_kept_beside_the_csv(
    tmp_path: Path, healthy_client
) -> None:
    """The .xlsx workbook is persisted next to its derived CSVs for provenance.
    Handing it to the tabular inspector raises — and one raise used to take the
    whole answer with it, on exactly the format this endpoint exists for."""
    data = _xlsx_bytes({"Data": [["id", "zt"], ["s1", 0.9], ["s2", 1.2]]})
    with _client(tmp_path, healthy_client) as client:
        ds_id = client.post(
            "/api/materialize", json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor"}
        ).json()["dataset"]["id"]
        client.post(
            f"/api/datasets/{ds_id}/source",
            files={"files": ("book.xlsx", data, _XLSX_MIME)},
        )
        assert (tmp_path / "registry" / ds_id / "source" / "book.xlsx").is_file()
        body = client.get(f"/api/datasets/{ds_id}/source-samples").json()
        assert body["columns"] == {"id": ["s1", "s2"], "zt": ["0.9", "1.2"]}


def test_source_samples_degrade_to_empty_rather_than_fail(
    tmp_path: Path, healthy_client
) -> None:
    """Evidence is enrichment: a design with no source attached yet must still
    open the meaning screen."""
    with _client(tmp_path, healthy_client) as client:
        ds_id = client.post(
            "/api/materialize", json={"proposal_md": _FIX_RECIPE_MD, "dataset_name": "sensor"}
        ).json()["dataset"]["id"]
        assert client.get(f"/api/datasets/{ds_id}/source-samples").json()["columns"] == {}
        assert client.get("/api/datasets/nope/source-samples").status_code == 404


# ---------------------------------------------------------------------------
# KZ-B-05 — fixing a meaning / unit without an AI round
# ---------------------------------------------------------------------------


def test_display_meta_edits_the_design_and_shows_up_in_the_rules(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _dataset_with_source(client)
        before = client.get(f"/api/datasets/{ds_id}").json()["artifacts"]["mapping.rml.ttl"]
        r = client.post(
            f"/api/datasets/{ds_id}/display-meta",
            json={
                "edits": [
                    {
                        "predicate": "https://example.com/sn#amplitude",
                        "column": "amplitude",
                        "label": "振幅",
                        "unit": "mV",
                    }
                ]
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["changed"] == ["amplitude"]

        artifacts = client.get(f"/api/datasets/{ds_id}").json()["artifacts"]
        ir = yaml.safe_load(artifacts["mapping.yaml"])
        row = next(
            p for p in ir["maps"][0]["properties"] if p.get("column") == "amplitude"
        )
        assert row["label"] == "振幅" and row["unit"] == "mV"
        # Display metadata only: the mapping that produces the triples is untouched.
        assert artifacts["mapping.rml.ttl"] == before
        # And the stored design (the single source of truth) carries it too, so
        # the next AI round starts from the corrected document.
        proposal = client.get(f"/api/datasets/{ds_id}/proposal").json()["proposal_md"]
        assert "振幅" in proposal

        rules = client.get(f"/api/datasets/{ds_id}/rules").json()
        amplitude = next(
            p
            for m in rules["maps"]
            for p in m["properties"]
            if p.get("reference") == "amplitude"
        )
        assert amplitude["label"] == "振幅" and amplitude["unit"] == "mV"


def test_display_meta_is_remembered_as_the_humans_and_is_write_gated(
    tmp_path: Path, healthy_client
) -> None:
    """The memo beside the bundle is what lets a later AI round be overruled
    (ADR data-facts-invariant N6); the edit itself is a registry write."""
    with _client(tmp_path, healthy_client) as client:
        ds_id = _dataset_with_source(client)
        client.post(
            f"/api/datasets/{ds_id}/display-meta",
            json={"edits": [{"predicate": "sn:channel", "label": "測定チャンネル"}]},
        )
        memo = json.loads((tmp_path / "registry" / ds_id / "display-meta.json").read_text())
        assert memo["edits"][0]["label"] == "測定チャンネル"

    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as no_token:  # deliberately no token
        r = no_token.post(
            f"/api/datasets/{ds_id}/display-meta",
            json={"edits": [{"predicate": "sn:channel", "label": "x"}]},
        )
        assert r.status_code == 401


def test_display_meta_refuses_an_empty_batch_and_an_unknown_dataset(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _dataset_with_source(client)
        assert (
            client.post(f"/api/datasets/{ds_id}/display-meta", json={"edits": []}).status_code
            == 422
        )
        r = client.post(
            "/api/datasets/nope/display-meta",
            json={"edits": [{"predicate": "sn:channel", "label": "x"}]},
        )
        assert r.status_code == 404


def test_staging_sheet_selection_is_write_gated(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = client.post(
            "/api/staging", files={"files": ("a.csv", _READINGS, "text/csv")}
        ).json()["staging_id"]
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as no_token:
        assert (
            no_token.post(f"/api/staging/{sid}/sources", json={"sources": ["a.csv"]}).status_code
            == 401
        )
        assert staging.valid_id(sid)
