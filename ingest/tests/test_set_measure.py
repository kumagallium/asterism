"""Tests for asterism.subject_tools.set_measure / linking_kinds (契約メモ
contract_pr_f4.md §1-2/§1-3/§1-4).

Backed by a real ``pyoxigraph.Store`` — same contract as ``_pyoxi_client`` in
test_subject_tools.py / test_class_schema.py. ``class_schema`` is the REAL
module (not monkeypatched): the fixture declares its own tiny ontology named
graph (mirrors test_class_schema.py's "④ ontology fallback" branch) so
``kind`` (quantity/category/link) comes from real data, exactly like a
declared dataset's class would. Fixture domain is a generic field-observation
log — never a single real-world domain's vocabulary (§0).
"""

from __future__ import annotations

import pytest

from asterism.measure_spec import MeasureSpecError
from asterism.subject_tools import (
    SubjectToolError,
    linking_kinds,
    run_subject_tool,
    set_breakdown,
    set_measure,
)
from asterism.subjects import normalize_set_spec, validate_subject_key
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    ONTOLOGY_GRAPH_BASE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str]):
    store = pyoxigraph.Store()
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
        if giri.startswith(CANONICAL_GRAPH_BASE):
            store.add(
                pyoxigraph.Quad(
                    pyoxigraph.NamedNode(giri),
                    pyoxigraph.NamedNode(STATUS_PREDICATE),
                    pyoxigraph.Literal(STATUS_PROMOTED),
                    pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
                )
            )

    class _C:
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

    return _C()


# ---------------------------------------------------------------------------
# Fixture data — a generic field-observation log (fictional, not a real
# domain), with an ontology named graph so the REAL class_schema module (no
# registry) resolves quantity/category/link kinds from live data.
# ---------------------------------------------------------------------------

EX = "https://ex/obs#"
OBS_CLASS = EX + "Observation"
DAY_PRED = EX + "day"
VALUE_PRED = EX + "value"
SITE_PRED = EX + "site"
STATION_PRED = EX + "station"

OBS_DATASET = "obs-log"
OBS_GRAPH = canonical_graph_iri(OBS_DATASET) + "/v1"

STATION_A = "https://ex/obs/resource/station-a"
STATION_B = "https://ex/obs/resource/station-b"
ACTIVITY_1 = "https://ex/obs/resource/activity-1"
OBS = [f"https://ex/obs/resource/obs-{n}" for n in range(1, 7)]

# 2 段 via clause（契約メモ contract_pr_f14.md §1.2・ADR O59）の実クエリテスト
# 用: station-a だけが region-a に属する（station-b は属さない）。
REGION_PRED = EX + "region"
REGION_A = "https://ex/obs/resource/region-a"

_ONTOLOGY_TTL = f"""
@prefix ex: <{EX}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:Observation a rdfs:Class ; rdfs:label "観測記録" .
ex:day a rdf:Property ; rdfs:domain ex:Observation ; rdfs:label "日" .
ex:value a rdf:Property ; rdfs:domain ex:Observation ; rdfs:label "値" .
ex:site a rdf:Property ; rdfs:domain ex:Observation ; rdfs:label "場所" .
ex:station a rdf:Property ; rdfs:domain ex:Observation ; rdfs:label "観測局" .
"""

# obs-1/2: day 1 (value 10, 20) — averages to 15 for the series test.
# obs-3: day 2 (value 30).
# obs-4/5: day 3 (value 40, 50) — averages to 45.
# obs-6: a SEPARATE station (station-b) — excluded once a where clause pins
# the link to station-a (link-clause test).
#
# ``ex:station``'s rdfs:label lives HERE (canonical), not in the ontology
# graph above: ``linking_kinds``' property-label lookup scopes to
# ``readable_graph_iris`` (canonical ∪ ontology), and the empty-group
# ``GRAPH ?g {}`` enumeration ``asterism.substrate.ontology_graphs`` uses
# does not bind ``?g`` against this pyoxigraph store in practice (実機所見 —
# same reason test_substrate.py exercises that enumeration via an
# ``_rdflib_client`` instead of a pyoxigraph one) — a canonical-graph label
# is the one this test's ``_pyoxi_client`` can actually exercise end-to-end.
_OBS_TTL = f"""
@prefix ex: <{EX}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix prov: <http://www.w3.org/ns/prov#> .

ex:station rdfs:label "観測局" .

<{STATION_A}> ex:region <{REGION_A}> .

<{OBS[0]}> a <{OBS_CLASS}> ; rdfs:label "Obs One" ;
    ex:day "1"^^xsd:integer ; ex:value "10"^^xsd:double ; ex:site "north" ;
    ex:station <{STATION_A}> .
<{OBS[1]}> a <{OBS_CLASS}> ; rdfs:label "Obs Two" ;
    ex:day "1"^^xsd:integer ; ex:value "20"^^xsd:double ; ex:site "north" ;
    ex:station <{STATION_A}> .
<{OBS[2]}> a <{OBS_CLASS}> ; rdfs:label "Obs Three" ;
    ex:day "2"^^xsd:integer ; ex:value "30"^^xsd:double ; ex:site "south" ;
    ex:station <{STATION_A}> .
<{OBS[3]}> a <{OBS_CLASS}> ; rdfs:label "Obs Four" ;
    ex:day "3"^^xsd:integer ; ex:value "40"^^xsd:double ; ex:site "south" ;
    ex:station <{STATION_A}> .
<{OBS[4]}> a <{OBS_CLASS}> ; rdfs:label "Obs Five" ;
    ex:day "3"^^xsd:integer ; ex:value "50"^^xsd:double ; ex:site "north" ;
    ex:station <{STATION_A}> .
<{OBS[5]}> a <{OBS_CLASS}> ; rdfs:label "Obs Six" ;
    ex:day "9"^^xsd:integer ; ex:value "999"^^xsd:double ; ex:site "other" ;
    ex:station <{STATION_B}> .

<{ACTIVITY_1}> a prov:Activity ; prov:used <{STATION_A}> .
"""


