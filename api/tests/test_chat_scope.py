"""11o — 範囲（点か行）つきの文・差分の絞り込み・下書きの札の行の鍵・1 行の承認。"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from asterism import chat_sessions, shared_vocab, themes
from fastapi.testclient import TestClient

from asterism_api import chat_turns
from tests.test_chat_api import CONCEPT_A, CONCEPT_B, REL_AB, _client, _new, _ops, _say
from tests.test_crosswalk_api import _AUTH
from tests.test_themes_api import _LLM, AI_JSON, KEY, Q_LOWER, _app

NEW = {"op": "add_concept", "key": "n1", "label_ja": "ナノ析出物", "label_en": "", "aliases": [],
       "kind": "class", "use": "new", "reason": ""}  # fmt: skip


def _term_iris(client: TestClient) -> dict[str, str]:
    terms = asyncio.run(shared_vocab._read_terms(client.app.state.client))
    return {t.label: t.iri for t in terms}


def _key_of(user: str, label: str) -> str:
    m = re.search(r"^- (c\d+) \| " + label + r" \|", user, re.MULTILINE)
    assert m is not None, user[:1500]
    return m.group(1)


class _ScopeLLM(_LLM):
    """最初は AI_JSON。2 度目は、範囲の中（格子熱伝導率）と外（点欠陥散乱を外す）の差分。"""

    def __init__(self) -> None:
        super().__init__(json.dumps(AI_JSON))

    def complete(self, system_prompt: str, user_message: str) -> str:
        r = self.routed(system_prompt, user_message)
        if r is not None:
            return r
        if not self.calls:
            return super().complete(system_prompt, user_message)
        self.calls.append((system_prompt, user_message))
        lattice = _key_of(user_message, "格子熱伝導率")
        point = _key_of(user_message, "点欠陥散乱")
        ops = [
            NEW,
            {"op": "add_relation", "from": "n1", "rel": "lowers", "to": lattice, "reason": ""},
            {"op": "remove", "target": "concept", "key": point, "reason": ""},
        ]
        return json.dumps({"reply": "ナノ析出物を足します。", "ops": ops}, ensure_ascii=False)


def _ai(tmp_path: Path):
    llm = _ScopeLLM()
    app, _ = _app(tmp_path, llm)
    return TestClient(app, headers={**_AUTH, **KEY}), llm


def _approved_session(client: TestClient) -> tuple[str, dict[str, str]]:
    sid = _new(client)
    t1 = _say(client, sid, Q_LOWER)["turn"]
    r = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={})
    assert r.json()["commit"] is not None
    return sid, _term_iris(client)


# ---- 純関数 ---------------------------------------------------------------------------------


def test_filter_keeps_what_touches_the_scope() -> None:
    rel_in = {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "a", "reason": ""}
    rel_out = {"op": "add_relation", "from": "n2", "rel": "lowers", "to": "b", "reason": ""}
    c1 = {"op": "add_concept", "key": "n1"}
    c2 = {"op": "add_concept", "key": "n2"}
    c3 = {"op": "add_concept", "key": "n3"}  # 範囲に結ぶ関係が無い
    rm_rel = {"op": "remove", "target": "relation", "from": "b", "rel": "sub", "to": "a"}
    rm_far = {"op": "remove", "target": "relation", "from": "b", "rel": "sub", "to": "c"}
    rm_con = {"op": "remove", "target": "concept", "key": "a"}
    rm_other = {"op": "remove", "target": "concept", "key": "b"}
    ops = [c1, c2, c3, rel_in, rel_out, rm_rel, rm_far, rm_con, rm_other]
    kept, dropped = chat_turns.filter_ops_to_scope(ops, {"keys": ["a"]})
    assert kept == [c1, rel_in, rm_rel, rm_con]
    assert dropped == [c2, c3, rel_out, rm_far, rm_other]
    # 行の範囲 = 両端
    kept2, _ = chat_turns.filter_ops_to_scope([rm_far, rm_other], {"keys": ["b", "c"]})
    assert kept2 == [rm_far, rm_other]
    assert chat_turns.filter_ops_to_scope([], {"keys": ["a"]}) == ([], [])


# ---- 範囲つきの文 -----------------------------------------------------------------------------


def test_scoped_text_skips_the_router_puts_scope_in_the_prompt_and_drops_the_rest(
    tmp_path: Path,
) -> None:
    client, llm = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)
        n_route = len(llm.route_calls)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        assert len(llm.route_calls) == n_route  # 振り分けを飛ばした
        user = llm.calls[-1][1]
        assert "# Scope — change only this" in user and "格子熱伝導率" in user
        turn = out["turn"]
        assert [o["op"] for o in turn["reply"]["ops"]] == ["add_concept", "add_relation"]
        (n,) = [x for x in turn["reply"]["notes"] if x["kind"] == "info"]
        assert n["text"] == "範囲の外の変更 1 件は外しました"
        draft = themes.load_draft(root, out["session"]["draft_id"])
        assert any(c["label_ja"] == "点欠陥散乱" for c in draft["concepts"])
        assert any(c["label_ja"] == "ナノ析出物" for c in draft["concepts"])


def test_scoped_text_english_note_and_line_scope(tmp_path: Path) -> None:
    client, llm = _ai(tmp_path)
    with client:
        sid, iris = _approved_session(client)
        pred = "https://example.org/lowers"  # 述語の IRI でも短い名に直る
        line = {"from": iris["粒界散乱"], "rel": pred, "to": iris["格子熱伝導率"]}
        out = _say(client, sid, "add it", lang="en", scope={"node": "x", "line": line})
        assert "Only this one relation" in llm.calls[-1][1]
        notes = [x["text"] for x in out["turn"]["reply"]["notes"]]
        assert "Left out 3 change(s) outside the scope." in notes


def test_scope_with_no_such_point_is_400(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    with client:
        sid, iris = _approved_session(client)
        for node in ("t|https://x.example/none", "draft|dnone|c1", "zzz"):
            r = client.post(
                f"/api/chat/sessions/{sid}/turns", json={"text": "直して", "scope": {"node": node}}
            )
            assert r.status_code == 400, node
        # 話にテーマも下書きも無ければ範囲は探せない
        sid2 = _new(client)
        r = client.post(
            f"/api/chat/sessions/{sid2}/turns",
            json={"text": "直して", "scope": {"node": "t|" + iris["粒界散乱"]}},
        )
        assert r.status_code == 400


# ---- 下書きの札の行の鍵・行を外す -------------------------------------------------------------


def test_draft_card_sentences_carry_the_row_keys_and_a_row_can_be_removed(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid = _new(client)
        _say(client, sid, "熱伝導率を下げるものは？")
        out = _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])
        did = out["session"]["draft_id"]
        card = client.get("/api/chat/point", params={"iri": f"draft:{did}:h2"}).json()
        (s,) = card["sentences"]
        assert (s["from"], s["rel"], s["to"], s["draft_id"]) == ("h2", "lowers", "h1", did)
        assert s["turn_id"]
        gone = {"op": "remove", "target": "relation", "from": "h2", "rel": "lowers", "to": "h1"}
        turn = _ops(client, sid, [gone])["turn"]
        assert [o["op"] for o in turn["reply"]["ops"]] == ["remove"]
        assert themes.load_draft(root, did)["relations"] == []


# ---- 1 行の承認 -------------------------------------------------------------------------------


def test_confirm_only_one_row_keeps_the_rest_as_a_proposal(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        turn = out["turn"]
        draft = themes.load_draft(root, out["session"]["draft_id"])
        lattice = next(c["key"] for c in draft["concepts"] if c["label_ja"] == "格子熱伝導率")
        only = {"relations": [{"from": "n1", "rel": "lowers", "to": lattice}], "concepts": []}
        r = client.post(
            f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={"only": only}
        )
        assert r.status_code == 200, r.text
        commit = r.json()["commit"]
        assert commit is not None, r.json()["turn"]["reply"]["notes"]
        assert commit["only"]["relations"][0]["from"] == "n1" and len(commit["minted"]) == 1
        assert r.json()["confirmed"] == []  # 返事そのものはまだ提案
        assert "ナノ析出物" in _term_iris(client)
        got = chat_sessions.load_session(root, sid)
        t = next(x for x in got["turns"] if x["id"] == turn["id"])
        assert t["state"] == "proposed" and t["confirmed"][0]["only"] == commit["only"]
        assert got["draft_id"] == out["session"]["draft_id"]  # 残りの下書きは残る
        kept = themes.load_draft(root, got["draft_id"])
        nano = next(c for c in kept["concepts"] if c["label_ja"] == "ナノ析出物")
        assert nano["use"].startswith("existing:")  # 二重に鋳造しない


def test_confirm_only_without_a_theme_is_409(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        t1 = _say(client, sid, "熱伝導率を下げるものは？")["turn"]
        _ops(client, sid, [CONCEPT_A, CONCEPT_B, REL_AB])
        only = {"relations": [{"from": "h2", "rel": "lowers", "to": "h1"}], "concepts": []}
        r = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={"only": only})
        assert r.status_code == 409
        assert "返事の承認" in r.json()["detail"]
        assert chat_turns.TurnRefused  # 参照（lint 避け）


def test_confirm_only_a_row_that_is_gone_is_409(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        only = {"relations": [{"from": "zz", "rel": "lowers", "to": "yy"}], "concepts": []}
        r = client.post(
            f"/api/chat/sessions/{sid}/turns/{out['turn']['id']}/confirm", json={"only": only}
        )
        assert r.status_code == 409


@pytest.mark.parametrize("bad", [{"node": ""}, {"node": "t|x", "line": {"from": "a"}}])
def test_scope_shape_is_checked(tmp_path: Path, bad: dict) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        r = client.post(f"/api/chat/sessions/{sid}/turns", json={"text": "x", "scope": bad})
        assert r.status_code == 422


# ---- checker の指摘の直し -----------------------------------------------------------------------


def test_scoped_text_without_an_ai_is_a_hint_and_makes_no_draft(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        sid = _new(client)
        out = _say(client, sid, "直して", scope={"node": "t|https://x.example/a"})
        assert out["turn"]["state"] == "none" and out["session"]["draft_id"] is None
        assert "AI の設定が要ります" in out["turn"]["reply"]["text"]
        en = _say(client, sid, "fix", lang="en", scope={"node": "x"})["turn"]["reply"]["text"]
        assert "needs the AI" in en


def test_seed_rows_are_marked_in_the_sky_and_the_card(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        did = out["session"]["draft_id"]
        (card,) = client.get("/api/sky").json()["proposed"]
        marks = {r["from_seed"] for r in card["relations"]}
        assert marks == {True, False}
        assert sum(1 for r in card["relations"] if not r["from_seed"]) == 1
        assert [c["label"] for c in card["concepts"] if not c["from_seed"]] == ["ナノ析出物"]
        pt = client.get("/api/chat/point", params={"iri": f"draft:{did}:n1"}).json()
        assert [s["from_seed"] for s in pt["sentences"]] == [False]
        lattice = next(c["key"] for c in card["concepts"] if c["label"] == "格子熱伝導率")
        pt2 = client.get("/api/chat/point", params={"iri": f"draft:{did}:{lattice}"}).json()
        assert {s["from_seed"] for s in pt2["sentences"]} == {True, False}


def test_after_one_row_approval_double_sum_and_revert(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        turn = out["turn"]
        draft = themes.load_draft(root, out["session"]["draft_id"])
        lattice = next(c["key"] for c in draft["concepts"] if c["label_ja"] == "格子熱伝導率")
        only = {"relations": [{"from": "n1", "rel": "lowers", "to": lattice}]}
        url = f"/api/chat/sessions/{sid}/turns/{turn['id']}"
        first = client.post(url + "/confirm", json={"only": only}).json()
        minted = first["commit"]["minted"]
        assert len(minted) == 1
        # (a) 二重承認は 409
        again = client.post(url + "/confirm", json={"only": only})
        assert again.status_code == 409 and "承認済み" in again.json()["detail"]
        # (c) 取り消しは承認済みの行を残す
        r = client.post(url + "/revert")
        assert r.status_code == 200, r.text
        kept = themes.load_draft(root, chat_sessions.load_session(root, sid)["draft_id"])
        assert any(c["label_ja"] == "ナノ析出物" for c in kept["concepts"])
        assert len(kept["relations"]) == len(draft["relations"])  # 種の行 + 承認した 1 行
        notes = [n["text"] for n in r.json()["turn"]["reply"]["notes"]]
        assert "承認済みの 1 行は残っています（「文」の行の「外す」で外せます）" in notes
        assert any(c["label_ja"] == "点欠陥散乱" for c in kept["concepts"]) is True


def test_full_confirm_sums_the_minted_of_the_one_row_approval(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        turn = out["turn"]
        draft = themes.load_draft(root, out["session"]["draft_id"])
        lattice = next(c["key"] for c in draft["concepts"] if c["label_ja"] == "格子熱伝導率")
        url = f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm"
        only = {"relations": [{"from": "n1", "rel": "lowers", "to": lattice}]}
        slug = client.post(url, json={"only": only}).json()["commit"]["minted"][0]
        full = client.post(url, json={}).json()
        assert full["commit"] is not None
        assert slug in full["commit"]["minted"]


def test_a_failure_after_the_commit_puts_the_draft_back(tmp_path: Path, monkeypatch) -> None:
    client, _ = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        did = out["session"]["draft_id"]
        draft = themes.load_draft(root, did)
        lattice = next(c["key"] for c in draft["concepts"] if c["label_ja"] == "格子熱伝導率")

        def boom(*a, **k):
            raise RuntimeError("disk")

        monkeypatch.setattr(chat_turns, "_save", boom)
        only = {"relations": [{"from": "n1", "rel": "lowers", "to": lattice}]}
        r = client.post(
            f"/api/chat/sessions/{sid}/turns/{out['turn']['id']}/confirm", json={"only": only}
        )
        assert r.status_code == 502
        back = themes.load_draft(root, did)
        assert back is not None and len(back["relations"]) == len(draft["relations"])


def test_everything_outside_the_scope_is_a_plain_sentence_and_no_change(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "足して", scope={"node": "t|" + iris["電子熱伝導率"]})
        turn = out["turn"]
        assert turn["state"] == "none" and turn["reply"]["ops"] == []
        assert turn["reply"]["text"] == "範囲の中では変えるものがありませんでした"
        assert turn["reply"]["notes"][0]["text"] == "範囲の外の変更 3 件は外しました"


def test_line_scope_keeps_only_that_line() -> None:
    scope = {"keys": ["a", "b"], "line": ["a", "sub", "b"]}
    rm = {"op": "remove", "target": "relation", "from": "a", "rel": "sub", "to": "b"}
    rt = {"op": "retarget", "from": "a", "rel": "sub", "to": "b", "new_to": "z"}
    add = {"op": "add_relation", "from": "a", "rel": "part", "to": "b", "reason": ""}
    ren = {"op": "rename", "key": "b", "label_ja": "x"}
    other_rel = {"op": "remove", "target": "relation", "from": "a", "rel": "sub", "to": "c"}
    other_con = {"op": "remove", "target": "concept", "key": "a"}
    other_add = {"op": "add_relation", "from": "a", "rel": "lowers", "to": "c", "reason": ""}
    kept, dropped = chat_turns.filter_ops_to_scope(
        [rm, rt, add, ren, other_rel, other_con, other_add, {"op": "add_concept", "key": "n"}],
        scope,
    )
    assert kept == [rm, rt, add, ren] and len(dropped) == 4


def test_scope_filter_uses_the_key_the_diff_folds_into() -> None:
    concepts = [{"key": "k1", "label_ja": "格子熱伝導率", "label_en": "", "kind": "property"}]
    ops = [
        {"op": "add_concept", "key": "n9", "label_ja": "格子熱伝導率", "label_en": "",
         "kind": "property"},
        {"op": "add_relation", "from": "n9", "rel": "lowers", "to": "k1", "reason": ""},
        {"op": "add_relation", "from": "n9", "rel": "raises", "to": "k2", "reason": ""},
    ]  # fmt: skip
    alias = chat_turns.scope_aliases(ops, concepts)
    assert alias == {"n9": "k1"}
    kept, dropped = chat_turns.filter_ops_to_scope(ops, {"keys": ["k1"]}, alias)
    assert kept == ops and dropped == []
    kept, _ = chat_turns.filter_ops_to_scope(ops, {"keys": ["k1"]})
    assert len(kept) == 2  # 寄せないと n9→k2 は範囲に触れない（寄せると k1→k2 で残る）
    assert chat_turns.filter_ops_to_scope(ops[:2], {"keys": ["zz"]}, alias)[0] == []


def test_eq_line_scope_ignores_direction() -> None:
    graph = {
        "concepts": [{"key": "a", "label_ja": "A"}, {"key": "b", "label_ja": "B"}],
        "relations": [{"from": "b", "rel": "eq", "to": "a"}],
    }
    got = chat_turns.resolve_scope({"node": "x", "line": {"from": "a", "rel": "eq", "to": "b"}},
                                   graph, [], "ja")  # fmt: skip
    assert got["keys"] == ["a", "b"]
    with pytest.raises(chat_turns.TurnRefused):
        chat_turns.resolve_scope({"node": "x", "line": {"from": "a", "rel": "sub", "to": "b"}},
                                 graph, [], "ja")  # fmt: skip


def _more(to: str) -> list[dict]:
    """別の返事 — 新しい概念 1 つを、既にある概念に結ぶ。"""
    new = {"op": "add_concept", "key": "m1", "label_ja": "ドーパント", "label_en": "",
           "aliases": [], "kind": "class", "use": "new", "reason": ""}  # fmt: skip
    return [new, {"op": "add_relation", "from": "m1", "rel": "lowers", "to": to, "reason": ""}]


# ---- checker 2 回目 ---------------------------------------------------------------------------


def test_seed_rows_on_a_draft_card_are_in_the_approved_iri_form(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        did = out["session"]["draft_id"]
        (card,) = client.get("/api/sky").json()["proposed"]
        lattice = next(c["key"] for c in card["concepts"] if c["label"] == "格子熱伝導率")
        pt = client.get("/api/chat/point", params={"iri": f"draft:{did}:{lattice}"}).json()
        seeded = [s for s in pt["sentences"] if s["from_seed"]]
        fresh = [s for s in pt["sentences"] if not s["from_seed"]]
        assert seeded and fresh
        for s in seeded:
            assert s["source"].startswith("http") and s["target"].startswith("http")
            assert not s["relation"].startswith("draft")
            assert s["from"] in card["concepts"][0].keys() | {s["from"]}
        assert any(s["source"] == iris["粒界散乱"] for s in seeded)
        assert fresh[0]["source"].startswith("draft:") and fresh[0]["relation"] == "lowers"


def test_one_row_approval_only_for_the_latest_proposal(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        draft = themes.load_draft(root, out["session"]["draft_id"])
        lattice = next(c["key"] for c in draft["concepts"] if c["label_ja"] == "格子熱伝導率")
        _ops(client, sid, _more(lattice))  # 後の提案が最後になる
        only = {"relations": [{"from": "n1", "rel": "lowers", "to": lattice}]}
        r = client.post(
            f"/api/chat/sessions/{sid}/turns/{out['turn']['id']}/confirm", json={"only": only}
        )
        assert r.status_code == 409 and "最後の提案" in r.json()["detail"]


def test_undoing_the_full_confirm_also_removes_the_one_row_lines(tmp_path: Path) -> None:
    client, _ = _ai(tmp_path)
    root = tmp_path / "registry"
    with client:
        sid, iris = _approved_session(client)

        def lines() -> int:
            theme_id = chat_sessions.load_session(root, sid)["theme_id"]
            data = themes.load_themes_file(root)
            return len(next(t for t in data["themes"] if t["id"] == theme_id)["relations"])

        base = lines()
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
        t2 = out["turn"]
        draft = themes.load_draft(root, out["session"]["draft_id"])
        lattice = next(c["key"] for c in draft["concepts"] if c["label_ja"] == "格子熱伝導率")
        only = {"relations": [{"from": "n1", "rel": "lowers", "to": lattice}]}
        one = client.post(f"/api/chat/sessions/{sid}/turns/{t2['id']}/confirm", json={"only": only})
        assert one.json()["commit"] is not None and lines() == base + 1
        t3 = _ops(client, sid, _more(lattice))["turn"]
        full = client.post(f"/api/chat/sessions/{sid}/turns/{t3['id']}/confirm", json={})
        assert full.json()["commit"] is not None
        assert full.json()["commit"]["lines"] >= 1
        r = client.post(f"/api/chat/sessions/{sid}/turns/{t3['id']}/revert")
        assert r.status_code == 200, r.text
        assert lines() == base
        assert "ナノ析出物" not in _term_iris(client)
