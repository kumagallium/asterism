"""Reshape wired into staging / inspect / attach / append / dataset (ADR
source-reshape.md §2/§4.0). The engine (detect/propose/validate_spec/apply/…)
is tested in ``ingest/tests/test_reshape.py``; this file is wiring only —
where the ledger lives, when it is (re)applied, and how staleness surfaces.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.reshape import apply as reshape_apply
from fastapi.testclient import TestClient

from asterism_api import registry, staging
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_FIXTURES = Path(__file__).parent / "fixtures" / "reshape"
_CURVES = (_FIXTURES / "starrydata_curves.csv").read_bytes()
_SAMPLES = (_FIXTURES / "starrydata_samples.csv").read_bytes()
_PAPERS = (_FIXTURES / "starrydata_papers.csv").read_bytes()


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def _three_files(
    curves: bytes = _CURVES, samples: bytes = _SAMPLES, papers: bytes = _PAPERS
) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("files", ("starrydata_curves.csv", curves, "text/csv")),
        ("files", ("starrydata_samples.csv", samples, "text/csv")),
        ("files", ("starrydata_papers.csv", papers, "text/csv")),
    ]


def _stage_three(client: TestClient) -> str:
    r = client.post("/api/staging", files=_three_files())
    assert r.status_code == 200, r.text
    return r.json()["staging_id"]


# The pivot op's "zt" group, exactly as asterism.reshape.propose() derives it
# from the real fixture (ingest/tests/test_reshape.py asserts the same shape) —
# used to build small, focused specs/ledgers without re-running detect/propose.
_ZT_GROUP = {
    "slug": "zt",
    "label": "ZT",
    "unit": "-",
    "table": "starrydata_curves__zt.csv",
    "members": [{"label": "ZT", "unit": "-"}],
    "partner": {
        "slug": "temperature",
        "label": "Temperature",
        "unit": "K",
        "members": [{"label": "Temperature", "unit": "K"}],
    },
}


def _zt_only_spec() -> dict:
    return {
        "version": 1,
        "ops": [
            {
                "kind": "pivot",
                "source": "starrydata_curves.csv",
                "dialect": {},
                "explode": {"arrays": ["x", "y"], "index": "point_index"},
                "carry": ["SID", "sample_id", "figure_id", "DOI", "composition", "figure_name"],
                "label": "prop_y",
                "unit": "unit_y",
                "value": "y",
                "partner": {"label": "prop_x", "unit": "unit_x", "value": "x"},
                "groups": [dict(_ZT_GROUP)],
            }
        ],
    }


_MIN_RML = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "starrydata_papers.csv" ; '
    "rml:referenceFormulation ql:CSV ] ;\n"
    '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] .\n'
)

_ZT_RML = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "starrydata_curves__zt.csv" ; '
    "rml:referenceFormulation ql:CSV ] ;\n"
    '  rr:subjectMap [ rr:template "https://ex/zt/{SID}-{point_index}" ] .\n'
)


def _save_dataset_with_rml(tmp_path: Path, rml: str = _MIN_RML) -> str:
    return registry.save_dataset(
        tmp_path / "registry",
        "demo",
        {
            "diagram.md": "classDiagram\n  class Paper",
            "model.yaml": "- Paper:",
            "mie.yaml": "schema_info:\n  title: x",
            "mapping.rml.ttl": rml,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-03T00:00:00+00:00",
    )["id"]


# ===========================================================================
# 1. GET /api/staging/{id}/reshape — detections + default spec, cached
# ===========================================================================


def test_get_staging_reshape_detects_and_caches(
    tmp_path: Path, healthy_client, monkeypatch
) -> None:
    import asterism_api.main as main_mod

    calls: list[str] = []
    orig_detect = main_mod.reshape.detect

    def counting_detect(path, **kw):
        calls.append(Path(path).name)
        return orig_detect(path, **kw)

    monkeypatch.setattr(main_mod.reshape, "detect", counting_detect)

    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)

        r1 = client.get(f"/api/staging/{sid}/reshape")
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["applied"] is False
        det = body1["detections"]
        curves_kinds = {d["kind"] for d in det["starrydata_curves.csv"]}
        assert {"explode", "pivot"} <= curves_kinds
        assert any(d["kind"] == "flatten" for d in det["starrydata_samples.csv"])
        op_kinds = {op["kind"] for op in body1["spec"]["ops"]}
        assert "pivot" in op_kinds and "flatten" in op_kinds
        assert body1["tables"] == {} and body1["counts"] == {}
        # detect() ran once per tabular source (3 files staged).
        assert len(calls) == 3

        r2 = client.get(f"/api/staging/{sid}/reshape")
        assert r2.status_code == 200, r2.text
        assert r2.json() == body1  # byte-identical, same cached proposal
        assert len(calls) == 3  # the second GET did not re-run detect()


# ===========================================================================
# 2. POST /api/staging/{id}/reshape — apply, then DELETE reverts
# ===========================================================================


def test_apply_and_delete_staging_reshape(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)
        raw_sources = set(client.get(f"/api/staging/{sid}").json()["sources"])
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]

        r = client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True
        derived = set(body["sources"]) - raw_sources
        assert derived, "reshape should have produced at least one derived table"
        assert "starrydata_curves__zt.csv" in derived
        sdir = staging.dir_for(tmp_path / "registry", sid)
        for name in derived:
            assert (sdir / name).is_file(), name

        # GET now reports the APPLIED spec (tables/counts filled), not the proposal.
        got = client.get(f"/api/staging/{sid}/reshape").json()
        assert got["applied"] is True
        assert got["tables"] and got["counts"]
        assert set(client.get(f"/api/staging/{sid}").json()["sources"]) == raw_sources | derived

        d = client.delete(f"/api/staging/{sid}/reshape")
        assert d.status_code == 200, d.text
        assert set(d.json()["removed"]) == derived
        for name in derived:
            assert not (sdir / name).is_file(), name
        assert set(client.get(f"/api/staging/{sid}").json()["sources"]) == raw_sources
        reverted = client.get(f"/api/staging/{sid}/reshape").json()
        assert reverted["applied"] is False  # back to a proposal, not applied


# ===========================================================================
# 3. POST with an invalid spec (R6) — 422, nothing written
# ===========================================================================


def test_apply_staging_reshape_rejects_invalid_spec(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)
        before = client.get(f"/api/staging/{sid}").json()["sources"]
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]
        pivot = next(op for op in spec["ops"] if op["kind"] == "pivot")
        groups = pivot["groups"]
        assert len(groups) >= 2
        # Put the SAME (label, unit) member into two different groups (R6).
        groups[1]["members"].append(dict(groups[0]["members"][0]))

        r = client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "reshape.invalid_spec"

        after = client.get(f"/api/staging/{sid}").json()["sources"]
        assert after == before  # nothing was written
        assert client.get(f"/api/staging/{sid}/reshape").json()["applied"] is False


# ===========================================================================
# 4. /api/inspect X-Asterism-Reshape header (staged)
# ===========================================================================


def test_inspect_staging_carries_reshape_detections_header(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)
        r = client.post("/api/inspect", data={"staging_id": sid})
        assert r.status_code == 200, r.text
        det = json.loads(r.headers["X-Asterism-Reshape"])
        curves_kinds = {d["kind"] for d in det["starrydata_curves.csv"]}
        assert {"explode", "pivot"} <= curves_kinds
        assert any(d["kind"] == "flatten" for d in det["starrydata_samples.csv"])


# ===========================================================================
# 5. attach (staging_id) persists derived tables + the reshape.json ledger
# ===========================================================================


def test_attach_staging_persists_derived_tables_and_ledger(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        dataset_id = _save_dataset_with_rml(tmp_path)
        sid = _stage_three(client)
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]
        client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})

        r = client.post(
            f"/api/datasets/{dataset_id}/source", data={"staging_id": sid}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "advisories" in body and isinstance(body["advisories"], list)
        # "source_files" in the top-level response is the just-uploaded raw list
        # (unchanged contract); the dataset's OWN persisted meta is what F
        # refreshes to include the derived tables too.
        assert "starrydata_curves__zt.csv" in body["dataset"]["source_files"]

        sdir = tmp_path / "registry" / dataset_id / "source"
        assert (sdir / "starrydata_curves__zt.csv").is_file()
        assert (sdir / "starrydata_curves.csv").is_file()  # raw stays too (R7)

        ledger_path = tmp_path / "registry" / dataset_id / "reshape.json"
        assert ledger_path.is_file()
        ledger = json.loads(ledger_path.read_text())
        assert ledger["stale"] == []
        assert any(op["kind"] == "pivot" for op in ledger["spec"]["ops"])

        listed = {p.name for p in registry.list_source_files(tmp_path / "registry", dataset_id)}
        assert "starrydata_curves__zt.csv" in listed  # F: a normal source file too


# ===========================================================================
# 6. Staleness: a re-attach with a changed raw header invalidates the op
# ===========================================================================


def test_reattach_with_renamed_column_marks_op_stale(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        dataset_id = _save_dataset_with_rml(tmp_path)
        sid = _stage_three(client)
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]
        client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})
        client.post(f"/api/datasets/{dataset_id}/source", data={"staging_id": sid})

        sdir = tmp_path / "registry" / dataset_id / "source"
        assert (sdir / "starrydata_curves__zt.csv").is_file()

        # Re-attach the SAME dataset (no staging this time — R21) with a curves
        # CSV whose unit_y column was renamed: every pivot op reading it goes stale.
        renamed = _CURVES.decode("utf-8").replace("unit_y", "UNIT_Y").encode("utf-8")
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files=_three_files(curves=renamed),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert any(a.startswith("reshape.op_stale") for a in body["advisories"])

        ledger = json.loads((tmp_path / "registry" / dataset_id / "reshape.json").read_text())
        assert ledger["stale"], "the pivot op reading unit_y should be stale"
        # The pivot op was the ONLY op reading curves.csv's prop_y/unit_y, so no
        # derived table for it exists any more.
        assert not (sdir / "starrydata_curves__zt.csv").is_file()


# ===========================================================================
# 7. append derives a batch through the ledger; raw always accumulates (A7)
# ===========================================================================


class _FeedOxi:
    """Oxigraph fake for append — see test_xlsx_source.py's twin."""

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
            rows = [{"o": {"type": "uri", "value": self._live}}] if "liveGraph" in q else []
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _fake_nt_materializer(*, triples: int = 1):
    def _materialize(
        rml_ttl, csv_dir, *, udfs_path=None, work_dir=None, run_id=None, should_cancel=None
    ) -> Path:
        out = Path(work_dir) / "out.nt"
        out.write_bytes(
            b"".join(
                f'<https://ex/zt/{i}> <https://schema.org/name> "p{i}" .\n'.encode()
                for i in range(triples)
            )
        )
        return out

    return _materialize


