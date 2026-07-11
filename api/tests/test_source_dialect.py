"""Source-dialect entrance + wiring (ADR docs/architecture/source-dialect.md).

The api's slice of "throw the legacy instrument file in as-is": widened tabular
extensions (.tsv/.txt/.dat/.asc), deterministic slugging of non-ASCII tabular
filenames at every entrance (the canonical name is what rml:source references),
a readable 422 instead of a decode/parse traceback on /api/inspect, the design
loop pinning inspect-detected dialects into the §9 mapping spec, and the append
guard refusing incremental append of a dialected source (snapshot re-ingest is
the supported path). Detection/normalization themselves are step0/ingest
territory — these tests exercise the api wiring with fakes where the seam is
still landing (see tests/conftest.py).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest
from asterism import substrate
from asterism.dialect import SourceDialect
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi import HTTPException
from fastapi.testclient import TestClient

from asterism_api import design_loop, registry
from asterism_api import main as api_main
from asterism_api.main import (
    Settings,
    _dialected_sources,
    _sanitize_tabular_name,
    build_app,
)

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


# The ADR's real-world case: CP932, CRLF, tab-separated, one preamble line
# (sample name) before the header row 「2θ (deg)\t強度 (cps)」.
_CP932_XRD = "サンプル名: 試料A\r\n2θ (deg)\t強度 (cps)\r\n10.0\t123\r\n10.2\t456\r\n".encode(
    "cp932"
)


# ---- _sanitize_tabular_name (the canonical-name rule) -------------------------


def test_sanitize_tabular_name_passthrough_for_safe_names() -> None:
    for name in ("papers.csv", "data.txt", "xrd-01.dat", "table.tsv", "scan.asc"):
        assert _sanitize_tabular_name(name) == name


def test_sanitize_tabular_name_slugs_japanese_deterministically() -> None:
    a = _sanitize_tabular_name("xrd_測定結果.txt")
    assert a == _sanitize_tabular_name("xrd_測定結果.txt")  # stable across calls
    assert api_main._SAFE_SOURCE_NAME.fullmatch(a), a
    assert a.endswith(".txt")


def test_sanitize_tabular_name_disambiguates_lossy_slugs() -> None:
    # Both ADR example files share the surviving stem "xrd" — the hash keeps the
    # two SOURCES distinct (colliding canonical names would merge in the RML).
    a = _sanitize_tabular_name("xrd_測定結果.txt")
    b = _sanitize_tabular_name("xrd_参考文献.txt")
    assert a != b


def test_sanitize_tabular_name_strips_traversal_to_basename() -> None:
    assert _sanitize_tabular_name("../../etc/passwd.csv") == "passwd.csv"


def test_sanitize_tabular_name_lowercases_extension() -> None:
    out = _sanitize_tabular_name("DATA.TXT")
    assert out.endswith(".txt")
    assert api_main._SAFE_SOURCE_NAME.fullmatch(out), out


def test_sanitize_tabular_name_rejects_unsupported_extension() -> None:
    for bad in ("notes.md", "run.exe", "README"):
        with pytest.raises(HTTPException) as exc:
            _sanitize_tabular_name(bad)
        assert exc.value.status_code == 400


# ---- /api/inspect: widened entrance + canonical names + 422 safety net --------


def test_inspect_accepts_txt_and_returns_canonical_name(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    canonical = _sanitize_tabular_name("xrd_測定結果.txt")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect",
            files={
                "files": ("xrd_測定結果.txt", b"angle,intensity\n10.0,123\n", "text/plain")
            },
        )
        assert r.status_code == 200, r.text
        # The client learns the exact name the design's rml:source must use.
        assert r.headers["X-Asterism-Source-Names"] == canonical
        assert f"## CSV: {canonical}" in r.text


def test_inspect_decode_failure_is_readable_422(tmp_path: Path, monkeypatch) -> None:
    # The safety net: whatever the inspector raises while decoding surfaces as a
    # 422 with a readable message, never a 500 traceback. (With dialect detection
    # in step0 this should be rare — a CP932 file normally just inspects.)
    def boom(paths, *, fk_hint_columns=None):
        raise UnicodeDecodeError("utf-8", b"\x88\xea", 0, 1, "invalid start byte")

    monkeypatch.setattr(api_main, "inspect_source_set", boom)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/inspect", files={"files": ("data.csv", _CP932_XRD, "text/plain")}
        )
    assert r.status_code == 422
    assert "エンコーディング" in r.json()["detail"]


def test_inspect_csv_parse_failure_is_readable_422(tmp_path: Path, monkeypatch) -> None:
    def boom(paths, *, fk_hint_columns=None):
        raise csv.Error("line contains NUL")

    monkeypatch.setattr(api_main, "inspect_source_set", boom)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/inspect", files={"files": ("data.csv", b"\x00", "text/csv")})
    assert r.status_code == 422
    assert "line contains NUL" in r.json()["detail"]


# ---- /api/propose: the done payload names the canonical sources ---------------


class _MockLLM:
    def __init__(self, key: str | None) -> None:
        self.key = key

    def complete(self, system_prompt: str, user_message: str) -> str:
        return "## Proposed schema\n\nMOCK PROPOSAL."


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload = line[len("data:") :].strip()
            events.append((name, json.loads(payload) if payload else {}))
    return events


def test_propose_done_payload_carries_canonical_source_names(tmp_path: Path) -> None:
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_healthy_client(),
        start_watcher=False,
        llm_factory=_MockLLM,
    )
    canonical = _sanitize_tabular_name("xrd_測定結果.txt")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/propose",
            params={"autocorrect": 0},
            files={
                "files": ("xrd_測定結果.txt", b"angle,intensity\n10.0,123\n", "text/plain")
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        done = next(d for n, d in events if n == "done")
        # Additive field: the slugged names the design's rml:source must reference.
        assert done["result"]["source_files"] == [canonical]


# ---- source attach / registry: legacy suffixes persist + classify as csv ------


def _save_dataset(tmp: Path, rml: str = "") -> str:
    return registry.save_dataset(
        tmp / "registry",
        "demo",
        {
            "diagram.md": "classDiagram\n  class Curve",
            "model.yaml": "- Curve:",
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": rml,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
    )["id"]


def test_attach_source_accepts_cp932_txt_as_is(tmp_path: Path) -> None:
    """The product promise: the CP932/tab/preamble file is persisted byte-identically
    under its canonical (slugged) name — no transcoding, no rejection at the door."""
    dataset_id = _save_dataset(tmp_path)
    canonical = _sanitize_tabular_name("xrd_測定結果.txt")
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("xrd_測定結果.txt", _CP932_XRD, "text/plain")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_files"] == [canonical]
    assert body["dataset"]["source_kind"] == "csv"  # tabular dialect suffixes are csv-kind
    saved = tmp_path / "registry" / dataset_id / "source" / canonical
    assert saved.read_bytes() == _CP932_XRD
    # list_source_files (the ingest path's source discovery) picks the .txt up.
    assert [p.name for p in registry.list_source_files(tmp_path / "registry", dataset_id)] == [
        canonical
    ]


def test_registry_lists_legacy_tabular_suffixes(tmp_path: Path) -> None:
    dataset_id = _save_dataset(tmp_path)
    sdir = tmp_path / "registry" / dataset_id / "source"
    sdir.mkdir(parents=True)
    for name in ("a.txt", "b.dat", "c.asc", "d.tsv", "e.csv"):
        (sdir / name).write_bytes(b"x\n1\n")
    names = [p.name for p in registry.list_source_files(tmp_path / "registry", dataset_id)]
    assert names == ["a.txt", "b.dat", "c.asc", "d.tsv", "e.csv"]
    assert registry.source_kind_of(names) == "csv"


# ---- design loop: detected dialects are pinned into the §9 spec ----------------


def _md_with_spec(source: str, reference_col: str) -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://example.org/ns#"\n'
        '  exr: "https://example.org/r/"\n'
        "maps:\n"
        "  - name: thing\n"
        f"    source: {source}\n"
        "    subject:\n"
        '      template: "exr:thing/{SID}"\n'
        "      classes: [ex:Thing]\n"
        "    properties:\n"
        "      - predicate: ex:comp\n"
        f"        column: {reference_col}\n"
        "```\n"
    )


class _ScriptedLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.model = "mock-model"

    def complete(self, system_prompt: str, user_message: str) -> str:
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


# ASCII content decodes identically under cp932, so the pinned encoding stays
# benign for the fixture while still being a NON-default dialect.
_DIALECT_MARKER = 'dialects:\n  "data.csv":\n    encoding: cp932\n'


def _fake_overlay_fns(calls: dict):
    """(detect, apply) fakes for the step0 seam: detect pins cp932 for every file,
    apply appends the ADR's ``dialects:`` section unless already present (explicit
    wins) — the api wiring under test is detection-once + overlay-per-round + splice."""

    def fake_detect(path: Path) -> SourceDialect:
        calls.setdefault("detected", []).append(Path(path).name)
        return SourceDialect(encoding="cp932")

    def fake_apply(ir_yaml: str, detected) -> str:
        calls["applied"] = calls.get("applied", 0) + 1
        if "dialects:" in ir_yaml:
            return ir_yaml
        return ir_yaml.rstrip("\n") + "\n" + _DIALECT_MARKER

    return fake_detect, fake_apply


def test_design_loop_pins_detected_dialect_into_spec(tmp_path: Path, monkeypatch) -> None:
    calls: dict = {}
    fake_detect, fake_apply = _fake_overlay_fns(calls)
    monkeypatch.setattr(design_loop, "detect_dialect", fake_detect)
    monkeypatch.setattr(design_loop, "apply_detected_dialects", fake_apply)
    (tmp_path / "data.csv").write_bytes(b"SID,composition\n1,Bi2Te3\n")

    llm = _ScriptedLLM([_md_with_spec("data.csv", "composition")])
    result = design_loop.run_design_loop(
        [tmp_path / "data.csv"], "hint", tmp_path, llm=llm, max_rounds=0
    )
    # Detection ran once over the upload; the overlay reached the §9 block the
    # client will materialize (dialect travels design → artifact).
    assert calls["detected"] == ["data.csv"]
    assert _DIALECT_MARKER in result.proposal_md


def test_design_loop_reapplies_dialects_after_refine_round(tmp_path: Path, monkeypatch) -> None:
    """A surgical §9 repair regenerates the spec WITHOUT the dialects section; the
    loop must re-pin the detected dialect before re-validating, so the final design
    still carries it."""
    calls: dict = {}
    fake_detect, fake_apply = _fake_overlay_fns(calls)
    monkeypatch.setattr(design_loop, "detect_dialect", fake_detect)
    monkeypatch.setattr(design_loop, "apply_detected_dialects", fake_apply)
    (tmp_path / "data.csv").write_bytes(b"SID,composition\n1,Bi2Te3\n")

    fixed_spec_json = (
        '{"version": 1,'
        ' "prefixes": {"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},'
        ' "maps": [{"name": "thing", "source": "data.csv",'
        ' "subject": {"template": "exr:thing/{SID}", "classes": ["ex:Thing"]},'
        ' "properties": [{"predicate": "ex:comp", "column": "composition"}]}]}'
    )
    # Round 0 references a bad column; the repair round returns the fixed spec.
    llm = _ScriptedLLM([_md_with_spec("data.csv", "comp"), fixed_spec_json])
    result = design_loop.run_design_loop(
        [tmp_path / "data.csv"], "hint", tmp_path, llm=llm, max_rounds=1
    )
    # The overlay ran on round-0 AND again after the surgical splice (which
    # regenerated §9 without the section), and the returned design carries it.
    assert calls["applied"] >= 2
    assert _DIALECT_MARKER in result.proposal_md


# The design-side twin (asterism_step0.dialect). When it has not landed yet the
# conftest stub (no __file__) stands in and the real-chain tests below skip.
_REAL_STEP0_DIALECT = getattr(
    __import__("asterism_step0.dialect", fromlist=["dialect"]), "__file__", None
) is not None


@pytest.mark.skipif(
    not _REAL_STEP0_DIALECT, reason="asterism_step0.dialect not landed (stub active)"
)
def test_detect_source_dialects_real_cp932_tab_file(tmp_path: Path) -> None:
    """No fakes: the ADR's CP932/tab/preamble file detects as a non-default dialect
    through the api's design-time detection pass."""
    p = tmp_path / "xrd.txt"
    rows = "".join(f"{10 + i}.0\t{100 + i}\r\n" for i in range(6))
    p.write_bytes(("サンプル名: 試料A\r\n2θ (deg)\t強度 (cps)\r\n" + rows).encode("cp932"))
    detected = design_loop._detect_source_dialects([p])
    assert "xrd.txt" in detected
    d = detected["xrd.txt"]
    assert (d.encoding, d.delimiter, d.skip_rows) == ("cp932", "\t", 1)


