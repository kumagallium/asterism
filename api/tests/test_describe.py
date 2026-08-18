"""Tests for GET /describe — IRI dereference over the published scope
(ADR instance-iri-base.md phase 2).

The Oxigraph side is a scripted httpx.MockTransport: the handler answers the
control-graph enumeration (canonical_graphs), the ontology-graph enumeration,
and the description SELECT/CONSTRUCT queries, so the tests pin the REAL query
composition (FROM NAMED over the published graphs only) without a store.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app

_CANONICAL = "https://kumagallium.github.io/asterism/graph/canonical/dataset-x/v1"
_ONTOLOGY = "https://kumagallium.github.io/asterism/graph/ontology/dataset-x"
_ENTITY = "https://data.lab.jp/asterism/datasets/xrd/resource/point/S1-10.00"
_UNKNOWN = "https://data.lab.jp/asterism/datasets/xrd/resource/point/nope"
_ONTOLOGY_NS = "https://data.lab.jp/asterism/datasets/xrd/ontology#"


def _settings(tmp: Path) -> Settings:
    return Settings(
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


def _select_json(rows: list[dict[str, dict[str, str]]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"head": {"vars": []}, "results": {"bindings": rows}},
        headers={"Content-Type": "application/sparql-results+json"},
    )


def _uri(v: str) -> dict[str, str]:
    return {"type": "uri", "value": v}


def _lit(v: str) -> dict[str, str]:
    return {"type": "literal", "value": v}


def _mock_client(*, promoted: bool = True) -> tuple[OxigraphClient, list[str]]:
    """A scripted store: one promoted dataset (canonical + ontology graph) and
    one entity with a label, a type and one inbound reference. Records every
    query so tests can assert the composed scope."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        queries.append(q)
        accept = request.headers.get("Accept", "")
        if "promoted" in q:  # canonical_graphs (control-graph enumeration)
            rows = [{"g": _uri(_CANONICAL)}] if promoted else []
            return _select_json(rows)
        if "GRAPH ?g {}" in q:  # ontology_graphs (empty-group enumeration)
            rows = [{"g": _uri(_ONTOLOGY)}] if promoted else []
            return _select_json(rows)
        if q.lstrip().startswith("CONSTRUCT"):
            assert "text/turtle" in accept
            if f"<{_ENTITY}> ?p ?o" in q:
                return httpx.Response(
                    200,
                    text=f"<{_ENTITY}> <https://schema.org/name> \"S1 point\" .\n",
                    headers={"Content-Type": "text/turtle"},
                )
            return httpx.Response(200, text="", headers={"Content-Type": "text/turtle"})
        # description SELECTs
        if "VALUES ?lp" in q:  # label lookup for the IRIs the page shows
            return _select_json(
                [
                    {
                        "t": _uri(f"{_ONTOLOGY_NS}hasIntensity"),
                        "l": _lit("強度"),
                    }
                ]
            )
        if f"<{_ENTITY}> ?p ?o" in q:  # outbound
            return _select_json(
                [
                    {
                        "p": _uri("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                        "o": _uri(f"{_ONTOLOGY_NS}DiffractionPoint"),
                        "g": _uri(_CANONICAL),
                    },
                    {
                        "p": _uri("http://www.w3.org/2000/01/rdf-schema#label"),
                        "o": _lit("S1 @ 10.00°"),
                        "g": _uri(_CANONICAL),
                    },
                    {
                        "p": _uri(f"{_ONTOLOGY_NS}hasIntensity"),
                        "o": {
                            "type": "literal",
                            "value": "1234.5",
                            "datatype": "http://www.w3.org/2001/XMLSchema#double",
                        },
                        "g": _uri(_CANONICAL),
                    },
                    {
                        "p": _uri(f"{_ONTOLOGY_NS}hasUnit"),
                        "o": _uri("http://qudt.org/vocab/unit/DEG"),
                        "g": _uri(_CANONICAL),
                    },
                    {
                        "p": _uri("http://www.w3.org/ns/prov#wasGeneratedBy"),
                        "o": _uri(
                            "https://data.lab.jp/asterism/datasets/xrd/resource/batch/b1"
                        ),
                        "g": _uri(_CANONICAL),
                    },
                ]
            )
        if f"?s ?p <{_ENTITY}>" in q:  # inbound
            return _select_json(
                [
                    {
                        "s": _uri("https://data.lab.jp/asterism/datasets/xrd/resource/scan/S1"),
                        "p": _uri(f"{_ONTOLOGY_NS}hasPoint"),
                        "g": _uri(_CANONICAL),
                    }
                ]
            )
        return _select_json([])  # unknown IRI: empty either way

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner), queries


