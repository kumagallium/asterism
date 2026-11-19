"""R0a: 関係の目録へ置き換える「前」の値の写し（スナップショット）。

main 17c1c520 の実装から機械的に書き出した。目録から導いた今の値が、置き換え前と
同じ値・同じ型・同じ並びであることを見張る。値を変えたくなったら、まず契約を見直す。
"""

from __future__ import annotations

import pytest

from asterism import crosswalk_runtime as cr
from asterism import graph_diff, pair_relations, themes

OLD_ALIGN_RELATIONS = {'equivalentClass': 'http://www.w3.org/2002/07/owl#equivalentClass',
 'subClassOf': 'http://www.w3.org/2000/01/rdf-schema#subClassOf',
 'equivalentProperty': 'http://www.w3.org/2002/07/owl#equivalentProperty',
 'subPropertyOf': 'http://www.w3.org/2000/01/rdf-schema#subPropertyOf',
 'hasQuantityKind': 'http://qudt.org/schema/qudt/hasQuantityKind',
 'isPartOf': 'http://purl.obolibrary.org/obo/BFO_0000050',
 'lowers': 'http://purl.obolibrary.org/obo/RO_0002212',
 'raises': 'http://purl.obolibrary.org/obo/RO_0002213',
 'affects': 'http://purl.obolibrary.org/obo/RO_0002211',
 'makes': 'http://purl.obolibrary.org/obo/RO_0002234',
 'measuresProperty': 'http://purl.obolibrary.org/obo/RO_0009006',
 'measuresMaterial': 'http://www.w3.org/ns/sosa/hasFeatureOfInterest',
 'becomes': 'http://purl.obolibrary.org/obo/RO_0001001',
 'competesWith': 'https://kumagallium.github.io/asterism/vocab/relation#competesWith',
 'hasProperty': 'http://purl.obolibrary.org/obo/RO_0000053'}

OLD_KNOWLEDGE_RELATIONS = ('isPartOf',
 'lowers',
 'raises',
 'affects',
 'makes',
 'measuresProperty',
 'measuresMaterial',
 'becomes',
 'competesWith',
 'hasProperty')

OLD_SYMMETRIC_RELATIONS = ['competesWith']

OLD_NO_SELF_RELATIONS = [
    'becomes', 'competesWith', 'hasProperty', 'makes', 'measuresMaterial', 'measuresProperty',
]

OLD_ROLES = ('material', 'method', 'concept', 'other')

OLD_ROLES_JA = {'material': '材料', 'method': '手法', 'concept': '概念', 'other': 'そのほか'}

OLD_ROLE_RULES = {'makes': (['concept', 'method', 'other'], ['material']),
 'measuresProperty': (['concept', 'method', 'other'], []),
 'measuresMaterial': (['concept', 'method', 'other'], ['material']),
 'becomes': (['material'], ['material']),
 'competesWith': (['material'], ['material']),
 'hasProperty': (['material'], [])}

OLD_OWN_RELATIONS = {'competesWith': {'label_ja': '競合する',
                  'label_en': 'competes with',
                  'definition': 'Two materials (phases) compete: under the same conditions, the '
                                'formation of one makes the other less likely to form. Symmetric.',
                  'searched': ['RO (release 2026-09-04)',
                               'BFO',
                               'SOSA / SSN',
                               'OBI',
                               'IAO',
                               'CHMO',
                               'SIO',
                               'PATO',
                               'ENVO',
                               'OLS4 (all ontologies; only schema.org competitor, a sports term)'],
                  'date': '2026-11-17'}}

OLD_SUBSUMPTION = ('subClassOf', 'subPropertyOf', 'isPartOf')

OLD_RELATION_SHAPES = {'subClassOf': (['class'], ['class']),
 'equivalentClass': (['class'], ['class']),
 'subPropertyOf': (['property'], ['property']),
 'equivalentProperty': (['property'], ['property']),
 'hasQuantityKind': (['property'], []),
 'isPartOf': (['class', 'property'], ['class', 'property']),
 'lowers': (['class', 'property'], ['property']),
 'raises': (['class', 'property'], ['property']),
 'affects': (['class', 'property'], ['property']),
 'makes': (['class'], ['class']),
 'measuresProperty': (['class'], ['property']),
 'measuresMaterial': (['class'], ['class']),
 'becomes': (['class'], ['class']),
 'competesWith': (['class'], ['class']),
 'hasProperty': (['class'], ['property'])}