_CURVES_HEADER = (
    "SID,DOI,composition,sample_id,figure_id,figure_name,prop_x,prop_y,unit_x,unit_y,"
    "x,y,created_at,updated_at,project_names,comments\n"
)


def _zt_row(sid: str) -> str:
    return (
        f'{sid},10.1/x,Comp,{sid}00,1,1(a),Temperature,ZT,K,-,'
        '"[300,310]","[1.1,1.2]",,,"[]",\n'
    )


def _promoted_zt_dataset(tmp_path: Path) -> tuple[str, str]:
    """A promoted dataset whose RML reads the "zt" derived table, with a live
    reshape ledger + persisted raw curves.csv + persisted derived table — the
    append-time scenario (ADR source-reshape.md R13, incremental-ingest.md A7).
    """
    dataset_id = _save_dataset_with_rml(tmp_path, rml=_ZT_RML)
    sdir = tmp_path / "registry" / dataset_id / "source"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "starrydata_curves.csv").write_bytes(_CURVES)
    spec = _zt_only_spec()
    applied = reshape_apply(spec, _FIXTURES, sdir)
    registry.mark_source_saved(
        tmp_path / "registry", dataset_id, ["starrydata_curves.csv", "starrydata_curves__zt.csv"]
    )
    (tmp_path / "registry" / dataset_id / "reshape.json").write_text(
        json.dumps(
            {"spec": applied, "stale": [], "attached_at": "2026-09-03T00:00:00+00:00"}
        ),
        encoding="utf-8",
    )
    live = substrate.versioned_graph_iri(dataset_id, 1)
    registry.mark_ingested(
        tmp_path / "registry",
        dataset_id,
        graph_iri=live,
        triple_count=1,
        ingested_at="2026-09-03T00:00:00+00:00",
        data_seq=1,
    )
    registry.mark_promoted(
        tmp_path / "registry",
        dataset_id,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-09-03T00:01:00+00:00",
        canonical_graph=substrate.canonical_graph_iri(dataset_id),
        live_graph=live,
    )
    return dataset_id, live


