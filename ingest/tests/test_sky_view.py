"""Tests for ``asterism.sky_view.sky_view``（契約 pr2-contract.md §A・§C）。

実物の ``pyoxigraph.Store`` と、registry の実ファイル（meta.json・query_tools.yaml・
wired.json・crosswalk.yaml）で動かす。語は ``mint_term``、線は ``assert_alignment`` で
実際に書く。題材は架空の分野（気象観測所）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from asterism import crosswalk_runtime, shared_vocab, substrate
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import (
    RuntimeConcept,
    RuntimeCrosswalkConfig,
    RuntimeParticipant,
    assert_alignment,
    save_config,
)
from asterism.shared_vocab import CQSpec
from asterism.sky_view import questions_of_term, sky_view

pyoxigraph = pytest.importorskip("pyoxigraph")

WX = "https://ex/wx#"
WY = "https://ex/wy#"
R = "https://ex/wx/resource/"
PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
STATION = WX + "Station"
OBSERVATION = WX + "Observation"
SITE = WY + "Site"
PLACE_STD = "https://schema.org/Place"  # 標準の名前空間（そのまま使う種類）
AT_LINE_1 = "2026-10-12T00:00:00+00:00"
AT_LINE_2 = "2026-10-13T00:00:00+00:00"
AT_TERM = "2026-10-11T00:00:00+00:00"
HUB_GRAPH = substrate.canonical_graph_iri("crosswalk")

PFX = (
    f"@prefix wx: <{WX}> .\n@prefix wy: <{WY}> .\n@prefix r: <{R}> .\n"
    f"@prefix prov: <{PROV}> .\n@prefix xw: <{XW}> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
    "@prefix schema: <https://schema.org/> .\n"
)


class _Client:
    """OxigraphClient の代わり（実 pyoxigraph.Store）。投げた SELECT を控える。"""

    def __init__(self) -> None:
        self.store = pyoxigraph.Store()
        self.queries: list[str] = []

    def load(self, graph: str, ttl: str) -> None:
        self.store.load(
            (PFX + ttl).encode("utf-8"),
            mime_type="text/turtle",
            to_graph=pyoxigraph.NamedNode(graph),
        )

    async def sparql_update(self, update: str) -> None:
        self.store.update(update)

    async def sparql_select(self, query: str) -> dict:
        self.queries.append(query)
        result = self.store.query(query, use_default_graph_as_union=True)
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
                    row[name] = cell
            bindings.append(row)
        return {"results": {"bindings": bindings}}


def _meta(root: Path, did: str, **fields: Any) -> None:
    d = root / did
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps({"id": did, **fields}, ensure_ascii=False), encoding="utf-8"
    )


async def _publish(client: _Client, did: str, ttl: str, *, version: int = 1) -> None:
    """版 graph にデータを入れて promoted にする（live を指す）。"""
    data = substrate.versioned_graph_iri(did, version)
    client.load(data, ttl)
    await substrate.mark_graph_promoted(client, substrate.canonical_graph_iri(did), live_graph=data)


_STATIONS_A = (
    "r:st-1 a wx:Station ; rdfs:label 'S1' .\nr:st-2 a wx:Station .\nr:st-3 a wx:Station .\n"
    + "".join(
        f"r:ob-{i} a wx:Observation ; wx:atStation r:st-1 ; wx:value {i} .\n" for i in range(5)
    )
    + "r:run-1 a prov:Activity .\n"
)
_ONTOLOGY_A = (
    "wx:Station a rdfs:Class ; rdfs:label 'ステーション'@ja .\n"
    "wx:Observation a rdfs:Class ; rdfs:label '観測'@ja .\n"
    "wx:atStation a rdf:Property ; rdfs:domain wx:Observation ; rdfs:range wx:Station .\n"
    "wx:value a rdf:Property ; rdfs:domain wx:Observation ; rdfs:range xsd:double .\n"
)
_SITES_B = (
    "r:site-1 a wy:Site .\nr:site-2 a wy:Site .\nr:pl-1 a schema:Place .\n"
    "r:run-9 a prov:Activity .\n"
)
_ONTOLOGY_B = (
    "wy:Site a rdfs:Class ; rdfs:label '地点'@ja .\n"
    "wy:near a rdf:Property ; rdfs:domain wy:Site ; rdfs:range wy:Site .\n"
)


async def _world(tmp_path: Path) -> tuple[_Client, Path]:
    """2 データセット・語 1 つ・線 3 本（標準を含む）・ハブ 1 つ。"""
    root = tmp_path / "registry"
    client = _Client()
    _meta(
        root,
        "wx-a",
        name="観測所データ",
        promoted=True,
        promoted_at="2026-10-01T09:00:00+00:00",
        created_at="2026-10-01T08:00:00+00:00",
        status="active",
    )
    _meta(
        root,
        "wy-b",
        name="地点データ",
        promoted=True,
        promoted_at="2026-10-02T09:00:00+00:00",
        created_at="2026-10-02T08:00:00+00:00",
        status="active",
    )
    await _publish(client, "wx-a", _STATIONS_A)
    await _publish(client, "wy-b", _SITES_B)
    client.load(substrate.ontology_graph_iri("wx-a"), _ONTOLOGY_A)
    client.load(substrate.ontology_graph_iri("wy-b"), _ONTOLOGY_B)

    # 語（実際に鋳造する: query_tools.yaml と vocab/shared が書かれる）
    await shared_vocab.mint_term(
        client,
        root,
        slug="place",
        kind="class",
        label="場所",
        label_en="Place",
        cqs=[CQSpec("場所は、どのデータセットに何件あるか", "count")],
        at=AT_TERM,
    )
    place = shared_vocab.term_iri("place")
    await assert_alignment(client, STATION, place, "subClassOf", at=AT_LINE_1)
    await assert_alignment(client, place, SITE, "equivalentClass", at=AT_LINE_1)
    await assert_alignment(client, place, PLACE_STD, "equivalentClass", at=AT_LINE_2)
    await assert_alignment(client, OBSERVATION, WX + "Reading", "equivalentClass", at=AT_LINE_2)
    await shared_vocab.recompute_wired(client, root)

    # ハブ（視点）: registry の項目・設定・hub graph（per-link の記録つき）
    _meta(
        root,
        "crosswalk-bridge",
        name="場所でつなぐ",
        promoted=True,
        status="active",
        is_crosswalk=True,
        crosswalk_perspective_id="composition",
        created_at="2026-10-03T00:00:00+00:00",
    )
    save_config(
        root,
        RuntimeCrosswalkConfig(
            concepts=(
                RuntimeConcept(
                    name="place",
                    class_iri=XW + "Place",
                    link_predicate=XW + "hasPlace",
                    normalizer="identity",
                    participants=(
                        RuntimeParticipant("wx-a", "A", WX + "name", subject_class=STATION),
                        RuntimeParticipant("wy-b", "B", WY + "name", subject_class=SITE),
                    ),
                ),
            )
        ),
    )
    client.load(
        HUB_GRAPH,
        "r:hub-1 a xw:Place .\nr:link-1 a xw:CrosswalkLink ; prov:wasGeneratedBy r:run-1 .\n"
        "r:run-1 a prov:Activity .\n",
    )
    await substrate.mark_graph_promoted(client, HUB_GRAPH)
    return client, root


def _dataset(out: dict, did: str) -> dict:
    return next(d for d in out["datasets"] if d["id"] == did)


# --- (1) 公開 0 件 -------------------------------------------------------------------------


async def test_zero_published_returns_the_empty_shape_without_any_sparql(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    _meta(root, "draft-only", name="下書き", promoted=False)
    _meta(root, "gone", name="取り下げ", promoted=True, status="retracted")
    client = _Client()
    client.load("https://ex/draft/draft-only", "r:x a wx:Station .\n")
    out = await sky_view(client, root)
    assert client.queries == []
    assert set(out) == {
        "datasets",
        "terms",
        "standards",
        "hubs",
        "questions",
        "edges",
        "facts",
        "recent",
        "truncated",
    }
    assert out["datasets"] == [] and out["terms"] == [] and out["recent"] == []
    assert out["facts"] == {
        "triples": 0,
        "datasets": 0,
        "kinds": 0,
        "terms": 0,
        "questions_answerable": 0,
        "questions_total": 0,
        "published_graphs": 0,
    }
    assert out["truncated"] is False
    assert await sky_view(client, None) == out  # registry が無くても同じ空の形
    assert await sky_view(client, tmp_path / "nowhere") == out
    assert client.queries == []


# --- (2) 形どおり ---------------------------------------------------------------------------


async def test_two_datasets_one_term_lines_standard_and_hub(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    out = await sky_view(client, root)

    # データセット: created_at 順・名前は登録の名前
    assert [d["id"] for d in out["datasets"]] == ["wx-a", "wy-b"]
    a, b = out["datasets"]
    assert a["label"] == "観測所データ" and a["created_at"] == "2026-10-01T08:00:00+00:00"
    assert set(a) == {"id", "label", "created_at", "kinds"}

    # 種類: 件数の多い順。PROV の記録は種類でない。線は domain→range の種類どうしだけ
    assert [k["iri"] for k in a["kinds"]] == [OBSERVATION, STATION]
    obs, st = a["kinds"]
    assert set(obs) == {"iri", "label", "count", "links", "standard"}
    assert (obs["count"], st["count"]) == (5, 3)
    assert obs["label"] == "観測" and st["label"] == "ステーション"
    assert obs["links"] == [STATION]  # wx:value → xsd:double は出ない
    assert st["links"] == []
    assert PROV + "Activity" not in {k["iri"] for d in out["datasets"] for k in d["kinds"]}
    # 標準: そのまま使う種類は自分自身・equivalentClass で標準につないだ種類はその先
    assert {k["iri"]: k["standard"] for k in b["kinds"]}[PLACE_STD] == PLACE_STD
    site = next(k for k in b["kinds"] if k["iri"] == SITE)
    assert site["links"] == []  # 自分自身への線は出さない
    assert site["standard"] is None
    assert obs["standard"] is None  # WX:Reading は標準ではない
    assert st["standard"] is None

    # 語: 直接の線だけ。標準 1 つ。答えるデータセット 2・問い 1
    (term,) = out["terms"]
    place = shared_vocab.term_iri("place")
    assert set(term) == {
        "iri",
        "slug",
        "label",
        "label_en",
        "kind",
        "members",
        "standard",
        "answering",
        "questions",
    }
    assert term["iri"] == place and term["slug"] == "place" and term["kind"] == "class"
    assert (term["label"], term["label_en"]) == ("場所", "Place")
    assert term["members"] == [
        {"iri": STATION, "dataset_id": "wx-a", "relation": "subClassOf"},
        {"iri": SITE, "dataset_id": "wy-b", "relation": "equivalentClass"},
    ]
    assert term["standard"] == {"iri": PLACE_STD, "relation": "equivalentClass"}
    assert term["answering"] == 2
    assert term["questions"] == ["cq_place_count"]

    # 標準用語
    (std,) = out["standards"]
    assert set(std) == {"iri", "label", "prefix"}
    assert std["iri"] == PLACE_STD and std["prefix"] == "schema"

    # ハブ
    assert out["hubs"] == [
        {"id": "composition", "label": "場所でつなぐ", "members": [STATION, SITE]}
    ]

    # 問い
    (q,) = out["questions"]
    assert q == {
        "tool_name": "cq_place_count",
        "title": "場所は、どのデータセットに何件あるか",
        "mode": "data",
        "theme_id": None,
        "theme_label": "はじめの問い",
        "answering": 2,
        "knowledge": 0,
        "total_datasets": 2,
        "needs": [place],
        "shape": "場所は、どのデータセットに何件あるか",
    }

    # 事実
    f = out["facts"]
    store_total = next(
        iter(
            client.store.query(
                "SELECT (COUNT(*) AS ?n) WHERE {?s ?p ?o}", use_default_graph_as_union=True
            )
        )
    )["n"].value
    assert f["triples"] == int(store_total)
    assert f["datasets"] == 2
    assert f["kinds"] == 4  # Observation / Station / Site / Place（PROV は除く）
    assert f["terms"] == 1
    assert (f["questions_answerable"], f["questions_total"]) == (1, 1)
    assert f["published_graphs"] == 3  # 2 データセットのデータ graph + ハブ。線の graph は数えない
    assert out["truncated"] is False

    # 最近: 新しい順（決めたこと 2・用語・公開 2）。取れない種別は出さない
    z = lambda at: at.replace("+00:00", "Z")  # noqa: E731 - store は dateTime を Z で返す
    assert [(r["kind"], r["at"]) for r in out["recent"]] == [
        ("decision", z(AT_LINE_2)),
        ("decision", z(AT_LINE_2)),
        ("decision", z(AT_LINE_1)),
        ("decision", z(AT_LINE_1)),
        ("term", z(AT_TERM)),
    ]
    assert all(set(r) == {"at", "kind", "a", "b", "relation"} and r["a"] for r in out["recent"])
    assert all((r["kind"] == "decision") == bool(r["b"] and r["relation"]) for r in out["recent"])


async def test_only_a_same_as_line_to_a_standard_counts_as_matching_it(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    await shared_vocab.mint_term(
        client,
        root,
        slug="region",
        kind="class",
        label="地域",
        cqs=[CQSpec("地域は何件か", "count")],
        at=AT_TERM,
    )
    region = shared_vocab.term_iri("region")
    await assert_alignment(client, region, PLACE_STD, "isPartOf", at=AT_LINE_2)  # 一致ではない
    out = await sky_view(client, root)
    by_slug = {t["slug"]: t for t in out["terms"]}
    assert by_slug["region"]["standard"] is None
    assert by_slug["place"]["standard"] == {"iri": PLACE_STD, "relation": "equivalentClass"}


async def test_same_input_gives_the_same_output(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    assert await sky_view(client, root) == await sky_view(client, root)


# --- (3) hub graph／alignment graph の記録が kinds に混ざらない -----------------------------


async def test_hub_and_alignment_records_are_not_counted_as_kinds(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    out = await sky_view(client, root)
    iris = {k["iri"] for d in out["datasets"] for k in d["kinds"]}
    assert iris == {OBSERVATION, STATION, SITE, PLACE_STD}
    assert XW + "Place" not in iris  # ハブの実体
    assert XW + "CrosswalkLink" not in iris  # ハブの per-link の記録
    assert XW + "Alignment" not in iris  # 線の graph の記録
    # ハブ・線の graph には dataset が無い（登録のデータセットだけが載る）
    assert {d["id"] for d in out["datasets"]} == {"wx-a", "wy-b"}
    assert HUB_GRAPH not in json.dumps(out)
    assert substrate.ALIGNMENT_GRAPH not in json.dumps(out)


async def test_unpublished_and_retracted_datasets_are_not_listed(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    _meta(root, "wz-draft", name="下書き", promoted=False, created_at="2026-10-05T00:00:00Z")
    _meta(root, "wz-old", name="取り下げ", promoted=True, status="retracted")
    _meta(root, "vocab-shared", name="ことば", promoted=True, is_shared_vocab=True)
    client.load("https://ex/draft/wz-draft", "r:d a wx:Station .\n")
    out = await sky_view(client, root)
    assert [d["id"] for d in out["datasets"]] == ["wx-a", "wy-b"]


async def test_published_graphs_skips_graphs_of_retracted_datasets(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    before = (await sky_view(client, root))["facts"]["published_graphs"]
    _meta(root, "wz-old", name="取り下げ", promoted=True, status="retracted")
    await _publish(client, "wz-old", "r:o-1 a wx:Station .\n")  # store だけ公開のまま
    out = await sky_view(client, root)
    assert out["facts"]["published_graphs"] == before  # registry で取り下げの graph は数えない
    assert out["facts"]["datasets"] == 2


# --- (4) 版 graph を二重に数えない ----------------------------------------------------------


async def test_a_superseded_version_graph_is_not_counted_twice(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    # 古い v1 は store に残っているが、live は v2 だけを指す
    client.load(
        substrate.versioned_graph_iri("wx-a", 1) + "-old-copy",
        "".join(f"r:old-{i} a wx:Station .\n" for i in range(40)),
    )
    old = substrate.versioned_graph_iri("wx-a", 7)
    client.load(old, "".join(f"r:ghost-{i} a wx:Station .\n" for i in range(40)))
    out = await sky_view(client, root)  # live は v1 のまま（v7 は promoted の印なし）
    station = next(k for k in _dataset(out, "wx-a")["kinds"] if k["iri"] == STATION)
    assert station["count"] == 3

    # 新しい版を公開すると、その版の件数だけになる（古い版と合算しない）
    await _publish(
        client, "wx-a", "".join(f"r:n-{i} a wx:Station .\n" for i in range(4)), version=2
    )
    out = await sky_view(client, root)
    station = next(k for k in _dataset(out, "wx-a")["kinds"] if k["iri"] == STATION)
    assert station["count"] == 4


# --- (5) truncated --------------------------------------------------------------------------


async def test_max_kinds_cuts_the_smallest_and_says_truncated(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    out = await sky_view(client, root, max_kinds=2)
    assert out["truncated"] is True
    pairs = [(d["id"], k["iri"], k["count"]) for d in out["datasets"] for k in d["kinds"]]
    assert pairs == [("wx-a", OBSERVATION, 5), ("wx-a", STATION, 3)]  # 件数の多い順に 2 つ
    assert out["facts"]["kinds"] == 2
    # 切れた種類は、ことばの members にも hub の members にも残らない
    assert all(m["iri"] != SITE for m in out["terms"][0]["members"])
    assert out["hubs"][0]["members"] == [STATION]
    assert (await sky_view(client, root))["truncated"] is False


# --- 周辺 ----------------------------------------------------------------------------------


async def test_a_term_with_no_lines_still_appears_and_questions_need_it(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    await shared_vocab.mint_term(
        client,
        root,
        slug="speed",
        kind="property",
        label="速さ",
        cqs=[CQSpec("速さの値は、どのデータセットにいくつあるか", "values")],
        at="2026-10-14T00:00:00+00:00",
    )
    await shared_vocab.recompute_wired(client, root)
    out = await sky_view(client, root)
    speed = next(t for t in out["terms"] if t["slug"] == "speed")
    assert speed["kind"] == "property" and speed["members"] == [] and speed["standard"] is None
    assert speed["answering"] == 0 and speed["label_en"] is None
    q = next(q for q in out["questions"] if q["tool_name"] == "cq_speed_values")
    assert q["answering"] == 0 and q["needs"] == [shared_vocab.term_iri("speed")]
    assert questions_of_term(root, shared_vocab.term_iri("speed")) == ["cq_speed_values"]
    assert out["facts"]["questions_total"] == 2 and out["facts"]["questions_answerable"] == 1
    assert out["recent"][0]["kind"] == "term"  # いちばん新しい


async def test_dataset_without_a_registry_entry_is_not_listed(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    await _publish(client, "stray", "r:s-1 a wx:Station .\n")  # 登録が無い版 graph
    out = await sky_view(client, root)
    assert [d["id"] for d in out["datasets"]] == ["wx-a", "wy-b"]
    assert out["facts"]["published_graphs"] == 3  # 登録の無い graph は datasets と同じく数えない


def test_perspective_helpers_are_the_ones_sky_view_reads() -> None:
    # 読み手が頼る公開名が消えたら、ここで気づく
    assert callable(crosswalk_runtime.list_perspectives)
    assert callable(crosswalk_runtime.list_alignments)


async def test_recent_lists_publishes_when_nothing_else_happened(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    client = _Client()
    _meta(root, "wx-a", name="観測所データ", promoted=True, promoted_at="2026-10-01T09:00:00Z")
    _meta(root, "wy-b", name="地点データ", promoted=True, promoted_at="2026-10-02")  # 日付だけ
    await _publish(client, "wx-a", _STATIONS_A)
    await _publish(client, "wy-b", _SITES_B)
    out = await sky_view(client, root)
    assert [(r["kind"], r["at"]) for r in out["recent"]] == [
        ("publish", "2026-10-02"),
        ("publish", "2026-10-01T09:00:00Z"),
    ]
    assert out["recent"][0]["a"] == "地点データ"
    assert out["terms"] == [] and out["questions"] == [] and out["hubs"] == []
    assert out["facts"]["published_graphs"] == 2


# --- 知識の問い・知識の線（A1・A2） ----------------------------------------------------------


async def test_knowledge_questions_and_term_to_term_lines(tmp_path: Path) -> None:
    client, root = await _world(tmp_path)
    at = "2026-10-14T00:00:00+00:00"
    names = (("thermal", "熱伝導率"), ("grain", "粒界散乱"), ("lattice", "格子熱伝導率"))
    for slug, label in names:
        await shared_vocab.mint_term(
            client, root, slug=slug, kind="property", label=label, cqs=[], theme_id="heat", at=at
        )
    thermal, grain, lattice = (shared_vocab.term_iri(s) for s in ("thermal", "grain", "lattice"))
    await assert_alignment(client, grain, thermal, "lowers", at=at, reason="粒界で散乱される")
    await assert_alignment(client, lattice, thermal, "isPartOf", at=at)
    await assert_alignment(client, thermal, grain, "subPropertyOf", at=at)  # 知識の線ではない
    await shared_vocab.recompute_wired(client, root)
    name = "kq_thermal_factors_lowers"
    (root / "vocab-shared" / "themes.json").write_text(
        json.dumps(
            {
                "themes": [
                    {
                        "id": "heat",
                        "label": "熱電材料",
                        "questions": [name, "kq_gone_parts"],
                        "selections": {
                            name: {
                                "mode": "knowledge",
                                "op": "factors",
                                "target_slug": "thermal",
                                "direction": "lowers",
                                "title": "熱伝導率を下げる要素は何か",
                            },
                            "kq_gone_parts": {
                                "mode": "knowledge",
                                "op": "parts",
                                "target_slug": "gone",
                                "title": "外された語の問い",
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out = await sky_view(client, root)
    q = next(x for x in out["questions"] if x["tool_name"] == name)
    assert q["mode"] == "knowledge" and q["theme_id"] == "heat" and q["needs"] == [thermal]
    # データ 0・知識 1（走者で答えた数。/api/themes の「知識 N」と同じ。2 は線の数）
    assert q["answering"] == 0 and q["knowledge"] == 1
    assert all(x["mode"] == "data" and x["knowledge"] == 0 for x in out["questions"] if x is not q)
    assert not any(x["tool_name"] == "kq_gone_parts" for x in out["questions"])  # 語の無い問い
    assert name in next(t for t in out["terms"] if t["slug"] == "thermal")["questions"]
    # 星図を引かずに数える関数は、星図の terms[].questions と同じ数え方
    for t in out["terms"]:
        assert questions_of_term(root, t["iri"]) == t["questions"]
    assert questions_of_term(root, thermal) == [name] and questions_of_term(root, grain) == []
    f = out["facts"]
    assert f["questions_total"] == len(out["questions"])
    assert f["questions_answerable"] == 1 + sum(  # 知識の問い 1 + データで答えられる問い
        1 for x in out["questions"] if x["mode"] == "data" and x["answering"] >= 1
    )
    # 語どうしの知識の線だけ（sub/eq は含めない）。決定論の順
    assert [(e["from"], e["to"], e["relation"]) for e in out["edges"]] == sorted(
        [(grain, thermal, "lowers"), (lattice, thermal, "isPartOf")]
    )
    lowers = next(e for e in out["edges"] if e["relation"] == "lowers")
    assert lowers["reason"] == "粒界で散乱される"