OLD_SAME_SHAPE_RELATIONS = ['isPartOf']

OLD_RELATION_KINDS = ('sub',
 'eq',
 'quantity',
 'part',
 'lowers',
 'raises',
 'affects',
 'makes',
 'measures',
 'becomes',
 'competes',
 'has_property')

OLD_EXPERIMENT_RELATIONS = ('makes', 'measures', 'becomes', 'competes', 'has_property')

OLD_SYMMETRIC_DRAFT_RELATIONS = ('competes',)

OLD_PRED_TO_REL = {'subClassOf': 'sub',
 'subPropertyOf': 'sub',
 'equivalentClass': 'eq',
 'equivalentProperty': 'eq',
 'hasQuantityKind': 'quantity',
 'isPartOf': 'part',
 'lowers': 'lowers',
 'raises': 'raises',
 'affects': 'affects',
 'makes': 'makes',
 'measuresProperty': 'measures',
 'measuresMaterial': 'measures',
 'becomes': 'becomes',
 'competesWith': 'competes',
 'hasProperty': 'has_property'}

OLD_REL_SENTENCE = {'ja': {'sub': '{s} は {o} の一種',
        'eq': '{s} と {o} は同じもの',
        'quantity': '{s} の量は {o}',
        'part': '{s} は {o} の一部',
        'lowers': '{s} は {o} を下げる',
        'raises': '{s} は {o} を上げる',
        'affects': '{s} は {o} を変える',
        'makes': '{s} で {o} を作る',
        'measures': '{s} で {o} を測る',
        'becomes': '{s} が {o} に変わる',
        'competes': '{s} と {o} は競合する',
        'has_property': '{s} の {o}'},
 'en': {'sub': '{s} is a kind of {o}',
        'eq': '{s} is the same as {o}',
        'quantity': 'The quantity of {s} is {o}',
        'part': '{s} is a part of {o}',
        'lowers': '{s} lowers {o}',
        'raises': '{s} raises {o}',
        'affects': '{s} changes {o}',
        'makes': '{s} makes {o}',
        'measures': '{s} measures {o}',
        'becomes': '{s} turns into {o}',
        'competes': '{s} competes with {o}',
        'has_property': '{s} has the property {o}'}}

OLD_PAIR_RELATIONS = ('sub',
 'eq',
 'part',
 'lowers',
 'raises',
 'affects',
 'makes',
 'measures',
 'becomes',
 'competes',
 'has_property')

OLD_SYMMETRIC_PAIR_RELATIONS = ('eq', 'competes')

_BOGUS_MSG = f"関係は {OLD_RELATION_KINDS} のどれかです: 'bogus'"
_MEASURES_MSG = "「測る」は、相手が項目か種類かが分からないと決められません"