def test_append_derives_batch_through_ledger_and_keeps_raw(
    tmp_path: Path, healthy_client, monkeypatch
) -> None:
    dataset_id, live = _promoted_zt_dataset(tmp_path)
    from asterism import substrate as substrate_mod

    monkeypatch.setattr(substrate_mod, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)

    persisted_zt_before = (
        tmp_path / "registry" / dataset_id / "source" / "starrydata_curves__zt.csv"
    ).read_text()
    header_before = persisted_zt_before.splitlines()[0]

    # A batch of ONLY "ZT" rows — every row matches the group, so the
    # unmatched rate is 0 and no reshape.unmatched_growth advisory fires.
    batch = (_CURVES_HEADER + _zt_row("900") + _zt_row("901")).encode("utf-8")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("starrydata_curves.csv", batch, "text/csv")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["live_graph"] == live and live in oxi.stores
    assert body["advisories"] == []

    zt_after = (
        tmp_path / "registry" / dataset_id / "source" / "starrydata_curves__zt.csv"
    ).read_text()
    assert zt_after.splitlines()[0] == header_before  # schema unchanged
    assert zt_after.count("\n") > persisted_zt_before.count("\n")  # grew

    raw_after = (
        tmp_path / "registry" / dataset_id / "source" / "starrydata_curves.csv"
    ).read_text()
    assert "900" in raw_after and "901" in raw_after  # A7: raw always accumulated