def _client() -> object:
    return _pyoxi_client({ONTOLOGY_GRAPH_BASE + OBS_DATASET: _ONTOLOGY_TTL, OBS_GRAPH: _OBS_TTL})


def _spec(**overrides) -> dict:
    spec = {"class": OBS_CLASS, "where": []}
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# series
# ---------------------------------------------------------------------------


async def test_set_measure_series_groups_and_averages_by_x() -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "series", "x": DAY_PRED, "y": VALUE_PRED}
    )
    assert out["output_kind"] == "series"
    by_x = {i["x"]: i["y"] for i in out["items"] if i["x"] != 9.0}  # exclude station-b's obs-6
    assert by_x == {1.0: 15.0, 2.0: 30.0, 3.0: 45.0}
    assert set(out["item"]) == {"x", "y"}
    assert out["item"]["x"]["role"] == "x"
    assert out["item"]["y"]["role"] == "y"


async def test_set_measure_series_is_ordered_by_x_ascending() -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "series", "x": DAY_PRED, "y": VALUE_PRED}
    )
    xs = [i["x"] for i in out["items"]]
    assert xs == sorted(xs)


# ---------------------------------------------------------------------------
# ranked
# ---------------------------------------------------------------------------


async def test_set_measure_ranked_orders_desc_by_default() -> None:
    out = await set_measure(_client(), _spec(), params={"shape": "ranked", "item": VALUE_PRED})
    assert out["output_kind"] == "ranked"
    values = [i["value"] for i in out["items"]]
    assert values == sorted(values, reverse=True)
    assert out["items"][0]["subject_iri"] == OBS[5]  # value 999, largest
    assert out["items"][0]["label"] == "Obs Six"


async def test_set_measure_ranked_orders_asc_when_requested() -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "ranked", "item": VALUE_PRED, "order": "asc"}
    )
    assert out["items"][0]["subject_iri"] == OBS[0]  # value 10, smallest


# ---------------------------------------------------------------------------
# breakdown — delegates to set_breakdown verbatim.
# ---------------------------------------------------------------------------


async def test_set_measure_breakdown_matches_set_breakdown_directly() -> None:
    spec = _spec()
    via_measure = await set_measure(
        _client(), spec, params={"shape": "breakdown", "category": SITE_PRED}
    )
    via_direct = await set_breakdown(_client(), spec, SITE_PRED)
    assert via_measure["items"] == via_direct["items"]
    assert via_measure["output_kind"] == "breakdown" == via_direct["output_kind"]


# ---------------------------------------------------------------------------
# pairs
# ---------------------------------------------------------------------------


async def test_set_measure_pairs_returns_distinct_xy_tuples() -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "pairs", "x": DAY_PRED, "y": VALUE_PRED}
    )
    assert out["output_kind"] == "pairs"
    pairs = {(i["x"], i["y"]) for i in out["items"]}
    assert (1.0, 10.0) in pairs
    assert (1.0, 20.0) in pairs
    assert (3.0, 50.0) in pairs
    assert len(out["items"]) == 6  # one row per observation, no aggregation


# ---------------------------------------------------------------------------
# quantity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agg", "expected"),
    [("avg", pytest.approx(191.5, rel=1e-6)), ("max", 999.0), ("min", 10.0), ("sum", 1149.0)],
)
async def test_set_measure_quantity_aggregates(agg: str, expected: float) -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "quantity", "item": VALUE_PRED, "agg": agg}
    )
    assert out["output_kind"] == "quantity"
    assert out["items"] == [{"value": expected}]


