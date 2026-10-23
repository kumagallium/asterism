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
from tests.test_upper_questions import EX, SV, _dataset, _OxClient, _stage

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


def test_an_off_shape_answer_is_retried_twice_then_502(tmp_path: Path) -> None:
    # 11n で聞き直しが 2 回（計 3 回の呼び出し）になった
    junk = json.dumps({"reply": "", "ops": [{"op": "add_relation", "from": "zz", "rel": "eq",
                                             "to": "c1", "reason": ""}]})  # fmt: skip
    llm = _LLM(json.dumps(WRONG), junk, junk, junk)
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        draft = _theme_draft(client)
        r = _propose(client, "theme", {"draft_id": draft["id"]})
        assert r.status_code == 502 and "決まった形" in r.json()["detail"]
        assert len(llm.calls) == 4  # the draft + the answer + two retries


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


def test_home_takes_published_kinds_as_the_end_of_a_line_and_never_shows_their_iris(
    tmp_path: Path,
) -> None:
    """A1: 公開済みの種類（星）は短い鍵 ``kind:…`` で AI に見せる（IRI は渡さない）。線の元に使え、
    適用は alignment graph に種類の IRI のまま・来歴つきで書く。公開済みでない鍵は 422。"""
    from asterism.graph_diff import kind_key

    key = kind_key(EX + "Sample")
    assert key.startswith("kind:") and "http" not in key and len(key) < 20
    same = {"op": "add_relation", "from": key, "rel": "eq", "to": "specimen", "reason": "同じ試料"}
    llm = _LLM(json.dumps({"reply": "同じものと見てよさそうです。", "ops": [same]}))
    app, store = _app(tmp_path, llm)
    dsid = _dataset(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "specimen", "試料の語", kind="class")  # 名前が違う — 完全一致の候補ではない
        assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200
        out = _propose(
            client, "home", {"focus_kind": EX + "Sample"}, "この種類は 試料の語 と同じもの？"
        ).json()
        assert out["ops"] == [same]
        assert out["sentences"] == ["試料 と 試料の語 は同じもの"]
        system, user = llm.calls[0]
        assert f"{key} | 試料 |" in user and "the one the person is looking at now" in user
        assert 'dataset "' in user and "records" in user  # どのデータセットの何件の種類か
        assert EX not in system + user  # IRI は AI に渡さない
        assert _lines(store) == []

        res = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": out["ops"], "batch": "b-kind",
                  "proposal_id": out["proposal_id"]},
        ).json()  # fmt: skip
        assert (res["applied"], res["dropped"]) == (1, [])
        assert res["lines"] == [  # 画面が星図を取り直し、この 2 点に輪を出す
            {"action": "add", "source": EX + "Sample", "target": SV + "specimen",
             "relation": "equivalentClass"}
        ]  # fmt: skip
        line = next(ln for ln in _lines(store) if ln["source"] == EX + "Sample")
        assert (line["target"], line["relation"]) == (SV + "specimen", "equivalentClass")
        assert (line["drafted_by"], line["batch"], line["reason"]) == ("ai", "b-kind", "同じ試料")
        # 同じ線をもう一度 → duplicate。公開済みでない鍵は検査で 422、項目の語への種類は mixed
        dup = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [same], "proposal_id": "again"},
        ).json()
        assert dup["duplicate"] is True
        ghost = {**same, "from": "kind:0000000000"}
        bad = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [ghost], "proposal_id": "g"},
        )
        assert bad.status_code == 422
        # ✕ は種類の IRI で保存され、次の適用で効く
        other = {**same, "to": "specimen", "rel": "sub", "reason": ""}
        assert client.post("/api/consult/graph/dismiss",
                           json={"screen": "home", "context": {}, "ops": [other]}
                           ).json() == {"dismissed": 1}  # fmt: skip
        res2 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [other], "proposal_id": "o"},
        ).json()
        assert [d["reason"] for d in res2["dropped"]] == ["dismissed"]
        # 外す（線は種類の IRI のまま消える）
        rm = {"op": "remove", "target": "relation", "from": key, "rel": "eq", "to": "specimen",
              "reason": "違った"}  # fmt: skip
        res3 = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "ops": [rm], "proposal_id": "r"},
        ).json()
        assert res3["applied"] == 1
    assert [ln for ln in _lines(store) if ln["source"] == EX + "Sample"] == []
    dismissed = shared_vocab.load_dismissed(tmp_path / "registry")
    assert [(d["subject"], d["term"], d.get("relation")) for d in dismissed] == [
        (EX + "Sample", SV + "specimen", "subClassOf")
    ]


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
                  "proposal_id": out["proposal_id"], "batch": "b-ds"},
        ).json()  # fmt: skip
        assert (res["applied"], res["dropped"]) == (1, [])
        upper = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        assert upper == [
            {"subject": "property:SampleMap/price", "term": SV + "cost",
             "relation": "equivalentProperty", "applied_at": None,
             "drafted_by": "ai", "batch": "b-ds", "reason": "価格の列"}
        ]  # fmt: skip (the provenance rides to the publication — see test_upper_provenance)
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


