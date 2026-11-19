"""知識の走者 — 確定した知識の関係（alignment graph）に、閉じたテンプレートで答える。

「熱伝導率を下げるものは？」「熱伝導率の一部は？」「熱伝導率の一種は？」は、データの値を
読まずに、人が確定した関係の線から決定論で答えられる（design.md §15.2・§15.5）。

* 閉じたテンプレートは 13 個 — :func:`factors`・:func:`parts`・:func:`kinds` と、14a-2 で
  足した 10 個（作る・測る・変わる・競合する・物性。:data:`LINE_OPS`・:func:`missing_properties`）。
  新しい型は直接の線だけを引く（閉包なし）。SPARQL は
  この関数の中の固定文字列で、引数は IRI だけ（:func:`~asterism.crosswalk_runtime._check_iri`）。
  AI は SPARQL も IRI も書かない。
* 読む graph は alignment graph（線と来歴）と ``vocab/shared``（ラベル）だけ。自前で
  ``FROM NAMED`` を書き、データの graph は読まない。公開済みのデータセットが 0 件でも動く
  （``canonical_merge_query`` を通さない）。
* 閉包は 1 段だけ — ``factors(X, 下げる)`` は X を直接「下げる」もの ∪ X の一部（``isPartOf``）を
  「下げる」もの。``affects``（変える）は ``lowers`` と ``raises`` と ``affects`` の 3 述語すべて。
* 各項目に「誰が（``drafted_by``）・いつ（``at``）・何をまとめて（``batch``）・なぜ
  （``reason``）」決めたかが付く（来歴）。確定した線だけを読む — 下書きは alignment graph に
  無いので出ない。
"""

from __future__ import annotations

from typing import Any

from asterism import relation_catalog
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import ALIGN_RELATIONS, ALIGNMENT_GRAPH, _check_iri
from asterism.shared_vocab import SHARED_VOCAB_GRAPH, _bindings, _local

_PROV = "http://www.w3.org/ns/prov#"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

#: 知識の問いの種類（閉じた集合）。
OPS = (
    "factors",
    "parts",
    "kinds",
    "makers",
    "products",
    "measurers",
    "measured",
    "sources",
    "outcomes",
    "competitors",
    "properties",
    "bearers",
    "missing",
)
#: 14a-2 で足した型 → ``(確定側の関係, X の位置)``。位置は ``to``（X が線の to）・``from``（X が
#: 線の from）・``either``（向きなしの線。どちらの端でも）。``missing`` は線を 1 本ずつ
#: 引かないので載せない。
LINE_OPS: dict[str, tuple[tuple[str, ...], str]] = {
    "makers": (("makes",), "to"),
    "products": (("makes",), "from"),
    "measurers": (("measuresMaterial", "measuresProperty"), "to"),
    "measured": (("measuresMaterial", "measuresProperty"), "from"),
    "sources": (("becomes",), "to"),
    "outcomes": (("becomes",), "from"),
    "competitors": (("competesWith",), "either"),
    "properties": (("hasProperty",), "from"),
    "bearers": (("hasProperty",), "to"),
}
#: ``missing`` — X 以外のこの数以上の語が持つ項目だけを「まだ無い物性」とする。
MISSING_MIN_OTHERS = 2
#: ``missing`` の行に添える「ほかの語」の最大件数（全数は ``others_count``）。
MISSING_OTHERS_MAX = 5
#: ``factors`` の向き。``affects`` は 3 述語すべて（「変える」は「下げる」「上げる」を含む）。
DIRECTIONS = relation_catalog.factor_relations()
#: 向き → 数える述語（確定側の名前は下書きの名前と同じ・``factor_directions`` の逆引き）。
_DIRECTION_RELATIONS: dict[str, tuple[str, ...]] = relation_catalog.direction_relations()


def _label_rank(lang: str) -> int:
    lang = (lang or "").lower()
    return 0 if lang.startswith("ja") else 1 if not lang else 2


def _best(labels: set[tuple[int, str]], iri: str) -> str:
    return min(labels)[1] if labels else _local(iri)


