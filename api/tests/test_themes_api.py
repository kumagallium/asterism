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

Q_LOWER = "熱伝導率を下げる要素になり得るのは？"

#: スタブ AI が返す v2 の下書き（設計 §15.3・熱伝導率の例）
AI_JSON = {
    "theme_label": "熱電材料",
    "concepts": [
        {"key": "c1", "label_ja": "熱伝導率", "label_en": "Thermal Conductivity",
         "aliases": ["kappa"], "kind": "property",
         "use": "standard:quantitykind:ThermalConductivity", "reason": "問いの対象"},
        {"key": "c2", "label_ja": "格子熱伝導率", "label_en": "lattice thermal conductivity",
         "aliases": [], "kind": "property", "use": "new", "reason": "フォノンが運ぶ分"},
        {"key": "c3", "label_ja": "電子熱伝導率", "label_en": "electronic thermal conductivity",
         "aliases": [], "kind": "property", "use": "new", "reason": "キャリアが運ぶ分"},
        {"key": "c4", "label_ja": "粒界散乱", "label_en": "grain boundary scattering",
         "aliases": [], "kind": "class", "use": "new", "reason": "ナノ結晶化で増える"},
        {"key": "c5", "label_ja": "点欠陥散乱", "label_en": "point defect scattering",
         "aliases": [], "kind": "class", "use": "new", "reason": "合金化・質量ゆらぎ"},
        {"key": "c6", "label_ja": "キャリア濃度", "label_en": "carrier concentration",
         "aliases": ["n"], "kind": "property", "use": "new", "reason": "WF 則"},
    ],
    "relations": [
        {"from": "c2", "rel": "part", "to": "c1", "reason": "熱伝導率 = 格子 + 電子"},
        {"from": "c3", "rel": "part", "to": "c1", "reason": "熱伝導率 = 格子 + 電子"},
        {"from": "c4", "rel": "lowers", "to": "c2", "reason": "粒界でフォノンが散乱される"},
        {"from": "c5", "rel": "lowers", "to": "c2", "reason": "質量ゆらぎ"},
        {"from": "c6", "rel": "raises", "to": "c3", "reason": "キャリアが増えると上がる"},
    ],
    "answers": [
        {"question": Q_LOWER, "mode": "knowledge", "op": "factors", "target": "c1",
         "direction": "lowers", "kind": None, "property": None,
         "wanted_data": "試料ごとの 粒径・熱伝導率 の表", "reason": "要因を知識で並べる"},
    ],
    "suggested_questions": ["組成ごとの熱伝導率の最小値は？"],
}  # fmt: skip

EXPAND_JSON = {
    "theme_label": "",
    "concepts": [
        {"key": "n1", "label_ja": "ナノ析出物", "label_en": "nano precipitate", "aliases": [],
         "kind": "class", "use": "new", "reason": "中周波フォノンを散乱"},
    ],
    "relations": [{"from": "n1", "rel": "lowers", "to": "c2", "reason": "界面散乱"}],
    "answers": [],
    "suggested_questions": [],
}  # fmt: skip


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


def _heat_body(**over: object) -> dict:
    """AI_JSON をそのまま確定する形（概念に slug を付け、全部 AI の下書きとして）。"""
    slugs = {"c1": "thermal_conductivity", "c2": "lattice_tc", "c3": "electronic_tc",
             "c4": "grain_boundary", "c5": "point_defect", "c6": "carrier_conc"}  # fmt: skip
    body: dict = {
        "label": "熱電材料",
        "lang": "ja",
        "ai_drafted": True,
        "batch": "batch-1",
        "questions": [Q_LOWER],
        "concepts": [{**c, "slug": slugs[c["key"]], "by": "ai"} for c in AI_JSON["concepts"]],
        "relations": [{**r, "by": "ai"} for r in AI_JSON["relations"]],
        "answers": AI_JSON["answers"],
    }
    body.update(over)
    return body


def _cell_body(**over: object) -> dict:
    """データで答える小さな例（AI なしで手入力した概念だけ）。"""
    body: dict = {
        "label": "電池材料",
        "lang": "ja",
        "ai_drafted": False,
        "questions": ["容量が最も大きいセルは？", "容量の推移は？"],
        "concepts": [
            {"key": "t1", "label_ja": "電池セル", "label_en": "battery cell", "aliases": ["cell"],
             "kind": "class", "use": "new", "slug": "battery_cell"},
            {"key": "t2", "label_ja": "容量", "label_en": "capacity", "aliases": ["cap"],
             "kind": "property", "use": "standard:quantitykind:Capacity", "slug": "capacity"},
        ],
        "relations": [],
        "answers": [
            {"question": "容量が最も大きいセルは？", "mode": "data", "op": "top",
             "kind": "t1", "property": "t2", "wanted_data": "セルごとの 容量 の表"},
            {"question": "容量の推移は？", "mode": "memo", "op": "trend"},
        ],
    }  # fmt: skip
    body.update(over)
    return body