@pytest.mark.skipif(
    not _REAL_STEP0_DIALECT, reason="asterism_step0.dialect not landed (stub active)"
)
def test_overlay_with_real_apply_pins_detected_encoding(tmp_path: Path) -> None:
    """No fakes: detect (cp932 comma CSV) → apply_detected_dialects → §9 splice."""
    p = tmp_path / "data.csv"
    rows = "".join(f"{i},試料{i}\n" for i in range(6))
    p.write_bytes(("SID,名称\n" + rows).encode("cp932"))
    detected = design_loop._detect_source_dialects([p])
    assert detected["data.csv"].encoding == "cp932"
    out = design_loop._overlay_detected_dialects(_md_with_spec("data.csv", "SID"), detected)
    assert "dialects:" in out
    assert "cp932" in out


def test_read_header_reads_cp932_tab_preamble_through_dialect(tmp_path: Path) -> None:
    """The dialect-aware header read the oracle / closed-set validation uses sees
    the columns Morph-KGC will get after normalization — the ADR's CP932 file."""
    p = tmp_path / "xrd.txt"
    p.write_bytes(_CP932_XRD)
    dialect = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    assert design_loop._read_header(p, dialect) == ["2θ (deg)", "強度 (cps)"]
    # None keeps today's read (utf-8-sig comma) — the is_default gate.
    assert design_loop._read_header(tmp_path / "absent.csv", None) == []


