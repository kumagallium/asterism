# ruff: noqa: E501, RUF001
# RUF001: 利用者に見せる日本語の文言（全角括弧）。IRI・SPARQL の文字列には全角を入れない。
"""関係の目録をストアの正本に置く（R0d・ADR ``docs/architecture/relation-catalog.md``）。

目録（:mod:`asterism.relation_catalog`）の JSON（``catalog_to_json``＝``GET /api/relations`` と同じ形）を、
専用のグラフ（:data:`asterism.substrate.RELATION_CATALOG_GRAPH`）へ RDF として置き、
同じ形の dict に読み戻す。

- :func:`catalog_triples` — 目録 → RDF（純関数・決定論。欄の型で決まる規則だけで写す）
- :func:`read_catalog` — ストア → 目録の dict（読み方の型は、今のコードの目録の形から導く）
- :func:`seed_builtin` — 組み込みを置く（冪等。組み込みでない項目には触らない）

規則（欄の型 → RDF）。欄が足されても、型が下の 6 つのどれかなら同じ規則で写る。

====================  =====================================================================
値                    写し方
====================  =====================================================================
``str``               平文の文字列 ``xw:<camelCase>``（``iri`` だけは IRI を指す ``xw:predicate``）
``bool`` / ``int``    ``xsd:boolean`` / ``xsd:integer``
``dict[str, str]``    言語タグつき文字列（``label`` は ``rdfs:label``、ほかは ``xw:<camelCase>``）
入れ子の ``dict``     子の節 ``<親>-<欄>``（``xw:EntryPart``）にぶら下げ、中の欄を同じ規則で
``list[str]``         子の節 ``<親>-<欄>-<n>``（``xw:EntryPart``・``xw:order``・``xw:value``）
``list[dict]``        子の節 ``<親>-<欄>-<n>``（``xw:order`` つき）。``predicates`` は
                      ``xw:rule`` ・``xw:PredicateRule``・``<関係>-rule-<n>``
``None``              何も書かない（読み戻すと ``None``）
それ以外              無視する（ADR に書く）
====================  =====================================================================

読み戻しは、今のコードの目録の形（欄の名前と型）を雛形にする。ストアにあってコードの目録に無い欄は
読まない（無視）。どの項目の子の節にも ``xw:ownerEntry <項目>`` を付け、置き換えのとき項目ごと消せるようにする。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import unicodedata
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rdflib
from rdflib import RDF, RDFS, XSD, Literal, URIRef

from asterism import relation_catalog, substrate
from asterism.crosswalk import XW
from asterism.term_match import merge_key

logger = logging.getLogger(__name__)

#: 目録の項目（関係・述語の記述）の名前空間。関係は ``#<短い名前>``、述語の記述は ``#predicate-<camelCase>``。
CATALOG_NS = "https://kumagallium.github.io/asterism/vocab/relation-catalog#"
CATALOG_GRAPH = substrate.RELATION_CATALOG_GRAPH

_PROV_AT = rdflib.URIRef("http://www.w3.org/ns/prov#generatedAtTime")

_X = rdflib.Namespace(XW)
RELATION_ENTRY = _X.RelationEntry
PREDICATE_ENTRY = _X.PredicateEntry
PREDICATE_RULE = _X.PredicateRule
ENTRY_PART = _X.EntryPart
_OWNER = _X.ownerEntry
_ORDER = _X.order
_VALUE = _X.value
_BUILTIN = _X.builtin
_ADDED_BY = _X.addedBy

#: 欄の名前 → 性質（camelCase でない特別扱い）。
_PROP_SPECIAL: dict[str, rdflib.URIRef] = {
    "label": RDFS.label,
    "iri": _X.predicate,
    "predicates": _X.rule,
}
#: 欄の名前 → 子の節の名前の部品（既定は camelCase の欄名）。
_SEG_SPECIAL = {"predicates": "rule"}


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _prop(key: str) -> rdflib.URIRef:
    return _PROP_SPECIAL.get(key) or _X[_camel(key)]


def _seg(key: str) -> str:
    return _SEG_SPECIAL.get(key) or _camel(key)


def _is_lang_map(v: Any) -> bool:
    return isinstance(v, dict) and bool(v) and all(isinstance(x, str) for x in v.values())


#: 項目の名前の規則。子の節の IRI（``<項目>-…``）や ``predicate-<名前>`` が、別の項目の IRI と
#: ぶつからないことを構造で保証する（関係の名前に ``-`` が無い・子の節は必ず ``-`` を含む）。
RELATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*\Z")
PREDICATE_NAME_RE = re.compile(r"^[a-z][A-Za-z0-9]*\Z")
#: 規則用に使っている性質の欄名（camelCase）。目録の欄がこれと同じ名前になると壊れるので断る。
#: ``name``・``builtin`` は目録の欄が同じ性質に写るので許す（予約の外）。
RESERVED_FIELDS = frozenset({"order", "value", "ownerEntry", "addedBy", "rule", "predicate"})


def _check_fields(obj: Any, where: str) -> None:
    if isinstance(obj, dict) and not _is_lang_map(obj):
        for k, v in obj.items():
            if _camel(k) in RESERVED_FIELDS:
                raise ValueError(f"{where}: field name {k!r} is reserved")
            _check_fields(v, where)
    elif isinstance(obj, list):
        for v in obj:
            _check_fields(v, where)


def relation_iri(name: str) -> URIRef:
    return URIRef(f"{CATALOG_NS}{name}")


def predicate_iri(name: str) -> URIRef:
    return URIRef(f"{CATALOG_NS}predicate-{name}")


# ---------------------------------------------------------------------------
# 目録 → RDF
# ---------------------------------------------------------------------------


def _emit(g: rdflib.Graph, node: URIRef, key: str, val: Any, top: URIRef) -> None:
    prop = _prop(key)
    if val is None:
        return
    if isinstance(val, bool):
        g.add((node, prop, Literal(val, datatype=XSD.boolean)))
    elif isinstance(val, int):
        g.add((node, prop, Literal(val, datatype=XSD.integer)))
    elif isinstance(val, str):
        g.add((node, prop, URIRef(val) if key == "iri" else Literal(val)))
    elif _is_lang_map(val):
        for lang in sorted(val):
            g.add((node, prop, Literal(val[lang], lang=lang)))
    elif isinstance(val, dict):
        child = URIRef(f"{node}-{_seg(key)}")
        g.add((node, prop, child))
        g.add((child, RDF.type, ENTRY_PART))
        g.add((child, _OWNER, top))
        for k, v in val.items():
            _emit(g, child, k, v, top)
    elif isinstance(val, list):
        for i, item in enumerate(val, start=1):
            if not isinstance(item, str | dict):
                continue  # 未知の型は無視（ADR に書く）
            child = URIRef(f"{node}-{_seg(key)}-{i}")
            g.add((node, prop, child))
            g.add((child, RDF.type, PREDICATE_RULE if key == "predicates" else ENTRY_PART))
            g.add((child, _OWNER, top))
            g.add((child, _ORDER, Literal(i, datatype=XSD.integer)))
            if isinstance(item, str):
                g.add((child, _VALUE, Literal(item)))
            else:
                for k, v in item.items():
                    _emit(g, child, k, v, top)
    # それ以外の型は無視


def catalog_triples(
    catalog: dict[str, Any],
    *,
    builtin: bool,
    at: str | None = None,
    added_by: str = "machine",
    order_base: int = 0,
) -> rdflib.Graph:
    """目録の JSON（``catalog_to_json``）→ RDF。純関数・決定論。

    ``builtin`` は組み込みかどうかを呼び手が明示する。項目に ``builtin`` の欄があればそれが優先、
    無ければ（述語）``builtin`` が真のときだけ ``xw:builtin true`` を付ける。
    ``at`` があれば、項目ごとに ``prov:generatedAtTime`` と ``xw:addedBy "machine"`` を付ける。
    名前が規則（:data:`RELATION_NAME_RE`・:data:`PREDICATE_NAME_RE`）に合わない項目、欄名が
    :data:`RESERVED_FIELDS` と同じ項目は ``ValueError``。
    ``added_by`` は来歴の ``xw:addedBy``（人が足した関係は ``"human"``）・``order_base`` は
    ``xw:order`` の振り始め（人の項目を組み込みの後ろに並べる）。"""
    g = rdflib.Graph()
    for section, typ, make, name_re in (
        ("relations", RELATION_ENTRY, relation_iri, RELATION_NAME_RE),
        ("predicates", PREDICATE_ENTRY, predicate_iri, PREDICATE_NAME_RE),
    ):
        for i, entry in enumerate(catalog.get(section, [])):
            name = entry.get("name")
            if not isinstance(name, str) or not name_re.match(name):
                raise ValueError(f"{section}: invalid name {name!r} (must match {name_re.pattern})")
            _check_fields(entry, f"{section}/{name}")
            top = make(name)
            g.add((top, RDF.type, typ))
            g.add((top, _ORDER, Literal(order_base + i, datatype=XSD.integer)))
            if "builtin" not in entry and builtin:
                g.add((top, _BUILTIN, Literal(True, datatype=XSD.boolean)))
            if at is not None:
                g.add((top, _PROV_AT, Literal(at, datatype=XSD.dateTime)))
                g.add((top, _ADDED_BY, Literal(added_by)))
            for k, v in entry.items():
                _emit(g, top, k, v, top)
    return g


# ---------------------------------------------------------------------------
# ストア → 目録（読み方の型は今のコードの目録から導く）
# ---------------------------------------------------------------------------


@dataclass
class _Spec:
    kind: str | None  # None のときは常に None（値の例が無い）
    nullable: bool = False
    sub: dict[str, _Spec] = field(default_factory=dict)


def _infer(values: list[Any]) -> _Spec:
    present = [v for v in values if v is not None]
    nullable = len(present) != len(values)
    if not present:
        return _Spec(None, nullable)
    v = present[0]
    if isinstance(v, bool):
        return _Spec("bool", nullable)
    if isinstance(v, int):
        return _Spec("int", nullable)
    if isinstance(v, str):
        return _Spec("str", nullable)
    if _is_lang_map(v):
        return _Spec("lang", nullable)
    if isinstance(v, dict):
        return _Spec("dict", nullable, _infer_fields(present))
    if isinstance(v, list):
        items = [x for lst in present for x in lst]
        if items and all(isinstance(x, dict) for x in items):
            return _Spec("list_dict", nullable, _infer_fields(items))
        return _Spec("list_str", nullable)
    return _Spec(None, nullable)


def _infer_fields(dicts: list[dict[str, Any]]) -> dict[str, _Spec]:
    keys: list[str] = []
    for d in dicts:
        for k in d:
            if k not in keys:
                keys.append(k)
    return {k: _infer([d.get(k) for d in dicts]) for k in keys}


_Node = dict[str, list[tuple[str, str, bool]]]  # 性質 IRI → [(値, 言語, IRI か)]


def _vals(data: dict[str, _Node], node: str, key: str) -> list[tuple[str, str, bool]]:
    return data.get(node, {}).get(str(_prop(key)), [])


def _order_of(data: dict[str, _Node], node: str) -> tuple[int, str]:
    vals = data.get(node, {}).get(str(_ORDER), [])
    try:
        return (int(vals[0][0]), node)
    except (IndexError, ValueError):
        return (10**9, node)


def _read_field(data: dict[str, _Node], node: str, key: str, sp: _Spec) -> Any:
    vals = _vals(data, node, key)
    if sp.kind is None:
        return None
    if sp.kind == "lang":
        got = {lang: v for v, lang, _ in vals if lang}
        return got or (None if sp.nullable else {})
    if sp.kind in ("dict", "list_dict", "list_str"):
        children = sorted({v for v, _, _ in vals}, key=lambda c: _order_of(data, c))
        if sp.kind == "dict":
            if not children:
                return None if sp.nullable else {}
            return _read_fields(data, children[0], sp.sub)
        if not children:
            return None if sp.nullable else []
        if sp.kind == "list_dict":
            return [_read_fields(data, c, sp.sub) for c in children]
        return [
            next((v for v, _, _ in data.get(c, {}).get(str(_VALUE), [])), "") for c in children
        ]
    plain = sorted(v for v, _, _ in vals)
    if not plain:
        return None if sp.nullable else {"bool": False, "int": 0, "str": ""}[sp.kind]
    if sp.kind == "bool":
        return plain[0].lower() == "true"
    if sp.kind == "int":
        return int(plain[0])
    return plain[0]


def _read_fields(data: dict[str, _Node], node: str, specs: dict[str, _Spec]) -> dict[str, Any]:
    return {k: _read_field(data, node, k, sp) for k, sp in specs.items()}


#: 人が足した関係だけが持つ欄の読み方（組み込みの JSON には無い欄なので、雛形に足して読む）。
_HUMAN_EXTRA: dict[str, _Spec] = {
    "retired": _Spec("bool"),
    "definition": _Spec("lang"),
    "exact_match": _Spec("str", nullable=True),
    "unstated_key": _Spec("str", nullable=True),
    "retired_at": _Spec("str", nullable=True),
}


def _first(data: dict[str, _Node], node: str, prop: URIRef) -> str:
    vals = data.get(node, {}).get(str(prop), [])
    return sorted(v for v, _, _ in vals)[0] if vals else ""


async def _load(client: Any) -> dict[str, _Node]:
    res = await client.sparql_select(
        "SELECT ?s ?p ?o ?lang WHERE { "
        f"GRAPH <{CATALOG_GRAPH}> {{ ?s ?p ?o }} BIND(LANG(?o) AS ?lang) }}"
    )
    data: dict[str, _Node] = {}
    for b in res.get("results", {}).get("bindings", []):
        o = b["o"]
        lang = (b.get("lang") or {}).get("value") or o.get("xml:lang") or ""
        data.setdefault(b["s"]["value"], {}).setdefault(b["p"]["value"], []).append(
            (o["value"], lang, o.get("type") == "uri")
        )
    return data


async def read_catalog(client: Any, *, builtin_only: bool = False) -> dict[str, Any]:
    """ストアの目録グラフ → ``catalog_to_json`` と同じ形の dict（並びは ``xw:order``）。

    ``builtin_only`` なら ``xw:builtin true`` の項目だけ。"""
    data = await _load(client)
    code = relation_catalog.catalog_to_json()
    out: dict[str, Any] = {}
    for section, typ in (("relations", RELATION_ENTRY), ("predicates", PREDICATE_ENTRY)):
        specs = _infer_fields(code[section])
        tops = [
            s
            for s, props in data.items()
            if any(v == str(typ) for v, _, _ in props.get(str(RDF.type), []))
            and (
                not builtin_only
                or any(v.lower() == "true" for v, _, _ in props.get(str(_BUILTIN), []))
            )
        ]
        tops.sort(key=lambda s: _order_of(data, s))
        entries = []
        for s in tops:
            entry = _read_fields(data, s, specs)
            if section == "relations" and not entry.get("builtin"):
                # 人が足した関係: 足した欄（R3a）と来歴を読む
                entry.update(_read_fields(data, s, _HUMAN_EXTRA))
                entry["added_by"] = _first(data, s, _ADDED_BY)
                entry["added_at"] = _first(data, s, _PROV_AT) or None
            entries.append(entry)
        out[section] = entries
    return out


# ---------------------------------------------------------------------------
# 種まき
# ---------------------------------------------------------------------------

_DELETE_BUILTIN = (
    f"DELETE {{ GRAPH <{CATALOG_GRAPH}> {{ ?s ?p ?o }} }} "
    f"WHERE {{ GRAPH <{CATALOG_GRAPH}> {{ ?top <{_BUILTIN}> true . "
    "{ ?top ?p ?o . BIND(?top AS ?s) } UNION { ?s <" + str(_OWNER) + "> ?top . ?s ?p ?o } } }"
)

_ISO_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})?\Z")


async def seed_builtin(client: Any, *, at: str) -> bool:
    """組み込みの目録をストアに置く。置いたら ``True``、何も書かなかったら ``False``。

    冪等: ストアの組み込みの項目（``xw:builtin true``）が今の目録と同じなら何も書かない。
    違えば組み込みの項目だけを置き換える（組み込みでない項目＝人が足した関係には触らない）。
    時刻（``at``）は置いたときだけ付く。"""
    if not _ISO_AT.match(at):
        raise ValueError(f"at must be an ISO-8601 datetime: {at!r}")
    target = relation_catalog.catalog_to_json()
    if await read_catalog(client, builtin_only=True) == target:
        return False
    nt = catalog_triples(target, builtin=True, at=at).serialize(format="nt")
    if isinstance(nt, bytes):
        nt = nt.decode()
    await client.sparql_update(
        f"{_DELETE_BUILTIN} ;\nINSERT DATA {{ GRAPH <{CATALOG_GRAPH}> {{\n{nt}\n}} }}"
    )
    return True


# ---------------------------------------------------------------------------
# 人が足す関係（R3a・ADR 「人が足す関係」）
# ---------------------------------------------------------------------------
#
# 1 件 = ``Relation``（``builtin=False``）＋ 確定側の ``Predicate`` 1 つ。書くのは目録のグラフだけ・
# 1 回の更新で（読み手が半端な項目を見ない）。組み込みの項目には触らない。

#: 人が足した関係の名前（機械が作る）。組み込みは ``u_`` で始めない（テストで固定）。
HUMAN_PREFIX = "u_"
_LABEL_MAX = 40
_DEFINITION_MAX = 200
_LANGS = ("ja", "en")
_UNSET: Any = object()


class RelationInputError(ValueError):
    """入力の検査に通らない（400）。"""


class RelationExists(ValueError):
    """同じ名前（表示名）の関係が既にある（409）。"""


class RelationForbidden(ValueError):
    """組み込みの関係には触れない（403）。"""


class RelationNotFound(KeyError):
    """足した関係の中に無い（404）。"""


class RelationInUse(ValueError):
    """使われているので消せない（409）。``usage`` は ``{lines, ledger, drafts}``。"""

    def __init__(self, usage: dict[str, int]) -> None:
        super().__init__(f"relation is in use: {usage}")
        self.usage = usage


_FORBIDDEN_CHARS = frozenset("<>{}`\\")
_URLISH = re.compile(r"(?:[a-z][a-z0-9+.-]*://|\bwww\.)", re.IGNORECASE)
_IRI_BAD = re.compile(r"[\s<>\"{}|\\^`]")


_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_SQL_WORDS = re.compile(r"\b(?:SELECT|INSERT|DELETE|WHERE|PREFIX)\b", re.IGNORECASE)
_NONE_WORDS = frozenset(
    {
        "none", "null", "n/a", "na", "unknown", "nothing", "no relation",
        "なし", "無し", "不明", "該当なし", "関係なし", "分からない", "わからない",
    }
)  # fmt: skip


def _clean_text(
    value: Any, *, field_name: str, max_len: int, strict: bool = False
) -> str:
    """入力の文字の規則（1 か所）。NFKC・空白の正規化・1〜``max_len`` 文字・制御／書式の文字なし・
    URL／``<>{}``／バッククォート／Markdown のリンクの形を断る。

    ``strict``（表示名・``unstated_key``＝名詞の短い札）は R1 の ``_unstated_kind`` と同じ系統に、
    さらにスキームの形・``//``・``[]``・SQL 語・「無い」の言い換え・記号だけ／数字だけも断る。
    説明・文は自然文なので strict にしない。"""
    if not isinstance(value, str):
        raise RelationInputError(f"{field_name}: 文字列で入れてください")
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    if not text:
        raise RelationInputError(f"{field_name}: 空にはできません")
    if len(text) > max_len:
        raise RelationInputError(f"{field_name}: {max_len} 文字までです")
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") or ch in _FORBIDDEN_CHARS:
            raise RelationInputError(f"{field_name}: 使えない文字があります")
    if _URLISH.search(text) or _MD_LINK.search(text):
        raise RelationInputError(f"{field_name}: URL の形は入れられません")
    if strict:
        low = text.casefold()
        if re.match(r"^[a-z][a-z0-9+.-]*:", text, re.IGNORECASE) or "//" in text:
            raise RelationInputError(f"{field_name}: URL の形は入れられません")
        if "[" in text or "]" in text or _SQL_WORDS.search(text):
            raise RelationInputError(f"{field_name}: 使えない文字があります")
        if low in _NONE_WORDS:
            raise RelationInputError(f"{field_name}: 「無い」の言い換えは入れられません")
        if not any(ch.isalpha() for ch in text):
            raise RelationInputError(f"{field_name}: 記号や数字だけにはできません")
    return text


def _clean_lang_map(
    value: Any,
    *,
    field_name: str,
    max_len: int,
    required: tuple[str, ...] = (),
    strict: bool = False,
) -> dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise RelationInputError(f"{field_name}: {{ja, en}} の形で入れてください")
    out: dict[str, str] = {}
    for lang, v in value.items():
        if lang not in _LANGS:
            raise RelationInputError(f"{field_name}: 言語は ja か en です: {lang!r}")
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        out[lang] = _clean_text(
            v, field_name=f"{field_name}.{lang}", max_len=max_len, strict=strict
        )
    for lang in required:
        if lang not in out:
            raise RelationInputError(f"{field_name}.{lang}: 必須です")
    return out


def _clean_exact_match(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise RelationInputError("exact_match: 文字列で入れてください")
    iri = value.strip()
    if (
        not re.match(r"^https?://[^/\s]+", iri)
        or len(iri) > 500
        or _IRI_BAD.search(iri)
        or iri.startswith(relation_catalog.REL_NS)
    ):
        raise RelationInputError(
            "exact_match: 標準の述語の IRI（http(s) の絶対 IRI）を入れてください"
        )
    return iri


def _clean_unstated_key(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _clean_text(value, field_name="unstated_key", max_len=200, strict=True)


def default_sentence(label: dict[str, str], *, symmetric: bool) -> dict[str, str]:
    """文の型が無いときに、表示名から機械が作る既定の文（``{s}`` ``{o}`` の差し込みは組み込みと同じ）。"""
    arrow = "↔" if symmetric else "→"
    ja = label.get("ja") or label.get("en") or ""
    en = label.get("en") or ja
    return {"ja": f"{{s}} {arrow} {{o}}（{ja}）", "en": f"{{s}} {arrow} {{o}} ({en})"}


def _sentence_for(
    given: dict[str, str], label: dict[str, str], *, symmetric: bool
) -> dict[str, str]:
    """言語ごとに、書かれた文があればそれ・無ければ既定の文（``rel_sentence`` は両言語を引くので両方埋める）。"""
    base = default_sentence(label, symmetric=symmetric)
    return {lang: given.get(lang) or base[lang] for lang in _LANGS}


def _clean_sentence(value: Any) -> dict[str, str]:
    """画面の文の型。``{s}`` と ``{o}`` の差し込みだけ許し、それ以外の ``{}`` ``<>`` は断る。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RelationInputError("sentence: {ja, en} の形で入れてください")
    out: dict[str, str] = {}
    for lang, v in value.items():
        if lang not in _LANGS:
            raise RelationInputError(f"sentence: 言語は ja か en です: {lang!r}")
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if not isinstance(v, str):
            raise RelationInputError(f"sentence.{lang}: 文字列で入れてください")
        text = " ".join(unicodedata.normalize("NFKC", v).split())
        if "{s}" not in text or "{o}" not in text:
            raise RelationInputError(f"sentence.{lang}: {{s}} と {{o}} を入れてください")
        _clean_text(
            text.replace("{s}", "").replace("{o}", ""),
            field_name=f"sentence.{lang}",
            max_len=_DEFINITION_MAX,
        )
        out[lang] = text
    return out