def _app_client(tmp: Path, oxi: OxigraphClient) -> TestClient:
    app = build_app(_settings(tmp), oxigraph_client=oxi, start_watcher=False)
    return TestClient(app)


def test_describe_html_renders_published_description(tmp_path: Path) -> None:
    oxi, queries = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "S1 @ 10.00°" in body  # label became the title
    assert "DiffractionPoint" in body  # type chip
    assert "/describe?iri=" in body  # object IRIs dereference further
    assert "hasPoint" in body  # inbound reference listed
    # The description queries were scoped to the published graphs only.
    scoped = [q for q in queries if q.lstrip().startswith("SELECT ?p ?o ?g")]
    assert scoped and all(f"FROM NAMED <{_CANONICAL}>" in q for q in scoped)


def test_describe_html_speaks_the_kantan_vocabulary(tmp_path: Path) -> None:
    """This page is where a citation lands (ADR kantan-mode-two-tier-ux §5), so
    the fatal words must be gone from the normal view and exits must exist."""
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    body = r.text
    assert '<html lang="ja">' in body
    for jargon in ("<th>predicate", "<th>graph", "<th>subject", "Statements", "canonical"):
        assert jargon not in body
    assert "この ID について分かっていること" in body
    assert "出どころ" not in body  # no resolvable dataset: the column is dropped
    assert "ずっと変わらないウェブ上の住所" in body  # the ID box, in ADR wording
    assert 'href="/#/ask"' in body  # an exit exists
    assert 'href="/#/datasets"' in body
    # ^^double / @ja notation moved to a tooltip rather than sitting beside values
    assert "^^double" not in body
    assert 'title="double"' in body


def test_describe_uses_labels_and_folds_the_bookkeeping(tmp_path: Path) -> None:
    oxi, queries = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    body = r.text
    label_queries = [q for q in queries if "VALUES ?lp" in q]
    assert label_queries and f"FROM NAMED <{_ONTOLOGY}>" in label_queries[0]
    assert "強度" in body  # rdfs:label from the projected ontology, not hasIntensity
    # The pipeline's own bookkeeping (prov:*) lives in the folded block, and the
    # user's own value row is in the main table above it.
    head, _, folded = body.partition("<details>")
    assert "強度" in head
    assert "wasGeneratedBy" in folded
    assert "wasGeneratedBy" not in head


def test_describe_does_not_repeat_the_heading_and_the_type_chips(tmp_path: Path) -> None:
    """rdf:type and rdfs:label are ALREADY the h1 and the chips above the table —
    repeating them would spend the table's first rows on nothing new."""
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        body = client.get("/describe", params={"iri": _ENTITY}).text
    assert "S1 @ 10.00°" in body  # still the heading
    assert "DiffractionPoint" in body  # still a chip
    # ...but neither is a row any more.
    assert "rdf-syntax-ns#type" not in body
    assert "rdf-schema#label" not in body


