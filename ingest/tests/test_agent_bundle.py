"""Tests for asterism.agent_bundle (契約メモ §3 — D1-export).

Backed by a real ``pyoxigraph.Store`` (same contract as
``ingest/tests/test_subject_tools.py``'s ``_pyoxi_client``), with the part5
control-graph triples set up explicitly (``<canonical/id>`` promoted +
``liveGraph`` -> a versioned data graph) so ``facts/control.trig`` has a real
``liveGraph`` pointer to assert on. Fixture data spans two unrelated
fictional domains (library checkouts / a field-log) — no materials-science
noun anywhere (§0).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import asterism.agent_bundle as agent_bundle_mod
from asterism import subject_tools
from asterism.agent_bundle import NotShareableError, build_export_bundle
from asterism.substrate import (
    CONTROL_GRAPH_IRI,
    LIVE_GRAPH_PREDICATE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")
rdflib = pytest.importorskip("rdflib")


def _pyoxi_client(graphs: dict[str, str], *, promote: dict[str, str]):
    """``graphs``: ``{graph_iri: ttl}``. ``promote``: ``{dataset_id:
    version_graph_iri}`` — part5 control triples (``promoted`` + ``liveGraph``)
    for each, on the dataset's KEY canonical graph (distinct from the version
    graph the data actually lives in — same shape a real promote leaves)."""
    store = pyoxigraph.Store()
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
    for dataset_id, version_graph in promote.items():
        key = canonical_graph_iri(dataset_id)
        store.add(
            pyoxigraph.Quad(
                pyoxigraph.NamedNode(key),
                pyoxigraph.NamedNode(STATUS_PREDICATE),
                pyoxigraph.Literal(STATUS_PROMOTED),
                pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
            )
        )
        store.add(
            pyoxigraph.Quad(
                pyoxigraph.NamedNode(key),
                pyoxigraph.NamedNode(LIVE_GRAPH_PREDICATE),
                pyoxigraph.NamedNode(version_graph),
                pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
            )
        )

    class _C:
        async def sparql_select(self, query: str) -> dict:
            result = store.query(query)
            if isinstance(result, bool):
                return {"head": {}, "boolean": result}
            names = [v.value for v in result.variables]
            bindings = []
            for solution in result:
                row = {}
                for name in names:
                    term = solution[name]
                    if term is None:
                        continue
                    if isinstance(term, pyoxigraph.NamedNode):
                        row[name] = {"type": "uri", "value": term.value}
                    elif isinstance(term, pyoxigraph.Literal):
                        cell = {"type": "literal", "value": term.value}
                        if term.language:
                            cell["xml:lang"] = term.language
                        elif term.datatype:
                            cell["datatype"] = term.datatype.value
                        row[name] = cell
                bindings.append(row)
            return {"results": {"bindings": bindings}}

        async def sparql_update(self, update: str) -> None:
            store.update(update)

    return _C()


# ---------------------------------------------------------------------------
# Fixture data — two unrelated fictional domains (§0).
# ---------------------------------------------------------------------------

EX_LIB = "https://ex/library#"
CHECKOUT_CLASS = EX_LIB + "CheckoutRecord"
BORROWER_PRED = EX_LIB + "borrower"
BRANCH_PRED = EX_LIB + "branch"

LIB_DATASET = "library-checkouts"
LIB_GRAPH = canonical_graph_iri(LIB_DATASET) + "/v1"

CHECKOUT_1 = "https://ex/library/resource/checkout-1"
BORROWER_A = "https://ex/library/resource/person-a"

_LIB_TTL = f"""
@prefix ex: <{EX_LIB}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<{CHECKOUT_1}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout One" ;
    ex:borrower <{BORROWER_A}> ;
    ex:branch "north" .

<{BORROWER_A}> rdfs:label "Borrower A" .
"""

EX_FIELD = "https://ex/field#"
READING_CLASS = EX_FIELD + "Reading"

FIELD_DATASET = "field-log"
FIELD_GRAPH = canonical_graph_iri(FIELD_DATASET) + "/v1"

READING_1 = "https://ex/field/resource/reading-1"

_FIELD_TTL = f"""
@prefix ex: <{EX_FIELD}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{READING_1}> a <{READING_CLASS}> ;
    rdfs:label "Reading One" ;
    ex:reading "42.5"^^xsd:double .