def _check_no_clash(label: dict[str, str], *, ignore: str | None = None) -> None:
    """表示名が組み込み・足した関係の名前・表示名と ``merge_key`` で同じなら ``RelationExists``。"""
    mine = {merge_key(t) for t in label.values()} - {""}
    builtin_preds = {merge_key(p.name) for p in relation_catalog.PREDICATES}
    if mine & builtin_preds:
        raise RelationExists("既にある関係（組み込みの述語名と同じ表示名）")
    for r in relation_catalog.all_relations():
        if r.name == ignore:
            continue
        theirs = {merge_key(r.name), *(merge_key(t) for t in r.label.values() if t)}
        theirs |= {merge_key(p.name) for p in r.predicates}  # 述語名と同じ表示名も断る
        if mine & theirs:
            shown = r.label.get("ja") or r.label.get("en") or r.name
            raise RelationExists(f"既にある関係（{shown}）")


def _pred_name(name: str) -> str:
    return "u" + name[len(HUMAN_PREFIX) :]


def _build(
    name: str,
    *,
    label: dict[str, str],
    sentence: dict[str, str],
    definition: dict[str, str],
    symmetric: bool,
    to_property_only: bool,
    exact_match: str | None,
    unstated_key: str | None,
    added_at: str,
    retired: bool = False,
    retired_at: str | None = None,
    own_searched: tuple[str, ...] = (),
    own_date: str | None = None,
) -> tuple[relation_catalog.Relation, relation_catalog.Predicate]:
    pname = _pred_name(name)
    iri = f"{relation_catalog.REL_NS}{pname}"
    rel = relation_catalog.Relation(
        name=name,
        label=label,
        sentence=sentence,
        predicates=(relation_catalog.PredicateRule(pname, iri),),
        symmetric_draft=symmetric,
        symmetric_pair=symmetric,
        experiment=False,
        builtin=False,
        to_property_only=to_property_only,
        same_kind=False,
        acyclic=False,
        question_op=None,
        reserve_slug=False,
        paper=False,
        retired=retired,
        definition=definition,
        exact_match=exact_match,
        unstated_key=unstated_key,
        added_by="human",
        added_at=added_at,
        retired_at=retired_at,
    )
    end = relation_catalog._PROPERTY if to_property_only else relation_catalog._EITHER
    pred = relation_catalog.Predicate(
        pname,
        iri,
        (relation_catalog._EITHER, end),
        symmetric=symmetric,
        knowledge=True,
        own=relation_catalog.OwnRecord(
            label=label,
            definition=definition.get("ja") or definition.get("en") or "",
            searched=own_searched,
            date=own_date or added_at[:10],
        ),
    )
    return rel, pred


