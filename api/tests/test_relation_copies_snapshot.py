# ruff: noqa: E501, RUF001
"""R0b: api に残っていた関係の写しを目録の導出に置き換える「前」の結果の写し（スナップショット）。

土台 R0a の先頭 70a90164 の実装から機械的に書き出した（置き換えの前に固定）。写しが目録の
導出になった後も、値・並び・検査の結果が同じであることを見張る。値を変えたくなったら、まず契約を見直す。
"""

from __future__ import annotations

import pytest
from asterism.shared_vocab import SharedTerm

from asterism_api import chat_paper, chat_turns, consult_graph_routes

RELS = ("sub", "eq", "quantity", "part", "lowers", "raises", "affects", "makes", "measures",
        "becomes", "competes", "has_property", "bogus")


def _cell(v: object) -> str:
    return "-" if v is None else str(v)


def _term(slug: str, label: str, kind: str) -> SharedTerm:
    return SharedTerm(iri=f"https://x/sv/{slug}", slug=slug, kind=kind, label=label,
                      label_en=label, comment=None, created_at="t", aliases=())  # fmt: skip


def _r(frm: str, rel: str, to: str) -> dict:
    return {"from": frm, "from_ja": None, "rel": rel, "to": to, "to_ja": None, "evidence": ["s1"]}


def _summary(rels: list[dict], existing: list[SharedTerm]) -> str:
    try:
        ops, dropped = chat_paper.build_ops(rels, existing)
    except Exception as e:
        return f"!{type(e).__name__}:{e}"
    kinds = [(o["label_en"], o["kind"], o["use"]) for o in ops if o["op"] == "add_concept"]
    rs = [(o["from"], o["rel"], o["to"]) for o in ops if o["op"] == "add_relation"]
    return f"{dropped}|{kinds}|{rs}"


def _consts() -> dict:
    return {
        "chat_paper._FACTOR_DIR": chat_paper._FACTOR_DIR,
        "chat_paper._OP_KIND": chat_paper._OP_KIND,
        "chat_paper._CLASS_BOTH": tuple(chat_paper._CLASS_BOTH),
        "consult._SYMMETRIC": sorted(consult_graph_routes._SYMMETRIC),
        "consult._SUBSUMPTION_RELATIONS": sorted(consult_graph_routes._SUBSUMPTION_RELATIONS),
        "consult.types": [type(consult_graph_routes._SYMMETRIC).__name__,
                          type(consult_graph_routes._SUBSUMPTION_RELATIONS).__name__],
    }


def _grid_build_ops_single() -> dict:
    """1 本の関係: 関係 × 主語（新・既存の種類・既存の項目）× 相手（同）。"""
    ends = {"new": None, "class": "class", "property": "property"}
    out = {}
    for rel in RELS:
        cells = []
        for fk in ends.values():
            for tk in ends.values():
                existing = []
                if fk:
                    existing.append(_term("fs", "Fname", fk))
                if tk:
                    existing.append(_term("ts", "Tname", tk))
                cells.append(_summary([_r("Fname", rel, "Tname")], existing))
        out[rel] = cells
    return out


def _grid_build_ops_pairs() -> dict:
    """2 本の関係（同じ提案の中で種別を決める）: A r1 B と、B r2 C／C r2 B。"""
    out = {}
    for r1 in RELS:
        for r2 in RELS:
            out[f"{r1}|{r2}"] = [
                _summary([_r("Aaa", r1, "Bbb"), _r("Bbb", r2, "Ccc")], []),
                _summary([_r("Aaa", r1, "Bbb"), _r("Ccc", r2, "Bbb")], []),
                _summary([_r("Aaa", r1, "Bbb"), _r("Aaa", r2, "Ccc")], []),
            ]
    return out


def _grid_add_questions() -> dict:
    out = {}
    for rel in RELS:
        ops, _ = chat_paper.build_ops([_r("Aaa", rel, "Bbb")], [])
        for lang in ("ja", "en"):
            try:
                got, dropped = chat_paper.add_questions(ops, lang)
                qs = [(o["question"], o["op_kind"], o["direction"], o["target"])
                      for o in got if o["op"] == "add_question"]
                out[f"{rel}|{lang}"] = (qs, dropped)
            except Exception as e:
                out[f"{rel}|{lang}"] = f"!{type(e).__name__}:{e}"
    return out


def _grid_scope_line() -> dict:
    """``filter_ops_to_scope`` の行の範囲: 関係 → 反対向きの add_relation／retarget／remove が残るか。"""
    out = {}
    for rel in RELS:
        ops = [
            {"op": "add_relation", "from": "b", "rel": rel, "to": "a"},
            {"op": "retarget", "from": "b", "rel": rel, "to": "a", "new_to": "c"},
            {"op": "remove", "target": "relation", "from": "b", "rel": rel, "to": "a"},
            {"op": "retarget", "from": "a", "rel": rel, "to": "b", "new_to": "c"},
            {"op": "remove", "target": "relation", "from": "a", "rel": rel, "to": "b"},
        ]
        kept, _ = chat_turns.filter_ops_to_scope(ops, {"keys": ["a", "b"], "line": ["a", rel, "b"]})
        out[rel] = [ops.index(o) for o in kept]
    return out


def _grid_resolve_scope() -> dict:
    """``resolve_scope`` の行の範囲: 関係 → 図が反対向きの線を持つときに通るか。"""
    out = {}
    for rel in RELS:
        graph = {
            "concepts": [{"key": "a", "label_ja": "A"}, {"key": "b", "label_ja": "B"}],
            "relations": [{"from": "b", "rel": rel, "to": "a"}],
        }
        try:
            got = chat_turns.resolve_scope(
                {"node": "x", "line": {"from": "a", "rel": rel, "to": "b"}}, graph, [], "ja")
            out[rel] = got["keys"]
        except chat_turns.TurnRefused as e:
            out[rel] = f"refused:{e.status_code}" if hasattr(e, "status_code") else "refused"
    return out


def _grid_answer_complete() -> dict:
    return {
        str(d): chat_turns._answer_complete(
            {"mode": "knowledge", "op": "factors", "target": "t", "direction": d})
        for d in ("lowers", "raises", "affects", "bogus", "", None)
    }


