"""12b — 添えた論文から、用語・関係・問いを根拠の文つきで提案する。

JATS は ``datasets/papers/`` の実物（Docling 不要）、AI は固定応答のスタブ。確かめること: 番号つきの
文の並べ方（参考文献を除く・上限で切る）／機械の検査（実在しない番号・語が現れない・英語の語の境目・
別名で通る）／下書きの根拠の文（IRI と文はストアから）／承認 1 つで公開と確定・関係の来歴に
``prov:wasDerivedFrom``／確定の失敗で論文は公開のまま・もう一度で確定／取り消しで下書きが戻る／
AI 無し・失敗は 12a のまま／問いの提案を二重に呼ばない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

import pytest
import rdflib
from asterism import chat_sessions, shared_vocab, themes
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


def test_more_than_the_limit_sentences_keep_abstract_results_and_conclusion_first() -> None:
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
    n_intro = chat_paper.MAX_SENTENCES - 120
    assert sum(1 for r in picked if r["text"].startswith("Intro")) == n_intro
    assert [r["begin"] for r in picked] == sorted(r["begin"] for r in picked)
    # 役割が 1 つも取れなければ先頭から
    plain = [_b(s=f"http://x/p/{i}", t=f"P{i}.", b=str(i), sec="http://x/sec/p")
             for i in range(400)]  # fmt: skip
    picked, truncated = chat_paper.select_sentences(
        chat_paper.build_rows(plain, [_b(sec="http://x/sec/p", title="Body")])
    )
    assert truncated and [r["text"] for r in picked][-1] == f"P{chat_paper.MAX_SENTENCES - 1}."


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


def _r(frm: str, rel: str, to: str, ev: str = "s1", **ja: str) -> dict:
    return {"from": frm, "from_ja": ja.get("f"), "rel": rel, "to": to, "to_ja": ja.get("t"),
            "evidence": [ev]}  # fmt: skip


def test_build_ops_merges_names_infers_kinds_and_drops_conflicts() -> None:
    rels = [
        _r("Grain boundary", "lowers", "thermal conductivity", f="粒界", t="熱伝導率"),
        _r("grain  boundaries", "affects", "Figure-of-Merit"),  # 大文字小文字・空白・単数複数
        _r("thermal conductivity", "affects", "figure of merit"),
        _r("heat", "part", "figure of merit"),  # part の両端の種類が違う（class と property）
        _r("heat", "sub", "heat"),  # 自分自身
    ]
    ops, dropped = chat_paper.build_ops(rels, [])
    concepts = {o["key"]: o for o in ops if o["op"] == "add_concept"}
    assert [(c["label_en"], c["label_ja"], c["kind"]) for c in concepts.values()] == [
        ("Grain boundary", "粒界", "class"),
        ("thermal conductivity", "熱伝導率", "property"),
        ("Figure-of-Merit", "Figure-of-Merit", "property"),
    ]
    assert concepts["n1"]["aliases"] == ["grain  boundaries"]
    rel_ops = [o for o in ops if o["op"] == "add_relation"]
    assert [(o["from"], o["rel"], o["to"]) for o in rel_ops] == [
        ("n1", "lowers", "n2"), ("n1", "affects", "n3"), ("n2", "affects", "n3"),
    ]  # fmt: skip
    assert dropped == 2 and all(o["evidence"] == ["s1"] for o in rel_ops)


def test_build_ops_uses_existing_terms_by_name_or_alias_and_keeps_their_kind() -> None:
    existing = [_term("zt", "性能指数", "figure of merit", "property", aliases=("ZT value",))]
    rels = [
        _r("Seebeck coefficient", "raises", "ZT values"),  # 別名（単数複数の違い）
        _r("figure of merit", "sub", "性能指数"),  # 同じ既存の用語どうし → 自分自身で落ちる
        _r("sample", "part", "figure of merit"),  # 既存は property・sample は class → 落ちる
    ]
    ops, dropped = chat_paper.build_ops(rels, existing)
    by = {o["label_en"] or o["label_ja"]: o for o in ops if o["op"] == "add_concept"}
    zt = by["figure of merit"]  # 既存の名前（書かれた "ZT values" ではなく）を使う
    assert zt["use"] == "existing:zt" and zt["kind"] == "property" and zt["label_ja"] == "性能指数"
    assert "ZT values" in zt["aliases"]  # 書かれた名前は別名（照合に使う）
    assert by["Seebeck coefficient"]["use"] == "new"
    assert dropped == 2 and "sample" not in by


def _chain_ops(n_components: int, lang: str = "ja") -> tuple[list[dict], int]:
    rels = []
    for i in range(n_components):  # 成分ごとに 2 本（a_i, b_i → t_i）
        rels += [_r(f"a{i} term", "lowers", f"t{i} term"), _r(f"b{i} term", "lowers", f"t{i} term")]
    ops, _ = chat_paper.build_ops(rels, [])
    return chat_paper.add_questions(ops, lang)


def test_questions_are_the_fewest_that_reach_every_term_and_at_most_five() -> None:
    ops, dropped = _chain_ops(1)
    qs = [o for o in ops if o["op"] == "add_question"]
    assert dropped == 0 and len(qs) == 1  # 1 つの網には問い 1 つ
    assert qs[0]["target"] == "n2" and qs[0]["op_kind"] == "factors"
    assert qs[0]["direction"] == "lowers" and qs[0]["question"] == "t0 term を下げるものは？"
    ops, dropped = _chain_ops(7)  # 7 つの網 → 問いは 5 つまで・残りの網の関係は落とす
    qs = [o for o in ops if o["op"] == "add_question"]
    assert len(qs) == 5 and dropped == 4
    assert len([o for o in ops if o["op"] == "add_relation"]) == 10
    # すべての問いが、残った用語から関係でたどれる（問いに入らない網の用語は prune される）
    kept, _ = chat_paper.prune_unlinked(ops)
    assert len([o for o in kept if o["op"] == "add_concept"]) == 15


def test_questions_follow_the_relation_kind_in_japanese_and_english() -> None:
    cases = {
        "part": ("{} の一部は？", "What are the parts of {}?", "parts", None),
        "sub": ("{} の種類は？", "What are the kinds of {}?", "kinds", None),
        "lowers": ("{} を下げるものは？", "What lowers {}?", "factors", "lowers"),
        "raises": ("{} を上げるものは？", "What raises {}?", "factors", "raises"),
        "affects": ("{} を変えるものは？", "What changes {}?", "factors", "affects"),
    }
    for rel, (ja, en, kind, direction) in cases.items():
        prop = rel in ("lowers", "raises", "affects")
        a, b = ("quantity a", "quantity b") if prop else ("thing a", "thing b")
        for lang, want in (("ja", ja), ("en", en)):
            base, _ = chat_paper.build_ops([_r(a, rel, b, f="甲", t="乙")], [])
            made = chat_paper.add_questions(base, lang)[0]
            q = next(o for o in made if o["op"] == "add_question")
            assert q["question"] == want.format("乙" if lang == "ja" else b)
            assert (q["mode"], q["op_kind"], q["direction"]) == ("knowledge", kind, direction)


def test_a_net_of_existing_terms_only_needs_no_question() -> None:
    existing = [_term("a", "甲", "alpha", "class"), _term("b", "乙", "beta", "class")]
    ops, _ = chat_paper.build_ops([_r("alpha", "part", "beta")], existing)
    assert [o for o in chat_paper.add_questions(ops, "ja")[0] if o["op"] == "add_question"] == []


def test_one_rule_for_singular_and_plural_in_keys_and_in_matching() -> None:
    same = [
        ("size", "sizes"), ("analysis", "analyses"), ("matrix", "matrices"),
        ("series", "series"), ("ZT", "ZTs"), ("boundary", "boundaries"), ("box", "boxes"),
        ("phase", "phases"), ("device", "devices"), ("Seebeck coefficient", "Seebeck coefficients"),
        ("gas", "gases"),
    ]  # fmt: skip
    for a, b in same:
        assert chat_paper.term_key(a) == chat_paper.term_key(b), (a, b)
        if len(a) > 2:  # 短い名前は照合に使わない
            assert chat_paper.mentions(chat_paper.prep_text(f"in {b} here"), a), (a, b)
    different = [("Cu2S", "Cu2"), ("bias", "bia"), ("gas", "ga"), ("bus", "bu"), ("glass", "glas")]
    for a, b in different:
        assert chat_paper.term_key(a) != chat_paper.term_key(b), (a, b)
        assert not chat_paper.mentions(chat_paper.prep_text(f"the {b} here"), a), (a, b)
    # 数字を含む語・全部大文字の語・3 文字以下の語は単数化しない
    assert chat_paper.term_key("Cu2S") == "cu2s" and chat_paper.term_key("XRD") == "xrd"
    assert chat_paper.term_key("ZTs") == chat_paper.term_key("ZT") == "zt"
    # 照合も同じ規則（文の側の複数形・名前の側の複数形のどちらでも）
    for said, name in [("the sizes vary", "size"), ("the size varies", "sizes"),
                       ("two analyses", "analysis"), ("the matrices", "matrix"),
                       ("the series ends", "series")]:  # fmt: skip
        assert chat_paper.mentions(chat_paper.prep_text(said), name), (said, name)
    assert not chat_paper.mentions(chat_paper.prep_text("the Cu2S film"), "Cu2")


def test_duplicate_relations_are_merged_and_keep_at_most_three_evidence_numbers() -> None:
    rels = [
        _r("grain boundary", "lowers", "thermal conductivity", "s1"),
        _r("Grain boundaries", "lowers", "thermal conductivities", "s2"),
        _r("grain boundary", "lowers", "thermal conductivity", "s1"),  # 同じ番号は 1 つ
        {**_r("grain boundary", "lowers", "thermal conductivity", "s3"), "evidence": ["s3", "s4"]},
    ]
    ops, dropped = chat_paper.build_ops(rels, [])
    rel_ops = [o for o in ops if o["op"] == "add_relation"]
    assert dropped == 0 and len(rel_ops) == 1
    assert rel_ops[0]["evidence"] == ["s1", "s2", "s3"]  # 出現順・最大 3
    assert len([o for o in ops if o["op"] == "add_concept"]) == 2


def test_same_name_existing_terms_are_picked_by_the_kind_the_position_needs() -> None:
    cls = _term("heat_c", "熱", "heat", "class")
    prop = _term("heat_p", "熱", "heat", "property")
    ops, dropped = chat_paper.build_ops([_r("laser", "lowers", "heat")], [cls, prop])
    heat = next(o for o in ops if o["op"] == "add_concept" and o["label_en"] == "heat")
    assert heat["use"] == "existing:heat_p" and heat["kind"] == "property" and dropped == 0
    # part/sub は相手側の種類に合わせる
    ops, dropped = chat_paper.build_ops([_r("thermal conductivity", "part", "heat")], [cls, prop])
    # thermal conductivity は新しい用語（class）→ 相手の heat は class の方
    heat = next(o for o in ops if o["op"] == "add_concept" and o["label_en"] == "heat")
    assert heat["use"] == "existing:heat_c" and dropped == 0
    # 合うものが無ければ先勝ち（関係は今どおり落ちる）
    two_props = [_term(f"heat_{i}", "熱", "heat", "property") for i in (1, 2)]
    ops, dropped = chat_paper.build_ops([_r("heat", "part", "laser")], two_props)
    assert dropped == 1


def test_names_follow_the_language_fields_and_the_other_names_become_aliases() -> None:
    en_paper = {**_r("grain boundary", "lowers", "kappa", f="粒界"), "to_ja": "熱伝導率",
                "to_en": "thermal conductivity", "from_en": None}  # fmt: skip
    ops, _ = chat_paper.build_ops([en_paper], [])
    c = {o["key"]: o for o in ops if o["op"] == "add_concept"}
    assert (c["n1"]["label_en"], c["n1"]["label_ja"]) == ("grain boundary", "粒界")
    assert (c["n2"]["label_en"], c["n2"]["label_ja"]) == ("thermal conductivity", "熱伝導率")
    assert c["n2"]["aliases"] == ["kappa"]  # 書かれた名前は別名（照合にも使う）
    ja_paper = {"from": "粒界散乱", "from_ja": None, "from_en": "grain boundary scattering",
                "rel": "lowers", "to": "熱伝導率", "to_ja": None, "to_en": None,
                "evidence": ["s1"]}  # fmt: skip
    ops, _ = chat_paper.build_ops([ja_paper], [])
    c = {o["key"]: o for o in ops if o["op"] == "add_concept"}
    assert (c["n1"]["label_ja"], c["n1"]["label_en"]) == ("粒界散乱", "grain boundary scattering")
    assert (c["n2"]["label_ja"], c["n2"]["label_en"]) == ("熱伝導率", "")  # 英語名が無ければ空


def test_an_existing_term_that_lost_all_its_relations_leaves_the_proposal() -> None:
    existing = [_term("a", "甲", "alpha", "class")]
    big = [_r(f"x{i} term", "lowers", f"t{i} term") for i in range(6)]  # 6 つの網
    ops, _ = chat_paper.build_ops([*big, _r("alpha", "part", "omega")], existing)
    ops, dropped = chat_paper.add_questions(ops, "ja")
    assert dropped >= 1 and len([o for o in ops if o["op"] == "add_question"]) == 5
    kept, n = chat_paper.prune_unlinked(ops, include_existing=True)
    assert "existing:a" not in [o["use"] for o in kept if o["op"] == "add_concept"]
    assert n >= 2  # 既存の甲と、新しい omega
    assert chat_paper.text("ja", "q_limit", n=3) == (
        "問いの数の上限（5）を超えたまとまりの関係を 3 件除きました"
    )
    assert chat_paper.text("ja", "rel_limit", n=2) == (
        "関係の数の上限（20）を超えた分を 2 件除きました"
    )
    assert chat_paper.text("ja", "kind_dropped", n=1) == "用語の種類が合わない関係を 1 件除きました"


def _commit_passes(tmp_path: Path, ops: list[dict]) -> None:
    from asterism import graph_diff

    from asterism_api import chat_turns
    from tests.test_crosswalk_api import _DatasetClient

    root = tmp_path / "registry"
    draft = themes.create_draft(root, "論文", [], at="2026-11-04T00:00:00+00:00")
    new, report = graph_diff.apply_to_draft(draft, ops, existing=[], dismissed=[], by="ai")
    assert report["dropped"] == [] and report["unlinked"] == []
    body = chat_turns.commit_body({**new, "step": 2}, "ja")
    body["batch"] = "b"
    client = _DatasetClient(rdflib.Dataset())
    _run(themes.check_commit(client, root, body, at="2026-11-04T00:00:00Z"))


@pytest.mark.parametrize(
    "rels",
    [
        # 連鎖: a → b → c（向きがそろった鎖）
        [_r("alpha rate", "affects", "beta rate"), _r("beta rate", "affects", "gamma rate")],
        # 向きの混在: 2 つの原因が同じ相手へ・その相手が別の相手へ・部分も
        [_r("cause one", "lowers", "target x"), _r("cause two", "raises", "target x"),
         _r("target x", "affects", "target y"), _r("part p", "part", "part q")],
        # 木: sub と part の鎖
        [_r("kind a", "sub", "kind b"), _r("kind b", "sub", "kind c"),
         _r("piece", "part", "kind c")],
    ],
)  # fmt: skip
def test_questions_for_chains_and_mixed_directions_reach_every_term_and_pass_the_check(
    tmp_path: Path, rels: list[dict]
) -> None:
    ops, dropped = chat_paper.build_ops(rels, [])
    assert dropped == 0
    ops, q_dropped = chat_paper.add_questions(ops, "ja")
    assert q_dropped == 0
    qs = [o for o in ops if o["op"] == "add_question"]
    assert 1 <= len(qs) <= 2
    _commit_passes(tmp_path, ops)  # 孤立なし・問いの対象から全部たどれる


# ---- 走者・承認・取り消し（本物のストア＋AI のスタブ） --------------------------------------


def _add_concept(key: str, ja: str, en: str, kind: str, **kw: Any) -> dict:
    return {"op": "add_concept", "key": key, "label_ja": ja, "label_en": en, "aliases": [],
            "kind": kind, "use": "new", "reason": "本文にある", **kw}  # fmt: skip


def _rel(frm: str, frm_ja: str | None, rel: str, to: str, to_ja: str | None, *ev: str) -> dict:
    return {"from": frm, "from_ja": frm_ja, "rel": rel, "to": to, "to_ja": to_ja,
            "evidence": list(ev)}  # fmt: skip


def _answer(sentences: dict[str, str], *, orphan: bool = False) -> dict:
    """スタブの答え（AI は名前 → 関係 → 名前＋根拠の番号だけ）。実物の論文（PMC5951533）の語。"""
    b, z = sentences["boundary"], sentences["seebeck"]
    rels = [
        _rel("grain and phase boundaries", "粒界と相の境界", "lowers",
             "thermal conductivity", "熱伝導率", b),
        _rel("thermal conductivity", "熱伝導率", "affects", "figure of merit", "性能指数", z),
        _rel("Seebeck coefficients", "ゼーベック係数", "raises", "figure of merit", None, z),
        # 落ちる関係: 粒界はその文に無い／番号の文が無い
        _rel("grain and phase boundaries", None, "affects", "figure of merit", None, z),
        _rel("Seebeck coefficient", None, "affects", "thermal conductivity", None, "s9999"),
    ]
    if orphan:  # この関係だけに出る用語は、関係が落ちると孤立する
        rels.append(_rel("resistivity", "電気抵抗率", "affects", "Seebeck coefficient", None,
                         "s9999"))
    return {"relations": rels}


class _PaperLLM:
    """論文の提案の呼び出し（paper_propose）だけに答えるスタブ。"""

    def __init__(self, numbers: dict[str, str] | None = None, *, fail: bool = False,
                 orphan: bool = False) -> None:  # fmt: skip
        self.numbers = numbers
        self.fail = fail
        self.orphan = orphan
        self.users: list[str] = []

    def complete(self, system: str, user: str) -> str:
        assert "RELATIONS" in system, system[:80]
        self.users.append(user)
        if self.fail:
            raise RuntimeError("AI down")
        return json.dumps(_answer(self.numbers or {}, orphan=self.orphan), ensure_ascii=False)


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
        assert draft["questions"] == ["性能指数 を変えるものは？"]
        assert turn["paper"] == {
            "draft_id": draft["id"], "created": True, "promoted": False,
            "concepts": ["n1", "n2", "n3", "n4"], "questions": ["性能指数 を変えるものは？"],
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


def _orphan_base(root: Path, sid: str) -> dict:
    """孤立した用語 h1 を持つ、承認できない下書きを話に置く。"""
    at = "2026-11-04T00:00:00+00:00"
    made = themes.create_draft(root, "熱", ["熱を下げるものは？"], at=at)
    row = {"key": "h1", "label_ja": "孤立", "label_en": "", "aliases": [], "kind": "class",
           "use": "new", "slug": "", "reason": "", "match": None, "by": "human"}  # fmt: skip
    base = themes.save_draft(root, {**made, "step": 2, "concepts": [row]})
    session = chat_sessions.load_session(root, sid)
    session["draft_id"] = base["id"]
    chat_sessions.save_session(root, session)
    return row


def test_a_term_left_unlinked_by_the_draft_itself_is_named_and_the_proposal_still_shown(
    tmp_path: Path,
) -> None:
    """当てる前の下書きでも承認が断られる（前からの孤立）なら、原因は下書き。論文の提案は出したまま
    注記で孤立した用語を名指しする。承認は公開の前に止まる。"""
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        root = tmp_path / "registry"
        _orphan_base(root, sid)
        turn = _paper(client, sid)
        assert turn["paper"]["created"] is False and len(turn["reply"]["ops"]) > 0
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert ("この話の下書きに、問いにつながらない用語があります: 孤立"
                "（承認の前に、関係を足すか外してください）") in texts  # fmt: skip
        assert "論文からの提案を、問いにつながる形にできませんでした" not in " ".join(texts)
        body = _confirm(client, sid, turn["id"]).json()
        assert body["turn"]["state"] == "proposed" and body["commit"] is None
        dsid = turn["attachment"]["dataset_id"]
        assert not json.loads((root / dsid / "meta.json").read_text()).get("promoted")


def test_a_proposal_that_breaks_the_draft_is_not_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """当てる前は通り、当てた後だけ断られるなら、提案を出さず論文だけにする。"""
    real = themes.check_commit

    async def picky(client: Any, root: Any, body: dict, *, at: str) -> None:
        if any(c.get("label_ja") == "性能指数" for c in body.get("concepts") or []):
            raise themes.ThemeError("提案が通りません")
        await real(client, root, body, at=at)

    monkeypatch.setattr(themes, "check_commit", picky)
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        root = tmp_path / "registry"
        at = "2026-11-04T00:00:00+00:00"
        made = themes.create_draft(root, "熱", ["熱を下げるものは？"], at=at)
        ans = themes._answer_row(
            "熱を下げるものは？", "knowledge", op="factors", target="h1", direction="lowers",
            kind="", prop="", value="", match="", prop2="", kind2="", memo=None,
            wanted_data="", reason="",
        )  # fmt: skip
        row = {"key": "h1", "label_ja": "比熱", "label_en": "", "aliases": [], "kind": "property",
               "use": "new", "slug": "", "reason": "", "match": None, "by": "human"}  # fmt: skip
        base = themes.save_draft(root, {**made, "step": 2, "concepts": [row], "answers": [ans]})
        session = chat_sessions.load_session(root, sid)
        session["draft_id"] = base["id"]
        chat_sessions.save_session(root, session)
        turn = _paper(client, sid)
        assert "paper" not in turn and turn["reply"]["ops"] == []
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert "論文からの提案を、問いにつながる形にできませんでした（論文は添えました）" in texts
        assert themes.load_draft(root, base["id"])["concepts"] == [row]  # 下書きは変えない


def test_a_term_in_no_relation_or_question_is_dropped_from_the_proposal(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _TwoStep(orphan=True))
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert "どの関係にも問いにも出ない用語を 1 件除きました" in texts
        assert "根拠の文が合わない関係を 3 件除きました" in texts
        assert "用語 4・関係 3・問い 1" in turn["reply"]["text"]
        draft = themes.load_draft(
            tmp_path / "registry",
            chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"],
        )
        assert "電気抵抗率" not in [c["label_ja"] for c in draft["concepts"]]
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


def test_the_one_row_approval_skips_the_unpublished_paper_s_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 行の承認（11o）は、論文の根拠つきの行・用語には 409、触れない手の行には効く。"""
    # 2 回目のスタブが使う文（材料と方法の節）は上限の外 — このテストでは上限を広げる
    wide = functools.partial(chat_paper.select_sentences, limit=400, budget=200_000)
    monkeypatch.setattr(chat_paper, "select_sentences", wide)

    class _Seq(_TwoStep):
        """1 回目 = いつもの答え・2 回目 = 別の 1 語から、承認済みの用語への 1 本の関係。"""

        def complete(self, system: str, user: str) -> str:
            if self.users:
                self.users.append(user)
                rows = {
                    m[1]: m[2]
                    for m in (re.match(r"- (s\d+) \| (.*)", x) for x in user.splitlines())
                    if m
                }
                num = next(k for k, v in rows.items() if "the specific heat capacity" in v)
                assert "熱伝導率 | thermal conductivity" in user  # 1 回目で承認した用語の名前
                rel = _rel("specific heat capacity", "比熱", "affects",
                           "thermal conductivity", None, num)  # fmt: skip
                return json.dumps({"relations": [rel]}, ensure_ascii=False)
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