OLD_RELATION_PREDICATE = {
    ('sub', 'class', 'class'): ('ok', 'subClassOf'),
    ('sub', 'class', 'property'): ('ok', 'subClassOf'),
    ('sub', 'class', None): ('ok', 'subClassOf'),
    ('sub', 'class', 'other'): ('ok', 'subClassOf'),
    ('sub', 'property', 'class'): ('ok', 'subPropertyOf'),
    ('sub', 'property', 'property'): ('ok', 'subPropertyOf'),
    ('sub', 'property', None): ('ok', 'subPropertyOf'),
    ('sub', 'property', 'other'): ('ok', 'subPropertyOf'),
    ('sub', None, 'class'): ('ok', 'subPropertyOf'),
    ('sub', None, 'property'): ('ok', 'subPropertyOf'),
    ('sub', None, None): ('ok', 'subPropertyOf'),
    ('sub', None, 'other'): ('ok', 'subPropertyOf'),
    ('sub', 'other', 'class'): ('ok', 'subPropertyOf'),
    ('sub', 'other', 'property'): ('ok', 'subPropertyOf'),
    ('sub', 'other', None): ('ok', 'subPropertyOf'),
    ('sub', 'other', 'other'): ('ok', 'subPropertyOf'),
    ('eq', 'class', 'class'): ('ok', 'equivalentClass'),
    ('eq', 'class', 'property'): ('ok', 'equivalentClass'),
    ('eq', 'class', None): ('ok', 'equivalentClass'),
    ('eq', 'class', 'other'): ('ok', 'equivalentClass'),
    ('eq', 'property', 'class'): ('ok', 'equivalentProperty'),
    ('eq', 'property', 'property'): ('ok', 'equivalentProperty'),
    ('eq', 'property', None): ('ok', 'equivalentProperty'),
    ('eq', 'property', 'other'): ('ok', 'equivalentProperty'),
    ('eq', None, 'class'): ('ok', 'equivalentProperty'),
    ('eq', None, 'property'): ('ok', 'equivalentProperty'),
    ('eq', None, None): ('ok', 'equivalentProperty'),
    ('eq', None, 'other'): ('ok', 'equivalentProperty'),
    ('eq', 'other', 'class'): ('ok', 'equivalentProperty'),
    ('eq', 'other', 'property'): ('ok', 'equivalentProperty'),
    ('eq', 'other', None): ('ok', 'equivalentProperty'),
    ('eq', 'other', 'other'): ('ok', 'equivalentProperty'),
    ('quantity', 'class', 'class'): ('ok', 'hasQuantityKind'),
    ('quantity', 'class', 'property'): ('ok', 'hasQuantityKind'),
    ('quantity', 'class', None): ('ok', 'hasQuantityKind'),
    ('quantity', 'class', 'other'): ('ok', 'hasQuantityKind'),
    ('quantity', 'property', 'class'): ('ok', 'hasQuantityKind'),
    ('quantity', 'property', 'property'): ('ok', 'hasQuantityKind'),
    ('quantity', 'property', None): ('ok', 'hasQuantityKind'),
    ('quantity', 'property', 'other'): ('ok', 'hasQuantityKind'),
    ('quantity', None, 'class'): ('ok', 'hasQuantityKind'),
    ('quantity', None, 'property'): ('ok', 'hasQuantityKind'),
    ('quantity', None, None): ('ok', 'hasQuantityKind'),
    ('quantity', None, 'other'): ('ok', 'hasQuantityKind'),
    ('quantity', 'other', 'class'): ('ok', 'hasQuantityKind'),
    ('quantity', 'other', 'property'): ('ok', 'hasQuantityKind'),
    ('quantity', 'other', None): ('ok', 'hasQuantityKind'),
    ('quantity', 'other', 'other'): ('ok', 'hasQuantityKind'),
    ('part', 'class', 'class'): ('ok', 'isPartOf'),
    ('part', 'class', 'property'): ('ok', 'isPartOf'),
    ('part', 'class', None): ('ok', 'isPartOf'),
    ('part', 'class', 'other'): ('ok', 'isPartOf'),
    ('part', 'property', 'class'): ('ok', 'isPartOf'),
    ('part', 'property', 'property'): ('ok', 'isPartOf'),
    ('part', 'property', None): ('ok', 'isPartOf'),
    ('part', 'property', 'other'): ('ok', 'isPartOf'),
    ('part', None, 'class'): ('ok', 'isPartOf'),
    ('part', None, 'property'): ('ok', 'isPartOf'),
    ('part', None, None): ('ok', 'isPartOf'),
    ('part', None, 'other'): ('ok', 'isPartOf'),
    ('part', 'other', 'class'): ('ok', 'isPartOf'),
    ('part', 'other', 'property'): ('ok', 'isPartOf'),
    ('part', 'other', None): ('ok', 'isPartOf'),
    ('part', 'other', 'other'): ('ok', 'isPartOf'),
    ('lowers', 'class', 'class'): ('ok', 'lowers'),
    ('lowers', 'class', 'property'): ('ok', 'lowers'),
    ('lowers', 'class', None): ('ok', 'lowers'),
    ('lowers', 'class', 'other'): ('ok', 'lowers'),
    ('lowers', 'property', 'class'): ('ok', 'lowers'),
    ('lowers', 'property', 'property'): ('ok', 'lowers'),
    ('lowers', 'property', None): ('ok', 'lowers'),
    ('lowers', 'property', 'other'): ('ok', 'lowers'),
    ('lowers', None, 'class'): ('ok', 'lowers'),
    ('lowers', None, 'property'): ('ok', 'lowers'),
    ('lowers', None, None): ('ok', 'lowers'),
    ('lowers', None, 'other'): ('ok', 'lowers'),
    ('lowers', 'other', 'class'): ('ok', 'lowers'),
    ('lowers', 'other', 'property'): ('ok', 'lowers'),
    ('lowers', 'other', None): ('ok', 'lowers'),
    ('lowers', 'other', 'other'): ('ok', 'lowers'),
    ('raises', 'class', 'class'): ('ok', 'raises'),
    ('raises', 'class', 'property'): ('ok', 'raises'),
    ('raises', 'class', None): ('ok', 'raises'),
    ('raises', 'class', 'other'): ('ok', 'raises'),
    ('raises', 'property', 'class'): ('ok', 'raises'),
    ('raises', 'property', 'property'): ('ok', 'raises'),
    ('raises', 'property', None): ('ok', 'raises'),
    ('raises', 'property', 'other'): ('ok', 'raises'),
    ('raises', None, 'class'): ('ok', 'raises'),
    ('raises', None, 'property'): ('ok', 'raises'),
    ('raises', None, None): ('ok', 'raises'),
    ('raises', None, 'other'): ('ok', 'raises'),
    ('raises', 'other', 'class'): ('ok', 'raises'),
    ('raises', 'other', 'property'): ('ok', 'raises'),
    ('raises', 'other', None): ('ok', 'raises'),
    ('raises', 'other', 'other'): ('ok', 'raises'),
    ('affects', 'class', 'class'): ('ok', 'affects'),
    ('affects', 'class', 'property'): ('ok', 'affects'),
    ('affects', 'class', None): ('ok', 'affects'),
    ('affects', 'class', 'other'): ('ok', 'affects'),
    ('affects', 'property', 'class'): ('ok', 'affects'),
    ('affects', 'property', 'property'): ('ok', 'affects'),
    ('affects', 'property', None): ('ok', 'affects'),
    ('affects', 'property', 'other'): ('ok', 'affects'),
    ('affects', None, 'class'): ('ok', 'affects'),
    ('affects', None, 'property'): ('ok', 'affects'),
    ('affects', None, None): ('ok', 'affects'),
    ('affects', None, 'other'): ('ok', 'affects'),
    ('affects', 'other', 'class'): ('ok', 'affects'),
    ('affects', 'other', 'property'): ('ok', 'affects'),
    ('affects', 'other', None): ('ok', 'affects'),
    ('affects', 'other', 'other'): ('ok', 'affects'),
    ('makes', 'class', 'class'): ('ok', 'makes'),
    ('makes', 'class', 'property'): ('ok', 'makes'),
    ('makes', 'class', None): ('ok', 'makes'),
    ('makes', 'class', 'other'): ('ok', 'makes'),
    ('makes', 'property', 'class'): ('ok', 'makes'),
    ('makes', 'property', 'property'): ('ok', 'makes'),
    ('makes', 'property', None): ('ok', 'makes'),
    ('makes', 'property', 'other'): ('ok', 'makes'),
    ('makes', None, 'class'): ('ok', 'makes'),
    ('makes', None, 'property'): ('ok', 'makes'),
    ('makes', None, None): ('ok', 'makes'),
    ('makes', None, 'other'): ('ok', 'makes'),
    ('makes', 'other', 'class'): ('ok', 'makes'),
    ('makes', 'other', 'property'): ('ok', 'makes'),
    ('makes', 'other', None): ('ok', 'makes'),
    ('makes', 'other', 'other'): ('ok', 'makes'),
    ('measures', 'class', 'class'): ('ok', 'measuresMaterial'),
    ('measures', 'class', 'property'): ('ok', 'measuresProperty'),
    ('measures', 'class', None): ('err', 'ThemeError', _MEASURES_MSG),
    ('measures', 'class', 'other'): ('ok', 'measuresMaterial'),
    ('measures', 'property', 'class'): ('ok', 'measuresMaterial'),
    ('measures', 'property', 'property'): ('ok', 'measuresProperty'),
    ('measures', 'property', None): ('err', 'ThemeError', _MEASURES_MSG),
    ('measures', 'property', 'other'): ('ok', 'measuresMaterial'),
    ('measures', None, 'class'): ('ok', 'measuresMaterial'),
    ('measures', None, 'property'): ('ok', 'measuresProperty'),
    ('measures', None, None): ('err', 'ThemeError', _MEASURES_MSG),
    ('measures', None, 'other'): ('ok', 'measuresMaterial'),
    ('measures', 'other', 'class'): ('ok', 'measuresMaterial'),
    ('measures', 'other', 'property'): ('ok', 'measuresProperty'),
    ('measures', 'other', None): ('err', 'ThemeError', _MEASURES_MSG),
    ('measures', 'other', 'other'): ('ok', 'measuresMaterial'),
    ('becomes', 'class', 'class'): ('ok', 'becomes'),
    ('becomes', 'class', 'property'): ('ok', 'becomes'),
    ('becomes', 'class', None): ('ok', 'becomes'),
    ('becomes', 'class', 'other'): ('ok', 'becomes'),
    ('becomes', 'property', 'class'): ('ok', 'becomes'),
    ('becomes', 'property', 'property'): ('ok', 'becomes'),
    ('becomes', 'property', None): ('ok', 'becomes'),
    ('becomes', 'property', 'other'): ('ok', 'becomes'),
    ('becomes', None, 'class'): ('ok', 'becomes'),
    ('becomes', None, 'property'): ('ok', 'becomes'),
    ('becomes', None, None): ('ok', 'becomes'),
    ('becomes', None, 'other'): ('ok', 'becomes'),
    ('becomes', 'other', 'class'): ('ok', 'becomes'),
    ('becomes', 'other', 'property'): ('ok', 'becomes'),
    ('becomes', 'other', None): ('ok', 'becomes'),
    ('becomes', 'other', 'other'): ('ok', 'becomes'),
    ('competes', 'class', 'class'): ('ok', 'competesWith'),
    ('competes', 'class', 'property'): ('ok', 'competesWith'),
    ('competes', 'class', None): ('ok', 'competesWith'),
    ('competes', 'class', 'other'): ('ok', 'competesWith'),
    ('competes', 'property', 'class'): ('ok', 'competesWith'),
    ('competes', 'property', 'property'): ('ok', 'competesWith'),
    ('competes', 'property', None): ('ok', 'competesWith'),
    ('competes', 'property', 'other'): ('ok', 'competesWith'),
    ('competes', None, 'class'): ('ok', 'competesWith'),
    ('competes', None, 'property'): ('ok', 'competesWith'),
    ('competes', None, None): ('ok', 'competesWith'),
    ('competes', None, 'other'): ('ok', 'competesWith'),
    ('competes', 'other', 'class'): ('ok', 'competesWith'),
    ('competes', 'other', 'property'): ('ok', 'competesWith'),
    ('competes', 'other', None): ('ok', 'competesWith'),
    ('competes', 'other', 'other'): ('ok', 'competesWith'),
    ('has_property', 'class', 'class'): ('ok', 'hasProperty'),
    ('has_property', 'class', 'property'): ('ok', 'hasProperty'),
    ('has_property', 'class', None): ('ok', 'hasProperty'),
    ('has_property', 'class', 'other'): ('ok', 'hasProperty'),
    ('has_property', 'property', 'class'): ('ok', 'hasProperty'),
    ('has_property', 'property', 'property'): ('ok', 'hasProperty'),
    ('has_property', 'property', None): ('ok', 'hasProperty'),
    ('has_property', 'property', 'other'): ('ok', 'hasProperty'),
    ('has_property', None, 'class'): ('ok', 'hasProperty'),
    ('has_property', None, 'property'): ('ok', 'hasProperty'),
    ('has_property', None, None): ('ok', 'hasProperty'),
    ('has_property', None, 'other'): ('ok', 'hasProperty'),
    ('has_property', 'other', 'class'): ('ok', 'hasProperty'),
    ('has_property', 'other', 'property'): ('ok', 'hasProperty'),
    ('has_property', 'other', None): ('ok', 'hasProperty'),
    ('has_property', 'other', 'other'): ('ok', 'hasProperty'),
    ('bogus', 'class', 'class'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'class', 'property'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'class', None): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'class', 'other'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'property', 'class'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'property', 'property'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'property', None): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'property', 'other'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', None, 'class'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', None, 'property'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', None, None): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', None, 'other'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'other', 'class'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'other', 'property'): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'other', None): ('err', 'ThemeError', _BOGUS_MSG),
    ('bogus', 'other', 'other'): ('err', 'ThemeError', _BOGUS_MSG),
}


