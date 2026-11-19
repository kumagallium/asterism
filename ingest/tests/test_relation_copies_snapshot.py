# ruff: noqa: E501
"""R0b: ingest に残っていた関係の写しを目録の導出に置き換える「前」の結果の写し（スナップショット）。

土台 R0a の先頭 70a90164 の実装から機械的に書き出した（置き換えの前に固定）。写しが目録の
導出になった後も、値・型・並び・検査の結果（エラーの理由の記号と、どの検査が先に当たるか）が
同じであることを見張る。値を変えたくなったら、まず契約を見直す。

- 定数: 各モジュールの private な定数（写しだったもの）の値。
- 表: 関係 12 + 未知 × 端の種別 × 役割の全組み合わせでの検査の結果。
- 摘要: 台帳の乱数の試行の結果（``questions``・``ledger_questions``・``answer``・``drop_cycles``・
  ``_would_cycle``）の SHA-256。
"""

from __future__ import annotations

import hashlib
import json
import random

import pytest

from asterism import crosswalk_runtime as cr
from asterism import graph_diff as gd
from asterism import knowledge, sky_view, themes
from asterism import ledger_answer as la
from asterism import pair_relations as pr
from asterism import shared_vocab as sv
from asterism.knowledge_bundle import KnowledgeCopy

RELS = ("sub", "eq", "quantity", "part", "lowers", "raises", "affects", "makes", "measures",
        "becomes", "competes", "has_property", "bogus")
KINDS = ("class", "property", "other", None)
ROLES = (None, "material", "method")


# ---------------------------------------------------------------------------
# 定数（写しだったもの）
# ---------------------------------------------------------------------------


def _consts() -> dict:
    return {
        "pr._DIRECTED_TO_PROPERTY": pr._DIRECTED_TO_PROPERTY,
        "pr._SAME_KIND": pr._SAME_KIND,
        "pr._CYCLE_RELS": pr._CYCLE_RELS,
        "pr._FACTOR_DIRECTIONS": pr._FACTOR_DIRECTIONS,
        "la._FACTOR_DIRECTIONS": la._FACTOR_DIRECTIONS,
        "themes.KNOWLEDGE_DIRECTIONS": themes.KNOWLEDGE_DIRECTIONS,
        "knowledge.DIRECTIONS": knowledge.DIRECTIONS,
        "knowledge._DIRECTION_RELATIONS": knowledge._DIRECTION_RELATIONS,
        "sv.RESERVED_SLUGS": sorted(sv.RESERVED_SLUGS),
        "sv.RESERVED_SLUGS.type": type(sv.RESERVED_SLUGS).__name__,
        "sv._SUBSUMPTION_RELS": sv._SUBSUMPTION_RELS,
        "sky_view._LINE_RELATIONS": sky_view._LINE_RELATIONS,
        "sky_view._SUBSUMPTION": sky_view._SUBSUMPTION,
        "sky_view._EDGE_RELATIONS": sorted(sky_view._EDGE_RELATIONS),
        "sky_view._STANDARD_MATCH_RELATIONS": sorted(sky_view._STANDARD_MATCH_RELATIONS),
        "sky_view._DECISION_RELATIONS": sorted(sky_view._DECISION_RELATIONS),
        "sv.cycle_rels": {p: sv.cycle_rels(p) for p in (*cr.ALIGN_RELATIONS, "bogus", "")},
    }


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------


def _cell(v: object) -> str:
    return "-" if v is None else str(v)


def _grid_relation_problem() -> dict:
    """``pair_relations.relation_problem``: 関係 × 役割の組 → 種別 4×4 の結果（並びは KINDS の直積）。"""
    out = {}
    for rel in RELS:
        for fr in ROLES:
            for tr in ROLES:
                cells = [
                    _cell(pr.relation_problem(
                        rel, {"id": "a", "kind": fk, "role": fr}, {"id": "b", "kind": tk, "role": tr}
                    ))
                    for fk in KINDS
                    for tk in KINDS
                ]
                out[f"{rel}|{_cell(fr)}|{_cell(tr)}"] = ",".join(cells)
    out["same"] = _cell(pr.relation_problem("sub", {"id": "a", "kind": "class"}, {"id": "a", "kind": "property"}))
    return out


def _t(key: str, kind: str, role: str | None) -> dict:
    return {
        "key": key, "label_ja": key, "label_en": key, "aliases": [], "kind": kind,
        "use": "new", "reason": "", **({"role": role} if role else {}),
    }


def _ai(concepts: list[dict], relations: list[dict]) -> dict:
    return {"theme_label": "", "concepts": concepts, "relations": relations, "answers": [],
            "suggested_questions": []}


def _err(e: Exception) -> str:
    return f"!{type(e).__name__}:{e}"


def _post(rel: str, fk: str, fr: str | None, tk: str, tr: str | None) -> str:
    try:
        out = themes.postprocess_draft(
            _ai([_t("a", fk, fr), _t("b", tk, tr)], [{"from": "a", "rel": rel, "to": "b", "reason": ""}]),
            questions=[],
            existing=[],
        )
    except Exception as e:
        return _err(e)
    rels = out["relations"]
    return _cell(rels[0]["problem"]) if rels else "(none)"


def _grid_postprocess() -> dict:
    """``themes.postprocess_draft`` の relations[0].problem: 関係 × 役割の組 → 種別 2×2。"""
    out = {}
    for rel in RELS:
        for fr in ROLES:
            for tr in ROLES:
                cells = [_post(rel, fk, fr, tk, tr) for fk in ("class", "property") for tk in ("class", "property")]
                out[f"{rel}|{_cell(fr)}|{_cell(tr)}"] = ",".join(cells)
    return out