# ---- 日本語の論文・注記の内訳・既存の用語（API） --------------------------------------------

JA_JATS = """<?xml version="1.0"?>
<article><front><article-meta><title-group><article-title>熱電材料</article-title></title-group>
</article-meta></front><body><sec id="s1"><title>はじめに</title>
<p>粒界散乱は熱伝導率を下げる。ゼーベック係数は性能指数を上げる。</p></sec></body></article>
""".encode()


class _JaLLM:
    """日本語の論文のスタブ — 名前は文のまま・英語名だけ ``*_en`` に。"""

    def __init__(self) -> None:
        self.users: list[str] = []

    def complete(self, system: str, user: str) -> str:
        assert "RELATIONS" in system and "from_en" in system
        self.users.append(user)
        rows = {m[2]: m[1] for m in (re.match(r"- (s\d+) \| (.*)", x) for x in user.splitlines())
                if m}  # fmt: skip
        num = next(iter(rows.values()))
        rel = lambda a, ae, r, b, be: {  # noqa: E731
            "from": a, "from_ja": None, "from_en": ae, "rel": r, "to": b, "to_ja": None,
            "to_en": be, "evidence": [num],
        }
        return json.dumps({"relations": [
            rel("粒界散乱", "grain boundary scattering", "lowers",
                "熱伝導率", "thermal conductivity"),
            rel("ゼーベック係数", None, "raises", "性能指数", "figure of merit"),
        ]}, ensure_ascii=False)  # fmt: skip