async def _lines_to(
    client: Any, target_iri: str, relations: tuple[str, ...], *, via_parts: bool
) -> list[dict]:
    """``target_iri`` を ``to`` に持つ ``relations`` の線の ``from`` を、来歴つきで返す。
    ``via_parts`` なら target の一部（``isPartOf`` の from）を ``to`` に持つ線も 1 段だけ足す。
    内部用 — SPARQL はここ 1 か所だけ（引数は検査済みの IRI と固定の述語）。"""
    _check_iri(target_iri)
    pairs = " ".join(f'(<{ALIGN_RELATIONS[r]}> "{r}")' for r in relations)
    direct = f"{{ ?from ?rel <{target_iri}> . BIND(<{target_iri}> AS ?via) }}"
    part = f"{{ ?via <{ALIGN_RELATIONS['isPartOf']}> <{target_iri}> . ?from ?rel ?via }}"
    body = f"{direct} UNION {part}" if via_parts else direct
    query = (
        "SELECT ?from ?rel ?via ?lbl ?vlbl ?who ?reason ?at ?batch ?ev "
        f"FROM NAMED <{ALIGNMENT_GRAPH}> FROM NAMED <{SHARED_VOCAB_GRAPH}> WHERE {{ "
        f"GRAPH <{ALIGNMENT_GRAPH}> {{ VALUES (?rel ?rn) {{ {pairs} }} {body} "
        "FILTER(isIRI(?from)) "
        f"OPTIONAL {{ ?a <{XW}alignSource> ?from ; <{XW}alignTarget> ?via ; "
        f"<{XW}alignRelation> ?rn . "
        f"OPTIONAL {{ ?a <{XW}draftedBy> ?who }} OPTIONAL {{ ?a <{XW}reason> ?reason }} "
        f"OPTIONAL {{ ?a <{_PROV}endedAtTime> ?at }} OPTIONAL {{ ?a <{XW}batch> ?batch }} "
        f"OPTIONAL {{ ?a <{_PROV}wasDerivedFrom> ?ev }} }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?from <{_RDFS_LABEL}> ?lbl }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?via <{_RDFS_LABEL}> ?vlbl }} }} }}"
    )
    rel_by_iri = {ALIGN_RELATIONS[r]: r for r in relations}
    acc: dict[tuple[str, str, str], dict] = {}
    for b in await _bindings(client, query):
        key = (b["from"]["value"], rel_by_iri[b["rel"]["value"]], b["via"]["value"])
        d = acc.setdefault(
            key, {"labels": set(), "via_labels": set(), "meta": {}, "evidence": set()}
        )
        if b.get("ev"):  # 論文の根拠の文（12b・prov:wasDerivedFrom）
            d["evidence"].add(str(b["ev"]["value"]))
        for var, bag in (("lbl", "labels"), ("vlbl", "via_labels")):
            cell = b.get(var)
            if cell:
                d[bag].add((_label_rank(str(cell.get("xml:lang") or "")), str(cell["value"])))
        for var in ("who", "reason", "at", "batch"):
            cell = b.get(var)
            if cell and var not in d["meta"]:
                d["meta"][var] = str(cell["value"])
    items = [
        {
            "iri": iri,
            "label": _best(d["labels"], iri),
            "relation": rel,
            "via": via,
            "via_label": _best(d["via_labels"], via),
            "reason": d["meta"].get("reason"),
            "drafted_by": d["meta"].get("who"),
            "at": d["meta"].get("at"),
            "batch": d["meta"].get("batch"),
            "evidence": sorted(d["evidence"]),  # 重複なし・IRI 順（同じ問いは同じ並び）
        }
        for (iri, rel, via), d in acc.items()
    ]
    # 直接の線を先に、次にラベル（安定した順序 — 同じ問いは同じ並びで答える）
    items.sort(key=lambda r: (r["via"] != target_iri, r["label"], r["iri"], r["relation"]))
    return items


