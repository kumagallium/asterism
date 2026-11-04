"""12b — 添えた論文から、用語・関係・問いを根拠の文つきで提案する。

JATS は ``datasets/papers/`` の実物（Docling 不要）、AI は固定応答のスタブ。確かめること: 番号つきの
文の並べ方（参考文献を除く・300 で切る）／機械の検査（実在しない番号・語が現れない・英語の語の境目・
別名で通る）／下書きの根拠の文（IRI と文はストアから）／承認 1 つで公開と確定・関係の来歴に
``prov:wasDerivedFrom``／確定の失敗で論文は公開のまま・もう一度で確定／取り消しで下書きが戻る／
AI 無し・失敗は 12a のまま／問いの提案を二重に呼ばない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import rdflib
from asterism import chat_sessions, themes
from asterism.crosswalk_runtime import ALIGNMENT_GRAPH, list_alignments
from asterism.shared_vocab import SharedTerm
from fastapi.testclient import TestClient

from asterism_api import chat_attach, chat_paper
from asterism_api.chat_paper import prep_text
from tests.test_chat_attach import JATS, KEY, _app, _attach_many, _drain, _new, _turn
from tests.test_crosswalk_api import _AUTH

PROV_FROM = "http://www.w3.org/ns/prov#wasDerivedFrom"


# ---- 文の並べ方・検査（純関数） ---------------------------------------------------------------


def _b(**kw: str) -> dict:
    return {k: {"type": "literal", "value": v} for k, v in kw.items()}


def test_mentions_uses_unicode_word_boundaries_and_ignores_width_and_case() -> None:
    said = prep_text("The lattice thermal conductivity is low in ＺＴ materials.")
    assert chat_paper.mentions(said, "Lattice Thermal Conductivity")  # 大文字小文字
    assert not chat_paper.mentions(prep_text("a subconductivity"), "conductivity")  # 語の境目
    assert not chat_paper.mentions(prep_text("Hall's effect"), "Hall")  # アポストロフィも語の一部
    assert not chat_paper.mentions(prep_text("naïveté"), "nai")
    assert chat_paper.mentions(prep_text("ゼーベック 係数が大きい"), "ゼーベック係数")  # 空白を無視
    assert chat_paper.mentions(prep_text("ゼーベック係数が大きい"), "係数")  # 日本語は部分一致
    assert chat_paper.mentions(prep_text("熱が逃げる"), "熱")  # 日本語は短くてもよい
    assert not chat_paper.mentions("anything", "  ")


def test_mentions_treats_plurals_hyphens_and_line_breaks_alike() -> None:
    pairs = [
        ("the Seebeck coefficients are large", "Seebeck coefficient"),
        ("the Seebeck coefficient is large", "Seebeck coefficients"),
        ("scattering at the grain boundaries", "grain boundary"),
        ("one grain boundary", "grain boundaries"),
        ("many boxes", "box"),
        ("a thermo-\nelectric generator", "thermoelectric"),  # 改行の前のハイフン
        ("a thermo-electric generator", "thermo electric"),
        ("a thermo electric generator", "thermo-electric"),
        ("the lattice\nthermal   conductivity", "lattice thermal conductivity"),
    ]
    for said, name in pairs:
        assert chat_paper.mentions(prep_text(said), name), (said, name)
    assert not chat_paper.mentions(prep_text("a glass"), "gla")  # 語の途中は当たらない


def test_short_latin_names_are_not_used_to_check_evidence() -> None:
    said = prep_text("ZT is high and TE devices are good")
    assert not chat_paper.mentions(said, "ZT") and not chat_paper.mentions(said, "TE")
    assert chat_paper.mentions(said, "ZT is")  # 3 文字以上なら使える


def test_reference_and_acknowledgement_sections_are_left_out() -> None:
    secs = [
        _b(sec="http://x/sec/a", title="1. Introduction", path="1", child="http://x/sec/a1"),
        _b(sec="http://x/sec/a1", title="1.1 Background", path="1-1"),
        _b(sec="http://x/sec/r", title="References", path="9"),
        _b(sec="http://x/sec/k", title="謝辞", path="10"),
        _b(sec="http://x/sec/d", title="Notes", path="11",
           role="http://purl.org/spar/deo/Acknowledgements"),
    ]  # fmt: skip
    sents = [
        _b(s="http://x/s/1", t="Intro.", b="0", sec="http://x/sec/a"),
        _b(s="http://x/s/2", t="Sub.", b="10", sec="http://x/sec/a1"),
        _b(s="http://x/s/3", t="Ref 1.", b="20", sec="http://x/sec/r"),
        _b(s="http://x/s/4", t="ありがとう。", b="30", sec="http://x/sec/k"),
        _b(s="http://x/s/5", t="By role.", b="40", sec="http://x/sec/d"),
    ]
    rows = chat_paper.build_rows(sents, secs)
    assert [(r["text"], r["skip"]) for r in rows] == [
        ("Intro.", False), ("Sub.", False), ("Ref 1.", True), ("ありがとう。", True),
        ("By role.", True),
    ]  # fmt: skip
    picked, truncated = chat_paper.select_sentences(rows)
    assert [(r["id"], r["text"]) for r in picked] == [("s1", "Intro."), ("s2", "Sub.")]
    assert truncated is False
    assert rows[1]["path"] == "1-1"  # 文の属する節のパス


def test_titles_that_cannot_be_read_leave_every_sentence_in() -> None:
    sents = [_b(s=f"http://x/s/{i}", t=f"S{i}.", b=str(i), sec="http://x/sec/_body")
             for i in range(3)]  # fmt: skip
    secs = [_b(sec="http://x/sec/_body", title="", path="")]
    rows = chat_paper.build_rows(sents, secs)
    assert not any(r["skip"] for r in rows) and rows[0]["path"] is None


def test_more_than_300_sentences_keep_abstract_results_and_conclusion_first() -> None:
    secs = [
        _b(sec="http://x/sec/i", title="Introduction", role="http://purl.org/spar/deo/Introduction"),
        _b(sec="http://x/sec/r", title="Results", role="http://purl.org/spar/deo/Results"),
    ]
    sents = [_b(s=f"http://x/i/{i}", t=f"Intro {i}.", b=str(i), sec="http://x/sec/i")
             for i in range(250)]  # fmt: skip
    sents += [_b(s=f"http://x/r/{i}", t=f"Result {i}.", b=str(1000 + i), sec="http://x/sec/r")
              for i in range(120)]  # fmt: skip
    picked, truncated = chat_paper.select_sentences(chat_paper.build_rows(sents, secs))
    assert truncated and len(picked) == chat_paper.MAX_SENTENCES
    assert [r["id"] for r in picked[:2]] == ["s1", "s2"]  # 出現順に振り直す
    assert sum(1 for r in picked if r["text"].startswith("Result")) == 120  # 優先は全部残る
    assert sum(1 for r in picked if r["text"].startswith("Intro")) == 180
    assert [r["begin"] for r in picked] == sorted(r["begin"] for r in picked)
    # 役割が 1 つも取れなければ先頭から
    plain = [_b(s=f"http://x/p/{i}", t=f"P{i}.", b=str(i), sec="http://x/sec/p")
             for i in range(400)]  # fmt: skip
    picked, truncated = chat_paper.select_sentences(
        chat_paper.build_rows(plain, [_b(sec="http://x/sec/p", title="Body")])
    )
    assert truncated and [r["text"] for r in picked][-1] == "P299."


def _term(slug: str, label: str, en: str, kind: str, aliases: tuple[str, ...] = ()) -> SharedTerm:
    return SharedTerm(iri=f"https://x/sv/{slug}", slug=slug, kind=kind, label=label,
                      label_en=en, comment=None, created_at="t", aliases=aliases)  # fmt: skip


def test_check_ops_drops_what_the_sentences_do_not_support() -> None:
    by_id = {
        "s1": {"id": "s1", "iri": "http://x/s/1", "path": "2-5",
               "text": "Scattering at the grain boundaries lowers the lattice thermal "
                       "conductivity."},
        "s2": {"id": "s2", "iri": "http://x/s/2", "path": None, "text": "Seebeck is large. " * 20},
    }  # fmt: skip
    c = lambda key, ja, en, kind, **kw: {  # noqa: E731
        "op": "add_concept", "key": key, "label_ja": ja, "label_en": en, "aliases": [],
        "kind": kind, "use": "new", "reason": "", **kw,
    }  # fmt: skip
    rel = lambda a, r, b, ev: {  # noqa: E731
        "op": "add_relation", "from": a, "rel": r, "to": b, "evidence": ev, "reason": ""
    }  # fmt: skip
    ops = [
        c("n1", "粒界", "grain boundaries", "class"),
        c("n2", "格子熱伝導率", "lattice thermal conductivity", "property"),
        c("n3", "ゼーベック", "Seebeck coefficient", "property"),
        c("n4", "導電", "conductivity", "property"),
        c("n5", "散乱", "x", "class", aliases=["scattering"]),  # 別名で通る
        rel("n1", "lowers", "n2", ["s1"]),  # 通る（両端が文に現れる）
        rel("n1", "lowers", "n2", ["s9"]),  # 番号の文が無い
        rel("n3", "affects", "n2", ["s1"]),  # n3 の名前が s1 に無い
        rel("n5", "lowers", "n2", ["s1"]),  # 別名 scattering で通る
        rel("n1", "lowers", "n4", ["s1"]),  # conductivity は lattice thermal conductivity の中で
        # 語の境目では現れる（"thermal conductivity" の前は空白）— ただし n1 は通るので通る
        rel("n1", "lowers", "c9", ["s1"]),  # 知らない端
        {"op": "add_question", "question": "何が下げる？", "mode": "knowledge",
         "op_kind": "factors", "target": "n2", "direction": "lowers", "reason": ""},
    ]  # fmt: skip
    kept, dropped = chat_paper.check_ops(ops, concepts=[], existing=[], by_id=by_id)
    rels = [o for o in kept if o["op"] == "add_relation"]
    assert [(o["from"], o["to"]) for o in rels] == [("n1", "n2"), ("n5", "n2"), ("n1", "n4")]
    assert dropped == 3
    assert [o["op"] for o in kept].count("add_concept") == 5  # 概念は残す
    assert [o["op"] for o in kept].count("add_question") == 1
    assert rels[0]["evidence"] == [{"iri": "http://x/s/1", "text": by_id["s1"]["text"][:150],
                                    "path": "2-5"}]  # fmt: skip


def test_check_ops_uses_the_store_names_and_aliases_of_existing_terms() -> None:
    by_id = {"s1": {"id": "s1", "iri": "http://x/s/1", "path": None,
                    "text": "熱電が大きい。The merit index is high."}}  # fmt: skip
    existing = [_term("zt", "性能指数", "figure of merit", "property", aliases=("merit index",))]
    ops = [
        {"op": "add_concept", "key": "n1", "label_ja": "性能指数", "label_en": "", "aliases": [],
         "kind": "property", "use": "existing:zt", "reason": ""},  # 別名 merit index が文にある
        {"op": "add_concept", "key": "n2", "label_ja": "熱電", "label_en": "", "aliases": [],
         "kind": "property", "use": "new", "reason": ""},
        {"op": "add_relation", "from": "n2", "rel": "raises", "to": "n1", "evidence": ["s1"],
         "reason": ""},
    ]  # fmt: skip
    kept, dropped = chat_paper.check_ops(ops, concepts=[], existing=existing, by_id=by_id)
    assert dropped == 0 and kept[-1]["evidence"][0]["iri"] == "http://x/s/1"
    # 下書きの概念（既存の用語に結んだ行）も、ストアの名前で引く
    row = {"key": "c1", "label_ja": "別の言い方", "label_en": "", "aliases": [], "kind": "property",
           "use": "existing:zt"}  # fmt: skip
    ops2 = [
        {"op": "add_relation", "from": "n2", "rel": "raises", "to": "c1", "evidence": ["s1"],
         "reason": ""},
    ]  # fmt: skip
    kept, dropped = chat_paper.check_ops(
        [ops[1], *ops2], concepts=[row], existing=existing, by_id=by_id
    )
    assert dropped == 0


# ---- 走者・承認・取り消し（本物のストア＋AI のスタブ） --------------------------------------


def _add_concept(key: str, ja: str, en: str, kind: str, **kw: Any) -> dict:
    return {"op": "add_concept", "key": key, "label_ja": ja, "label_en": en, "aliases": [],
            "kind": kind, "use": "new", "reason": "本文にある", **kw}  # fmt: skip


def _answer(sentences: dict[str, str], *, ask: bool = True) -> dict:
    """スタブの答え。実物の論文（PMC5951533）の文の語から作る。"""
    ops: list[dict] = [
        _add_concept("n1", "粒界と相の境界", "grain and phase boundaries", "class"),
        _add_concept("n2", "熱伝導率", "thermal conductivity", "property"),
        _add_concept("n3", "ゼーベック係数", "Seebeck coefficient", "property"),
        _add_concept("n4", "性能指数", "figure of merit", "property"),
        {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "n2",
         "evidence": [sentences["boundary"]], "reason": "散乱で下がる"},
        {"op": "add_relation", "from": "n2", "rel": "affects", "to": "n4",
         "evidence": [sentences["seebeck"]], "reason": "低い熱伝導率が効く"},
        {"op": "add_relation", "from": "n3", "rel": "raises", "to": "n4",
         "evidence": [sentences["seebeck"]], "reason": "支配的"},
        {"op": "add_relation", "from": "n1", "rel": "affects", "to": "n4",
         "evidence": [sentences["seebeck"]], "reason": "粒界はその文に無い"},  # 落ちる（名前）
        {"op": "add_relation", "from": "n3", "rel": "affects", "to": "n2",
         "evidence": ["s9999"], "reason": "番号が無い"},  # 落ちる（番号）
    ]  # fmt: skip
    if ask:
        ops.append({"op": "add_question", "question": "格子熱伝導率を下げる要因は？",
                    "mode": "knowledge", "op_kind": "factors", "target": "n2",
                    "direction": "lowers", "reason": ""})  # fmt: skip
    return {"reply": "AI の地の文（保存しない）", "ops": ops}


class _PaperLLM:
    """論文の提案の呼び出し（graph_propose）だけに答えるスタブ。"""

    def __init__(self, numbers: dict[str, str] | None = None, *, fail: bool = False,
                 ask: bool = True) -> None:  # fmt: skip
        self.numbers = numbers
        self.fail = fail
        self.ask = ask
        self.users: list[str] = []

    def complete(self, system: str, user: str) -> str:
        assert "numbered SENTENCES" in system, system[:80]
        self.users.append(user)
        if self.fail:
            raise RuntimeError("AI down")
        return json.dumps(_answer(self.numbers or {}, ask=self.ask), ensure_ascii=False)


def _numbers(user: str) -> dict[str, str]:
    """プロンプトの番号つきの文（``- s12 | 文``）から、使う 2 つの文の番号を引く。"""
    rows = {m[1]: m[2] for m in (re.match(r"- (s\d+) \| (.*)", x) for x in user.splitlines()) if m}

    def find(needle: str) -> str:
        hit = [k for k, v in rows.items() if needle in v]
        assert hit, needle  # スタブの中の失敗は、固まらずに見える形で
        return hit[0]

    return {
        "boundary": find("additional scattering at the grain and phase boundaries"),
        "seebeck": find("The values of the figure of merit are dominated by the high values"),
    }


class _TwoStep(_PaperLLM):
    """1 回目の呼び出しでプロンプトから番号を引き、答えを返す。"""

    def complete(self, system: str, user: str) -> str:
        if self.numbers is None:
            self.numbers = _numbers(user)
        return super().complete(system, user)


def _paper(client: TestClient, sid: str, headers: dict | None = KEY) -> dict:
    r = _attach_many(client, sid, [("論文.xml", JATS)], headers=headers if headers else {})
    assert r.status_code == 201, r.text
    body = r.json()
    _drain(client, body["job_id"])
    return _turn(client, sid, body["turn"]["id"])


def _confirm(client: TestClient, sid: str, tid: str, **body: Any):
    return client.post(f"/api/chat/sessions/{sid}/turns/{tid}/confirm", json=body, headers=KEY)


def test_a_paper_with_an_ai_proposes_terms_relations_and_questions_with_evidence(
    tmp_path: Path,
) -> None:
    llm = _TwoStep()
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        assert turn["state"] == "proposed" and turn["attachment"]["kind"] == "document"
        assert "用語 4・関係 3・問い 1 を提案しました" in turn["reply"]["text"]
        assert "AI の地の文" not in turn["reply"]["text"]  # AI の文は保存しない
        ops = turn["reply"]["ops"]
        rels = [o for o in ops if o["op"] == "add_relation"]
        assert len(rels) == 3 and len(turn["reply"]["sentences"]) == len(ops)
        notes = [n["text"] for n in turn["reply"]["notes"]]
        assert "根拠の文が合わない関係を 2 件除きました" in notes
        # 根拠の文はストアから引いた文（IRI・150 字以内・節の path）。AI の文字列ではない
        for r in rels:
            (ev,) = r["evidence"]
            assert ev["iri"].startswith("https://") and "/sent/" in ev["iri"]
            assert 0 < len(ev["text"]) <= 1000 and ev["path"]
        assert rels[0]["evidence"][0]["text"].startswith("All samples with a decomposition")
        assert rels[1]["evidence"] == rels[2]["evidence"]  # 同じ文を根拠にした 2 本
        # 下書き: 関係に evidence・問いが入る・話の下書きになる
        session = chat_sessions.load_session(tmp_path / "registry", sid)
        draft = themes.load_draft(tmp_path / "registry", session["draft_id"])
        assert draft is not None and len(draft["concepts"]) == 4
        assert all(r["evidence"] and r["by"] == "ai" for r in draft["relations"])
        assert draft["questions"] == ["格子熱伝導率を下げる要因は？"]
        assert turn["paper"] == {
            "draft_id": draft["id"], "created": True, "promoted": False,
            "concepts": ["n1", "n2", "n3", "n4"], "questions": ["格子熱伝導率を下げる要因は？"],
        }  # fmt: skip
        assert turn["before"] is None
        # AI に渡ったのは番号つきの文（参考文献・謝辞は除く）
        user = llm.users[0]
        assert "# Sentences of the paper" in user and "gratefully acknowledge" not in user
        assert "s1 | The search for alternative energy technologies" in user
        # 12a の札（論文名・節・文）はそのまま
        assert turn["attachment"]["sentences"] > 0


def test_one_approval_publishes_the_paper_and_confirms_the_draft_with_evidence(
    tmp_path: Path,
) -> None:
    seen: list[dict] = []

    async def spy(*a: Any, **kw: Any) -> list[dict]:
        seen.append(kw)
        return []

    app, ds = _app(tmp_path, _TwoStep())
    real = chat_attach.suggest_questions
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        dsid = turn["attachment"]["dataset_id"]
        chat_attach.suggest_questions = spy  # type: ignore[assignment]
        try:
            r = _confirm(client, sid, turn["id"])
        finally:
            chat_attach.suggest_questions = real  # type: ignore[assignment]
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["turn"]["state"] == "confirmed" and body["confirmed"] == [turn["id"]]
        assert body["commit"]["dataset_id"] == dsid and body["commit"]["theme_id"]
        assert not seen  # この手番に問いの提案がある — 10b の問いの提案は呼ばない
        meta = json.loads((tmp_path / "registry" / dsid / "meta.json").read_text())
        assert meta["promoted"] is True  # 論文の公開
        # 関係の来歴に根拠の文 IRI（prov:wasDerivedFrom）
        derived = {
            (str(s), str(o))
            for s, p, o in ds.graph(rdflib.URIRef(ALIGNMENT_GRAPH))
            if str(p) == PROV_FROM
        }
        assert len(derived) == 3 and all("/sent/" in o for _, o in derived)
        rows = [x for x in _run(list_alignments(_Wrap(ds))) if x["evidence"]]
        assert len(rows) == 3 and all(len(x["evidence"]) == 1 for x in rows)
        # 話: 下書きは確定して消え、テーマが置かれる
        session = chat_sessions.load_session(tmp_path / "registry", sid)
        assert session["draft_id"] is None and session["theme_id"]
        assert themes.list_drafts(tmp_path / "registry") == []


def test_a_failed_confirmation_keeps_the_paper_published_and_a_second_approval_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, ds = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        dsid = turn["attachment"]["dataset_id"]
        real = themes.commit_theme

        async def boom(*a: Any, **kw: Any) -> dict:
            raise themes.ThemeError("検査で断られました")

        monkeypatch.setattr(themes, "commit_theme", boom)
        r = _confirm(client, sid, turn["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["turn"]["state"] == "proposed" and body["commit"] is None
        texts = [n["text"] for n in body["turn"]["reply"]["notes"]]
        assert "提案は確定できませんでした（論文は公開しました）" in texts
        meta = json.loads((tmp_path / "registry" / dsid / "meta.json").read_text())
        assert meta["promoted"] is True
        session = chat_sessions.load_session(tmp_path / "registry", sid)
        assert session["draft_id"]  # 下書きは残る
        assert body["turn"]["paper"]["promoted"] is True
        # もう一度 ✓ — 公開はやり直さず、確定だけ
        monkeypatch.setattr(themes, "commit_theme", real)
        r = _confirm(client, sid, turn["id"])
        assert r.status_code == 200, r.text
        assert r.json()["turn"]["state"] == "confirmed" and r.json()["commit"]["theme_id"]
        assert [x for x in _run(list_alignments(_Wrap(ds))) if x["evidence"]]


def test_undoing_an_unapproved_paper_discards_the_dataset_and_the_draft_it_made(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        dsid = turn["attachment"]["dataset_id"]
        assert themes.list_drafts(tmp_path / "registry")
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/revert")
        assert r.status_code == 200, r.text
        assert r.json()["turn"]["state"] == "reverted" and r.json()["reverted"] == [turn["id"]]
        assert themes.list_drafts(tmp_path / "registry") == []
        assert not (tmp_path / "registry" / dsid).exists()  # データセットも片づく
        assert chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"] is None


def test_undoing_a_paper_puts_back_the_draft_it_was_added_to(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        root = tmp_path / "registry"
        at = "2026-11-04T00:00:00+00:00"
        made = themes.create_draft(root, "熱", ["熱を下げるものは？"], at=at)
        row = {"key": "h1", "label_ja": "比熱", "label_en": "", "aliases": [],
               "kind": "property", "use": "new", "slug": "", "reason": "", "match": None,
               "by": "human"}  # fmt: skip
        ans = themes._answer_row(
            "熱を下げるものは？", "knowledge", op="factors", target="h1", direction="lowers",
            kind="", prop="", value="", match="", prop2="", kind2="", memo=None,
            wanted_data="", reason="",
        )  # fmt: skip
        before = themes.save_draft(
            root, {**made, "step": 2, "concepts": [row], "answers": [ans]}
        )
        session = chat_sessions.load_session(root, sid)
        session["draft_id"] = before["id"]
        chat_sessions.save_session(root, session)
        turn = _paper(client, sid)
        mid = themes.load_draft(root, before["id"])
        assert len(mid["concepts"]) == 1 + 4 and turn["paper"]["created"] is False
        assert turn["before"]["concepts"] == before["concepts"]  # 当てる前の下書きを控える
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/revert")
        assert r.status_code == 200, r.text
        after = themes.load_draft(root, before["id"])
        assert [c["label_ja"] for c in after["concepts"]] == ["比熱"]
        assert after["relations"] == [] and after["questions"] == before["questions"]
        assert chat_sessions.load_session(root, sid)["draft_id"] == before["id"]


def test_without_an_ai_the_paper_is_attached_as_in_12a(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)  # AI 未設定
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid, headers=None)
        assert turn["state"] == "proposed" and "paper" not in turn
        assert turn["reply"]["ops"] == [] and "提案しました" not in turn["reply"]["text"]
        assert themes.list_drafts(tmp_path / "registry") == []


def test_an_ai_that_fails_leaves_the_paper_with_a_note(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _PaperLLM({}, fail=True))
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        assert turn["state"] == "proposed" and "paper" not in turn
        assert turn["reply"]["ops"] == []
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert "論文からの提案を作れませんでした（論文は添えました）" in texts
        assert themes.list_drafts(tmp_path / "registry") == []


def test_a_proposal_that_cannot_be_confirmed_is_not_shown_and_the_paper_is_attached_alone(
    tmp_path: Path,
) -> None:
    """答え方の決まった問いが無いと ``commit_theme`` は断る。提案の時点で同じ検査（書かずに）を
    走らせ、通らなければ提案を出さず論文だけにする。承認は 12a のとおり公開だけ。"""
    class _NoAsk(_TwoStep):
        def __init__(self) -> None:
            super().__init__(ask=False)

    app, _ = _app(tmp_path, _NoAsk())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        assert "paper" not in turn and turn["reply"]["ops"] == []
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert "論文からの提案を、問いにつながる形にできませんでした（論文は添えました）" in texts
        assert themes.list_drafts(tmp_path / "registry") == []  # 作った下書きは残さない
        assert chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"] is None
        r = _confirm(client, sid, turn["id"])
        assert r.status_code == 200 and r.json()["turn"]["state"] == "confirmed"
        dsid = turn["attachment"]["dataset_id"]
        meta = json.loads((tmp_path / "registry" / dsid / "meta.json").read_text())
        assert meta["promoted"] is True


def test_a_term_in_no_relation_or_question_is_dropped_from_the_proposal(tmp_path: Path) -> None:
    class _Orphan(_TwoStep):
        def complete(self, system: str, user: str) -> str:
            if self.numbers is None:
                self.numbers = _numbers(user)
            out = _answer(self.numbers)
            out["ops"].append(_add_concept("n9", "孤立した語", "orphan term", "property"))
            return json.dumps(out, ensure_ascii=False)

    app, _ = _app(tmp_path, _Orphan())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert "どの関係にも問いにも出ない用語を 1 件除きました" in texts
        assert "用語 4・関係 3・問い 1" in turn["reply"]["text"]
        draft = themes.load_draft(
            tmp_path / "registry",
            chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"],
        )
        assert "孤立した語" not in [c["label_ja"] for c in draft["concepts"]]
        assert _confirm(client, sid, turn["id"]).json()["turn"]["state"] == "confirmed"


def test_prune_unlinked_keeps_existing_terms_and_drops_questions_about_dropped_ones() -> None:
    c = lambda k, use="new": {"op": "add_concept", "key": k, "use": use}  # noqa: E731
    ops = [
        c("a"), c("b"), c("e", "existing:zt"), c("z"),
        {"op": "add_relation", "from": "a", "rel": "lowers", "to": "b"},
        {"op": "add_question", "target": "b", "kind": None},
    ]  # fmt: skip
    kept, n = chat_paper.prune_unlinked(ops)
    assert n == 1 and [o["key"] for o in kept if o["op"] == "add_concept"] == ["a", "b", "e"]
    assert chat_paper.prune_unlinked(ops[:3])[1] == 2  # 関係も問いも無ければ a・b も落ちる


def test_text_approvals_wait_for_an_unpublished_paper_that_they_would_touch(tmp_path: Path) -> None:
    """公開していない論文の根拠つきの関係・その論文が足した用語と問いに触れる ✓ は 409。論文を
    承認すれば下書き全部が一度に確定する。"""
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        root = tmp_path / "registry"
        dsid = turn["attachment"]["dataset_id"]
        hand = [
            {"op": "add_concept", "key": "h1", "label_ja": "粒界散乱", "label_en": "",
             "aliases": [], "kind": "class", "use": "new", "reason": ""},
            {"op": "add_relation", "from": "h1", "rel": "lowers", "to": "n2", "reason": ""},
        ]  # fmt: skip
        r = client.post(f"/api/chat/sessions/{sid}/turns", json={"ops": hand})
        assert r.status_code == 201, r.text
        mine = r.json()["turn"]
        r = _confirm(client, sid, mine["id"])  # 全部の ✓
        assert r.status_code == 409
        assert "論文〈論文〉の提案が承認待ちです。先に論文を承認するか取り消してください" in r.text
        rel = next(x for x in themes.load_draft(root, chat_sessions.load_session(root, sid)[
            "draft_id"])["relations"] if x.get("evidence"))
        r = _confirm(client, sid, mine["id"], only={"relations": [
            {"from": rel["from"], "rel": rel["rel"], "to": rel["to"]}], "concepts": []})
        assert r.status_code == 409  # 論文の行の 1 行の承認も
        assert not json.loads((root / dsid / "meta.json").read_text()).get("promoted")
        r = _confirm(client, sid, turn["id"])  # 論文の ✓ — 公開して、手で足した分も含め全部確定
        assert r.status_code == 200, r.text
        assert r.json()["turn"]["state"] == "confirmed" and r.json()["commit"]["theme_id"]
        assert _turn(client, sid, mine["id"])["state"] == "confirmed"
        assert chat_sessions.load_session(root, sid)["draft_id"] is None


def test_a_proposal_that_breaks_after_a_hand_edit_stops_before_the_paper_is_published(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        root = tmp_path / "registry"
        dsid = turn["attachment"]["dataset_id"]
        orphan = [{"op": "add_concept", "key": "h1", "label_ja": "孤立", "label_en": "",
                   "aliases": [], "kind": "class", "use": "new", "reason": ""}]  # fmt: skip
        r = client.post(f"/api/chat/sessions/{sid}/turns", json={"ops": orphan})
        assert r.status_code == 201
        r = _confirm(client, sid, turn["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["turn"]["state"] == "proposed" and body["commit"] is None
        assert any(n["kind"] == "commit_error" and "孤立" in n["text"]
                   for n in body["turn"]["reply"]["notes"])  # fmt: skip
        assert not json.loads((root / dsid / "meta.json").read_text()).get("promoted")
        assert body["turn"]["paper"]["promoted"] is False  # 論文は公開していない


def test_undoing_after_the_paper_was_published_keeps_the_paper_and_drops_the_proposals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        root = tmp_path / "registry"
        dsid = turn["attachment"]["dataset_id"]

        async def boom(*a: Any, **kw: Any) -> dict:
            raise themes.ThemeError("書けませんでした")

        monkeypatch.setattr(themes, "commit_theme", boom)
        assert _confirm(client, sid, turn["id"]).json()["turn"]["state"] == "proposed"
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/revert")
        assert r.status_code == 200, r.text
        got = r.json()["turn"]
        assert got["state"] == "confirmed" and got["commit"]["dataset_id"] == dsid
        assert got["reply"]["ops"] == []
        texts = [n["text"] for n in got["reply"]["notes"]]
        assert "提案は取り消しました（論文は公開済みです）" in texts
        assert (root / dsid).exists()  # データセットは消さない
        assert json.loads((root / dsid / "meta.json").read_text())["promoted"] is True
        assert themes.list_drafts(root) == []
        assert chat_sessions.load_session(root, sid)["draft_id"] is None


def test_the_one_row_approval_skips_the_unpublished_paper_s_rows_only(tmp_path: Path) -> None:
    """1 行の承認（11o）は、論文の根拠つきの行・用語には 409、触れない手の行には効く。"""

    class _Seq(_TwoStep):
        """1 回目 = いつもの答え・2 回目 = 別の 1 語と 1 本の関係。"""

        def complete(self, system: str, user: str) -> str:
            if self.users:
                self.users.append(user)
                rows = {
                    m[1]: m[2]
                    for m in (re.match(r"- (s\d+) \| (.*)", x) for x in user.splitlines())
                    if m
                }
                num = next(k for k, v in rows.items() if "the specific heat capacity" in v)
                known = re.search(r"^- (\S+) \| 熱伝導率 \|", user, re.M)
                assert known, user  # 1 回目で承認した用語が、種として図に載っている
                return json.dumps({"reply": "", "ops": [
                    _add_concept("n1", "比熱", "specific heat capacity", "property"),
                    {"op": "add_relation", "from": "n1", "rel": "affects", "to": known[1],
                     "evidence": [num], "reason": "式に出る"},
                ]}, ensure_ascii=False)  # fmt: skip
            return super().complete(system, user)

    app, _ = _app(tmp_path, _Seq())
    root = tmp_path / "registry"
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        first = _paper(client, sid)
        assert _confirm(client, sid, first["id"]).status_code == 200  # テーマが置かれる
        second = _paper(client, sid)
        assert second["paper"]["created"] is True and second["state"] == "proposed"
        draft = themes.load_draft(root, chat_sessions.load_session(root, sid)["draft_id"])
        rel = next(r for r in draft["relations"] if r.get("evidence"))
        known = rel["to"]
        r = _confirm(
            client, sid, second["id"],
            only={"relations": [{"from": rel["from"], "rel": rel["rel"], "to": rel["to"]}],
                  "concepts": []},
        )  # fmt: skip
        assert r.status_code == 409 and "承認待ち" in r.text
        # 論文に触れない手の行（手の用語から種の用語へ）は通る
        hand = [
            {"op": "add_concept", "key": "h1", "label_ja": "粒径", "label_en": "", "aliases": [],
             "kind": "property", "use": "new", "reason": ""},
            {"op": "add_relation", "from": "h1", "rel": "affects", "to": known, "reason": ""},
        ]  # fmt: skip
        r = client.post(f"/api/chat/sessions/{sid}/turns", json={"ops": hand})
        assert r.status_code == 201, r.text
        mine = r.json()["turn"]
        r = _confirm(
            client, sid, mine["id"],
            only={"relations": [{"from": "h1", "rel": "affects", "to": known}], "concepts": []},
        )  # fmt: skip
        assert r.status_code == 200, r.text
        assert not [n for n in r.json()["turn"]["reply"]["notes"] if n["kind"] == "commit_error"]
        assert _turn(client, sid, second["id"])["state"] == "proposed"  # 論文はまだ


# ---- 小道具 -------------------------------------------------------------------------------------


class _Wrap:
    """``list_alignments`` に渡す、テストの Dataset を包んだ client。"""

    def __new__(cls, ds: rdflib.Dataset) -> Any:
        from tests.test_crosswalk_api import _DatasetClient

        return _DatasetClient(ds)


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