"""


def _client(*, with_field: bool = False) -> Any:
    graphs = {LIB_GRAPH: _LIB_TTL}
    promote = {LIB_DATASET: LIB_GRAPH}
    if with_field:
        graphs[FIELD_GRAPH] = _FIELD_TTL
        promote[FIELD_DATASET] = FIELD_GRAPH
    return _pyoxi_client(graphs, promote=promote)


_LIB_TOOLS_YAML = """
tools:
  - name: branch_of
    title: "貸出元"
    output_kind: facts
    parameters:
      - name: checkout
        type: iri
        required: true
    query: |
      SELECT ?v WHERE { BIND({{checkout}} AS ?s) ?s <https://ex/library#branch> ?v } LIMIT 1
    result:
      item:
        value: {var: v, role: value}
"""


def _write_registry(
    root: Path, dataset_id: str = LIB_DATASET, name: str = "Library", *, with_tools: bool = False
) -> None:
    dest = root / dataset_id
    dest.mkdir(parents=True, exist_ok=True)
    meta = {"id": dataset_id, "name": name, "promoted": True, "classes": [CHECKOUT_CLASS]}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if with_tools:
        (dest / "query_tools.yaml").write_text(_LIB_TOOLS_YAML, encoding="utf-8")


def _zip_names(zip_bytes: bytes) -> set[str]:
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return set(zf.namelist())


def _zip_read(zip_bytes: bytes, name: str) -> str:
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        return zf.read(name).decode("utf-8")


# ---------------------------------------------------------------------------
# zip shape (§3's own test contract)
# ---------------------------------------------------------------------------


async def test_bundle_has_every_required_file(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    for required in (
        f"{slug}/AGENT.md",
        f"{slug}/mcp.json",
        f"{slug}/README.md",
        f"{slug}/materials.json",
        f"{slug}/facts/facts.trig",
        f"{slug}/facts/control.trig",
        f"{slug}/cards/card-1.json",
    ):
        assert required in names, names
    assert bundle.filename == f"{slug}-agent.zip"


async def test_declared_tool_card_writes_dataset_tools_and_meta(tmp_path: Path) -> None:
    """A card using a declared tool (``tool = '<dataset_id>/<tool_name>'``,
    契約メモ §3) must land in ``tools/<dataset_id>/query_tools.yaml`` with a
    ``meta.json`` carrying exactly the 8 documented keys."""
    _write_registry(tmp_path, with_tools=True)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[
            {
                "card_id": "card-1",
                "tool": f"{LIB_DATASET}/branch_of",
                "params": {"checkout": CHECKOUT_1},
            }
        ],
        share="full",
        lang="ja",
    )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    assert f"{slug}/tools/{LIB_DATASET}/query_tools.yaml" in names
    assert f"{slug}/tools/{LIB_DATASET}/meta.json" in names

    import yaml as yaml_mod

    tools_doc = yaml_mod.safe_load(
        _zip_read(bundle.zip_bytes, f"{slug}/tools/{LIB_DATASET}/query_tools.yaml")
    )
    assert [t["name"] for t in tools_doc["tools"]] == ["branch_of"]

    meta = json.loads(_zip_read(bundle.zip_bytes, f"{slug}/tools/{LIB_DATASET}/meta.json"))
    assert set(meta) == {
        "id",
        "name",
        "promoted",
        "canonical_graph",
        "live_graph",
        "classes",
        "origin",
        "license",
    }
    assert meta["id"] == LIB_DATASET


async def test_facts_trig_parses_with_rdflib_and_graph_is_the_version_graph(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    text = _zip_read(bundle.zip_bytes, f"{bundle.slug}/facts/facts.trig")
    ds = rdflib.Dataset()
    ds.parse(data=text, format="trig")
    graph_iris = {
        str(g.identifier) for g in ds.graphs() if str(g.identifier) != "urn:x-rdflib:default"
    }
    assert LIB_GRAPH in graph_iris


async def test_control_trig_carries_live_graph_pointer(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    text = _zip_read(bundle.zip_bytes, f"{bundle.slug}/facts/control.trig")
    ds = rdflib.Dataset()
    ds.parse(data=text, format="trig")
    control = ds.graph(rdflib.URIRef(CONTROL_GRAPH_IRI))
    key = rdflib.URIRef(canonical_graph_iri(LIB_DATASET))
    assert (key, rdflib.URIRef(LIVE_GRAPH_PREDICATE), rdflib.URIRef(LIB_GRAPH)) in control
    assert (
        key,
        rdflib.URIRef(STATUS_PREDICATE),
        rdflib.Literal(STATUS_PROMOTED),
    ) in control


async def test_control_trig_drops_unsafe_live_graph_iri(tmp_path: Path) -> None:
    """A store that returns a ``?lg`` value the SPARQL IRIREF grammar
    forbids (defense in depth — a well-behaved store never does this, but
    nothing should trust that blindly, per :func:`asterism.subjects.safe_iri`)
    must not have it embedded into ``control.trig`` — it is dropped, not
    written raw."""
    unsafe_live = f"{LIB_GRAPH}> <http://evil/injected> <http://evil/x"

    class _UnsafeLiveClient:
        async def sparql_select(self, query: str) -> dict:
            if "?lg" in query and "VALUES ?c" in query:
                key = canonical_graph_iri(LIB_DATASET)
                return {
                    "results": {
                        "bindings": [
                            {
                                "c": {"type": "uri", "value": key},
                                "lg": {"type": "uri", "value": unsafe_live},
                            }
                        ]
                    }
                }
            return {"results": {"bindings": []}}

    control = await agent_bundle_mod._control_info(_UnsafeLiveClient(), {LIB_DATASET})
    assert control[LIB_DATASET]["live_graph"] is None
    trig = agent_bundle_mod._render_control_trig(control)
    assert "evil" not in trig
    assert unsafe_live not in trig


def test_render_control_trig_drops_unsafe_canonical_graph() -> None:
    """A ``canonical_graph`` value that fails :func:`safe_iri` (e.g. built
    from a dataset_id with an unsafe character upstream) must not reach
    ``control.trig`` either — the whole entry is skipped."""
    unsafe_key = "https://ex/canonical/oops> <http://evil/injected> <http://evil/x"
    control = {
        "bad": {"canonical_graph": unsafe_key, "live_graph": LIB_GRAPH, "effective_graph": None},
        LIB_DATASET: {
            "canonical_graph": canonical_graph_iri(LIB_DATASET),
            "live_graph": LIB_GRAPH,
            "effective_graph": LIB_GRAPH,
        },
    }
    trig = agent_bundle_mod._render_control_trig(control)
    assert "evil" not in trig
    assert LIB_GRAPH in trig


async def test_builtin_page_facts_tool_lints_clean(tmp_path: Path) -> None:
    from asterism.query_tools import lint_query_tool, parse_query_tools

    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    import yaml

    text = _zip_read(bundle.zip_bytes, f"{bundle.slug}/tools/_builtin/query_tools.yaml")
    doc = yaml.safe_load(text)
    tools = parse_query_tools(doc)
    names = [t.name for t in tools]
    assert "page_facts" in names
    for tool in tools:
        lint = lint_query_tool(tool)
        assert not lint.errors, (tool.name, lint.errors)


async def test_builtin_queries_carry_no_from_clause(tmp_path: Path) -> None:
    """束の ``tools/_builtin/query_tools.yaml`` の ``query`` は FROM/FROM
    NAMED を一切持たない素の問い合わせでなければならない — ``asterism-agent
    serve`` 側（束の store）が実行時に own の版グラフで FROM を注入する
    契約（実機で FROM 込みのまま書き出すと束にない他データセットの版グラフ
    が混ざり、束の store の許可リストに無いので毎回 0 件で落ちていた）。"""
    import yaml

    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    text = _zip_read(bundle.zip_bytes, f"{bundle.slug}/tools/_builtin/query_tools.yaml")
    doc = yaml.safe_load(text)
    assert doc["tools"], doc
    for tool in doc["tools"]:
        query = tool["query"]
        assert "FROM <" not in query, (tool["name"], query)
        assert "FROM NAMED <" not in query, (tool["name"], query)


async def test_builtin_page_facts_query_returns_rows_once_from_is_reinjected(
    tmp_path: Path,
) -> None:
    """束の store 側が実行時にすることの最小シミュレーション:
    ``canonical_from_clauses`` で own の版グラフを注入して束の ``page_facts``
    query を pyoxigraph の実 store に投げると、facts が 1 行以上返る（FROM
    抜きの生クエリが実際に正しい形であることの固定）。"""
    import yaml

    from asterism.query_tools import parse_query_tools, render_query
    from asterism.substrate import canonical_from_clauses

    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    text = _zip_read(bundle.zip_bytes, f"{bundle.slug}/tools/_builtin/query_tools.yaml")
    doc = yaml.safe_load(text)
    tools = {t.name: t for t in parse_query_tools(doc)}
    tool = tools["page_facts"]
    bare = render_query(tool, {})
    reinjected = bare.replace("WHERE {", canonical_from_clauses([LIB_GRAPH]) + "WHERE {", 1)

    store = pyoxigraph.Store()
    store.load(
        _LIB_TTL.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(LIB_GRAPH)
    )
    result = store.query(reinjected)
    rows = list(result)
    assert len(rows) >= 1


async def test_builtin_item_overrides_vars_are_projected_by_their_query() -> None:
    """契約メモ D 修正の固定テスト: :data:`agent_bundle._BUILTIN_ITEM_OVERRIDES`
    の各 ``var`` は、その組み込みツールが実際に組む生 SPARQL の SELECT 節に
    必ず現れる — ずれると ``run_query_tool``/``_shape_row`` が全行 None を
    返す（実機所見: 束の ``page_facts`` で ``count`` は出るのに行の中身が
    全部 ``null`` になっていた）。5 つの組み込みツール全部を実行して機械的
    に確認する。"""
    client = _client()
    sparql_by_bundle_name: dict[str, str | None] = {}

    facts = await subject_tools.subject_facts(client, CHECKOUT_1)
    sparql_by_bundle_name["page_facts"] = facts["sparql"]

    sources = await subject_tools.subject_sources(client, CHECKOUT_1)
    sparql_by_bundle_name["page_sources"] = sources["sparql"]

    members = await subject_tools.set_members(
        client,
        {
            "class": CHECKOUT_CLASS,
            "where": [],
            "order_by": {"property": BORROWER_PRED, "dir": "asc"},
        },
    )
    sparql_by_bundle_name["page_members"] = members["sparql"]

    breakdown = await subject_tools.set_breakdown(
        client, {"class": CHECKOUT_CLASS, "where": []}, BRANCH_PRED
    )
    sparql_by_bundle_name["page_breakdown"] = breakdown["sparql"]

    count = await subject_tools.set_count(client, {"class": CHECKOUT_CLASS, "where": []})
    sparql_by_bundle_name["page_count"] = count["sparql"]

    assert set(sparql_by_bundle_name) == set(agent_bundle_mod._BUILTIN_ITEM_OVERRIDES)
    for bundle_name, overrides in agent_bundle_mod._BUILTIN_ITEM_OVERRIDES.items():
        sparql = sparql_by_bundle_name[bundle_name]
        assert sparql, bundle_name
        stripped = agent_bundle_mod._strip_from_clauses(sparql)
        projected = agent_bundle_mod._select_projected_vars(stripped)
        for key, spec in overrides.items():
            assert spec["var"] in projected, (bundle_name, key, spec, projected)


async def test_builtin_page_facts_shapes_rows_with_non_none_values(tmp_path: Path) -> None:
    """束の store 実行を pyoxigraph で再現し、``page_facts`` の shaped 行
    （``asterism.query_tools._shape_row`` 相当）が ``property_iri``/``value``
    とも ``None`` でないことを固定する（item の var がずれていた頃はここが
    全部 ``None`` になっていた — 実機所見）。"""
    import yaml

    from asterism.query_tools import _shape_row, parse_query_tools, render_query
    from asterism.substrate import canonical_from_clauses

    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    text = _zip_read(bundle.zip_bytes, f"{bundle.slug}/tools/_builtin/query_tools.yaml")
    doc = yaml.safe_load(text)
    tools = {t.name: t for t in parse_query_tools(doc)}
    tool = tools["page_facts"]
    bare = render_query(tool, {})
    reinjected = bare.replace("WHERE {", canonical_from_clauses([LIB_GRAPH]) + "WHERE {", 1)

    facts_client = _pyoxi_client({LIB_GRAPH: _LIB_TTL}, promote={})
    raw = await facts_client.sparql_select(reinjected)
    bindings = raw["results"]["bindings"]
    assert bindings
    shaped = [_shape_row(tool.item, row) for row in bindings]
    assert any(
        row.get("property_iri") is not None and row.get("value") is not None for row in shaped
    ), shaped


async def test_card_json_has_the_contract_shape(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    spec = json.loads(_zip_read(bundle.zip_bytes, f"{bundle.slug}/cards/card-1.json"))
    assert spec["id"] == "card-1"
    assert spec["subject"] == {"kind": "individual", "iri": CHECKOUT_1}
    assert spec["tool"] == "subject_facts"
    assert spec["params"] == {"iri": CHECKOUT_1}
    assert spec["view"]["lang"] == "table"
    assert isinstance(spec["materials"], list)
    assert spec["shareable"] is False  # 出どころ・ライセンス不明(既定)


async def test_agent_md_has_seven_sections_ja_and_en(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    for lang, needle in (("ja", "答えを作らず"), ("en", "Do not make up answers")):
        bundle = await build_export_bundle(
            _client(),
            tmp_path,
            subject={"kind": "individual", "iri": CHECKOUT_1},
            cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
            share="full",
            lang=lang,
        )
        doc = _zip_read(bundle.zip_bytes, f"{bundle.slug}/AGENT.md")
        assert needle in doc
        assert "subject_facts" in doc


async def test_mcp_json_shape(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
    )
    doc = json.loads(_zip_read(bundle.zip_bytes, f"{bundle.slug}/mcp.json"))
    slug = bundle.slug
    assert doc == {"mcpServers": {slug: {"command": "asterism-agent", "args": ["serve", "."]}}}


# ---------------------------------------------------------------------------
# 契約メモ contract_pr_f4.md §1-6 — 足したカード（appdata cards の
# ``set_measure``）は card_id ごとに束の tools/_builtin/ に個別に凍結され、
# cards/ にも載る。``set_measure`` はまだ他担当（tool）の並行作業なので、
# ``subject_tools.run_subject_tool`` を差し替えて結果を固定する（このファイル
# が書く凍結ロジックそのものの検証 — 実際の SPARQL 組み立ての正しさは
# tool 側の test_set_measure.py の担当）。
# ---------------------------------------------------------------------------


async def test_added_card_is_frozen_into_its_own_builtin_tool_and_card_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(tmp_path)

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        assert tool == "set_measure"
        # ``_measure_quantity`` と同じ形: raw SPARQL は ``?value`` を直接
        # 射影する（quantity は数少ない「item のロールキーと生クエリの変数
        # 名が一致する」shape — :data:`agent_bundle._MEASURE_RAW_VAR` 参照）。
        return {
            "tool": tool,
            "count": 1,
            "items": [{"value": 3}],
            "truncated": False,
            "sparql": "SELECT (COUNT(?s) AS ?value) WHERE { ?s a <https://ex/lib#R> }",
            "output_kind": "quantity",
            "item": {"value": {"var": "value", "role": "value", "number": True}},
            "materials": [],
            "shareable": False,
            "shareable_reasons": ["no_materials"],
        }

    monkeypatch.setattr(
        "asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool
    )
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-added-1", "tool": "set_measure", "params": {"agg": "count"}}],
        share="full",
        lang="ja",
    )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    assert f"{slug}/cards/card-added-1.json" in names
    assert f"{slug}/tools/_builtin/query_tools.yaml" in names

    import yaml as yaml_mod

    doc = yaml_mod.safe_load(_zip_read(bundle.zip_bytes, f"{slug}/tools/_builtin/query_tools.yaml"))
    tool_names = [t["name"] for t in doc["tools"]]
    assert tool_names == ["card_added_1"]
    frozen = doc["tools"][0]
    assert frozen["output_kind"] == "quantity"
    assert "FROM <" not in frozen["query"]
    assert set(frozen["result"]["item"]) == {"value"}

    spec = json.loads(_zip_read(bundle.zip_bytes, f"{slug}/cards/card-added-1.json"))
    assert spec["tool"] == "set_measure"
    assert spec["params"] == {"agg": "count"}

    # 見つけた磨き #4: AGENT.md の一覧には呼び出し時の共通名
    # "set_measure" ではなく、query_tools.yaml に実際に書かれた凍結名が
    # 出る（複数枚あっても "set_measure" 1 行に潰れて実体が見えない、
    # という実機所見の再現・回帰防止）。
    agent_md = _zip_read(bundle.zip_bytes, f"{slug}/AGENT.md")
    assert "card_added_1" in agent_md


async def test_two_added_cards_freeze_into_two_distinct_builtin_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(tmp_path)

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        return {
            "tool": tool,
            "count": 1,
            "items": [{"value": 1}],
            "truncated": False,
            "sparql": "SELECT (COUNT(?s) AS ?value) WHERE { ?s a <https://ex/lib#R> }",
            "output_kind": "quantity",
            "item": {"value": {"var": "value", "role": "value", "number": True}},
            "materials": [],
            "shareable": False,
            "shareable_reasons": ["no_materials"],
        }

    monkeypatch.setattr(
        "asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool
    )
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[
            {"card_id": "card-a", "tool": "set_measure", "params": {"agg": "count"}},
            {"card_id": "card-b", "tool": "set_measure", "params": {"agg": "sum"}},
        ],
        share="full",
        lang="ja",
    )
    import yaml as yaml_mod

    doc = yaml_mod.safe_load(
        _zip_read(bundle.zip_bytes, f"{bundle.slug}/tools/_builtin/query_tools.yaml")
    )
    assert {t["name"] for t in doc["tools"]} == {"card_a", "card_b"}


async def test_added_card_series_shape_renames_x_to_the_raw_query_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """固定テスト: ``asterism.subject_tools._measure_series`` は item 上
    ``x`` の値を実際には ``BIND(... AS ?xn)`` で ``?xn`` に射影する（``y`` は
    たまたま ``?y`` のまま一致するが、``x`` は一致しない）。対応表
    （:data:`agent_bundle._MEASURE_RAW_VAR`）で var を差し替えないと、束の
    ``page`` 相当ツールが SELECT に無い ``?x`` を指す壊れた宣言になり
    （またはロール `x` が丸ごと消えて ``series`` の role 必須チェックに
    落ちて凍結自体がスキップされ）、"推移" カードが AI に渡せない。"""
    _write_registry(tmp_path)

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        return {
            "tool": tool,
            "count": 1,
            "items": [{"x": 2020, "y": 3.5}],
            "truncated": False,
            "sparql": (
                "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
                "SELECT ?xn (AVG(?yn) AS ?y) WHERE { "
                "?s <https://ex/library#year> ?xr . BIND(xsd:double(str(?xr)) AS ?xn) "
                "?s <https://ex/library#count> ?yr . BIND(xsd:double(str(?yr)) AS ?yn) "
                "} GROUP BY ?xn"
            ),
            "output_kind": "series",
            "item": {
                "x": {"var": "x", "role": "x", "number": True},
                "y": {"var": "y", "role": "y", "number": True},
            },
            "materials": [],
            "shareable": False,
            "shareable_reasons": ["no_materials"],
        }

    monkeypatch.setattr(
        "asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool
    )
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-series", "tool": "set_measure", "params": {"shape": "series"}}],
        share="full",
        lang="ja",
    )
    import yaml as yaml_mod

    from asterism.query_tools import lint_query_tool, parse_query_tools

    doc = yaml_mod.safe_load(
        _zip_read(bundle.zip_bytes, f"{bundle.slug}/tools/_builtin/query_tools.yaml")
    )
    frozen = next(t for t in doc["tools"] if t["name"] == "card_series")
    assert frozen["result"]["item"]["x"]["var"] == "xn"
    assert frozen["result"]["item"]["y"]["var"] == "y"
    tools = parse_query_tools(doc)
    lint = lint_query_tool(tools[0])
    assert not lint.errors, lint.errors


async def test_added_card_with_no_sparql_is_skipped_from_tools_but_keeps_card_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """安全側: ``sparql``/``item`` が無ければツールとしては凍結しない（壊れた
    ツールを束に入れるより省く — :func:`_builtin_tool_raw` と同じ規律）。ただ
    し ``cards/`` の JSON は既存のカード書き出しがそのまま担うので残る。"""
    _write_registry(tmp_path)

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        return {
            "tool": tool,
            "count": 0,
            "items": [],
            "truncated": False,
            "sparql": None,
            "output_kind": "quantity",
            "item": {},
            "materials": [],
            "shareable": False,
            "shareable_reasons": ["no_materials"],
        }

    monkeypatch.setattr(
        "asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool
    )
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-broken", "tool": "set_measure", "params": {}}],
        share="full",
        lang="ja",
    )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    assert f"{slug}/cards/card-broken.json" in names
    assert f"{slug}/tools/" in names  # 空でもディレクトリ印は残る
    assert f"{slug}/tools/_builtin/query_tools.yaml" not in names


# ---------------------------------------------------------------------------
# share="shareable" — own drops out; all-own raises 409-shaped error.
# ---------------------------------------------------------------------------


OWN_DATASET = "own-shelf"

# subject_facts/subject_sources/set_* の materials は「その IRI/その SetSpec が
# 触れたグラフ」に対する事実であって、カードごとに変わるものではない（同じ
# subject を見る 2 枚のカードは同じ材料表になる — PR C の設計どおり）。
# 「配れるカードだけ残り、配れないカードと own グラフだけが消える」という
# フィルタそのもの（このファイルが書いたロジック）を実データの偶然に頼らず
# 固定するため、ここは ``subject_tools.run_subject_tool`` をカードごとに
# 別の materials/shareable を返すよう差し替える（そのカード自身の SPARQL 実行
# は D1-export の担当外・既存テストで別に固定済み）。


async def test_share_shareable_drops_own_dependent_card_keeps_open_only_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(tmp_path, LIB_DATASET, "Library")

    def _result(tool: str) -> dict[str, Any]:
        if tool == "open_tool":
            return {
                "tool": tool,
                "count": 1,
                "items": [{"label": "x"}],
                "truncated": False,
                "sparql": None,
                "output_kind": "facts",
                "item": {"label": {"var": "label"}},
                "materials": [
                    {
                        "kind": "open",
                        "dataset_id": LIB_DATASET,
                        "dataset_label": "Library",
                        "snapshot": "v1",
                        "license": "CC-BY-4.0",
                        "redistributable": True,
                        "count": 1,
                    }
                ],
                "shareable": True,
                "shareable_reasons": [],
            }
        return {
            "tool": tool,
            "count": 1,
            "items": [{"label": "y"}],
            "truncated": False,
            "sparql": None,
            "output_kind": "facts",
            "item": {"label": {"var": "label"}},
            "materials": [
                {
                    "kind": "own",
                    "dataset_id": OWN_DATASET,
                    "dataset_label": "My Shelf",
                    "snapshot": "v1",
                    "license": None,
                    "redistributable": None,
                    "count": 1,
                }
            ],
            "shareable": False,
            "shareable_reasons": ["own_data"],
        }

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        return _result(tool)

    monkeypatch.setattr(
        "asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool
    )
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[
            {"card_id": "card-open", "tool": "open_tool", "params": {}},
            {"card_id": "card-own", "tool": "own_tool", "params": {}},
        ],
        share="shareable",
        lang="ja",
    )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    assert f"{slug}/cards/card-open.json" in names
    assert f"{slug}/cards/card-own.json" not in names
    assert f"{slug}/tools/{OWN_DATASET}/meta.json" not in names
    materials_doc = json.loads(_zip_read(bundle.zip_bytes, f"{slug}/materials.json"))
    assert materials_doc["cards"] == ["card-open"]
    assert materials_doc["shareable"] is True
    assert all(m["dataset_id"] != OWN_DATASET for m in materials_doc["materials"])


async def test_share_shareable_all_own_raises_not_shareable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全カードが own しか材料を持たないとき: own を除いた"あと"で判定する
    （下のテスト群）ので own を除くと材料が 0 件になり、``no_materials`` で
    409 になる（own を除いても残る材料があるならそちらを見て配れる／配れ
    ないを決める — own であること自体は理由に出ない、既に除いてあるので）。"""
    _write_registry(tmp_path)
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "own")
    with pytest.raises(NotShareableError) as excinfo:
        await build_export_bundle(
            _client(),
            tmp_path,
            subject={"kind": "individual", "iri": CHECKOUT_1},
            cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
            share="shareable",
            lang="ja",
        )
    assert "no_materials" in excinfo.value.reasons


