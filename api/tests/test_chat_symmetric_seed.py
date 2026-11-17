"""向きの無い関係「競合する」を、下書きに逆向き（IRI の大きい側 → 小さい側）で置いたとき。

ストアは両端を IRI の文字列順に並べる（``canonical_ends``）が、下書きの行は置いた向きのまま残る。
種（承認済みの控え）との突き合わせが向きつきだと、✓ のたびに線が「外された」と見られて消える。
下書きが置いた向きのまま残るのは「1 行の承認」のあと（承認済みの行は下書きに残る）。
実際の口（HTTP のテストクライアント）で通す。語は作り物（Mg2Si・Mg2Sn。slug は aaa < zzz）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from asterism import chat_sessions, crosswalk_runtime, shared_vocab, themes
from fastapi.testclient import TestClient

from asterism_api import chat_turns
from tests.test_chat_api import _new, _say
from tests.test_crosswalk_api import _AUTH
from tests.test_themes_api import _LLM, KEY, _app
from tests.test_vocab_api import _mint

Q = "Mg2Si に競合する材料は？"
#: 最初の提案。Mg2Si（aaa）だけ置いて ✓ し、テーマと種を作る
FIRST = {
    "theme_label": "熱電材料",
    "concepts": [
        {"key": "c1", "label_ja": "Mg2Si", "label_en": "", "aliases": [], "kind": "class",
         "use": "new", "reason": ""},
    ],
    "relations": [],
    "answers": [{"question": Q, "mode": "knowledge", "op": "factors", "target": "c1",
                 "direction": "lowers", "kind": None, "property": None,
                 "wanted_data": "", "reason": ""}],
    "suggested_questions": [],
}  # fmt: skip
NEW_Z = {"op": "add_concept", "key": "cz", "label_ja": "Mg2Sn", "label_en": "", "aliases": [],
         "kind": "class", "use": "new", "reason": ""}  # fmt: skip


class _TwoStepLLM(_LLM):
    """最初は FIRST。2 度目は、Mg2Sn（zzz）から Mg2Si（aaa）へ逆向きに「競合する」を置く。"""

    def __init__(self) -> None:
        super().__init__(json.dumps(FIRST))

    def complete(self, system_prompt: str, user_message: str) -> str:
        r = self.routed(system_prompt, user_message)
        if r is not None:
            return r
        if not self.calls:
            return super().complete(system_prompt, user_message)
        self.calls.append((system_prompt, user_message))
        m = re.search(r"^- (c\d+) \| Mg2Si \|", user_message, re.MULTILINE)
        assert m is not None, user_message[:1500]
        rel = {"op": "add_relation", "from": "cz", "rel": "competes", "to": m.group(1),
               "reason": ""}  # fmt: skip
        return json.dumps({"reply": "競合を置きます。", "ops": [NEW_Z, rel]}, ensure_ascii=False)


def _competes_lines(client: TestClient) -> list[tuple[str, str, str]]:
    lines = asyncio.run(crosswalk_runtime.list_alignments(client.app.state.client))
    return [
        (ln["source"], ln["relation"], ln["target"])
        for ln in lines
        if ln["relation"] == "competesWith"
    ]


def _start(tmp_path: Path):
    """aaa・zzz を材料として作り、Mg2Si だけで ✓ した話に、逆向きの競合を提案させる。"""
    app, _ = _app(tmp_path, _TwoStepLLM())
    client = TestClient(app, headers={**_AUTH, **KEY})
    root = tmp_path / "registry"
    client.__enter__()
    assert _mint(client, slug="aaa", label_ja="Mg2Si", role="material").status_code == 201
    assert _mint(client, slug="zzz", label_ja="Mg2Sn", role="material").status_code == 201
    sid = _new(client)
    t1 = _say(client, sid, Q)["turn"]
    c = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={})
    assert c.json()["commit"] is not None, c.json()["turn"]["reply"]["notes"]
    return client, root, sid


def _propose_reversed(client: TestClient, root: Path, sid: str):
    """逆向き（zzz → aaa）の競合を提案させ、その 1 行だけ承認する。``(返事の手番, 1 行の鍵)``。"""
    out = _say(client, sid, "Mg2Sn が競合する")
    turn = out["turn"]
    assert turn["state"] == "proposed", turn["reply"]["notes"]
    draft = themes.load_draft(root, out["session"]["draft_id"])
    slug_of = {c["key"]: c["slug"] for c in draft["concepts"]}
    (row,) = [r for r in draft["relations"] if r["rel"] == "competes"]
    assert (slug_of[row["from"]], slug_of[row["to"]]) == ("zzz", "aaa")  # 置いた向き（逆向き）
    only = {"relations": [{"from": row["from"], "rel": "competes", "to": row["to"]}]}
    r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={"only": only})
    assert r.status_code == 200, r.text
    assert r.json()["commit"] is not None, r.json()["turn"]["reply"]["notes"]
    return turn, row


def test_a_reversed_competes_stays_after_the_next_approval_and_is_marked(tmp_path: Path) -> None:
    client, root, sid = _start(tmp_path)
    try:
        a, z = shared_vocab.term_iri("aaa"), shared_vocab.term_iri("zzz")
        turn, row = _propose_reversed(client, root, sid)
        assert _competes_lines(client) == [(a, "competesWith", z)]  # 並べ替わって 1 本
        # 承認済みの印（from_seed）が、逆向きに置いた行にも付く
        (card,) = client.get("/api/sky").json()["proposed"]
        (mark,) = [r for r in card["relations"] if r["rel"] == "competes"]
        assert (mark["from"], mark["to"], mark["from_seed"]) == (row["from"], row["to"], True)
        # 何も変えずにもう一度 ✓ — 線は外されない
        c = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={})
        assert c.status_code == 200, c.text
        commit = c.json()["commit"]
        assert commit is not None, c.json()["turn"]["reply"]["notes"]
        assert commit["removed"] == {"lines": 0, "terms": 0}
        assert _competes_lines(client) == [(a, "competesWith", z)]
    finally:
        client.__exit__(None, None, None)


def test_reverting_keeps_the_approved_reversed_competes(tmp_path: Path) -> None:
    client, root, sid = _start(tmp_path)
    try:
        turn, _row = _propose_reversed(client, root, sid)
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/revert")
        assert r.status_code == 200, r.text
        kept = themes.load_draft(root, chat_sessions.load_session(root, sid)["draft_id"])
        assert [x["rel"] for x in kept["relations"]] == ["competes"]  # 承認済みの行は残る
        assert len(_competes_lines(client)) == 1
    finally:
        client.__exit__(None, None, None)


def test_remove_from_a_point_works_for_either_direction_of_competes(tmp_path: Path) -> None:
    for first, second in (("aaa", "zzz"), ("zzz", "aaa")):
        sub = tmp_path / f"{first}-{second}"
        sub.mkdir()
        client, root, sid = _start(sub)
        try:
            _propose_reversed(client, root, sid)
            base = f"/api/chat/sessions/{sid}/turns"
            r = client.post(
                base,
                json={"remove": {"from": shared_vocab.term_iri(first),
                                 "relation": "competesWith",
                                 "to": shared_vocab.term_iri(second)}},
            )  # fmt: skip
            assert r.status_code == 201, r.text
            turn = r.json()["turn"]
            assert turn["state"] == "proposed", turn["reply"]["notes"]
            assert [o["target"] for o in turn["reply"]["ops"]] == ["relation"]
            c = client.post(f"{base}/{turn['id']}/confirm", json={})
            assert c.json()["commit"]["removed"]["lines"] == 1
            assert _competes_lines(client) == []
        finally:
            client.__exit__(None, None, None)


# ---- 片端が標準の語（curie）の「競合する」 ---------------------------------------------------

STD = "cmso:CrystalStructure"
STD_IRI = "http://purls.helmholtz-metadaten.de/cmso/CrystalStructure"
FIRST_STD = {
    **FIRST,
    "relations": [{"from": "c1", "rel": "competes", "to": STD, "reason": ""}],
}  # fmt: skip


class _StdLLM(_TwoStepLLM):
    def __init__(self) -> None:
        _LLM.__init__(self, json.dumps(FIRST_STD))

    def complete(self, system_prompt: str, user_message: str) -> str:
        r = self.routed(system_prompt, user_message)
        if r is not None:
            return r
        if not self.calls:
            return _LLM.complete(self, system_prompt, user_message)
        self.calls.append((system_prompt, user_message))
        new = {**NEW_Z, "key": "cq", "label_ja": "Mg2Ge"}
        rel = {"op": "add_relation", "from": "cq", "rel": "competes", "to": STD, "reason": ""}
        return json.dumps({"reply": "足します。", "ops": [new, rel]}, ensure_ascii=False)


def test_competes_with_a_standard_term_is_read_back_marked_kept_and_removable(
    tmp_path: Path,
) -> None:
    from asterism_api import chat_seed

    app, _ = _app(tmp_path, _StdLLM())
    client = TestClient(app, headers={**_AUTH, **KEY})
    root = tmp_path / "registry"
    with client:
        assert _mint(client, slug="aaa", label_ja="Mg2Si", role="material").status_code == 201
        sid = _new(client)
        t1 = _say(client, sid, Q)["turn"]
        c = client.post(f"/api/chat/sessions/{sid}/turns/{t1['id']}/confirm", json={})
        assert c.json()["commit"] is not None, c.json()["turn"]["reply"]["notes"]
        a = shared_vocab.term_iri("aaa")
        # ストアは IRI の文字列順: 標準（http://）が先
        assert _competes_lines(client) == [(STD_IRI, "competesWith", a)]
        # 種に読み戻される（from＝用語・to＝標準の curie）
        theme = next(t for t in themes.load_themes_file(root)["themes"] if t.get("relations"))
        terms = asyncio.run(shared_vocab._read_terms(client.app.state.client))
        seed = chat_seed.seed_from_theme(root, terms, theme)["seed"]
        assert seed["relations"] == [["aaa", "competes", STD]]
        # 次の手番: 承認済みの印が付く・もう一度 ✓ しても線が残る
        out = _say(client, sid, "Mg2Ge も足して")
        turn = out["turn"]
        assert turn["state"] == "proposed", turn["reply"]["notes"]
        (card,) = client.get("/api/sky").json()["proposed"]
        marks = [r["from_seed"] for r in card["relations"] if r["rel"] == "competes"]
        assert sorted(marks) == [False, True]
        c2 = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={})
        assert c2.json()["commit"]["removed"] == {"lines": 0, "terms": 0}
        assert (STD_IRI, "competesWith", a) in _competes_lines(client)
        # 点から外す: ストアの向きでも逆でも効く
        for first, second in ((STD_IRI, a), (a, STD_IRI)):
            r = client.post(
                f"/api/chat/sessions/{sid}/turns",
                json={"remove": {"from": first, "relation": "competesWith", "to": second}},
            )
            assert r.status_code == 201, r.text
            assert [o["target"] for o in r.json()["turn"]["reply"]["ops"]] == ["relation"]
            client.post(f"/api/chat/sessions/{sid}/turns/{r.json()['turn']['id']}/revert")


# ---- 逆向きの「競合する」を、行の承認・範囲・1 枚が同じ行と見る ----------------------------


def test_approving_the_same_reversed_row_again_from_the_other_end_is_409(tmp_path: Path) -> None:
    client, root, sid = _start(tmp_path)
    try:
        turn, row = _propose_reversed(client, root, sid)
        again = {"relations": [{"from": row["to"], "rel": "competes", "to": row["from"]}]}
        url = f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm"
        r = client.post(url, json={"only": again})
        assert r.status_code == 409 and "承認済み" in r.json()["detail"], r.text
    finally:
        client.__exit__(None, None, None)


def test_line_scope_on_a_competes_row_ignores_direction() -> None:
    graph = {
        "concepts": [{"key": "a", "label_ja": "A"}, {"key": "b", "label_ja": "B"}],
        "relations": [{"from": "b", "rel": "competes", "to": "a"}],
    }
    got = chat_turns.resolve_scope(
        {"node": "x", "line": {"from": "a", "rel": "competes", "to": "b"}}, graph, [], "ja"
    )
    assert got["keys"] == ["a", "b"]
    with pytest.raises(chat_turns.TurnRefused):  # 向きのある関係は逆向きでは当たらない
        chat_turns.resolve_scope(
            {"node": "x", "line": {"from": "a", "rel": "makes", "to": "b"}},
            {**graph, "relations": [{"from": "b", "rel": "makes", "to": "a"}]}, [], "ja",
        )  # fmt: skip


def test_line_scope_keeps_a_competes_op_put_the_other_way() -> None:
    scope = {"keys": ["a", "b"], "line": ["a", "competes", "b"]}
    back = {"op": "remove", "target": "relation", "from": "b", "rel": "competes", "to": "a"}
    rt = {"op": "retarget", "from": "b", "rel": "competes", "to": "a", "new_to": "z"}
    add = {"op": "add_relation", "from": "b", "rel": "competes", "to": "a", "reason": ""}
    other = {"op": "remove", "target": "relation", "from": "b", "rel": "competes", "to": "c"}
    kept, dropped = chat_turns.filter_ops_to_scope([back, rt, add, other], scope)
    assert kept == [back, rt, add] and dropped == [other]


def test_a_draft_row_card_is_found_from_either_end_with_the_same_turn(tmp_path: Path) -> None:
    client, root, sid = _start(tmp_path)
    try:
        _turn, row = _propose_reversed(client, root, sid)
        did = chat_sessions.load_session(root, sid)["draft_id"]
        a, b = f"draft:{did}:{row['from']}", f"draft:{did}:{row['to']}"

        def line(x: str, y: str):
            params = {"from": x, "relation": "competes", "to": y}
            return client.get("/api/chat/point", params=params)

        fwd, back = line(a, b), line(b, a)
        assert fwd.status_code == 200 and back.status_code == 200, back.text
        assert back.json()["label"] == fwd.json()["label"]
        assert back.json()["provenance"]["turn_id"] == fwd.json()["provenance"]["turn_id"]
        # 確定した線（IRI の指定）も、どちらの向きでも引ける
        z, y = shared_vocab.term_iri("zzz"), shared_vocab.term_iri("aaa")
        for x1, x2 in ((y, z), (z, y)):
            r = client.get(
                "/api/chat/point", params={"from": x1, "relation": "competesWith", "to": x2}
            )
            assert r.status_code == 200, r.text
    finally:
        client.__exit__(None, None, None)