def test_a_japanese_paper_gets_japanese_names_english_names_and_machine_questions(
    tmp_path: Path,
) -> None:
    llm = _JaLLM()
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = _attach_many(client, sid, [("論文.xml", JA_JATS)])
        assert r.status_code == 201, r.text
        _drain(client, r.json()["job_id"])
        turn = _turn(client, sid, r.json()["turn"]["id"])
        assert turn["paper"]["created"] is True, turn["reply"]["notes"]
        assert "Respond in" not in llm.users[0]  # 言語の指示は付けない
        root = tmp_path / "registry"
        draft = themes.load_draft(root, chat_sessions.load_session(root, sid)["draft_id"])
        names = {c["label_ja"]: c["label_en"] for c in draft["concepts"]}
        assert names == {
            "粒界散乱": "grain boundary scattering", "熱伝導率": "thermal conductivity",
            "ゼーベック係数": "", "性能指数": "figure of merit",
        }  # fmt: skip
        assert sorted(draft["questions"]) == [
            "性能指数 を上げるものは？", "熱伝導率 を下げるものは？",
        ]  # fmt: skip
        assert _confirm(client, sid, turn["id"]).json()["turn"]["state"] == "confirmed"


class _Counting(_TwoStep):
    """種類の食い違う関係と、21 本の同じ関係を混ぜて返す。"""

    def complete(self, system: str, user: str) -> str:
        if self.numbers is None:
            self.numbers = _numbers(user)
        self.users.append(user)
        out = _answer(self.numbers)
        z = self.numbers["seebeck"]
        base = out["relations"]
        # 種類の食い違い: 同じ用語が lowers の相手（property）にも part の相手（class 同士）にもなる
        base.append(_rel("figure of merit", None, "part", "Seebeck coefficient", None, z))
        base += [base[0]] * 20  # 重複は 1 本にまとまり、20 本を超えた分は別に数える
        for r in base:
            r.setdefault("from_en", None)
            r.setdefault("to_en", None)
        return json.dumps(out, ensure_ascii=False)