def test_upper_provenance_rides_from_the_chat_to_the_published_line(tmp_path: Path) -> None:
    """C2: 会話で受けた当てはめの来歴（drafted_by・batch・reason）は ``upper.json`` に残り、表からの
    PUT で落ちず、公開で ``assert_alignment`` に届く。表で直に受けた線は従来どおり ``human``。"""
    from tests.test_upper_questions import _stage

    bind = {"op": "bind_column", "column": "price", "to": "cost", "rel": "sub",
            "reason": "価格の列"}  # fmt: skip
    app, store = _app(tmp_path, _LLM())
    dsid = _dataset(tmp_path)
    _stage(tmp_path, store, dsid)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "cost", "費用")
        ctx = {"dataset_id": dsid}
        res = client.post(
            "/api/consult/graph/apply",
            json={"screen": "dataset", "context": ctx, "ops": [bind], "proposal_id": "p",
                  "batch": "b-ds", "drafted_by": "ai"},
        ).json()  # fmt: skip
        assert res["applied"] == 1
        hand = {"subject": "property:SampleMap/weight", "term": SV + "cost",
                "relation": "equivalentProperty"}  # fmt: skip
        # 表の PUT は来歴を知らない（送らない）— 既存の線の来歴は落ちない
        cur = client.get(f"/api/datasets/{dsid}/upper").json()["upper"]
        plain = [{k: v for k, v in u.items() if k in ("subject", "term", "relation")} for u in cur]
        put = client.put(f"/api/datasets/{dsid}/upper", json={"upper": [*plain, hand]}).json()
        rows = {u["subject"]: u for u in put["upper"]}
        assert rows["property:SampleMap/price"]["drafted_by"] == "ai"
        assert "drafted_by" not in rows["property:SampleMap/weight"]

        assert client.post(f"/api/datasets/{dsid}/promote").status_code == 200
    lines = {ln["source"]: ln for ln in _lines(store)}
    price = lines[EX + "price"]
    assert (price["relation"], price["drafted_by"], price["batch"], price["reason"]) == (
        "subPropertyOf", "ai", "b-ds", "価格の列",
    )  # fmt: skip
    weight = lines[EX + "weight"]
    assert (weight["drafted_by"], weight["batch"]) == ("human", None)


# ---- suggest questions (PR 10a) ----------------------------------------------------------------

_PRICES_IR = """\
version: 1
prefixes:
  prices: "https://example.org/prices#"
  pricesr: "https://example.org/prices/r/"
maps:
  - name: record
    source: prices.csv
    subject:
      template: "pricesr:record/{no}"
      classes: [prices:Record]
      label: "値段の記録"
    properties:
      - {predicate: "prices:no", column: "no", label: "行の通し番号"}
      - {predicate: "prices:price", column: price, datatype: "xsd:integer",
         label: "1 つあたりの値段"}
      - {predicate: "prices:shop", column: shop, label: "店の名前"}
      - {predicate: "prices:date", column: date, label: "値段を調べた日"}
      - {predicate: "prices:hasFood", object_template: "pricesr:food/{food}", label: "食材の名前"}
  - name: food
    source: prices.csv
    subject:
      template: "pricesr:food/{food}"
      classes: [prices:Food]
      label: "食材の名前"
    properties:
      - {predicate: "prices:food", column: food, label: "食材の名前"}
"""