# ===========================================================================
# 8. GET/PUT /api/datasets/{id}/reshape
# ===========================================================================


def test_get_and_put_dataset_reshape(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        dataset_id, _live = _promoted_zt_dataset(tmp_path)

        got = client.get(f"/api/datasets/{dataset_id}/reshape")
        assert got.status_code == 200, got.text
        ledger = got.json()
        assert ledger["stale"] == []
        pivot = next(op for op in ledger["spec"]["ops"] if op["kind"] == "pivot")
        assert pivot["groups"][0]["slug"] == "zt"
        assert "starrydata_curves__zt.csv" in ledger["counts"]["0"]["tables"]

        # Disable the only group → the derived table disappears.
        edited = json.loads(json.dumps(ledger["spec"]))
        edited["ops"][0]["groups"][0]["enabled"] = False
        put = client.put(f"/api/datasets/{dataset_id}/reshape", json={"spec": edited})
        assert put.status_code == 200, put.text
        assert put.json()["counts"]["0"]["tables"] == {}
        assert not (
            tmp_path / "registry" / dataset_id / "source" / "starrydata_curves__zt.csv"
        ).is_file()

        no_more = client.get(f"/api/datasets/{dataset_id}/reshape").json()
        assert no_more["spec"]["ops"][0]["groups"][0]["enabled"] is False


# ===========================================================================
# 9. Table-name safety: path traversal / absolute path is rejected (never
#    reaches reshape.apply(), which would otherwise shutil.move() there)
# ===========================================================================


def test_apply_staging_reshape_rejects_path_traversal_table_name(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)
        before = client.get(f"/api/staging/{sid}").json()["sources"]
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]
        pivot = next(op for op in spec["ops"] if op["kind"] == "pivot")
        evil = tmp_path / "outside" / "evil.csv"
        pivot["groups"][0]["table"] = f"../../../../{evil}"

        r = client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "reshape.invalid_spec"
        assert not evil.exists()  # nothing was ever written outside the sandbox

        after = client.get(f"/api/staging/{sid}").json()["sources"]
        assert after == before  # nothing was written, not even inside the sandbox


