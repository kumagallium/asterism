"""Crosswalk hub API (crosswalk-hub.md productize ①②④): build / get / propose +
the promote/append auto-rebuild hooks.

The store is a real in-memory ``rdflib.Dataset`` injected as the app's client, so the
two-pass read, the hub write + promoted flag, and the FROM-merge resolution run for
real through the endpoints (no triplestore, no network).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import rdflib
from asterism import crosswalk_runtime, substrate
from fastapi.testclient import TestClient

from asterism_api.main import (
    CrosswalkRebuilder,
    Settings,
    _maybe_rebuild_crosswalk,
    build_app,
)

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}
PRED = "https://kumagallium.github.io/asterism/x/ontology#comp"


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


class _DatasetClient:
    """OxigraphClient stand-in over a real rdflib Dataset (SELECT/UPDATE + Graph-Store
    POST). Duck-typed into ``build_app(oxigraph_client=...)``."""

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds
        self.posted: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_graph
        g.parse(data=payload.decode("utf-8"), format="turtle")
        self.posted.append(graph_iri or "")
        return len(payload)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _seed_promoted(ds: rdflib.Dataset, registry_root: Path, dataset_id: str, rows) -> None:
    """A promoted dataset: rows in its key graph + control flag + a registry meta."""
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for entity, raw in rows:
        g.add((rdflib.URIRef(entity), rdflib.URIRef(PRED), rdflib.Literal(raw)))
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )
    d = registry_root / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "id": dataset_id,
                "name": dataset_id,
                "created_at": "2026-06-11T00:00:00+00:00",
                "promoted": True,
                "status": "active",
                "canonical_graph": key,
            }
        ),
        encoding="utf-8",
    )


def _config_body(participants):
    return {
        "config": {
            "min_datasets": 2,
            "concepts": [
                {
                    "name": "composition",
                    "normalizer": "composition",
                    "participants": [
                        {"dataset_id": d, "label": d, "predicate": PRED} for d in participants
                    ],
                }
            ],
        }
    }


def test_build_with_config_then_get(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi₂Te₃")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/crosswalk/build", json=_config_body(["ds-a", "ds-b"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["shared"]["composition"] == ["Bi2Te3"]
        assert body["shared_total"] == 1
        assert {p["dataset_id"] for p in body["participants_used"]} == {"ds-a", "ds-b"}
        assert body["dataset"]["is_crosswalk"] is True

        # Persisted: config + the crosswalk-bridge registry scaffold.
        assert (tmp_path / "registry" / "crosswalk-bridge" / "crosswalk.yaml").is_file()
        assert (tmp_path / "registry" / "crosswalk-bridge" / "query_tools.yaml").is_file()

        g = client.get("/api/crosswalk")
        assert g.status_code == 200
        gb = g.json()
        assert gb["exists"] is True
        ids = {p["dataset_id"] for p in gb["config"]["concepts"][0]["participants"]}
        assert ids == {"ds-a", "ds-b"}
        assert gb["dataset"]["crosswalk_shared_compositions"] == 1


def test_get_crosswalk_enriches_participant_and_concept_labels(tmp_path: Path) -> None:
    """XW-01/XW-04/XW-06: GET enriches the READ, never the persisted config —
    the participant's CURRENT dataset name, a design-authored predicate_label,
    and a concept_label derived from agreement across participants."""
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi₂Te₃")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    # ds-a's design authored a label for PRED (Mapping IR §9); ds-b has none —
    # concept_label still resolves from the ONE participant that has a label.
    registry_root = tmp_path / "registry"
    (registry_root / "ds-a" / "meta.json").write_text(
        json.dumps(
            {
                "id": "ds-a",
                "name": "アルファ測定",  # renamed since the crosswalk was built
                "created_at": "2026-06-11T00:00:00+00:00",
                "promoted": True,
                "status": "active",
                "canonical_graph": substrate.canonical_graph_iri("ds-a"),
            }
        ),
        encoding="utf-8",
    )
    (registry_root / "ds-a" / "mapping.yaml").write_text(
        "version: 1\n"
        "prefixes:\n"
        '  x: "https://kumagallium.github.io/asterism/x/ontology#"\n'
        "maps:\n"
        "  - name: sample\n"
        "    source: samples.csv\n"
        "    subject:\n"
        '      template: "x:sample/{SID}"\n'
        "    properties:\n"
        "      - predicate: x:comp\n"
        "        column: comp\n"
        '        label: "組成"\n',
        encoding="utf-8",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/crosswalk/build", json=_config_body(["ds-a", "ds-b"]))
        assert r.status_code == 200, r.text

        g = client.get("/api/crosswalk")
        concept = g.json()["config"]["concepts"][0]
        assert concept["concept_label"] == "組成"
        by_id = {p["dataset_id"]: p for p in concept["participants"]}
        assert by_id["ds-a"]["name"] == "アルファ測定"
        assert by_id["ds-a"]["predicate_label"] == "組成"
        # ds-b's design has no authored label -> no predicate_label key added
        # (the UI falls back to the stored ascii label), but its CURRENT name
        # (unchanged) still comes through.
        assert by_id["ds-b"].get("predicate_label") is None
        assert by_id["ds-b"]["name"] == "ds-b"

        listed = client.get("/api/crosswalks").json()["perspectives"]
        default = next(p for p in listed if p["perspective_id"] == "composition")
        assert default["config"]["concepts"][0]["concept_label"] == "組成"


_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
_X = "https://kumagallium.github.io/asterism/x/ontology#"

# A design whose value catalogs all store their value under rdfs:label (the label
# guarantee): the predicate alone has no single word, each KIND's field does.
_KINDS_IR = (
    "version: 1\n"
    "prefixes:\n"
    f'  x: "{_X}"\n'
    '  rdfs: "http://www.w3.org/2000/01/rdf-schema#"\n'
    "maps:\n"
    "  - name: composition\n"
    "    source: records.csv\n"
    "    subject:\n"
    '      template: "x:composition/{composition}"\n'
    "      classes: [x:Composition]\n"
    "    properties:\n"
    "      - predicate: rdfs:label\n"
    "        column: composition\n"
    '        label: "試料化学組成"\n'
    "  - name: doi\n"
    "    source: records.csv\n"
    "    subject:\n"
    '      template: "x:doi/{DOI}"\n'
    "      classes: [x:Doi]\n"
    "    properties:\n"
    "      - predicate: rdfs:label\n"
    "        column: DOI\n"  # no authored label: the column heading is the word
)


def test_ir_field_labels_name_each_kinds_own_field() -> None:
    from asterism_api.main import _crosswalk_predicate_labels, _ir_field_labels

    fields, kinds = _ir_field_labels(_KINDS_IR)
    assert fields[(f"{_X}Composition", _RDFS_LABEL)] == "試料化学組成"
    assert fields[(f"{_X}Doi", _RDFS_LABEL)] == "DOI"  # the column heading, kind known
    assert kinds == {f"{_X}Composition": "Composition", f"{_X}Doi": "Doi"}
    # The kind-agnostic resolver stays silent on the shared predicate — that is
    # exactly why the kind-scoped one exists.
    root = Path(__file__).parent / "_nonexistent"
    assert _crosswalk_predicate_labels(root, "nope") == {}


def _seed_kinds(ds: rdflib.Dataset, registry_root: Path, dataset_id: str, ir: str) -> None:
    """A promoted dataset with a Composition and a Doi kind, both labelled with
    rdfs:label, plus the design (mapping.yaml) that names each kind's field."""
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for i, (kind, raw) in enumerate(
        [("Composition", "Bi2Te3"), ("Composition", "PbTe"), ("Doi", "10.1000/x1")]
    ):
        e = rdflib.URIRef(f"urn:{dataset_id}:{i}")
        g.add((e, rdflib.RDF.type, rdflib.URIRef(f"{_X}{kind}")))
        g.add((e, rdflib.URIRef(_RDFS_LABEL), rdflib.Literal(raw)))
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )
    d = registry_root / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "id": dataset_id,
                "name": dataset_id,
                "created_at": "2026-09-03T00:00:00+00:00",
                "promoted": True,
                "status": "active",
                "canonical_graph": key,
            }
        ),
        encoding="utf-8",
    )
    (d / "mapping.yaml").write_text(ir, encoding="utf-8")


