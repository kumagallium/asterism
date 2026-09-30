"""Tests for the ingest-rules transparency surface.

Three layers, all read-only for the user-facing half:

- registry: ``mapping.yaml`` (the reviewed §9 Mapping IR spec) persists like the
  other artifacts, and a redesign snapshots the PREVIOUS artifact set under
  ``history/`` before overwriting in place.
- ``GET /api/datasets/{id}/rules``: the deterministic human-readable projection
  of the persisted RML (+ model.yaml labels).
- ``GET /api/datasets/{id}/history[/{snapshot_id}]``: the redesign audit trail,
  with server-side unified diffs against the current artifacts.
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


_RML = """\
@prefix rr:  <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
@prefix ex:  <https://example.org/onto#> .

<#SampleMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sample/{sid}" ;
    rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:label ;
    rr:objectMap [ rml:reference "name" ] ] .
"""

_MODEL = """\
- Sample <https://example.org/resource/sample/s1>:
    - a: ex:Sample
    - ex:label:
        - name: "pellet A"
"""

# model.yaml CURIEs resolve against prefixes extracted from the RML/MIE text.
_MIE = "# prefixes\n# @prefix ex: <https://example.org/onto#> .\n"

# A VALID Mapping IR mirroring _RML — the rules endpoint parses it to merge
# reviewer-facing label/unit onto the projected rows (kantan-mode ADR K8).
_MAPPING_IR = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
maps:
  - name: SampleMap
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:label
        column: name
        label: "試料名"
        unit: "µV/K"
"""

_ARTIFACTS = {
    "diagram.md": "```mermaid\nclassDiagram\n  class Sample\n```\n",
    "model.yaml": _MODEL,
    "mie.yaml": "",
    "mapping.rml.ttl": _RML,
    "mapping.yaml": _MAPPING_IR,
}


def _save(tmp: Path) -> dict:
    return registry.save_dataset(
        tmp / "registry",
        "Samples",
        _ARTIFACTS,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )


# ---------------------------------------------------------------------------
# registry layer
# ---------------------------------------------------------------------------