# ===========================================================================
# 10. Table-name safety: a name colliding with an existing RAW source is
#     rejected (R7 — reshape.apply() would otherwise silently overwrite it)
# ===========================================================================


def test_apply_staging_reshape_rejects_table_name_colliding_with_raw(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)
        sdir = staging.dir_for(tmp_path / "registry", sid)
        original_papers = (sdir / "starrydata_papers.csv").read_bytes()
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]
        pivot = next(op for op in spec["ops"] if op["kind"] == "pivot")
        pivot["groups"][0]["table"] = "starrydata_papers.csv"  # an already-staged raw file

        r = client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "reshape.invalid_spec"
        # The raw citable source must survive untouched — not silently overwritten
        # with pivoted curve rows.
        assert (sdir / "starrydata_papers.csv").read_bytes() == original_papers


# ===========================================================================
# 11. A disabled group's phantom tables/counts entry does not survive a
#     GET→edit→POST re-apply round (R19) even though the response IS an
#     echo of the round's own prior tables/counts.
# ===========================================================================


def test_reapply_staging_reshape_drops_phantom_table_for_disabled_group(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = _stage_three(client)
        spec = client.get(f"/api/staging/{sid}/reshape").json()["spec"]
        r1 = client.post(f"/api/staging/{sid}/reshape", json={"spec": spec})
        assert r1.status_code == 200, r1.text
        applied = r1.json()["spec"]
        pivot = next(op for op in applied["ops"] if op["kind"] == "pivot")
        disabled_table = pivot["groups"][0]["table"]
        assert disabled_table in r1.json()["tables"]

        # The natural UI loop: take the round's OWN echoed spec (tables/counts
        # included), flip one group off, re-POST the whole thing unedited otherwise.
        pivot["groups"][0]["enabled"] = False
        r2 = client.post(f"/api/staging/{sid}/reshape", json={"spec": applied})
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert disabled_table not in body2["tables"]
        assert disabled_table not in body2["counts"]
        sdir = staging.dir_for(tmp_path / "registry", sid)
        assert not (sdir / disabled_table).is_file()


# ===========================================================================
# 12. Append: a batch whose op-referenced columns are missing (renamed) is
#     REJECTED, not silently applied to zero derived rows (explode/flatten
#     have no conservation check that would ever notice 0 == 0).
# ===========================================================================


_POINTS_RML = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "starrydata_curves__points.csv" ; '
    "rml:referenceFormulation ql:CSV ] ;\n"
    '  rr:subjectMap [ rr:template "https://ex/point/{SID}-{point_index}" ] .\n'
)


def _points_only_spec() -> dict:
    return {
        "version": 1,
        "ops": [
            {
                "kind": "explode",
                "source": "starrydata_curves.csv",
                "dialect": {},
                "table": "starrydata_curves__points.csv",
                "arrays": ["x", "y"],
                "carry": ["SID", "sample_id"],
            }
        ],
    }


def _promoted_points_dataset(tmp_path: Path) -> tuple[str, str]:
    """A promoted dataset whose RML reads an EXPLODE-derived table (arrays x,y),
    with a live reshape ledger + persisted raw curves.csv + persisted derived
    table — the append-time scenario for the silent-data-loss finding above."""
    dataset_id = _save_dataset_with_rml(tmp_path, rml=_POINTS_RML)
    sdir = tmp_path / "registry" / dataset_id / "source"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "starrydata_curves.csv").write_bytes(_CURVES)
    spec = _points_only_spec()
    applied = reshape_apply(spec, _FIXTURES, sdir)
    registry.mark_source_saved(
        tmp_path / "registry",
        dataset_id,
        ["starrydata_curves.csv", "starrydata_curves__points.csv"],
    )
    (tmp_path / "registry" / dataset_id / "reshape.json").write_text(
        json.dumps(
            {"spec": applied, "stale": [], "attached_at": "2026-09-03T00:00:00+00:00"}
        ),
        encoding="utf-8",
    )
    live = substrate.versioned_graph_iri(dataset_id, 1)
    registry.mark_ingested(
        tmp_path / "registry",
        dataset_id,
        graph_iri=live,
        triple_count=1,
        ingested_at="2026-09-03T00:00:00+00:00",
        data_seq=1,
    )
    registry.mark_promoted(
        tmp_path / "registry",
        dataset_id,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-09-03T00:01:00+00:00",
        canonical_graph=substrate.canonical_graph_iri(dataset_id),
        live_graph=live,
    )
    return dataset_id, live


