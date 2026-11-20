"""関係の目録（組み込み）— 関係の定義を置く、コードの中の 1 か所。

design.md §23.13・ADR ``docs/architecture/relation-catalog.md``。

2 つの表を持つ。

- :data:`RELATIONS` — 下書き側の短い名前（``sub`` ``eq`` …）1 つにつき 1 件。表示名・文の型・
  向きの有無・確定側の述語の決め方（:class:`PredicateRule`）。
- :data:`PREDICATES` — 確定側の述語（``subClassOf`` ``competesWith`` …）1 つにつき 1 件。
  端の許す種別・役割・自分自身への線・循環・向き・自前の記録（:class:`Predicate`）。

``themes`` / ``graph_diff`` / ``crosswalk_runtime`` / ``pair_relations`` / ``ledger_answer`` /
``shared_vocab`` / ``sky_view`` / ``knowledge`` と api の ``chat_paper`` / ``chat_turns`` /
``consult_graph_routes`` が持っていた関係の定義と写しは、ここから導く
（公開名は各モジュールに残す）。

このモジュールは**葉**: ``asterism`` のほかのモジュールを import しない（循環を作らない）。
例外（``ThemeError`` など）は呼び出し側が作る。向きの有無は 3 層で中身が違い
（下書き＝``competes`` だけ／台帳と提案＝``eq`` と ``competes``／確定＝``competesWith`` だけ）、
揃えると振る舞いが変わるので欄を分けて今の値を保つ。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
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
    # 循環を調べるとき辿る辺の述語（自分と、同じものを言う述語）。``subsumption`` の述語だけが持つ
    cycle_rels: tuple[str, ...] = ()
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
    # --- R0b: 検査と問いの規則（写しを目録の導出にするための欄。JSON では足すだけ）
    to_property_only: bool = False  # 相手は項目だけ（下げる・上げる・変える）
    same_kind: bool = False  # 両端が同じ種別（一種・同じ・一部）
    acyclic: bool = False  # 下書き・台帳の上で輪を拒む（一種・一部）
    factor_directions: tuple[str, ...] = ()  # 数える ``factors`` の向き（下げる等の問い）
    question_op: str | None = None  # この関係を数える問いの型（``knowledge.OPS`` の名前）
    reserve_slug: bool = False  # 関係の名前を ``sv:`` の slug に使わせない
    paper: bool = True  # 論文から読む関係の対象（``paper_propose.PAPER_RELS``）
    # --- R3a: 人が足した関係の欄（組み込みは既定のまま。JSON にも人の項目にだけ出す）
    retired: bool = False  # 使うのをやめた（新しくは選べない・付いた組と根拠は読める）
    definition: dict[str, str] = field(default_factory=dict)  # 人が書く説明（AI への 1 文）
    exact_match: str | None = None  # 標準の述語の IRI（「同じ意味」として指すだけ）
    unstated_key: str | None = None  # どの「言えなかった種類」（R2 の群の鍵）から足したか
    added_by: str = ""  # 来歴（人の項目は ``"human"``）
    added_at: str | None = None  # 足した時刻（ISO-8601）
    retired_at: str | None = None  # やめた時刻（ISO-8601）


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
    Predicate(
        "subClassOf", f"{_RDFS}subClassOf", (_CLASS, _CLASS),
        subsumption=True, cycle_rels=("subClassOf", "equivalentClass"),
    ),
    Predicate("equivalentProperty", f"{_OWL}equivalentProperty", (_PROPERTY, _PROPERTY)),
    Predicate(
        "subPropertyOf", f"{_RDFS}subPropertyOf", (_PROPERTY, _PROPERTY),
        subsumption=True, cycle_rels=("subPropertyOf", "equivalentProperty"),
    ),
    Predicate("hasQuantityKind", "http://qudt.org/schema/qudt/hasQuantityKind", (_PROPERTY, _NONE)),
    # 知識の関係（問いから育てる・design.md §15.2）。自前の述語は作らず標準を使う。
    # BFO の part of と RO の regulates 系。
    Predicate(
        "isPartOf",
        f"{_OBO}BFO_0000050",
        (_EITHER, _EITHER),
        same_shape=True,
        subsumption=True,
        cycle_rels=("isPartOf",),
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
    **flags: Any,
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
        same_kind=True, acyclic=True, question_op="kinds",
    ),
    _rel(
        "eq", "同じ", "same as", "{s} と {o} は同じもの", "{s} is the same as {o}",
        (
            _P("equivalentClass", _iri("equivalentClass"), ("from_kind", "class")),
            _P("equivalentProperty", _iri("equivalentProperty"), ("from_kind", "property")),
        ),
        symmetric_pair=True, same_kind=True, paper=False,
    ),
    _rel(
        "quantity", "量", "quantity", "{s} の量は {o}", "The quantity of {s} is {o}",
        _one("hasQuantityKind"), paper=False,
    ),
    _rel(
        "part", "一部", "part of", "{s} は {o} の一部", "{s} is a part of {o}", _one("isPartOf"),
        same_kind=True, acyclic=True, question_op="parts",
    ),
    _rel(
        "lowers", "下げる", "lowers", "{s} は {o} を下げる", "{s} lowers {o}", _one("lowers"),
        to_property_only=True, factor_directions=("lowers", "affects"), question_op="factors",
        reserve_slug=True,
    ),
    _rel(
        "raises", "上げる", "raises", "{s} は {o} を上げる", "{s} raises {o}", _one("raises"),
        to_property_only=True, factor_directions=("raises", "affects"), question_op="factors",
        reserve_slug=True,
    ),
    _rel(
        "affects", "変える", "changes", "{s} は {o} を変える", "{s} changes {o}", _one("affects"),
        to_property_only=True, factor_directions=("affects",), question_op="factors",
        reserve_slug=True,
    ),
    _rel(
        "makes", "作る", "makes", "{s} で {o} を作る", "{s} makes {o}", _one("makes"),
        experiment=True, question_op="makers", reserve_slug=True,
    ),
    _rel(
        "measures", "測る", "measures", "{s} で {o} を測る", "{s} measures {o}",
        (
            _P("measuresProperty", _iri("measuresProperty"), ("to_kind", "property")),
            _P("measuresMaterial", _iri("measuresMaterial"), ("to_kind", "class")),
        ),
        experiment=True, question_op="measurers", reserve_slug=True,
    ),
    _rel(
        "becomes", "変わる", "turns into", "{s} が {o} に変わる", "{s} turns into {o}",
        _one("becomes"), experiment=True, question_op="sources", reserve_slug=True,
    ),
    _rel(
        "competes", "競合する", "competes with", "{s} と {o} は競合する", "{s} competes with {o}",
        _one("competesWith"), symmetric_draft=True, symmetric_pair=True, experiment=True,
        question_op="competitors", reserve_slug=True,
    ),
    _rel(
        "has_property", "物性", "has property", "{s} の {o}", "{s} has the property {o}",
        _one("hasProperty"), experiment=True, question_op="bearers", reserve_slug=True,
    ),
)  # fmt: skip

# ---------------------------------------------------------------------------
# 人が足した関係（R3a）。組み込み（:data:`RELATIONS`・:data:`PREDICATES`）の後ろに足した順で重ねる。
# ストアから読んで :func:`set_human` で入れる（起動時と、口で書いた後）。単一のプロセスの前提。
# 足した関係が 0 件なら、下の読み出し関数の値はすべて組み込みだけのときと同じ。
# ---------------------------------------------------------------------------

_HUMAN_RELATIONS: tuple[Relation, ...] = ()
_HUMAN_PREDICATES: tuple[Predicate, ...] = ()
_ALL_RELATIONS: tuple[Relation, ...] = RELATIONS
_ALL_PREDICATES: tuple[Predicate, ...] = PREDICATES
_BY_NAME = {r.name: r for r in _ALL_RELATIONS}
_PRED_BY_NAME = {p.name: p for p in _ALL_PREDICATES}
_GENERATION = 0  # 足した関係を入れ替えるたびに増える（導いた値の控えを捨てる合図）
_LISTENERS: list[Callable[[], None]] = []


def on_change(callback: Callable[[], None]) -> None:
    """足した関係が入れ替わったときに呼ぶ関数を登録する（実体の dict を持つ読み手が追いつく）。"""
    _LISTENERS.append(callback)


def generation() -> int:
    """足した関係が入れ替わった回数。導いた値を控える読み手が、古くなったか見る。"""
    return _GENERATION


def set_human(
    relations: tuple[Relation, ...] | list[Relation],
    predicates: tuple[Predicate, ...] | list[Predicate],
) -> None:
    """人が足した関係を入れ替える（空なら組み込みだけに戻る）。組み込みと同じ名前の項目は無視する。"""
    global _HUMAN_RELATIONS, _HUMAN_PREDICATES, _ALL_RELATIONS, _ALL_PREDICATES
    global _BY_NAME, _PRED_BY_NAME, _GENERATION
    builtin_r = {r.name for r in RELATIONS}
    builtin_p = {p.name for p in PREDICATES}
    hr = tuple(r for r in relations if r.name not in builtin_r)
    hp = tuple(p for p in predicates if p.name not in builtin_p)
    _HUMAN_RELATIONS, _HUMAN_PREDICATES = hr, hp
    _ALL_RELATIONS = (*RELATIONS, *hr)
    _ALL_PREDICATES = (*PREDICATES, *hp)
    _BY_NAME = {r.name: r for r in _ALL_RELATIONS}
    _PRED_BY_NAME = {p.name: p for p in _ALL_PREDICATES}
    _GENERATION += 1
    for callback in _LISTENERS:
        callback()


def all_relations() -> tuple[Relation, ...]:
    """組み込み＋足した関係（組み込みが先・足した順）。やめた関係も含む。"""
    return _ALL_RELATIONS


def all_predicates() -> tuple[Predicate, ...]:
    return _ALL_PREDICATES


def human_relations() -> tuple[Relation, ...]:
    """人が足した関係（足した順・やめたものも含む）。"""
    return _HUMAN_RELATIONS


def human_predicates() -> tuple[Predicate, ...]:
    return _HUMAN_PREDICATES


def is_human(name: str) -> bool:
    """名前が人の足した関係か。"""
    r = _BY_NAME.get(name)
    return r is not None and not r.builtin


def is_retired(name: str) -> bool:
    """名前が「使うのをやめた」関係か（組み込み・未知の名前は偽）。"""
    r = _BY_NAME.get(name)
    return r is not None and r.retired


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
    return tuple(r.name for r in _ALL_RELATIONS)


def assignable_relation_kinds() -> tuple[str, ...]:
    """新しく付ける・選べる関係（``retired`` を除く）。読み戻し・文は :func:`relation_kinds`。"""
    return tuple(r.name for r in _ALL_RELATIONS if not r.retired)


def assignable_predicates() -> tuple[str, ...]:
    """新しく確定できる述語（やめた関係の述語を除く）。"""
    retired = {p.name for r in _ALL_RELATIONS if r.retired for p in r.predicates}
    return tuple(p.name for p in _ALL_PREDICATES if p.name not in retired)


def experiment_relations() -> tuple[str, ...]:
    return tuple(r.name for r in _ALL_RELATIONS if r.experiment)


def symmetric_draft_relations() -> tuple[str, ...]:
    return tuple(r.name for r in _ALL_RELATIONS if r.symmetric_draft)


def symmetric_pair_relations() -> tuple[str, ...]:
    return tuple(r.name for r in _ALL_RELATIONS if r.symmetric_pair)


def pred_to_rel() -> dict[str, str]:
    """確定側の述語 → 下書きの関係名（``graph_diff.PRED_TO_REL``）。"""
    return {p.name: r.name for r in _ALL_RELATIONS for p in r.predicates}


def rel_sentence() -> dict[str, dict[str, str]]:
    """言語 → 関係名 → 文の型（``graph_diff.REL_SENTENCE``）。"""
    return {lang: {r.name: r.sentence[lang] for r in _ALL_RELATIONS} for lang in ("ja", "en")}


def align_relations() -> dict[str, str]:
    return {p.name: p.iri for p in _ALL_PREDICATES}


def knowledge_relations() -> tuple[str, ...]:
    return tuple(p.name for p in _ALL_PREDICATES if p.knowledge)


def symmetric_predicates() -> frozenset[str]:
    return frozenset(p.name for p in _ALL_PREDICATES if p.symmetric)


def no_self_predicates() -> frozenset[str]:
    return frozenset(p.name for p in _ALL_PREDICATES if p.no_self)


def subsumption_predicates() -> tuple[str, ...]:
    return tuple(p.name for p in _ALL_PREDICATES if p.subsumption)


def same_shape_predicates() -> frozenset[str]:
    return frozenset(p.name for p in _ALL_PREDICATES if p.same_shape)


def role_rules() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    return {p.name: p.roles for p in _ALL_PREDICATES if p.roles is not None}


_Shapes = dict[str, tuple[frozenset[str], frozenset[str]]]


def relation_shapes(order: tuple[str, ...] | None = None) -> _Shapes:
    """述語 → 端の許す種別。``order`` があればその並び（既存の公開名の並びを保つため）。"""
    table = {p.name: p.shapes for p in _ALL_PREDICATES}
    if order is None:
        return table
    assert set(order) == {p.name for p in PREDICATES}, "order must list every predicate once"
    # 並びは組み込みの順・人が足した述語はその後ろ
    out = {name: table[name] for name in order}
    out.update({n: sh for n, sh in table.items() if n not in out})
    return out


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
        for p in _ALL_PREDICATES
        if p.own is not None
    }


# ---------------------------------------------------------------------------
# 検査と問いの規則（R0b: 各モジュールの写しの元）
# ---------------------------------------------------------------------------


def to_property_only_relations() -> tuple[str, ...]:
    """相手は項目だけの関係（下書きの名前）。"""
    return tuple(r.name for r in _ALL_RELATIONS if r.to_property_only)


def to_property_only_predicates() -> frozenset[str]:
    """相手は項目だけの関係の、確定側の述語の名前。"""
    return frozenset(
        rule.name for r in _ALL_RELATIONS if r.to_property_only for rule in r.predicates
    )


def same_kind_relations() -> tuple[str, ...]:
    """両端が同じ種別の関係（下書きの名前）。"""
    return tuple(r.name for r in _ALL_RELATIONS if r.same_kind)


def paper_same_kind_relations() -> tuple[str, ...]:
    """``same_kind`` のうち論文から読む関係（``paper``）。"""
    return tuple(r.name for r in _ALL_RELATIONS if r.same_kind and r.paper)


def acyclic_relations() -> tuple[str, ...]:
    """下書き・台帳の上で輪を拒む関係。"""
    return tuple(r.name for r in _ALL_RELATIONS if r.acyclic)


def factor_relations() -> tuple[str, ...]:
    """``factors`` の向きになる関係（``knowledge.DIRECTIONS``・下げる・上げる・変える）。"""
    return tuple(r.name for r in _ALL_RELATIONS if r.factor_directions)


def factor_directions() -> dict[str, tuple[str, ...]]:
    """関係 → その線を数える ``factors`` の向き。"""
    return {r.name: r.factor_directions for r in _ALL_RELATIONS if r.factor_directions}


def direction_relations() -> dict[str, tuple[str, ...]]:
    """``factors`` の向き → 数える関係（``factor_directions`` の逆引き）。"""
    return {
        d: tuple(r.name for r in _ALL_RELATIONS if d in r.factor_directions)
        for d in factor_relations()
    }


def question_ops() -> dict[str, str]:
    """関係 → その線を数える問いの型。"""
    return {r.name: r.question_op for r in _ALL_RELATIONS if r.question_op is not None}


def relation_for_op(op: str) -> str | None:
    """問いの型 → その型 1 つだけが数える関係（複数なら先頭）。無ければ ``None``。"""
    return next((r.name for r in _ALL_RELATIONS if r.question_op == op), None)


def reserved_slugs() -> frozenset[str]:
    """``sv:`` の slug に使わせない関係の名前（目録の名前の分）。"""
    return frozenset(r.name for r in _ALL_RELATIONS if r.reserve_slug)


def paper_relations() -> tuple[str, ...]:
    return tuple(r.name for r in _ALL_RELATIONS if r.paper)


def class_both_relations() -> tuple[str, ...]:
    """両端とも種類の関係（全部の述語が ``(class, class)``）。"""
    both = (frozenset({"class"}), frozenset({"class"}))
    return tuple(
        r.name
        for r in _ALL_RELATIONS
        if all(_PRED_BY_NAME[p.name].shapes == both for p in r.predicates)
    )


def from_class_relations() -> tuple[str, ...]:
    """主語が種類の関係（全部の述語の from が ``class`` だけ）。"""
    return tuple(
        r.name
        for r in _ALL_RELATIONS
        if all(_PRED_BY_NAME[p.name].shapes[0] == _CLASS for p in r.predicates)
    )


def symmetric_pair_predicates() -> frozenset[str]:
    """台帳・提案で向きが無い関係の、確定側の述語の名前（``eq`` の 2 つと ``competesWith``）。"""
    return frozenset(p.name for r in _ALL_RELATIONS if r.symmetric_pair for p in r.predicates)


def from_class_predicates() -> frozenset[str]:
    """主語が種類のときに選ばれる述語（``when`` が ``from_kind=class`` の物）。"""
    return frozenset(
        p.name for r in _ALL_RELATIONS for p in r.predicates if p.when == ("from_kind", "class")
    )


def predicates_of(*rels: str) -> tuple[str, ...]:
    """下書きの関係（名前）が選びうる確定側の述語。``rels`` の並び・各関係の述語の並びのまま。"""
    return tuple(p.name for name in rels for p in _BY_NAME[name].predicates)


def cycle_rels(pred: str) -> tuple[str, ...] | None:
    """述語の線を足したとき輪を調べる辺の述語。輪を拒まない述語・未知の名前は ``None``。"""
    p = _PRED_BY_NAME.get(pred)
    return p.cycle_rels if p is not None and p.cycle_rels else None


# ---------------------------------------------------------------------------
# JSON（``GET /api/relations``）
# ---------------------------------------------------------------------------


def _sorted(s: frozenset[str]) -> list[str]:
    return sorted(s)


def relation_to_json(r: Relation) -> dict[str, Any]:
    out = _relation_base_json(r)
    if not r.builtin:
        out.update(
            {
                "retired": r.retired,
                "definition": dict(r.definition),
                "exact_match": r.exact_match,
                "unstated_key": r.unstated_key,
                "added_by": r.added_by,
                "added_at": r.added_at,
                "retired_at": r.retired_at,
            }
        )
    return out


def _relation_base_json(r: Relation) -> dict[str, Any]:
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
        "to_property_only": r.to_property_only,
        "same_kind": r.same_kind,
        "acyclic": r.acyclic,
        "factor_directions": list(r.factor_directions),
        "question_op": r.question_op,
        "reserve_slug": r.reserve_slug,
        "paper": r.paper,
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
        "cycle_rels": list(p.cycle_rels),
    }


def catalog_to_json() -> dict[str, Any]:
    """組み込みだけの目録（種まきと TS 生成の正）。足した関係は入らない。"""
    return {
        "relations": [relation_to_json(r) for r in RELATIONS],
        "predicates": [predicate_to_json(p) for p in PREDICATES],
    }


def overlay_catalog_to_json() -> dict[str, Any]:
    """組み込み＋人が足した関係（``GET /api/relations`` の既定）。0 件なら組み込みだけと同じ。"""
    return {
        "relations": [relation_to_json(r) for r in _ALL_RELATIONS],
        "predicates": [predicate_to_json(p) for p in _ALL_PREDICATES],
    }


# ---------------------------------------------------------------------------
# JSON → 目録の物（人が足した関係をストアから読み戻すとき）
# ---------------------------------------------------------------------------


def relation_from_json(d: dict[str, Any]) -> Relation:
    """:func:`relation_to_json` の逆（人が足した項目用）。足りない欄は既定で埋める。"""
    rules = tuple(
        PredicateRule(
            str(p["name"]),
            str(p["iri"]),
            (str(p["when"][0]), str(p["when"][1])) if p.get("when") else None,
        )
        for p in d.get("predicates") or []
    )
    return Relation(
        name=str(d["name"]),
        label=dict(d.get("label") or {}),
        sentence=dict(d.get("sentence") or {}),
        predicates=rules,
        symmetric_draft=bool(d.get("symmetric_draft")),
        symmetric_pair=bool(d.get("symmetric_pair")),
        experiment=bool(d.get("experiment")),
        builtin=bool(d.get("builtin", False)),
        to_property_only=bool(d.get("to_property_only")),
        same_kind=bool(d.get("same_kind")),
        acyclic=bool(d.get("acyclic")),
        factor_directions=tuple(d.get("factor_directions") or ()),
        question_op=d.get("question_op") or None,
        reserve_slug=bool(d.get("reserve_slug")),
        paper=bool(d.get("paper")),
        retired=bool(d.get("retired")),
        definition=dict(d.get("definition") or {}),
        exact_match=d.get("exact_match") or None,
        unstated_key=d.get("unstated_key") or None,
        added_by=str(d.get("added_by") or ""),
        added_at=d.get("added_at") or None,
        retired_at=d.get("retired_at") or None,
    )


def predicate_from_json(d: dict[str, Any]) -> Predicate:
    """:func:`predicate_to_json` の逆。"""
    shapes = d.get("shapes") or {}
    roles = d.get("roles")
    own = d.get("own")
    return Predicate(
        name=str(d["name"]),
        iri=str(d["iri"]),
        shapes=(frozenset(shapes.get("from") or ()), frozenset(shapes.get("to") or ())),
        same_shape=bool(d.get("same_shape")),
        roles=(
            None
            if roles is None
            else (frozenset(roles.get("from") or ()), frozenset(roles.get("to") or ()))
        ),
        no_self=bool(d.get("no_self")),
        subsumption=bool(d.get("subsumption")),
        cycle_rels=tuple(d.get("cycle_rels") or ()),
        symmetric=bool(d.get("symmetric")),
        knowledge=bool(d.get("knowledge")),
        own=(
            None
            if not own
            else OwnRecord(
                label=dict(own.get("label") or {}),
                definition=str(own.get("definition") or ""),
                searched=tuple(own.get("searched") or ()),
                date=str(own.get("date") or ""),
            )
        ),
    )


class LiveMap(Mapping[Any, Any]):
    """呼ぶたびに目録から導く読み取り専用の辞書。``from x import NAME`` で束ねた読み手にも、
    足した関係が届く（目録が入れ替わった回数が変わったときだけ導き直す）。"""

    def __init__(self, derive: Callable[[], dict[Any, Any]]) -> None:
        self._derive = derive
        self._gen = -1
        self._data: dict[Any, Any] = {}

    def _now(self) -> dict[Any, Any]:
        if self._gen != _GENERATION:
            self._data = self._derive()
            self._gen = _GENERATION
        return self._data

    def __getitem__(self, key: Any) -> Any:
        return self._now()[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._now())

    def __len__(self) -> int:
        return len(self._now())

    def __repr__(self) -> str:
        return repr(self._now())
