"""Tests for POST /api/cards/converse (契約メモ contract_pr_f12.md §1-3)。

Backed by a real ``pyoxigraph.Store`` (same recipe as ``test_cards_api.py`` /
``ingest/tests/test_set_measure.py``'s ``_pyoxi_client``) so ``class_schema``/
``linking_kinds`` resolve from real data — only the LLM is a stub (never a
network call). Fixture domain is a generic field-observation log (§0: never a
real-world specialist domain)."""

# テストデータの日本語文言は全角の括弧・句読点が正しい表記（``describe.py``/
# ``converse_prompt.py`` と同じ理由）。
# ruff: noqa: RUF001
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    ONTOLOGY_GRAPH_BASE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings

pyoxigraph = pytest.importorskip("pyoxigraph")

_HEADERS = {**_AUTH, "X-API-Key": "sk-test-key"}


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
            if isinstance(result, bool):
                return {"head": {}, "boolean": result}
            names = [v.value for v in result.variables]
            bindings = []
            for solution in result:
                row: dict[str, Any] = {}
                for name in names:
                    term = solution[name]
                    if term is None:
                        continue
                    if isinstance(term, pyoxigraph.NamedNode):
                        row[name] = {"type": "uri", "value": term.value}
                    elif isinstance(term, pyoxigraph.Literal):
                        cell: dict[str, Any] = {"type": "literal", "value": term.value}
                        if term.language:
                            cell["xml:lang"] = term.language
                        elif term.datatype:
                            cell["datatype"] = term.datatype.value
                        row[name] = cell
                bindings.append(row)
            return {"results": {"bindings": bindings}}

        async def sparql_update(self, update: str) -> None:
            store.update(update)

        async def ping(self) -> bool:
            return True

        async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
            return 0

        async def aclose(self) -> None:
            return None

    return _C()


# ---------------------------------------------------------------------------
# Fixture data — a generic field-observation log: a Station individual with
# Reading records that point at it (link where-clause / linking_kinds), never
# a real-world specialist domain (§0).
# ---------------------------------------------------------------------------

EX = "https://ex/log#"
STATION_CLASS = EX + "Station"
READING_CLASS = EX + "Reading"
STATION_PRED = EX + "station"
DAY_PRED = EX + "day"
VALUE_PRED = EX + "value"
SITE_PRED = EX + "site"

LOG_DATASET = "field-log"
LOG_GRAPH = canonical_graph_iri(LOG_DATASET) + "/v1"

STATION_A = "https://ex/log/resource/station-a"
READING_1 = "https://ex/log/resource/reading-1"
READING_2 = "https://ex/log/resource/reading-2"

_ONTOLOGY_TTL = f"""
@prefix ex: <{EX}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:Station a rdfs:Class ; rdfs:label "観測局" .
ex:Reading a rdfs:Class ; rdfs:label "観測記録" .
ex:day a rdf:Property ; rdfs:domain ex:Reading ; rdfs:label "日" .
ex:value a rdf:Property ; rdfs:domain ex:Reading ; rdfs:label "値" .
ex:site a rdf:Property ; rdfs:domain ex:Reading ; rdfs:label "場所" .
ex:station a rdf:Property ; rdfs:domain ex:Reading ; rdfs:label "観測局" .
"""

_LOG_TTL = f"""
@prefix ex: <{EX}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{STATION_A}> a <{STATION_CLASS}> ; rdfs:label "Station A" .

<{READING_1}> a <{READING_CLASS}> ; rdfs:label "Reading One" ;
    ex:day "1"^^xsd:integer ; ex:value "10"^^xsd:double ; ex:site "north" ;
    ex:station <{STATION_A}> .
<{READING_2}> a <{READING_CLASS}> ; rdfs:label "Reading Two" ;
    ex:day "2"^^xsd:integer ; ex:value "20"^^xsd:double ; ex:site "south" ;
    ex:station <{STATION_A}> .
"""


def _client() -> object:
    return _pyoxi_client({ONTOLOGY_GRAPH_BASE + LOG_DATASET: _ONTOLOGY_TTL, LOG_GRAPH: _LOG_TTL})