def test_build_oracle_lists_dialected_columns(tmp_path: Path) -> None:
    p = tmp_path / "xrd.txt"
    p.write_bytes(_CP932_XRD)
    oracle = design_loop.build_oracle(
        tmp_path,
        [p],
        dialects={"xrd.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)},
    )
    assert "xrd.txt — columns: 2θ (deg), 強度 (cps)" in oracle


def test_read_header_undeclared_cp932_csv_cannot_check(tmp_path: Path) -> None:
    """C8: a CP932 file with NO pinned dialect (e.g. an upload no map declares)
    must read as "cannot check" — the default utf-8-sig read used to raise
    UnicodeDecodeError out of _collect_ir_issues / build_oracle and kill the
    whole design job."""
    p = tmp_path / "junk.csv"
    p.write_bytes(_CP932_XRD)
    assert design_loop._read_header(p, None) == []


def test_collect_ir_issues_survives_undeclared_cp932_sidecar(tmp_path: Path) -> None:
    # C8 integration: the sidecar file is headers-scanned but undecodable; the
    # declared clean source still validates and the job does not crash.
    (tmp_path / "data.csv").write_bytes(b"SID,composition\n1,Bi2Te3\n")
    (tmp_path / "junk.csv").write_bytes(_CP932_XRD)
    ir_yaml = (
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://example.org/ns#"\n'
        '  exr: "https://example.org/r/"\n'
        "maps:\n"
        "  - name: thing\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:thing/{SID}"\n'
        "      classes: [ex:Thing]\n"
        "    properties:\n"
        "      - predicate: ex:comp\n"
        "        column: composition\n"
    )
    assert design_loop._collect_ir_issues(ir_yaml, tmp_path) == []


# ---- /api/materialize: dialect re-pin from the persisted source (C9/C15) ------

_SPEC_MD_NO_DIALECTS = (
    "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
    "```yaml\n"
    "version: 1\n"
    "prefixes:\n"
    '  ex: "https://example.org/ns#"\n'
    '  exr: "https://example.org/r/"\n'
    "maps:\n"
    "  - name: point\n"
    "    source: xrd.txt\n"
    "    subject:\n"
    '      template: "exr:p/{2θ (deg)}"\n'
    "      classes: [ex:Point]\n"
    "    properties:\n"
    "      - predicate: ex:intensity\n"
    '        column: "強度 (cps)"\n'
    "```\n"
)


def test_materialize_repins_dialects_from_persisted_source(tmp_path: Path) -> None:
    """C9/C15: a refine round (or hand edit) can drop the §9 ``dialects:`` section;
    re-materializing WITH dataset_id must re-pin it deterministically from the
    dataset's persisted source dir — otherwise the compiled RML silently loses the
    annotations and ingest mis-reads the file."""
    dataset_id = _save_dataset(tmp_path)
    sdir = tmp_path / "registry" / dataset_id / "source"
    sdir.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{10 + i}.0\t{100 + i}\r\n" for i in range(6))  # run ≥ 5 to detect
    (sdir / "xrd.txt").write_bytes(
        ("サンプル名: 試料A\r\n2θ (deg)\t強度 (cps)\r\n" + rows).encode("cp932")
    )
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        # Without dataset_id there is no source to detect against — no annotations.
        r0 = client.post(
            "/api/materialize",
            json={"proposal_md": _SPEC_MD_NO_DIALECTS, "persist": False},
        )
        assert r0.status_code == 200, r0.text
        assert "ast:sourceEncoding" not in (r0.json()["artifacts"]["mapping.rml.ttl"] or "")
        # With dataset_id the persisted source re-pins the dialect end-to-end.
        r = client.post(
            "/api/materialize",
            json={
                "proposal_md": _SPEC_MD_NO_DIALECTS,
                "persist": False,
                "dataset_id": dataset_id,
            },
        )
        assert r.status_code == 200, r.text
        artifacts = r.json()["artifacts"]
        assert "dialects:" in artifacts["mapping.yaml"]
        rml = artifacts["mapping.rml.ttl"]
        assert 'ast:sourceEncoding "cp932"' in rml
        assert "ast:sourceSkipRows 1" in rml


# ---- append guard: a dialected source cannot be incrementally appended --------

_RML_PLAIN = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] .\n'
)

