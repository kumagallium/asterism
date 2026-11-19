# ruff: noqa: E501
"""asterism.relation_store — 関係の目録をストアの正本に置く（R0d）。

実物の ``pyoxigraph.Store``・rdflib の Dataset・本物の Oxigraph（``ASTERISM_OXIGRAPH_BIN`` か PATH の
``oxigraph`` があるときだけ）で動かす。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import rdflib

from asterism import relation_catalog as rc
from asterism import relation_store as rs
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

pyoxigraph = pytest.importorskip("pyoxigraph")

AT = "2026-11-19T00:00:00Z"
AT2 = "2026-11-19T01:00:00Z"
XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
HUMAN = "https://kumagallium.github.io/asterism/vocab/relation-catalog#human-rel"


class _Client:
    """pyoxigraph の Store（書き込みの回数を数える）。"""

    def __init__(self) -> None:
        self.store = pyoxigraph.Store()
        self.updates = 0

    async def sparql_update(self, update: str) -> None:
        self.updates += 1
        self.store.update(update)

    async def sparql_select(self, query: str) -> dict:
        result = self.store.query(query, use_default_graph_as_union=True)
        if isinstance(result, bool):
            return {"boolean": result}
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row: dict[str, Any] = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                row[name] = {"type": kind, "value": term.value}
            bindings.append(row)
        return {"results": {"bindings": bindings}}


class _RdflibClient:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()
        self.updates = 0

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.updates += 1
        self.ds.update(update)


@pytest.fixture(params=["pyoxigraph", "rdflib"])
def client(request: pytest.FixtureRequest) -> Any:
    return _Client() if request.param == "pyoxigraph" else _RdflibClient()


def _fake(monkeypatch: pytest.MonkeyPatch, mutate: Any) -> dict:
    base = copy.deepcopy(rc.catalog_to_json())
    mutate(base)
    monkeypatch.setattr(rc, "catalog_to_json", lambda: copy.deepcopy(base))
    return base


# --- 目録 → RDF ---------------------------------------------------------------


def test_triples_are_deterministic() -> None:
    a = rs.catalog_triples(rc.catalog_to_json(), at=AT)
    b = rs.catalog_triples(rc.catalog_to_json(), at=AT)
    assert set(a) == set(b)
    assert rs.catalog_triples(rc.catalog_to_json()).serialize(
        format="nt"
    ) == rs.catalog_triples(rc.catalog_to_json()).serialize(format="nt")


def test_triples_shape() -> None:
    g = rs.catalog_triples(rc.catalog_to_json(), at=AT)
    sub = rdflib.URIRef(rs.CATALOG_NS + "sub")
    assert (sub, rdflib.RDF.type, rs.RELATION_ENTRY) in g
    # 表示名・文の型は言語タグつき
    assert (sub, rdflib.RDFS.label, rdflib.Literal("一種", lang="ja")) in g
    assert (sub, rdflib.RDFS.label, rdflib.Literal("kind of", lang="en")) in g
    sentence = rdflib.Literal("{s} is a kind of {o}", lang="en")
    assert (sub, rdflib.URIRef(XW + "sentence"), sentence) in g
    # 真偽は xsd:boolean・組み込みの印・置いた時刻と主体
    assert (sub, rdflib.URIRef(XW + "builtin"), rdflib.Literal(True)) in g
    assert (sub, rdflib.URIRef(XW + "addedBy"), rdflib.Literal("machine")) in g
    assert (
        sub,
        rdflib.URIRef("http://www.w3.org/ns/prov#generatedAtTime"),
        rdflib.Literal(AT, datatype=rdflib.XSD.dateTime),
    ) in g
    # 規則は 1 つ目から並びを持ち、述語は IRI を指すだけ
    rule1 = rdflib.URIRef(rs.CATALOG_NS + "sub-rule-1")
    assert (sub, rdflib.URIRef(XW + "rule"), rule1) in g
    assert (rule1, rdflib.RDF.type, rs.PREDICATE_RULE) in g
    assert (rule1, rdflib.URIRef(XW + "order"), rdflib.Literal(1)) in g
    assert (
        rule1,
        rdflib.URIRef(XW + "predicate"),
        rdflib.URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf"),
    ) in g
    # 標準の述語（外の語彙の IRI）には何も書かない（主語にならない）
    subjects = {s for s in g.subjects() if isinstance(s, rdflib.URIRef)}
    assert all(str(s).startswith(rs.CATALOG_NS) for s in subjects)
    # 自前の述語の記録は述語の記述の側
    comp = rdflib.URIRef(rs.CATALOG_NS + "predicate-competesWith")
    assert (comp, rs._X.predicate, rdflib.URIRef(rc.REL_NS + "competesWith")) in g
    own = rdflib.URIRef(str(comp) + "-own")
    assert (comp, rs._X.own, own) in g
    assert (own, rdflib.RDFS.label, rdflib.Literal("競合する", lang="ja")) in g


def test_unknown_field_is_mapped_by_type() -> None:
    cat = copy.deepcopy(rc.catalog_to_json())
    cat["relations"][0]["note"] = {"ja": "メモ"}
    cat["relations"][0]["weight"] = 3
    cat["relations"][0]["tags"] = ["x", "y"]
    g = rs.catalog_triples(cat)
    sub = rdflib.URIRef(rs.CATALOG_NS + "sub")
    assert (sub, rs._X.note, rdflib.Literal("メモ", lang="ja")) in g
    assert (sub, rs._X.weight, rdflib.Literal(3)) in g
    assert len(list(g.objects(sub, rs._X.tags))) == 2


# --- 読み戻し・種まき ----------------------------------------------------------


async def test_seed_and_read_back_equals_code(client: Any) -> None:
    assert await rs.seed_builtin(client, at=AT) is True
    got = await rs.read_catalog(client)
    assert got == rc.catalog_to_json()
    # 並び（関係・述語・規則・searched）が同じ
    assert [r["name"] for r in got["relations"]] == [r.name for r in rc.RELATIONS]
    assert [p["name"] for p in got["predicates"]] == [p.name for p in rc.PREDICATES]
    measures = next(r for r in got["relations"] if r["name"] == "measures")
    assert [p["name"] for p in measures["predicates"]] == ["measuresProperty", "measuresMaterial"]
    assert measures["predicates"][0]["when"] == ["to_kind", "property"]
    comp = next(p for p in got["predicates"] if p["name"] == "competesWith")
    assert comp["own"]["searched"] == list(rc.predicate("competesWith").own.searched)
    assert comp["roles"] == {"from": ["material"], "to": ["material"]}
    assert next(p for p in got["predicates"] if p["name"] == "subClassOf")["roles"] is None


async def test_seed_is_idempotent_and_second_run_writes_nothing(client: Any) -> None:
    assert await rs.seed_builtin(client, at=AT) is True
    n = client.updates
    assert await rs.seed_builtin(client, at=AT2) is False
    assert client.updates == n


async def test_time_is_kept_when_unchanged_and_renewed_when_changed(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rs.seed_builtin(client, at=AT)
    await rs.seed_builtin(client, at=AT2)
    q = (
        f"SELECT ?t WHERE {{ GRAPH <{rs.CATALOG_GRAPH}> {{ <{rs.CATALOG_NS}sub> "
        "<http://www.w3.org/ns/prov#generatedAtTime> ?t } }"
    )
    times = [b["t"]["value"].replace("+00:00", "Z") for b in (await client.sparql_select(q))["results"]["bindings"]]
    assert times == [AT]

    def change(c: dict) -> None:
        c["relations"][0]["label"]["ja"] = "種類の一つ"

    base = _fake(monkeypatch, change)
    assert await rs.seed_builtin(client, at=AT2) is True
    times = [b["t"]["value"].replace("+00:00", "Z") for b in (await client.sparql_select(q))["results"]["bindings"]]
    assert times == [AT2]
    assert await rs.read_catalog(client) == base


async def test_change_replaces_only_builtin_and_keeps_human_entries(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rs.seed_builtin(client, at=AT)
    # 人が足した関係（xw:builtin が無い）
    await client.sparql_update(
        f"INSERT DATA {{ GRAPH <{rs.CATALOG_GRAPH}> {{ "
        f"<{HUMAN}> a <{XW}RelationEntry> ; <{XW}name> \"human-rel\" ; "
        f"<{XW}order> 99 ; <{RDFS_LABEL}> \"人の関係\"@ja ; <{XW}addedBy> \"human\" . "
        f"<{HUMAN}-rule-1> a <{XW}PredicateRule> ; <{XW}ownerEntry> <{HUMAN}> . "
        f"<{HUMAN}> <{XW}rule> <{HUMAN}-rule-1> . }} }}"
    )

    def change(c: dict) -> None:
        c["relations"] = [r for r in c["relations"] if r["name"] != "affects"]

    base = _fake(monkeypatch, change)
    assert await rs.seed_builtin(client, at=AT2) is True
    # 組み込みだけを読むとコードの目録と一致・消えた関係は残らない
    assert await rs.read_catalog(client, builtin_only=True) == base
    allrel = [r["name"] for r in (await rs.read_catalog(client))["relations"]]
    assert "affects" not in allrel
    # 人の項目は無傷（子の節も）
    q = f"SELECT ?p ?o WHERE {{ GRAPH <{rs.CATALOG_GRAPH}> {{ <{HUMAN}> ?p ?o }} }}"
    props = {b["p"]["value"] for b in (await client.sparql_select(q))["results"]["bindings"]}
    assert {f"{XW}addedBy", f"{XW}rule", RDFS_LABEL} <= props
    q2 = f"ASK {{ GRAPH <{rs.CATALOG_GRAPH}> {{ <{HUMAN}-rule-1> <{XW}ownerEntry> <{HUMAN}> }} }}"
    assert (await client.sparql_select(q2))["boolean"] is True


async def test_new_field_in_catalog_round_trips(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(c: dict) -> None:
        for i, r in enumerate(c["relations"]):
            r["note"] = {"ja": f"メモ{i}", "en": f"note {i}"}
            r["rank"] = i
            r["tags"] = ["b", "a"] if i % 2 else ["a"]

    base = _fake(monkeypatch, change)
    await rs.seed_builtin(client, at=AT)
    assert await rs.read_catalog(client) == base


async def test_only_catalog_graph_is_written(client: Any) -> None:
    await rs.seed_builtin(client, at=AT)
    if isinstance(client, _Client):
        graphs = {q.graph_name.value for q in client.store}
    else:
        graphs = {str(c.identifier) for c in client.ds.contexts() if len(c)}
    assert graphs == {rs.CATALOG_GRAPH}


def test_bad_time_is_rejected() -> None:
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(rs.seed_builtin(_Client(), at="yesterday"))


# --- 本物の Oxigraph -----------------------------------------------------------


def _oxigraph_binary() -> str | None:
    override = (os.environ.get("ASTERISM_OXIGRAPH_BIN") or "").strip()
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() else shutil.which(override)
    return shutil.which("oxigraph")


@pytest.mark.skipif(_oxigraph_binary() is None, reason="no oxigraph binary")
async def test_real_oxigraph_round_trip(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    proc = subprocess.Popen(
        [
            str(_oxigraph_binary()),
            "serve",
            "--location",
            str(tmp_path / "s"),
            "--bind",
            f"127.0.0.1:{port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = OxigraphClient(OxigraphConfig(base_url=f"http://127.0.0.1:{port}"))
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
                break
            except httpx.HTTPError:
                assert time.monotonic() < deadline, "oxigraph did not start"
                time.sleep(0.1)
        assert await rs.seed_builtin(client, at=AT) is True
        assert await rs.read_catalog(client) == rc.catalog_to_json()
        assert await rs.seed_builtin(client, at=AT2) is False
    finally:
        await client.aclose()
        proc.terminate()
        proc.wait(timeout=10)