async def test_share_shareable_own_plus_open_keeps_card_without_own(
    tmp_path: Path,
) -> None:
    """契約メモ §3: own の材料に依存するカードを外すのではなく、**own の
    材料だけを外す** — 残りが全部 open＋再配布可なら、そのカード自体は束に
    残る（own のデータセットは materials／facts／tools から消える）。"""
    _write_registry(tmp_path, LIB_DATASET, "Library")

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        return {
            "tool": tool,
            "count": 1,
            "items": [{"label": "x"}],
            "truncated": False,
            "sparql": None,
            "output_kind": "facts",
            "item": {"label": {"var": "label"}},
            "materials": [
                {
                    "kind": "own",
                    "dataset_id": OWN_DATASET,
                    "dataset_label": "My Shelf",
                    "snapshot": "v1",
                    "license": None,
                    "redistributable": None,
                    "count": 1,
                },
                {
                    "kind": "open",
                    "dataset_id": LIB_DATASET,
                    "dataset_label": "Library",
                    "snapshot": "v1",
                    "license": "CC-BY-4.0",
                    "redistributable": True,
                    "count": 1,
                },
            ],
            "shareable": False,
            "shareable_reasons": ["own_data"],
        }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool)
        bundle = await build_export_bundle(
            _client(),
            tmp_path,
            subject={"kind": "individual", "iri": CHECKOUT_1},
            cards=[{"card_id": "card-mixed", "tool": "mixed_tool", "params": {}}],
            share="shareable",
            lang="ja",
        )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    assert f"{slug}/cards/card-mixed.json" in names
    assert f"{slug}/tools/{OWN_DATASET}/meta.json" not in names
    materials_doc = json.loads(_zip_read(bundle.zip_bytes, f"{slug}/materials.json"))
    assert materials_doc["cards"] == ["card-mixed"]
    assert materials_doc["shareable"] is True
    assert all(m["dataset_id"] != OWN_DATASET for m in materials_doc["materials"])
    card_spec = json.loads(_zip_read(bundle.zip_bytes, f"{slug}/cards/card-mixed.json"))
    assert all(m["dataset_id"] != OWN_DATASET for m in card_spec["materials"])