EXPECTED: dict = {'consts': {'chat_paper._FACTOR_DIR': {'lowers': 'lowers',
                                       'raises': 'raises',
                                       'affects': 'affects'},
            'chat_paper._OP_KIND': {'part': 'parts',
                                    'sub': 'kinds',
                                    'lowers': 'factors',
                                    'raises': 'factors',
                                    'affects': 'factors',
                                    'makes': 'makers',
                                    'measures': 'measurers',
                                    'becomes': 'sources',
                                    'competes': 'competitors',
                                    'has_property': 'bearers'},
            'chat_paper._CLASS_BOTH': ('makes', 'becomes', 'competes'),
            'consult._SYMMETRIC': ['competesWith', 'equivalentClass', 'equivalentProperty'],
            'consult._SUBSUMPTION_RELATIONS': ['equivalentClass', 'subClassOf'],
            'consult.types': ['frozenset', 'frozenset']},
 'build_ops_single': {'sub': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', 'new')]|[('n1', "
                              "'sub', 'n2')]",
                              "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                              "'existing:ts')]|[('n1', 'sub', 'n2')]",
                              '1|[]|[]',
                              "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                              "'new')]|[('n1', 'sub', 'n2')]",
                              "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                              "'existing:ts')]|[('n1', 'sub', 'n2')]",
                              '1|[]|[]',
                              '1|[]|[]',
                              '1|[]|[]',
                              "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                              "'existing:ts')]|[('n1', 'sub', 'n2')]"],
                      'eq': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', 'new')]|[('n1', "
                             "'eq', 'n2')]",
                             "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                             "'existing:ts')]|[('n1', 'eq', 'n2')]",
                             '1|[]|[]',
                             "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                             "'new')]|[('n1', 'eq', 'n2')]",
                             "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                             "'existing:ts')]|[('n1', 'eq', 'n2')]",
                             '1|[]|[]',
                             '1|[]|[]',
                             '1|[]|[]',
                             '1|[]|[]'],
                      'quantity': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                   "'new')]|[('n1', 'quantity', 'n2')]",
                                   "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                   "'existing:ts')]|[('n1', 'quantity', 'n2')]",
                                   '1|[]|[]',
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                   "'new')]|[('n1', 'quantity', 'n2')]",
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                   "'existing:ts')]|[('n1', 'quantity', 'n2')]",
                                   '1|[]|[]',
                                   '1|[]|[]',
                                   '1|[]|[]',
                                   '1|[]|[]'],
                      'part': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', 'new')]|[('n1', "
                               "'part', 'n2')]",
                               "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                               "'existing:ts')]|[('n1', 'part', 'n2')]",
                               '1|[]|[]',
                               "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                               "'new')]|[('n1', 'part', 'n2')]",
                               "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                               "'existing:ts')]|[('n1', 'part', 'n2')]",
                               '1|[]|[]',
                               '1|[]|[]',
                               '1|[]|[]',
                               "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                               "'existing:ts')]|[('n1', 'part', 'n2')]"],
                      'lowers': ["0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                 "'new')]|[('n1', 'lowers', 'n2')]",
                                 '1|[]|[]',
                                 "0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                 "'existing:ts')]|[('n1', 'lowers', 'n2')]",
                                 "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                 "'new')]|[('n1', 'lowers', 'n2')]",
                                 '1|[]|[]',
                                 "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                 "'existing:ts')]|[('n1', 'lowers', 'n2')]",
                                 "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                                 "'new')]|[('n1', 'lowers', 'n2')]",
                                 '1|[]|[]',
                                 "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                                 "'existing:ts')]|[('n1', 'lowers', 'n2')]"],
                      'raises': ["0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                 "'new')]|[('n1', 'raises', 'n2')]",
                                 '1|[]|[]',
                                 "0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                 "'existing:ts')]|[('n1', 'raises', 'n2')]",
                                 "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                 "'new')]|[('n1', 'raises', 'n2')]",
                                 '1|[]|[]',
                                 "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                 "'existing:ts')]|[('n1', 'raises', 'n2')]",
                                 "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                                 "'new')]|[('n1', 'raises', 'n2')]",
                                 '1|[]|[]',
                                 "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                                 "'existing:ts')]|[('n1', 'raises', 'n2')]"],
                      'affects': ["0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                  "'new')]|[('n1', 'affects', 'n2')]",
                                  '1|[]|[]',
                                  "0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                  "'existing:ts')]|[('n1', 'affects', 'n2')]",
                                  "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                  "'new')]|[('n1', 'affects', 'n2')]",
                                  '1|[]|[]',
                                  "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                  "'existing:ts')]|[('n1', 'affects', 'n2')]",
                                  "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                                  "'new')]|[('n1', 'affects', 'n2')]",
                                  '1|[]|[]',
                                  "0|[('Fname', 'property', 'existing:fs'), ('Tname', 'property', "
                                  "'existing:ts')]|[('n1', 'affects', 'n2')]"],
                      'makes': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', 'new')]|[('n1', "
                                "'makes', 'n2')]",
                                "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                "'existing:ts')]|[('n1', 'makes', 'n2')]",
                                '1|[]|[]',
                                "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                "'new')]|[('n1', 'makes', 'n2')]",
                                "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                "'existing:ts')]|[('n1', 'makes', 'n2')]",
                                '1|[]|[]',
                                '1|[]|[]',
                                '1|[]|[]',
                                '1|[]|[]'],
                      'measures': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                   "'new')]|[('n1', 'measures', 'n2')]",
                                   "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                   "'existing:ts')]|[('n1', 'measures', 'n2')]",
                                   "0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                   "'existing:ts')]|[('n1', 'measures', 'n2')]",
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                   "'new')]|[('n1', 'measures', 'n2')]",
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                   "'existing:ts')]|[('n1', 'measures', 'n2')]",
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'property', "
                                   "'existing:ts')]|[('n1', 'measures', 'n2')]",
                                   '1|[]|[]',
                                   '1|[]|[]',
                                   '1|[]|[]'],
                      'becomes': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                  "'new')]|[('n1', 'becomes', 'n2')]",
                                  "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                  "'existing:ts')]|[('n1', 'becomes', 'n2')]",
                                  '1|[]|[]',
                                  "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                  "'new')]|[('n1', 'becomes', 'n2')]",
                                  "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                  "'existing:ts')]|[('n1', 'becomes', 'n2')]",
                                  '1|[]|[]',
                                  '1|[]|[]',
                                  '1|[]|[]',
                                  '1|[]|[]'],
                      'competes': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                   "'new')]|[('n1', 'competes', 'n2')]",
                                   "0|[('Tname', 'class', 'existing:ts'), ('Fname', 'class', "
                                   "'new')]|[('n1', 'competes', 'n2')]",
                                   '1|[]|[]',
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                   "'new')]|[('n1', 'competes', 'n2')]",
                                   "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                   "'existing:ts')]|[('n1', 'competes', 'n2')]",
                                   '1|[]|[]',
                                   '1|[]|[]',
                                   '1|[]|[]',
                                   '1|[]|[]'],
                      'has_property': ["0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                       "'new')]|[('n1', 'has_property', 'n2')]",
                                       '1|[]|[]',
                                       "0|[('Fname', 'class', 'new'), ('Tname', 'property', "
                                       "'existing:ts')]|[('n1', 'has_property', 'n2')]",
                                       "0|[('Fname', 'class', 'existing:fs'), ('Tname', "
                                       "'property', 'new')]|[('n1', 'has_property', 'n2')]",
                                       '1|[]|[]',
                                       "0|[('Fname', 'class', 'existing:fs'), ('Tname', "
                                       "'property', 'existing:ts')]|[('n1', 'has_property', 'n2')]",
                                       '1|[]|[]',
                                       '1|[]|[]',
                                       '1|[]|[]'],
                      'bogus': ["0|[('Fname', 'class', 'new'), ('Tname', 'class', 'new')]|[('n1', "
                                "'bogus', 'n2')]",
                                "0|[('Fname', 'class', 'new'), ('Tname', 'class', "
                                "'existing:ts')]|[('n1', 'bogus', 'n2')]",
                                '1|[]|[]',
                                "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                "'new')]|[('n1', 'bogus', 'n2')]",
                                "0|[('Fname', 'class', 'existing:fs'), ('Tname', 'class', "
                                "'existing:ts')]|[('n1', 'bogus', 'n2')]",
                                '1|[]|[]',
                                '1|[]|[]',
                                '1|[]|[]',
                                '1|[]|[]']},
 'build_ops_pairs': {'sub|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'sub', 'n3')]",
                                 "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', 'sub', 'n2')]",
                                 "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'sub', 'n3')]"],
                     'sub|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                "'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'eq', 'n3')]",
                                "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                "'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', 'eq', 'n2')]",
                                "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                "'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'eq', 'n3')]"],
                     'sub|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', "
                                      "'quantity', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', "
                                      "'quantity', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', "
                                      "'quantity', 'n3')]"],
                     'sub|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'part', 'n3')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', 'part', 'n2')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'part', 'n3')]"],
                     'sub|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'property', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'lowers', "
                                    "'n3')]",
                                    "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'lowers', 'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'property', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'lowers', "
                                    "'n3')]"],
                     'sub|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'property', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'raises', "
                                    "'n3')]",
                                    "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'raises', 'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'property', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'raises', "
                                    "'n3')]"],
                     'sub|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'property', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'affects', "
                                     "'n3')]",
                                     "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'affects', 'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'property', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'affects', "
                                     "'n3')]"],
                     'sub|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'makes', 'n3')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', 'makes', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'makes', 'n3')]"],
                     'sub|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', "
                                      "'measures', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', "
                                      "'measures', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', "
                                      "'measures', 'n3')]"],
                     'sub|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'becomes', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', 'becomes', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'becomes', "
                                     "'n3')]"],
                     'sub|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', "
                                      "'competes', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', "
                                      "'competes', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', "
                                      "'competes', 'n3')]"],
                     'sub|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'sub', 'n2'), "
                                          "('n2', 'has_property', 'n3')]",
                                          "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'has_property', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'sub', 'n2'), "
                                          "('n1', 'has_property', 'n3')]"],
                     'sub|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'sub', 'n2'), ('n2', 'bogus', 'n3')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'sub', 'n2'), ('n3', 'bogus', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'sub', 'n2'), ('n1', 'bogus', 'n3')]"],
                     'eq|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'sub', 'n3')]",
                                "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'sub', 'n2')]",
                                "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'sub', 'n3')]"],
                     'eq|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                               "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'eq', 'n3')]",
                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                               "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'eq', 'n2')]",
                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                               "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'eq', 'n3')]"],
                     'eq|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'quantity', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'quantity', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'quantity', "
                                     "'n3')]"],
                     'eq|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'part', 'n3')]",
                                 "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'part', 'n2')]",
                                 "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'part', 'n3')]"],
                     'eq|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'property', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'lowers', "
                                   "'n3')]",
                                   "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                   "'new')]|[('n1', 'lowers', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'property', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'lowers', "
                                   "'n3')]"],
                     'eq|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'property', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'raises', "
                                   "'n3')]",
                                   "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                   "'new')]|[('n1', 'raises', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'property', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'raises', "
                                   "'n3')]"],
                     'eq|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'property', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'affects', "
                                    "'n3')]",
                                    "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'affects', 'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'property', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'affects', "
                                    "'n3')]"],
                     'eq|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'makes', 'n3')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'makes', 'n2')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'makes', 'n3')]"],
                     'eq|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'measures', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'measures', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'measures', "
                                     "'n3')]"],
                     'eq|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'becomes', "
                                    "'n3')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'becomes', "
                                    "'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'becomes', "
                                    "'n3')]"],
                     'eq|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'competes', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'competes', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'competes', "
                                     "'n3')]"],
                     'eq|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'eq', 'n2'), ('n2', "
                                         "'has_property', 'n3')]",
                                         "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'has_property', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'eq', 'n2'), ('n1', "
                                         "'has_property', 'n3')]"],
                     'eq|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'eq', 'n2'), ('n2', 'bogus', 'n3')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'eq', 'n2'), ('n3', 'bogus', 'n2')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'eq', 'n2'), ('n1', 'bogus', 'n3')]"],
                     'quantity|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), ('n2', "
                                      "'sub', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), ('n3', "
                                      "'sub', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), ('n1', "
                                      "'sub', 'n3')]"],
                     'quantity|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'quantity', 'n2'), ('n2', 'eq', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'quantity', 'n2'), ('n3', 'eq', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'quantity', 'n2'), ('n1', 'eq', "
                                     "'n3')]"],
                     'quantity|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n2', 'quantity', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n3', 'quantity', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n1', 'quantity', 'n3')]"],
                     'quantity|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), ('n2', "
                                       "'part', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), ('n3', "
                                       "'part', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), ('n1', "
                                       "'part', 'n3')]"],
                     'quantity|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'quantity', 'n2'), "
                                         "('n2', 'lowers', 'n3')]",
                                         "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'quantity', 'n2'), "
                                         "('n1', 'lowers', 'n3')]"],
                     'quantity|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'quantity', 'n2'), "
                                         "('n2', 'raises', 'n3')]",
                                         "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'quantity', 'n2'), "
                                         "('n1', 'raises', 'n3')]"],
                     'quantity|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'quantity', 'n2'), "
                                          "('n2', 'affects', 'n3')]",
                                          "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'quantity', 'n2'), "
                                          "('n1', 'affects', 'n3')]"],
                     'quantity|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                        "('n2', 'makes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                        "('n3', 'makes', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                        "('n1', 'makes', 'n3')]"],
                     'quantity|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n2', 'measures', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n3', 'measures', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n1', 'measures', 'n3')]"],
                     'quantity|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                          "('n2', 'becomes', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                          "('n3', 'becomes', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                          "('n1', 'becomes', 'n3')]"],
                     'quantity|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n2', 'competes', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n2', 'competes', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                           "('n1', 'competes', 'n3')]"],
                     'quantity|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                               "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                               "'quantity', 'n2'), ('n2', 'has_property', 'n3')]",
                                               "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                               "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                               "'quantity', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'quantity|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                        "('n2', 'bogus', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                        "('n3', 'bogus', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'quantity', 'n2'), "
                                        "('n1', 'bogus', 'n3')]"],
                     'part|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'part', 'n2'), ('n2', 'sub', 'n3')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'part', 'n2'), ('n3', 'sub', 'n2')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'part', 'n2'), ('n1', 'sub', 'n3')]"],
                     'part|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'part', 'n2'), ('n2', 'eq', 'n3')]",
                                 "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'part', 'n2'), ('n3', 'eq', 'n2')]",
                                 "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                 "'class', 'new')]|[('n1', 'part', 'n2'), ('n1', 'eq', 'n3')]"],
                     'part|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n2', "
                                       "'quantity', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n3', "
                                       "'quantity', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n1', "
                                       "'quantity', 'n3')]"],
                     'part|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'part', 'n2'), ('n2', 'part', 'n3')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'part', 'n2'), ('n3', 'part', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'part', 'n2'), ('n1', 'part', 'n3')]"],
                     'part|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'property', 'new')]|[('n1', 'part', 'n2'), ('n2', 'lowers', "
                                     "'n3')]",
                                     "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'lowers', 'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'property', 'new')]|[('n1', 'part', 'n2'), ('n1', 'lowers', "
                                     "'n3')]"],
                     'part|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'property', 'new')]|[('n1', 'part', 'n2'), ('n2', 'raises', "
                                     "'n3')]",
                                     "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'raises', 'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'property', 'new')]|[('n1', 'part', 'n2'), ('n1', 'raises', "
                                     "'n3')]"],
                     'part|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'part', 'n2'), ('n2', "
                                      "'affects', 'n3')]",
                                      "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'affects', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'part', 'n2'), ('n1', "
                                      "'affects', 'n3')]"],
                     'part|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'part', 'n2'), ('n2', 'makes', "
                                    "'n3')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'part', 'n2'), ('n3', 'makes', "
                                    "'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'part', 'n2'), ('n1', 'makes', "
                                    "'n3')]"],
                     'part|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n2', "
                                       "'measures', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n3', "
                                       "'measures', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n1', "
                                       "'measures', 'n3')]"],
                     'part|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n2', "
                                      "'becomes', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n3', "
                                      "'becomes', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n1', "
                                      "'becomes', 'n3')]"],
                     'part|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n2', "
                                       "'competes', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n2', "
                                       "'competes', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'part', 'n2'), ('n1', "
                                       "'competes', 'n3')]"],
                     'part|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'property', 'new')]|[('n1', 'part', 'n2'), "
                                           "('n2', 'has_property', 'n3')]",
                                           "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                           "'new')]|[('n1', 'has_property', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'property', 'new')]|[('n1', 'part', 'n2'), "
                                           "('n1', 'has_property', 'n3')]"],
                     'part|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'part', 'n2'), ('n2', 'bogus', "
                                    "'n3')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'part', 'n2'), ('n3', 'bogus', "
                                    "'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'part', 'n2'), ('n1', 'bogus', "
                                    "'n3')]"],
                     'lowers|sub': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'lowers', 'n2')]",
                                    "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'lowers', 'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                    "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                    "'sub', 'n3')]"],
                     'lowers|eq': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                   "'new')]|[('n1', 'lowers', 'n2')]",
                                   "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                   "'new')]|[('n1', 'lowers', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                   "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', 'eq', "
                                   "'n3')]"],
                     'lowers|quantity': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                         "'quantity', 'n3')]"],
                     'lowers|part': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'lowers', 'n2')]",
                                     "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'lowers', 'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                     "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                     "'part', 'n3')]"],
                     'lowers|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'lowers', 'n2'), "
                                       "('n2', 'lowers', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n3', "
                                       "'lowers', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'lowers', 'n2'), "
                                       "('n1', 'lowers', 'n3')]"],
                     'lowers|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'lowers', 'n2'), "
                                       "('n2', 'raises', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n3', "
                                       "'raises', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'lowers', 'n2'), "
                                       "('n1', 'raises', 'n3')]"],
                     'lowers|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'lowers', 'n2'), "
                                        "('n2', 'affects', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n3', "
                                        "'affects', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'lowers', 'n2'), "
                                        "('n1', 'affects', 'n3')]"],
                     'lowers|makes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'lowers', 'n2')]",
                                      "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'lowers', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                      "'makes', 'n3')]"],
                     'lowers|measures': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n3', "
                                         "'measures', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                         "'measures', 'n3')]"],
                     'lowers|becomes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                        "'new')]|[('n1', 'lowers', 'n2')]",
                                        "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                        "'new')]|[('n1', 'lowers', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                        "'becomes', 'n3')]"],
                     'lowers|competes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                         "'competes', 'n3')]"],
                     'lowers|has_property': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new')]|[('n1', 'lowers', 'n2')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'class', 'new')]|[('n1', 'lowers', "
                                             "'n2'), ('n3', 'has_property', 'n2')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                             "'lowers', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'lowers|bogus': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'lowers', 'n2')]",
                                      "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'lowers', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'lowers', 'n2'), ('n1', "
                                      "'bogus', 'n3')]"],
                     'raises|sub': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'raises', 'n2')]",
                                    "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'raises', 'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                    "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                    "'sub', 'n3')]"],
                     'raises|eq': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                   "'new')]|[('n1', 'raises', 'n2')]",
                                   "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                   "'new')]|[('n1', 'raises', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                   "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', 'eq', "
                                   "'n3')]"],
                     'raises|quantity': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                         "'quantity', 'n3')]"],
                     'raises|part': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'raises', 'n2')]",
                                     "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'raises', 'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                     "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                     "'part', 'n3')]"],
                     'raises|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'raises', 'n2'), "
                                       "('n2', 'lowers', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n3', "
                                       "'lowers', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'raises', 'n2'), "
                                       "('n1', 'lowers', 'n3')]"],
                     'raises|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'raises', 'n2'), "
                                       "('n2', 'raises', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n3', "
                                       "'raises', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'raises', 'n2'), "
                                       "('n1', 'raises', 'n3')]"],
                     'raises|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'raises', 'n2'), "
                                        "('n2', 'affects', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n3', "
                                        "'affects', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'raises', 'n2'), "
                                        "('n1', 'affects', 'n3')]"],
                     'raises|makes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'raises', 'n2')]",
                                      "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'raises', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                      "'makes', 'n3')]"],
                     'raises|measures': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n3', "
                                         "'measures', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                         "'measures', 'n3')]"],
                     'raises|becomes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                        "'new')]|[('n1', 'raises', 'n2')]",
                                        "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                        "'new')]|[('n1', 'raises', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                        "'becomes', 'n3')]"],
                     'raises|competes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                         "'competes', 'n3')]"],
                     'raises|has_property': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new')]|[('n1', 'raises', 'n2')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'class', 'new')]|[('n1', 'raises', "
                                             "'n2'), ('n3', 'has_property', 'n2')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                             "'raises', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'raises|bogus': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'raises', 'n2')]",
                                      "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'raises', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'raises', 'n2'), ('n1', "
                                      "'bogus', 'n3')]"],
                     'affects|sub': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'affects', 'n2')]",
                                     "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                     "'new')]|[('n1', 'affects', 'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                     "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n1', "
                                     "'sub', 'n3')]"],
                     'affects|eq': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'affects', 'n2')]",
                                    "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                    "'new')]|[('n1', 'affects', 'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                    "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n1', "
                                    "'eq', 'n3')]"],
                     'affects|quantity': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), "
                                          "('n1', 'quantity', 'n3')]"],
                     'affects|part': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'affects', 'n2')]",
                                      "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'affects', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n1', "
                                      "'part', 'n3')]"],
                     'affects|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'affects', 'n2'), "
                                        "('n2', 'lowers', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n3', "
                                        "'lowers', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'affects', 'n2'), "
                                        "('n1', 'lowers', 'n3')]"],
                     'affects|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'affects', 'n2'), "
                                        "('n2', 'raises', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n3', "
                                        "'raises', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'affects', 'n2'), "
                                        "('n1', 'raises', 'n3')]"],
                     'affects|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'affects', 'n2'), "
                                         "('n2', 'affects', 'n3')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), "
                                         "('n3', 'affects', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'affects', 'n2'), "
                                         "('n1', 'affects', 'n3')]"],
                     'affects|makes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                       "'new')]|[('n1', 'affects', 'n2')]",
                                       "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                       "'new')]|[('n1', 'affects', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n1', "
                                       "'makes', 'n3')]"],
                     'affects|measures': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), "
                                          "('n3', 'measures', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), "
                                          "('n1', 'measures', 'n3')]"],
                     'affects|becomes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'affects', 'n2')]",
                                         "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'affects', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), "
                                         "('n1', 'becomes', 'n3')]"],
                     'affects|competes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), "
                                          "('n1', 'competes', 'n3')]"],
                     'affects|has_property': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new')]|[('n1', 'affects', 'n2')]",
                                              "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new'), ('Ccc', 'class', 'new')]|[('n1', 'affects', "
                                              "'n2'), ('n3', 'has_property', 'n2')]",
                                              "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                              "'affects', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'affects|bogus': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                       "'new')]|[('n1', 'affects', 'n2')]",
                                       "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                       "'new')]|[('n1', 'affects', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'affects', 'n2'), ('n1', "
                                       "'bogus', 'n3')]"],
                     'makes|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', 'sub', 'n3')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', 'sub', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', 'sub', 'n3')]"],
                     'makes|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', 'eq', 'n3')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', 'eq', 'n2')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', 'eq', 'n3')]"],
                     'makes|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                        "'quantity', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', "
                                        "'quantity', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                        "'quantity', 'n3')]"],
                     'makes|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', 'part', "
                                    "'n3')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', 'part', "
                                    "'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', 'part', "
                                    "'n3')]"],
                     'makes|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                      "'lowers', 'n3')]",
                                      "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'lowers', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                      "'lowers', 'n3')]"],
                     'makes|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                      "'raises', 'n3')]",
                                      "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'raises', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                      "'raises', 'n3')]"],
                     'makes|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                       "'affects', 'n3')]",
                                       "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                       "'new')]|[('n1', 'affects', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                       "'affects', 'n3')]"],
                     'makes|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', 'makes', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', 'makes', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', 'makes', "
                                     "'n3')]"],
                     'makes|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                        "'measures', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', "
                                        "'measures', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                        "'measures', 'n3')]"],
                     'makes|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                       "'becomes', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', "
                                       "'becomes', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                       "'becomes', 'n3')]"],
                     'makes|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                        "'competes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', "
                                        "'competes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', "
                                        "'competes', 'n3')]"],
                     'makes|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                            "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), "
                                            "('n2', 'has_property', 'n3')]",
                                            "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                            "'new')]|[('n1', 'has_property', 'n2')]",
                                            "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                            "('Ccc', 'property', 'new')]|[('n1', 'makes', 'n2'), "
                                            "('n1', 'has_property', 'n3')]"],
                     'makes|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'makes', 'n2'), ('n2', 'bogus', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'makes', 'n2'), ('n3', 'bogus', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'makes', 'n2'), ('n1', 'bogus', "
                                     "'n3')]"],
                     'measures|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), ('n2', "
                                      "'sub', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), ('n3', "
                                      "'sub', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), ('n1', "
                                      "'sub', 'n3')]"],
                     'measures|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'measures', 'n2'), ('n2', 'eq', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'measures', 'n2'), ('n3', 'eq', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'measures', 'n2'), ('n1', 'eq', "
                                     "'n3')]"],
                     'measures|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n2', 'quantity', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n3', 'quantity', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n1', 'quantity', 'n3')]"],
                     'measures|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), ('n2', "
                                       "'part', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), ('n3', "
                                       "'part', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), ('n1', "
                                       "'part', 'n3')]"],
                     'measures|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'measures', 'n2'), "
                                         "('n2', 'lowers', 'n3')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                         "('n3', 'lowers', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'measures', 'n2'), "
                                         "('n1', 'lowers', 'n3')]"],
                     'measures|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'measures', 'n2'), "
                                         "('n2', 'raises', 'n3')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                         "('n3', 'raises', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'measures', 'n2'), "
                                         "('n1', 'raises', 'n3')]"],
                     'measures|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'measures', 'n2'), "
                                          "('n2', 'affects', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                          "('n3', 'affects', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'measures', 'n2'), "
                                          "('n1', 'affects', 'n3')]"],
                     'measures|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                        "('n2', 'makes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                        "('n3', 'makes', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                        "('n1', 'makes', 'n3')]"],
                     'measures|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n2', 'measures', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n3', 'measures', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n1', 'measures', 'n3')]"],
                     'measures|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                          "('n2', 'becomes', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                          "('n3', 'becomes', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                          "('n1', 'becomes', 'n3')]"],
                     'measures|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n2', 'competes', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n2', 'competes', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                           "('n1', 'competes', 'n3')]"],
                     'measures|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                               "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                               "'measures', 'n2'), ('n2', 'has_property', 'n3')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                               "'measures', 'n2'), ('n3', 'has_property', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                               "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                               "'measures', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'measures|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                        "('n2', 'bogus', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                        "('n3', 'bogus', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'measures', 'n2'), "
                                        "('n1', 'bogus', 'n3')]"],
                     'becomes|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'becomes', 'n2'), ('n2', 'sub', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'becomes', 'n2'), ('n3', 'sub', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'becomes', 'n2'), ('n1', 'sub', "
                                     "'n3')]"],
                     'becomes|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'becomes', 'n2'), ('n2', 'eq', "
                                    "'n3')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'becomes', 'n2'), ('n3', 'eq', "
                                    "'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'becomes', 'n2'), ('n1', 'eq', "
                                    "'n3')]"],
                     'becomes|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n2', 'quantity', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n3', 'quantity', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n1', 'quantity', 'n3')]"],
                     'becomes|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n2', "
                                      "'part', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n3', "
                                      "'part', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n1', "
                                      "'part', 'n3')]"],
                     'becomes|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'becomes', 'n2'), "
                                        "('n2', 'lowers', 'n3')]",
                                        "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                        "'new')]|[('n1', 'lowers', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'becomes', 'n2'), "
                                        "('n1', 'lowers', 'n3')]"],
                     'becomes|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'becomes', 'n2'), "
                                        "('n2', 'raises', 'n3')]",
                                        "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                        "'new')]|[('n1', 'raises', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'property', 'new')]|[('n1', 'becomes', 'n2'), "
                                        "('n1', 'raises', 'n3')]"],
                     'becomes|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'becomes', 'n2'), "
                                         "('n2', 'affects', 'n3')]",
                                         "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'affects', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'becomes', 'n2'), "
                                         "('n1', 'affects', 'n3')]"],
                     'becomes|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n2', "
                                       "'makes', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n3', "
                                       "'makes', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n1', "
                                       "'makes', 'n3')]"],
                     'becomes|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n2', 'measures', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n3', 'measures', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n1', 'measures', 'n3')]"],
                     'becomes|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                         "('n2', 'becomes', 'n3')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                         "('n3', 'becomes', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                         "('n1', 'becomes', 'n3')]"],
                     'becomes|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n2', 'competes', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n2', 'competes', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), "
                                          "('n1', 'competes', 'n3')]"],
                     'becomes|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                              "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                              "'becomes', 'n2'), ('n2', 'has_property', 'n3')]",
                                              "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                              "'new')]|[('n1', 'has_property', 'n2')]",
                                              "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                              "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                              "'becomes', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'becomes|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n2', "
                                       "'bogus', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n3', "
                                       "'bogus', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'becomes', 'n2'), ('n1', "
                                       "'bogus', 'n3')]"],
                     'competes|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), ('n2', "
                                      "'sub', 'n3')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), ('n3', "
                                      "'sub', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), ('n1', "
                                      "'sub', 'n3')]"],
                     'competes|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'competes', 'n2'), ('n2', 'eq', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'competes', 'n2'), ('n3', 'eq', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'competes', 'n2'), ('n1', 'eq', "
                                     "'n3')]"],
                     'competes|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n2', 'quantity', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n3', 'quantity', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n1', 'quantity', 'n3')]"],
                     'competes|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), ('n2', "
                                       "'part', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), ('n3', "
                                       "'part', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), ('n1', "
                                       "'part', 'n3')]"],
                     'competes|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'competes', 'n2'), "
                                         "('n2', 'lowers', 'n3')]",
                                         "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'lowers', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'competes', 'n2'), "
                                         "('n1', 'lowers', 'n3')]"],
                     'competes|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'competes', 'n2'), "
                                         "('n2', 'raises', 'n3')]",
                                         "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'raises', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                         "('Ccc', 'property', 'new')]|[('n1', 'competes', 'n2'), "
                                         "('n1', 'raises', 'n3')]"],
                     'competes|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'competes', 'n2'), "
                                          "('n2', 'affects', 'n3')]",
                                          "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'affects', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'property', 'new')]|[('n1', 'competes', 'n2'), "
                                          "('n1', 'affects', 'n3')]"],
                     'competes|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                        "('n2', 'makes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                        "('n3', 'makes', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                        "('n1', 'makes', 'n3')]"],
                     'competes|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n2', 'measures', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n3', 'measures', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n1', 'measures', 'n3')]"],
                     'competes|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                          "('n2', 'becomes', 'n3')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                          "('n3', 'becomes', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                          "('n1', 'becomes', 'n3')]"],
                     'competes|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n2', 'competes', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n2', 'competes', 'n3')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                           "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                           "('n1', 'competes', 'n3')]"],
                     'competes|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                               "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                               "'competes', 'n2'), ('n2', 'has_property', 'n3')]",
                                               "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', "
                                               "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                               "'competes', 'n2'), ('n1', 'has_property', 'n3')]"],
                     'competes|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                        "('n2', 'bogus', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                        "('n3', 'bogus', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'competes', 'n2'), "
                                        "('n1', 'bogus', 'n3')]"],
                     'has_property|sub': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'has_property', 'n2')]",
                                          "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                          "'new')]|[('n1', 'has_property', 'n2')]",
                                          "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                          "('Ccc', 'class', 'new')]|[('n1', 'has_property', 'n2'), "
                                          "('n1', 'sub', 'n3')]"],
                     'has_property|eq': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'has_property', 'n2')]",
                                         "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                         "'new')]|[('n1', 'has_property', 'n2')]",
                                         "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', 'new'), "
                                         "('Ccc', 'class', 'new')]|[('n1', 'has_property', 'n2'), "
                                         "('n1', 'eq', 'n3')]"],
                     'has_property|quantity': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                               "'has_property', 'n2'), ('n1', 'quantity', 'n3')]"],
                     'has_property|part': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                           "'new')]|[('n1', 'has_property', 'n2')]",
                                           "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                           "'new')]|[('n1', 'has_property', 'n2')]",
                                           "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                           "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                           "'has_property', 'n2'), ('n1', 'part', 'n3')]"],
                     'has_property|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                             "'has_property', 'n2'), ('n2', 'lowers', 'n3')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                             "'has_property', 'n2'), ('n3', 'lowers', 'n2')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                             "'has_property', 'n2'), ('n1', 'lowers', 'n3')]"],
                     'has_property|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                             "'has_property', 'n2'), ('n2', 'raises', 'n3')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                             "'has_property', 'n2'), ('n3', 'raises', 'n2')]",
                                             "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                             "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                             "'has_property', 'n2'), ('n1', 'raises', 'n3')]"],
                     'has_property|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                              "'has_property', 'n2'), ('n2', 'affects', 'n3')]",
                                              "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                              "'has_property', 'n2'), ('n3', 'affects', 'n2')]",
                                              "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new'), ('Ccc', 'property', 'new')]|[('n1', "
                                              "'has_property', 'n2'), ('n1', 'affects', 'n3')]"],
                     'has_property|makes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                            "'new')]|[('n1', 'has_property', 'n2')]",
                                            "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                            "'new')]|[('n1', 'has_property', 'n2')]",
                                            "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                            "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                            "'has_property', 'n2'), ('n1', 'makes', 'n3')]"],
                     'has_property|measures': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                               "'has_property', 'n2'), ('n3', 'measures', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                               "'has_property', 'n2'), ('n1', 'measures', 'n3')]"],
                     'has_property|becomes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new')]|[('n1', 'has_property', 'n2')]",
                                              "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new')]|[('n1', 'has_property', 'n2')]",
                                              "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                              "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                              "'has_property', 'n2'), ('n1', 'becomes', 'n3')]"],
                     'has_property|competes': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new')]|[('n1', 'has_property', 'n2')]",
                                               "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                               "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                               "'has_property', 'n2'), ('n1', 'competes', 'n3')]"],
                     'has_property|has_property': ["1|[('Aaa', 'class', 'new'), ('Bbb', "
                                                   "'property', 'new')]|[('n1', 'has_property', "
                                                   "'n2')]",
                                                   "0|[('Aaa', 'class', 'new'), ('Bbb', "
                                                   "'property', 'new'), ('Ccc', 'class', "
                                                   "'new')]|[('n1', 'has_property', 'n2'), ('n3', "
                                                   "'has_property', 'n2')]",
                                                   "0|[('Aaa', 'class', 'new'), ('Bbb', "
                                                   "'property', 'new'), ('Ccc', 'property', "
                                                   "'new')]|[('n1', 'has_property', 'n2'), ('n1', "
                                                   "'has_property', 'n3')]"],
                     'has_property|bogus': ["1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                            "'new')]|[('n1', 'has_property', 'n2')]",
                                            "1|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                            "'new')]|[('n1', 'has_property', 'n2')]",
                                            "0|[('Aaa', 'class', 'new'), ('Bbb', 'property', "
                                            "'new'), ('Ccc', 'class', 'new')]|[('n1', "
                                            "'has_property', 'n2'), ('n1', 'bogus', 'n3')]"],
                     'bogus|sub': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', 'sub', 'n3')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', 'sub', 'n2')]",
                                   "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                   "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', 'sub', 'n3')]"],
                     'bogus|eq': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', 'eq', 'n3')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', 'eq', 'n2')]",
                                  "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                  "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', 'eq', 'n3')]"],
                     'bogus|quantity': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                        "'quantity', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', "
                                        "'quantity', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                        "'quantity', 'n3')]"],
                     'bogus|part': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', 'part', "
                                    "'n3')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', 'part', "
                                    "'n2')]",
                                    "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                    "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', 'part', "
                                    "'n3')]"],
                     'bogus|lowers': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                      "'lowers', 'n3')]",
                                      "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'lowers', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                      "'lowers', 'n3')]"],
                     'bogus|raises': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                      "'raises', 'n3')]",
                                      "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                      "'new')]|[('n1', 'raises', 'n2')]",
                                      "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                      "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                      "'raises', 'n3')]"],
                     'bogus|affects': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                       "'affects', 'n3')]",
                                       "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                       "'new')]|[('n1', 'affects', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                       "'affects', 'n3')]"],
                     'bogus|makes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', 'makes', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', 'makes', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', 'makes', "
                                     "'n3')]"],
                     'bogus|measures': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                        "'measures', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', "
                                        "'measures', 'n2')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                        "'measures', 'n3')]"],
                     'bogus|becomes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                       "'becomes', 'n3')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', "
                                       "'becomes', 'n2')]",
                                       "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                       "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                       "'becomes', 'n3')]"],
                     'bogus|competes': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                        "'competes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', "
                                        "'competes', 'n3')]",
                                        "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                        "('Ccc', 'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', "
                                        "'competes', 'n3')]"],
                     'bogus|has_property': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                            "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), "
                                            "('n2', 'has_property', 'n3')]",
                                            "1|[('Ccc', 'class', 'new'), ('Bbb', 'property', "
                                            "'new')]|[('n1', 'has_property', 'n2')]",
                                            "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), "
                                            "('Ccc', 'property', 'new')]|[('n1', 'bogus', 'n2'), "
                                            "('n1', 'has_property', 'n3')]"],
                     'bogus|bogus': ["0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n2', 'bogus', "
                                     "'n3')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n3', 'bogus', "
                                     "'n2')]",
                                     "0|[('Aaa', 'class', 'new'), ('Bbb', 'class', 'new'), ('Ccc', "
                                     "'class', 'new')]|[('n1', 'bogus', 'n2'), ('n1', 'bogus', "
                                     "'n3')]"]},
 'add_questions': {'sub|ja': ([('Bbb の種類は？', 'kinds', None, 'n2')], 0),
                   'sub|en': ([('What are the kinds of Bbb?', 'kinds', None, 'n2')], 0),
                   'eq|ja': "!KeyError:'eq'",
                   'eq|en': "!KeyError:'eq'",
                   'quantity|ja': "!KeyError:'quantity'",
                   'quantity|en': "!KeyError:'quantity'",
                   'part|ja': ([('Bbb の一部は？', 'parts', None, 'n2')], 0),
                   'part|en': ([('What are the parts of Bbb?', 'parts', None, 'n2')], 0),
                   'lowers|ja': ([('Bbb を下げるものは？', 'factors', 'lowers', 'n2')], 0),
                   'lowers|en': ([('What lowers Bbb?', 'factors', 'lowers', 'n2')], 0),
                   'raises|ja': ([('Bbb を上げるものは？', 'factors', 'raises', 'n2')], 0),
                   'raises|en': ([('What raises Bbb?', 'factors', 'raises', 'n2')], 0),
                   'affects|ja': ([('Bbb を変えるものは？', 'factors', 'affects', 'n2')], 0),
                   'affects|en': ([('What changes Bbb?', 'factors', 'affects', 'n2')], 0),
                   'makes|ja': ([('Bbb を作る手法は？', 'makers', None, 'n2')], 0),
                   'makes|en': ([('Which methods make Bbb?', 'makers', None, 'n2')], 0),
                   'measures|ja': ([('Bbb を測る手法は？', 'measurers', None, 'n2')], 0),
                   'measures|en': ([('Which methods measure Bbb?', 'measurers', None, 'n2')], 0),
                   'becomes|ja': ([('Bbb に変わるものは？', 'sources', None, 'n2')], 0),
                   'becomes|en': ([('What turns into Bbb?', 'sources', None, 'n2')], 0),
                   'competes|ja': ([('Bbb と競合するものは？', 'competitors', None, 'n2')], 0),
                   'competes|en': ([('What competes with Bbb?', 'competitors', None, 'n2')], 0),
                   'has_property|ja': ([('Bbb を持つ材料は？', 'bearers', None, 'n2')], 0),
                   'has_property|en': ([('Which materials have Bbb?', 'bearers', None, 'n2')], 0),
                   'bogus|ja': "!KeyError:'bogus'",
                   'bogus|en': "!KeyError:'bogus'"},
 'scope_line': {'sub': [0, 3, 4],
                'eq': [0, 1, 2, 3, 4],
                'quantity': [0, 3, 4],
                'part': [0, 3, 4],
                'lowers': [0, 3, 4],
                'raises': [0, 3, 4],
                'affects': [0, 3, 4],
                'makes': [0, 3, 4],
                'measures': [0, 3, 4],
                'becomes': [0, 3, 4],
                'competes': [0, 1, 2, 3, 4],
                'has_property': [0, 3, 4],
                'bogus': [0, 3, 4]},
 'resolve_scope': {'sub': 'refused',
                   'eq': ['a', 'b'],
                   'quantity': 'refused',
                   'part': 'refused',
                   'lowers': 'refused',
                   'raises': 'refused',
                   'affects': 'refused',
                   'makes': 'refused',
                   'measures': 'refused',
                   'becomes': 'refused',
                   'competes': ['a', 'b'],
                   'has_property': 'refused',
                   'bogus': 'refused'},
 'answer_complete': {'lowers': True,
                     'raises': True,
                     'affects': True,
                     'bogus': False,
                     '': False,
                     'None': False}}


def test_the_pinned_tables_are_not_empty() -> None:
    assert set(EXPECTED) == {"consts", "build_ops_single", "build_ops_pairs", "add_questions",
                             "scope_line", "resolve_scope", "answer_complete"}


@pytest.mark.parametrize(
    "name, compute",
    [
        ("consts", _consts),
        ("build_ops_single", _grid_build_ops_single),
        ("build_ops_pairs", _grid_build_ops_pairs),
        ("add_questions", _grid_add_questions),
        ("scope_line", _grid_scope_line),
        ("resolve_scope", _grid_resolve_scope),
        ("answer_complete", _grid_answer_complete),
    ],
)
def test_results_are_the_same_as_before_the_copies_were_derived(name: str, compute) -> None:
    assert compute() == EXPECTED[name]