def _delete_entry(top: URIRef) -> str:
    return (
        f"DELETE {{ GRAPH <{CATALOG_GRAPH}> {{ ?s ?p ?o }} }} "
        f"WHERE {{ GRAPH <{CATALOG_GRAPH}> {{ "
        f"{{ ?s ?p ?o . FILTER(?s = <{top}>) }} UNION {{ ?s <{_OWNER}> <{top}> . ?s ?p ?o }} }} }}"
    )


def _nt(g: rdflib.Graph) -> str:
    nt = g.serialize(format="nt")
    return nt.decode() if isinstance(nt, bytes) else nt


async def _write(
    client: Any,
    rel: relation_catalog.Relation,
    pred: relation_catalog.Predicate,
    *,
    order: tuple[int, int],
    replace: bool,
) -> None:
    """関係 1 件と述語 1 件を、目録のグラフへ 1 回の更新で書く（``replace`` なら先に同じ項目を消す）。"""
    rj = relation_catalog.relation_to_json(rel)
    rj.pop("added_by", None)  # 来歴は ``xw:addedBy``・``prov:generatedAtTime`` で書く
    rj.pop("added_at", None)
    at = rel.added_at or ""
    g_rel = catalog_triples(
        {"relations": [rj]}, builtin=False, at=at, added_by="human", order_base=order[0]
    )
    g_pred = catalog_triples(
        {"predicates": [relation_catalog.predicate_to_json(pred)]},
        builtin=False,
        at=at,
        added_by="human",
        order_base=order[1],
    )
    parts = []
    if replace:
        parts += [_delete_entry(relation_iri(rel.name)), _delete_entry(predicate_iri(pred.name))]
    parts.append(f"INSERT DATA {{ GRAPH <{CATALOG_GRAPH}> {{\n{_nt(g_rel)}\n{_nt(g_pred)}\n}} }}")
    await client.sparql_update(" ;\n".join(parts))