async def _lines_at(
    client: Any, x_iri: str, relations: tuple[str, ...], *, side: str
) -> list[dict]:
    """``x_iri`` が線の ``from``（``side="from"``）か、どちらかの端（``"either"``）にある
    ``relations`` の線の、X でないほうの端を来歴つきで返す。行の形は :func:`_lines_to` と同じ
    （``iri``／``label`` = 相手・``via``／``via_label`` = X）。内部用 — 引数は検査済みの IRI と
    固定の述語だけ。"""
    _check_iri(x_iri)
    pairs = " ".join(f'(<{ALIGN_RELATIONS[r]}> "{r}")' for r in relations)
    out_branch = f"{{ <{x_iri}> ?rel ?other . BIND(<{x_iri}> AS ?s) BIND(?other AS ?o) }}"
    in_branch = f"{{ ?other ?rel <{x_iri}> . BIND(?other AS ?s) BIND(<{x_iri}> AS ?o) }}"
    body = f"{out_branch} UNION {in_branch}" if side == "either" else out_branch
    query = (
        "SELECT ?other ?rel ?lbl ?vlbl ?who ?reason ?at ?batch ?ev "
        f"FROM NAMED <{ALIGNMENT_GRAPH}> FROM NAMED <{SHARED_VOCAB_GRAPH}> WHERE {{ "
        f"GRAPH <{ALIGNMENT_GRAPH}> {{ VALUES (?rel ?rn) {{ {pairs} }} {body} "
        "FILTER(isIRI(?other)) "
        f"OPTIONAL {{ ?a <{XW}alignSource> ?s ; <{XW}alignTarget> ?o ; "
        f"<{XW}alignRelation> ?rn . "
        f"OPTIONAL {{ ?a <{XW}draftedBy> ?who }} OPTIONAL {{ ?a <{XW}reason> ?reason }} "
        f"OPTIONAL {{ ?a <{_PROV}endedAtTime> ?at }} OPTIONAL {{ ?a <{XW}batch> ?batch }} "
        f"OPTIONAL {{ ?a <{_PROV}wasDerivedFrom> ?ev }} }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?other <{_RDFS_LABEL}> ?lbl }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ <{x_iri}> <{_RDFS_LABEL}> ?vlbl }} }} }}"
    )
    rel_by_iri = {ALIGN_RELATIONS[r]: r for r in relations}
    acc: dict[tuple[str, str], dict] = {}
    for b in await _bindings(client, query):
        key = (b["other"]["value"], rel_by_iri[b["rel"]["value"]])
        d = acc.setdefault(
            key, {"labels": set(), "via_labels": set(), "meta": {}, "evidence": set()}
        )
        if b.get("ev"):
            d["evidence"].add(str(b["ev"]["value"]))
        for var, bag in (("lbl", "labels"), ("vlbl", "via_labels")):
            cell = b.get(var)
            if cell:
                d[bag].add((_label_rank(str(cell.get("xml:lang") or "")), str(cell["value"])))
        for var in ("who", "reason", "at", "batch"):
            cell = b.get(var)
            if cell and var not in d["meta"]:
                d["meta"][var] = str(cell["value"])
    items = [
        {
            "iri": iri,
            "label": _best(d["labels"], iri),
            "relation": rel,
            "via": x_iri,
            "via_label": _best(d["via_labels"], x_iri),
            "reason": d["meta"].get("reason"),
            "drafted_by": d["meta"].get("who"),
            "at": d["meta"].get("at"),
            "batch": d["meta"].get("batch"),
            "evidence": sorted(d["evidence"]),
        }
        for (iri, rel), d in acc.items()
    ]
    items.sort(key=lambda r: (r["label"], r["iri"], r["relation"]))
    return items


async def missing_properties(client: Any, target_iri: str) -> list[dict]:
    """``target_iri`` が ``hasProperty`` を持たない項目 P のうち、X 以外の
    :data:`MISSING_MIN_OTHERS` 件以上の語が ``hasProperty`` で持つもの（まだ無い物性）。
    確定した線だけを読む。行は ``iri``／``label`` = P・``relation`` = ``hasProperty``・
    ``via``／``via_label`` = X・``reason``／``drafted_by``／``at``／``batch`` = ``None``・
    ``evidence`` = ``[]``・``others`` = ``[{iri, label}]``（P を持つほかの語。ラベル順・最大
    :data:`MISSING_OTHERS_MAX`）・``others_count``（全数）。並びは ``others_count`` の多い順 →
    ラベル → IRI。"""
    _check_iri(target_iri)
    query = (
        "SELECT ?from ?p ?fl ?pl ?xl "
        f"FROM NAMED <{ALIGNMENT_GRAPH}> FROM NAMED <{SHARED_VOCAB_GRAPH}> WHERE {{ "
        f"GRAPH <{ALIGNMENT_GRAPH}> {{ ?from <{ALIGN_RELATIONS['hasProperty']}> ?p . "
        "FILTER(isIRI(?from) && isIRI(?p)) } "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?from <{_RDFS_LABEL}> ?fl }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?p <{_RDFS_LABEL}> ?pl }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ <{target_iri}> <{_RDFS_LABEL}> ?xl }} }} }}"
    )
    labels: dict[str, set[tuple[int, str]]] = {}
    owners: dict[str, set[str]] = {}
    for b in await _bindings(client, query):
        frm, p = b["from"]["value"], b["p"]["value"]
        owners.setdefault(p, set()).add(frm)
        for var, iri in (("fl", frm), ("pl", p), ("xl", target_iri)):  # xl = X 自身（線が無くても）
            cell = b.get(var)
            if cell:
                labels.setdefault(iri, set()).add(
                    (_label_rank(str(cell.get("xml:lang") or "")), str(cell["value"]))
                )
    x_label = _best(labels.get(target_iri, set()), target_iri)
    items: list[dict] = []
    for p, froms in owners.items():
        others = froms - {target_iri}
        if target_iri in froms or len(others) < MISSING_MIN_OTHERS:
            continue
        ranked = sorted(
            ({"iri": o, "label": _best(labels.get(o, set()), o)} for o in others),
            key=lambda r: (r["label"], r["iri"]),
        )
        items.append(
            {
                "iri": p,
                "label": _best(labels.get(p, set()), p),
                "relation": "hasProperty",
                "via": target_iri,
                "via_label": x_label,
                "reason": None,
                "drafted_by": None,
                "at": None,
                "batch": None,
                "evidence": [],
                "others": ranked[:MISSING_OTHERS_MAX],
                "others_count": len(others),
            }
        )
    items.sort(key=lambda r: (-r["others_count"], r["label"], r["iri"]))
    return items