def test_mapping_ir_is_persisted_and_flagged(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    assert (root / meta["id"] / "mapping.yaml").read_text() == _ARTIFACTS["mapping.yaml"]
    assert meta["has_mapping_ir"] is True

    data = registry.load_dataset(root, meta["id"])
    assert data is not None
    assert data["artifacts"]["mapping.yaml"] == _ARTIFACTS["mapping.yaml"]


def test_redesign_snapshots_previous_artifacts(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"

    # Same content → no snapshot (idempotent re-save must not pile up history).
    registry.update_dataset_artifacts(
        root, meta["id"], _ARTIFACTS,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v1\n",
    )
    assert registry.list_dataset_history(root, meta["id"]) == []

    changed = dict(_ARTIFACTS, **{"mapping.rml.ttl": _RML.replace('"name"', '"label"')})
    new_meta = registry.update_dataset_artifacts(
        root, meta["id"], changed,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v2\n",
    )
    assert new_meta is not None and new_meta["has_mapping_ir"] is True

    snapshots = registry.list_dataset_history(root, meta["id"])
    assert len(snapshots) == 1
    snap = registry.load_dataset_history(root, meta["id"], snapshots[0]["id"])
    assert snap is not None
    # The snapshot holds the PREVIOUS (v1) content, not the new one.
    assert '"name"' in snap["artifacts"]["mapping.rml.ttl"]
    assert snap["artifacts"]["proposal.md"] == "# design v1\n"
    # An artifact with no content is not stored as an empty file.
    assert "mie.yaml" not in snap["artifacts"]


def test_history_ids_are_validated(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    assert registry.load_dataset_history(root, meta["id"], "../escape") is None
    assert registry.load_dataset_history(root, "no-such", "20260711T000000Z") is None
    assert registry.list_dataset_history(root, "../escape") == []


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------


def test_rules_endpoint_projects_mapping(tmp_path: Path, healthy_client) -> None:
    meta = _save(tmp_path)
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["warnings"] == []
        assert len(body["maps"]) == 1
        m = body["maps"][0]
        assert m["id"] == "SampleMap"
        assert m["source"] == "samples.csv"
        assert m["subject"]["classes"] == ["ex:Sample"]
        rows = {row["predicate"]: row for row in m["properties"]}
        assert rows["ex:label"]["reference"] == "name"
        # Mapping IR display metadata is merged by expanded predicate IRI
        # (kantan-mode ADR K8).
        assert rows["ex:label"]["label"] == "試料名"
        assert rows["ex:label"]["unit"] == "µV/K"
        # model.yaml labels ride along, keyed by full IRI.
        assert body["labels"].get("https://example.org/onto#Sample") == "Sample"

        r404 = client.get("/api/datasets/nope/rules")
        assert r404.status_code == 404


def test_rules_endpoint_autocompletes_bracketed_unit(tmp_path: Path, healthy_client) -> None:
    """A single-column property with no authored unit but a bracketed column name
    ("Resistivity(Ohm m)") gets its display unit filled deterministically in the
    projection (task #10) — so an IR saved without the unit still shows it."""
    rml = _RML.replace('rml:reference "name"', 'rml:reference "Resistivity(Ohm m)"')
    ir = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
maps:
  - name: SampleMap
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:label
        column: "Resistivity(Ohm m)"
"""
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "Samples",
        dict(_ARTIFACTS, **{"mapping.rml.ttl": rml, "mapping.yaml": ir}),
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        rows = {row["predicate"]: row for row in r.json()["maps"][0]["properties"]}
        assert rows["ex:label"]["unit"] == "Ohm m"


def test_rules_endpoint_warns_on_unparsable_ir(tmp_path: Path, healthy_client) -> None:
    """A broken mapping.yaml must degrade to a warning, never fail the
    read-only projection (and never invent label/unit)."""
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "Samples",
        dict(_ARTIFACTS, **{"mapping.yaml": "version: 1\nmaps:\n  - id: nope\n"}),
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
        assert any("mapping.yaml" in w for w in body["warnings"])
        rows = {row["predicate"]: row for row in body["maps"][0]["properties"]}
        assert "label" not in rows["ex:label"]


def test_history_endpoints_list_and_diff(tmp_path: Path, healthy_client) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    changed = dict(_ARTIFACTS, **{"mapping.rml.ttl": _RML.replace('"name"', '"label"')})
    registry.update_dataset_artifacts(
        root, meta["id"], changed,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v2\n",
    )

    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        listing = client.get(f"/api/datasets/{meta['id']}/history")
        assert listing.status_code == 200
        body = listing.json()
        assert body["count"] == 1
        snap_id = body["snapshots"][0]["id"]
        assert "mapping.rml.ttl" in body["snapshots"][0]["artifacts"]

        detail = client.get(f"/api/datasets/{meta['id']}/history/{snap_id}")
        assert detail.status_code == 200
        d = detail.json()
        # Only genuinely changed files carry a diff; direction is snapshot → current.
        assert set(d["diffs"]) == {"mapping.rml.ttl", "proposal.md"}
        diff = d["diffs"]["mapping.rml.ttl"]
        assert '-  rr:predicateObjectMap [ rr:predicate ex:label ;' not in diff
        assert '-    rr:objectMap [ rml:reference "name" ] ] .' in diff
        assert '+    rr:objectMap [ rml:reference "label" ] ] .' in diff

        assert client.get(f"/api/datasets/{meta['id']}/history/does-not-exist").status_code == 404
        assert client.get("/api/datasets/nope/history").status_code == 404


def test_a_retired_artifact_is_not_diffed_as_a_deletion(
    tmp_path: Path, healthy_client
) -> None:
    """Snapshots taken before ingester.py was removed still hold the file. It is
    no longer a design artifact, so the history view must ignore it — diffing it
    against nothing would show a whole-file deletion the person never made."""
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    changed = dict(_ARTIFACTS, **{"mapping.rml.ttl": _RML.replace('"name"', '"label"')})
    registry.update_dataset_artifacts(
        root, meta["id"], changed,
        complete=True, warnings=[], traps=[], exit_code=0, proposal_md="# design v2\n",
    )
    snap_id = registry.list_dataset_history(root, meta["id"])[0]["id"]
    # A pre-removal snapshot: the retired script is on disk inside the snapshot.
    (root / meta["id"] / "history" / snap_id / "ingester.py").write_text(
        "def go(): ...\n", encoding="utf-8"
    )

    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        d = client.get(f"/api/datasets/{meta['id']}/history/{snap_id}").json()
    # Still readable as history…
    assert "ingester.py" in d["snapshot"]["artifacts"]
    # …but never presented as a change.
    assert "ingester.py" not in d["diffs"]


def test_a_unit_that_is_just_the_column_name_again_is_not_shown_as_a_unit() -> None:
    """A weak model fills K8's optional label:/unit: pair with the same string.

    The label side already drops that echo before display; the unit side did not,
    so a text column arrived at the review screen carrying `unit: Name` and a
    person was asked to confirm a unit for a chemical name (2026-08-19 review).
    Only the echo is dropped — an unrecognised unit is still someone's unit.
    """
    from asterism_api.main import _ir_predicate_display

    ir = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
maps:
  - name: SampleMap
    source: samples.csv
    subject:
      template: "exr:sample/{No}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:name
        column: Name
        label: "Name"
        unit: "Name"
      - predicate: ex:sampleId
        column: No
        unit: "sample_id"
      - predicate: ex:temperature
        column: "Measurement temp.(C)"
        unit: "C"
      - predicate: ex:pressure
        column: Pressure
        unit: "kPa"
"""
    meta = _ir_predicate_display(ir)
    ex = "https://example.org/onto#"
    # Echoes of the column heading / of the predicate's own local name: dropped.
    assert "unit" not in meta.get(f"{ex}name", {})
    assert "unit" not in meta.get(f"{ex}sampleId", {})
    # Real units survive — both the authored one and the one derived from the
    # column's own parentheses.
    assert meta[f"{ex}temperature"]["unit"] == "C"
    assert meta[f"{ex}pressure"]["unit"] == "kPa"


def test_same_predicate_in_two_maps_keeps_each_row_its_own_label() -> None:
    """同じ述語を複数の map が束縛しても、行のラベルは取り違えない。

    ⭐値のカタログには `ensure_value_catalog_labels` が**全部に** `rdfs:label` を
    書く。表示メタを述語 IRI だけで引いていたため、最後の 1 つが全部を塗り、
    実機で「化学組成」「DOI」など 6 つのカタログが揃って『縦軸単位』と表示された
    （利用者報告 2026-09-02）。行は (述語, 列) で引く。
    """
    from asterism_api.main import _merge_ir_display_metadata

    ir = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
  rdfs: "http://www.w3.org/2000/01/rdf-schema#"
maps:
  - name: composition
    source: c.csv
    subject:
      template: "exr:composition/{composition}"
      classes: [ex:Composition]
    properties:
      - predicate: rdfs:label
        column: composition
        label: "化学組成"
  - name: unit_y
    source: c.csv
    subject:
      template: "exr:unit_y/{unit_y}"
      classes: [ex:UnitY]
    properties:
      - predicate: rdfs:label
        column: unit_y
        label: "縦軸単位"
"""
    rdfs_label = "http://www.w3.org/2000/01/rdf-schema#label"
    summary = {
        "maps": [
            {
                "id": "CompositionMap",
                "properties": [
                    {"predicate": "rdfs:label", "predicate_iri": rdfs_label,
                     "reference": "composition"}
                ],
            },
            {
                "id": "UnitYMap",
                "properties": [
                    {"predicate": "rdfs:label", "predicate_iri": rdfs_label,
                     "reference": "unit_y"}
                ],
            },
        ]
    }
    _merge_ir_display_metadata(ir, summary)
    labels = {m["id"]: m["properties"][0].get("label") for m in summary["maps"]}
    assert labels == {"CompositionMap": "化学組成", "UnitYMap": "縦軸単位"}


def test_ambiguous_predicate_never_borrows_another_maps_label() -> None:
    """列で引けなかった行は、その述語が**複数列**に束縛されているなら
    述語ごとの全体像に落ちない（落とすと他の map のラベルを借りてしまう）。
    束縛が 1 列だけの述語は従来どおり落ちる（取り違えようがない）。"""
    from asterism_api.main import _merge_ir_display_metadata

    ir = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
  rdfs: "http://www.w3.org/2000/01/rdf-schema#"
maps:
  - name: a
    source: c.csv
    subject:
      template: "exr:a/{a}"
      classes: [ex:A]
    properties:
      - predicate: rdfs:label
        column: a
        label: "Aの名前"
  - name: b
    source: c.csv
    subject:
      template: "exr:b/{b}"
      classes: [ex:B]
    properties:
      - predicate: rdfs:label
        column: b
        label: "Bの名前"
  - name: rec
    source: c.csv
    subject:
      template: "exr:rec/{id}"
      classes: [ex:Rec]
    properties:
      - predicate: ex:only
        column: only
        label: "唯一の項目"
"""
    rdfs_label = "http://www.w3.org/2000/01/rdf-schema#label"
    summary = {
        "maps": [
            # 列が読めない行（関数・定数など）を模す: reference なし
            {"id": "AMap", "properties": [
                {"predicate": "rdfs:label", "predicate_iri": rdfs_label}]},
            {"id": "RecMap", "properties": [
                {"predicate": "ex:only",
                 "predicate_iri": "https://example.org/onto#only"}]},
        ]
    }
    _merge_ir_display_metadata(ir, summary)
    # 曖昧な述語: 借りない（ラベル無し → 後段の _fill_missing_labels に任せる）
    assert summary["maps"][0]["properties"][0].get("label") is None
    # 一意な述語: 従来どおり落ちる
    assert summary["maps"][1]["properties"][0].get("label") == "唯一の項目"


def test_column_heading_fallback_is_each_maps_own_column(
    tmp_path: Path, healthy_client
) -> None:
    """③（列見出しの fallback）も行の列で引く。

    #554 が直したのは①（authored label）の取り違え。ラベルを書いていない値の
    カタログ（`ensure_value_catalog_labels` が書く `{column, predicate: rdfs:label}`
    そのもの）は③に落ちる — そこが述語だけの辞書のままだと、最後のカタログの
    列見出しが全部の行に乗る（同じ事故の残り半分）。"""
    from asterism_step0.mapping_ir import parse_mapping_ir
    from asterism_step0.rml_compile import compile_mapping_ir

    ir = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
  rdfs: "http://www.w3.org/2000/01/rdf-schema#"
maps:
  - name: doi
    source: records.csv
    subject:
      template: "exr:doi/{文献DOI}"
      classes: [ex:Doi]
    properties:
      - predicate: rdfs:label
        column: 文献DOI
  - name: unit_y
    source: records.csv
    subject:
      template: "exr:unit/{縦軸単位}"
      classes: [ex:UnitY]
    properties:
      - predicate: rdfs:label
        column: 縦軸単位
"""
    artifacts = dict(
        _ARTIFACTS,
        **{
            "mapping.rml.ttl": compile_mapping_ir(parse_mapping_ir(ir)),
            "mapping.yaml": ir,
            "model.yaml": "",  # ② が無い設計: ③ が実際に効く経路
        },
    )
    meta = registry.save_dataset(
        tmp_path / "registry",
        "Records",
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-02T00:00:00+00:00",
        proposal_md="",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["warnings"] == []
        rdfs_label = "http://www.w3.org/2000/01/rdf-schema#label"
        got = {
            m["id"]: next(
                row["label"] for row in m["properties"] if row["predicate_iri"] == rdfs_label
            )
            for m in body["maps"]
        }
        assert got == {"DoiMap": "文献DOI", "UnitYMap": "縦軸単位"}


def test_projected_local_name_does_not_block_the_column_heading(
    tmp_path: Path, healthy_client
) -> None:
    """model.yaml の投影が項目に付ける名前（いつもローカル名）は、答えに数えない。

    投影に名前があると「答え済み」として③（列の見出し）と④（読みくだし）に
    進まず、表示名の無い行は図でローカル名のまま出ていた（K52 で残した穴）。"""
    from asterism_step0.mapping_ir import parse_mapping_ir
    from asterism_step0.rml_compile import compile_mapping_ir

    ir = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
maps:
  - name: sample
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:hasWeight
        column: 重さ
      - predicate: ex:hasBatchCode
        column: code
"""
    model = """\
- Sample <https://example.org/resource/sample/s1>:
    - a: ex:Sample
    - ex:hasWeight:
        - weight: 1.5
    - ex:hasBatchCode:
        - code: "B-1"
"""
    artifacts = dict(
        _ARTIFACTS,
        **{
            "mapping.rml.ttl": compile_mapping_ir(parse_mapping_ir(ir)),
            "mapping.yaml": ir,
            "model.yaml": model,
            "mie.yaml": _MIE,
        },
    )
    meta = registry.save_dataset(
        tmp_path / "registry",
        "Samples",
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-30T00:00:00+00:00",
        proposal_md="",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
        weight = "https://example.org/onto#hasWeight"
        code = "https://example.org/onto#hasBatchCode"
        # 投影はローカル名を持っている（前提の確認）
        assert body["labels"].get(weight) == "hasWeight"
        rows = {row["predicate_iri"]: row for row in body["maps"][0]["properties"]}
        # ③ 列の見出し。ローカル名（hasWeight・hasBatchCode）ではない
        assert rows[weight]["label"] == "重さ"
        assert rows[code]["label"] == "code"


def test_rules_labels_prefer_the_authored_kind_name(tmp_path: Path, healthy_client) -> None:
    """IR の subject.label が、model.yaml の種類のラベル（ローカル名）に勝つ。"""
    labelled = _MAPPING_IR.replace(
        "      classes: [ex:Sample]\n", '      classes: [ex:Sample]\n      label: "食材の名前"\n'
    )
    meta = registry.save_dataset(
        tmp_path / "registry",
        "Samples",
        dict(_ARTIFACTS, **{"mapping.yaml": labelled}),
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
        assert body["labels"]["https://example.org/onto#Sample"] == "食材の名前"


def test_surgical_repair_keeps_subject_label() -> None:
    """全 IR 修復の答えに label が無くても、直前の IR の種類の表示名が残る。"""
    import json as _json

    from asterism_api.design_loop import _surgical_spec_repair

    class _Llm:
        def complete(self, system: str, user: str) -> str:
            import yaml

            doc = yaml.safe_load(_MAPPING_IR.replace('      label: "食材の名前"\n', ""))
            return _json.dumps(doc)

    labelled = _MAPPING_IR.replace(
        "      classes: [ex:Sample]\n", '      classes: [ex:Sample]\n      label: "食材の名前"\n'
    )
    md = f"# Title\n\n### 9. Declarative mapping spec\n\n```yaml\n{labelled}```\n\n### tail\n"
    out = _surgical_spec_repair(_Llm(), md, labelled, [], "")
    assert "食材の名前" in out


# ---------------------------------------------------------------------------
# 種類の名前は、ワークスペースと同じ読み手（class_schema.class_label）で引く
# ---------------------------------------------------------------------------

_KIND_IRI = "https://example.org/onto#Sample"

# IR に種類の表示名（subject.label）は無く、model.yaml の ``classes:`` にだけある。
_MODEL_WITH_KIND_NAME = """\
classes:
  ex:Sample:
    label: "貸し出しの記録"
properties:
  ex:label:
    domain: ex:Sample
"""


def _save_promoted(tmp: Path, artifacts: dict) -> dict:
    meta = registry.save_dataset(
        tmp / "registry",
        "Samples",
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-11T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    graph = f"https://kumagallium.github.io/asterism/graph/canonical/{meta['id']}"
    registry.mark_promoted(
        tmp / "registry",
        meta["id"],
        triples_promoted=1,
        alignment={"predicates": {"reuse": [], "new": []}, "classes": {"reuse": [], "new": []}},
        promoted_at="2026-07-11T01:00:00+00:00",
        canonical_graph=graph,
        live_graph=f"{graph}/v1",
    )
    return meta


def _store(answer) -> OxigraphClient:
    """``answer(query) -> bindings`` で答える偽のストア。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/query":
            return httpx.Response(204)
        rows = answer(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            text=json.dumps({"head": {"vars": []}, "results": {"bindings": rows}}),
            headers={"content-type": "application/sparql-results+json"},
        )

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _workspace_name(tmp: Path, store: OxigraphClient, class_iri: str) -> str:
    """ワークスペース（GET /api/classes）が種類に付ける名前 — 同じ 1 関数を直に呼ぶ。"""
    import asyncio

    from asterism import class_schema

    return asyncio.run(class_schema.class_label(store, tmp / "registry", class_iri))


def test_rules_kind_name_is_read_from_model_yaml(tmp_path: Path) -> None:
    """IR に表示名が無く model.yaml にある種類は、図に渡る名前も表示名になる。"""
    meta = _save_promoted(tmp_path, dict(_ARTIFACTS, **{"model.yaml": _MODEL_WITH_KIND_NAME}))
    app = build_app(_settings(tmp_path), oxigraph_client=_store(lambda q: []), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    assert body["labels"][_KIND_IRI] == "貸し出しの記録"
    assert body["labels"][_KIND_IRI] == _workspace_name(tmp_path, _store(lambda q: []), _KIND_IRI)


def test_rules_kind_name_is_read_from_the_ontology_graph(tmp_path: Path) -> None:
    """IR にも model.yaml にも無く、公開したオントロジーの graph にある名前も届く。"""

    def answer(query: str) -> list[dict]:
        if f"<{_KIND_IRI}>" in query and "rdf-schema#Class" in query:
            return [{"label": {"type": "literal", "value": "貸し出しの記録"}}]
        return []

    meta = _save_promoted(tmp_path, _ARTIFACTS)
    app = build_app(_settings(tmp_path), oxigraph_client=_store(answer), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    assert body["labels"][_KIND_IRI] == "貸し出しの記録"
    assert body["labels"][_KIND_IRI] == _workspace_name(tmp_path, _store(answer), _KIND_IRI)


def test_rules_kind_name_prefers_the_design_in_hand(tmp_path: Path) -> None:
    """この設計の IR の表示名が先 — 公開する前の直しが、図にすぐ出る。"""
    labelled = _MAPPING_IR.replace(
        "      classes: [ex:Sample]\n", '      classes: [ex:Sample]\n      label: "返した記録"\n'
    )
    meta = _save_promoted(
        tmp_path,
        dict(_ARTIFACTS, **{"model.yaml": _MODEL_WITH_KIND_NAME, "mapping.yaml": labelled}),
    )
    asked: list[str] = []

    def answer(query: str) -> list[dict]:
        asked.append(query)
        return []

    app = build_app(_settings(tmp_path), oxigraph_client=_store(answer), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        asked.clear()
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    assert body["labels"][_KIND_IRI] == "返した記録"
    assert not [q for q in asked if f"<{_KIND_IRI}>" in q]  # 名前があるので聞きに行かない


def test_rules_do_not_make_up_a_kind_name(tmp_path: Path, healthy_client) -> None:
    """どこにも名前が無い種類に、名前を作って足さない（今までどおり）。"""
    no_model = dict(_ARTIFACTS, **{"model.yaml": ""})
    meta = _save_promoted(tmp_path, no_model)
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    assert _KIND_IRI not in body["labels"]


def test_rules_survive_a_store_that_cannot_answer_kind_names(tmp_path: Path) -> None:
    """ストアが答えられなくても /rules は返る（名前は元の投影のまま）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query" and b"?label" in request.content:
            return httpx.Response(500, text="store is down")
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    store = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)
    meta = _save_promoted(tmp_path, _ARTIFACTS)
    app = build_app(_settings(tmp_path), oxigraph_client=store, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/rules")
    assert r.status_code == 200, r.text
    assert r.json()["labels"][_KIND_IRI] == "Sample"
    assert len(r.json()["maps"]) == 1


def test_rules_kind_name_falls_back_like_the_workspace(tmp_path: Path) -> None:
    """どこにも名前が無い種類は、ワークスペースと同じ読みくだしになる。"""
    iri = "https://example.org/onto#LoanRecord"
    artifacts = {
        name: text.replace("ex:Sample", "ex:LoanRecord") for name, text in _ARTIFACTS.items()
    }
    meta = _save_promoted(tmp_path, dict(artifacts, **{"model.yaml": ""}))
    app = build_app(_settings(tmp_path), oxigraph_client=_store(lambda q: []), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    assert body["labels"][iri] == "Loan Record"
    assert body["labels"][iri] == _workspace_name(tmp_path, _store(lambda q: []), iri)


def test_rules_ask_for_kind_names_once_each(tmp_path: Path) -> None:
    """同じ種類を何度も聞かない（種類が 2 つの map にあっても 1 回）。"""
    second_map = """\
  - name: SampleNoteMap
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:note
        column: note
"""
    second_rml = """
<#SampleNoteMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sample/{sid}" ;
    rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:note ;
    rr:objectMap [ rml:reference "note" ] ] .
"""
    asked: list[str] = []

    def answer(query: str) -> list[dict]:
        asked.append(query)
        return []

    meta = _save_promoted(
        tmp_path,
        dict(
            _ARTIFACTS,
            **{"mapping.yaml": _MAPPING_IR + second_map, "mapping.rml.ttl": _RML + second_rml},
        ),
    )
    app = build_app(_settings(tmp_path), oxigraph_client=_store(answer), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        asked.clear()
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    assert len(body["maps"]) == 2
    about_the_kind = [q for q in asked if f"<{_KIND_IRI}>" in q and "rdf-schema#Class" in q]
    assert len(about_the_kind) == 1


def test_rules_name_every_kind_of_the_bundled_sample(tmp_path: Path) -> None:
    """同梱の見本の種類は、図に渡る名前がどれも表示名（ローカル名ではない）。"""
    sample = Path(__file__).resolve().parents[2] / "datasets" / "world"
    artifacts = {
        name: (sample / name).read_text(encoding="utf-8")
        for name in ("model.yaml", "mie.yaml", "mapping.rml.ttl", "mapping.yaml")
    }
    meta = _save_promoted(tmp_path, dict(artifacts, **{"diagram.md": ""}))
    app = build_app(_settings(tmp_path), oxigraph_client=_store(lambda q: []), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        body = client.get(f"/api/datasets/{meta['id']}/rules").json()
    names = {m["id"]: body["labels"].get(m["subject"]["class_iris"][0]) for m in body["maps"]}
    assert names == {
        "ActivityMap": "取り込みの記録",
        "CountryMap": "国",
        "ObservationMap": "年ごとの記録",
    }


# ---------------------------------------------------------------------------
# 行の表示名の引き方（述語×行が読む列 → 種類×述語 → 述語だけ）
# ---------------------------------------------------------------------------

_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def _ir_header(*maps: str) -> str:
    return (
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://example.org/onto#"\n'
        '  exr: "https://example.org/resource/"\n'
        '  rdfs: "http://www.w3.org/2000/01/rdf-schema#"\n'
        "maps:\n" + "".join(maps)
    )


def _real_summary(ir: str) -> dict:
    """IR → 本物のコンパイル → 本物の要約（手書きの要約に頼らない）。"""
    from asterism.rml_summary import summarize_rml
    from asterism_step0.mapping_ir import parse_mapping_ir
    from asterism_step0.rml_compile import compile_mapping_ir

    return summarize_rml(compile_mapping_ir(parse_mapping_ir(ir)))


def _resolve(ir: str) -> dict:
    """/rules ハンドラと同じ順（merge → fill）で行の表示名を決めた要約。"""
    from asterism_api.main import (
        _fill_missing_labels,
        _ir_display_by_column,
        _ir_predicate_display,
        _merge_ir_display_metadata,
    )

    summary = _real_summary(ir)
    ir_meta = _merge_ir_display_metadata(ir, summary)
    assert ir_meta == _ir_predicate_display(ir)
    _fill_missing_labels(summary, {}, ir_meta, _ir_display_by_column(ir))
    return summary


def _rows(summary: dict, map_id: str, predicate_iri: str) -> list[dict]:
    return [
        r
        for m in summary["maps"]
        if m["id"] == map_id
        for r in m["properties"]
        if r["predicate_iri"] == predicate_iri
    ]


_MAP_COMP = """\
  - name: comp
    source: c.csv
    subject:
      template: "exr:comp/{comp}"
      classes: [ex:Comp]
    properties:
      - predicate: rdfs:label
        column: comp
        label: "組成"
"""
_MAP_UNIT = """\
  - name: unit_y
    source: c.csv
    subject:
      template: "exr:unit/{unit_y}"
      classes: [ex:UnitY]
    properties:
      - predicate: rdfs:label
        column: unit_y
        label: "縦軸単位"
"""


def test_rows_without_a_column_get_their_own_kinds_label() -> None:
    """同じ述語をいくつもの種類が束縛しても、定数の行・テンプレートの行は
    自分の種類の表示名になる（列の行の表示名を借りない・述語だけで落ちない）。"""
    ir = _ir_header(
        _MAP_COMP,
        _MAP_UNIT,
        """\
  - name: prov
    source: c.csv
    subject:
      template: "exr:prov/{pid}"
      classes: [ex:Prov]
    properties:
      - predicate: rdfs:label
        constant: "manual"
        label: "来歴"
  - name: yearly
    source: c.csv
    subject:
      template: "exr:yearly/{yid}"
      classes: [ex:Yearly]
    properties:
      - predicate: rdfs:label
        object_template: "Year {y}"
        object_type: literal
        label: "年ごとの記録"
""",
    )
    summary = _resolve(ir)
    (prov_row,) = _rows(summary, "ProvMap", _LABEL)
    (yearly_row,) = _rows(summary, "YearlyMap", _LABEL)
    assert prov_row["kind"] == "constant"
    assert yearly_row["kind"] == "template"
    assert prov_row["label"] == "来歴"
    assert yearly_row["label"] == "年ごとの記録"
    assert _rows(summary, "CompMap", _LABEL)[0]["label"] == "組成"
    assert _rows(summary, "UnitYMap", _LABEL)[0]["label"] == "縦軸単位"


def test_function_row_reading_one_column_is_found_by_that_column() -> None:
    """関数を通して列を 1 つ読む行（最上位に reference が無い）も、
    複数の列に束縛された述語で自分の表示名になる。"""
    ir = _ir_header(
        _MAP_COMP,
        """\
  - name: doi
    source: c.csv
    subject:
      template: "exr:doi/{doi}"
      classes: [ex:Doi]
    properties:
      - predicate: rdfs:label
        column: doi
        function: slug
        label: "文献DOI"
""",
    )
    summary = _resolve(ir)
    (row,) = _rows(summary, "DoiMap", _LABEL)
    assert row["kind"] == "function"
    assert "reference" not in row
    assert row["label"] == "文献DOI"


def test_templates_with_different_strings_each_keep_their_own_label() -> None:
    """同じ種類・同じ述語に、文字列の違うテンプレートの行が 2 つあれば、
    表示名が違っても、それぞれ自分の表示名になる（文字列で見分けられる）。"""
    ir = _ir_header(
        _MAP_COMP,
        _MAP_UNIT,
        """\
  - name: yearly
    source: c.csv
    subject:
      template: "exr:yearly/{yid}"
      classes: [ex:Yearly]
    properties:
      - predicate: rdfs:label
        object_template: "Year {y}"
        object_type: literal
        label: "年"
      - predicate: rdfs:label
        object_template: "Month {m}"
        object_type: literal
        label: "月"
""",
    )
    summary = _resolve(ir)
    rows = _rows(summary, "YearlyMap", _LABEL)
    assert {r["template"]: r["label"] for r in rows} == {"Year {y}": "年", "Month {m}": "月"}


def test_rows_that_the_rml_cannot_tell_apart_borrow_nothing() -> None:
    """同じ種類・同じ述語に、RML に書き出される文字列では見分けられない行が 2 つ
    （どちらも複数の列を読む関数）あり表示名が違うなら、どちらにも付けない。"""
    ir = _ir_header(
        """\
  - name: rec
    source: c.csv
    subject:
      template: "exr:rec/{rid}"
      classes: [ex:Rec]
    properties:
      - predicate: ex:v
        columns: [a, b]
        function: float_array_count
        datatype: xsd:integer
        label: "一つ目"
      - predicate: ex:v
        columns: [c, d]
        function: float_array_count
        datatype: xsd:integer
        label: "二つ目"
""",
    )
    summary = _resolve(ir)
    rows = _rows(summary, "RecMap", "https://example.org/onto#v")
    assert len(rows) == 2
    # 種類×述語の引きは割れて黙る。述語の読み下しも述語名と同じなので無印。
    assert all(r.get("label") is None for r in rows)


def test_constant_row_never_borrows_the_column_rows_label() -> None:
    """同じ種類・同じ述語の列の行（表示名 A）と定数の行（表示名 B）は、
    定数の行が B になる（A を借りない）。"""
    ir = _ir_header(
        _MAP_UNIT,
        """\
  - name: mix
    source: c.csv
    subject:
      template: "exr:mix/{mid}"
      classes: [ex:Mix]
    properties:
      - predicate: rdfs:label
        column: mname
        label: "A 列の名前"
      - predicate: rdfs:label
        constant: "fixed"
        label: "B 定数の名前"
""",
    )
    summary = _resolve(ir)
    by_kind = {r["kind"]: r["label"] for r in _rows(summary, "MixMap", _LABEL)}
    assert by_kind == {"reference": "A 列の名前", "constant": "B 定数の名前"}


def test_column_rows_of_one_kind_keep_their_own_column_heading() -> None:
    """同じ種類・同じ述語を 2 つの列が束縛し、どちらにも表示名が無いとき、
    列の行はそれぞれ自分の列の見出しになる（種類×述語の引き方に流れない）。"""
    ir = _ir_header(
        """\
  - name: rec
    source: c.csv
    subject:
      template: "exr:rec/{rid}"
      classes: [ex:Rec]
    properties:
      - predicate: ex:v
        column: alpha
      - predicate: ex:v
        column: beta
""",
    )
    summary = _resolve(ir)
    got = {
        r["reference"]: r["label"] for r in _rows(summary, "RecMap", "https://example.org/onto#v")
    }
    assert got == {"alpha": "alpha", "beta": "beta"}


_LINK_MAPS = """\
  - name: parent
    source: c.csv
    subject:
      template: "exr:parent/{pid}"
      classes: [ex:Parent]
      label: "親の種類"
    properties:
      - predicate: ex:n
        column: pn
  - name: bare
    source: c.csv
    subject:
      template: "exr:bare/{bid}"
      classes: [ex:Bare]
    properties:
      - predicate: ex:n
        column: bn
  - name: child
    source: c.csv
    subject:
      template: "exr:child/{cid}"
      classes: [ex:Child]
    properties:
      - predicate: ex:belongsTo
        object_template: "exr:parent/{pid}"
      - predicate: ex:pointsAt
        object_template: "exr:bare/{bid}"
      - predicate: ex:named
        object_template: "exr:parent/{pid}"
        label: "書いてある名前"
"""


def test_link_row_reads_the_target_kinds_label() -> None:
    """表示名の無いつなぐ行は、つなぐ先の種類の表示名になる。
    つなぐ先に表示名が無ければ付かない。行に書いた表示名が勝つ。"""
    summary = _resolve(_ir_header(_LINK_MAPS))
    onto = "https://example.org/onto#"
    (belongs,) = _rows(summary, "ChildMap", onto + "belongsTo")
    (points,) = _rows(summary, "ChildMap", onto + "pointsAt")
    (named,) = _rows(summary, "ChildMap", onto + "named")
    assert belongs["label"] == "親の種類"
    assert named["label"] == "書いてある名前"
    # つなぐ先に表示名が無い: 付かないので、述語の読み下し（今までどおり）に落ちる
    assert points["label"] == "points At"


def _saved_client(tmp_path: Path, healthy_client, ir: str) -> tuple[TestClient, str]:
    from asterism_step0.mapping_ir import parse_mapping_ir
    from asterism_step0.rml_compile import compile_mapping_ir

    artifacts = dict(
        _ARTIFACTS,
        **{
            "mapping.rml.ttl": compile_mapping_ir(parse_mapping_ir(ir)),
            "mapping.yaml": ir,
            "model.yaml": "",
        },
    )
    meta = registry.save_dataset(
        tmp_path / "registry",
        "Records",
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-30T00:00:00+00:00",
        proposal_md="",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH), meta["id"]


def test_rules_endpoint_names_columnless_rows_and_links(tmp_path: Path, healthy_client) -> None:
    """/rules の返り値で、列を読まない行とつなぐ行が人向けの名前になる。"""
    ir = _ir_header(
        _MAP_COMP,
        _MAP_UNIT,
        """\
  - name: prov
    source: c.csv
    subject:
      template: "exr:prov/{pid}"
      classes: [ex:Prov]
    properties:
      - predicate: rdfs:label
        constant: "manual"
        label: "来歴"
""",
        _LINK_MAPS,
    )
    client, dataset_id = _saved_client(tmp_path, healthy_client, ir)
    with client:
        r = client.get(f"/api/datasets/{dataset_id}/rules")
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["warnings"] == []
    (prov_row,) = _rows(body, "ProvMap", _LABEL)
    assert prov_row["label"] == "来歴"
    (belongs,) = _rows(body, "ChildMap", "https://example.org/onto#belongsTo")
    assert belongs["label"] == "親の種類"


def test_a_composed_value_is_never_named_after_one_of_its_columns() -> None:
    """変換つきのテンプレートは、引数に生の列が 1 つだけ残っていても、その列を
    読む行ではない。同じ述語でその列を読む行の表示名を借りない。"""
    ir = _ir_header(
        _MAP_UNIT,
        """\
  - name: mix
    source: c.csv
    subject:
      template: "exr:mix/{mid}"
      classes: [ex:Mix]
    properties:
      - predicate: ex:ref
        column: b
        function: iri_safe
        object_type: iri
        label: "列 b の名前"
      - predicate: ex:ref
        object_template: "exr:pair/{a}-{b}"
        transform:
          a: slug
        label: "組み立てた名前"
""",
    )
    summary = _resolve(ir)
    rows = _rows(summary, "MixMap", "https://example.org/onto#ref")
    got = {r["function"]: r["label"] for r in rows}
    assert got == {"iri_safe": "列 b の名前", "template": "組み立てた名前"}


def test_a_template_without_placeholders_is_matched_as_the_constant_it_compiles_to() -> None:
    """穴あきの無いテンプレートは RML では定数になる。同じ種類・同じ述語に
    穴あきのあるテンプレートが並んでいても、それぞれ自分の表示名になる。"""
    ir = _ir_header(
        _MAP_COMP,
        _MAP_UNIT,
        """\
  - name: mix
    source: c.csv
    subject:
      template: "exr:mix/{mid}"
      classes: [ex:Mix]
    properties:
      - predicate: rdfs:label
        object_template: "fixed words"
        object_type: literal
        label: "決まった言葉"
      - predicate: rdfs:label
        object_template: "No. {mid}"
        object_type: literal
        label: "番号つきの見出し"
""",
    )
    summary = _resolve(ir)
    by_kind = {r["kind"]: r["label"] for r in _rows(summary, "MixMap", _LABEL)}
    assert by_kind == {"constant": "決まった言葉", "template": "番号つきの見出し"}


_ONTO = "https://example.org/onto#"


def _parents_and_child(child_rows: str) -> str:
    return _ir_header(
        """\
  - name: pa
    source: c.csv
    subject:
      template: "exr:pa/{pid}"
      classes: [ex:Pa]
      label: "親A"
    properties:
      - predicate: ex:n
        column: pn
  - name: pb
    source: c.csv
    subject:
      template: "exr:pb/{qid}"
      classes: [ex:Pb]
      label: "親B"
    properties:
      - predicate: ex:n
        column: qn
  - name: child
    source: c.csv
    subject:
      template: "exr:child/{cid}"
      classes: [ex:Child]
    properties:
"""
        + child_rows
    )


def test_two_links_on_one_predicate_each_read_their_own_parents_label() -> None:
    """同じ述語で別々の親へつなぐ行が 2 本あれば、それぞれつなぐ先の表示名になる。
    行に表示名を書いてあれば、それぞれ自分の表示名になる。"""
    ir = _parents_and_child(
        """\
      - predicate: ex:belongsTo
        object_template: "exr:pa/{pid}"
      - predicate: ex:belongsTo
        object_template: "exr:pb/{qid}"
"""
    )
    rows = _rows(_resolve(ir), "ChildMap", _ONTO + "belongsTo")
    assert sorted(r["label"] for r in rows) == ["親A", "親B"]

    authored = _parents_and_child(
        """\
      - predicate: ex:belongsTo
        object_template: "exr:pa/{pid}"
        label: "書いた一"
      - predicate: ex:belongsTo
        object_template: "exr:pb/{qid}"
        label: "書いた二"
"""
    )
    rows = _rows(_resolve(authored), "ChildMap", _ONTO + "belongsTo")
    assert sorted(r["label"] for r in rows) == ["書いた一", "書いた二"]


def test_a_constant_row_never_takes_another_kinds_column_label() -> None:
    """種類 A が列で読む述語を、種類 B が定数で持つとき、B の定数の行に A の表示名は付かない。"""
    ir = _ir_header(
        """\
  - name: ka
    source: c.csv
    subject:
      template: "exr:ka/{kid}"
      classes: [ex:Ka]
    properties:
      - predicate: rdfs:label
        column: nm
        label: "A の名前"
  - name: kb
    source: c.csv
    subject:
      template: "exr:kb/{kid}"
      classes: [ex:Kb]
    properties:
      - predicate: rdfs:label
        constant: "fixed"
""",
    )
    summary = _resolve(ir)
    (row,) = _rows(summary, "KbMap", _LABEL)
    assert row["kind"] == "constant"
    assert "label" not in row


def _two_maps_of_one_kind(value_a: str, value_b: str) -> str:
    return _ir_header(
        _MAP_UNIT,
        f"""\
  - name: alpha
    source: c.csv
    subject:
      template: "exr:alpha/{{aid}}"
      classes: [ex:Shared]
    properties:
      - predicate: rdfs:label
        constant: "{value_a}"
        label: "甲"
  - name: beta
    source: c.csv
    subject:
      template: "exr:beta/{{bid}}"
      classes: [ex:Shared]
    properties:
      - predicate: rdfs:label
        constant: "{value_b}"
        label: "乙"
""",
    )


def test_same_kind_in_two_maps_with_different_constants_keeps_each_label() -> None:
    """同じ種類を 2 つの map が持ち、定数の値が違うなら、それぞれ自分の表示名になる。
    別の種類の列の行の表示名は借りない。"""
    summary = _resolve(_two_maps_of_one_kind("one", "two"))
    (a,) = _rows(summary, "AlphaMap", _LABEL)
    (b,) = _rows(summary, "BetaMap", _LABEL)
    assert a["label"] == "甲"
    assert b["label"] == "乙"


def test_same_kind_in_two_maps_with_the_same_constant_borrows_nothing() -> None:
    """同じ種類を 2 つの map が持ち、定数の値が同じで表示名だけ違うなら、どちらにも付けない。
    別の種類の表示名も借りない。"""
    summary = _resolve(_two_maps_of_one_kind("same", "same"))
    for map_id in ("AlphaMap", "BetaMap"):
        (row,) = _rows(summary, map_id, _LABEL)
        assert row.get("label") is None


def test_a_column_row_and_a_template_row_keep_their_own_labels() -> None:
    """同じ map・同じ述語の列の行とテンプレートの行は、それぞれ自分の表示名になる。"""
    ir = _ir_header(
        """\
  - name: mix
    source: c.csv
    subject:
      template: "exr:mix/{mid}"
      classes: [ex:Mix]
    properties:
      - predicate: rdfs:label
        column: mname
        label: "列の名前"
      - predicate: rdfs:label
        object_template: "No. {mid}"
        object_type: literal
        label: "番号の名前"
""",
    )
    rows = _rows(_resolve(ir), "MixMap", _LABEL)
    assert {r["kind"]: r["label"] for r in rows} == {
        "reference": "列の名前",
        "template": "番号の名前",
    }


def test_a_several_column_function_never_borrows_a_column_rows_label() -> None:
    """複数の列を読む関数の行は、同じ述語でその列の 1 つを読む行と並んでいても、
    列の行の表示名を借りず、自分の表示名になる。"""
    ir = _ir_header(
        """\
  - name: rec
    source: c.csv
    subject:
      template: "exr:rec/{rid}"
      classes: [ex:Rec]
    properties:
      - predicate: ex:v
        column: alpha
        label: "列の名前"
      - predicate: ex:v
        columns: [alpha, beta]
        function: float_array_count
        datatype: xsd:integer
        label: "関数の名前"
""",
    )
    rows = _rows(_resolve(ir), "RecMap", _ONTO + "v")
    assert {r["kind"]: r["label"] for r in rows} == {
        "reference": "列の名前",
        "function": "関数の名前",
    }


def test_a_link_to_its_own_map_does_not_take_its_own_kinds_label() -> None:
    """自分自身の map の主語と同じ object_template を持つ行は、
    自分の種類の表示名にならない。"""
    ir = _ir_header(
        """\
  - name: selfy
    source: c.csv
    subject:
      template: "exr:selfy/{sid}"
      classes: [ex:Selfy]
      label: "自分の種類"
    properties:
      - predicate: ex:sameAs
        object_template: "exr:selfy/{sid}"
""",
    )
    (row,) = _rows(_resolve(ir), "SelfyMap", _ONTO + "sameAs")
    assert row.get("label") != "自分の種類"


def test_a_constant_link_reads_the_target_only_when_it_is_an_iri() -> None:
    """定数でつなぐ行は、object_type が iri で定数が別の map の主語の定数と同じときだけ、
    つなぐ先の表示名になる。リテラルの定数は文字列が同じでも付かない。"""
    ir = _ir_header(
        """\
  - name: doc
    source: c.csv
    subject:
      constant: "exr:doc1"
      classes: [ex:Doc]
      label: "文書"
    properties:
      - predicate: ex:n
        column: dn
  - name: note
    source: c.csv
    subject:
      template: "exr:note/{nid}"
      classes: [ex:Note]
    properties:
      - predicate: ex:about
        constant: "exr:doc1"
        object_type: iri
      - predicate: ex:remark
        constant: "exr:doc1"
""",
    )
    summary = _resolve(ir)
    (about,) = _rows(summary, "NoteMap", _ONTO + "about")
    (remark,) = _rows(summary, "NoteMap", _ONTO + "remark")
    assert about["label"] == "文書"
    assert remark.get("label") != "文書"


def test_unit_on_a_row_without_a_column_is_returned() -> None:
    """列を読まない行（テンプレート・定数）に書いた unit は、行の unit として返る。"""
    ir = _ir_header(
        """\
  - name: mix
    source: c.csv
    subject:
      template: "exr:mix/{mid}"
      classes: [ex:Mix]
    properties:
      - predicate: ex:size
        object_template: "{mid} big"
        object_type: literal
        unit: "kg"
      - predicate: ex:fixedSize
        constant: "10"
        unit: "m"
""",
    )
    summary = _resolve(ir)
    (tpl,) = _rows(summary, "MixMap", _ONTO + "size")
    (const,) = _rows(summary, "MixMap", _ONTO + "fixedSize")
    assert tpl["kind"] == "template"
    assert tpl["unit"] == "kg"
    assert const["kind"] == "constant"
    assert const["unit"] == "m"


_RAW_ROW = """\
      - predicate: rdfs:label
        column: doi
        label: "素のままの名前"
"""
_FUNCTION_ROW = """\
      - predicate: rdfs:label
        column: doi
        function: slug
        label: "関数を通した名前"
"""


@pytest.mark.parametrize("rows", [(_FUNCTION_ROW, _RAW_ROW), (_RAW_ROW, _FUNCTION_ROW)])
def test_a_function_row_and_a_raw_row_on_one_column_keep_their_own_labels(
    rows: tuple[str, str],
) -> None:
    """同じ述語・同じ列を、関数を通す行と素のままの行が読むとき、
    それぞれ自分の表示名になる（行の順によらない）。"""
    ir = _ir_header(
        """\
  - name: doi
    source: c.csv
    subject:
      template: "exr:doi/{doi}"
      classes: [ex:Doi]
    properties:
"""
        + "".join(rows)
    )
    got = {r["kind"]: r["label"] for r in _rows(_resolve(ir), "DoiMap", _LABEL)}
    assert got == {"reference": "素のままの名前", "function": "関数を通した名前"}


def _fn_arg(name: str) -> dict:
    return {"kind": "reference", "reference": name}


def test_row_column_reads_reference_and_single_function_column() -> None:
    """reference のある行はその列、関数で列 1 つならその列、関数で列 2 つなら列なし。"""
    from asterism_api.main import _row_column

    assert _row_column({"kind": "reference", "reference": "a"}) == "a"
    assert _row_column({"kind": "function", "function": "slug", "args": [_fn_arg("a")]}) == "a"
    two = {
        "kind": "function",
        "function": "float_array_count",
        "args": [_fn_arg("a"), _fn_arg("b")],
    }
    assert _row_column(two) == ""


def test_row_column_is_empty_for_a_template_function() -> None:
    """テンプレートを組む関数は、生の列が 1 つだけ残っていても、列を読む行ではない。"""
    from asterism_api.main import _row_column

    row = {
        "kind": "function",
        "function": "template",
        "args": [_fn_arg("a"), {"kind": "constant", "value": "-"}],
    }
    assert _row_column(row) == ""


def test_row_column_is_empty_when_an_arg_is_a_nested_function() -> None:
    """引数に関数の入れ子がある行は、生の列が 1 つだけ残っていても、列を読む行ではない。"""
    from asterism_api.main import _row_column

    nested = {"kind": "function", "function": "slug", "args": [_fn_arg("a")]}
    row = {"kind": "function", "function": "concat", "args": [nested, _fn_arg("b")]}
    assert _row_column(row) == ""


def test_a_constant_row_never_takes_another_kinds_column_heading() -> None:
    """種類 A が列で読む述語（表示名なし）を、種類 B が定数で持つとき、
    B の定数の行に A の列の見出しは付かない。"""
    ir = _ir_header(
        """\
  - name: ka
    source: c.csv
    subject:
      template: "exr:ka/{kid}"
      classes: [ex:Ka]
    properties:
      - predicate: ex:p
        column: c
  - name: kb
    source: c.csv
    subject:
      template: "exr:kb/{kid}"
      classes: [ex:Kb]
    properties:
      - predicate: ex:p
        constant: "fixed"
""",
    )
    (row,) = _rows(_resolve(ir), "KbMap", _ONTO + "p")
    assert row["kind"] == "constant"
    assert row.get("label") is None


def test_a_literal_template_is_never_a_link_even_with_the_parents_string() -> None:
    """object_type が literal のテンプレートは、別の map の主語と同じ文字列でも、
    つなぐ先の表示名にならない。"""
    ir = _ir_header(
        """\
  - name: pa
    source: c.csv
    subject:
      template: "exr:pa/{pid}"
      classes: [ex:Pa]
      label: "親A"
    properties:
      - predicate: ex:n
        column: pn
  - name: child
    source: c.csv
    subject:
      template: "exr:child/{cid}"
      classes: [ex:Child]
    properties:
      - predicate: ex:code
        object_template: "exr:pa/{pid}"
        object_type: literal
""",
    )
    (row,) = _rows(_resolve(ir), "ChildMap", _ONTO + "code")
    assert row.get("label") != "親A"


def _kind_map(name: str, kind: str, rows: str, subject: str | None = None) -> str:
    head = subject or f'template: "exr:{name}/{{{name}_id}}"'
    return f"""\
  - name: {name}
    source: c.csv
    subject:
      {head}
      classes: [{kind}]
    properties:
{rows}"""


def test_raw_and_function_reads_of_one_column_on_different_kinds_keep_their_labels() -> None:
    """種類 X が素の列 a で、種類 Y が列 a を関数に通して読む同じ述語は、
    それぞれ自分の表示名になる（読み方が違う行どうしで表示名を貸し借りしない）。"""
    ir = _ir_header(
        _kind_map("xk", "ex:X", "      - predicate: ex:p\n        column: a\n        label: A\n"),
        _kind_map(
            "yk",
            "ex:Y",
            "      - predicate: ex:p\n"
            "        columns: [a]\n"
            "        function: slug\n"
            "        label: B\n",
        ),
    )
    summary = _resolve(ir)
    (x,) = _rows(summary, "XkMap", _ONTO + "p")
    (y,) = _rows(summary, "YkMap", _ONTO + "p")
    assert y["kind"] == "function"
    assert x["label"] == "A"
    assert y["label"] == "B"


def test_escaped_braces_only_template_is_told_apart_from_a_real_hole() -> None:
    """波括弧をエスケープしただけの文字列（RML ではテンプレートの行になる）と、
    穴あきのテンプレートは、同じ種類・同じ述語でもそれぞれ自分の表示名になる。"""
    ir = _ir_header(
        _kind_map(
            "esc",
            "ex:Shared",
            "      - predicate: ex:p\n"
            "        object_template: 'Year \\{lit}'\n"
            "        object_type: literal\n"
            "        label: A\n",
        ),
        _kind_map(
            "hole",
            "ex:Shared",
            "      - predicate: ex:p\n"
            '        object_template: "Year {y}"\n'
            "        object_type: literal\n"
            "        label: B\n",
        ),
    )
    real = _real_summary(ir)
    (esc_real,) = _rows(real, "EscMap", _ONTO + "p")
    assert esc_real["kind"] == "template"
    summary = _resolve(ir)
    (esc,) = _rows(summary, "EscMap", _ONTO + "p")
    (hole,) = _rows(summary, "HoleMap", _ONTO + "p")
    assert esc["label"] == "A"
    assert hole["label"] == "B"


@pytest.mark.parametrize(
    ("parent_subject", "child_object"),
    [
        ("https://example.org/resource/parent/{pid}", "exr:parent/{pid}"),
        ("exr:parent/{pid}", "https://example.org/resource/parent/{pid}"),
    ],
)
def test_link_finds_its_parent_whether_curie_or_full_iri(
    parent_subject: str, child_object: str
) -> None:
    """親の主語と子のつなぐ先は、CURIE で書いても完全な IRI で書いても
    同じものとして比べ、つなぐ行は親の表示名になる（テンプレート）。"""
    ir = _ir_header(
        _kind_map(
            "parent",
            "ex:Parent",
            "      - predicate: ex:n\n        column: pn\n",
            f'template: "{parent_subject}"\n      label: 親の種類',
        ),
        _kind_map(
            "child",
            "ex:Child",
            f'      - predicate: ex:belongsTo\n        object_template: "{child_object}"\n',
        ),
    )
    (row,) = _rows(_resolve(ir), "ChildMap", _ONTO + "belongsTo")
    assert row["label"] == "親の種類"


@pytest.mark.parametrize(
    ("parent_subject", "child_object"),
    [
        ("https://example.org/resource/parent1", "exr:parent1"),
        ("exr:parent1", "https://example.org/resource/parent1"),
    ],
)
def test_constant_link_finds_its_parent_whether_curie_or_full_iri(
    parent_subject: str, child_object: str
) -> None:
    """定数の主語と定数のつなぐ先も、CURIE と完全な IRI の書き分けに関わらず
    同じものとして比べ、つなぐ行は親の表示名になる。"""
    ir = _ir_header(
        _kind_map(
            "parent",
            "ex:Parent",
            "      - predicate: ex:n\n        column: pn\n",
            f'constant: "{parent_subject}"\n      label: 親の種類',
        ),
        _kind_map(
            "child",
            "ex:Child",
            "      - predicate: ex:belongsTo\n"
            f'        constant: "{child_object}"\n'
            "        object_type: iri\n",
        ),
    )
    (row,) = _rows(_resolve(ir), "ChildMap", _ONTO + "belongsTo")
    assert row["label"] == "親の種類"


def test_two_curie_iri_constants_on_one_kind_and_predicate_keep_their_labels() -> None:
    """同じ種類・同じ述語に、CURIE で書いた IRI の定数の行が 2 つあり、値も表示名も
    違うなら、それぞれ自分の表示名になる。"""
    ir = _ir_header(
        _kind_map(
            "rec",
            "ex:Rec",
            "      - predicate: ex:tag\n"
            '        constant: "ex:one"\n'
            "        object_type: iri\n"
            "        label: 一\n"
            "      - predicate: ex:tag\n"
            '        constant: "ex:two"\n'
            "        object_type: iri\n"
            "        label: 二\n",
        ),
    )
    rows = _rows(_resolve(ir), "RecMap", _ONTO + "tag")
    assert len(rows) == 2
    assert sorted(r["label"] for r in rows) == ["一", "二"]
    assert {r["constant"]: r["label"] for r in rows} == {"ex:one": "一", "ex:two": "二"}


def test_same_constant_or_template_string_on_different_kinds_keeps_each_label() -> None:
    """別々の種類が同じ述語に同じ文字列（定数・穴あきのテンプレート）を持つとき、
    表示名が違っても、それぞれ自分の種類の表示名になる。"""
    ir = _ir_header(
        _kind_map(
            "ka",
            "ex:Ka",
            "      - predicate: ex:c\n"
            '        constant: "same"\n'
            "        label: 甲\n"
            "      - predicate: ex:t\n"
            '        object_template: "Year {y}"\n'
            "        object_type: literal\n"
            "        label: 甲T\n",
        ),
        _kind_map(
            "kb",
            "ex:Kb",
            "      - predicate: ex:c\n"
            '        constant: "same"\n'
            "        label: 乙\n"
            "      - predicate: ex:t\n"
            '        object_template: "Year {y}"\n'
            "        object_type: literal\n"
            "        label: 乙T\n",
        ),
    )
    summary = _resolve(ir)
    assert _rows(summary, "KaMap", _ONTO + "c")[0]["label"] == "甲"
    assert _rows(summary, "KbMap", _ONTO + "c")[0]["label"] == "乙"
    assert _rows(summary, "KaMap", _ONTO + "t")[0]["label"] == "甲T"
    assert _rows(summary, "KbMap", _ONTO + "t")[0]["label"] == "乙T"


def test_multi_column_function_row_and_constant_row_keep_their_own_labels() -> None:
    """同じ種類・同じ述語の、複数の列を読む関数の行と定数の行は、
    それぞれ自分の表示名になる（形が違う行の表示名を貸し借りしない）。"""
    ir = _ir_header(
        _kind_map(
            "rec",
            "ex:Rec",
            "      - predicate: ex:v\n"
            "        columns: [a, b]\n"
            "        function: float_array_count\n"
            "        datatype: xsd:integer\n"
            "        label: Y\n"
            "      - predicate: ex:v\n"
            '        constant: "fixed"\n'
            "        label: X\n",
        ),
    )
    by_kind = {r["kind"]: r["label"] for r in _rows(_resolve(ir), "RecMap", _ONTO + "v")}
    assert by_kind == {"function": "Y", "constant": "X"}


def test_function_row_without_a_label_takes_its_own_column_heading() -> None:
    """関数を通して列を 1 つ読む行に表示名が無く、その述語が別の種類で別の列に
    束縛されていても、関数の行は自分の列の見出しになる。"""
    ir = _ir_header(
        _kind_map(
            "xk", "ex:X", "      - predicate: ex:p\n        column: beta\n        label: 他\n"
        ),
        _kind_map(
            "yk",
            "ex:Y",
            "      - predicate: ex:p\n        column: alpha\n        function: slug\n",
        ),
    )
    (row,) = _rows(_resolve(ir), "YkMap", _ONTO + "p")
    assert row["kind"] == "function"
    assert row["label"] == "alpha"


@pytest.mark.parametrize("breakage", ["prefixes_list", "class_iris_str", "class_iris_mixed"])
def test_broken_summary_shape_does_not_skip_the_label_merge(breakage: str) -> None:
    """要約の一部の形が壊れていても、表示名の付与は丸ごと飛ばず（警告も出さず）、
    列を読む行には表示名が付く。"""
    from asterism_api.main import _merge_ir_display_metadata

    ir = _ir_header(_MAP_COMP, _MAP_UNIT)
    summary = _real_summary(ir)
    if breakage == "prefixes_list":
        summary["prefixes"] = ["ex", "exr"]
    elif breakage == "class_iris_str":
        summary["maps"][0]["subject"]["class_iris"] = "https://example.org/onto#Comp"
    else:
        summary["maps"][0]["subject"]["class_iris"] = [None, 3, "https://example.org/onto#Comp"]
    before = list(summary.get("warnings") or [])
    _merge_ir_display_metadata(ir, summary)
    assert list(summary.get("warnings") or []) == before
    assert _rows(summary, "CompMap", _LABEL)[0]["label"] == "組成"
    assert _rows(summary, "UnitYMap", _LABEL)[0]["label"] == "縦軸単位"


def test_a_column_row_the_design_left_unnamed_borrows_nothing() -> None:
    """列の名前が見出しとして何も残らない（`_`）行は、設計が名前を付けなかった行。
    同じ述語の別の種類の行に表示名があっても、述語だけの引き方で借りない。"""
    ir = _ir_header(
        _kind_map(
            "solo",
            "ex:Solo",
            "      - predicate: ex:only\n        column: _\n",
        ),
        _kind_map(
            "other",
            "ex:Other",
            "      - predicate: ex:only\n        constant: fixed\n        label: 別\n",
        ),
    )
    summary = _resolve(ir)
    (row,) = _rows(summary, "SoloMap", _ONTO + "only")
    assert row.get("label") is None
    (other,) = _rows(summary, "OtherMap", _ONTO + "only")
    assert other["label"] == "別"


def test_a_column_row_the_design_left_unnamed_borrows_no_column_heading() -> None:
    """設計が名前を付けなかった列の行は、同じ述語を読む別の種類の列の見出しも借りない。"""
    ir = _ir_header(
        _kind_map("solo", "ex:Solo", "      - predicate: ex:p\n        column: _\n"),
        _kind_map("other", "ex:Other", "      - predicate: ex:p\n        column: bcol\n"),
    )
    summary = _resolve(ir)
    (row,) = _rows(summary, "SoloMap", _ONTO + "p")
    assert row.get("label") is None
    (other,) = _rows(summary, "OtherMap", _ONTO + "p")
    assert other["label"] == "bcol"


def test_a_function_reading_one_column_twice_is_found_by_that_column() -> None:
    """同じ列を 2 回渡す関数の行（`columns: [a, a]`）も、列 a を読む行として引く。
    同じ述語を別の列で読む別の種類の表示名を借りない。"""
    ir = _ir_header(
        _kind_map(
            "xk", "ex:X", "      - predicate: ex:v\n        column: beta\n        label: 他\n"
        ),
        _kind_map(
            "yk",
            "ex:Y",
            "      - predicate: ex:v\n"
            "        columns: [alpha, alpha]\n"
            "        function: float_array_count\n"
            "        label: 自分\n",
        ),
    )
    (row,) = _rows(_resolve(ir), "YkMap", _ONTO + "v")
    assert row["kind"] == "function"
    assert row["label"] == "自分"


def test_a_function_row_written_with_one_column_in_a_list_takes_its_column_heading() -> None:
    """列を 1 つだけ並べた書き方（`columns: [a]`）の関数の行も、表示名が無ければ
    自分の列の見出しになる（`column: a` と書いた行と同じ）。"""
    ir = _ir_header(
        _kind_map(
            "xk", "ex:X", "      - predicate: ex:p\n        column: beta\n        label: 他\n"
        ),
        _kind_map(
            "yk",
            "ex:Y",
            "      - predicate: ex:p\n        columns: [alpha]\n        function: slug\n",
        ),
    )
    (row,) = _rows(_resolve(ir), "YkMap", _ONTO + "p")
    assert row["kind"] == "function"
    assert row["label"] == "alpha"