async def test_set_measure_quantity_count_counts_matching_records() -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "quantity", "item": VALUE_PRED, "agg": "count"}
    )
    assert out["items"] == [{"value": 6.0}]


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------


async def test_set_measure_facts_returns_a_table_of_the_chosen_columns() -> None:
    out = await set_measure(
        _client(), _spec(), params={"shape": "facts", "items": [DAY_PRED, SITE_PRED]}
    )
    assert out["output_kind"] == "facts"
    assert len(out["items"]) == 6
    rows = {(i["value0"], i["value1"]) for i in out["items"]}
    assert (1.0, "north") in rows
    assert (9.0, "other") in rows
    assert out["item"]["value0"]["role"] == "value"  # day is quantity
    assert out["item"]["value1"]["role"] == "category"  # site is category
    assert out["item"]["value0"]["label"] == "日"
    assert out["item"]["value1"]["label"] == "場所"


# ---------------------------------------------------------------------------
# link where-clause (契約メモ §1-3) — narrows to records pointing at 1 IRI.
# ---------------------------------------------------------------------------


async def test_set_measure_link_where_clause_narrows_to_the_pointed_at_record() -> None:
    spec = normalize_set_spec(
        {"class": OBS_CLASS, "where": [{"property": STATION_PRED, "iri": STATION_A}]}
    )
    out = await set_measure(
        _client(), spec, params={"shape": "quantity", "item": VALUE_PRED, "agg": "count"}
    )
    assert out["items"] == [{"value": 5.0}]  # excludes obs-6 (station-b)


async def test_set_measure_via_where_clause_narrows_two_hops() -> None:
    """2 段 via clause（契約メモ §1.2）: ``?s <station> ?wl . ?wl <region>
    <region-a>`` — station-a は region-a に属するが station-b は属さないので
    station-b の obs-6 だけ除外される（実 pyoxigraph クエリで確かめる）。"""
    spec = normalize_set_spec(
        {
            "class": OBS_CLASS,
            "where": [
                {"property": STATION_PRED, "via": {"property": REGION_PRED, "iri": REGION_A}}
            ],
        }
    )
    out = await set_measure(
        _client(), spec, params={"shape": "quantity", "item": VALUE_PRED, "agg": "count"}
    )
    assert out["items"] == [{"value": 5.0}]  # excludes obs-6 (station-b, no region-a link)


async def test_set_measure_link_where_clause_rejects_mixed_op_and_iri() -> None:
    with pytest.raises(Exception):  # noqa: B017 — SetSpecError, a ValueError subclass
        normalize_set_spec(
            {
                "class": OBS_CLASS,
                "where": [{"property": STATION_PRED, "iri": STATION_A, "op": "eq", "value": 1}],
            }
        )


# ---------------------------------------------------------------------------
# validity table enforcement (ADR O44 — "最後の砦").
# ---------------------------------------------------------------------------


async def test_set_measure_rejects_a_combination_outside_the_validity_table() -> None:
    """内訳（breakdown）の category に quantity 列を渡す — UI をバイパスした
    呼び出しは 400 相当の SubjectToolError（ValueError の連鎖）になる。"""
    with pytest.raises(SubjectToolError):
        await set_measure(_client(), _spec(), params={"shape": "breakdown", "category": VALUE_PRED})


async def test_set_measure_error_chains_from_measure_spec_error() -> None:
    with pytest.raises(SubjectToolError) as excinfo:
        await set_measure(_client(), _spec(), params={"shape": "not-a-shape"})
    assert isinstance(excinfo.value.__cause__, MeasureSpecError)


# ---------------------------------------------------------------------------
# 「年」のような主語テンプレートの列（kind=identifier・datatype=数値）を
# series の x に使う — 実機所見（見本「世界の国」）: is_coordinate を通る
# identifier は x として通ることを、実 pyoxigraph + registry の
# class_schema 経由で確かめる（measure_spec の単体テストとは別に、
# `set_measure` の実行経路まで一気通貫で確認する）。
# ---------------------------------------------------------------------------

KEY_EX = "https://ex/keyobs#"
KEY_CLASS = KEY_EX + "Record"
KEY_YEAR_PRED = KEY_EX + "year"
KEY_VALUE_PRED = KEY_EX + "value"
KEY_DATASET = "keyobs-dddddddd"
KEY_GRAPH = canonical_graph_iri(KEY_DATASET) + "/v1"

_KEY_MAPPING_YAML = f"""
version: 1
prefixes:
  ex: "{KEY_EX}"
  exr: "https://ex/keyobs/resource/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: record
    source: records.csv
    subject:
      template: "exr:record/{{year}}"
      classes: [ex:Record]
    properties:
      - predicate: ex:year
        column: year
        datatype: xsd:integer
      - predicate: ex:value
        column: value
        datatype: xsd:double
"""