async def _read_human_raw(
    client: Any,
) -> tuple[list[relation_catalog.Relation], list[relation_catalog.Predicate]]:
    """検査しない読み出し（壊れた項目も返す。削除・使われ方の数え方が使う）。変換できない項目は飛ばす。"""
    cat = await read_catalog(client)
    rels: list[relation_catalog.Relation] = []
    for r in cat["relations"]:
        if r.get("builtin"):
            continue
        try:
            rels.append(relation_catalog.relation_from_json(r))
        except Exception:
            logger.warning("relation_store: unreadable human relation %r", r.get("name"))
    wanted = {rule.name for r in rels for rule in r.predicates}
    preds: list[relation_catalog.Predicate] = []
    for p in cat["predicates"]:
        if p.get("name") not in wanted:
            continue
        try:
            preds.append(relation_catalog.predicate_from_json(p))
        except Exception:
            logger.warning("relation_store: unreadable human predicate %r", p.get("name"))
    return rels, preds


async def read_human(
    client: Any,
) -> tuple[list[relation_catalog.Relation], list[relation_catalog.Predicate]]:
    """ストアの人が足した関係（組み込みでない項目）→ ``(関係, 述語)``（足した順）。

    壊れた項目（述語の記述が無い等）は捨てて警告のログを出す（目録全体を落とさない）。"""
    rels, preds = await _read_human_raw(client)
    return relation_catalog.sanitize_human(rels, preds)


