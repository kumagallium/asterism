"""相談の適用（ホーム）で、向きの無い関係「競合する」を逆向きでも同じ線と見る。

既にある (A, B) に ``add_relation B competes A`` が来たら ``duplicate``（来歴を上書きしない）・
``remove`` は逆向きでも見つかる・同じ依頼の中で A→B のあとの B→A は 2 本目＝重複。語は作り物。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_consult_graph_api import _LLM, KEY, _app, _lines, _mint
from tests.test_crosswalk_api import _AUTH
from tests.test_upper_questions import SV


def _op(op: str, frm: str, to: str, **extra: object) -> dict:
    base = {"op": op, "from": frm, "rel": "competes", "to": to, "reason": ""}
    return {**base, **extra}


def _competes(store) -> list[tuple[str, str]]:
    return [(r["source"], r["target"]) for r in _lines(store) if r["relation"] == "competesWith"]


def test_a_reversed_competes_is_a_duplicate_and_keeps_the_provenance(tmp_path: Path) -> None:
    app, store = _app(tmp_path, _LLM())
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "aaa", "Mg2Si", kind="class")
        _mint(client, "zzz", "Mg2Sn", kind="class")

        def post(ops: list[dict], pid: str, **kw: object) -> dict:
            return client.post(
                "/api/consult/graph/apply",
                json={"screen": "home", "context": {}, "ops": ops, "proposal_id": pid, **kw},
            ).json()

        first = post([{**_op("add_relation", "aaa", "zzz"), "reason": "人の理由"}], "p1",
                     batch="b-human")  # fmt: skip
        assert first["applied"] == 1
        back = post([{**_op("add_relation", "zzz", "aaa"), "reason": "AI の理由"}], "p2",
                    batch="b-ai")  # fmt: skip
        assert (back["applied"], back["duplicate"]) == (0, True)
        assert [d["reason"] for d in back["dropped"]] == ["duplicate"]
        (row,) = [r for r in _lines(store) if r["relation"] == "competesWith"]
        assert (row["batch"], row["reason"]) == ("b-human", "人の理由")  # 上書きされていない
        # 逆向きの remove も見つかる
        gone = post([_op("remove", "zzz", "aaa", target="relation")], "p3")
        assert gone["applied"] == 1 and not gone["dropped"]
        assert _competes(store) == []


def test_a_then_b_in_one_request_is_one_line(tmp_path: Path) -> None:
    app, store = _app(tmp_path, _LLM())
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        _mint(client, "aaa", "Mg2Si", kind="class")
        _mint(client, "zzz", "Mg2Sn", kind="class")
        out = client.post(
            "/api/consult/graph/apply",
            json={"screen": "home", "context": {}, "proposal_id": "p1",
                  "ops": [_op("add_relation", "zzz", "aaa"), _op("add_relation", "aaa", "zzz")]},
        ).json()  # fmt: skip
        assert out["applied"] == 1
        assert [(d["index"], d["reason"]) for d in out["dropped"]] == [(1, "duplicate")]
        assert _competes(store) == [(SV + "aaa", SV + "zzz")]