_KEY_TTL = f"""
@prefix ex: <{KEY_EX}> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://ex/keyobs/resource/record/2020> a <{KEY_CLASS}> ;
    ex:year "2020"^^xsd:integer ; ex:value "10"^^xsd:double .
<https://ex/keyobs/resource/record/2020b> a <{KEY_CLASS}> ;
    ex:year "2020"^^xsd:integer ; ex:value "20"^^xsd:double .
<https://ex/keyobs/resource/record/2021> a <{KEY_CLASS}> ;
    ex:year "2021"^^xsd:integer ; ex:value "30"^^xsd:double .
"""


def _key_registry_root(tmp_path) -> object:
    import json

    root = tmp_path / "registry"
    dest = root / KEY_DATASET
    dest.mkdir(parents=True)
    meta = {
        "id": KEY_DATASET,
        "promoted": True,
        "promoted_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "mapping.yaml").write_text(_KEY_MAPPING_YAML, encoding="utf-8")
    return root


async def test_set_measure_series_accepts_numeric_identifier_x_end_to_end(tmp_path) -> None:
    """「年」が class_schema で kind=identifier・datatype=数値integer と判定
    されても、series の x として通り実データを返す（実機所見の再現）。"""
    registry_root = _key_registry_root(tmp_path)
    client = _pyoxi_client({KEY_GRAPH: _KEY_TTL})
    out = await set_measure(
        client,
        {"class": KEY_CLASS, "where": []},
        params={"shape": "series", "x": KEY_YEAR_PRED, "y": KEY_VALUE_PRED},
        registry_root=registry_root,
    )
    assert out["output_kind"] == "series"
    by_x = {i["x"]: i["y"] for i in out["items"]}
    assert by_x == {2020.0: 15.0, 2021.0: 30.0}


# ---------------------------------------------------------------------------
# linking_kinds — ADR O46.
# ---------------------------------------------------------------------------


async def test_linking_kinds_finds_the_class_and_property_pointing_at_the_record() -> None:
    out = await linking_kinds(_client(), STATION_A)
    by_class = {row["class_iri"]: row for row in out}
    assert OBS_CLASS in by_class
    row = by_class[OBS_CLASS]
    assert row["property"] == STATION_PRED
    assert row["count"] == 5
    assert row["class_label"] == "観測記録"
    assert row["property_label"] == "観測局"


async def test_linking_kinds_excludes_provenance_classes() -> None:
    out = await linking_kinds(_client(), STATION_A)
    classes = {row["class_iri"] for row in out}
    assert "http://www.w3.org/ns/prov#Activity" not in classes
    assert len(out) == 1  # only the Observation/station pair, not the prov:Activity one


async def test_linking_kinds_empty_for_a_record_nothing_points_at() -> None:
    out = await linking_kinds(_client(), "https://ex/obs/resource/does-not-exist")
    assert out == []


# ---------------------------------------------------------------------------
# run_subject_tool dispatch — set_measure carries its own class/where in
# ``params`` (契約メモ §1-4), so it runs from an INDIVIDUAL subject too (the
# ui builds the link where-clause client-side from ``linking_kinds`` and still
# sends ``subject={"kind": "individual", iri}"`` — see the docstring on
# ``run_subject_tool``).
# ---------------------------------------------------------------------------


async def test_run_subject_tool_set_measure_works_from_an_individual_subject() -> None:
    subject = validate_subject_key({"kind": "individual", "iri": STATION_A})
    out = await run_subject_tool(
        _client(),
        None,
        subject,
        "set_measure",
        {
            "class": OBS_CLASS,
            "where": [{"property": STATION_PRED, "iri": STATION_A}],
            "shape": "quantity",
            "item": VALUE_PRED,
            "agg": "count",
        },
    )
    assert out["items"] == [{"value": 5.0}]  # excludes obs-6 (station-b)


async def test_run_subject_tool_set_measure_works_from_a_set_subject() -> None:
    subject = validate_subject_key({"kind": "set", "spec": {"class": OBS_CLASS, "where": []}})
    out = await run_subject_tool(
        _client(),
        None,
        subject,
        "set_measure",
        {
            "class": OBS_CLASS,
            "where": [],
            "shape": "breakdown",
            "category": SITE_PRED,
        },
    )
    assert out["output_kind"] == "breakdown"


async def test_run_subject_tool_set_measure_bad_class_is_subject_tool_error() -> None:
    subject = validate_subject_key({"kind": "individual", "iri": STATION_A})
    with pytest.raises(SubjectToolError):
        await run_subject_tool(
            _client(),
            None,
            subject,
            "set_measure",
            {"where": [], "shape": "quantity", "item": VALUE_PRED, "agg": "count"},
        )