async def _next_orders(client: Any) -> tuple[int, int]:
    """次に振る ``xw:order``（組み込みの後ろ・既にある人の項目の後ろ）。"""
    data = await _load(client)
    nxt = [10_000, 10_000]
    for node, props in data.items():
        types = {v for v, _, _ in props.get(str(RDF.type), [])}
        for i, typ in enumerate((RELATION_ENTRY, PREDICATE_ENTRY)):
            if str(typ) in types:
                nxt[i] = max(nxt[i], _order_of(data, node)[0] + 1)
    return nxt[0], nxt[1]


async def _orders_of(client: Any, rel: relation_catalog.Relation) -> tuple[int, int]:
    """既にある項目の ``xw:order``（置き換えても並びが変わらないように）。"""
    data = await _load(client)
    return (
        _order_of(data, str(relation_iri(rel.name)))[0],
        _order_of(data, str(predicate_iri(rel.predicates[0].name)))[0],
    )


async def _raw_relation(client: Any, name: str) -> relation_catalog.Relation:
    """足した関係を検査なしで引く（壊れていても消せる）。組み込みは 403・無ければ 404。"""
    if name in {r.name for r in relation_catalog.RELATIONS}:
        raise RelationForbidden("組み込みの関係には触れません")
    rels, _ = await _read_human_raw(client)
    rel = next((r for r in rels if r.name == name), None)
    if rel is None:
        raise RelationNotFound(name)
    return rel