def _q(question: str, mode: str, op: str, **kw: object) -> dict:
    base = {"question": question, "mode": mode, "op": op, "kind": None, "property": None,
            "property2": None, "kind2": None, "value": None, "match": None, "target": None,
            "direction": None, "reason": "知りたくなる"}  # fmt: skip
    return {**base, **kw}


#: 「スーパーの価格表」への AI の提案（閉じた形に落ちる 4 本・落ちない 3 本）
SUGGESTED = {
    "questions": [
        _q("いちばん高い値段の記録は？", "data", "top", kind="値段の記録", property="price"),
        _q("食材ごとに記録は何件ある？", "data", "group", kind="値段の記録", property="食材の名前"),
        _q("店の名前に「北」を含む記録は？", "data", "filter", kind="値段の記録",
           property="shop", value="北", match="contains"),
        _q("食材は何種類ある？", "data", "count", kind="食材の名前"),
        _q("粘度が高い記録は？", "data", "top", kind="値段の記録", property="粘度"),  # no such col
    ]
}  # fmt: skip


def _prices(tmp_path: Path) -> str:
    meta = registry.save_dataset(
        tmp_path / "registry", "スーパーの価格表", {"mapping.yaml": _PRICES_IR},
        complete=True, warnings=[], traps=[], exit_code=0,
        created_at="2026-10-21T00:00:00+00:00",
    )  # fmt: skip
    return meta["id"]


def _files(root: Path) -> dict[str, bytes]:
    paths = [p for p in sorted(root.rglob("*")) if p.is_file()]
    return {str(p.relative_to(root)): p.read_bytes() for p in paths}


def _suggest(client: TestClient, dsid: str, **kw):
    return client.post(
        "/api/consult/suggest-questions",
        json={"screen": "dataset", "context": {"dataset_id": dsid}, "lang": "ja", **kw},
    )


def test_suggest_questions_gives_closed_shapes_and_saves_nothing_and_sends_no_value(
    tmp_path: Path,
) -> None:
    sentinel = "SENTINEL-VALUE-55501"
    llm = _LLM(json.dumps(SUGGESTED))
    app, store = _app(tmp_path, llm)
    dsid = _prices(tmp_path)
    store.add(ox.Quad(  # a value of the table sits in the store — it must never reach the model
        ox.NamedNode("https://example.org/prices/r/record/1"),
        ox.NamedNode("https://example.org/prices#shop"),
        ox.Literal(sentinel),
        ox.NamedNode(substrate.versioned_graph_iri(dsid, 1)),
    ))  # fmt: skip
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        before = _files(tmp_path)
        r = _suggest(client, dsid)
        assert r.status_code == 200, r.text
        out = r.json()["suggestions"]
        assert [(s["op"], s["shape"]) for s in out] == [
            ("top", "値段の記録のうち、1 つあたりの値段がいちばん大きいものを出します"
                    "（データセットごと）"),
            ("group", "値段の記録を 食材の名前 ごとに数える"),
            ("filter", "値段の記録のうち、店の名前に 北 を含むもの"),
            ("count", "食材の名前の件数を数えます（データセットごと）"),
        ]  # the one with a column that does not exist is not offered
        assert len(out) >= 3 and all(s["mode"] == "data" for s in out)
        assert set(out[0]) == {"question", "mode", "op", "shape", "kind", "property", "property2",
                               "kind2", "value", "match", "target", "direction"}  # fmt: skip
        # nothing is kept: the sentences, a refusal, nothing
        assert _files(tmp_path) == before
        assert client.get("/api/themes").json()["drafts"] == []
        system, user = llm.calls[0]
        assert "# Column headings" in user and "price | " in user and "shop | " in user
        assert "スーパーの価格表" in user and "値段の記録" in user  # names, not values
        assert sentinel not in system + user
        assert "Do NOT write SPARQL" in system
        assert len(llm.calls) == 1


