"""Checks for the **world** example dataset (object-cards-ui PR E, 契約メモ
contract_pr_e.md §1) — the real, citable見本 the first screen embeds.

Scope: only what the `ingest` package's own venv can import (rdflib / PyYAML —
this package does NOT depend on `asterism-step0` or `asterism-api`, see
``asterism.mapping_ir_read`` module docstring). The deeper
``asterism_api.exchange.import_snapshot`` round-trip is exercised instead by
``scripts/build_world_demo.py --verify`` (its default) — see that script's
``_verify_import`` — and mirrored here as an ``importorskip``-guarded test so
it still runs in any venv where ``asterism-api`` happens to be importable,
without making it a hard dependency of this test file / this package.

Real data only: every count below is the actual vega-datasets@2
``gapminder.json`` shape (62 countries x 11 years = 682 rows), not the
contract memo's illustrative "63 x 11 = 693" figure — see the deviation noted
in the PR description / final report.
"""
from __future__ import annotations

import csv
import hashlib
import json
import tarfile
from pathlib import Path

import pytest
import rdflib
import yaml

from asterism.mapping_ir_read import read_mapping_ir
from asterism.query_tools import lint_query_tool, parse_query_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "world"
WORLD_CSV = DATASET_DIR / "source" / "world.csv"
MAPPING_YAML = DATASET_DIR / "mapping.yaml"
QUERY_TOOLS_YAML = DATASET_DIR / "query_tools.yaml"
SNAPSHOT_TAR = DATASET_DIR / "snapshot.tar"

WORLD_NS = "https://kumagallium.github.io/asterism/world/ontology#"
RESOURCE_NS = "https://kumagallium.github.io/asterism/world/resource/"
PROV_NS = "http://www.w3.org/ns/prov#"
ACTIVITY_IRI = RESOURCE_NS + "activity/ingest-world-v1"

EXPECTED_COLUMNS = [
    "country",
    "country_ja",
    "region",
    "region_ja",
    "year",
    "population",
    "life_expectancy",
    "fertility",
]

# The real shape of vega-datasets@2's gapminder.json (checked against the
# fetched source/gapminder.json: 62 countries, complete 11-year grid).
EXPECTED_ROWS = 682
EXPECTED_COUNTRIES = 62
EXPECTED_YEARS = 11


def _read_world_csv_rows() -> list[dict[str, str]]:
    with WORLD_CSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# source/world.csv
# ---------------------------------------------------------------------------


def test_world_csv_has_the_expected_shape() -> None:
    rows = _read_world_csv_rows()
    assert len(rows) == EXPECTED_ROWS
    assert len({r["country"] for r in rows}) == EXPECTED_COUNTRIES
    assert len({r["year"] for r in rows}) == EXPECTED_YEARS
    with WORLD_CSV.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert header == EXPECTED_COLUMNS


def test_world_csv_is_a_complete_country_year_grid() -> None:
    """Every country carries every year — a rectangular 62 x 11 grid, not a
    ragged one (guards a future re-run against a partial vega-datasets fetch)."""
    rows = _read_world_csv_rows()
    by_country: dict[str, set[str]] = {}
    for r in rows:
        by_country.setdefault(r["country"], set()).add(r["year"])
    assert len(by_country) == EXPECTED_COUNTRIES
    year_sets = {frozenset(v) for v in by_country.values()}
    assert len(year_sets) == 1  # every country has the SAME set of years
    assert len(next(iter(year_sets))) == EXPECTED_YEARS


def test_world_csv_country_ja_never_falls_back_silently_for_known_edge_cases() -> None:
    """Spot-check a handful of countries whose English name is not itself a
    valid IRI segment / not obviously mappable, to catch a silent English
    fallback regression for names the lookup table DOES cover."""
    rows = {r["country"]: r["country_ja"] for r in _read_world_csv_rows()}
    assert rows["Japan"] == "日本"
    assert rows["United States"] == "アメリカ合衆国"
    assert rows["Hong Kong, China"] == "香港"
    assert rows["South Korea"] == "韓国"
    assert rows["North Korea"] == "北朝鮮"


# ---------------------------------------------------------------------------
# mapping.yaml — readable by the ingest-side view (asterism.mapping_ir_read,
# the same reader `asterism.class_schema` / `asterism.shape_match` use)
# ---------------------------------------------------------------------------