async def _human_pair(
    client: Any, name: str
) -> tuple[relation_catalog.Relation, relation_catalog.Predicate]:
    if name in {r.name for r in relation_catalog.RELATIONS}:
        raise RelationForbidden("組み込みの関係には触れません")
    rels, preds = await read_human(client)
    rel = next((r for r in rels if r.name == name), None)
    pred = next((p for p in preds if rel is not None and p.name == rel.predicates[0].name), None)
    if rel is None or pred is None:
        raise RelationNotFound(name)
    return rel, pred


async def _add_relation_locked(client: Any, spec: dict[str, Any], *, at: str) -> relation_catalog.Relation:
    """人が関係を足す。名前（``u_<8 桁の 16 進>``）と述語は機械が作る。

    ``spec``: ``label {ja, en?}``（ja 必須）・``definition {ja?, en?}``（どちらか必須・AI への 1 文）・
    ``symmetric``・``to_property_only``・``sentence?``・``exact_match?``・``unstated_key?``。
    入力の検査は :class:`RelationInputError`・同じ名前（表示名）は :class:`RelationExists`。"""
    if not _ISO_AT.match(at):
        raise ValueError(f"at must be an ISO-8601 datetime: {at!r}")
    label = _clean_lang_map(
        spec.get("label"), field_name="label", max_len=_LABEL_MAX, required=("ja",), strict=True
    )
    definition = _clean_lang_map(
        spec.get("definition"), field_name="definition", max_len=_DEFINITION_MAX
    )
    if not definition:
        raise RelationInputError("definition: AI への説明を 1 文入れてください（日本語か英語）")
    symmetric = bool(spec.get("symmetric"))
    sentence = _sentence_for(_clean_sentence(spec.get("sentence")), label, symmetric=symmetric)
    exact_match = _clean_exact_match(spec.get("exact_match"))
    unstated_key = _clean_unstated_key(spec.get("unstated_key"))

    rels, preds = await read_human(client)  # 別の口で足された分も見て、重複を断る
    relation_catalog.set_human(rels, preds)
    if len((await _read_human_raw(client))[0]) >= MAX_HUMAN_RELATIONS:
        raise RelationLimit(f"足せる関係は {MAX_HUMAN_RELATIONS} 件までです")
    _check_no_clash(label)
    taken = {r.name for r in relation_catalog.all_relations()} | {
        p.name for p in relation_catalog.all_predicates()
    }
    while True:
        name = f"{HUMAN_PREFIX}{secrets.token_hex(4)}"
        if name not in taken and _pred_name(name) not in taken:
            break
    rel, pred = _build(
        name,
        label=label,
        sentence=sentence,
        definition=definition,
        symmetric=symmetric,
        to_property_only=bool(spec.get("to_property_only")),
        exact_match=exact_match,
        unstated_key=unstated_key,
        added_at=at,
    )
    await _write(client, rel, pred, order=await _next_orders(client), replace=False)
    return rel