def test_suggest_questions_hides_what_is_already_asked_and_tells_the_model(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(SUGGESTED))
    app, _ = _app(tmp_path, llm)
    dsid = _prices(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        d = client.post(
            "/api/themes/drafts",
            json={"label": "", "questions": ["食材は何種類ある？"]},
        ).json()
        assert d["id"]
        out = _suggest(client, dsid).json()["suggestions"]
        assert "食材は何種類ある？" in llm.calls[0][1]  # shown so it is not repeated
        assert [s["op"] for s in out] == ["top", "group", "filter"]  # and filtered again


def test_suggest_questions_needs_the_token_an_ai_and_a_known_dataset(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)  # no AI
    dsid = _prices(tmp_path)
    with TestClient(app, headers=KEY) as anon:
        assert _suggest(anon, dsid).status_code == 401
    with TestClient(app, headers=_AUTH) as client:
        r = _suggest(client, dsid)
        assert r.status_code == 503 and "AI" in r.json()["detail"]
    llm = _LLM()
    app2, _ = _app(tmp_path / "b", llm)
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        assert _suggest(client, "nope").status_code == 404
        no_ctx = client.post("/api/consult/suggest-questions", json={"screen": "dataset"})
        assert no_ctx.status_code == 422
    assert llm.calls == []


def test_suggest_questions_retries_once_then_502_on_an_off_shape_answer(tmp_path: Path) -> None:
    bad = json.dumps({"questions": [_q("SELECT ?s WHERE { ?s ?p ?o }", "data", "count",
                                       kind="値段の記録")]})  # fmt: skip
    llm = _LLM(bad, bad)
    app, _ = _app(tmp_path, llm)
    dsid = _prices(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        r = _suggest(client, dsid)
        assert r.status_code == 502 and "決まった形" in r.json()["detail"]
        assert len(llm.calls) == 2
    llm2 = _LLM(bad, json.dumps(SUGGESTED))
    app2, _ = _app(tmp_path / "c", llm2)
    dsid2 = _prices(tmp_path / "c")
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        assert len(_suggest(client, dsid2).json()["suggestions"]) == 4


# ---- suggest questions from a paper (PR 10b) ---------------------------------------------------

#: このテストのために自分で書いた短い要約（実在の論文の本文ではない）
PAPER_ABSTRACT = "Bi2Te3 に Sb を加えた試料で、粒界の密度が増えると熱伝導率が下がり ZT が上がった。"
PAPER_CONCLUSION = "粒界でのフォノン散乱が格子熱伝導率を下げ、ZT の向上につながると結論した。"
PAPER_METHODS = "SENTINEL-METHODS-77: 試料は焼結で作り、レーザーフラッシュ法で測った。"


def _paper_jats() -> str:
    return (
        "<article><front><article-meta><abstract>"
        f"<p>{PAPER_ABSTRACT}</p></abstract></article-meta></front><body>"
        f"<sec><title>2. Methods</title><p>{PAPER_METHODS}</p></sec>"
        f"<sec><title>4. Conclusions</title><p>{PAPER_CONCLUSION}</p></sec></body></article>"
    )


def _paper(tmp_path: Path, *, suffix: str = ".xml", cached: bool = True) -> str:
    """文書型のデータセット（JATS の ``.xml``、または PDF + 取り込みで控えた変換結果）。"""
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root, "熱電の論文", {"diagram.md": "classDiagram\n  class Document"},
        complete=True, warnings=[], traps=[], exit_code=0,
        created_at="2026-10-21T00:00:00+00:00",
    )  # fmt: skip
    sdir = registry.source_dir(root, meta["id"])
    assert sdir is not None
    sdir.mkdir(parents=True, exist_ok=True)
    name = f"paper{suffix}"
    if suffix == ".pdf":
        (sdir / name).write_bytes(b"%PDF-1.4 not read")
        if cached:
            (sdir / f"{name}.jats").write_text(_paper_jats(), encoding="utf-8")
    else:
        (sdir / name).write_text(_paper_jats(), encoding="utf-8")
    registry.mark_source_saved(root, meta["id"], [name])
    return meta["id"]


def _suggest_paper(client: TestClient, dsid: str | None, **kw):
    ctx = {"dataset_id": dsid} if dsid else {}
    return client.post(
        "/api/consult/suggest-questions",
        json={"screen": "paper", "context": ctx, "lang": "ja", **kw},
    )


def _paper_terms(client: TestClient) -> None:
    _mint(client, "thermal", "熱伝導率")
    _mint(client, "lattice", "格子熱伝導率")
    _mint(client, "grain", "粒界散乱", kind="class")


PAPER_SUGGESTED = {
    "questions": [
        _q("粒界散乱は格子熱伝導率を下げる？", "knowledge", "factors", target="格子熱伝導率",
           direction="lowers"),
        _q("熱伝導率はどんな成分でできている？", "knowledge", "parts", target="熱伝導率"),
        _q("粒界散乱にはどんな種類がある？", "knowledge", "kinds", target="粒界散乱"),
        _q("ZT を上げるものは？", "knowledge", "factors", target="ZT", direction="raises"),
        _q("試料ごとの Sb 量は？", "data", "group", kind="試料", property="Sb 量"),
    ]
}  # fmt: skip


def test_suggest_questions_from_a_paper_gives_three_knowledge_questions_from_the_abstract_only(
    tmp_path: Path,
) -> None:
    llm = _LLM(json.dumps(PAPER_SUGGESTED))
    app, _ = _app(tmp_path, llm)
    dsid = _paper(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _paper_terms(client)
        before = _files(tmp_path)
        r = _suggest_paper(client, dsid)
        assert r.status_code == 200, r.text
        out = r.json()["suggestions"]
        assert [(s["mode"], s["op"], s["target"]) for s in out] == [
            ("knowledge", "factors", "格子熱伝導率"),
            ("knowledge", "parts", "熱伝導率"),
            ("knowledge", "kinds", "粒界散乱"),
        ]  # a target no term names, and a table's column the paper does not have, are not offered
        assert all(s["question"] and s["shape"] for s in out)
        assert _files(tmp_path) == before  # nothing is kept
        assert client.get("/api/themes").json()["drafts"] == []
        system, user = llm.calls[0]
        assert "PAPER" in system and "Do NOT write SPARQL" in system
        asked = f"# Text to ask about\nAbstract: {PAPER_ABSTRACT} Conclusion: {PAPER_CONCLUSION}"
        assert asked in user
        assert "SENTINEL-METHODS-77" not in system + user  # the body is never passed
        assert "熱電の論文" in user and "格子熱伝導率 | " in user  # the data set's name, term names
        assert "Column headings" not in user and "Kinds of thing a row" not in user
        assert len(llm.calls) == 1


def test_suggest_questions_from_a_pdf_reads_the_conversion_kept_at_ingest(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(PAPER_SUGGESTED))
    app, _ = _app(tmp_path, llm)
    dsid = _paper(tmp_path, suffix=".pdf")
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _paper_terms(client)
        assert len(_suggest_paper(client, dsid).json()["suggestions"]) == 3
        assert PAPER_CONCLUSION in llm.calls[0][1]
    # a PDF that was ingested before the conversion was kept: nothing to read → 404, no AI call
    llm2 = _LLM()
    app2, _ = _app(tmp_path / "b", llm2)
    old = _paper(tmp_path / "b", suffix=".pdf", cached=False)
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        r = _suggest_paper(client, old)
        assert r.status_code == 404 and "取り込み直" in r.json()["detail"]
    assert llm2.calls == []
    # the kept conversion exists after ingest (the .pdf.jats beside the .pdf)
    assert any(f.name.endswith(".pdf.jats") for f in (tmp_path / "registry").rglob("*"))
    # a kept file that cannot be read as text is "no readable document" (404), not a 500
    for f in (tmp_path / "registry").rglob("*.pdf.jats"):
        f.write_bytes(b"\xff\xfe\x00bad")
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        assert _suggest_paper(client, dsid).status_code == 404


def test_suggest_questions_from_a_paper_hides_what_is_already_asked(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(PAPER_SUGGESTED))
    app, _ = _app(tmp_path, llm)
    dsid = _paper(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _paper_terms(client)
        client.post(
            "/api/themes/drafts",
            json={"label": "", "questions": ["熱伝導率はどんな成分でできている？"]},
        )
        out = _suggest_paper(client, dsid).json()["suggestions"]
        assert "熱伝導率はどんな成分でできている？" in llm.calls[0][1]
        assert [s["op"] for s in out] == ["factors", "kinds"]


def test_suggest_questions_from_a_paper_needs_the_token_an_ai_and_a_document(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path)  # no AI
    doc = _paper(tmp_path)
    table = _prices(tmp_path)
    with TestClient(app, headers=KEY) as anon:
        assert _suggest_paper(anon, doc).status_code == 401
    with TestClient(app, headers=_AUTH) as client:
        assert _suggest_paper(client, doc).status_code == 503
    llm = _LLM()
    app2, _ = _app(tmp_path / "b", llm)
    doc2 = _paper(tmp_path / "b")
    table2 = _prices(tmp_path / "b")
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        assert _suggest_paper(client, "nope").status_code == 404
        assert _suggest_paper(client, None).status_code == 422
        r = _suggest_paper(client, table2)  # a table is not a paper
        assert r.status_code == 422 and "文書型" in r.json()["detail"]
    assert llm.calls == [] and doc2 and table and doc  # every refusal came before the AI was asked


def test_a_star_of_a_document_dataset_asks_with_screen_dataset_and_is_read_as_a_paper(
    tmp_path: Path,
) -> None:
    """星の札（10a）は ``dataset`` で呼ぶ。文書型は表が無いので 422 にせず、論文として読む。"""
    llm = _LLM(json.dumps(PAPER_SUGGESTED))
    app, _ = _app(tmp_path, llm)
    dsid = _paper(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _paper_terms(client)
        r = _suggest(client, dsid)
        assert r.status_code == 200, r.text
        assert [s["op"] for s in r.json()["suggestions"]] == ["factors", "parts", "kinds"]
        assert "PAPER" in llm.calls[0][0] and PAPER_CONCLUSION in llm.calls[0][1]


# ---- suggest questions for a term with none (PR 10c) -------------------------------------------


def _mint_bare(
    tmp_path: Path, store: ox.Store, slug: str, label: str, kind: str = "property"
) -> None:
    """問い（cq）を持たない用語 — 画面からは、テーマの問いに関係でつなぐ形（``theme_id``）で
    できる。api の ``/api/vocab/shared`` は問い 1 つ以上を要るので、道具の層で直に作る。"""
    asyncio.run(
        shared_vocab.mint_term(
            _OxClient(store), tmp_path / "registry", slug=slug, kind=kind, label=label,
            cqs=[], theme_id="th-1", at="2026-10-21T00:00:00+00:00",
        )
    )


def _suggest_term(client: TestClient, slug: str | None, **kw):
    ctx = {"term_slug": slug} if slug else {}
    return client.post(
        "/api/consult/suggest-questions",
        json={"screen": "term", "context": ctx, "lang": "ja", **kw},
    )


def _term_lines(client: TestClient) -> None:
    """熱伝導率（問い無し）に掛かる線 2 本（格子は一部・粒界散乱は下げる）。"""
    ops = [
        {"op": "add_relation", "from": "lattice", "rel": "part", "to": "thermal", "reason": ""},
        {"op": "add_relation", "from": "scatter", "rel": "lowers", "to": "thermal", "reason": ""},
    ]  # fmt: skip
    res = client.post(
        "/api/consult/graph/apply",
        json={"screen": "home", "context": {}, "ops": ops, "proposal_id": "p-term"},
    ).json()
    assert res["applied"] == 2, res


TERM_SUGGESTED = {
    "questions": [
        _q("熱伝導率を下げるものは？", "knowledge", "factors", target="熱伝導率",
           direction="lowers"),
        _q("粒界散乱は熱伝導率を下げる？", "knowledge", "factors", target="粒界散乱",
           direction="lowers"),  # a neighbour, not this term
        _q("熱伝導率の最大は？", "data", "top", kind="試料", property="熱伝導率"),  # no such kind
        _q("熱伝導率の最小と最大は？", "data", "range", property="熱伝導率"),  # a term as item
        _q("熱伝導率はどんな成分でできている？", "knowledge", "parts", target="熱伝導率"),
    ]
}  # fmt: skip


def test_suggest_questions_for_a_term_with_none_reads_its_lines_and_gives_closed_shapes(
    tmp_path: Path,
) -> None:
    llm = _LLM(json.dumps(TERM_SUGGESTED))
    app, store = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint_bare(tmp_path, store, "thermal", "熱伝導率")
        _mint_bare(tmp_path, store, "lattice", "格子熱伝導率")
        _mint_bare(tmp_path, store, "scatter", "粒界散乱", kind="class")
        _term_lines(client)
        before = _files(tmp_path)
        lines_before = _lines(store)
        r = _suggest_term(client, "thermal")
        assert r.status_code == 200, r.text
        out = r.json()["suggestions"]
        assert [(s["op"], s["target"]) for s in out] == [
            ("factors", "熱伝導率"), ("range", ""), ("parts", "熱伝導率"),
        ]  # only this term (a neighbour is dropped) · a kind not on the map dropped
        assert all(s["question"] and s["shape"] for s in out)
        assert _files(tmp_path) == before and _lines(store) == lines_before  # nothing kept
        system, user = llm.calls[0]
        assert "ONE TERM" in system and "# Name of the data set" not in user
        facts = user.split("# The term and its surroundings (one fact per line)\n")[1]
        facts = facts.split("\n\n")[0].splitlines()
        assert facts[0] == "用語: 熱伝導率（項目）"
        assert "格子熱伝導率 は 熱伝導率 の一部" in facts
        assert "粒界散乱 は 熱伝導率 を下げる" in facts
        assert len(llm.calls) == 1


def test_suggest_questions_for_a_term_that_has_a_question_is_409_before_the_ai(
    tmp_path: Path,
) -> None:
    llm = _LLM(json.dumps(TERM_SUGGESTED))
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "thermal", "熱伝導率")  # with a competency question
        r = _suggest_term(client, "thermal")
        assert r.status_code == 409 and "問いがあります" in r.json()["detail"]
    assert llm.calls == []


def test_suggest_questions_for_a_term_needs_the_token_an_ai_and_a_known_term(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path)  # no AI
    with TestClient(app, headers=KEY) as anon:
        assert _suggest_term(anon, "thermal").status_code == 401
    with TestClient(app, headers=_AUTH) as client:
        assert _suggest_term(client, "thermal").status_code == 503
    llm = _LLM()
    app2, store2 = _app(tmp_path / "b", llm)
    with TestClient(app2, headers={**_AUTH, **KEY}) as client:
        _mint_bare(tmp_path / "b", store2, "thermal", "熱伝導率")
        assert _suggest_term(client, "nope").status_code == 404
        assert _suggest_term(client, None).status_code == 422
    assert llm.calls == []


def test_suggest_questions_for_a_dataset_also_names_the_link_items_without_values(
    tmp_path: Path,
) -> None:
    """列の無い項目（別の種類へつなぐ線）も、名前だけ「つなぐ項目」として AI に渡す。"""
    join = _q("値段の記録が指す食材は？", "data", "join", kind="値段の記録", property="食材の名前",
              kind2="食材の名前")  # fmt: skip
    llm = _LLM(json.dumps({"questions": [join]}))
    app, _ = _app(tmp_path, llm)
    dsid = _prices(tmp_path)
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        out = _suggest(client, dsid).json()["suggestions"]
        assert [s["op"] for s in out] == ["join"]
        user = llm.calls[0][1]
        assert "食材の名前 | link to another kind" in user