def _draft_and_commit(client: TestClient, body: dict | None = None):
    b = body if body is not None else _cell_body()
    d = client.post(
        "/api/themes/drafts", json={"label": b.get("label", ""), "questions": ["q"]}
    ).json()
    return d, client.post(f"/api/themes/{d['id']}/commit", json=b)


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------


def test_fitting_ops_are_the_ops_the_declarative_tools_answer() -> None:
    # かんたんの問いづくり（questions_routes）の 3 つ + テーマの 4 つ（絞る・〜ごと・推移・結合）
    assert themes.FITTING_OPS[: len(QUESTION_OPS)] == QUESTION_OPS
    assert themes.FITTING_OPS[len(QUESTION_OPS) :] == ("filter", "group", "trend", "join")


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


def _copilot_app(tmp_path: Path, *, single_user: bool, llm: _LLM):
    cfg = _settings(tmp_path)
    cfg.single_user = single_user
    return build_app(
        cfg,
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
        llm_factory=lambda key: llm,
    )


def test_draft_with_copilot_is_not_503_without_a_key(tmp_path: Path) -> None:
    # copilot はキー無しで「設定済み」。単一ユーザーなら AI が走る。
    llm = _LLM(json.dumps(AI_JSON))
    app = _copilot_app(tmp_path, single_user=True, llm=llm)
    with TestClient(app, headers={**_AUTH, "X-LLM-Provider": "copilot-subscription"}) as client:
        r = client.post("/api/themes/draft", json={"questions": [Q_LOWER], "lang": "ja"})
    assert r.status_code == 200
    assert llm.calls


def test_draft_with_copilot_on_a_shared_server_is_400_not_503(tmp_path: Path) -> None:
    app = _copilot_app(tmp_path, single_user=False, llm=_LLM())
    with TestClient(app, headers={**_AUTH, "X-LLM-Provider": "copilot-subscription"}) as client:
        r = client.post("/api/themes/draft", json={"questions": [Q_LOWER]})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "copilot_unavailable"


def test_draft_needs_a_question(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _LLM(json.dumps(AI_JSON)))
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        assert client.post("/api/themes/draft", json={"questions": [" ", ""]}).status_code == 422


def test_draft_returns_the_knowledge_map_after_the_machines_checks(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(AI_JSON))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        r = client.post("/api/themes/draft", json={"questions": [Q_LOWER], "lang": "ja"})
    assert r.status_code == 200
    out = r.json()
    # the model's raw output is not handed back: only the post-processed rows
    assert set(out) == {
        "theme_label", "concepts", "relations", "answers", "suggested_questions",
        "standards", "dropped",
    }  # fmt: skip
    assert out["theme_label"] == "熱電材料"  # the model named it (the person gave none)
    rows = {c["label_ja"]: c for c in out["concepts"]}
    # the machine's exact match with a quantity kind beats the model's "new"
    assert rows["熱伝導率"]["use"] == "standard:quantitykind:ThermalConductivity"
    assert out["standards"]["quantitykind:ThermalConductivity"]["kind"] == "quantity"
    # 画面が curie を IRI に直す表を持たなくて済むよう、標準の語の IRI も返す
    assert out["standards"]["quantitykind:ThermalConductivity"]["iri"].endswith(
        "/quantitykind/ThermalConductivity"
    )
    assert rows["粒界散乱"]["use"] == "new"
    assert rows["粒界散乱"]["slug"] == "grain_boundary_scattering"
    assert [r["rel"] for r in out["relations"]] == ["part", "part", "lowers", "lowers", "raises"]
    assert all(r["problem"] is None for r in out["relations"])
    assert out["relations"][2]["reason"] == "粒界でフォノンが散乱される"
    (ans,) = out["answers"]
    assert (ans["mode"], ans["op"], ans["target"], ans["direction"], ans["memo"]) == (
        "knowledge", "factors", "c1", "lowers", None,
    )  # fmt: skip
    assert ans["wanted_data"] == "試料ごとの 粒径・熱伝導率 の表"
    # what the model saw: the question and NAMES — a closed prompt with no data in it
    system, user = llm.calls[0]
    assert "Do NOT write SPARQL" in system and "FACTORS" in system
    assert Q_LOWER in user and "# Standard terms" in user and "Existing terms" in user
    assert "fill theme_label" in user  # no name was given