def test_mapping_yaml_is_readable_by_mapping_ir_read() -> None:
    text = MAPPING_YAML.read_text(encoding="utf-8")
    view = read_mapping_ir(text)
    assert view.prefixes["world"] == WORLD_NS
    assert view.prefixes["wr"] == RESOURCE_NS
    assert view.prefixes["prov"] == PROV_NS
    names = {m.name for m in view.maps}
    assert names == {"activity", "country", "observation"}

    activity_map = next(m for m in view.maps if m.name == "activity")
    assert activity_map.source == "activity.csv"
    assert activity_map.subject_classes == ("prov:Activity", "world:IngestionActivity")
    predicates = {p.predicate for p in activity_map.properties}
    assert predicates == {
        "prov:used",
        "prov:endedAtTime",
        "prov:wasAssociatedWith",
        "rdfs:label",
    }

    country_map = next(m for m in view.maps if m.name == "country")
    assert country_map.source == "world.csv"
    assert country_map.subject_classes == ("world:Country",)
    assert country_map.subject_columns == ("country",)
    predicates = {p.predicate for p in country_map.properties}
    assert predicates == {
        "rdfs:label",
        "schema:name",
        "world:nameJa",
        "world:region",
        "world:regionJa",
        "prov:wasGeneratedBy",
    }

    observation_map = next(m for m in view.maps if m.name == "observation")
    assert observation_map.subject_classes == ("world:Observation",)
    assert observation_map.subject_columns == ("country", "year")
    predicates = {p.predicate for p in observation_map.properties}
    assert predicates == {
        "rdfs:label",
        "world:year",
        "world:population",
        "world:lifeExpectancy",
        "world:fertility",
        "world:ofCountry",
        "prov:wasGeneratedBy",
    }
    life_expectancy = next(
        p for p in observation_map.properties if p.predicate == "world:lifeExpectancy"
    )
    assert life_expectancy.unit == "YR"
    assert life_expectancy.datatype == "xsd:double"


def test_country_map_region_ja_is_declared_before_region() -> None:
    """実機所見: 絞り込みの内訳は distinct が最小の分類プロパティを選び、
    同点は Mapping IR の宣言順で先勝ちする — `regionJa`（日本語の地域名）を
    `region`（英語）より前に置かないと内訳が英語で出る。"""
    view = read_mapping_ir(MAPPING_YAML.read_text(encoding="utf-8"))
    country_map = next(m for m in view.maps if m.name == "country")
    order = [
        p.predicate
        for p in country_map.properties
        if p.predicate in ("world:regionJa", "world:region")
    ]
    assert order == ["world:regionJa", "world:region"]


def test_country_map_rdfs_label_rows_share_one_item_label() -> None:
    """`asterism.subject_tools` の事実の表は述語ごとに 1 つしかラベルを持てない
    （class_schema の properties は predicate IRI をキーに最後の行が勝つ）ので、
    同じ predicate（rdfs:label）の 2 行（ja/en）は同じ ``label:`` に揃える —
    片方だけ変えても「見出し（英語）」がもう片方まで乗っ取ってしまう。"""
    view = read_mapping_ir(MAPPING_YAML.read_text(encoding="utf-8"))
    country_map = next(m for m in view.maps if m.name == "country")
    labels = {p.label for p in country_map.properties if p.predicate == "rdfs:label"}
    assert labels == {"見出し"}


# ---------------------------------------------------------------------------
# query_tools.yaml — parses + lints clean (asterism.query_tools, the same
# engine every dataset's tools load through)
# ---------------------------------------------------------------------------


def test_query_tools_yaml_parses_and_lints_clean() -> None:
    data = yaml.safe_load(QUERY_TOOLS_YAML.read_text(encoding="utf-8"))
    tools = parse_query_tools(data)
    names = {t.name for t in tools}
    assert names == {
        "life_expectancy_by_year",
        "population_by_year",
        "population_vs_life_expectancy",
        "countries_by_population",
        "countries_by_region",
        "counts_by_kind",
    }
    for tool in tools:
        lint = lint_query_tool(tool)
        assert lint.errors == (), f"{tool.name}: {lint.errors}"