def _grid_postprocess_cycle() -> dict:
    """a→b と b→a の 2 本: 関係 → (2 本目までの problem 列, 本数)。種別は 2 通り。"""
    out = {}
    for rel in RELS:
        for k in ("class", "property"):
            try:
                o = themes.postprocess_draft(
                    _ai(
                        [_t("a", k, "material"), _t("b", k, "material")],
                        [{"from": "a", "rel": rel, "to": "b", "reason": ""},
                         {"from": "b", "rel": rel, "to": "a", "reason": ""}],
                    ),
                    questions=[],
                    existing=[],
                )
            except Exception as e:
                out[f"{rel}|{k}"] = _err(e)
                continue
            out[f"{rel}|{k}"] = [(r["from"], _cell(r["problem"])) for r in o["relations"]]
    return out


def _applier(rels: list[dict]) -> gd._Applier:
    return gd._Applier(
        {"concepts": [_t("a", "class", None), _t("b", "class", None)], "relations": rels},
        existing=[], dismissed=[], by="ai",
    )


def _check(ap: gd._Applier, rel: str, fk: str, fr: str | None, tk: str, tr: str | None) -> str:
    try:
        return _cell(ap.check_relation(
            {"key": "a", "kind": fk, "role": fr, "use": "new"}, rel,
            {"key": "b", "kind": tk, "role": tr, "use": "new"}, None))
    except Exception as e:
        return _err(e)


def _grid_check_relation() -> dict:
    """``graph_diff._Applier.check_relation``: 関係 × 役割の組 → 種別 2×2。反対向きの線がある版も。"""
    out = {}
    for rel in RELS:
        for fr in ROLES:
            for tr in ROLES:
                cells = []
                for fk in ("class", "property"):
                    for tk in ("class", "property"):
                        ap = _applier([])
                        cells.append(_check(ap, rel, fk, fr, tk, tr))
                out[f"{rel}|{_cell(fr)}|{_cell(tr)}"] = ",".join(cells)
    for rel in RELS:
        cells = []
        for k in ("class", "property"):
            ap = _applier([{"from": "b", "rel": rel, "to": "a", "by": "ai"}])
            cells.append(_check(ap, rel, k, "material", k, "material"))
        out[f"{rel}|back"] = ",".join(cells)
    return out


def _grid_has_relation() -> dict:
    out = {}
    for rel in RELS:
        ap = _applier([{"from": "a", "to": "b", "rel": rel}])
        out[rel] = [ap.has_relation("a", rel, "b"), ap.has_relation("b", rel, "a")]
    return out


def _grid_line_shapes() -> dict:
    """``themes._check_line_shapes``: 述語 × 端の種別の集合 → エラーの文（通れば -）。"""
    shape_sets = ({"class"}, {"property"}, {"class", "property"}, set())
    out = {}
    for pred in (*cr.ALIGN_RELATIONS, "bogus"):
        cells = []
        for s in shape_sets:
            for t in shape_sets:
                try:
                    themes._check_line_shapes(
                        [themes._Line("s", "t", pred)], {"s": set(s), "t": set(t)})
                    cells.append("-")
                except Exception as e:
                    cells.append(_err(e))
        out[pred] = cells
    return out


# ---------------------------------------------------------------------------
# 摘要（台帳の乱数の試行）
# ---------------------------------------------------------------------------

_AT = "2026-11-19T00:00:00+00:00"


def _trial(seed: int) -> tuple[dict, KnowledgeCopy]:
    rnd = random.Random(seed)
    n = rnd.randint(3, 6)  # 少ない語で輪ができやすい
    terms = [
        {"id": f"t{i}", "name": f"名{i}", "kind": rnd.choice(["class", "class", "property"]), "role": None}
        for i in range(n)
    ]
    ids = [t["id"] for t in terms]
    pairs: dict[str, dict] = {}
    for _ in range(rnd.randint(3, 16)):
        a, b = rnd.sample(ids, 2)
        key = "|".join(sorted((a, b)))
        if key in pairs:
            continue
        rel = rnd.choice(RELS[:-1])
        x, y = sorted((a, b))
        e = {"key": key, "a": x, "b": y, "state": "relation", "rel": rel, "from": a, "to": b,
             "evidence": [], "claims": [], "claims_total": 0}
        if rnd.random() < 0.3:
            e["confirmed"] = {"at": _AT}
        pairs[key] = e
    ld = pr.empty_ledger("lab")
    ld["pairs"] = pairs
    origin = []
    for _ in range(rnd.randint(0, 6)):
        a, b = rnd.sample(ids, 2)
        rel = rnd.choice(RELS[:-1])
        if rel in pr.SYMMETRIC_PAIR_RELATIONS:
            a, b = sorted((a, b))
        origin.append({"from": a, "rel": rel, "to": b, "by": "origin"})
    ld["origin"] = origin
    return ld, KnowledgeCopy(terms=terms)


def _digest() -> str:
    h = hashlib.sha256()
    for seed in range(80):
        ld, copy = _trial(seed)
        items, total = pr.questions(ld, copy, lang="ja", limit=100000)
        got = la.ledger_questions(ld, copy, lang="ja")
        ans = [la.answer(ld, copy, q, lang="ja") for q in got]
        dropped = pr.drop_cycles([dict(e) for e in ld["pairs"].values()], ())
        cyc = [
            pr._would_cycle(ld, e["key"], rel, str(e["from"]), str(e["to"]))
            for e in ld["pairs"].values()
            for rel in RELS[:-1]
        ]
        h.update(json.dumps([items, total, got, ans, dropped, cyc], sort_keys=True, default=str,
                            ensure_ascii=False).encode())
    return h.hexdigest()