def test_draft_names_the_theme_from_the_person_then_the_model_then_the_question(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path, _LLM(json.dumps(AI_JSON), json.dumps({**AI_JSON, "theme_label": ""})))
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        given = client.post("/api/themes/draft", json={"questions": [Q_LOWER], "label": "私の名前"})
        assert given.json()["theme_label"] == "私の名前"
        none = client.post("/api/themes/draft", json={"questions": [Q_LOWER]})
        assert none.json()["theme_label"] == Q_LOWER[:20]


def test_draft_hands_existing_term_names_to_the_model(tmp_path: Path) -> None:
    empty = {**AI_JSON, "concepts": [], "relations": [], "answers": []}
    llm = _LLM(json.dumps(empty))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        client.post(
            "/api/vocab/shared",
            json={"slug": "sample", "kind": "class", "label_ja": "試料", "label_en": "sample",
                  "cqs": [{"title": "試料は何件？", "op": "count"}]},
        )  # fmt: skip
        client.post("/api/themes/draft", json={"questions": ["試料は？"]})
    assert "sample | 試料 | sample | class" in llm.calls[0][1]


def test_draft_drops_what_the_person_dismissed_before(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _LLM(json.dumps(AI_JSON)))
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        shared_vocab.dismiss_fit(
            tmp_path / "registry",
            shared_vocab.concept_subject("点欠陥散乱"),
            shared_vocab.CONCEPT_DISMISS_TERM,
            at="2026-10-20T00:00:00+00:00",
        )
        out = client.post("/api/themes/draft", json={"questions": [Q_LOWER]}).json()
    assert "点欠陥散乱" not in {c["label_ja"] for c in out["concepts"]}
    assert out["dropped"]["concepts"] == ["点欠陥散乱"]
    assert len(out["relations"]) == 4


def test_an_answer_off_the_closed_shape_is_retried_once_then_502(tmp_path: Path) -> None:
    bad = json.dumps({**AI_JSON, "concepts": [{**AI_JSON["concepts"][0], "label_en": "SELECT ?s"}]})
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


# ---------------------------------------------------------------------------
# 「ここを広げる」
# ---------------------------------------------------------------------------


