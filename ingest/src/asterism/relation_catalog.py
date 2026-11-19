"""関係の目録（組み込み）— 関係の定義を置く、コードの中の 1 か所。

design.md §23.13・ADR ``docs/architecture/relation-catalog.md``。

2 つの表を持つ。

- :data:`RELATIONS` — 下書き側の短い名前（``sub`` ``eq`` …）1 つにつき 1 件。表示名・文の型・
  向きの有無・確定側の述語の決め方（:class:`PredicateRule`）。
- :data:`PREDICATES` — 確定側の述語（``subClassOf`` ``competesWith`` …）1 つにつき 1 件。
  端の許す種別・役割・自分自身への線・循環・向き・自前の記録（:class:`Predicate`）。

``themes`` / ``graph_diff`` / ``crosswalk_runtime`` / ``pair_relations`` の正の定義は、
ここから導く（公開名は各モジュールに残す）。

このモジュールは**葉**: ``asterism`` のほかのモジュールを import しない（循環を作らない）。
例外（``ThemeError`` など）は呼び出し側が作る。向きの有無は 3 層で中身が違い
（下書き＝``competes`` だけ／台帳と提案＝``eq`` と ``competes``／確定＝``competesWith`` だけ）、
揃えると振る舞いが変わるので欄を分けて今の値を保つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 自前の関係の述語の名前空間（関係の述語だけ・利用者裁定 2026-11-17）。
REL_NS = "https://kumagallium.github.io/asterism/vocab/relation#"

_OWL = "http://www.w3.org/2002/07/owl#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_OBO = "http://purl.obolibrary.org/obo/"

_NONE: frozenset[str] = frozenset()  # 空 = その端は検査しない
_CLASS = frozenset({"class"})
_PROPERTY = frozenset({"property"})
_EITHER = frozenset({"class", "property"})
_MATERIAL = frozenset({"material"})
_NOT_MATERIAL = frozenset({"method", "concept", "other"})


@dataclass(frozen=True)
class OwnRecord:
    """自前の述語の記録（ADR relations-for-experiments.md §3）。"""

    label: dict[str, str]
    definition: str
    searched: tuple[str, ...]
    date: str


@dataclass(frozen=True)
class Predicate:
    """確定側の述語 1 つ。"""

    name: str  # camelCase（ストアの線の ``relation``）
    iri: str
    shapes: tuple[frozenset[str], frozenset[str]]  # (from, to) の許す種別。空 = 検査しない
    same_shape: bool = False  # 両端が同じ種別であることも求める
    # (from, to) の許す役割。None = 検査しない
    roles: tuple[frozenset[str], frozenset[str]] | None = None
    no_self: bool = False  # 自分自身に結ぶのを拒む
    subsumption: bool = False  # 循環を拒む（上へ辿って自分に戻る線）
    symmetric: bool = False  # 向きが無い（確定側）
    knowledge: bool = False  # 人が確定する「知識の関係」
    own: OwnRecord | None = None  # 自前の述語の記録（標準が無かったものだけ）


#: ``when`` = ``(「from_kind」か「to_kind」, 「class」か「property」)``。``None`` なら無条件。
When = tuple[str, str] | None


@dataclass(frozen=True)
class PredicateRule:
    """下書き側の関係が、確定側のどの述語になるか。"""

    name: str  # 確定側の camelCase
    iri: str
    when: When = None


@dataclass(frozen=True)
class Relation:
    """下書き側の関係 1 つ（主キーは短い名前）。"""

    name: str
    label: dict[str, str]
    sentence: dict[str, str]
    predicates: tuple[PredicateRule, ...]
    symmetric_draft: bool = False  # 下書き側で向きが無い
    symmetric_pair: bool = False  # 台帳・提案で向きが無い
    experiment: bool = False  # 実験の記録の関係
    builtin: bool = True


# ---------------------------------------------------------------------------
# 確定側の述語（並びは ``ALIGN_RELATIONS`` と同じ）
# ---------------------------------------------------------------------------

_COMPETES_OWN = OwnRecord(
    label={"ja": "競合する", "en": "competes with"},
    definition=(
        "Two materials (phases) compete: under the same conditions, the formation of one "
        "makes the other less likely to form. Symmetric."
    ),
    searched=(
        "RO (release 2026-09-04)",
        "BFO",
        "SOSA / SSN",
        "OBI",
        "IAO",
        "CHMO",
        "SIO",
        "PATO",
        "ENVO",
        "OLS4 (all ontologies; only schema.org competitor, a sports term)",
    ),
    date="2026-11-17",
)

PREDICATES: tuple[Predicate, ...] = (
    Predicate("equivalentClass", f"{_OWL}equivalentClass", (_CLASS, _CLASS)),
    Predicate("subClassOf", f"{_RDFS}subClassOf", (_CLASS, _CLASS), subsumption=True),
    Predicate("equivalentProperty", f"{_OWL}equivalentProperty", (_PROPERTY, _PROPERTY)),
    Predicate("subPropertyOf", f"{_RDFS}subPropertyOf", (_PROPERTY, _PROPERTY), subsumption=True),
    Predicate("hasQuantityKind", "http://qudt.org/schema/qudt/hasQuantityKind", (_PROPERTY, _NONE)),
    # 知識の関係（問いから育てる・design.md §15.2）。自前の述語は作らず標準を使う。
    # BFO の part of と RO の regulates 系。
    Predicate(
        "isPartOf",
        f"{_OBO}BFO_0000050",
        (_EITHER, _EITHER),
        same_shape=True,
        subsumption=True,
        knowledge=True,
    ),
    # negatively regulates
    Predicate("lowers", f"{_OBO}RO_0002212", (_EITHER, _PROPERTY), knowledge=True),
    # positively regulates
    Predicate("raises", f"{_OBO}RO_0002213", (_EITHER, _PROPERTY), knowledge=True),
    Predicate("affects", f"{_OBO}RO_0002211", (_EITHER, _PROPERTY), knowledge=True),  # regulates
    # 実験の記録の関係（14a-1・design.md §23）。標準の述語を使い、自前は competesWith の 1 つだけ。
    Predicate(  # has output（手法 → 材料）
        "makes", f"{_OBO}RO_0002234", (_CLASS, _CLASS),
        roles=(_NOT_MATERIAL, _MATERIAL), no_self=True, knowledge=True,
    ),
    Predicate(  # assay measures characteristic
        "measuresProperty", f"{_OBO}RO_0009006", (_CLASS, _PROPERTY),
        roles=(_NOT_MATERIAL, _NONE), no_self=True, knowledge=True,
    ),
    Predicate(
        "measuresMaterial", "http://www.w3.org/ns/sosa/hasFeatureOfInterest", (_CLASS, _CLASS),
        roles=(_NOT_MATERIAL, _MATERIAL), no_self=True, knowledge=True,
    ),
    Predicate(  # derives into（材料 → 材料）
        "becomes", f"{_OBO}RO_0001001", (_CLASS, _CLASS),
        roles=(_MATERIAL, _MATERIAL), no_self=True, knowledge=True,
    ),
    Predicate(  # 向きなし・自前（標準に無い）
        "competesWith", f"{REL_NS}competesWith", (_CLASS, _CLASS),
        roles=(_MATERIAL, _MATERIAL), no_self=True, symmetric=True, knowledge=True,
        own=_COMPETES_OWN,
    ),
    Predicate(  # has characteristic
        "hasProperty", f"{_OBO}RO_0000053", (_CLASS, _PROPERTY),
        roles=(_MATERIAL, _NONE), no_self=True, knowledge=True,
    ),
)  # fmt: skip

# ---------------------------------------------------------------------------
# 下書き側の関係（並びは ``themes.RELATION_KINDS`` と同じ）
# ---------------------------------------------------------------------------

_P = PredicateRule


def _rel(
    name: str,
    ja: str,
    en: str,
    s_ja: str,
    s_en: str,
    predicates: tuple[PredicateRule, ...],
    **flags: bool,
) -> Relation:
    return Relation(
        name=name,
        label={"ja": ja, "en": en},
        sentence={"ja": s_ja, "en": s_en},
        predicates=predicates,
        **flags,
    )


def _iri(name: str) -> str:
    return next(p.iri for p in PREDICATES if p.name == name)


def _one(name: str) -> tuple[PredicateRule, ...]:
    return (_P(name, _iri(name)),)


RELATIONS: tuple[Relation, ...] = (
    _rel(
        "sub", "一種", "kind of", "{s} は {o} の一種", "{s} is a kind of {o}",
        (
            _P("subClassOf", _iri("subClassOf"), ("from_kind", "class")),
            _P("subPropertyOf", _iri("subPropertyOf"), ("from_kind", "property")),
        ),
    ),
    _rel(
        "eq", "同じ", "same as", "{s} と {o} は同じもの", "{s} is the same as {o}",
        (
            _P("equivalentClass", _iri("equivalentClass"), ("from_kind", "class")),
            _P("equivalentProperty", _iri("equivalentProperty"), ("from_kind", "property")),
        ),
        symmetric_pair=True,
    ),
    _rel(
        "quantity", "量", "quantity", "{s} の量は {o}", "The quantity of {s} is {o}",
        _one("hasQuantityKind"),
    ),
    _rel("part", "一部", "part of", "{s} は {o} の一部", "{s} is a part of {o}", _one("isPartOf")),
    _rel("lowers", "下げる", "lowers", "{s} は {o} を下げる", "{s} lowers {o}", _one("lowers")),
    _rel("raises", "上げる", "raises", "{s} は {o} を上げる", "{s} raises {o}", _one("raises")),
    _rel("affects", "変える", "changes", "{s} は {o} を変える", "{s} changes {o}", _one("affects")),
    _rel(
        "makes", "作る", "makes", "{s} で {o} を作る", "{s} makes {o}", _one("makes"),
        experiment=True,
    ),
    _rel(
        "measures", "測る", "measures", "{s} で {o} を測る", "{s} measures {o}",
        (
            _P("measuresProperty", _iri("measuresProperty"), ("to_kind", "property")),
            _P("measuresMaterial", _iri("measuresMaterial"), ("to_kind", "class")),
        ),
        experiment=True,
    ),
    _rel(
        "becomes", "変わる", "turns into", "{s} が {o} に変わる", "{s} turns into {o}",
        _one("becomes"), experiment=True,
    ),
    _rel(
        "competes", "競合する", "competes with", "{s} と {o} は競合する", "{s} competes with {o}",
        _one("competesWith"), symmetric_draft=True, symmetric_pair=True, experiment=True,
    ),
    _rel(
        "has_property", "物性", "has property", "{s} の {o}", "{s} has the property {o}",
        _one("hasProperty"), experiment=True,
    ),
)  # fmt: skip

_BY_NAME = {r.name: r for r in RELATIONS}
_PRED_BY_NAME = {p.name: p for p in PREDICATES}


def relation(name: str) -> Relation:
    """下書きの関係を名前で引く。無ければ ``KeyError``。"""
    return _BY_NAME[name]


def predicate(name: str) -> Predicate:
    """確定側の述語を名前で引く。無ければ ``KeyError``。"""
    return _PRED_BY_NAME[name]


def resolve_predicate(rel: str, kind: str | None, to_kind: str | None = None) -> str | None:
    """下書きの関係 ``rel`` → 確定側の述語の名前。

    - 無条件の述語が 1 つなら、種別は見ない。
    - 条件つきが 2 つ（``sub``・``eq``＝主語の種別 ``from_kind``／``measures``＝相手の種別
      ``to_kind``）なら、1 つ目の条件に当たればそれ、当たらなければ 2 つ目（``kind`` が
      ``class`` でも ``property`` でもないときも 2 つ目）。
    - ただし条件が ``to_kind`` で ``to_kind`` が ``None`` のときは決められないので ``None``。

    ``rel`` が目録に無ければ ``KeyError``。"""
    rules = _BY_NAME[rel].predicates
    if len(rules) == 1:
        return rules[0].name
    first = rules[0]
    assert first.when is not None
    key, want = first.when
    value = kind if key == "from_kind" else to_kind
    if value is None and key == "to_kind":
        return None
    return first.name if value == want else rules[-1].name


# ---------------------------------------------------------------------------
# 導いた値（各モジュールの正の定義の元）
# ---------------------------------------------------------------------------


def relation_kinds() -> tuple[str, ...]:
    return tuple(r.name for r in RELATIONS)


def experiment_relations() -> tuple[str, ...]:
    return tuple(r.name for r in RELATIONS if r.experiment)


def symmetric_draft_relations() -> tuple[str, ...]:
    return tuple(r.name for r in RELATIONS if r.symmetric_draft)


def symmetric_pair_relations() -> tuple[str, ...]:
    return tuple(r.name for r in RELATIONS if r.symmetric_pair)


def pred_to_rel() -> dict[str, str]:
    """確定側の述語 → 下書きの関係名（``graph_diff.PRED_TO_REL``）。"""
    return {p.name: r.name for r in RELATIONS for p in r.predicates}


def rel_sentence() -> dict[str, dict[str, str]]:
    """言語 → 関係名 → 文の型（``graph_diff.REL_SENTENCE``）。"""
    return {lang: {r.name: r.sentence[lang] for r in RELATIONS} for lang in ("ja", "en")}


def align_relations() -> dict[str, str]:
    return {p.name: p.iri for p in PREDICATES}


def knowledge_relations() -> tuple[str, ...]:
    return tuple(p.name for p in PREDICATES if p.knowledge)


def symmetric_predicates() -> frozenset[str]:
    return frozenset(p.name for p in PREDICATES if p.symmetric)


def no_self_predicates() -> frozenset[str]:
    return frozenset(p.name for p in PREDICATES if p.no_self)


def subsumption_predicates() -> tuple[str, ...]:
    return tuple(p.name for p in PREDICATES if p.subsumption)


def same_shape_predicates() -> frozenset[str]:
    return frozenset(p.name for p in PREDICATES if p.same_shape)


def role_rules() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    return {p.name: p.roles for p in PREDICATES if p.roles is not None}


_Shapes = dict[str, tuple[frozenset[str], frozenset[str]]]


def relation_shapes(order: tuple[str, ...] | None = None) -> _Shapes:
    """述語 → 端の許す種別。``order`` があればその並び（既存の公開名の並びを保つため）。"""
    table = {p.name: p.shapes for p in PREDICATES}
    if order is None:
        return table
    assert set(order) == set(table), "order must list every predicate once"
    return {name: table[name] for name in order}


def own_relations() -> dict[str, dict[str, Any]]:
    """自前の述語の記録（``crosswalk_runtime.OWN_RELATIONS`` と同じ形）。"""
    return {
        p.name: {
            "label_ja": p.own.label["ja"],
            "label_en": p.own.label["en"],
            "definition": p.own.definition,
            "searched": list(p.own.searched),
            "date": p.own.date,
        }
        for p in PREDICATES
        if p.own is not None
    }


# ---------------------------------------------------------------------------
# JSON（``GET /api/relations``）
# ---------------------------------------------------------------------------


def _sorted(s: frozenset[str]) -> list[str]:
    return sorted(s)


def relation_to_json(r: Relation) -> dict[str, Any]:
    return {
        "name": r.name,
        "label": dict(r.label),
        "sentence": dict(r.sentence),
        "predicates": [
            {"name": p.name, "iri": p.iri, "when": list(p.when) if p.when else None}
            for p in r.predicates
        ],
        "symmetric_draft": r.symmetric_draft,
        "symmetric_pair": r.symmetric_pair,
        "experiment": r.experiment,
        "builtin": r.builtin,
    }


def predicate_to_json(p: Predicate) -> dict[str, Any]:
    return {
        "name": p.name,
        "iri": p.iri,
        "shapes": {"from": _sorted(p.shapes[0]), "to": _sorted(p.shapes[1])},
        "same_shape": p.same_shape,
        "roles": (
            None if p.roles is None else {"from": _sorted(p.roles[0]), "to": _sorted(p.roles[1])}
        ),
        "no_self": p.no_self,
        "subsumption": p.subsumption,
        "symmetric": p.symmetric,
        "knowledge": p.knowledge,
        "own": (
            None
            if p.own is None
            else {
                "label": dict(p.own.label),
                "definition": p.own.definition,
                "searched": list(p.own.searched),
                "date": p.own.date,
            }
        ),
    }


def catalog_to_json() -> dict[str, Any]:
    return {
        "relations": [relation_to_json(r) for r in RELATIONS],
        "predicates": [predicate_to_json(p) for p in PREDICATES],
    }