def test_countries_by_population_defaults_match_contract() -> None:
    data = yaml.safe_load(QUERY_TOOLS_YAML.read_text(encoding="utf-8"))
    params = {t["name"]: t["parameters"] for t in data["tools"]}["countries_by_population"]
    year_param = next(p for p in params if p["name"] == "year")
    top_n_param = next(p for p in params if p["name"] == "top_n")
    assert year_param["default"] == 2005
    assert top_n_param["default"] == 20


# ---------------------------------------------------------------------------
# snapshot.tar — a well-formed asterism-snapshot archive
# ---------------------------------------------------------------------------


def _extract_snapshot() -> dict[str, bytes]:
    """Mirror ``asterism_api.exchange._safe_members``'s safety checks (no
    absolute/traversal member paths) without importing that module — this
    package's venv has neither ``asterism-api`` nor ``fastapi`` (see module
    docstring)."""
    out: dict[str, bytes] = {}
    with tarfile.open(SNAPSHOT_TAR, mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            assert not name.startswith(("/", "\\")), f"unsafe absolute path: {name}"
            assert ".." not in Path(name).parts, f"unsafe traversal path: {name}"
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            assert extracted is not None
            out[name] = extracted.read()
    return out


def test_snapshot_tar_matches_the_asterism_snapshot_format() -> None:
    members = _extract_snapshot()
    manifest = json.loads(members["manifest.json"])
    # Literal values, not an import of asterism_api.exchange (unavailable in
    # this venv) — SNAPSHOT_FORMAT / SNAPSHOT_FORMAT_VERSION there.
    assert manifest["format"] == "asterism-snapshot"
    assert manifest["format_version"] == 1
    assert manifest["dataset_id"] == "world"

    canonical_ttl = members["graphs/canonical.ttl"]
    assert hashlib.sha256(canonical_ttl).hexdigest() == manifest["canonical_sha256"]
    assert manifest["canonical_triples"] > 0

    meta = json.loads(members["registry/meta.json"])
    assert meta["id"] == "world"
    assert meta["has_mapping_ir"] is True
    assert meta["has_rml"] is True
    assert meta["source_kind"] == "csv"
    assert meta["source_files"] == ["activity.csv", "world.csv"]

    for path in (
        "registry/model.yaml",
        "registry/mie.yaml",
        "registry/mapping.yaml",
        "registry/mapping.rml.ttl",
        "registry/query_tools.yaml",
        "registry/source/world.csv",
        "registry/source/activity.csv",
    ):
        assert path in members, f"snapshot is missing {path}"


async def test_snapshot_tar_is_importable_via_exchange_import_snapshot() -> None:
    """The strongest check (契約メモ §1: 「自分で import_snapshot をテストで
    呼んで確認」) — actually calls ``asterism_api.exchange.import_snapshot``.
    Skipped when ``asterism-api`` (and its ``fastapi`` dependency) is not
    importable, which is the normal case for `ingest`'s own isolated venv;
    this build's own self-check (``scripts/build_world_demo.py``'s default
    ``--verify``, run from the `api` venv where both packages are installed
    together) is what actually exercises this path in CI-equivalent terms."""
    pytest.importorskip("fastapi")
    exchange = pytest.importorskip("asterism_api.exchange")
    import tempfile
    from types import SimpleNamespace

    class _RdflibStoreClient:
        def __init__(self) -> None:
            self.ds = rdflib.Dataset()

        async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
            g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_context
            g.parse(data=payload, format="turtle")
            return len(payload)

        async def sparql_select(self, query: str) -> dict:
            raw = self.ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

        async def sparql_update(self, update: str) -> None:
            self.ds.update(update)

        async def graph_triple_count(self, graph_iri: str) -> int:
            data = await self.sparql_select(
                f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}"
            )
            bindings = data["results"]["bindings"]
            return int(bindings[0]["c"]["value"]) if bindings else 0

    tar_bytes = SNAPSHOT_TAR.read_bytes()
    with tempfile.TemporaryDirectory(prefix="world-demo-test-") as tmp:
        cfg = SimpleNamespace(registry_root=Path(tmp), iri_base="https://asterism.invalid")
        client = _RdflibStoreClient()
        result = await exchange.import_snapshot(
            cfg, client, tar_bytes, max_extracted_bytes=64 * 1024 * 1024
        )
    assert result["dataset_id"] == "world"
    assert result["status"] == "ingested"
    assert result["triples"] > 0


# ---------------------------------------------------------------------------
# graphs/canonical.ttl — real instance counts (rdflib)
# ---------------------------------------------------------------------------


