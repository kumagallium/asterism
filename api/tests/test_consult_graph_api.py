"""作業場の会話の api — ui-redesign PR 7a（``/api/consult/graph`` と apply / dismiss）。

ストアは本物の ``pyoxigraph.Store``（線の書き込み・循環の検査が実際の SPARQL で動く）。AI は注入した
偽物（ネットワークなし）。確かめること: 提案は閉じた差分だけ通る・何も書かない・AI 未設定は 503・
適用は人の「そう」（鍵つき）で決定論の検査を通る・同じ提案は 2 度当たらない・ホームは線に直結して
概念の追加は 422・データは ``upper.json`` に入る・値は prompt に混ざらず列の見出しだけ・却下は
次の適用で効く。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pyoxigraph as ox
from asterism import crosswalk_runtime, shared_vocab, substrate
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import build_app
from asterism_api.upper_routes import dump_upper
from tests.test_crosswalk_api import _AUTH, _settings
from tests.test_themes_api import _LLM, AI_JSON, KEY, Q_LOWER
from tests.test_upper_questions import SV, _dataset, _OxClient

#: 熱伝導率の例のうち、粒界散乱・点欠陥散乱・ナノ析出物が「熱伝導率」そのものを下げる線になっている
#: 下書き（「散乱因子は格子熱伝導率に紐づくはず」と人が言う前）
WRONG = copy.deepcopy(AI_JSON)
WRONG["concepts"].append(
    {"key": "c7", "label_ja": "ナノ析出物", "label_en": "nano precipitate", "aliases": [],
     "kind": "class", "use": "new", "reason": ""}
)  # fmt: skip
WRONG["relations"] = [
    *WRONG["relations"][:2],  # 格子・電子 は 熱伝導率 の一部
    *[
        {"from": k, "rel": "lowers", "to": "c1", "reason": "旧"}
        for k in ("c4", "c5", "c7")
    ],
]
SAY = "散乱因子は格子熱伝導率に紐づくはず"
RETARGETS = {
    "reply": "粒界・点欠陥・ナノ析出物の散乱は、フォノンが運ぶ格子の分を下げます。",
    "ops": [
        {"op": "retarget", "from": k, "rel": "lowers", "to": "c1", "new_to": "c2",
         "reason": "フォノンの散乱"}
        for k in ("c4", "c5", "c7")
    ],
}  # fmt: skip


def _app(tmp_path: Path, llm: _LLM | None = None):
    store = ox.Store()
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_OxClient(store),
        start_watcher=False,
        llm_factory=(lambda key: llm) if llm is not None else None,
    )
    return app, store


def _theme_draft(client: TestClient) -> dict:
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


def _propose(client: TestClient, screen: str, context: dict, say: str = SAY, **kw):
    return client.post(
        "/api/consult/graph",
        json={"screen": screen, "context": context,
              "messages": [{"role": "user", "content": say}], **kw},
    )  # fmt: skip


def _rels(draft: dict, frm: str, rel: str) -> list[str]:
    return [r["to"] for r in draft["relations"] if r["from"] == frm and r["rel"] == rel]


def _by_label(draft: dict) -> dict[str, str]:
    return {c["label_ja"]: c["key"] for c in draft["concepts"]}


def _mint(client: TestClient, slug: str, label: str, kind: str = "property") -> None:
    cq = (
        {"title": f"{label}は、どのデータセットに何件あるか", "op": "count"}
        if kind == "class"
        else {"title": f"{label}の値は、どのデータセットにいくつあるか", "op": "values"}
    )
    r = client.post(
        "/api/vocab/shared", json={"slug": slug, "kind": kind, "label_ja": label, "cqs": [cq]}
    )
    assert r.status_code == 201, r.text


def _lines(store: ox.Store) -> list[dict]:
    return asyncio.run(crosswalk_runtime.list_alignments(_OxClient(store)))


# ---- theme -----------------------------------------------------------------------------------


def test_propose_then_apply_moves_the_lines_of_a_theme_draft(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(WRONG), json.dumps(RETARGETS))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        key = _by_label(draft)
        assert _rels(draft, key["粒界散乱"], "lowers") == [key["熱伝導率"]]
        assert key["格子熱伝導率"] == "c2"  # the model answers with the screen's keys

        r = _propose(client, "theme", {"draft_id": draft["id"]})
        assert r.status_code == 200, r.text
        out = r.json()
        assert set(out) == {"reply", "ops", "sentences", "proposal_id"}
        assert [o["op"] for o in out["ops"]] == ["retarget"] * 3
        assert len(out["proposal_id"]) == 12
        assert out["sentences"][0] == "粒界散乱 は 格子熱伝導率 を下げる に付け替え"
        assert out["reply"].startswith("粒界・点欠陥")
        # nothing was written by the proposal
        got = client.get(f"/api/themes/drafts/{draft['id']}").json()
        assert _rels(got, key["粒界散乱"], "lowers") == [key["熱伝導率"]]
        assert "chat" not in got

        a = client.post(
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": {"draft_id": draft["id"]}, "ops": out["ops"],
                  "proposal_id": out["proposal_id"],
                  "messages": [{"role": "user", "content": SAY},
                               {"role": "assistant", "content": "AI の返事は残さない"}]},
        )  # fmt: skip
        assert a.status_code == 200, a.text
        res = a.json()
        assert (res["applied"], res["dropped"]) == (3, [])
        moved = res["draft"]
        names = ("粒界散乱", "点欠陥散乱", "ナノ析出物")
        assert [_rels(moved, key[n], "lowers") for n in names] == [["c2"], ["c2"], ["c2"]]
        assert all(r["by"] == "ai" for r in moved["relations"] if r["rel"] == "lowers")
        # the conversation kept with the draft: the person's words and the accepted diff only
        assert [m["content"] for m in moved["chat"]["messages"]] == [SAY]
        (proposal,) = moved["chat"]["proposals"]
        assert proposal["proposal_id"] == out["proposal_id"] and proposal["ops"] == out["ops"]
        assert proposal["by"] == "ai" and proposal["at"]
        assert "AI の返事" not in json.dumps(moved, ensure_ascii=False)
        assert client.get(f"/api/themes/drafts/{draft['id']}").json() == moved

        # the same proposal is applied only once
        again = client.post(
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": {"draft_id": draft["id"]}, "ops": out["ops"],
                  "proposal_id": out["proposal_id"]},
        ).json()  # fmt: skip
        assert (again["applied"], again["duplicate"]) == (0, True)
        assert again["draft"]["chat"] == moved["chat"]

        # an ordinary save of the rows (the wizard's PUT) does not lose the conversation
        rows = {k: moved[k] for k in ("label", "step", "questions", "concepts", "relations",
                                      "answers", "suggested_questions", "standards")}  # fmt: skip
        saved = client.put(f"/api/themes/drafts/{draft['id']}", json=rows).json()
        assert saved["chat"] == moved["chat"]


def test_the_prompt_carries_names_only_and_the_models_ops_are_not_trusted(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(WRONG), json.dumps(RETARGETS))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        assert _propose(client, "theme", {"draft_id": draft["id"]}).status_code == 200
        system, user = llm.calls[-1]
        assert "c4 | 粒界散乱 | grain boundary scattering | class" in user
        assert f"# The person now says\n{SAY}" in user
        assert "Do NOT write SPARQL" in system
        # a diff the client makes up is checked at apply time, like the model's
        bad = [{"op": "add_relation", "from": "c1", "rel": "eq", "to": "http://x.org/a",
                "reason": ""}]  # fmt: skip
        r = client.post(
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": {"draft_id": draft["id"]}, "ops": bad,
                  "proposal_id": "p1"},
        )  # fmt: skip
        assert r.status_code == 422
        unknown = [{"op": "run_sparql", "query": "DELETE WHERE {}"}]
        r2 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": {"draft_id": draft["id"]}, "ops": unknown,
                  "proposal_id": "p2"},
        )  # fmt: skip
        assert r2.status_code == 422


def test_an_off_shape_answer_is_retried_once_then_502(tmp_path: Path) -> None:
    junk = json.dumps({"reply": "", "ops": [{"op": "add_relation", "from": "zz", "rel": "eq",
                                             "to": "c1", "reason": ""}]})  # fmt: skip
    llm = _LLM(json.dumps(WRONG), junk, junk)
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        r = _propose(client, "theme", {"draft_id": draft["id"]})
        assert r.status_code == 502 and "決まった形" in r.json()["detail"]
        assert len(llm.calls) == 3  # the draft + the answer + the one retry


def test_without_an_ai_it_is_503_and_a_missing_context_is_404_422(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)  # no llm_factory, no key, no server key
    with TestClient(app, headers=_AUTH) as client:
        r = _propose(client, "theme", {"draft_id": "x"})
        assert r.status_code == 503 and "AI" in r.json()["detail"]
    llm = _LLM()
    app2, _ = _app(tmp_path / "b", llm)
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        assert _propose(client, "theme", {"draft_id": "ghost"}).status_code == 404
        assert _propose(client, "theme", {}).status_code == 422
        assert _propose(client, "dataset", {"dataset_id": "nope"}).status_code == 404
        # the last turn has to be the person's
        r = client.post(
            "/api/consult/graph",
            json={"screen": "home", "messages": [{"role": "assistant", "content": "x"}]},
        )
        assert r.status_code == 400
    assert llm.calls == []


def test_writes_need_the_token_but_a_home_proposal_does_not(tmp_path: Path) -> None:
    llm = _LLM(json.dumps({"reply": "同じ意味です。", "ops": []}))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=KEY) as anon:  # an AI key, no token
        r = _propose(anon, "home", {})
        assert r.status_code == 200 and r.json()["ops"] == []
        # a theme / dataset proposal hands a draft / a dataset's headings to the model → a token
        assert _propose(anon, "theme", {"draft_id": "x"}).status_code == 401
        assert _propose(anon, "dataset", {"dataset_id": "x"}).status_code == 401
        assert llm.calls == [llm.calls[0]]  # only the home one reached the model
        body = {"screen": "home", "context": {}, "ops": [], "proposal_id": "p"}
        assert anon.post("/api/consult/graph/apply", json=body).status_code == 401
        dismiss = {"screen": "home", "context": {}, "ops": []}
        assert anon.post("/api/consult/graph/dismiss", json=dismiss).status_code == 401


def test_a_cycle_a_kind_mismatch_and_a_dismissed_line_are_dropped_with_reasons(
    tmp_path: Path,
) -> None:
    llm = _LLM(json.dumps(WRONG))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        key = _by_label(draft)
        ctx = {"draft_id": draft["id"]}
        cycle = {"op": "add_relation", "from": key["熱伝導率"], "rel": "part",
                 "to": key["格子熱伝導率"], "reason": "逆"}  # fmt: skip
        mixed = {"op": "add_relation", "from": key["粒界散乱"], "rel": "part",
                 "to": key["格子熱伝導率"], "reason": ""}  # fmt: skip
        want = {"op": "add_relation", "from": key["点欠陥散乱"], "rel": "lowers",
                "to": key["電子熱伝導率"], "reason": "電子も散乱される"}  # fmt: skip
        # ✕ on `want` saves a dismissal …
        d = client.post("/api/consult/graph/dismiss",
                        json={"screen": "theme", "context": ctx, "ops": [want]})  # fmt: skip
        assert d.status_code == 200 and d.json() == {"dismissed": 1}
        assert client.post("/api/consult/graph/dismiss",
                           json={"screen": "theme", "context": ctx, "ops": [want]}
                           ).json() == {"dismissed": 0}  # idempotent  # fmt: skip
        # … and the apply step then drops it, next to the ones the tables refuse
        r = client.post(
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": ctx, "ops": [cycle, mixed, want],
                  "proposal_id": "p9"},
        )  # fmt: skip
        res = r.json()
        assert res["applied"] == 0
        assert [(d["index"], d["reason"]) for d in res["dropped"]] == [
            (0, "cycle"), (1, "mixed"), (2, "dismissed"),
        ]  # fmt: skip
        assert res["draft"]["relations"] == draft["relations"]
        assert "chat" not in client.get(f"/api/themes/drafts/{draft['id']}").json()  # nothing saved


def test_add_question_and_add_concept_reach_the_draft(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(WRONG))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        ctx = {"draft_id": draft["id"]}
        ops = [
            {"op": "add_concept", "key": "n1", "label_ja": "結晶粒径", "label_en": "grain size",
             "aliases": [], "kind": "property", "use": "new", "reason": "粒界の密度を決める"},
            {"op": "add_relation", "from": "n1", "rel": "affects", "to": "c2",
             "reason": "粒径で変わる"},
            {"op": "add_question", "question": "粒径は格子熱伝導率にどう効く？",
             "mode": "knowledge", "op_kind": "factors", "target": "c2", "direction": "affects",
             "kind": None, "property": None, "wanted_data": "試料ごとの粒径と熱伝導率の表",
             "reason": "会話から"},
        ]  # fmt: skip
        res = client.post(
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": ctx, "ops": ops, "proposal_id": "p3",
                  "drafted_by": "human", "batch": "b-1"},
        ).json()  # fmt: skip
        assert (res["applied"], res["dropped"]) == (3, [])
        d = res["draft"]
        assert d["concepts"][-1]["label_ja"] == "結晶粒径" and d["concepts"][-1]["by"] == "human"
        assert d["questions"][-1] == "粒径は格子熱伝導率にどう効く？"
        last = d["answers"][-1]
        assert (last["mode"], last["op"], last["target"]) == ("knowledge", "factors", "c2")
        assert last["wanted_data"] == "試料ごとの粒径と熱伝導率の表"
        assert d["chat"]["proposals"][0]["by"] == "human"


# ---- home ------------------------------------------------------------------------------------


def _home_terms(client: TestClient) -> None:
    _mint(client, "thermal", "熱伝導率")
    _mint(client, "lattice", "格子熱伝導率")
    _mint(client, "scatter", "粒界散乱", kind="class")


def test_home_applies_lines_straight_to_the_alignment_graph_with_provenance(
    tmp_path: Path,
) -> None:
    add = [
        {"op": "add_relation", "from": "lattice", "rel": "part", "to": "thermal",
         "reason": "熱伝導率 = 格子 + 電子"},
        {"op": "add_relation", "from": "scatter", "rel": "lowers", "to": "lattice",
         "reason": "粒界でフォノンが散乱される"},
    ]  # fmt: skip
    llm = _LLM(json.dumps({"reply": "格子の分は熱伝導率の一部です。", "ops": add}))
    app, store = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _home_terms(client)
        out = _propose(client, "home", {}, "格子熱伝導率は熱伝導率の一部では？").json()
        assert out["sentences"] == [
            "格子熱伝導率 は 熱伝導率 の一部",
            "粒界散乱 は 格子熱伝導率 を下げる",
        ]
        system, user = llm.calls[0]
        assert "lattice | 格子熱伝導率 | " in user and "Never add a" in system
        assert _lines(store) == []  # the proposal wrote nothing

        res = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": out["ops"], "batch": "b-home",
                  "proposal_id": out["proposal_id"]},
        ).json()  # fmt: skip
        assert (res["applied"], res["dropped"]) == (2, [])
    rows = {(r["source"], r["relation"], r["target"]): r for r in _lines(store)}
    part = rows[(SV + "lattice", "isPartOf", SV + "thermal")]
    assert (part["drafted_by"], part["batch"], part["reason"]) == (
        "ai", "b-home", "熱伝導率 = 格子 + 電子",
    )  # fmt: skip
    assert (SV + "scatter", "lowers", SV + "lattice") in rows

    # a cycle in part-of, and a part-of between a class and a property, are refused; remove works
    llm2 = _LLM(
        json.dumps(
            {"reply": "", "ops": [
                {"op": "add_relation", "from": "thermal", "rel": "part", "to": "lattice",
                 "reason": "逆"},
                {"op": "add_relation", "from": "scatter", "rel": "part", "to": "lattice",
                 "reason": ""},
                {"op": "remove", "target": "relation", "from": "scatter", "rel": "lowers",
                 "to": "lattice", "reason": "違った"},
            ]}
        )
    )  # fmt: skip
    app2 = build_app(
        _settings(tmp_path),
        oxigraph_client=_OxClient(store),
        start_watcher=False,
        llm_factory=lambda key: llm2,
    )
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        out2 = _propose(client, "home", {}, "逆でしょ").json()
        res2 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": out2["ops"],
                  "proposal_id": out2["proposal_id"]},
        ).json()  # fmt: skip
    assert res2["applied"] == 1
    assert [(d["index"], d["reason"]) for d in res2["dropped"]] == [(0, "cycle"), (1, "mixed")]
    left = {(r["source"], r["relation"], r["target"]) for r in _lines(store)}
    assert (SV + "scatter", "lowers", SV + "lattice") not in left
    assert (SV + "lattice", "isPartOf", SV + "thermal") in left


def test_home_does_not_add_concepts_and_only_takes_lines(tmp_path: Path) -> None:
    app, store = _app(tmp_path, _LLM())
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _home_terms(client)
        concept = {"op": "add_concept", "key": "n1", "label_ja": "新しい語", "label_en": "",
                   "aliases": [], "kind": "class", "use": "new", "reason": ""}  # fmt: skip
        r = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [concept], "proposal_id": "p"},
        )
        assert r.status_code == 422 and "知りたいこと" in r.json()["detail"]
        rename = {"op": "rename", "key": "thermal", "label_ja": "熱伝導度", "label_en": "",
                  "aliases": [], "reason": ""}  # fmt: skip
        res = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [rename], "proposal_id": "q"},
        ).json()  # fmt: skip
        assert (res["applied"], [d["reason"] for d in res["dropped"]]) == (0, ["unsupported"])
        # the dismissal of a line saves the pair, so the next apply drops it
        line = {"op": "add_relation", "from": "lattice", "rel": "eq", "to": "thermal",
                "reason": ""}  # fmt: skip
        d = client.post("/api/consult/graph/dismiss",
                        json={"screen": "home", "context": {}, "ops": [line]})  # fmt: skip
        assert d.json() == {"dismissed": 1}
        res2 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [line], "proposal_id": "r"},
        ).json()  # fmt: skip
        assert [d["reason"] for d in res2["dropped"]] == ["dismissed"]
    assert _lines(store) == []


# ---- dataset ---------------------------------------------------------------------------------


def test_dataset_binds_a_column_into_upper_json_and_the_prompt_has_headings_only(
    tmp_path: Path,
) -> None:
    sentinel = "SENTINEL-VALUE-91827"
    bind = {"op": "bind_column", "column": "price", "to": "cost", "rel": "eq", "reason": "価格の列"}
    llm = _LLM(json.dumps({"reply": "価格の列は費用です。", "ops": [bind]}))
    app, store = _app(tmp_path, llm)
    dsid = _dataset(tmp_path)
    # a value of the dataset sits in the store — it must never reach the model
    store.add(ox.Quad(
        ox.NamedNode("https://example.org/resource/sample/s1"),
        ox.NamedNode("https://example.org/onto#price"),
        ox.Literal(sentinel),
        ox.NamedNode(substrate.versioned_graph_iri(dsid, 1)),
    ))  # fmt: skip
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "cost", "費用")
        _mint(client, "specimen", "試料", kind="class")
        ctx = {"dataset_id": dsid}
        out = _propose(client, "dataset", ctx, "price 列は費用のこと？").json()
        assert out["ops"] == [{**bind, "op": "bind_column"}]
        assert out["sentences"] == ["列「price」 と 費用 は同じもの"]
        system, user = llm.calls[0]
        assert "# Column headings" in user and "price | " in user and "weight | " in user
        assert "価格" in user  # the label set in the design is a name, not a value
        assert sentinel not in system + user
        assert "SampleMap" not in user  # not even the map's name is needed

        res = client.post(
            "/api/consult/graph/apply",
            json={"screen": "dataset", "context": ctx, "ops": out["ops"],
                  "proposal_id": out["proposal_id"]},
        ).json()  # fmt: skip
        assert (res["applied"], res["dropped"]) == (1, [])
        upper = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        assert upper == [
            {"subject": "property:SampleMap/price", "term": SV + "cost",
             "relation": "equivalentProperty", "applied_at": None}
        ]  # fmt: skip
        # the same binding again is a duplicate; a class, a quantity kind and a stranger are refused
        more = [
            bind,
            {**bind, "to": "specimen"},
            {**bind, "to": "quantitykind:Temperature"},
            {**bind, "rel": "quantity", "to": "cost"},
            {**bind, "column": "nope"},
            {"op": "remove", "target": "concept", "key": "cost", "reason": ""},
        ]
        res2 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "dataset", "context": ctx, "ops": more, "proposal_id": "p2"},
        ).json()  # fmt: skip
        assert res2["applied"] == 0
        assert [(d["index"], d["reason"]) for d in res2["dropped"]] == [
            (0, "duplicate"), (1, "mixed"), (2, "mixed"), (3, "unsupported"),
            (4, "unknown_key"), (5, "unsupported"),
        ]  # fmt: skip
        # ✕ on a column → the pair is saved and the apply step refuses it
        sub = {**bind, "column": "weight", "rel": "sub"}
        assert client.post("/api/consult/graph/dismiss",
                           json={"screen": "dataset", "context": ctx, "ops": [sub]}
                           ).json() == {"dismissed": 1}  # fmt: skip
        res3 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "dataset", "context": ctx, "ops": [sub], "proposal_id": "p3"},
        ).json()  # fmt: skip
        assert [d["reason"] for d in res3["dropped"]] == ["dismissed"]
        assert len(client.get(f"/api/datasets/{dsid}/upper").json()["upper"]) == 1
    assert shared_vocab.load_dismissed(tmp_path / "registry")[0]["subject"] == (
        "property:SampleMap/weight"
    )


# ---- 7a の指摘: 冪等・認証・画面での絞り込み ---------------------------------------------------


def test_a_theme_proposal_needs_the_token_and_without_a_token_configured_it_is_503(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path, _LLM())
    settings = _settings(tmp_path / "x")
    settings.api_token = None
    app2 = build_app(settings, oxigraph_client=_OxClient(ox.Store()), start_watcher=False)
    with TestClient(app, headers=KEY) as anon:
        assert _propose(anon, "theme", {"draft_id": "x"}).status_code == 401
    with TestClient(app2, headers=KEY) as closed:  # no token configured → the route is closed
        assert _propose(closed, "theme", {"draft_id": "x"}).status_code == 503


def test_the_proposal_is_cut_to_what_each_screen_takes(tmp_path: Path) -> None:
    rename = {"op": "rename", "key": "thermal", "label_ja": "熱", "label_en": "", "aliases": [],
              "reason": ""}  # fmt: skip
    line = {"op": "add_relation", "from": "lattice", "rel": "part", "to": "thermal", "reason": ""}
    llm = _LLM(json.dumps({"reply": "", "ops": [rename, line]}))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _home_terms(client)
        out = _propose(client, "home", {}, "直して").json()
    assert [o["op"] for o in out["ops"]] == ["add_relation"] and len(out["sentences"]) == 1


def test_a_replayed_theme_diff_is_a_no_op_on_the_current_draft_not_a_422(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(WRONG))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        ctx = {"draft_id": draft["id"]}
        add = {"op": "add_concept", "key": "n1", "label_ja": "結晶粒径", "label_en": "grain size",
               "aliases": [], "kind": "property", "use": "new", "reason": ""}  # fmt: skip
        rel = {"op": "add_relation", "from": "n1", "rel": "affects", "to": "c2", "reason": ""}
        bad = {"op": "add_relation", "from": "c4", "rel": "part", "to": "c2", "reason": ""}
        post = lambda ops, pid: client.post(  # noqa: E731
            "/api/consult/graph/apply",
            json={"screen": "theme", "context": ctx, "ops": ops, "proposal_id": pid},
        )
        first = post([add, rel, bad], "p1").json()
        assert (first["applied"], [d["reason"] for d in first["dropped"]]) == (2, ["mixed"])
        # the history keeps only what was applied (the refused line is not in it)
        (proposal,) = first["draft"]["chat"]["proposals"]
        assert [o["op"] for o in proposal["ops"]] == ["add_concept", "add_relation"]

        # the same ops again — and under another id — are duplicates (the key n1 is taken: not 422)
        for pid in ("p1", "p2"):
            again = post([add, rel], pid)
            assert again.status_code == 200, again.text
            body = again.json()
            assert (body["applied"], body["duplicate"]) == (0, True)
            assert [d["reason"] for d in body["dropped"]] == ["duplicate", "duplicate"]
            assert body["draft"]["chat"] == first["draft"]["chat"]  # nothing saved

        # an old proposal id is not a block: once the line is gone, the same ops apply again
        gone = {"op": "remove", "target": "relation", "from": "n1", "rel": "affects", "to": "c2",
                "reason": ""}  # fmt: skip
        assert post([gone], "p3").json()["applied"] == 1
        back = post([rel], "p1").json()
        assert (back["applied"], "duplicate" in back) == (1, False)
        # removing what is not there is not_found
        res = post([gone, gone], "p4").json()
        assert (res["applied"], [d["reason"] for d in res["dropped"]]) == (1, ["not_found"])
        # a retarget of a line that is not there at all is not_found, and not a duplicate
        miss = {"op": "retarget", "from": "c4", "rel": "lowers", "to": "c3", "new_to": "c2",
                "reason": ""}  # fmt: skip
        res2 = post([miss], "p5").json()
        assert [d["reason"] for d in res2["dropped"]] == ["not_found"] and "duplicate" not in res2


def test_home_keeps_the_provenance_of_a_line_that_is_already_there(tmp_path: Path) -> None:
    app, store = _app(tmp_path, _LLM())
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _home_terms(client)
        part = {"op": "add_relation", "from": "lattice", "rel": "part", "to": "thermal",
                "reason": "人の理由"}  # fmt: skip

        def post(ops, pid, **kw):
            return client.post(
                "/api/consult/graph/apply",
                json={"screen": "home", "context": {}, "ops": ops, "proposal_id": pid, **kw},
            ).json()

        first = post([part], "a", batch="b-human", drafted_by="human")
        assert first["applied"] == 1
        again = post([{**part, "reason": "AI の別の理由"}], "b", batch="b-ai")
        assert (again["applied"], again["duplicate"]) == (0, True)
        assert [d["reason"] for d in again["dropped"]] == ["duplicate"]
        row = {(r["source"], r["relation"], r["target"]): r for r in _lines(store)}[
            (SV + "lattice", "isPartOf", SV + "thermal")
        ]
        assert (row["drafted_by"], row["batch"], row["reason"]) == ("human", "b-human", "人の理由")

        # a mix: the line that is there is dropped, the new one is written
        scatter = {"op": "add_relation", "from": "scatter", "rel": "lowers", "to": "lattice",
                   "reason": ""}  # fmt: skip
        mix = post([part, scatter], "c")
        assert mix["applied"] == 1 and "duplicate" not in mix
        assert [(d["index"], d["reason"]) for d in mix["dropped"]] == [(0, "duplicate")]

        # eq is the same line in both directions
        eq = {"op": "add_relation", "from": "thermal", "rel": "eq", "to": "lattice", "reason": ""}
        assert post([eq], "d")["applied"] == 1
        back = post([{**eq, "from": "lattice", "to": "thermal"}], "e")
        assert (back["applied"], back["duplicate"]) == (0, True)

        # remove: a line that is not there is not_found; one that is there goes
        gone = {"op": "remove", "target": "relation", "from": "scatter", "rel": "lowers",
                "to": "lattice", "reason": ""}  # fmt: skip
        nope = {**gone, "from": "lattice", "to": "scatter"}
        res = post([nope, gone], "f")
        assert (res["applied"], [d["reason"] for d in res["dropped"]]) == (1, ["not_found"])
        assert post([gone], "g")["dropped"][0]["reason"] == "not_found"
        # removing an eq line by the other direction removes it
        res2 = post([{**gone, "from": "lattice", "rel": "eq", "to": "thermal"}], "h")
        assert res2["applied"] == 1
    assert (SV + "thermal", "equivalentProperty", SV + "lattice") not in {
        (r["source"], r["relation"], r["target"]) for r in _lines(store)
    }


def test_batch_is_one_short_line(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _LLM())
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        def post(batch):
            return client.post(
                "/api/consult/graph/apply",
                json={"screen": "home", "context": {}, "ops": [], "proposal_id": "p",
                      "batch": batch},
            ).status_code
        assert post("b-1") == 200
        assert post("x" * 101) == 422
        assert post("a\nb") == 422
        assert post("a\x00b") == 422
        assert post("a\u2028b") == 422


def test_dismiss_checks_every_key_before_it_saves_any(tmp_path: Path, monkeypatch) -> None:
    app, _ = _app(tmp_path, _LLM())
    real = shared_vocab.dismiss_fit

    def picky(root, subject, term, **kw):
        if term.endswith("thermal"):
            raise ValueError("refused")
        return real(root, subject, term, **kw)

    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _home_terms(client)
        monkeypatch.setattr(shared_vocab, "dismiss_fit", picky)
        first = {"op": "add_relation", "from": "scatter", "rel": "lowers", "to": "lattice",
                 "reason": ""}  # fmt: skip
        second = {"op": "add_relation", "from": "lattice", "rel": "part", "to": "thermal",
                  "reason": ""}  # fmt: skip
        r = client.post("/api/consult/graph/dismiss",
                        json={"screen": "home", "context": {}, "ops": [first, second]})  # fmt: skip
        assert r.status_code == 422
        assert shared_vocab.load_dismissed(tmp_path / "registry") == []  # not even the first
        monkeypatch.setattr(shared_vocab, "dismiss_fit", real)
        both = {"screen": "home", "context": {}, "ops": [first, second]}
        assert client.post("/api/consult/graph/dismiss", json=both).json() == {"dismissed": 2}


def test_dataset_apply_keeps_the_skipped_reason_of_other_lines(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _LLM())
    dsid = _dataset(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "cost", "費用")
        _mint(client, "mass", "質量")
        old = {"subject": "property:SampleMap/weight", "term": SV + "mass",
               "relation": "equivalentProperty", "applied_at": None,
               "skipped": "subject missing"}  # fmt: skip
        registry.write_artifact(tmp_path / "registry", dsid, "upper.json", dump_upper([old]))
        bind = {"op": "bind_column", "column": "price", "to": "cost", "rel": "eq", "reason": ""}
        r = client.post(
            "/api/consult/graph/apply",
            json={"screen": "dataset", "context": {"dataset_id": dsid}, "ops": [bind],
                  "proposal_id": "p"},
        ).json()  # fmt: skip
        assert r["applied"] == 1
        upper = {u["subject"]: u for u in client.get(f"/api/datasets/{dsid}/upper").json()["upper"]}
        assert upper["property:SampleMap/weight"]["skipped"] == "subject missing"
        assert "skipped" not in upper["property:SampleMap/price"]
        # the same binding again → duplicate, and the whole call is a no-op
        again = client.post(
            "/api/consult/graph/apply",
            json={"screen": "dataset", "context": {"dataset_id": dsid}, "ops": [bind],
                  "proposal_id": "p2"},
        ).json()  # fmt: skip
        assert (again["applied"], again["duplicate"]) == (0, True)