def test_describe_says_so_when_only_a_name_and_a_type_are_recorded() -> None:
    """Dropping the rows must never leave an empty table under a promising
    heading — an entity whose whole description IS its name and type says that
    in one sentence."""
    from asterism_api.describe import render_html

    body = render_html(
        _ENTITY,
        {
            "graphs": [_CANONICAL],
            "outbound": [
                {
                    "p": _uri("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                    "o": _uri(f"{_ONTOLOGY_NS}DiffractionPoint"),
                    "g": _uri(_CANONICAL),
                },
                {
                    "p": _uri("http://www.w3.org/2000/01/rdf-schema#label"),
                    "o": _lit("S1 @ 10.00°"),
                    "g": _uri(_CANONICAL),
                },
            ],
            "inbound": [],
            "out_truncated": False,
            "in_truncated": False,
            "label": "S1 @ 10.00°",
            "labels": {},
            "types": [f"{_ONTOLOGY_NS}DiffractionPoint"],
        },
    )
    assert "この ID について記録されているのは、名前と種類だけです。" in body
    assert "<tbody></tbody>" not in body


def test_describe_does_not_link_the_item_column(tmp_path: Path) -> None:
    """項目 names a word, not a thing: following it lands on a definition page
    with none of the reader's data on it, one level deeper into nowhere."""
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        body = client.get("/describe", params={"iri": _ENTITY}).text
    assert "ontology%23hasIntensity" not in body  # the 項目 cell is not a link
    assert f'title="{_ONTOLOGY_NS}hasIntensity"' in body  # the full IRI is on hover
    assert "ontology%23hasPoint" not in body  # …in the inbound table either
    # The 値 column and the inbound subject — real things — stay followable.
    assert "resource%2Fscan%2FS1" in body


def test_describe_does_not_link_external_vocabulary(tmp_path: Path) -> None:
    """An external term has no statements in the published scope, so linking it
    would manufacture a dead end on the landing page."""
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        body = client.get("/describe", params={"iri": _ENTITY}).text
    assert "qudt.org%2Fvocab%2Funit%2FDEG" not in body
    assert 'title="http://qudt.org/vocab/unit/DEG"' in body
    # ...while an IRI this install mints stays browsable.
    assert "resource%2Fbatch%2Fb1" in body


def test_describe_resolves_source_graphs_to_dataset_names(tmp_path: Path) -> None:
    from asterism_api import registry

    settings = _settings(tmp_path)
    registry.save_dataset(
        Path(settings.registry_root),
        name="XRD 測定",
        artifacts={},
        created_at="2026-01-01T00:00:00Z",
        complete=True,
        warnings=[],
        exit_code=0,
        traps={},
    )
    # Re-point the canonical graph at the dataset the registry actually knows.
    dataset_id = registry.list_datasets(Path(settings.registry_root))[0]["id"]
    canonical = (
        f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}/v1"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        if "promoted" in q:
            return _select_json([{"g": _uri(canonical)}])
        if "GRAPH ?g {}" in q:
            return _select_json([])
        if "VALUES ?lp" in q:
            return _select_json([])
        if f"<{_ENTITY}> ?p ?o" in q:
            return _select_json(
                [
                    {
                        "p": _uri(f"{_ONTOLOGY_NS}hasIntensity"),
                        "o": _lit("1234.5"),
                        "g": _uri(canonical),
                    }
                ]
            )
        return _select_json([])

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    oxi = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)
    app = build_app(settings, oxigraph_client=oxi, start_watcher=False)
    with TestClient(app) as client:
        body = client.get("/describe", params={"iri": _ENTITY}).text
    assert "出どころ" in body  # the source column is back, named for a reader
    assert "XRD 測定" in body
    assert dataset_id not in body.split("<details>")[0].replace(
        f'href="/#/datasets/{dataset_id}"', ""
    )  # the opaque id appears only as the dataset link target
    assert f'href="/#/datasets/{dataset_id}"' in body


def test_describe_language_follows_accept_language_and_query(tmp_path: Path) -> None:
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        ja = client.get("/describe", params={"iri": _ENTITY})
        en = client.get(
            "/describe", params={"iri": _ENTITY}, headers={"Accept-Language": "en-US,en"}
        )
        forced = client.get(
            "/describe",
            params={"iri": _ENTITY, "lang": "ja"},
            headers={"Accept-Language": "en-US,en"},
        )
    assert "この ID について分かっていること" in ja.text
    assert '<html lang="en">' in en.text
    assert "What is known about this ID" in en.text
    assert "この ID について分かっていること" in forced.text


def test_describe_turtle_via_accept_and_format(tmp_path: Path) -> None:
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get(
            "/describe", params={"iri": _ENTITY}, headers={"Accept": "text/turtle"}
        )
        r2 = client.get("/describe", params={"iri": _ENTITY, "format": "ttl"})
    for res in (r, r2):
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/turtle")
        assert "S1 point" in res.text


def test_describe_unknown_iri_is_404_html(tmp_path: Path) -> None:
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _UNKNOWN})
    assert r.status_code == 404
    body = r.text
    assert "このリンクのデータは、まだ公開されていないようです" in body
    # No graph count, no canonical/promote/mint vocabulary, and always an exit.
    for jargon in ("canonical", "promoted", "minted", "graph(s)"):
        assert jargon not in body
    assert 'href="/#/datasets"' in body and 'href="/#/ask"' in body