def test_the_notes_count_each_kind_of_dropped_relation_separately(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Counting())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        texts = [n["text"] for n in turn["reply"]["notes"]]
        assert "根拠の文が合わない関係を 2 件除きました" in texts
        assert "用語の種類が合わない関係を 1 件除きました" in texts
        assert "関係の数の上限（20）を超えた分を 6 件除きました" in texts
        assert "用語 4・関係 3・問い 1" in turn["reply"]["text"]


def test_an_existing_term_keeps_its_own_name_on_the_card_and_in_the_question(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = client.post("/api/vocab/shared", json={
            "slug": "merit", "kind": "property", "label_ja": "優秀さの指標",
            "label_en": "figure of merit",
            "cqs": [{"title": "優秀さの指標の値は、どこにいくつあるか", "op": "values"}],
        })  # fmt: skip
        assert r.status_code == 201, r.text
        turn = _paper(client, sid)
        ops = turn["reply"]["ops"]
        merit = next(o for o in ops if o["op"] == "add_concept" and o["use"] == "existing:merit")
        assert merit["label_ja"] == "優秀さの指標" and merit["label_en"] == "figure of merit"
        assert turn["reply"]["sentences"][0] and any(
            "優秀さの指標" in x for x in turn["reply"]["sentences"]
        )  # 札の文は既存の名前
        assert [o["question"] for o in ops if o["op"] == "add_question"] == [
            "優秀さの指標 を変えるものは？"
        ]  # 問い文も既存の名前
        assert _confirm(client, sid, turn["id"]).json()["turn"]["state"] == "confirmed"


# ---------------------------------------------------------------------------
# 12c: 根拠の文を見せる（点・論文の点・回答の出典）
# ---------------------------------------------------------------------------


def _point_get(client: TestClient, **params: str):
    return client.get("/api/chat/point", params=params)


def test_evidence_shows_in_draft_points_then_confirmed_lines_the_paper_point_and_answers(
    tmp_path: Path,
) -> None:
    import httpx

    app, _ = _app(tmp_path, _TwoStep())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _paper(client, sid)
        dsid = turn["attachment"]["dataset_id"]
        name = turn["attachment"]["name"]
        draft_id = chat_sessions.load_session(tmp_path / "registry", sid)["draft_id"]
        # 下書きの札: 関係の evidence が文に付く（論文名はその手番の論文・full は text）
        card = _point_get(client, iri=f"draft:{draft_id}:n1").json()
        (ev,) = card["sentences"][0]["evidence"]
        assert ev["dataset_label"] == name and ev["dataset_id"] == dsid and ev["section"] is None
        assert ev["path"] and ev["iri"].startswith("https://") and ev["text"]
        assert ev["full"].startswith(ev["text"].rstrip("…"))
        assert _confirm(client, sid, turn["id"]).status_code == 200
        # 確定した線: ストアから引いた文・節・論文名
        terms = {t.label: t.iri for t in _run(shared_vocab._read_terms(app.state.client))}
        ends = {"from": terms["粒界と相の境界"], "relation": "lowers", "to": terms["熱伝導率"]}
        line = _point_get(client, **ends).json()
        (ev,) = line["sentences"][0]["evidence"]
        assert ev["text"].startswith("All samples with a decomposition")
        assert ev["dataset_id"] == dsid and ev["dataset_label"] and ev["path"]
        assert len(ev["text"]) <= 151 and ev["full"].startswith(ev["text"].rstrip("…"))
        # 論文の点: この論文が根拠になっている関係（根拠を全部付けて）
        paper = _point_get(client, iri=f"paper:{dsid}").json()
        assert paper["kind"] == "paper" and paper["confirmed"] is True
        assert paper["label"] == ev["dataset_label"] and paper["questions"] == []
        assert len(paper["sentences"]) == 3
        assert all(s["evidence"] and s["evidence"][0]["dataset_id"] == dsid
                   for s in paper["sentences"])  # fmt: skip
        assert _point_get(client, iri="paper:no-such").status_code == 404
        assert _point_get(client, iri="paper:bad id").status_code == 422
        # 回答の出典: 文 IRI には quote・section・論文名（AI の文字列ではなくストアから）
        reply = {
            "answer": "粒界は熱伝導率を下げます。", "answered": True,
            "citations": [{"iri": ev["iri"], "label": "AI が書いた名前"},
                          {"iri": "https://x.example/none"}],
        }  # fmt: skip
        client.app.state.demo_agent_url = "http://demo.test"
        client.app.state.ask_transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=reply)
        )
        sid2 = _new(client)
        r = client.post(
            f"/api/chat/sessions/{sid2}/turns",
            json={"text": "熱伝導率を下げるものは何か 答えて"},
            headers=KEY,
        )
        assert r.status_code == 201, r.text
        c1, c2 = r.json()["turn"]["reply"]["answer"]["citations"]
        assert c1["quote"] == ev["text"] and c1["dataset_id"] == dsid
        assert c1["label"] == ev["dataset_label"]  # 名前は論文名（AI の名前ではない）
        assert c1["dataset_label"] == ev["dataset_label"] and "section" in c1
        assert c1["path"] == ev["path"]  # 節の題名が無い文は UI が「節 n」にする
        assert "quote" not in c2  # ほかの出典は今のまま