def test_canonical_ttl_has_the_expected_instance_counts() -> None:
    members = _extract_snapshot()
    g = rdflib.Graph()
    g.parse(data=members["graphs/canonical.ttl"], format="turtle")

    n_country = len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(WORLD_NS + "Country"))))
    n_observation = len(
        list(g.subjects(rdflib.RDF.type, rdflib.URIRef(WORLD_NS + "Observation")))
    )
    assert n_country == EXPECTED_COUNTRIES
    assert n_observation == EXPECTED_ROWS

    japan = rdflib.URIRef(RESOURCE_NS + "country/japan")
    assert (japan, rdflib.URIRef(WORLD_NS + "nameJa"), rdflib.Literal("日本")) in g
    hong_kong = rdflib.URIRef(RESOURCE_NS + "country/hong-kong-china")
    assert (hong_kong, rdflib.URIRef(WORLD_NS + "nameJa"), rdflib.Literal("香港")) in g

    japan_2005 = rdflib.URIRef(RESOURCE_NS + "observation/japan-2005")
    assert (japan_2005, rdflib.URIRef(WORLD_NS + "ofCountry"), japan) in g


def test_canonical_ttl_has_ja_and_en_rdfs_label_for_japan() -> None:
    """The actual defect this build fixes (実機所見): the見本 subject page's
    heading was resolving to "Japan" because ``asterism.subjects.pick_label``
    (rdfs:label -> schema:name -> ... priority; ja -> en -> untagged among
    ties) only ever saw ``schema:name "Japan"`` — ``world:nameJa`` is a
    dataset-private predicate the shared label picker does not know. `country`
    now also carries ``rdfs:label`` itself, in both languages, so the picker's
    top-ranked predicate has a Japanese candidate to prefer."""
    members = _extract_snapshot()
    g = rdflib.Graph()
    g.parse(data=members["graphs/canonical.ttl"], format="turtle")

    japan = rdflib.URIRef(RESOURCE_NS + "country/japan")
    labels = {
        (str(o), o.language)
        for o in g.objects(japan, rdflib.URIRef("http://www.w3.org/2000/01/rdf-schema#label"))
    }
    assert ("日本", "ja") in labels
    assert ("Japan", "en") in labels


def test_pick_label_prefers_japan_country_rdfs_label_ja_over_en() -> None:
    """``asterism.subjects.pick_label`` — the ONE shared "which literal is THE
    display label" rule every read path composes into its own query — actually
    resolves Japan's card heading to "日本", not "Japan", given the two
    rdfs:label candidates this build now emits (rank 0 = rdfs:label, the
    predicate's top priority; ja beats en among rows tied for best rank)."""
    from asterism.subjects import pick_label

    members = _extract_snapshot()
    g = rdflib.Graph()
    g.parse(data=members["graphs/canonical.ttl"], format="turtle")

    japan = rdflib.URIRef(RESOURCE_NS + "country/japan")
    candidates = [
        (str(o), 0, o.language)
        for o in g.objects(japan, rdflib.URIRef("http://www.w3.org/2000/01/rdf-schema#label"))
    ]
    assert pick_label(candidates) == "日本"


# ---------------------------------------------------------------------------
# provenance — checker blocker fix: the real Mapping IR -> RML -> Morph-KGC
# path DOES attach an ingestion activity (contract memo follow-up §1), not
# hand-written Turtle.
# ---------------------------------------------------------------------------


def test_canonical_ttl_has_the_ingestion_activity_and_wasGeneratedBy_links() -> None:
    members = _extract_snapshot()
    g = rdflib.Graph()
    g.parse(data=members["graphs/canonical.ttl"], format="turtle")

    activity_type = rdflib.URIRef("http://www.w3.org/ns/prov#Activity")
    was_generated_by = rdflib.URIRef("http://www.w3.org/ns/prov#wasGeneratedBy")
    used = rdflib.URIRef("http://www.w3.org/ns/prov#used")
    ended_at_time = rdflib.URIRef("http://www.w3.org/ns/prov#endedAtTime")

    activities = list(g.subjects(rdflib.RDF.type, activity_type))
    assert len(activities) == 1
    activity = activities[0]
    assert str(activity) == RESOURCE_NS + "activity/ingest-world-v1"
    assert (activity, rdflib.RDF.type, rdflib.URIRef(WORLD_NS + "IngestionActivity")) in g

    generated_by_links = list(g.subject_objects(was_generated_by))
    assert len(generated_by_links) == EXPECTED_COUNTRIES + EXPECTED_ROWS  # 62 + 682 = 744
    assert all(obj == activity for _subj, obj in generated_by_links)

    used_objs = list(g.objects(activity, used))
    assert used_objs == [
        rdflib.URIRef("https://cdn.jsdelivr.net/npm/vega-datasets@2/data/gapminder.json")
    ]
    ended_at = list(g.objects(activity, ended_at_time))
    assert len(ended_at) == 1