# The dialected twin: the compiler pinned the detected dialect as ast: annotations
# on the logicalSource (only non-default values are ever emitted).
_RML_DIALECTED = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    "  rml:logicalSource [\n"
    '    rml:source "xrd.txt" ; rml:referenceFormulation ql:CSV ;\n'
    '    ast:sourceEncoding "cp932" ; ast:sourceDelimiter "\\t" ;\n'
    "    ast:sourceSkipRows 1 ;\n"
    "  ] ;\n"
    '  rr:subjectMap [ rr:template "https://ex/point/{2θ (deg)}" ] .\n'
    "<#N> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] .\n'
)


def test_dialected_sources_reads_annotations_per_source() -> None:
    assert _dialected_sources(_RML_PLAIN) == set()
    assert _dialected_sources(_RML_DIALECTED) == {"xrd.txt"}


class _FeedOxi:
    """Records /store POSTs and answers the liveGraph SELECT with a fixed pointer."""

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


def _promoted_feed_dataset(tmp: Path, rml: str) -> tuple[str, str]:
    dataset_id = _save_dataset(tmp, rml)
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
        ingested_at="2026-07-11T00:00:00+00:00",
        data_seq=1,
    )
    registry.mark_promoted(
        tmp / "registry",
        dataset_id,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-07-11T00:01:00+00:00",
        canonical_graph=substrate.canonical_graph_iri(dataset_id),
        live_graph=live,
    )
    return dataset_id, live