def test_append_rejects_batch_with_renamed_array_columns(
    tmp_path: Path, healthy_client, monkeypatch
) -> None:
    dataset_id, live = _promoted_points_dataset(tmp_path)
    from asterism import substrate as substrate_mod

    monkeypatch.setattr(substrate_mod, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)

    points_path = tmp_path / "registry" / dataset_id / "source" / "starrydata_curves__points.csv"
    raw_path = tmp_path / "registry" / dataset_id / "source" / "starrydata_curves.csv"
    points_before = points_path.read_text()
    raw_before = raw_path.read_text()

    # A batch whose array columns were renamed x/y -> X/Y (a routine dated
    # instrument export with a header change) — the explode op can no longer
    # read x/y at all: every row degrades to 0 elements, not an error, so this
    # must be caught by the header staleness check before reshape.apply() runs.
    renamed_header = _CURVES_HEADER.replace(",x,y,created_at", ",X,Y,created_at")
    batch = (renamed_header + _zt_row("900") + _zt_row("901")).encode("utf-8")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("starrydata_curves.csv", batch, "text/csv")},
        )
    assert r.status_code == 422, r.text
    assert "reshape.op_stale" in r.json()["detail"]
    # Fail-closed: neither the derived table nor the raw feed grew.
    assert points_path.read_text() == points_before
    assert raw_path.read_text() == raw_before


# ===========================================================================
# 13. R15 — reshape reads a non-default dialect (ADR source-reshape.md), not
#     always the plain-UTF-8-comma default (ADR source-dialect.md)
# ===========================================================================

# A tab-separated export with a two-line preamble before the header (the
# ADR's shape: real instrument files are rarely clean UTF-8 comma CSV) —
# 6 data rows so the tab candidate's trailing run clears detect_dialect's
# _MIN_RUN=5 floor. ``x``/``y`` are JSON numeric arrays (parallel, same
# length every row) so a plain-comma read would see one un-splittable token
# per line and reshape.detect() would find nothing at all.
_TABBED_ARRAYS = (
    "note line 1\n"
    "note line 2\n"
    "id\tx\ty\n"
    + "".join(
        f"r{i}\t[{3 * i + 1},{3 * i + 2},{3 * i + 3}]\t[{3 * i + 4},{3 * i + 5},{3 * i + 6}]\n"
        for i in range(6)
    )
).encode("utf-8")


def test_reshape_reads_staged_source_through_detected_dialect(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.post(
            "/api/staging",
            files=[("files", ("measurement.txt", _TABBED_ARRAYS, "text/plain"))],
        )
        assert r.status_code == 200, r.text
        sid = r.json()["staging_id"]

        got = client.get(f"/api/staging/{sid}/reshape")
        assert got.status_code == 200, got.text
        body = got.json()
        det = body["detections"]["measurement.txt"]
        assert any(d["kind"] == "explode" for d in det)
        explode_ops = [op for op in body["spec"]["ops"] if op["kind"] == "explode"]
        assert explode_ops, body["spec"]["ops"]
        assert explode_ops[0]["dialect"]["delimiter"] == "\t"
        assert explode_ops[0]["dialect"]["skip_rows"] == 2

        applied = client.post(f"/api/staging/{sid}/reshape", json={"spec": body["spec"]})
        assert applied.status_code == 200, applied.text
        applied_body = applied.json()
        derived = [name for name in applied_body["sources"] if name != "measurement.txt"]
        assert derived, applied_body["sources"]

        sdir = staging.dir_for(tmp_path / "registry", sid)
        table_bytes = (sdir / derived[0]).read_bytes()
        # R15: the derived table itself is always plain UTF-8 comma CSV,
        # regardless of the raw source's own dialect.
        rows = list(csv.DictReader(table_bytes.decode("utf-8").splitlines()))
        assert rows
        assert {"id", "x", "y"} <= set(rows[0].keys())