async def _update_relation_locked(
    client: Any,
    name: str,
    *,
    label: dict[str, str] | None = None,
    sentence: dict[str, str] | None = None,
    definition: dict[str, str] | None = None,
    exact_match: Any = _UNSET,
) -> relation_catalog.Relation:
    """表示名・文・説明・``exact_match`` だけ直す（名前・述語・向き・終わりの種類は変えない）。

    渡さなかった欄はそのまま。``exact_match`` は ``None`` を渡すと消す。"""
    rel, pred = await _human_pair(client, name)
    new_label = (
        _clean_lang_map(label, field_name="label", max_len=_LABEL_MAX, required=("ja",), strict=True)
        if label is not None
        else dict(rel.label)
    )
    new_def = (
        _clean_lang_map(definition, field_name="definition", max_len=_DEFINITION_MAX)
        if definition is not None
        else dict(rel.definition)
    )
    if not new_def:
        raise RelationInputError("definition: AI への説明を 1 文入れてください（日本語か英語）")
    new_match = rel.exact_match if exact_match is _UNSET else _clean_exact_match(exact_match)
    if sentence is not None:
        given = _clean_sentence(sentence)
    else:  # 既定の文のままだった言語は新しい表示名で作り直す。書かれた文はそのまま
        old = default_sentence(rel.label, symmetric=rel.symmetric_pair)
        given = {lang: v for lang, v in rel.sentence.items() if v != old.get(lang)}
    new_sentence = _sentence_for(given, new_label, symmetric=rel.symmetric_pair)
    if new_label != rel.label:
        rels, preds = await read_human(client)
        relation_catalog.set_human(rels, preds)
        _check_no_clash(new_label, ignore=name)
    new_rel, new_pred = _build(
        name,
        label=new_label,
        sentence=new_sentence,
        definition=new_def,
        symmetric=rel.symmetric_pair,
        to_property_only=rel.to_property_only,
        exact_match=new_match,
        unstated_key=rel.unstated_key,
        added_at=rel.added_at or "",
        retired=rel.retired,
        retired_at=rel.retired_at,
        own_searched=pred.own.searched if pred.own else (),
        own_date=pred.own.date if pred.own else None,
    )
    await _write(client, new_rel, new_pred, order=await _orders_of(client, rel), replace=True)
    return new_rel