def _saved_draft(client: TestClient) -> dict:
    """下書きを作り、AI の後処理済みの行を保存する（UI がやる手順）。"""
    d = client.post("/api/themes/drafts", json={"label": "", "questions": [Q_LOWER]}).json()
    out = client.post("/api/themes/draft", json={"questions": [Q_LOWER]}).json()
    body = {k: out[k] for k in ("concepts", "relations", "answers", "suggested_questions",
                                "standards")}  # fmt: skip
    r = client.put(
        f"/api/themes/drafts/{d['id']}",
        json={"label": out["theme_label"], "step": 2, "questions": [Q_LOWER], **body},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_expand_adds_to_the_draft_and_saves_it(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(AI_JSON), json.dumps(EXPAND_JSON))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _saved_draft(client)
        r = client.post(
            "/api/themes/expand", json={"draft_id": draft["id"], "focus_key": "c2"}
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert [c["label_ja"] for c in out["added"]["concepts"]] == ["ナノ析出物"]
        assert out["added"]["relations"][0]["to"] == "c2"
        assert len(out["concepts"]) == 7 and len(out["relations"]) == 6
        # saved: reading it back gives the same
        assert client.get(f"/api/themes/drafts/{draft['id']}").json()["concepts"] == out["concepts"]
        assert out["label"] == "熱電材料"
    # the model saw the map's names and the focus — and no data
    system, user = llm.calls[1]
    assert "ONE concept" in system
    assert "# Concept to expand (key)\nc2" in user
    assert "c2 | 格子熱伝導率 | lattice thermal conductivity | property" in user


def test_expand_needs_the_token_a_draft_a_focus_and_an_ai(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(AI_JSON))
    app, _ = _app(tmp_path, llm)
    with TestClient(app) as anon:
        r = anon.post("/api/themes/expand", json={"draft_id": "x", "focus_key": "c1"})
        assert r.status_code in (401, 503)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        ghost = client.post("/api/themes/expand", json={"draft_id": "nope", "focus_key": "c1"})
        assert ghost.status_code == 404
        draft = _saved_draft(client)
        bad = client.post(
            "/api/themes/expand", json={"draft_id": draft["id"], "focus_key": "zz"}
        )
        assert bad.status_code == 422
    app2, _ = _app(tmp_path / "noai")  # no AI configured
    with TestClient(app2, headers=_AUTH) as client:
        d = client.post("/api/themes/drafts", json={"label": "x", "questions": ["q"]}).json()
        client.put(
            f"/api/themes/drafts/{d['id']}",
            json={"label": "x", "step": 2, "concepts": [{"key": "c1", "label_ja": "あ"}]},
        )
        r = client.post("/api/themes/expand", json={"draft_id": d["id"], "focus_key": "c1"})
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# 照合・実行文
# ---------------------------------------------------------------------------


def test_match_finds_exact_names_without_a_token_and_without_the_ai(tmp_path: Path) -> None:
    llm = _LLM()  # a call would pop from an empty list
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        client.post(
            "/api/vocab/shared",
            json={"slug": "record", "kind": "class", "label_ja": "記録", "label_en": "record",
                  "cqs": [{"title": "記録は何件？", "op": "count"}]},
        )  # fmt: skip
    with TestClient(app) as anon:  # no token at all
        r = anon.post(
            "/api/themes/match",
            json={"terms": [
                {"key": "a", "label_ja": "記録", "label_en": "", "aliases": [], "kind": "class"},
                {"key": "b", "label_ja": "温度", "label_en": "temperature", "aliases": [],
                 "kind": "property"},
                {"key": "c", "label_ja": "記録の件", "label_en": "", "aliases": [],
                 "kind": "class"},
            ]},
        )  # fmt: skip
    assert r.status_code == 200
    rows = {t["key"]: t for t in r.json()["terms"]}
    assert set(r.json()) == {"terms"} and set(rows["a"]) == {"key", "match", "use", "slug"}
    assert rows["a"]["use"] == "existing:record" and rows["a"]["match"]["kind"] == "existing"
    assert rows["b"]["use"] == "standard:quantitykind:Temperature"
    # a new row gets the ID the machine would give it (t<n> — no English name here)
    assert rows["c"] == {"key": "c", "match": None, "use": "new", "slug": "t3"}
    assert llm.calls == []


def test_describe_phrases_selections_data_and_knowledge(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/api/themes/describe",
            json={
                "selections": [
                    {"op": "top", "kind": "電池セル", "property": "容量"},
                    {"op": "count"},
                    {"op": "factors", "target": "熱伝導率", "direction": "lowers"},
                    {"op": "parts", "target": "熱伝導率"},
                    {"op": "kinds", "target": "熱伝導率"},
                ]
            },
        )
    assert r.json() == {
        "sentences": [
            "電池セルのうち、容量がいちばん大きいものを出します（データセットごと）",
            "",
            "熱伝導率 を下げるもの",
            "熱伝導率 の一部",
            "熱伝導率 の一種",
        ]
    }


def test_describe_phrases_the_four_wider_shapes(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/api/themes/describe",
            json={
                "selections": [
                    {"op": "filter", "kind": "試料", "property": "組成", "value": "SnSe"},
                    {"op": "group", "kind": "試料", "property": "組成"},
                    {"op": "trend", "kind": "測定曲線", "property": "温度",
                     "property2": "Seebeck 係数"},
                    {"op": "join", "kind": "論文", "property": "報告する", "kind2": "試料"},
                    {"op": "join", "kind": "論文", "property": "報告する"},
                ]
            },
        )  # fmt: skip
    assert r.json()["sentences"] == [
        "試料のうち、組成に SnSe を含むもの",
        "試料を 組成 ごとに数える",
        "測定曲線の 温度 と Seebeck 係数 の並び",
        "論文 が 報告する 試料",
        "",
    ]


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
            json={"label": "電池", "step": 3, "questions": ["a"], "concepts": [{"key": "c1"}],
                  "ai": {"concepts": [{"label_ja": "生の出力"}]}},
        )  # fmt: skip
        assert saved.status_code == 200 and saved.json()["step"] == 3
        got = client.get(f"/api/themes/drafts/{did}").json()
        assert got["concepts"] == [{"key": "c1"}] and "ai" not in got
        assert [x["id"] for x in client.get("/api/themes").json()["drafts"]] == [did]
        for bad_step in (4, 0, 9):  # three steps now
            r = client.put(f"/api/themes/drafts/{did}", json={"label": "電池", "step": bad_step})
            assert r.status_code == 422
        ghost = client.put("/api/themes/drafts/ghost", json={"label": "x", "step": 1})
        assert ghost.status_code == 404
        assert client.delete(f"/api/themes/drafts/{did}").status_code == 204
        assert client.get(f"/api/themes/drafts/{did}").status_code == 404


def test_a_draft_saves_the_thin_rows_and_can_forget_the_chat(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        made = client.post("/api/themes/drafts", json={"label": "電池", "questions": ["a"]})
        did = made.json()["id"]
        url = f"/api/themes/drafts/{did}"
        base = {"label": "電池", "step": 2, "questions": ["a"], "concepts": [{"key": "n1"}]}
        assert "pending" not in client.put(url, json=base).json()  # 送っていない — 欄が無い
        thin = {"concepts": ["n1"], "relations": [["n1", "lowers", "n2"]]}
        assert client.put(url, json={**base, "pending": thin}).json()["pending"] == thin
        assert client.get(url).json()["pending"] == thin
        # 送らない保存では残る・空は「全部本物」として残る
        assert client.put(url, json=base).json()["pending"] == thin
        empty = {"concepts": [], "relations": []}
        assert client.put(url, json={**base, "pending": empty}).json()["pending"] == empty
        # 会話は reset_chat でだけ空になる（下書きの会話はサーバが持つ — 直接置いて確かめる）
        path = themes._draft_path(tmp_path / "registry", did)
        data = json.loads(path.read_text(encoding="utf-8"))
        said = {"role": "user", "content": "広げて", "at": "t"}
        data["chat"] = {"messages": [said], "proposals": []}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert client.put(url, json=base).json()["chat"]["messages"][0]["content"] == "広げて"
        redo = client.put(url, json={**base, "reset_chat": True, "pending": thin}).json()
        assert "chat" not in redo and redo["pending"] == thin
        assert "chat" not in client.get(url).json()


def test_a_draft_may_start_without_a_name(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d = client.post("/api/themes/drafts", json={"questions": [Q_LOWER]})
        assert d.status_code == 201 and d.json()["label"] == Q_LOWER[:20]
        assert client.post("/api/themes/drafts", json={"questions": []}).status_code == 422


def test_an_old_six_step_draft_reads_as_gone(tmp_path: Path) -> None:
    folder = tmp_path / "registry/vocab-shared/themes/old"
    folder.mkdir(parents=True)
    (folder / "draft.json").write_text(
        json.dumps({"id": "old", "label": "古い", "step": 5, "questions": ["q"],
                    "terms": [], "relations": [], "selections": [], "updated_at": "t"}),
        encoding="utf-8",
    )  # fmt: skip
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/themes/drafts/old").status_code == 404
        assert client.get("/api/themes").json()["drafts"] == []


def test_the_sky_of_drafts_needs_the_token_and_works_with_no_data(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(AI_JSON))
    app, _ = _app(tmp_path, llm)
    with TestClient(app) as anon:
        assert anon.get("/api/themes/drafts/sky").status_code in (401, 503)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        assert client.get("/api/themes/drafts/sky").json() == {"drafts": []}
        # a draft with no concept yet is no card
        client.post("/api/themes/drafts", json={"label": "空", "questions": ["q"]})
        assert client.get("/api/themes/drafts/sky").json() == {"drafts": []}
        draft = _saved_draft(client)  # no dataset anywhere in this store
        out = client.get("/api/themes/drafts/sky").json()
        (d,) = out["drafts"]
        assert d["draft_id"] == draft["id"] and d["theme_label"] == "熱電材料"
        assert d["questions"] == [Q_LOWER]
        assert {"key": "c1", "label": "熱伝導率", "kind": "property",
                "use": "standard:quantitykind:ThermalConductivity"} in d["concepts"]  # fmt: skip
        assert {"from": "c4", "to": "c2", "rel": "lowers"} in d["relations"]
        en = client.get("/api/themes/drafts/sky", params={"lang": "en"}).json()["drafts"][0]
        assert "Thermal Conductivity" in {c["label"] for c in en["concepts"]}
        # the public sky is untouched: a draft is not on it
        # （鍵つきの GET /api/sky は proposed に下書きを足す — 公開の事実は鍵なしの形で見る）
        sky = client.get("/api/sky", headers={"X-Asterism-Token": ""}).json()
        assert "proposed" not in sky
        assert "熱伝導率" not in json.dumps(sky, ensure_ascii=False)


def test_writes_need_the_token(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:  # no token
        assert client.post("/api/themes/drafts", json={"label": "x"}).status_code in (401, 503)
        # 下書きの中身（概念・関係）を読むのも書き込み側の鍵が要る
        assert client.get("/api/themes/drafts/anything").status_code in (401, 503)
        assert client.post("/api/themes/x/commit", json=_cell_body()).status_code in (401, 503)
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
        (q,) = theme["questions"]
        assert q["mode"] == "data" and q["wanted_data"] == "セルごとの 容量 の表"
        assert q["shape"] == (
            "電池セルのうち、容量がいちばん大きいものを出します（データセットごと）"
        )
        assert theme["memo_questions"][0]["title"] == "容量の推移は？"
        # no default question: 電池セル is a kind the 'top' question names, not a question's host
        assert {t["slug"] for t in theme["terms"]} == {"capacity"}
        assert theme["knowledge_total"] == 0
        assert listing["unassigned_questions"] == []
        # the terms carry the draft mark and the aliases (a person's own here: not AI)
        terms = {t["slug"]: t for t in client.get("/api/vocab/shared").json()["terms"]}
        assert terms["battery_cell"]["aliases"] == ["cell"]
        assert terms["battery_cell"]["drafted_by"] is None
        assert [c["slug"] for c in terms["battery_cell"]["cqs"]] == []


def test_a_knowledge_theme_commits_and_answers_know_n_data_n(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d = client.post(
            "/api/themes/drafts", json={"label": "熱電材料", "questions": [Q_LOWER]}
        ).json()
        r = client.post(f"/api/themes/{d['id']}/commit", json=_heat_body())
        assert r.status_code == 201, r.text
        out = r.json()
        assert len(out["minted"]) == 6 and out["lines"] == 6 and out["batch"] == "batch-1"
        assert out["questions"] == ["kq_thermal_conductivity_factors_lowers"]
        # the list: a knowledge question with its sentence and knowledge count
        listing = client.get("/api/themes").json()
        theme = next(t for t in listing["themes"] if t["id"] == d["id"])
        (q,) = theme["questions"]
        assert q["mode"] == "knowledge" and q["knowledge_count"] == 2
        assert q["shape"] == "熱伝導率 を下げるもの" and q["wanted_data"].startswith("試料ごとの")
        # 知識だけで答えた問いも「答えられる」に数える（データ 0・知識 2）
        assert theme["knowledge_total"] == 2 and theme["answerable"] == 1
        assert {t["slug"] for t in theme["terms"]} == {"thermal_conductivity"}
        # the answers: knowledge 2 (one closure step), data 0
        a = client.get(f"/api/themes/{d['id']}/answers").json()
        (ans,) = a["answers"]
        assert ans["tool_name"] == "kq_thermal_conductivity_factors_lowers"
        assert ans["mode"] == "knowledge" and ans["title"] == Q_LOWER
        assert ans["knowledge"]["count"] == 2
        assert ans["data"] == {"answering": 0, "total_datasets": 0}
        assert ans["wanted_data"] == "試料ごとの 粒径・熱伝導率 の表"
        by = {i["label"]: i for i in ans["knowledge"]["items"]}
        assert set(by) == {"粒界散乱", "点欠陥散乱"}
        assert by["粒界散乱"]["reason"] == "粒界でフォノンが散乱される"
        assert by["粒界散乱"]["drafted_by"] == "ai" and by["粒界散乱"]["batch"] == "batch-1"
        assert by["粒界散乱"]["via_label"] == "格子熱伝導率" and by["粒界散乱"]["at"]
        en = client.get(f"/api/themes/{d['id']}/answers", params={"lang": "en"}).json()
        assert {i["label"] for i in en["answers"][0]["knowledge"]["items"]} == {
            "grain boundary scattering", "point defect scattering",
        }  # fmt: skip
        assert en["answers"][0]["shape"] == "What lowers Thermal Conductivity"
        # a dataset's kind joined under a factor does not make it wired: knowledge is no data
        wired = json.loads((tmp_path / "registry/vocab-shared/wired.json").read_text())["terms"]
        client.post(
            "/api/crosswalk/align",
            json={"source": "https://example.org/ds-a#Sample",
                  "target": shared_vocab.SV + "lattice_tc", "relation": "lowers"},
        )  # fmt: skip
        after = json.loads((tmp_path / "registry/vocab-shared/wired.json").read_text())["terms"]
        assert after == wired and set(after.values()) == {0}
        again = client.get(f"/api/themes/{d['id']}/answers").json()["answers"][0]
        assert again["knowledge"]["count"] == 3  # 試料 lowers 格子: now a third factor
        assert again["data"]["answering"] == 0
        assert client.get("/api/themes/ghost/answers").status_code == 404


def test_the_ai_less_path_commits_typed_concepts(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)  # no AI anywhere
    with TestClient(app, headers=_AUTH) as client:
        body = {
            "label": "", "lang": "ja", "ai_drafted": False, "questions": ["電池セルは何件？"],
            "concepts": [{"key": "t1", "label_ja": "電池セル", "kind": "class", "use": "new"}],
            "relations": [],
            "answers": [{"question": "電池セルは何件？", "mode": "data", "op": "count",
                         "kind": "t1"}],
        }  # fmt: skip
        r = client.post("/api/themes/mine/commit", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["minted"] == ["t1"] and r.json()["questions"] == ["cq_t1_count"]
        theme = client.get("/api/themes").json()["themes"][0]
        assert theme["label"] == "電池セルは何件？"


def test_a_refused_commit_is_422_and_writes_nothing(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        client.get("/api/themes")  # migrate first
        before = client.get("/api/vocab/shared").json()
        d = client.post("/api/themes/drafts", json={"label": "電池", "questions": []}).json()
        bad = _cell_body(relations=[{"from": "t1", "rel": "sub", "to": "t2"}])
        r = client.post(f"/api/themes/{d['id']}/commit", json=bad)
        assert r.status_code == 422 and "種類と項目" in r.json()["detail"]
        lone = _heat_body()
        lone["concepts"].append({"key": "c9", "label_ja": "迷子", "label_en": "stray",
                                 "kind": "class", "use": "new", "slug": "stray"})  # fmt: skip
        r2 = client.post(f"/api/themes/{d['id']}/commit", json=lone)
        assert r2.status_code == 422 and "迷子" in r2.json()["detail"]
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


def test_removing_a_theme_takes_back_what_only_it_needed(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d = client.post("/api/themes/drafts", json={"label": "熱電材料", "questions": ["q"]}).json()
        assert client.post(f"/api/themes/{d['id']}/commit", json=_heat_body()).status_code == 201
        # a dataset kind is joined under 粒界散乱: that is the data's use, so it stays
        client.post(
            "/api/crosswalk/align",
            json={"source": "https://example.org/ds-a#GB",
                  "target": shared_vocab.SV + "grain_boundary", "relation": "subClassOf"},
        )  # fmt: skip
        r = client.delete(f"/api/themes/{d['id']}")
        assert r.status_code == 200
        out = r.json()
        assert {c["slug"] for c in out["removed_concepts"]} == {
            "thermal_conductivity", "lattice_tc", "electronic_tc", "point_defect", "carrier_conc",
        }  # fmt: skip
        assert out["kept_concepts"] == [
            {"slug": "grain_boundary", "label": "粒界散乱", "reason": "has_lines"}
        ]
        assert len(out["removed_relations"]) == 6
        terms = {t["slug"] for t in client.get("/api/vocab/shared").json()["terms"]}
        assert terms == {"grain_boundary"}
        assert d["id"] not in {t["id"] for t in client.get("/api/themes").json()["themes"]}
        assert client.delete(f"/api/themes/{d['id']}").status_code == 404


def test_a_question_can_be_added_to_a_theme(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        extra = _cell_body(
            answers=[
                *_cell_body()["answers"],
                {"question": "セルは何件？", "mode": "data", "op": "count", "kind": "t1"},
            ],
            questions=["容量が最も大きいセルは？", "容量の推移は？", "セルは何件？"],
        )
        d, _ = _draft_and_commit(client, extra)
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
            assert zf.namelist() == ["battery_cell.csv"]
            assert zf.read("battery_cell.csv").decode().splitlines() == ["battery_cell_id,capacity"]
        assert client.get("/api/themes/ghost/template.csv.zip").status_code == 404


def test_csv_header_cells_never_start_a_spreadsheet_formula() -> None:
    from asterism_api.themes_routes import _csv_line

    assert _csv_line(["cell_id", "=SUM(A1)", "@x", "+1", "-1", "ok"]) == (
        "cell_id,'=SUM(A1),'@x,'+1,'-1,ok\r\n"
    )


# ---------------------------------------------------------------------------
# データで答える 4 つの形（値で絞る・〜ごと・推移・結合 1 段）
# ---------------------------------------------------------------------------


def _wide_body() -> dict:
    qs = ["組成に SnSe を含む試料は？", "試料は組成ごとに何件？", "論文が報告する試料は？"]

    def c(key: str, ja: str, en: str, kind: str, slug: str) -> dict:
        return {"key": key, "label_ja": ja, "label_en": en, "aliases": [], "kind": kind,
                "use": "new", "slug": slug}  # fmt: skip

    return {
        "label": "熱電",
        "lang": "ja",
        "ai_drafted": False,
        "questions": qs,
        "concepts": [
            c("t1", "試料", "sample", "class", "sample"),
            c("t2", "組成", "composition", "property", "composition"),
            c("t3", "論文", "paper", "class", "paper"),
            c("t4", "報告する", "reports", "property", "reports"),
        ],
        "relations": [],
        "answers": [
            {"question": qs[0], "mode": "data", "op": "filter", "kind": "t1", "property": "t2",
             "value": "SnSe", "match": "contains"},
            {"question": qs[1], "mode": "data", "op": "group", "kind": "t1", "property": "t2"},
            {"question": qs[2], "mode": "data", "op": "join", "kind": "t3", "property": "t4",
             "kind2": "t1"},
        ],
    }  # fmt: skip


def test_wide_shapes_commit_and_answers_carry_the_sentence_and_data_n(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        d, r = _draft_and_commit(client, _wide_body())
        assert r.status_code == 201, r.text
        assert r.json()["questions"] == ["cq_sample_filter", "cq_sample_group", "cq_paper_join"]
        got = client.get(f"/api/themes/{d['id']}/answers").json()["answers"]
        assert [a["shape"] for a in got] == [
            "試料のうち、組成に SnSe を含むもの",
            "試料を 組成 ごとに数える",
            "論文 が 報告する 試料",
        ]
        assert all(a["mode"] == "data" and a["data"]["answering"] == 0 for a in got)
        listing = client.get("/api/themes").json()
        theme = next(t for t in listing["themes"] if t["id"] == d["id"])
        assert [q["shape"] for q in theme["questions"]] == [a["shape"] for a in got]
        # 宣言ツールとして出る（MCP の形は変えない）: for_terms は関わる語の全部
        tools = client.get("/api/vocab/shared").json()
        names = {c["tool_name"] for t in tools["terms"] for c in t["cqs"]}
        assert {"cq_sample_filter", "cq_sample_group", "cq_paper_join"} <= names
        # 実行文を記録していないテーマに足しても、宣言ツールから組み直せる
        other = client.post(
            "/api/themes/drafts", json={"label": "別", "questions": ["q"]}
        ).json()
        client.post(f"/api/themes/{other['id']}/commit", json=_cell_body(label="別"))
        client.post(
            f"/api/themes/{other['id']}/questions",
            json={"tool_names": ["cq_sample_filter", "cq_paper_join"]},
        )
        ans = client.get(f"/api/themes/{other['id']}/answers").json()["answers"]
        shapes = {a["tool_name"]: a["shape"] for a in ans}
        assert shapes["cq_sample_filter"] == "試料のうち、組成に SnSe を含むもの"
        assert shapes["cq_paper_join"] == "論文 が 報告する 試料"


def test_wide_selection_reads_terms_by_iri_not_by_position() -> None:
    """一覧に無い語（ずれの元）があっても、位置でなく IRI で引く。"""
    from asterism_api.themes_routes import _wide_selection

    k, p, p2 = "urn:k", "urn:p", "urn:p2"
    tool = {"name": "cq_curve_trend", "for_terms": [k, p, p2]}
    by = {k: {"label": "曲線", "kind": "class"}, p: {"label": "温度", "kind": "property"},
          p2: {"label": "Seebeck", "kind": "property"}}  # fmt: skip
    assert _wide_selection(tool, by) == {
        "op": "trend", "kind": "曲線", "property": "温度", "property2": "Seebeck",
    }  # fmt: skip
    # 2 つ目の項目だけ一覧に無い: 3 番目の語を「項目」に詰めない
    no_p = {i: v for i, v in by.items() if i != p}
    assert _wide_selection(tool, no_p) == {}
    no_p2 = {i: v for i, v in by.items() if i != p2}
    assert _wide_selection(tool, no_p2) == {"op": "trend", "kind": "曲線", "property": "温度"}