def test_append_dialected_source_is_422(tmp_path: Path) -> None:
    dataset_id, live = _promoted_feed_dataset(tmp_path, _RML_DIALECTED)
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("xrd.txt", _CP932_XRD, "text/plain")},
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "dialect" in detail
    assert "再取り込み" in detail  # points at the supported path (snapshot re-ingest)
    assert oxi.stores == []  # nothing reached the live graph


def test_append_clean_source_of_dialected_mapping_still_works(
    tmp_path: Path, monkeypatch
) -> None:
    # The guard is per-source: the mapping's OTHER (default-dialect) source appends.
    from tests.test_ingest import _fake_nt_materializer

    dataset_id, live = _promoted_feed_dataset(tmp_path, _RML_DIALECTED)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("papers.csv", b"SID\n2\n", "text/csv")},
        )
    assert r.status_code == 200, r.text
    assert live in oxi.stores


def test_append_sanitizes_batch_name_to_match_rml_source(tmp_path: Path, monkeypatch) -> None:
    """A batch dropped under the instrument's original (non-ASCII) filename appends to
    the source the design pinned under the canonical slug — same rule, both ends."""
    from tests.test_ingest import _fake_nt_materializer

    canonical = _sanitize_tabular_name("実験ログ.csv")
    rml = _RML_PLAIN.replace("papers.csv", canonical)
    dataset_id, live = _promoted_feed_dataset(tmp_path, rml)
    sdir = tmp_path / "registry" / dataset_id / "source"
    (sdir / canonical).write_bytes(b"SID\n1\n")
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    oxi = _FeedOxi(live)
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("実験ログ.csv", b"SID\n2\n", "text/csv")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataset"]["appends"][0]["batch_files"] == [canonical]
    # The batch accumulated into the canonical source file (A7).
    assert (sdir / canonical).read_text().splitlines() == ["SID", "1", "2"]