async def _retire_relation_locked(client: Any, name: str, *, at: str) -> relation_catalog.Relation:
    """「使うのをやめる」。何度呼んでも同じ（やめた時刻は最初の 1 回）。戻す口は無い。"""
    if not _ISO_AT.match(at):
        raise ValueError(f"at must be an ISO-8601 datetime: {at!r}")
    rel, pred = await _human_pair(client, name)
    if rel.retired:
        return rel
    new_rel, _ = _build(
        name,
        label=rel.label,
        sentence=rel.sentence,
        definition=rel.definition,
        symmetric=rel.symmetric_pair,
        to_property_only=rel.to_property_only,
        exact_match=rel.exact_match,
        unstated_key=rel.unstated_key,
        added_at=rel.added_at or "",
        retired=True,
        retired_at=at,
    )
    await _write(client, new_rel, pred, order=await _orders_of(client, rel), replace=True)
    return new_rel


# ---- 使われているかの数え方 -------------------------------------------------


def _count_json(obj: Any, name: str, pred: str) -> int:
    """JSON の中で ``rel`` が関係の名前・``relation`` が述語の名前の辞書の数。"""
    n = 0
    if isinstance(obj, dict):
        if obj.get("rel") == name or obj.get("relation") == pred:
            n += 1
        n += sum(_count_json(v, name, pred) for v in obj.values())
    elif isinstance(obj, list):
        n += sum(_count_json(v, name, pred) for v in obj)
    return n


def _scan_registry(root: str | Path | None, name: str, pred: str) -> tuple[int, int]:
    """``(台帳の件数, 下書き・却下の記録の件数)``。読めないファイルは数えない。"""
    if root is None:
        return 0, 0
    base = Path(root) / "vocab-shared"
    ledger = drafts = 0
    if not base.is_dir():
        return 0, 0
    for path in sorted(base.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if path.relative_to(base).parts[0] == "knowledge":
            ledger += _count_json(data, name, pred)
        else:
            drafts += _count_json(data, name, pred)
    return ledger, drafts


async def usage(
    client: Any, name: str, *, registry_root: str | Path | None = None
) -> dict[str, int]:
    """足した関係の使われ方 ``{lines, ledger, drafts}``。

    * ``lines`` — 確定の線（足した関係の述語の 3 つ組がどこかのグラフにある数）
    * ``ledger`` — 取り込み元の台帳で ``rel == 名前`` の件
    * ``drafts`` — 話の下書き・却下の記録に名前（関係の名前・述語の名前）が出る件"""
    rel = await _raw_relation(client, name)  # 壊れた項目でも数えられる（502 にしない）
    lines = 0
    pred_name = _pred_name(name)
    for rule in rel.predicates:
        pred_name = rule.name
        if not rule.iri:
            continue
        res = await client.sparql_select(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ ?s <{rule.iri}> ?o }} }}"
        )
        rows = res.get("results", {}).get("bindings", [])
        lines += int(rows[0]["n"]["value"]) if rows else 0
    ledger, drafts = _scan_registry(registry_root, name, pred_name)
    return {"lines": lines, "ledger": ledger, "drafts": drafts}


async def _delete_relation_locked(
    client: Any, name: str, *, registry_root: str | Path | None = None
) -> None:
    """どこにも使われていないときだけ、目録の項目（関係と述語）を消す。使われていれば :class:`RelationInUse`。"""
    rel = await _raw_relation(client, name)  # 壊れた項目（述語の記述が無い等）も消せる
    counts = await usage(client, name, registry_root=registry_root)
    if any(counts.values()):
        raise RelationInUse(counts)
    tops = [relation_iri(name), *(predicate_iri(rule.name) for rule in rel.predicates)]
    await client.sparql_update(" ;\n".join(_delete_entry(t) for t in tops))


_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)
#: 人の関係の数の上限（超えたら足せない）。
MAX_HUMAN_RELATIONS = 500


class RelationLimit(RelationExists):
    """人が足せる関係の上限（409）。"""


def _write_lock() -> asyncio.Lock:
    """書き込みの排他（重複の検査と書き込み・使われ方の集計と削除の間を閉じる）。単一のプロセスの前提で、
    イベントループごとに 1 つ（ループをまたいで 1 つの Lock を使い回さない）。"""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    return lock


async def add_relation(client: Any, spec: dict[str, Any], *, at: str) -> relation_catalog.Relation:
    async with _write_lock():
        return await _add_relation_locked(client, spec, at=at)


async def update_relation(client: Any, name: str, **kw: Any) -> relation_catalog.Relation:
    async with _write_lock():
        return await _update_relation_locked(client, name, **kw)


async def retire_relation(client: Any, name: str, *, at: str) -> relation_catalog.Relation:
    async with _write_lock():
        return await _retire_relation_locked(client, name, at=at)


async def delete_relation(
    client: Any, name: str, *, registry_root: str | Path | None = None
) -> None:
    async with _write_lock():
        await _delete_relation_locked(client, name, registry_root=registry_root)


async def refresh_human(client: Any, *, startup: bool = False) -> int:
    """ストアの人が足した関係を読んで、重ねた目録（``relation_catalog.set_human``）に入れる。

    失敗しても起動を止めない。``startup`` の失敗だけ足した関係 0 件として動く。書き込み後の失敗は
    前の状態を保つ（0 件にしない）。入れた（保った）件数を返す。"""
    try:
        rels, preds = await read_human(client)
    except Exception:
        logger.warning("relation_store: human relations read failed", exc_info=True)
        if startup:
            relation_catalog.set_human((), ())
            return 0
        return len(relation_catalog.human_relations())
    relation_catalog.set_human(rels, preds)
    return len(rels)