def test_describe_rejects_non_http_iri(tmp_path: Path) -> None:
    """Machines still get the 400; a browser gets a page it can act on."""
    oxi, _ = _mock_client()
    bad = ["urn:uuid:x", "https://a b/c", "https://x/> } UNION { ?s ?p ?o "]
    with _app_client(tmp_path, oxi) as client:
        for iri in bad:
            r = client.get("/describe", params={"iri": iri})
            assert r.status_code == 400
            assert r.headers["content-type"].startswith("text/html")
            assert "リンクが正しくないようです" in r.text
        ttl = client.get(
            "/describe", params={"iri": bad[0], "format": "ttl"}
        )
        assert ttl.status_code == 400
        assert ttl.json()["detail"] == "iri must be an absolute http(s) IRI"


def test_describe_upstream_failure_is_html_for_browsers(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    oxi = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
        ttl = client.get("/describe", params={"iri": _ENTITY, "format": "ttl"})
    assert r.status_code == 502
    assert r.headers["content-type"].startswith("text/html")
    assert "いま一時的に読み込めませんでした" in r.text
    assert 'href="/#/datasets"' in r.text
    assert ttl.status_code == 502 and ttl.json()["detail"] == "upstream SPARQL error"


def test_describe_no_published_data_is_404(tmp_path: Path) -> None:
    oxi, _ = _mock_client(promoted=False)
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    assert r.status_code == 404


def test_describe_html_escapes_hostile_literals(tmp_path: Path) -> None:
    """A literal containing markup must render inert (the page is served from
    the api origin — an XSS here would run inside the product)."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        queries.append(q)
        if "promoted" in q:
            return _select_json([{"g": _uri(_CANONICAL)}])
        if "GRAPH ?g {}" in q:
            return _select_json([])
        if "?p ?o" in q and _ENTITY in q:
            return _select_json(
                [
                    {
                        "p": _uri("http://www.w3.org/2000/01/rdf-schema#label"),
                        "o": _lit("<script>alert(1)</script>"),
                        "g": _uri(_CANONICAL),
                    }
                ]
            )
        return _select_json([])

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    oxi = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    # The page's own copy helper is the only script on it.
    assert r.text.count("<script>") == 1


def test_label_query_is_valid_sparql_over_a_real_store() -> None:
    """The label lookup runs against pyoxigraph, not a mock: a query that only
    *looks* right would silently return no labels and the page would quietly
    fall back to English identifiers forever."""
    import pyoxigraph

    from asterism_api import describe as describe_mod

    store = pyoxigraph.Store()
    graph = pyoxigraph.NamedNode(_ONTOLOGY)
    term = pyoxigraph.NamedNode(f"{_ONTOLOGY_NS}hasIntensity")
    store.add(
        pyoxigraph.Quad(
            term,
            pyoxigraph.NamedNode("http://www.w3.org/2000/01/rdf-schema#label"),
            pyoxigraph.Literal("強度", language="ja"),
            graph,
        )
    )
    store.add(  # a label outside the scope must not leak in
        pyoxigraph.Quad(
            term,
            pyoxigraph.NamedNode("http://www.w3.org/2000/01/rdf-schema#label"),
            pyoxigraph.Literal("leaked"),
            pyoxigraph.NamedNode("https://example.org/other"),
        )
    )
    q = describe_mod.label_query([str(term.value)], [_ONTOLOGY])
    rows = [(s["t"].value, s["l"].value) for s in store.query(q)]
    assert rows == [(term.value, "強度")]


def test_instance_endpoint_still_reports_base(tmp_path: Path) -> None:
    """Companion sanity: /api/instance (previous PR) keeps serving the base the
    dereference story starts from."""
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        body = client.get("/api/instance").json()
    assert json.loads(json.dumps(body)) == {
        "iri_base": "https://asterism.invalid",
        "iri_base_configured": False,
        # Desktop identity (settings → About). Absent here: no shell started this.
        "app_version": None,
        "desktop": False,
        # No ASTERISM_API_TOKEN in this fixture: the write gate is shut for
        # everyone, so the settings UI has no token field to offer.
        "write_gate": "closed",
    }