def test_the_old_values_are_kept() -> None:
    assert cr.ALIGN_RELATIONS == OLD_ALIGN_RELATIONS
    assert list(cr.ALIGN_RELATIONS) == list(OLD_ALIGN_RELATIONS)
    assert type(cr.ALIGN_RELATIONS) is dict
    assert cr.KNOWLEDGE_RELATIONS == OLD_KNOWLEDGE_RELATIONS
    assert type(cr.KNOWLEDGE_RELATIONS) is tuple
    assert type(cr.SYMMETRIC_RELATIONS) is frozenset
    assert sorted(cr.SYMMETRIC_RELATIONS) == OLD_SYMMETRIC_RELATIONS
    assert type(cr.NO_SELF_RELATIONS) is frozenset
    assert sorted(cr.NO_SELF_RELATIONS) == OLD_NO_SELF_RELATIONS
    assert cr.ROLES == OLD_ROLES
    assert cr.ROLES_JA == OLD_ROLES_JA
    assert cr.OWN_RELATIONS == OLD_OWN_RELATIONS
    assert list(cr.OWN_RELATIONS) == list(OLD_OWN_RELATIONS)
    assert cr._SUBSUMPTION == OLD_SUBSUMPTION
    assert type(cr._SUBSUMPTION) is tuple
    assert type(cr.SAME_SHAPE_RELATIONS) is frozenset
    assert sorted(cr.SAME_SHAPE_RELATIONS) == OLD_SAME_SHAPE_RELATIONS