async def test_share_shareable_open_with_unknown_license_still_not_shareable(
    tmp_path: Path,
) -> None:
    """own が無くても、残った open の材料のライセンスが不明なら配れない
    （own の除去は "own である" 以外の配れない理由を消さない）。"""

    async def fake_run_subject_tool(client, registry_root, subject, tool, params):
        return {
            "tool": tool,
            "count": 1,
            "items": [{"label": "x"}],
            "truncated": False,
            "sparql": None,
            "output_kind": "facts",
            "item": {"label": {"var": "label"}},
            "materials": [
                {
                    "kind": "open",
                    "dataset_id": LIB_DATASET,
                    "dataset_label": "Library",
                    "snapshot": "v1",
                    "license": None,
                    "redistributable": None,
                    "count": 1,
                }
            ],
            "shareable": False,
            "shareable_reasons": ["unknown_license"],
        }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("asterism.agent_bundle.subject_tools.run_subject_tool", fake_run_subject_tool)
        with pytest.raises(NotShareableError) as excinfo:
            await build_export_bundle(
                _client(),
                tmp_path,
                subject={"kind": "individual", "iri": CHECKOUT_1},
                cards=[{"card_id": "card-1", "tool": "open_unknown_license", "params": {}}],
                share="shareable",
                lang="ja",
            )
    assert "unknown_license" in excinfo.value.reasons
    assert "own_data" not in excinfo.value.reasons


# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------


async def test_truncated_flag_when_over_max_triples(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-1", "tool": "subject_facts", "params": {"iri": CHECKOUT_1}}],
        share="full",
        lang="ja",
        max_triples=1,
    )
    materials_doc = json.loads(_zip_read(bundle.zip_bytes, f"{bundle.slug}/materials.json"))
    assert materials_doc["truncated"] is True
    facts = _zip_read(bundle.zip_bytes, f"{bundle.slug}/facts/facts.trig")
    assert facts.count(" .") <= 1


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


async def test_tools_directory_present_even_with_no_dataset_materials(
    tmp_path: Path,
) -> None:
    """A page made only of a flow card (subject_flow has no ``materials`` and
    is not a ``_builtin``-eligible tool) still yields a ``tools/`` entry, so
    ``asterism-agent serve``'s ``check_bundle`` (D1-serve) does not refuse a
    legitimately-built bundle for lacking the directory."""
    bundle = await build_export_bundle(
        _client(),
        tmp_path,
        subject={"kind": "individual", "iri": CHECKOUT_1},
        cards=[{"card_id": "card-flow", "tool": "subject_flow", "params": {}}],
        share="full",
        lang="ja",
    )
    names = _zip_names(bundle.zip_bytes)
    slug = bundle.slug
    assert f"{slug}/tools/" in names
    assert f"{slug}/cards/card-flow.json" in names


async def test_unknown_share_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await build_export_bundle(
            _client(),
            tmp_path,
            subject={"kind": "individual", "iri": CHECKOUT_1},
            cards=[],
            share="bogus",
            lang="ja",
        )