async def factors(client: Any, target_iri: str, direction: str) -> list[dict]:
    """``target_iri`` を下げる／上げる／変えるもの（``direction`` = ``lowers`` | ``raises`` |
    ``affects``）。閉包は 1 段: target を直接持つ from ∪ target の ``isPartOf`` の from を
    to に持つ from。``affects`` は 3 述語すべて。

    返り値 ``[{iri, label, relation, via, via_label, reason, drafted_by, at, batch}]``。
    ``via`` は線の to — target 自身か、target の一部。ラベルは ``vocab/shared`` の
    ``rdfs:label``（日本語優先）、無ければ IRI の末尾。"""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}: {direction!r}")
    return await _lines_to(client, target_iri, _DIRECTION_RELATIONS[direction], via_parts=True)


async def parts(client: Any, target_iri: str) -> list[dict]:
    """``target_iri`` の一部（``isPartOf`` の直接の from）。行の形は :func:`factors` と同じ
    （``via`` は target・``relation`` は ``isPartOf``）。"""
    return await _lines_to(client, target_iri, ("isPartOf",), via_parts=False)


async def kinds(client: Any, target_iri: str) -> list[dict]:
    """``target_iri`` の一種（``subClassOf`` / ``subPropertyOf`` の直接の from）。行の形は
    :func:`factors` と同じ。"""
    return await _lines_to(client, target_iri, ("subClassOf", "subPropertyOf"), via_parts=False)


async def knowledge_answer(client: Any, spec: dict) -> dict:
    """``spec = {"op": <:data:`OPS` のどれか>, "target": <IRI>, "direction": …}`` を検査して
    閉じたテンプレートで答える。``direction`` は ``factors`` だけが使う（``lowers`` |
    ``raises`` | ``affects``）。戻り値 ``{"items", "count", "op", "target", "direction"}``。

    ``ValueError`` — op が閉集合に無い・target が絶対 IRI でない・direction が不正。"""
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    op, target = spec.get("op"), spec.get("target")
    if op not in OPS:
        raise ValueError(f"op must be one of {OPS}: {op!r}")
    if not isinstance(target, str):
        raise ValueError("target must be an IRI")
    _check_iri(target)
    direction = spec.get("direction")
    if op == "factors":
        items = await factors(client, target, str(direction))
    else:
        direction = None
        if op == "parts":
            items = await parts(client, target)
        elif op == "kinds":
            items = await kinds(client, target)
        elif op == "missing":
            items = await missing_properties(client, target)
        else:
            relations, side = LINE_OPS[op]
            if side == "to":
                items = await _lines_to(client, target, relations, via_parts=False)
            else:
                items = await _lines_at(client, target, relations, side=side)
    return {"items": items, "count": len(items), "op": op, "target": target, "direction": direction}


def item_sentence(op: str | None, item: dict, lang: str = "ja") -> str | None:
    """答えの行 → 線の向きどおりの 1 文（決定論）。14a-2 の新しい型（:data:`LINE_OPS` と
    ``missing``）だけ。既存の 3 つの型・文にできない行は ``None``。

    文は ``graph_diff.REL_SENTENCE``（``PRED_TO_REL`` で述語を関係名に）。X が to の型は相手が
    主語・X が from の型と競合は X が先。``missing`` は
    「P（ほかの n 件にある: A・B）」。"""
    from asterism import graph_diff  # 循環 import を避ける

    lg = "en" if lang == "en" else "ja"
    if op == "missing":
        name = str(item.get("label") or "")
        others = [str(o.get("label") or "") for o in item.get("others") or []]
        n = int(item.get("others_count") or len(others))
        if lg == "en":
            return f"{name} (recorded for {n} others: {', '.join(others)})"
        return f"{name}（ほかの {n} 件にある: {'・'.join(others)}）"  # noqa: RUF001
    spec = LINE_OPS.get(str(op))
    if spec is None:
        return None
    rel = graph_diff.PRED_TO_REL.get(str(item.get("relation")))
    if rel is None:
        return None
    other, x = str(item.get("label") or ""), str(item.get("via_label") or "")
    s, o = (other, x) if spec[1] == "to" else (x, other)
    return graph_diff.REL_SENTENCE[lg][rel].format(s=s, o=o)


__all__ = [
    "DIRECTIONS",
    "LINE_OPS",
    "MISSING_MIN_OTHERS",
    "MISSING_OTHERS_MAX",
    "OPS",
    "factors",
    "item_sentence",
    "kinds",
    "knowledge_answer",
    "missing_properties",
    "parts",
]
