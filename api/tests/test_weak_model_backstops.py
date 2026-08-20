"""The deterministic backstops on the HUMAN-initiated design paths.

Round 0 of the design loop re-pins dialects, re-asserts what the data proved,
validates and repairs before any model is asked. The paths a person drives —
"AI に直してもらう" (refine), 保存 (materialize), and the source attach that
follows the first materialize — used to skip all of it. These tests pin that
they no longer do, with a scripted LLM (or none at all): every assertion here is
about work the machine does itself.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import design_loop, registry
from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}

# A §9 spec whose numeric column carries NO datatype — the exact thing a weak
# model drops when it rewrites the block to honour a one-line comment.
_SPEC_UNTYPED = """\
## Schema proposal

### 9. Declarative mapping spec

```yaml
version: 1
prefixes:
  ex: "https://ns.invalid/onto#"
  exr: "https://ns.invalid/resource/"
maps:
  - name: sample
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:temperature
        column: temp
```

### 7. MIE YAML extras

```yaml
schema_info:
  title: Samples
  keywords: [sample, temperature, thermoelectric, measurement, powder]
  categories: [materials]
```
"""

# `pressure` is mapped by nothing — the "a column of your file is unused"
# advisory needs a real source to see it, which is the whole point below.
_CSV = "sid,temp,pressure\ns1,300.5,1.0\ns2,450.25,2.5\n"


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


def _oxigraph() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"head": {"vars": []}, "results": {"bindings": []}},
            headers={"Content-Type": "application/sparql-results+json"},
        )

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _dataset_with_source(tmp: Path) -> str:
    meta = registry.save_dataset(
        tmp / "registry",
        "Samples",
        {"diagram.md": "```mermaid\nclassDiagram\n  class Sample\n```\n"},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-08-18T00:00:00+00:00",
        proposal_md="",
    )
    sdir = tmp / "registry" / meta["id"] / "source"
    sdir.mkdir(parents=True)
    (sdir / "samples.csv").write_text(_CSV, encoding="utf-8")
    return str(meta["id"])


def test_materialize_reasserts_numeric_datatypes(tmp_path: Path) -> None:
    """A dropped ``datatype:`` turns numbers into strings, and SPARQL then
    compares them lexically — a range question answers WRONGLY instead of
    failing. So materialize re-asserts what the rows prove, with no LLM."""
    dataset_id = _dataset_with_source(tmp_path)
    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={
                "proposal_md": _SPEC_UNTYPED,
                "dataset_name": "Samples",
                "dataset_id": dataset_id,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # The repaired design comes back so the client's copy does not drift...
    assert "datatype: xsd:double" in body["proposal_md"]
    # ...and it is what got saved, so a later redesign starts from the fix.
    saved = registry.load_proposal(tmp_path / "registry", dataset_id) or ""
    assert "datatype: xsd:double" in saved
    assert "xsd:double" in (body["artifacts"].get("mapping.yaml") or "")


def test_materialize_without_a_source_is_unchanged(tmp_path: Path) -> None:
    """A brand-new design has no persisted source; nothing may be invented."""
    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/materialize",
            json={"proposal_md": _SPEC_UNTYPED, "dataset_name": "Samples"},
        )
    assert r.status_code == 200, r.text
    assert "proposal_md" not in r.json()  # absent == "we changed nothing"


def test_attach_source_answers_with_the_now_computable_advice(tmp_path: Path) -> None:
    """kantan runs materialize → attach → ingest, so the FIRST materialize has no
    source and "column X of your file is not used" could not be computed even
    once. The attach that follows is the first moment it is knowable."""
    dataset_id = _dataset_with_source(tmp_path)
    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        # Give the dataset a design that ignores one of the two columns.
        client.post(
            "/api/materialize",
            json={
                "proposal_md": _SPEC_UNTYPED,
                "dataset_name": "Samples",
                "dataset_id": dataset_id,
            },
        )
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("samples.csv", _CSV, "text/csv")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_files"] == ["samples.csv"]
    assert "advisories" in body and "validation_issues" in body
    # `pressure` is in the file and in no map: only the source can reveal that.
    assert any("pressure" in a for a in body["advisories"]), body["advisories"]


def test_repair_after_refine_stamps_the_datatype_without_an_llm(tmp_path: Path) -> None:
    """The refine backstop: validate + deterministic repair, zero LLM rounds when
    the machine can settle it alone."""
    (tmp_path / "samples.csv").write_text(_CSV, encoding="utf-8")
    calls: list[str] = []
    repaired, autocorrect = design_loop.repair_after_refine(
        _SPEC_UNTYPED,
        [tmp_path / "samples.csv"],
        tmp_path,
        llm=object(),  # must never be called
        on_llm_call=calls.append,
    )
    assert calls == []
    assert "datatype: xsd:double" in repaired
    assert autocorrect["converged"] is True
    # Zero on BOTH counts: the deterministic repair runs inside the very first
    # evaluation, so the person is never shown a problem the machine had already
    # settled. The datatype assertion above is what proves work happened.
    assert autocorrect["initial_issue_count"] == 0
    assert autocorrect["final_issue_count"] == 0


def test_repair_after_refine_reports_what_it_could_not_fix(tmp_path: Path) -> None:
    """A design naming a column the file does not have cannot be repaired
    deterministically; with no usable LLM the backstop must say so rather than
    quietly pass the broken document on to materialize."""
    (tmp_path / "samples.csv").write_text(_CSV, encoding="utf-8")
    broken = _SPEC_UNTYPED.replace("column: temp", "column: temperature_typo")

    class _Failing:
        model = "mock-model"

        def complete(self, system_prompt: str, user_message: str) -> str:
            raise RuntimeError("provider down")

    _, autocorrect = design_loop.repair_after_refine(
        broken, [tmp_path / "samples.csv"], tmp_path, llm=_Failing()
    )
    assert autocorrect["converged"] is False
    assert autocorrect["remaining_issues"]
    assert any("temperature_typo" in m for m in autocorrect["remaining_issues"])
