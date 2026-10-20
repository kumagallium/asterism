"""知識の走者 — 確定した知識の関係（alignment graph）に、閉じたテンプレートで答える。

「熱伝導率を下げるものは？」「熱伝導率の一部は？」「熱伝導率の一種は？」は、データの値を
読まずに、人が確定した関係の線から決定論で答えられる（design.md §15.2・§15.5）。

* 閉じたテンプレートは 3 つ — :func:`factors`・:func:`parts`・:func:`kinds`。SPARQL は
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

from asterism.crosswalk import XW
from asterism.crosswalk_runtime import ALIGN_RELATIONS, ALIGNMENT_GRAPH, _check_iri
from asterism.shared_vocab import SHARED_VOCAB_GRAPH, _bindings, _local

_PROV = "http://www.w3.org/ns/prov#"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

#: 知識の問いの種類（閉じた集合）。
OPS = ("factors", "parts", "kinds")
#: ``factors`` の向き。``affects`` は 3 述語すべて（「変える」は「下げる」「上げる」を含む）。
DIRECTIONS = ("lowers", "raises", "affects")
_DIRECTION_RELATIONS: dict[str, tuple[str, ...]] = {
    "lowers": ("lowers",),
    "raises": ("raises",),
    "affects": ("lowers", "raises", "affects"),
}


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
        "SELECT ?from ?rel ?via ?lbl ?vlbl ?who ?reason ?at ?batch "
        f"FROM NAMED <{ALIGNMENT_GRAPH}> FROM NAMED <{SHARED_VOCAB_GRAPH}> WHERE {{ "
        f"GRAPH <{ALIGNMENT_GRAPH}> {{ VALUES (?rel ?rn) {{ {pairs} }} {body} "
        "FILTER(isIRI(?from)) "
        f"OPTIONAL {{ ?a <{XW}alignSource> ?from ; <{XW}alignTarget> ?via ; "
        f"<{XW}alignRelation> ?rn . "
        f"OPTIONAL {{ ?a <{XW}draftedBy> ?who }} OPTIONAL {{ ?a <{XW}reason> ?reason }} "
        f"OPTIONAL {{ ?a <{_PROV}endedAtTime> ?at }} OPTIONAL {{ ?a <{XW}batch> ?batch }} }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?from <{_RDFS_LABEL}> ?lbl }} }} "
        f"OPTIONAL {{ GRAPH <{SHARED_VOCAB_GRAPH}> {{ ?via <{_RDFS_LABEL}> ?vlbl }} }} }}"
    )
    rel_by_iri = {ALIGN_RELATIONS[r]: r for r in relations}
    acc: dict[tuple[str, str, str], dict] = {}
    for b in await _bindings(client, query):
        key = (b["from"]["value"], rel_by_iri[b["rel"]["value"]], b["via"]["value"])
        d = acc.setdefault(
            key, {"labels": set(), "via_labels": set(), "meta": {}}
        )
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
        }
        for (iri, rel, via), d in acc.items()
    ]
    # 直接の線を先に、次にラベル（安定した順序 — 同じ問いは同じ並びで答える）
    items.sort(key=lambda r: (r["via"] != target_iri, r["label"], r["iri"], r["relation"]))
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
    """``spec = {"op": "factors|parts|kinds", "target": <IRI>, "direction": …}`` を検査して
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
        items = await (parts if op == "parts" else kinds)(client, target)
    return {"items": items, "count": len(items), "op": op, "target": target, "direction": direction}


__all__ = ["DIRECTIONS", "OPS", "factors", "kinds", "knowledge_answer", "parts"]
