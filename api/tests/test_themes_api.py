"""テーマ（知りたいことから作る）の api — ui-redesign PR 4。

ストアは本物のインメモリ ``rdflib.Dataset``（``test_vocab_api`` と同じ差し替え口）。AI は
注入した偽物（ネットワークなし）。確かめること: AI 未設定は 503・AI の答えは閉じた形だけ
通る・確定は 1 操作（先に検査・失敗で何も書かない）・テーマを外しても用語は残る・
別名は ``/api/vocab/fit`` の完全一致に効く・型紙は見出し行だけ。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import rdflib
from asterism import shared_vocab, themes
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from asterism_api.questions_routes import QUESTION_OPS
from tests.test_crosswalk_api import _AUTH, _DatasetClient, _settings

KEY = {"X-API-Key": "test-key"}

AI_JSON = {
    "terms": [
        {
            "label_ja": "電池セル",
            "label_en": "battery cell",
            "aliases": ["cell"],
            "kind": "class",
            "use": "new",
            "reason": "答えに要る",
        },
        {
            "label_ja": "容量",
            "label_en": "capacity",
            "aliases": ["cap"],
            "kind": "property",
            "use": "new",
            "reason": "",
        },
    ],
    "relations": [{"subject": "容量", "object": "quantitykind:Capacity", "relation": "quantity"}],
    "selections": [
        {
            "question": "容量が最も大きいセルは？",
            "op": "top",
            "kind": "電池セル",
            "property": "容量",
            "value": None,
            "fits": True,
            "reason": "",
        },
        {
            "question": "容量の推移は？",
            "op": "trend",
            "kind": "電池セル",
            "property": "容量",
            "value": None,
            "fits": True,
            "reason": "",
        },
    ],
    "suggested_questions": ["正極ごとの平均容量は？"],
}


class _LLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.responses.pop(0)


def _app(tmp_path: Path, llm: _LLM | None = None):
    ds = rdflib.Dataset()
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
        llm_factory=(lambda key: llm) if llm is not None else None,
    )
    return app, ds


def _commit_body(**over: object) -> dict:
    body: dict = {
        "label": "電池材料",
        "lang": "ja",
        "ai_drafted": True,
        "questions": ["容量が最も大きいセルは？", "容量の推移は？"],
        "terms": [
            {
                "key": "t1", "label_ja": "電池セル", "label_en": "battery cell",
                "aliases": ["cell"], "kind": "class", "use": "new", "slug": "battery_cell",
                "by": "ai",
            },
            {
                "key": "t2", "label_ja": "容量", "label_en": "capacity",
                "aliases": ["cap"], "kind": "property",
                "use": "standard:quantitykind:Capacity", "slug": "capacity", "by": "ai",
            },
        ],
        "relations": [],
        "selections": [
            {"question": "容量が最も大きいセルは？", "op": "top", "kind": "t1", "property": "t2"},
            {"question": "容量の推移は？", "op": "trend", "kind": "t1", "property": "t2"},
        ],
    }  # fmt: skip
    body.update(over)
    return body


def _draft_and_commit(client: TestClient, body: dict | None = None):
    d = client.post(
        "/api/themes/drafts", json={"label": "電池材料", "questions": ["q"]}
    ).json()
    return d, client.post(f"/api/themes/{d['id']}/commit", json=body or _commit_body())


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------


def test_fitting_ops_are_the_ops_the_declarative_tools_answer_today() -> None:
    assert themes.FITTING_OPS == QUESTION_OPS


# ---------------------------------------------------------------------------
# 一覧・最初の移行
# ---------------------------------------------------------------------------


def test_empty_store_lists_no_theme_and_records_the_migration(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        out = client.get("/api/themes").json()
        assert out["themes"] == [] and out["drafts"] == [] and out["unassigned_questions"] == []
        assert out["total_datasets"] == 0
        saved = json.loads((tmp_path / "registry/vocab-shared/themes.json").read_text())
        assert saved == {"themes": [], "migrated": True}


def test_existing_questions_become_the_first_theme_once(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/vocab/shared",
            json={
                "slug": "composition", "kind": "property", "label_ja": "組成",
                "cqs": [{"title": "組成は、どのデータセットにいくつあるか", "op": "values"}],
            },
        )  # fmt: skip
        assert r.status_code == 201
        first = client.get("/api/themes").json()
        assert [t["id"] for t in first["themes"]] == ["first"]
        theme = first["themes"][0]
        assert theme["label"] == "はじめの問い"
        q = theme["questions"][0]
        assert q["tool_name"] == "cq_composition_values"
        assert q["shape"] == "組成の値が、どのデータセットにいくつあるかを数えます"
        assert theme["terms"][0]["slug"] == "composition"
        assert client.get("/api/themes?lang=en").json()["themes"][0]["questions"][0]["shape"] == (
            "Counts how many values of 組成 each dataset has."
        )
        # a question minted later is in no theme (and no second theme appears)
        client.post(
            "/api/vocab/shared",
            json={"slug": "late", "kind": "class", "label_ja": "後",
                  "cqs": [{"title": "後は何件？", "op": "count"}]},
        )  # fmt: skip
        again = client.get("/api/themes").json()
        assert [t["id"] for t in again["themes"]] == ["first"]
        assert [q["tool_name"] for q in again["unassigned_questions"]] == ["cq_late_count"]


# ---------------------------------------------------------------------------
# AI の下書き
# ---------------------------------------------------------------------------


def test_draft_without_an_ai_is_503_so_the_screen_goes_manual(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)  # no llm_factory, no key, no server key
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/themes/draft", json={"questions": ["セルは何件？"]})
        assert r.status_code == 503
        assert "AI" in r.json()["detail"]


def test_draft_needs_a_question(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _LLM(json.dumps(AI_JSON)))
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        assert client.post("/api/themes/draft", json={"questions": [" ", ""]}).status_code == 422


def test_draft_returns_the_closed_json_and_the_machines_rows(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(AI_JSON))
    app, _ = _app(tmp_path, llm)
    qs = ["容量が最も大きいセルは？", "容量の推移は？"]
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        r = client.post("/api/themes/draft", json={"questions": qs, "lang": "ja"})
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {
        "ai", "terms", "relations", "selections", "suggested_questions", "standards", "groups",
    }  # fmt: skip
    rows = {t["label_ja"]: t for t in out["terms"]}
    assert rows["電池セル"]["use"] == "new" and rows["電池セル"]["slug"] == "battery_cell"
    # the machine's exact match with a quantity kind beats the model's "new"
    assert rows["容量"]["use"] == "standard:quantitykind:Capacity"
    assert out["standards"]["quantitykind:Capacity"]["kind"] == "quantity"
    assert out["relations"][0]["problem"] is None
    top, trend = out["selections"]
    assert (top["op"], top["fits"], top["memo"]) == ("top", True, None)
    assert (trend["op"], trend["fits"], trend["memo"]) == ("trend", False, "op")
    # what the model saw: the questions and NAMES — a closed prompt with no data in it
    system, user = llm.calls[0]
    assert "Do NOT write SPARQL" in system
    assert "容量が最も大きいセルは？" in user and "# Standard terms" in user
    assert "Existing terms" in user


def test_draft_hands_existing_term_names_to_the_model(tmp_path: Path) -> None:
    llm = _LLM(json.dumps({**AI_JSON, "terms": [], "relations": [], "selections": []}))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        client.post(
            "/api/vocab/shared",
            json={"slug": "sample", "kind": "class", "label_ja": "試料", "label_en": "sample",
                  "cqs": [{"title": "試料は何件？", "op": "count"}]},
        )  # fmt: skip
        client.post("/api/themes/draft", json={"questions": ["試料は？"]})
    assert "sample | 試料 | sample | class" in llm.calls[0][1]


def test_an_answer_off_the_closed_shape_is_retried_once_then_502(tmp_path: Path) -> None:
    bad = json.dumps({**AI_JSON, "terms": [{**AI_JSON["terms"][0], "label_en": "SELECT ?s"}]})
    llm = _LLM(bad, json.dumps(AI_JSON))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        assert client.post("/api/themes/draft", json={"questions": ["q"]}).status_code == 200
    assert len(llm.calls) == 2

    llm2 = _LLM("nonsense", "{still nonsense")
    app2, _ = _app(tmp_path / "again", llm2)
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        r = client.post("/api/themes/draft", json={"questions": ["q"]})
    assert r.status_code == 502 and "決まった形" in r.json()["detail"]


def test_describe_phrases_selections(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/api/themes/describe",
            json={
                "selections": [
                    {"op": "top", "kind": "電池セル", "property": "容量"},
                    {"op": "count"},
                ]
            },
        )
    assert r.json() == {
        "sentences": ["電池セルのうち、容量がいちばん大きいものを出します（データセットごと）", ""]
    }


# ---------------------------------------------------------------------------
# 下書き
# ---------------------------------------------------------------------------


def test_drafts_round_trip_and_survive_a_reopen(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d = client.post("/api/themes/drafts", json={"label": "電池", "questions": ["a", "b"]})
        assert d.status_code == 201
        did = d.json()["id"]
        saved = client.put(
            f"/api/themes/drafts/{did}",
            json={"label": "電池", "step": 3, "questions": ["a"], "terms": [{"key": "t1"}]},
        )
        assert saved.status_code == 200 and saved.json()["step"] == 3
        assert client.get(f"/api/themes/drafts/{did}").json()["terms"] == [{"key": "t1"}]
        assert [x["id"] for x in client.get("/api/themes").json()["drafts"]] == [did]
        assert client.put(
            f"/api/themes/drafts/{did}", json={"label": "電池", "step": 9}
        ).status_code == 422
        ghost = client.put("/api/themes/drafts/ghost", json={"label": "x", "step": 1})
        assert ghost.status_code == 404
        assert client.delete(f"/api/themes/drafts/{did}").status_code == 204
        assert client.get(f"/api/themes/drafts/{did}").status_code == 404


def test_writes_need_the_token(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:  # no token
        assert client.post("/api/themes/drafts", json={"label": "x"}).status_code in (401, 503)
        assert client.post("/api/themes/x/commit", json=_commit_body()).status_code in (401, 503)
        assert client.delete("/api/themes/x").status_code in (401, 503)


# ---------------------------------------------------------------------------
# 確定
# ---------------------------------------------------------------------------


def test_commit_places_terms_lines_questions_and_the_theme(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d, r = _draft_and_commit(client)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["minted"] == ["battery_cell", "capacity"]
        assert out["lines"] == 1  # capacity ties to the standard quantity kind
        assert out["memo"] == 1  # the trend cannot be answered yet
        assert out["questions"] == ["cq_capacity_values"]
        # the draft is gone, the theme is listed with its question's sentence
        assert client.get(f"/api/themes/drafts/{d['id']}").status_code == 404
        listing = client.get("/api/themes").json()
        theme = next(t for t in listing["themes"] if t["id"] == d["id"])
        assert theme["questions"][0]["shape"] == (
            "電池セルのうち、容量がいちばん大きいものを出します（データセットごと）"
        )
        assert theme["memo_questions"][0]["title"] == "容量の推移は？"
        # a term no question is about still counts as needed (it carries a default question)
        assert {t["slug"] for t in theme["terms"]} == {"battery_cell", "capacity"}
        assert listing["unassigned_questions"] == []
        # the terms carry the draft mark and the aliases
        terms = {t["slug"]: t for t in client.get("/api/vocab/shared").json()["terms"]}
        assert terms["battery_cell"]["aliases"] == ["cell"]
        assert terms["battery_cell"]["drafted_by"] == "ai"
        assert terms["battery_cell"]["confirmed_at"]


def test_a_refused_commit_is_422_and_writes_nothing(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        client.get("/api/themes")  # migrate first
        before = client.get("/api/vocab/shared").json()
        d = client.post("/api/themes/drafts", json={"label": "電池", "questions": []}).json()
        bad = _commit_body(relations=[{"subject": "t1", "object": "t2", "relation": "sub"}])
        r = client.post(f"/api/themes/{d['id']}/commit", json=bad)
        assert r.status_code == 422 and "種類と項目" in r.json()["detail"]
        assert client.get("/api/vocab/shared").json() == before
        assert client.get("/api/themes").json()["themes"] == []
        # the draft is still there to be fixed
        assert client.get(f"/api/themes/drafts/{d['id']}").status_code == 200


def test_a_taken_id_is_409(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        client.post(
            "/api/vocab/shared",
            json={"slug": "battery_cell", "kind": "class", "label_ja": "既存",
                  "cqs": [{"title": "既存は？", "op": "count"}]},
        )  # fmt: skip
        _, r = _draft_and_commit(client)
        assert r.status_code == 409 and "battery_cell" in r.json()["detail"]


def test_removing_a_theme_keeps_the_terms(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d, r = _draft_and_commit(client)
        assert r.status_code == 201
        before = client.get("/api/vocab/shared").json()
        assert client.delete(f"/api/themes/{d['id']}").status_code == 204
        assert d["id"] not in {t["id"] for t in client.get("/api/themes").json()["themes"]}
        assert client.get("/api/vocab/shared").json() == before  # terms, lines, questions stay
        listing = client.get("/api/themes").json()
        assert [q["tool_name"] for q in listing["unassigned_questions"]] == [
            "cq_battery_cell_count",
            "cq_capacity_values",
        ]
        assert client.delete(f"/api/themes/{d['id']}").status_code == 404


def test_a_question_can_be_added_to_a_theme(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d, _ = _draft_and_commit(client)
        r = client.post(
            f"/api/themes/{d['id']}/questions",
            json={
                "tool_names": ["cq_battery_cell_count"],
                "selections": {"cq_battery_cell_count": {"op": "count", "kind": "電池セル"}},
            },
        )
        assert r.status_code == 200
        theme = next(t for t in client.get("/api/themes").json()["themes"] if t["id"] == d["id"])
        names = [q["tool_name"] for q in theme["questions"]]
        assert names == ["cq_capacity_values", "cq_battery_cell_count"]
        assert theme["questions"][1]["shape"] == "電池セルの件数を数えます（データセットごと）"
        url = f"/api/themes/{d['id']}/questions"
        assert client.post(url, json={"tool_names": ["nope"]}).status_code == 422
        ghost = client.post("/api/themes/ghost/questions", json={"tool_names": []})
        assert ghost.status_code == 404


# ---------------------------------------------------------------------------
# 別名・型紙
# ---------------------------------------------------------------------------


def test_aliases_are_stored_listed_and_match_fit_exactly(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/vocab/shared",
            json={
                "slug": "composition", "kind": "property", "label_ja": "組成",
                "aliases": ["comp_str", "Formula", "formula "],
                "cqs": [{"title": "組成は？", "op": "values"}],
            },
        )  # fmt: skip
        assert r.status_code == 201 and r.json()["term"]["aliases"] == ["comp_str", "Formula"]
        listed = client.get("/api/vocab/shared").json()["terms"][0]
        assert listed["aliases"] == ["comp_str", "Formula"]  # by folded name
        hit = client.get("/api/vocab/fit", params={"label": "", "column": "comp_str"}).json()
        assert [(c["term"], c["matched_by"]) for c in hit["candidates"]] == [
            (shared_vocab.SV + "composition", "alias")
        ]
        by_meaning = client.get("/api/vocab/fit", params={"label": "FORMULA", "column": "x"}).json()
        assert by_meaning["candidates"][0]["matched_by"] == "alias"
        assert client.get("/api/vocab/fit", params={"column": "comp"}).json() == {"candidates": []}


def test_template_zip_is_one_header_row_per_kind(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d, r = _draft_and_commit(client)
        assert r.status_code == 201
        z = client.get(f"/api/themes/{d['id']}/template.csv.zip")
        assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
            assert zf.namelist() == ["battery_cell.csv"]  # (this body has no helper kind)
            assert zf.read("battery_cell.csv").decode().splitlines() == ["battery_cell_id,capacity"]
        assert client.get("/api/themes/ghost/template.csv.zip").status_code == 404