def _frozen_pairs(d: dict) -> dict:
    for v in d.values():
        assert type(v) is tuple and all(type(x) is frozenset for x in v)
    return {k: (sorted(v[0]), sorted(v[1])) for k, v in d.items()}


def test_the_old_shapes_and_roles_are_kept() -> None:
    assert _frozen_pairs(cr.RELATION_SHAPES) == OLD_RELATION_SHAPES
    assert list(cr.RELATION_SHAPES) == list(OLD_RELATION_SHAPES)
    assert _frozen_pairs(cr.ROLE_RULES) == OLD_ROLE_RULES
    assert list(cr.ROLE_RULES) == list(OLD_ROLE_RULES)


def test_the_old_draft_side_values_are_kept() -> None:
    assert themes.RELATION_KINDS == OLD_RELATION_KINDS and type(themes.RELATION_KINDS) is tuple
    assert themes.EXPERIMENT_RELATIONS == OLD_EXPERIMENT_RELATIONS
    assert type(themes.EXPERIMENT_RELATIONS) is tuple
    assert themes.SYMMETRIC_DRAFT_RELATIONS == OLD_SYMMETRIC_DRAFT_RELATIONS
    assert type(themes.SYMMETRIC_DRAFT_RELATIONS) is tuple
    assert pair_relations.PAIR_RELATIONS == OLD_PAIR_RELATIONS
    assert type(pair_relations.PAIR_RELATIONS) is tuple
    assert pair_relations.SYMMETRIC_PAIR_RELATIONS == OLD_SYMMETRIC_PAIR_RELATIONS
    assert type(pair_relations.SYMMETRIC_PAIR_RELATIONS) is tuple


def test_the_old_graph_diff_tables_are_kept() -> None:
    assert graph_diff.PRED_TO_REL == OLD_PRED_TO_REL
    assert list(graph_diff.PRED_TO_REL) == list(OLD_PRED_TO_REL)
    assert graph_diff.REL_SENTENCE == OLD_REL_SENTENCE
    for lang in OLD_REL_SENTENCE:
        assert list(graph_diff.REL_SENTENCE[lang]) == list(OLD_REL_SENTENCE[lang])
    assert list(graph_diff.REL_SENTENCE) == list(OLD_REL_SENTENCE)


@pytest.mark.parametrize("key", list(OLD_RELATION_PREDICATE))
def test_relation_predicate_gives_the_same_result_for_every_combination(key) -> None:
    rel, kind, to_kind = key
    want = OLD_RELATION_PREDICATE[key]
    if want[0] == "ok":
        assert themes.relation_predicate(rel, kind, to_kind) == want[1]
        return
    with pytest.raises(Exception) as ei:
        themes.relation_predicate(rel, kind, to_kind)
    assert type(ei.value).__name__ == want[1]
    assert str(ei.value) == want[2]