EXPECTED: dict = {'consts': {'pr._DIRECTED_TO_PROPERTY': ('lowers', 'raises', 'affects'),
            'pr._SAME_KIND': ('sub', 'eq', 'part'),
            'pr._CYCLE_RELS': ('sub', 'part'),
            'pr._FACTOR_DIRECTIONS': {'lowers': ('lowers', 'affects'),
                                      'raises': ('raises', 'affects'),
                                      'affects': ('affects',)},
            'la._FACTOR_DIRECTIONS': {'lowers': ('lowers', 'affects'),
                                      'raises': ('raises', 'affects'),
                                      'affects': ('affects',)},
            'themes.KNOWLEDGE_DIRECTIONS': ('lowers', 'raises', 'affects'),
            'knowledge.DIRECTIONS': ('lowers', 'raises', 'affects'),
            'knowledge._DIRECTION_RELATIONS': {'lowers': ('lowers',),
                                               'raises': ('raises',),
                                               'affects': ('lowers', 'raises', 'affects')},
            'sv.RESERVED_SLUGS': ['affects',
                                  'becomes',
                                  'competes',
                                  'competes_with',
                                  'has_property',
                                  'lowers',
                                  'makes',
                                  'measures',
                                  'part_of',
                                  'raises',
                                  'regulates'],
            'sv.RESERVED_SLUGS.type': 'frozenset',
            'sv._SUBSUMPTION_RELS': ('subClassOf', 'subPropertyOf', 'isPartOf'),
            'sky_view._LINE_RELATIONS': ('subClassOf',
                                         'equivalentClass',
                                         'subPropertyOf',
                                         'equivalentProperty'),
            'sky_view._SUBSUMPTION': ('subClassOf', 'subPropertyOf'),
            'sky_view._EDGE_RELATIONS': ['affects',
                                         'becomes',
                                         'competesWith',
                                         'hasProperty',
                                         'isPartOf',
                                         'lowers',
                                         'makes',
                                         'measuresMaterial',
                                         'measuresProperty',
                                         'raises'],
            'sky_view._STANDARD_MATCH_RELATIONS': ['equivalentClass',
                                                   'equivalentProperty',
                                                   'hasQuantityKind'],
            'sky_view._DECISION_RELATIONS': ['equivalentClass',
                                             'equivalentProperty',
                                             'hasQuantityKind',
                                             'subClassOf',
                                             'subPropertyOf'],
            'sv.cycle_rels': {'equivalentClass': None,
                              'subClassOf': ('subClassOf', 'equivalentClass'),
                              'equivalentProperty': None,
                              'subPropertyOf': ('subPropertyOf', 'equivalentProperty'),
                              'hasQuantityKind': None,
                              'isPartOf': ('isPartOf',),
                              'lowers': None,
                              'raises': None,
                              'affects': None,
                              'makes': None,
                              'measuresProperty': None,
                              'measuresMaterial': None,
                              'becomes': None,
                              'competesWith': None,
                              'hasProperty': None,
                              'bogus': None,
                              '': None}},
 'relation_problem': {'sub|-|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|-|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|-|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|material|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|material|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|material|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|method|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|method|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'sub|method|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|-|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|-|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|-|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|material|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|material|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|material|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|method|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|method|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'eq|method|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'quantity|-|-': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|-|material': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|-|method': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|material|-': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|material|material': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|material|method': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|method|-': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|method|material': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'quantity|method|method': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'part|-|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|-|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|-|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|material|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|material|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|material|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|method|-': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|method|material': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'part|method|method': '-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-,mixed,mixed,mixed,mixed,-',
                      'lowers|-|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|-|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|-|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|material|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|material|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|material|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|method|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|method|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'lowers|method|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|-|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|-|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|-|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|material|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|material|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|material|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|method|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|method|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'raises|method|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|-|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|-|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|-|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|material|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|material|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|material|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|method|-': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|method|material': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'affects|method|method': 'target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property,target_not_property,-,target_not_property,target_not_property',
                      'makes|-|-': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|-|material': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|-|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|material|-': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|material|material': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|material|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|method|-': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|method|material': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'makes|method|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|-|-': '-,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|-|material': '-,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|-|method': 'role_mismatch,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|material|-': 'role_mismatch,role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|material|material': 'role_mismatch,role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|material|method': 'role_mismatch,role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|method|-': '-,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|method|material': '-,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'measures|method|method': 'role_mismatch,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|-|-': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|-|material': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|-|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|material|-': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|material|material': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|material|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|method|-': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|method|material': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'becomes|method|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|-|-': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|-|material': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|-|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|material|-': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|material|material': '-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|material|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|method|-': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|method|material': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'competes|method|method': 'role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|-|-': 'mixed,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|-|material': 'mixed,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|-|method': 'mixed,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|material|-': 'mixed,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|material|material': 'mixed,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|material|method': 'mixed,-,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|method|-': 'mixed,role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|method|material': 'mixed,role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'has_property|method|method': 'mixed,role_mismatch,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed,mixed',
                      'bogus|-|-': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|-|material': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|-|method': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|material|-': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|material|material': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|material|method': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|method|-': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|method|material': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'bogus|method|method': 'relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation,relation',
                      'same': 'same'},
 'postprocess': {'sub|-|-': '-,mixed,mixed,-',
                 'sub|-|material': '-,mixed,mixed,-',
                 'sub|-|method': '-,mixed,mixed,-',
                 'sub|material|-': '-,mixed,mixed,-',
                 'sub|material|material': '-,mixed,mixed,-',
                 'sub|material|method': '-,mixed,mixed,-',
                 'sub|method|-': '-,mixed,mixed,-',
                 'sub|method|material': '-,mixed,mixed,-',
                 'sub|method|method': '-,mixed,mixed,-',
                 'eq|-|-': '-,mixed,mixed,-',
                 'eq|-|material': '-,mixed,mixed,-',
                 'eq|-|method': '-,mixed,mixed,-',
                 'eq|material|-': '-,mixed,mixed,-',
                 'eq|material|material': '-,mixed,mixed,-',
                 'eq|material|method': '-,mixed,mixed,-',
                 'eq|method|-': '-,mixed,mixed,-',
                 'eq|method|material': '-,mixed,mixed,-',
                 'eq|method|method': '-,mixed,mixed,-',
                 'quantity|-|-': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|-|material': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|-|method': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|material|-': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|material|material': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|material|method': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|method|-': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|method|material': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'quantity|method|method': 'quantity_target,quantity_target,quantity_target,quantity_target',
                 'part|-|-': '-,mixed,mixed,-',
                 'part|-|material': '-,mixed,mixed,-',
                 'part|-|method': '-,mixed,mixed,-',
                 'part|material|-': '-,mixed,mixed,-',
                 'part|material|material': '-,mixed,mixed,-',
                 'part|material|method': '-,mixed,mixed,-',
                 'part|method|-': '-,mixed,mixed,-',
                 'part|method|material': '-,mixed,mixed,-',
                 'part|method|method': '-,mixed,mixed,-',
                 'lowers|-|-': 'target_not_property,-,target_not_property,-',
                 'lowers|-|material': 'target_not_property,-,target_not_property,-',
                 'lowers|-|method': 'target_not_property,-,target_not_property,-',
                 'lowers|material|-': 'target_not_property,-,target_not_property,-',
                 'lowers|material|material': 'target_not_property,-,target_not_property,-',
                 'lowers|material|method': 'target_not_property,-,target_not_property,-',
                 'lowers|method|-': 'target_not_property,-,target_not_property,-',
                 'lowers|method|material': 'target_not_property,-,target_not_property,-',
                 'lowers|method|method': 'target_not_property,-,target_not_property,-',
                 'raises|-|-': 'target_not_property,-,target_not_property,-',
                 'raises|-|material': 'target_not_property,-,target_not_property,-',
                 'raises|-|method': 'target_not_property,-,target_not_property,-',
                 'raises|material|-': 'target_not_property,-,target_not_property,-',
                 'raises|material|material': 'target_not_property,-,target_not_property,-',
                 'raises|material|method': 'target_not_property,-,target_not_property,-',
                 'raises|method|-': 'target_not_property,-,target_not_property,-',
                 'raises|method|material': 'target_not_property,-,target_not_property,-',
                 'raises|method|method': 'target_not_property,-,target_not_property,-',
                 'affects|-|-': 'target_not_property,-,target_not_property,-',
                 'affects|-|material': 'target_not_property,-,target_not_property,-',
                 'affects|-|method': 'target_not_property,-,target_not_property,-',
                 'affects|material|-': 'target_not_property,-,target_not_property,-',
                 'affects|material|material': 'target_not_property,-,target_not_property,-',
                 'affects|material|method': 'target_not_property,-,target_not_property,-',
                 'affects|method|-': 'target_not_property,-,target_not_property,-',
                 'affects|method|material': 'target_not_property,-,target_not_property,-',
                 'affects|method|method': 'target_not_property,-,target_not_property,-',
                 'makes|-|-': '-,mixed,mixed,mixed',
                 'makes|-|material': '-,mixed,mixed,mixed',
                 'makes|-|method': '-,mixed,mixed,mixed',
                 'makes|material|-': '-,mixed,mixed,mixed',
                 'makes|material|material': '-,mixed,mixed,mixed',
                 'makes|material|method': '-,mixed,mixed,mixed',
                 'makes|method|-': '-,mixed,mixed,mixed',
                 'makes|method|material': '-,mixed,mixed,mixed',
                 'makes|method|method': '-,mixed,mixed,mixed',
                 'measures|-|-': '-,-,mixed,mixed',
                 'measures|-|material': '-,-,mixed,mixed',
                 'measures|-|method': '-,-,mixed,mixed',
                 'measures|material|-': '-,-,mixed,mixed',
                 'measures|material|material': '-,-,mixed,mixed',
                 'measures|material|method': '-,-,mixed,mixed',
                 'measures|method|-': '-,-,mixed,mixed',
                 'measures|method|material': '-,-,mixed,mixed',
                 'measures|method|method': '-,-,mixed,mixed',
                 'becomes|-|-': '-,mixed,mixed,mixed',
                 'becomes|-|material': '-,mixed,mixed,mixed',
                 'becomes|-|method': '-,mixed,mixed,mixed',
                 'becomes|material|-': '-,mixed,mixed,mixed',
                 'becomes|material|material': '-,mixed,mixed,mixed',
                 'becomes|material|method': '-,mixed,mixed,mixed',
                 'becomes|method|-': '-,mixed,mixed,mixed',
                 'becomes|method|material': '-,mixed,mixed,mixed',
                 'becomes|method|method': '-,mixed,mixed,mixed',
                 'competes|-|-': '-,mixed,mixed,mixed',
                 'competes|-|material': '-,mixed,mixed,mixed',
                 'competes|-|method': '-,mixed,mixed,mixed',
                 'competes|material|-': '-,mixed,mixed,mixed',
                 'competes|material|material': '-,mixed,mixed,mixed',
                 'competes|material|method': '-,mixed,mixed,mixed',
                 'competes|method|-': '-,mixed,mixed,mixed',
                 'competes|method|material': '-,mixed,mixed,mixed',
                 'competes|method|method': '-,mixed,mixed,mixed',
                 'has_property|-|-': 'mixed,-,mixed,mixed',
                 'has_property|-|material': 'mixed,-,mixed,mixed',
                 'has_property|-|method': 'mixed,-,mixed,mixed',
                 'has_property|material|-': 'mixed,-,mixed,mixed',
                 'has_property|material|material': 'mixed,-,mixed,mixed',
                 'has_property|material|method': 'mixed,-,mixed,mixed',
                 'has_property|method|-': 'mixed,-,mixed,mixed',
                 'has_property|method|material': 'mixed,-,mixed,mixed',
                 'has_property|method|method': 'mixed,-,mixed,mixed',
                 'bogus|-|-': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                              "'raises', 'affects', 'makes', 'measures', 'becomes', 'competes', "
                              "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                              "'quantity', 'part', 'lowers', 'raises', 'affects', 'makes', "
                              "'measures', 'becomes', 'competes', 'has_property') のどれかです: "
                              "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                              "'raises', 'affects', 'makes', 'measures', 'becomes', 'competes', "
                              "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                              "'quantity', 'part', 'lowers', 'raises', 'affects', 'makes', "
                              "'measures', 'becomes', 'competes', 'has_property') のどれかです: 'bogus'",
                 'bogus|-|material': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                     "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                     "'competes', 'has_property') のどれかです: 'bogus',!ThemeError:関係は "
                                     "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', "
                                     "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                     "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', "
                                     "'eq', 'quantity', 'part', 'lowers', 'raises', 'affects', "
                                     "'makes', 'measures', 'becomes', 'competes', 'has_property') "
                                     "のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', "
                                     "'part', 'lowers', 'raises', 'affects', 'makes', 'measures', "
                                     "'becomes', 'competes', 'has_property') のどれかです: 'bogus'",
                 'bogus|-|method': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                   "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                   "'competes', 'has_property') のどれかです: 'bogus',!ThemeError:関係は "
                                   "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', "
                                   "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                   "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                                   "'quantity', 'part', 'lowers', 'raises', 'affects', 'makes', "
                                   "'measures', 'becomes', 'competes', 'has_property') のどれかです: "
                                   "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                   "'lowers', 'raises', 'affects', 'makes', 'measures', 'becomes', "
                                   "'competes', 'has_property') のどれかです: 'bogus'",
                 'bogus|material|-': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                     "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                     "'competes', 'has_property') のどれかです: 'bogus',!ThemeError:関係は "
                                     "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', "
                                     "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                     "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', "
                                     "'eq', 'quantity', 'part', 'lowers', 'raises', 'affects', "
                                     "'makes', 'measures', 'becomes', 'competes', 'has_property') "
                                     "のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', "
                                     "'part', 'lowers', 'raises', 'affects', 'makes', 'measures', "
                                     "'becomes', 'competes', 'has_property') のどれかです: 'bogus'",
                 'bogus|material|material': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                            "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                            "'becomes', 'competes', 'has_property') のどれかです: "
                                            "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', "
                                            "'part', 'lowers', 'raises', 'affects', 'makes', "
                                            "'measures', 'becomes', 'competes', 'has_property') "
                                            "のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                                            "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                            "'makes', 'measures', 'becomes', 'competes', "
                                            "'has_property') のどれかです: 'bogus',!ThemeError:関係は "
                                            "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', "
                                            "'affects', 'makes', 'measures', 'becomes', "
                                            "'competes', 'has_property') のどれかです: 'bogus'",
                 'bogus|material|method': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                          "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                          "'becomes', 'competes', 'has_property') のどれかです: "
                                          "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', "
                                          "'part', 'lowers', 'raises', 'affects', 'makes', "
                                          "'measures', 'becomes', 'competes', 'has_property') "
                                          "のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                                          "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                          "'makes', 'measures', 'becomes', 'competes', "
                                          "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', "
                                          "'eq', 'quantity', 'part', 'lowers', 'raises', "
                                          "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                          "'has_property') のどれかです: 'bogus'",
                 'bogus|method|-': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                   "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                   "'competes', 'has_property') のどれかです: 'bogus',!ThemeError:関係は "
                                   "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', "
                                   "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                   "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                                   "'quantity', 'part', 'lowers', 'raises', 'affects', 'makes', "
                                   "'measures', 'becomes', 'competes', 'has_property') のどれかです: "
                                   "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                   "'lowers', 'raises', 'affects', 'makes', 'measures', 'becomes', "
                                   "'competes', 'has_property') のどれかです: 'bogus'",
                 'bogus|method|material': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                          "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                          "'becomes', 'competes', 'has_property') のどれかです: "
                                          "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', "
                                          "'part', 'lowers', 'raises', 'affects', 'makes', "
                                          "'measures', 'becomes', 'competes', 'has_property') "
                                          "のどれかです: 'bogus',!ThemeError:関係は ('sub', 'eq', "
                                          "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                          "'makes', 'measures', 'becomes', 'competes', "
                                          "'has_property') のどれかです: 'bogus',!ThemeError:関係は ('sub', "
                                          "'eq', 'quantity', 'part', 'lowers', 'raises', "
                                          "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                          "'has_property') のどれかです: 'bogus'",
                 'bogus|method|method': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                        "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                        "'becomes', 'competes', 'has_property') のどれかです: "
                                        "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                        "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                        "'becomes', 'competes', 'has_property') のどれかです: "
                                        "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                        "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                        "'becomes', 'competes', 'has_property') のどれかです: "
                                        "'bogus',!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                        "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                        "'becomes', 'competes', 'has_property') のどれかです: 'bogus'"},
 'postprocess_cycle': {'sub|class': [('a', '-'), ('b', 'cycle')],
                       'sub|property': [('a', '-'), ('b', 'cycle')],
                       'eq|class': [('a', '-'), ('b', '-')],
                       'eq|property': [('a', '-'), ('b', '-')],
                       'quantity|class': [('a', 'quantity_target'), ('b', 'quantity_target')],
                       'quantity|property': [('a', 'quantity_target'), ('b', 'quantity_target')],
                       'part|class': [('a', '-'), ('b', 'cycle')],
                       'part|property': [('a', '-'), ('b', 'cycle')],
                       'lowers|class': [('a', 'target_not_property'), ('b', 'target_not_property')],
                       'lowers|property': [('a', '-'), ('b', '-')],
                       'raises|class': [('a', 'target_not_property'), ('b', 'target_not_property')],
                       'raises|property': [('a', '-'), ('b', '-')],
                       'affects|class': [('a', 'target_not_property'),
                                         ('b', 'target_not_property')],
                       'affects|property': [('a', '-'), ('b', '-')],
                       'makes|class': [('a', '-'), ('b', '-')],
                       'makes|property': [('a', 'mixed'), ('b', 'mixed')],
                       'measures|class': [('a', '-'), ('b', '-')],
                       'measures|property': [('a', 'mixed'), ('b', 'mixed')],
                       'becomes|class': [('a', '-'), ('b', '-')],
                       'becomes|property': [('a', 'mixed'), ('b', 'mixed')],
                       'competes|class': [('a', '-')],
                       'competes|property': [('a', 'mixed')],
                       'has_property|class': [('a', 'mixed'), ('b', 'mixed')],
                       'has_property|property': [('a', 'mixed'), ('b', 'mixed')],
                       'bogus|class': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                      "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                      "'competes', 'has_property') のどれかです: 'bogus'",
                       'bogus|property': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                         "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                         "'becomes', 'competes', 'has_property') のどれかです: 'bogus'"},
 'check_relation': {'sub|-|-': '-,mixed,mixed,-',
                    'sub|-|material': '-,mixed,mixed,-',
                    'sub|-|method': '-,mixed,mixed,-',
                    'sub|material|-': '-,mixed,mixed,-',
                    'sub|material|material': '-,mixed,mixed,-',
                    'sub|material|method': '-,mixed,mixed,-',
                    'sub|method|-': '-,mixed,mixed,-',
                    'sub|method|material': '-,mixed,mixed,-',
                    'sub|method|method': '-,mixed,mixed,-',
                    'eq|-|-': '-,mixed,mixed,-',
                    'eq|-|material': '-,mixed,mixed,-',
                    'eq|-|method': '-,mixed,mixed,-',
                    'eq|material|-': '-,mixed,mixed,-',
                    'eq|material|material': '-,mixed,mixed,-',
                    'eq|material|method': '-,mixed,mixed,-',
                    'eq|method|-': '-,mixed,mixed,-',
                    'eq|method|material': '-,mixed,mixed,-',
                    'eq|method|method': '-,mixed,mixed,-',
                    'quantity|-|-': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|-|material': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|-|method': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|material|-': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|material|material': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|material|method': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|method|-': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|method|material': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'quantity|method|method': 'quantity_target,quantity_target,quantity_target,quantity_target',
                    'part|-|-': '-,mixed,mixed,-',
                    'part|-|material': '-,mixed,mixed,-',
                    'part|-|method': '-,mixed,mixed,-',
                    'part|material|-': '-,mixed,mixed,-',
                    'part|material|material': '-,mixed,mixed,-',
                    'part|material|method': '-,mixed,mixed,-',
                    'part|method|-': '-,mixed,mixed,-',
                    'part|method|material': '-,mixed,mixed,-',
                    'part|method|method': '-,mixed,mixed,-',
                    'lowers|-|-': 'target_not_property,-,target_not_property,-',
                    'lowers|-|material': 'target_not_property,-,target_not_property,-',
                    'lowers|-|method': 'target_not_property,-,target_not_property,-',
                    'lowers|material|-': 'target_not_property,-,target_not_property,-',
                    'lowers|material|material': 'target_not_property,-,target_not_property,-',
                    'lowers|material|method': 'target_not_property,-,target_not_property,-',
                    'lowers|method|-': 'target_not_property,-,target_not_property,-',
                    'lowers|method|material': 'target_not_property,-,target_not_property,-',
                    'lowers|method|method': 'target_not_property,-,target_not_property,-',
                    'raises|-|-': 'target_not_property,-,target_not_property,-',
                    'raises|-|material': 'target_not_property,-,target_not_property,-',
                    'raises|-|method': 'target_not_property,-,target_not_property,-',
                    'raises|material|-': 'target_not_property,-,target_not_property,-',
                    'raises|material|material': 'target_not_property,-,target_not_property,-',
                    'raises|material|method': 'target_not_property,-,target_not_property,-',
                    'raises|method|-': 'target_not_property,-,target_not_property,-',
                    'raises|method|material': 'target_not_property,-,target_not_property,-',
                    'raises|method|method': 'target_not_property,-,target_not_property,-',
                    'affects|-|-': 'target_not_property,-,target_not_property,-',
                    'affects|-|material': 'target_not_property,-,target_not_property,-',
                    'affects|-|method': 'target_not_property,-,target_not_property,-',
                    'affects|material|-': 'target_not_property,-,target_not_property,-',
                    'affects|material|material': 'target_not_property,-,target_not_property,-',
                    'affects|material|method': 'target_not_property,-,target_not_property,-',
                    'affects|method|-': 'target_not_property,-,target_not_property,-',
                    'affects|method|material': 'target_not_property,-,target_not_property,-',
                    'affects|method|method': 'target_not_property,-,target_not_property,-',
                    'makes|-|-': '-,mixed,mixed,mixed',
                    'makes|-|material': '-,mixed,mixed,mixed',
                    'makes|-|method': 'role_mismatch,mixed,mixed,mixed',
                    'makes|material|-': 'role_mismatch,mixed,mixed,mixed',
                    'makes|material|material': 'role_mismatch,mixed,mixed,mixed',
                    'makes|material|method': 'role_mismatch,mixed,mixed,mixed',
                    'makes|method|-': '-,mixed,mixed,mixed',
                    'makes|method|material': '-,mixed,mixed,mixed',
                    'makes|method|method': 'role_mismatch,mixed,mixed,mixed',
                    'measures|-|-': '-,-,mixed,mixed',
                    'measures|-|material': '-,-,mixed,mixed',
                    'measures|-|method': 'role_mismatch,-,mixed,mixed',
                    'measures|material|-': 'role_mismatch,role_mismatch,mixed,mixed',
                    'measures|material|material': 'role_mismatch,role_mismatch,mixed,mixed',
                    'measures|material|method': 'role_mismatch,role_mismatch,mixed,mixed',
                    'measures|method|-': '-,-,mixed,mixed',
                    'measures|method|material': '-,-,mixed,mixed',
                    'measures|method|method': 'role_mismatch,-,mixed,mixed',
                    'becomes|-|-': '-,mixed,mixed,mixed',
                    'becomes|-|material': '-,mixed,mixed,mixed',
                    'becomes|-|method': 'role_mismatch,mixed,mixed,mixed',
                    'becomes|material|-': '-,mixed,mixed,mixed',
                    'becomes|material|material': '-,mixed,mixed,mixed',
                    'becomes|material|method': 'role_mismatch,mixed,mixed,mixed',
                    'becomes|method|-': 'role_mismatch,mixed,mixed,mixed',
                    'becomes|method|material': 'role_mismatch,mixed,mixed,mixed',
                    'becomes|method|method': 'role_mismatch,mixed,mixed,mixed',
                    'competes|-|-': '-,mixed,mixed,mixed',
                    'competes|-|material': '-,mixed,mixed,mixed',
                    'competes|-|method': 'role_mismatch,mixed,mixed,mixed',
                    'competes|material|-': '-,mixed,mixed,mixed',
                    'competes|material|material': '-,mixed,mixed,mixed',
                    'competes|material|method': 'role_mismatch,mixed,mixed,mixed',
                    'competes|method|-': 'role_mismatch,mixed,mixed,mixed',
                    'competes|method|material': 'role_mismatch,mixed,mixed,mixed',
                    'competes|method|method': 'role_mismatch,mixed,mixed,mixed',
                    'has_property|-|-': 'mixed,-,mixed,mixed',
                    'has_property|-|material': 'mixed,-,mixed,mixed',
                    'has_property|-|method': 'mixed,-,mixed,mixed',
                    'has_property|material|-': 'mixed,-,mixed,mixed',
                    'has_property|material|material': 'mixed,-,mixed,mixed',
                    'has_property|material|method': 'mixed,-,mixed,mixed',
                    'has_property|method|-': 'mixed,role_mismatch,mixed,mixed',
                    'has_property|method|material': 'mixed,role_mismatch,mixed,mixed',
                    'has_property|method|method': 'mixed,role_mismatch,mixed,mixed',
                    'bogus|-|-': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                 "'raises', 'affects', 'makes', 'measures', 'becomes', 'competes', "
                                 "'has_property') のどれかです: 'bogus',mixed,mixed,!ThemeError:関係は "
                                 "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', 'affects', "
                                 "'makes', 'measures', 'becomes', 'competes', 'has_property') "
                                 "のどれかです: 'bogus'",
                    'bogus|-|material': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                        "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                        "'becomes', 'competes', 'has_property') のどれかです: "
                                        "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                        "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                        "'makes', 'measures', 'becomes', 'competes', "
                                        "'has_property') のどれかです: 'bogus'",
                    'bogus|-|method': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                      "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                      "'competes', 'has_property') のどれかです: "
                                      "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                      "'quantity', 'part', 'lowers', 'raises', 'affects', 'makes', "
                                      "'measures', 'becomes', 'competes', 'has_property') のどれかです: "
                                      "'bogus'",
                    'bogus|material|-': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                        "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                        "'becomes', 'competes', 'has_property') のどれかです: "
                                        "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                        "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                        "'makes', 'measures', 'becomes', 'competes', "
                                        "'has_property') のどれかです: 'bogus'",
                    'bogus|material|material': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                               "'lowers', 'raises', 'affects', 'makes', "
                                               "'measures', 'becomes', 'competes', 'has_property') "
                                               "のどれかです: 'bogus',mixed,mixed,!ThemeError:関係は "
                                               "('sub', 'eq', 'quantity', 'part', 'lowers', "
                                               "'raises', 'affects', 'makes', 'measures', "
                                               "'becomes', 'competes', 'has_property') のどれかです: "
                                               "'bogus'",
                    'bogus|material|method': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                             "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                             "'becomes', 'competes', 'has_property') のどれかです: "
                                             "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                             "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                             "'makes', 'measures', 'becomes', 'competes', "
                                             "'has_property') のどれかです: 'bogus'",
                    'bogus|method|-': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                      "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                      "'competes', 'has_property') のどれかです: "
                                      "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                      "'quantity', 'part', 'lowers', 'raises', 'affects', 'makes', "
                                      "'measures', 'becomes', 'competes', 'has_property') のどれかです: "
                                      "'bogus'",
                    'bogus|method|material': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                             "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                             "'becomes', 'competes', 'has_property') のどれかです: "
                                             "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                             "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                             "'makes', 'measures', 'becomes', 'competes', "
                                             "'has_property') のどれかです: 'bogus'",
                    'bogus|method|method': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', "
                                           "'lowers', 'raises', 'affects', 'makes', 'measures', "
                                           "'becomes', 'competes', 'has_property') のどれかです: "
                                           "'bogus',mixed,mixed,!ThemeError:関係は ('sub', 'eq', "
                                           "'quantity', 'part', 'lowers', 'raises', 'affects', "
                                           "'makes', 'measures', 'becomes', 'competes', "
                                           "'has_property') のどれかです: 'bogus'",
                    'sub|back': 'cycle,-',
                    'eq|back': '-,-',
                    'quantity|back': 'quantity_target,quantity_target',
                    'part|back': 'cycle,cycle',
                    'lowers|back': 'target_not_property,-',
                    'raises|back': 'target_not_property,-',
                    'affects|back': 'target_not_property,-',
                    'makes|back': 'role_mismatch,mixed',
                    'measures|back': 'role_mismatch,mixed',
                    'becomes|back': '-,mixed',
                    'competes|back': '-,mixed',
                    'has_property|back': 'mixed,mixed',
                    'bogus|back': "!ThemeError:関係は ('sub', 'eq', 'quantity', 'part', 'lowers', "
                                  "'raises', 'affects', 'makes', 'measures', 'becomes', "
                                  "'competes', 'has_property') のどれかです: 'bogus',!ThemeError:関係は "
                                  "('sub', 'eq', 'quantity', 'part', 'lowers', 'raises', "
                                  "'affects', 'makes', 'measures', 'becomes', 'competes', "
                                  "'has_property') のどれかです: 'bogus'"},
 'has_relation': {'sub': [True, False],
                  'eq': [True, True],
                  'quantity': [True, False],
                  'part': [True, False],
                  'lowers': [True, False],
                  'raises': [True, False],
                  'affects': [True, False],
                  'makes': [True, False],
                  'measures': [True, False],
                  'becomes': [True, False],
                  'competes': [True, True],
                  'has_property': [True, False],
                  'bogus': [True, False]},
 'line_shapes': {'equivalentClass': ['-',
                                     '!ThemeError:種類と項目は結べません',
                                     '-',
                                     '-',
                                     '!ThemeError:種類と項目は結べません',
                                     '!ThemeError:種類と項目は結べません',
                                     '!ThemeError:種類と項目は結べません',
                                     '!ThemeError:種類と項目は結べません',
                                     '-',
                                     '!ThemeError:種類と項目は結べません',
                                     '-',
                                     '-',
                                     '-',
                                     '!ThemeError:種類と項目は結べません',
                                     '-',
                                     '-'],
                 'subClassOf': ['-',
                                '!ThemeError:種類と項目は結べません',
                                '-',
                                '-',
                                '!ThemeError:種類と項目は結べません',
                                '!ThemeError:種類と項目は結べません',
                                '!ThemeError:種類と項目は結べません',
                                '!ThemeError:種類と項目は結べません',
                                '-',
                                '!ThemeError:種類と項目は結べません',
                                '-',
                                '-',
                                '-',
                                '!ThemeError:種類と項目は結べません',
                                '-',
                                '-'],
                 'equivalentProperty': ['!ThemeError:種類と項目は結べません',
                                        '!ThemeError:種類と項目は結べません',
                                        '!ThemeError:種類と項目は結べません',
                                        '!ThemeError:種類と項目は結べません',
                                        '!ThemeError:種類と項目は結べません',
                                        '-',
                                        '-',
                                        '-',
                                        '!ThemeError:種類と項目は結べません',
                                        '-',
                                        '-',
                                        '-',
                                        '!ThemeError:種類と項目は結べません',
                                        '-',
                                        '-',
                                        '-'],
                 'subPropertyOf': ['!ThemeError:種類と項目は結べません',
                                   '!ThemeError:種類と項目は結べません',
                                   '!ThemeError:種類と項目は結べません',
                                   '!ThemeError:種類と項目は結べません',
                                   '!ThemeError:種類と項目は結べません',
                                   '-',
                                   '-',
                                   '-',
                                   '!ThemeError:種類と項目は結べません',
                                   '-',
                                   '-',
                                   '-',
                                   '!ThemeError:種類と項目は結べません',
                                   '-',
                                   '-',
                                   '-'],
                 'hasQuantityKind': ['!ThemeError:種類と項目は結べません',
                                     '!ThemeError:種類と項目は結べません',
                                     '!ThemeError:種類と項目は結べません',
                                     '!ThemeError:種類と項目は結べません',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-',
                                     '-'],
                 'isPartOf': ['-',
                              '!ThemeError:種類と項目は結べません',
                              '-',
                              '-',
                              '!ThemeError:種類と項目は結べません',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-',
                              '-'],
                 'lowers': ['!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-',
                            '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-',
                            '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-',
                            '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-'],
                 'raises': ['!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-',
                            '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-',
                            '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-',
                            '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                            '-',
                            '-',
                            '-'],
                 'affects': ['!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                             '-',
                             '-',
                             '-',
                             '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                             '-',
                             '-',
                             '-',
                             '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                             '-',
                             '-',
                             '-',
                             '!ThemeError:下げる・上げる・変えるの相手は、項目だけです',
                             '-',
                             '-',
                             '-'],
                 'makes': ['-',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '-',
                           '-',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '-',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '-',
                           '-',
                           '-',
                           '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                           '-',
                           '-'],
                 'measuresProperty': ['!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '-',
                                      '-',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '-',
                                      '-',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '-',
                                      '-'],
                 'measuresMaterial': ['-',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '-',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '-',
                                      '-',
                                      '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                      '-',
                                      '-'],
                 'becomes': ['-',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '-',
                             '-',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '-',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '-',
                             '-',
                             '-',
                             '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                             '-',
                             '-'],
                 'competesWith': ['-',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '-',
                                  '-',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '-',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '-',
                                  '-',
                                  '-',
                                  '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                  '-',
                                  '-'],
                 'hasProperty': ['!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '-',
                                 '-',
                                 '-',
                                 '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '-',
                                 '-',
                                 '-',
                                 '!ThemeError:この関係の両端は、種類と項目の組が合いません',
                                 '-',
                                 '-',
                                 '-'],
                 'bogus': ["!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'",
                           "!KeyError:'bogus'"]},
 'digest': '7c815ac64af2cc7196ba060f440d9b1503e44fbba160bf2ee8cd6dc9c0b77023'}


def test_the_pinned_tables_are_not_empty() -> None:
    assert set(EXPECTED) == {"consts", "relation_problem", "postprocess", "postprocess_cycle",
                             "check_relation", "has_relation", "line_shapes", "digest"}


@pytest.mark.parametrize(
    "name, compute",
    [
        ("consts", _consts),
        ("relation_problem", _grid_relation_problem),
        ("postprocess", _grid_postprocess),
        ("postprocess_cycle", _grid_postprocess_cycle),
        ("check_relation", _grid_check_relation),
        ("has_relation", _grid_has_relation),
        ("line_shapes", _grid_line_shapes),
        ("digest", _digest),
    ],
)
def test_results_are_the_same_as_before_the_copies_were_derived(name: str, compute) -> None:
    assert compute() == EXPECTED[name]