def test_crosswalk_fields_lists_each_kinds_field_with_the_designs_words(tmp_path: Path) -> None:
    """The dropdown without an AI: (kind, predicate) pairs sampled from the live
    graph, named with the design's words — 「Composition › 試料化学組成」 rather than
    a bare `label` (利用者報告 2026-09-03「データにそんなのない」)."""
    ds = rdflib.Dataset()
    _seed_kinds(ds, tmp_path / "registry", "ds-k", _KINDS_IR)
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/crosswalk/fields/ds-k")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["promoted"] is True
        by_kind = {f["subject_class"]: f for f in body["fields"]}
        comp = by_kind[f"{_X}Composition"]
        assert comp["iri"] == _RDFS_LABEL
        assert comp["label"] == "試料化学組成"
        assert comp["subject_class_label"] == "Composition"
        assert comp["sample"] in {"Bi2Te3", "PbTe"}
        assert by_kind[f"{_X}Doi"]["label"] == "DOI"
        assert client.get("/api/crosswalk/fields/nope").status_code == 404


def test_propose_carries_the_kind_and_the_get_names_it(tmp_path: Path) -> None:
    """The AI is shown each candidate's kind and hands it back; a config built from
    that pick is read back with the kind's name and the kind's own field label."""
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_kinds(ds, root, "ds-k", _KINDS_IR)
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3")])
    resp = (
        '{"participants": ['
        f'{{"dataset_id": "ds-k", "predicate": "{_RDFS_LABEL}", '
        f'"subject_class": "{_X}Composition", "why": "formula"}},'
        f'{{"dataset_id": "ds-b", "predicate": "{PRED}", "why": "formula"}}]}}'
    )
    llm = _MockLLM(resp)
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
        llm_factory=lambda key: llm,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/crosswalk/propose",
            json={"dataset_ids": ["ds-k", "ds-b"], "concept": "composition"},
            headers={"X-API-Key": "sk-test"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        picks = {p["dataset_id"]: p for p in body["participants"]}
        assert picks["ds-k"]["subject_class"] == f"{_X}Composition"
        assert "subject_class" not in picks["ds-b"]  # untyped subjects: no kind
        # The prompt showed the kind next to the predicate, so it could be copied.
        _system, user = llm.calls[0]
        assert f"kind: {_X}Composition" in user
        # The candidates that fill the dropdown carry the design's words per kind.
        cand = next(c for c in body["candidates"] if c["dataset_id"] == "ds-k")
        comp = next(p for p in cand["predicates"] if p.get("subject_class") == f"{_X}Composition")
        assert comp["label"] == "試料化学組成" and comp["subject_class_label"] == "Composition"

        config = {
            "concepts": [
                {
                    "name": "composition",
                    "participants": [
                        {
                            "dataset_id": "ds-k",
                            "label": "k",
                            "predicate": _RDFS_LABEL,
                            "subject_class": f"{_X}Composition",
                        },
                        {"dataset_id": "ds-b", "label": "b", "predicate": PRED},
                    ],
                }
            ]
        }
        b = client.post("/api/crosswalk/build", json={"config": config})
        assert b.status_code == 200, b.text
        assert b.json()["shared_total"] == 1  # Bi2Te3; the DOI never joined
        got = client.get("/api/crosswalk").json()["config"]["concepts"][0]
        by_id = {p["dataset_id"]: p for p in got["participants"]}
        assert by_id["ds-k"]["subject_class"] == f"{_X}Composition"
        assert by_id["ds-k"]["subject_class_label"] == "Composition"
        assert by_id["ds-k"]["predicate_label"] == "試料化学組成"
        assert got["concept_label"] == "試料化学組成"


def test_build_without_config_uses_persisted(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        first = client.post("/api/crosswalk/build", json=_config_body(["ds-a", "ds-b"]))
        assert first.status_code == 200, first.text
        # No body -> rebuild from the persisted config.
        r = client.post("/api/crosswalk/build", json={})
        assert r.status_code == 200, r.text
        assert r.json()["shared_total"] == 1


def test_build_without_config_and_none_persisted_is_400(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/crosswalk/build", json={})
        assert r.status_code == 400
    # GET with no hub yet -> exists False.
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/crosswalk").json()["exists"] is False


def test_named_perspective_endpoints(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    client_obj = _DatasetClient(ds)
    app = build_app(_settings(tmp_path), oxigraph_client=client_obj, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        # Build BOTH the default (composition) and a NAMED perspective ("crystal").
        base = client.post("/api/crosswalk/build", json=_config_body(["ds-a", "ds-b"]))
        assert base.status_code == 200, base.text
        r = client.post(
            "/api/crosswalk/crystal/build",
            json={**_config_body(["ds-a", "ds-b"]), "name": "結晶構造"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["perspective_id"] == "crystal"
        assert body["dataset_id"] == "crosswalk-crystal"
        assert body["hub_graph"].endswith("/graph/canonical/crosswalk/crystal")
        assert body["shared_total"] == 1

        # The list endpoint returns BOTH perspectives, distinctly.
        persp = client.get("/api/crosswalks").json()["perspectives"]
        ids = {p["perspective_id"] for p in persp}
        assert ids == {"composition", "crystal"}

        # GET one named perspective.
        g = client.get("/api/crosswalk/crystal").json()
        assert g["exists"] is True
        assert g["dataset"]["name"] == "結晶構造"

    # The two perspectives wrote to DISTINCT graphs (the legacy + the new sub-path).
    posted = set(client_obj.posted)
    assert "https://kumagallium.github.io/asterism/graph/canonical/crosswalk" in posted
    assert "https://kumagallium.github.io/asterism/graph/canonical/crosswalk/crystal" in posted


def test_alignment_endpoints(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    xw = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/crosswalk/align",
            json={
                "source": f"{xw}Composition",
                "target": f"{xw}Material",
                "relation": "equivalentClass",
                "from_perspective": "composition",
                "to_perspective": "material",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["relation"] == "equivalentClass"

        al = client.get("/api/crosswalk/alignments").json()
        assert "equivalentClass" in al["relations"]
        assert len(al["alignments"]) == 1
        assert al["alignments"][0]["source"] == f"{xw}Composition"

        # a relation outside the closed set is rejected
        bad = client.post(
            "/api/crosswalk/align",
            json={"source": f"{xw}A", "target": f"{xw}B", "relation": "sameAs"},
        )
        assert bad.status_code == 400

        # remove withdraws it
        rm = client.post(
            "/api/crosswalk/align",
            json={
                "source": f"{xw}Composition",
                "target": f"{xw}Material",
                "relation": "equivalentClass",
                "remove": True,
            },
        )
        assert rm.status_code == 200
        assert client.get("/api/crosswalk/alignments").json()["alignments"] == []


def test_normalizer_recipe_endpoints(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        prims = client.get("/api/crosswalk/normalizer/primitives").json()["primitives"]
        assert {"casefold", "nfkc", "collapse_ws", "fold_subscripts", "remove_ws"} <= set(prims)

        # preview applies the recipe to each sample (the join keys it would produce)
        r = client.post(
            "/api/crosswalk/normalizer/preview",
            json={
                "recipe": ["nfkc", "casefold", "collapse_ws"],
                "samples": ["Iron  Oxide", "ＦｅＯ"],  # noqa: RUF001 (full-width is intentional)
            },
        )
        assert r.status_code == 200, r.text
        out = {x["input"]: x["output"] for x in r.json()["results"]}
        assert out["Iron  Oxide"] == "iron oxide"
        assert out["ＦｅＯ"] == "feo"  # noqa: RUF001  full-width folded by NFKC then casefolded

        # an unknown primitive is rejected (closed-set gate)
        bad = client.post(
            "/api/crosswalk/normalizer/preview",
            json={"recipe": ["nfkc", "danger"], "samples": ["x"]},
        )
        assert bad.status_code == 400


class _MockLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


def test_propose_suggests_predicates(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    resp = (
        '{"participants": ['
        f'{{"dataset_id": "ds-a", "predicate": "{PRED}", "why": "formula"}},'
        f'{{"dataset_id": "ds-b", "predicate": "{PRED}", "why": "formula"}}]}}'
    )
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
        llm_factory=lambda key: _MockLLM(resp),
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/crosswalk/propose",
            json={"dataset_ids": ["ds-a", "ds-b"], "concept": "composition"},
            headers={"X-API-Key": "sk-test"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert {p["dataset_id"]: p["predicate"] for p in body["participants"]} == {
            "ds-a": PRED,
            "ds-b": PRED,
        }
        # The store-sampled candidates are returned so the UI can populate dropdowns.
        assert any(c["dataset_id"] == "ds-a" for c in body["candidates"])


def test_propose_forwards_language_to_llm(tmp_path: Path) -> None:
    # body.language reaches the LLM's USER message (the "why" prose follows the UI
    # language); the system prompt stays free of the directive (prompt caching).
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi2Te3")])
    llm = _MockLLM('{"participants": []}')
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
        llm_factory=lambda key: llm,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/crosswalk/propose",
            json={"dataset_ids": ["ds-a"], "concept": "composition", "language": "ja"},
            headers={"X-API-Key": "sk-test"},
        )
        assert r.status_code == 200, r.text
        system, user = llm.calls[0]
        assert "# Output language" in user and "Japanese (日本語)" in user
        assert "# Output language" not in system


def test_propose_requires_key(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/crosswalk/propose", json={"dataset_ids": ["ds-a"]})
        assert r.status_code == 400


def test_maybe_rebuild_hook_fires_for_participant_only(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_promoted(ds, root, "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3")])
    client = _DatasetClient(ds)
    cfg = crosswalk_runtime.parse_config(_config_body(["ds-a", "ds-b"])["config"])
    crosswalk_runtime.save_config(root, cfg)

    # A non-participant promote does NOT rebuild.
    asyncio.run(_maybe_rebuild_crosswalk(client, root, "ds-unrelated"))
    assert crosswalk_runtime.HUB_GRAPH not in client.posted

    # A participant promote rebuilds the hub.
    asyncio.run(_maybe_rebuild_crosswalk(client, root, "ds-a"))
    assert crosswalk_runtime.HUB_GRAPH in client.posted
    assert crosswalk_runtime.HUB_GRAPH in set(asyncio.run(substrate.canonical_graphs(client)))


def test_debounced_rebuilder_coalesces(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_promoted(ds, root, "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3")])
    client = _DatasetClient(ds)
    crosswalk_runtime.save_config(
        root, crosswalk_runtime.parse_config(_config_body(["ds-a", "ds-b"])["config"])
    )

    async def run() -> int:
        rebuilder = CrosswalkRebuilder(client, root, delay_s=0.05)
        rebuilder.schedule("ds-a")
        rebuilder.schedule("ds-a")  # supersedes the first (a burst of appends -> ONE rebuild)
        await asyncio.sleep(0.2)
        await rebuilder.aclose()
        return client.posted.count(crosswalk_runtime.HUB_GRAPH)

    assert asyncio.run(run()) == 1


# --- discover: find the joins that exist, without an LLM (kantan-mode ADR) ---------


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE response body into (event_name, data_dict) pairs."""
    events: list[tuple[str, dict]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload = line[len("data:") :].strip()
            events.append((name, json.loads(payload) if payload else {}))
    return events


def _discover(client, **body) -> dict:
    """Run discovery to completion and return the job's result."""
    r = client.post("/api/crosswalk/discover", json=body)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
    done = [data for name, data in events if name == "done"]
    assert done, events
    return done[0]["result"]


def test_discover_returns_candidates_with_the_evidence(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_promoted(ds, root, "ds-a", [("urn:a1", "Bi₂Te₃"), ("urn:a2", "PbTe")])
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3"), ("urn:b2", "PbTe")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        result = _discover(client)

    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    assert {p["dataset_id"] for p in cand["participants"]} == {"ds-a", "ds-b"}
    assert cand["matched"] == 2
    assert cand["samples"]
    assert result["limits"]["ladder"]  # the bounds are disclosed, not implicit


def test_discover_candidate_builds_without_edits(tmp_path: Path) -> None:
    # The contract that makes "connect these" one click: what discovery promises
    # (`matched`) must equal what a build of its own config produces (`shared_total`).
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_promoted(ds, root, "ds-a", [("urn:a1", "Bi₂Te₃"), ("urn:a2", "PbTe")])
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3"), ("urn:b2", "PbTe")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        cand = _discover(client)["candidates"][0]
        r = client.post(
            f"/api/crosswalk/{cand['perspective_id']}/build",
            json={"config": cand["build_config"], "name": cand["name"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["shared_total"] == cand["matched"]


def test_discover_needs_no_llm_key(tmp_path: Path) -> None:
    # propose is key-gated (see test_propose_requires_key); discovery must NOT be —
    # the entrance to connecting data cannot be an API-key prompt (ADR K5).
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        assert client.post("/api/crosswalk/discover", json={}).status_code == 202


def test_discover_requires_write_auth(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app) as client:
        assert client.post("/api/crosswalk/discover", json={}).status_code in (401, 403)


def test_discover_excludes_drafts_and_the_hub_itself(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_promoted(ds, root, "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3")])
    # A draft (never promoted) and a crosswalk hub are both ineligible participants.
    (root / "ds-draft").mkdir(parents=True, exist_ok=True)
    (root / "ds-draft" / "meta.json").write_text(
        json.dumps({"id": "ds-draft", "name": "draft", "promoted": False}), encoding="utf-8"
    )
    (root / "crosswalk-bridge").mkdir(parents=True, exist_ok=True)
    (root / "crosswalk-bridge" / "meta.json").write_text(
        json.dumps(
            {"id": "crosswalk-bridge", "name": "hub", "promoted": True, "is_crosswalk": True}
        ),
        encoding="utf-8",
    )
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        result = _discover(client)

    reasons = {s["dataset_id"]: s["reason"] for s in result["scanned"]["datasets_skipped"]}
    assert reasons["ds-draft"] == "not_promoted"
    assert reasons["crosswalk-bridge"] == "crosswalk"
    assert all(
        "crosswalk-bridge" not in [p["dataset_id"] for p in c["participants"]]
        for c in result["candidates"]
    )


def test_discover_discloses_datasets_it_was_not_asked_about(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    for dsid in ("ds-a", "ds-b", "ds-c"):
        _seed_promoted(ds, root, dsid, [(f"urn:{dsid}", "Bi2Te3")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        result = _discover(client, dataset_ids=["ds-a", "ds-b"])

    reasons = {s["dataset_id"]: s["reason"] for s in result["scanned"]["datasets_skipped"]}
    assert reasons == {"ds-c": "not_requested"}


def test_discover_warns_when_a_candidate_would_replace_an_existing_crosswalk(
    tmp_path: Path,
) -> None:
    ds = rdflib.Dataset()
    root = tmp_path / "registry"
    _seed_promoted(ds, root, "ds-a", [("urn:a1", "Bi2Te3"), ("urn:a2", "PbTe")])
    _seed_promoted(ds, root, "ds-b", [("urn:b1", "Bi2Te3"), ("urn:b2", "PbTe")])
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        first = _discover(client)["candidates"][0]
        client.post(
            f"/api/crosswalk/{first['perspective_id']}/build",
            json={"config": first["build_config"], "name": first["name"]},
        )
        again = _discover(client)["candidates"][0]

    assert again["perspective_exists"] is True


def test_delete_perspective_removes_hub_and_registry(tmp_path: Path) -> None:
    """つながりの削除: hub グラフと登録が消え、一覧からも消える。元データセットの
    三つ組には触れない（hub は投影 — 同じ設定で作り直せる可逆な操作）。"""
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    client_obj = _DatasetClient(ds)
    app = build_app(_settings(tmp_path), oxigraph_client=client_obj, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/crosswalk/crystal/build",
            json={**_config_body(["ds-a", "ds-b"]), "name": "結晶構造"},
        )
        assert r.status_code == 200, r.text

        d = client.delete("/api/crosswalk/crystal")
        assert d.status_code == 200, d.text
        assert d.json() == {
            "deleted": True,
            "perspective_id": "crystal",
            "dataset_id": "crosswalk-crystal",
        }
        # 一覧から消え、登録ディレクトリも無い。
        ids = {p["perspective_id"] for p in client.get("/api/crosswalks").json()["perspectives"]}
        assert "crystal" not in ids
        assert not (tmp_path / "registry" / "crosswalk-crystal").exists()
        # 2 度目は 404（冪等な成功を装わない）。
        assert client.delete("/api/crosswalk/crystal").status_code == 404
    # hub グラフは実際に空（DROP 済み）。元データセットの三つ組は無傷。
    hub = ds.graph(
        rdflib.URIRef("https://kumagallium.github.io/asterism/graph/canonical/crosswalk/crystal")
    )
    assert len(hub) == 0
    key_a = ds.graph(rdflib.URIRef(substrate.canonical_graph_iri("ds-a")))
    assert len(key_a) > 0


def test_predicate_labels_stay_silent_when_one_predicate_has_two_labels(
    tmp_path: Path,
) -> None:
    """同じ述語に別々のラベルが付いていたら、どれか 1 つを名乗らない。

    ⭐値のカタログはどれも `rdfs:label` を束縛する（ensure_value_catalog_labels）。
    述語だけで引くと「化学組成」の候補が『縦軸単位』と名乗った（利用者報告
    2026-09-02）。この経路の約束は「設計が選んだ言葉を言う、さもなくば黙る」。
    """
    from asterism_api.main import _crosswalk_predicate_labels

    registry_root = tmp_path / "registry"
    (registry_root / "ds-a").mkdir(parents=True)
    (registry_root / "ds-a" / "meta.json").write_text(
        json.dumps({"id": "ds-a", "created_at": "2026-06-11T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (registry_root / "ds-a" / "mapping.yaml").write_text(
        "version: 1\n"
        "prefixes:\n"
        '  x: "https://kumagallium.github.io/asterism/x/ontology#"\n'
        '  rdfs: "http://www.w3.org/2000/01/rdf-schema#"\n'
        "maps:\n"
        "  - name: composition\n"
        "    source: c.csv\n"
        "    subject:\n"
        '      template: "x:composition/{composition}"\n'
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: composition\n"
        '        label: "化学組成"\n'
        "  - name: unit_y\n"
        "    source: c.csv\n"
        "    subject:\n"
        '      template: "x:unit_y/{unit_y}"\n'
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: unit_y\n"
        '        label: "縦軸単位"\n'
        "  - name: record\n"
        "    source: c.csv\n"
        "    subject:\n"
        '      template: "x:record/{id}"\n'
        "    properties:\n"
        "      - predicate: x:comp\n"
        "        column: comp\n"
        '        label: "試料組成"\n',
        encoding="utf-8",
    )
    labels = _crosswalk_predicate_labels(registry_root, "ds-a")
    # 曖昧な述語は名乗らない（どちらか一方を返すのが今回のバグだった）
    assert "http://www.w3.org/2000/01/rdf-schema#label" not in labels
    # 一意な述語は従来どおり設計のラベルを名乗る
    assert labels["https://kumagallium.github.io/asterism/x/ontology#comp"] == "試料組成"
