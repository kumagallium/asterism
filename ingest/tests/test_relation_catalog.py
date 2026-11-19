"""関係の目録（``asterism.relation_catalog``）そのものの検査。

置き換え前の値との一致は ``test_relation_catalog_snapshot.py``、写しとの同期は
``test_relation_catalog_sync.py``。ここでは目録の形と、導いた値の基本を見る。
"""

from __future__ import annotations

import json

import pytest

from asterism import crosswalk_runtime as cr
from asterism import relation_catalog as rc


def test_counts_and_order() -> None:
    assert len(rc.RELATIONS) == 12
    assert len(rc.PREDICATES) == 15
    assert rc.relation_kinds()[0] == "sub" and rc.relation_kinds()[-1] == "has_property"
    assert len({r.name for r in rc.RELATIONS}) == 12
    assert len({p.name for p in rc.PREDICATES}) == 15


def test_every_rule_points_at_a_predicate_and_every_predicate_is_covered() -> None:
    names = {p.name: p.iri for p in rc.PREDICATES}
    used: list[str] = []
    for r in rc.RELATIONS:
        assert r.builtin is True
        assert set(r.label) == {"ja", "en"} and set(r.sentence) == {"ja", "en"}
        for lang in ("ja", "en"):
            assert "{s}" in r.sentence[lang] and "{o}" in r.sentence[lang]
        for rule in r.predicates:
            assert names[rule.name] == rule.iri
            used.append(rule.name)
        if len(r.predicates) == 2:
            assert all(rule.when is not None for rule in r.predicates)
        else:
            assert r.predicates[0].when is None
    assert sorted(used) == sorted(names)  # 1 つの述語は 1 つの関係にだけ属する


def test_measures_splits_in_two_predicates() -> None:
    m = rc.relation("measures")
    assert [(p.name, p.when) for p in m.predicates] == [
        ("measuresProperty", ("to_kind", "property")),
        ("measuresMaterial", ("to_kind", "class")),
    ]
    assert rc.resolve_predicate("measures", "class", "property") == "measuresProperty"
    assert rc.resolve_predicate("measures", "class", "class") == "measuresMaterial"
    assert rc.resolve_predicate("measures", "class", None) is None
    with pytest.raises(KeyError):
        rc.resolve_predicate("nope", "class")


def test_derived_flags() -> None:
    assert rc.symmetric_draft_relations() == ("competes",)
    assert rc.symmetric_pair_relations() == ("eq", "competes")
    assert rc.symmetric_predicates() == frozenset({"competesWith"})
    assert rc.experiment_relations() == ("makes", "measures", "becomes", "competes", "has_property")
    assert [p.name for p in rc.PREDICATES if p.own] == ["competesWith"]


def test_the_crosswalk_runtime_names_are_the_catalog_values() -> None:
    assert rc.align_relations() == cr.ALIGN_RELATIONS
    assert cr.REL == rc.REL_NS
    assert rc.own_relations() == cr.OWN_RELATIONS


def test_catalog_json_shape() -> None:
    body = rc.catalog_to_json()
    assert list(body) == ["relations", "predicates"]
    json.dumps(body)  # JSON にできる
    assert len(body["relations"]) == 12 and len(body["predicates"]) == 15
    rel0 = body["relations"][0]
    assert set(rel0) == {
        "name", "label", "sentence", "predicates",
        "symmetric_draft", "symmetric_pair", "experiment", "builtin",
        # R0b で足した欄（既存の欄は変えていない）
        "to_property_only", "same_kind", "acyclic", "factor_directions", "question_op",
        "reserve_slug", "paper",
    }  # fmt: skip
    pred = {p["name"]: p for p in body["predicates"]}
    assert set(pred["becomes"]) == {
        "name", "iri", "shapes", "same_shape", "roles",
        "no_self", "subsumption", "symmetric", "knowledge", "own", "cycle_rels",
    }  # fmt: skip
    assert pred["becomes"]["roles"] == {"from": ["material"], "to": ["material"]}
    assert pred["hasQuantityKind"]["roles"] is None
    assert pred["hasQuantityKind"]["shapes"] == {"from": ["property"], "to": []}
    assert pred["competesWith"]["own"]["label"] == {"ja": "競合する", "en": "competes with"}