# ---------------------------------------------------------------------------
# model.yaml — the class-level Japanese label `asterism.class_schema.class_label`
# reads first (実機所見: 絞り込みページの見出しが英語の "Country" に落ちていた —
# model.yaml が rdf-config の例示リスト形しか持たず、`classes.<curie>.label` を
# 名乗れていなかった)。
# ---------------------------------------------------------------------------


async def test_model_yaml_class_label_matches_contract_labels() -> None:
    class_schema = pytest.importorskip("asterism.class_schema")
    import json as _json

    class _NoopClient:
        async def sparql_select(self, query: str) -> dict:
            return {"results": {"bindings": []}}

    def _build_registry(tmp_path: Path) -> Path:
        dest = tmp_path / "world"
        dest.mkdir(parents=True)
        (dest / "model.yaml").write_text(
            (DATASET_DIR / "model.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (dest / "mapping.yaml").write_text(
            MAPPING_YAML.read_text(encoding="utf-8"), encoding="utf-8"
        )
        meta = {"id": "world", "promoted": True, "promoted_at": "2024-01-01"}
        (dest / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
        return tmp_path

    import tempfile

    with tempfile.TemporaryDirectory(prefix="world-model-label-") as tmp:
        registry_root = _build_registry(Path(tmp))
        client = _NoopClient()
        country_label = await class_schema.class_label(
            client, registry_root, WORLD_NS + "Country"
        )
        observation_label = await class_schema.class_label(
            client, registry_root, WORLD_NS + "Observation"
        )
    assert country_label == "国"
    assert observation_label == "年ごとの記録"


async def test_prov_graph_follows_wasGeneratedBy_from_japan_to_the_activity() -> None:
    """``asterism.prov_graph.prov_graph`` (the generic PROV-O reader) actually
    walks the edge this build produced, for a real subject a first-time
    visitor would open (the embedded "日本" subject page)."""
    pyoxigraph = pytest.importorskip("pyoxigraph")
    from asterism.prov_graph import prov_graph
    from asterism.substrate import (
        CANONICAL_GRAPH_BASE,
        CONTROL_GRAPH_IRI,
        STATUS_PREDICATE,
        STATUS_PROMOTED,
    )

    members = _extract_snapshot()
    canonical_ttl = members["graphs/canonical.ttl"]
    version_graph = "https://kumagallium.github.io/asterism/graph/canonical/world/v1"
    assert version_graph.startswith(CANONICAL_GRAPH_BASE)

    store = pyoxigraph.Store()
    store.load(
        canonical_ttl,
        mime_type="text/turtle",
        to_graph=pyoxigraph.NamedNode(version_graph),
    )
    store.add(
        pyoxigraph.Quad(
            pyoxigraph.NamedNode(version_graph),
            pyoxigraph.NamedNode(STATUS_PREDICATE),
            pyoxigraph.Literal(STATUS_PROMOTED),
            pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
        )
    )

    class _Client:
        async def sparql_select(self, query: str) -> dict:
            result = store.query(query)
            names = [v.value for v in result.variables]
            bindings = []
            for solution in result:
                row = {}
                for name in names:
                    term = solution[name]
                    if term is None:
                        continue
                    kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                    row[name] = {"type": kind, "value": term.value}
                bindings.append(row)
            return {"results": {"bindings": bindings}}

    japan = RESOURCE_NS + "country/japan"
    out = await prov_graph(_Client(), japan)
    assert out["found"] is True
    edges = {(e["from"], e["to"], e["label"]) for e in out["graph"]["edges"]}
    assert (ACTIVITY_IRI, japan, "generated") in edges
    activity_node = next(n for n in out["graph"]["nodes"] if n["id"] == ACTIVITY_IRI)
    assert activity_node["kind"] == "activity"