class _ScriptedLLM:
    """Returns ``replies[n]`` on the n-th call; records every (system, user)
    pair it was called with so a test can inspect the retry hint."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls: list[dict[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append({"system": system_prompt, "user": user_message})
        return self._replies[len(self.calls) - 1]


class _RaisingLLM:
    def complete(self, system_prompt: str, user_message: str) -> str:
        raise RuntimeError("no API key configured")


def _app(tmp_path: Path, llm_factory):
    return build_app(
        _settings(tmp_path),
        oxigraph_client=_client(),
        start_watcher=False,
        llm_factory=llm_factory,
    )


def _body(**overrides: Any) -> dict[str, Any]:
    body = {
        "subject": {"kind": "individual", "iri": STATION_A},
        "messages": [{"role": "user", "content": "値の平均を教えて"}],
        "lang": "ja",
    }
    body.update(overrides)
    return body


def test_converse_returns_proposal_when_the_ai_offers_one(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            "『値』の平均は 15 です（各観測記録の value を平均）。"
            "<proposal>"
            '{"params": {"class": "' + READING_CLASS + '", "shape": "quantity", '
            '"item": "' + VALUE_PRED + '", "agg": "avg"}, "presentation": null, '
            '"title": "値の平均"}'
            "</proposal>"
        ]
    )
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert "<proposal>" not in body["reply"]
        assert "平均" in body["reply"]
        proposal = body["proposal"]
        assert proposal is not None
        assert proposal["params"]["class"] == READING_CLASS
        assert proposal["params"]["shape"] == "quantity"
        assert proposal["params"]["item"] == VALUE_PRED
        assert proposal["params"]["agg"] == "avg"
        # where は AI の指定を信用せず、linking_kinds から機械が補う。
        assert proposal["params"]["where"] == [{"property": STATION_PRED, "iri": STATION_A}]
        assert proposal["output_kind"] == "quantity"
        assert proposal["title"] == "値の平均"
        assert len(llm.calls) == 1


def test_converse_returns_no_proposal_when_the_ai_only_answers(tmp_path: Path) -> None:
    llm = _ScriptedLLM(["観測記録は 2 件あります（『Reading One』『Reading Two』）。"])
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposal"] is None
        assert "2 件" in body["reply"]
        assert len(llm.calls) == 1


def test_converse_retries_once_then_succeeds(tmp_path: Path) -> None:
    """1 回目は妥当性表の外（category に quantity 列）→ 機械が理由を渡して
    直させ、2 回目で通る提案を返す。"""
    invalid = (
        "内訳を作ります。"
        "<proposal>"
        '{"params": {"class": "' + READING_CLASS + '", "shape": "breakdown", '
        '"category": "' + VALUE_PRED + '"}, "presentation": null, "title": "内訳"}'
        "</proposal>"
    )
    valid = (
        "場所ごとの件数を出します。"
        "<proposal>"
        '{"params": {"class": "' + READING_CLASS + '", "shape": "breakdown", '
        '"category": "' + SITE_PRED + '"}, "presentation": null, "title": "内訳"}'
        "</proposal>"
    )
    llm = _ScriptedLLM([invalid, valid])
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body())
        assert r.status_code == 200, r.text
        body = r.json()
        proposal = body["proposal"]
        assert proposal is not None
        assert proposal["params"]["category"] == SITE_PRED
        assert len(llm.calls) == 2
        # 2 回目の呼び出しには、AI 向けの言い直し指示が乗っている。
        assert "通りませんでした" in llm.calls[1]["user"]


def test_converse_gives_up_after_one_retry_and_hides_the_raw_reason(tmp_path: Path) -> None:
    """2 回とも妥当性表の外の class を提案し続けたら、proposal は null で、
    reply には生の MeasureSpecError（property IRI を含み得る）を出さない
    （K4）。"""
    always_invalid = (
        "作ります。<proposal>"
        '{"params": {"class": "https://ex/log#NotAKind", "shape": "quantity", '
        '"item": "' + VALUE_PRED + '", "agg": "avg"}}'
        "</proposal>"
    )
    llm = _ScriptedLLM([always_invalid, always_invalid])
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposal"] is None
        assert "NotAKind" not in body["reply"]
        assert "うまく観点を作れませんでした" in body["reply"]
        assert len(llm.calls) == 2


# ---------------------------------------------------------------------------
# kind: "view"（契約メモ contract_pr_f13.md §1-2）— AI が Vega-Lite/表仕様/
# Mermaid を「書く」方の経路。
# ---------------------------------------------------------------------------


def _page_with_card(card_id: str = "card-1") -> dict[str, Any]:
    return {
        "facts": [],
        "cards": [
            {
                "card_id": card_id,
                "title": "値の推移",
                "output_kind": "series",
                "rows": [{"x": 1, "y": 10}, {"x": 2, "y": 20}],
            }
        ],
    }


def test_converse_returns_a_view_proposal_when_the_spec_is_in_the_allow_list(
    tmp_path: Path,
) -> None:
    llm = _ScriptedLLM(
        [
            "2 系列を重ねて描きます。"
            "<proposal>"
            '{"kind": "view", "view": {"lang": "vega-lite", "spec": '
            '{"mark": "line", "encoding": {'
            '"x": {"field": "x", "type": "quantitative"}, '
            '"y": {"field": "y", "type": "quantitative"}}}, '
            '"source_card_id": "card-1"}}'
            "</proposal>"
        ]
    )
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=_page_with_card()))
        assert r.status_code == 200, r.text
        body = r.json()
        proposal = body["proposal"]
        assert proposal is not None
        assert proposal["kind"] == "view"
        assert proposal["view"]["lang"] == "vega-lite"
        assert proposal["view"]["source_card_id"] == "card-1"
        assert "data" not in proposal["view"]["spec"]
        assert len(llm.calls) == 1


def test_converse_view_proposal_retries_once_when_the_spec_has_data_then_succeeds(
    tmp_path: Path,
) -> None:
    """契約 §1-2 の許可リストの外（``data``）は必ず落ち、1 回だけ言い直させる。"""
    with_data = (
        "描きます。<proposal>"
        '{"kind": "view", "view": {"lang": "vega-lite", "spec": '
        '{"mark": "line", "data": {"values": [{"x": 1}]}}, '
        '"source_card_id": "card-1"}}'
        "</proposal>"
    )
    without_data = (
        "描きます。<proposal>"
        '{"kind": "view", "view": {"lang": "vega-lite", "spec": '
        '{"mark": "line", "encoding": {"x": {"field": "x", "type": "quantitative"}}}, '
        '"source_card_id": "card-1"}}'
        "</proposal>"
    )
    llm = _ScriptedLLM([with_data, without_data])
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=_page_with_card()))
        assert r.status_code == 200, r.text
        body = r.json()
        proposal = body["proposal"]
        assert proposal is not None
        assert proposal["kind"] == "view"
        assert "data" not in proposal["view"]["spec"]
        assert len(llm.calls) == 2
        assert "通りませんでした" in llm.calls[1]["user"]


def test_converse_view_proposal_rejects_an_unknown_source_card_id(tmp_path: Path) -> None:
    """``source_card_id`` が ``page.cards`` に無ければ、2 回とも通らず
    proposal は null（K4: 生の理由は reply に出さない）。"""
    always_unknown = (
        "描きます。<proposal>"
        '{"kind": "view", "view": {"lang": "vega-lite", "spec": '
        '{"mark": "line"}, "source_card_id": "card-does-not-exist"}}'
        "</proposal>"
    )
    llm = _ScriptedLLM([always_unknown, always_unknown])
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=_page_with_card()))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposal"] is None
        assert "card-does-not-exist" not in body["reply"]
        assert len(llm.calls) == 2


def test_converse_view_proposal_rejects_a_mermaid_click_directive(tmp_path: Path) -> None:
    always_bad = (
        "描きます。<proposal>"
        '{"kind": "view", "view": {"lang": "mermaid", '
        '"text": "flowchart LR\\n  a[A] --> b[B]\\n  click a \\"https://ex/leak\\"", '
        '"source_card_id": "card-1"}}'
        "</proposal>"
    )
    llm = _ScriptedLLM([always_bad, always_bad])
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=_page_with_card()))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposal"] is None
        assert len(llm.calls) == 2


def test_converse_without_a_key_is_502(tmp_path: Path) -> None:
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_client(),
        start_watcher=False,
        llm_factory=lambda key: _RaisingLLM(),
    )
    with TestClient(app, headers=_AUTH) as client:  # no X-API-Key header
        r = client.post("/api/cards/converse", json=_body())
        assert r.status_code == 502


def test_converse_rejects_an_oversized_body(tmp_path: Path) -> None:
    huge_page = {"facts": [{"label": "x", "value": "y" * 1000} for _ in range(200)]}
    app = _app(tmp_path, lambda key: _ScriptedLLM(["never reached"]))
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=huge_page))
        assert r.status_code == 413


def test_converse_rejects_empty_messages(tmp_path: Path) -> None:
    app = _app(tmp_path, lambda key: _ScriptedLLM(["unused"]))
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(messages=[]))
        assert r.status_code == 400


def test_converse_rejects_a_malformed_subject(tmp_path: Path) -> None:
    app = _app(tmp_path, lambda key: _ScriptedLLM(["unused"]))
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(subject={"kind": "individual"}))
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# appdata pagechat threads (§1-1 — same shape as consult threads / cards).
# ---------------------------------------------------------------------------


def _single_user_settings(tmp_path: Path):
    s = _settings(tmp_path)
    s.single_user = True
    s.appdata_root = tmp_path / "appdata"
    return s


def test_appdata_pagechat_404_without_single_user(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/appdata/pagechat/threads").status_code == 404
        thread_id = "11111111-1111-4111-8111-111111111111"
        assert client.put(f"/api/appdata/pagechat/threads/{thread_id}", json={}).status_code == 404
        assert client.delete(f"/api/appdata/pagechat/threads/{thread_id}").status_code == 404


def test_appdata_pagechat_round_trip(tmp_path: Path) -> None:
    app = build_app(_single_user_settings(tmp_path), oxigraph_client=_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        thread_id = "22222222-2222-4222-8222-222222222222"
        payload = {"subject_key": f"i:{STATION_A}", "messages": []}
        r = client.put(f"/api/appdata/pagechat/threads/{thread_id}", json=payload)
        assert r.status_code == 200, r.text
        assert r.json() == {"saved": True}

        listed = client.get("/api/appdata/pagechat/threads").json()["threads"]
        assert payload in listed

        r = client.delete(f"/api/appdata/pagechat/threads/{thread_id}")
        assert r.status_code == 200
        assert r.json() == {"deleted": True}
        assert client.get("/api/appdata/pagechat/threads").json()["threads"] == []


# ---------------------------------------------------------------------------
# source_card_id の手がかり — 実機で「プロンプトが card_id を AI に一切見せて
# いない」穴が見つかったので、各行の先頭に [id: …] を出し、題名でも引けるように
# する（弱い LLM の救済）。
# ---------------------------------------------------------------------------


def test_render_user_prompt_shows_each_card_id_as_a_hint_for_source_card_id() -> None:
    from asterism_api.converse_prompt import render_user_prompt

    page = _page_with_card("card-1")
    page["cards"].append({"title": "id の無いカード", "output_kind": "quantity", "rows": []})
    text = render_user_prompt([{"role": "user", "content": "面グラフにして"}], page)
    assert "- [id: card-1] 値の推移 (series):" in text
    assert "- id の無いカード (quantity):" in text
    assert text.count("[id:") == 1


def test_converse_view_proposal_accepts_the_exact_card_title_as_source(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            "面グラフにします。"
            "<proposal>"
            '{"kind": "view", "view": {"lang": "vega-lite", "spec": '
            '{"mark": "area", "encoding": {'
            '"x": {"field": "x", "type": "quantitative"}, '
            '"y": {"field": "y", "type": "quantitative"}}}, '
            '"source_card_id": "値の推移"}}'
            "</proposal>"
        ]
    )
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=_page_with_card()))
        assert r.status_code == 200, r.text
        proposal = r.json()["proposal"]
        assert proposal is not None and proposal["kind"] == "view"
        # 題名は id に引き直されて返る（UI は id でしか元カードを探さない）。
        assert proposal["view"]["source_card_id"] == "card-1"
        assert len(llm.calls) == 1


def test_converse_view_proposal_rejects_an_ambiguous_card_title(tmp_path: Path) -> None:
    reply = (
        "面グラフにします。"
        "<proposal>"
        '{"kind": "view", "view": {"lang": "vega-lite", "spec": {"mark": "area"}, '
        '"source_card_id": "値の推移"}}'
        "</proposal>"
    )
    llm = _ScriptedLLM([reply, reply])
    page = _page_with_card("card-1")
    page["cards"].append(dict(page["cards"][0], card_id="card-2"))
    app = _app(tmp_path, lambda key: llm)
    with TestClient(app, headers=_HEADERS) as client:
        r = client.post("/api/cards/converse", json=_body(page=page))
        assert r.status_code == 200, r.text
        # 同じ題名が 2 枚あると曖昧なので引かない（1 回直させても同じなら提案なし）。
        assert r.json()["proposal"] is None
        assert len(llm.calls) == 2
