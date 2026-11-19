# ruff: noqa: E501
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

import re
from dataclasses import dataclass, field
from typing import Any

import rdflib
from rdflib import RDF, RDFS, XSD, Literal, URIRef

from asterism import relation_catalog, substrate
from asterism.crosswalk import XW

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


def catalog_triples(catalog: dict[str, Any], *, at: str | None = None) -> rdflib.Graph:
    """目録の JSON（``catalog_to_json``）→ RDF。純関数・決定論。

    ``at`` があれば、項目ごとに ``prov:generatedAtTime`` と ``xw:addedBy "machine"`` を付ける。
    項目に ``builtin`` の欄が無ければ（述語）、``xw:builtin true`` を付ける。"""
    g = rdflib.Graph()
    for section, typ, make in (
        ("relations", RELATION_ENTRY, relation_iri),
        ("predicates", PREDICATE_ENTRY, predicate_iri),
    ):
        for i, entry in enumerate(catalog.get(section, [])):
            top = make(entry["name"])
            g.add((top, RDF.type, typ))
            g.add((top, _ORDER, Literal(i, datatype=XSD.integer)))
            if "builtin" not in entry:
                g.add((top, _BUILTIN, Literal(True, datatype=XSD.boolean)))
            if at is not None:
                g.add((top, _PROV_AT, Literal(at, datatype=XSD.dateTime)))
                g.add((top, _ADDED_BY, Literal("machine")))
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
        out[section] = [_read_fields(data, s, specs) for s in tops]
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
    nt = catalog_triples(target, at=at).serialize(format="nt")
    if isinstance(nt, bytes):
        nt = nt.decode()
    await client.sparql_update(
        f"{_DELETE_BUILTIN} ;\nINSERT DATA {{ GRAPH <{CATALOG_GRAPH}> {{\n{nt}\n}} }}"
    )
    return True
