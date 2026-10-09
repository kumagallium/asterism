"""「値でつなぐ網」— 全体グラフ（中間 B）の読み手。

公開済みの graph（:func:`asterism.substrate.canonical_graphs`）だけを読み、件・値・束・
ハブを点に、件どうしの線と件→値の線を決定論で返す（契約メモ contract_network_view.md §2）。
全部の件を点にすると星形の円になって読めないので、次の規則で網にする。

* 件 = IRI の主語・IRI の目的語（``xw:CrosswalkLink`` は点にしない）。
* 件どうしの線 = rdf:type 以外の IRI→IRI。PROV の述語は既定で外し、PROV の型だけを
  持つ件（取り込みの記録など）も既定では点にしない（``include_prov`` で入る）。
* 値の点 = graph ごと・述語ごとに文字の値を数え、値の種類 ≤ 50 かつ 使われた数 ÷ 値の
  種類 ≥ 3 の述語だけ、その値を点にする。id は「述語 IRI＋値の文字列」なので、同じ述語を
  使う別のデータセットの同じ値は同じ点になる。
* 束 = 同じ種類で、つながる相手（件・値の点）がまったく同じ件が 20 件以上。
* 線の数 0 の点は出さない。

分野の語は実装に書かない。LLM は呼ばない。同じ入力は同じ返り値。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from asterism.crosswalk import XW as _XW_NS
from asterism.prov_graph import PROV as _PROV_NS
from asterism.subject_tools import (
    _cell,
    _class_labels,
    _fallback_label,
    _label_lookup,
    _local_name,
    _predicate_names,
    _ref,
    _representative_kind,
    _rows,
)
from asterism.subjects import resolve_dataset_label
from asterism.substrate import (
    SupportsSparql,
    canonical_from_clauses,
    canonical_graphs,
    dataset_id_of_canonical_graph,
    is_hub_graph,
    ontology_graphs,
)

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_XW_LINK_CLASS = _XW_NS + "CrosswalkLink"

#: 値の点にする述語の条件（値の種類の上限・使われた数 ÷ 値の種類の下限）。
VALUE_MAX_KINDS = 50
VALUE_MIN_RATIO = 3
#: 束にする件数の下限。
BUNDLE_MIN = 20
DEFAULT_MAX_NODES = 5000
DEFAULT_MAX_ROWS = 200000

#: 名前や説明の述語は値の点にしない（1 件ごとに違う文字列で、分類ではない）。
_NAME_PREDICATES = frozenset(
    {
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://www.w3.org/2000/01/rdf-schema#comment",
        "http://schema.org/name",
        "https://schema.org/name",
        "http://schema.org/description",
        "https://schema.org/description",
        "http://purl.org/dc/terms/title",
        "http://purl.org/dc/terms/description",
        "http://www.w3.org/2004/02/skos/core#prefLabel",
        "http://www.w3.org/2004/02/skos/core#altLabel",
    }
)

_CHUNK = 300


def _is_prov(iri: str) -> bool:
    return iri.startswith(_PROV_NS)


def _value_id(predicate: str, value: str) -> str:
    return f"value:{predicate}\n{value}"


def _bundle_id(class_iri: str | None, signature: frozenset[tuple[str, str, str]]) -> str:
    body = (class_iri or "") + "\n" + "\n".join("\t".join(t) for t in sorted(signature))
    return "bundle:" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def _named_clauses(graphs: list[str]) -> str:
    return canonical_from_clauses(graphs, named=True)


def _empty() -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "kinds": [],
        "datasets": [],
        "stats": {
            "entities": 0,
            "nodes": 0,
            "edges": 0,
            "values": 0,
            "bundles": 0,
            "published_graphs": 0,
        },
        "truncated": False,
    }


async def network_view(
    client: SupportsSparql,
    *,
    registry_root: Path | str | None = None,
    include_prov: bool = False,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_rows: int = DEFAULT_MAX_ROWS,
    upper: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """「値でつなぐ網」を組み立てる（点・線・束の規則はモジュールの説明）。

    ``upper`` は上位構造の対応表（``asterism.shared_vocab.upper_map`` の返り値の形:
    ``{"classes": {種類: 最上位}, "properties": {述語: 最上位}}``）。渡されたときは、
    値の点を「最上位の項目＋値」で合流させ（別の述語でも同じ上位の項目なら同じ点）、
    件・束の ``group_iri``（色の鍵）を最上位の種類にする。表に無い IRI はそのまま
    （ADR global-network-view.md §上位構造との接続・upper-structure-shared-terms.md）。
    """
    class_top: Mapping[str, str] = (upper or {}).get("classes") or {}
    property_top: Mapping[str, str] = (upper or {}).get("properties") or {}
    graphs = await canonical_graphs(client)
    if not graphs:
        return _empty()
    truncated = False
    from_default = canonical_from_clauses(graphs)
    from_named = _named_clauses(graphs)

    # --- 1. 件どうしの線 -------------------------------------------------------
    prov_filter = "" if include_prov else f'FILTER(!STRSTARTS(STR(?p), "{_PROV_NS}")) '
    link_rows = _rows(
        await client.sparql_select(
            f"SELECT DISTINCT ?s ?p ?o\n{from_default}"
            f"WHERE {{ ?s ?p ?o FILTER(isIRI(?s) && isIRI(?o)) "
            f"FILTER(?p != {_ref(_RDF_TYPE)}) FILTER(?s != ?o) {prov_filter}}} "
            f"ORDER BY ?s ?p ?o LIMIT {max_rows + 1}"
        )
    )
    if len(link_rows) > max_rows:
        truncated = True
        link_rows = link_rows[:max_rows]
    links = [
        (s, p, o)
        for r in link_rows
        if (s := _cell(r, "s")) and (p := _cell(r, "p")) and (o := _cell(r, "o"))
    ]

    # --- 2. 型（種類・ハブ・データセット）--------------------------------------
    type_rows = _rows(
        await client.sparql_select(
            f"SELECT DISTINCT ?s ?c ?g\n{from_named}"
            "WHERE { GRAPH ?g { ?s a ?c } FILTER(isIRI(?s) && isIRI(?c)) } "
            f"ORDER BY ?s ?c ?g LIMIT {max_rows + 1}"
        )
    )
    types_cut = len(type_rows) > max_rows  # 型の行が切れた → 型が確かめられた IRI だけを点にする
    cut_subject: str | None = None
    if types_cut:
        truncated = True
        type_rows = type_rows[:max_rows]
        # 切れ目の主語は型の一部しか読めていない
        # （CrosswalkLink や PROV の型が、切れた後ろにあるかもしれない）
        cut_subject = _cell(type_rows[-1], "s") if type_rows else None
    types: dict[str, set[str]] = defaultdict(set)
    hub_types: dict[str, set[str]] = defaultdict(set)
    dataset_of: dict[str, str] = {}
    for r in type_rows:
        s, c, g = _cell(r, "s"), _cell(r, "c"), _cell(r, "g")
        if not (s and c and g):
            continue
        types[s].add(c)
        if is_hub_graph(g):
            if not _is_prov(c) and c != _XW_LINK_CLASS:
                hub_types[s].add(c)
        else:
            did = dataset_id_of_canonical_graph(g)
            if did is not None and s not in dataset_of:
                dataset_of[s] = did  # g の辞書順で最初

    def excluded(iri: str) -> bool:
        ts = types.get(iri)
        if not ts:
            return False
        if _XW_LINK_CLASS in ts:
            return True
        return not include_prov and all(_is_prov(t) for t in ts)

    # --- 3. 値の点 -------------------------------------------------------------
    stat_rows = _rows(
        await client.sparql_select(
            f"SELECT ?g ?p\n{from_named}"
            "WHERE { GRAPH ?g { ?s ?p ?o FILTER(isIRI(?s) && isLiteral(?o)) } } "
            "GROUP BY ?g ?p "
            f"HAVING (COUNT(DISTINCT STR(?o)) <= {VALUE_MAX_KINDS} "
            f"&& COUNT(*) >= {VALUE_MIN_RATIO} * COUNT(DISTINCT STR(?o))) "
            f"ORDER BY ?g ?p LIMIT {max_rows + 1}"
        )
    )
    if len(stat_rows) > max_rows:
        truncated = True
        stat_rows = stat_rows[:max_rows]
    pairs = [
        (g, p)
        for r in stat_rows
        if (g := _cell(r, "g"))
        and (p := _cell(r, "p"))
        and p not in _NAME_PREDICATES
        and p != _RDF_TYPE
        and (include_prov or not _is_prov(p))
    ]
    value_edges: list[tuple[str, str, str]] = []  # (件, 述語, 値の点 id)
    value_text: dict[str, tuple[str, str]] = {}  # 値の点 id → (述語, 値)
    # 値の点 id → (件, 元の述語, 元の文字列) の並び
    value_raw: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    budget = max_rows
    for i in range(0, len(pairs), _CHUNK):
        chunk = pairs[i : i + _CHUNK]
        values = " ".join(f"({_ref(g)} {_ref(p)})" for g, p in chunk)
        rows = _rows(
            await client.sparql_select(
                f"SELECT DISTINCT ?s ?p ?o\n{from_named}"
                f"WHERE {{ VALUES (?g ?p) {{ {values} }} "
                "GRAPH ?g { ?s ?p ?o FILTER(isIRI(?s) && isLiteral(?o)) } } "
                f"ORDER BY ?s ?p ?o LIMIT {budget + 1}"
            )
        )
        if len(rows) > budget:
            truncated = True
            rows = rows[:budget]
        budget -= len(rows)
        for r in rows:
            s, p, o = _cell(r, "s"), _cell(r, "p"), _cell(r, "o")
            if not (s and p and o is not None):
                continue
            text = o.strip()
            if not text:
                continue
            key_p = property_top.get(p, p)  # 上位の項目があればそれで合流する
            vid = _value_id(key_p, text)
            value_text[vid] = (key_p, text)
            value_raw[vid].append((s, p, o))
            value_edges.append((s, p, vid))
        if budget <= 0:
            if i + _CHUNK < len(pairs):
                truncated = True
            break

    # --- 4. 点と線をそろえる ----------------------------------------------------
    raw_edges: set[tuple[str, str, str]] = set()  # (source, target, 述語)

    def confirmed(iri: str) -> bool:
        return not types_cut or (iri in types and iri != cut_subject)

    for s, p, o in links:
        if confirmed(s) and confirmed(o) and not excluded(s) and not excluded(o):
            raw_edges.add((s, o, p))
    for s, p, vid in value_edges:
        if confirmed(s) and not excluded(s):
            raw_edges.add((s, vid, p))

    def is_value(nid: str) -> bool:
        return nid in value_text

    entity_ids = sorted({n for s, t, _ in raw_edges for n in (s, t) if not is_value(n)})
    kind_of = {e: _representative_kind(types[e]) if e in types else None for e in entity_ids}
    hub_ids = {e for e in entity_ids if e in hub_types}

    # --- 5. 束 -----------------------------------------------------------------
    neighbors: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for s, t, p in raw_edges:
        neighbors[s].add((t, p, "out"))
        if not is_value(t):
            neighbors[t].add((s, p, "in"))
    groups: dict[tuple[str | None, frozenset[tuple[str, str, str]]], list[str]] = defaultdict(list)
    for e in entity_ids:
        if e not in hub_ids and kind_of[e] is not None:
            groups[(kind_of[e], frozenset(neighbors[e]))].append(e)
    bundle_of: dict[str, str] = {}
    bundles: dict[str, dict[str, Any]] = {}
    for (kind, sig), members in sorted(groups.items(), key=lambda kv: kv[1][0]):
        if len(members) < BUNDLE_MIN:
            continue
        bid = _bundle_id(kind, sig)
        bundles[bid] = {"class_iri": kind, "members": members, "signature": sig}
        for m in members:
            bundle_of[m] = bid

    edge_set: set[tuple[str, str, str]] = set()
    for s, t, p in raw_edges:
        s2, t2 = bundle_of.get(s, s), bundle_of.get(t, t)
        if s2 != t2:
            edge_set.add((s2, t2, p))

    # --- 6. 点の一覧と上限 -------------------------------------------------------
    degree: dict[str, int] = defaultdict(int)
    for s, t, _ in edge_set:
        degree[s] += 1
        degree[t] += 1
    shown_entities = [e for e in entity_ids if e not in bundle_of and degree[e] > 0]
    fixed = (
        len([b for b in bundles if degree[b] > 0])
        + len([v for v in value_text if degree[v] > 0])
        + len([e for e in shown_entities if e in hub_ids])
    )
    plain = [e for e in shown_entities if e not in hub_ids]
    room = max(0, max_nodes - fixed)
    if len(plain) > room:
        truncated = True
        plain = sorted(plain, key=lambda e: (-degree[e], e))[:room]
    keep_entities = set(plain) | {e for e in shown_entities if e in hub_ids}
    live = (
        keep_entities
        | {b for b in bundles if degree[b] > 0}
        | {v for v in value_text if degree[v] > 0}
    )
    edge_set = {(s, t, p) for s, t, p in edge_set if s in live and t in live}
    degree = defaultdict(int)
    for s, t, _ in edge_set:
        degree[s] += 1
        degree[t] += 1
    live = {n for n in live if degree[n] > 0}
    keep_entities &= live
    if len(live) > max_nodes:  # 固定の点（ハブ・値・束）だけで上限を超えた分
        truncated = True
    total_entities = len(entity_ids)

    # --- 7. 名前 ---------------------------------------------------------------
    heading_graphs = sorted(set(graphs) | set(await ontology_graphs(client)))
    labels: dict[str, str] = {}
    to_label = sorted(keep_entities)
    for i in range(0, len(to_label), 500):
        labels.update(await _label_lookup(client, heading_graphs, set(to_label[i : i + 500])))

    predicates = {p for _, _, p in edge_set} | {p for p, _ in value_text.values()}
    sample: dict[str, list[str]] = defaultdict(list)
    name_rows: list[dict[str, dict[str, Any]]] = []
    for _s, t, p in sorted(edge_set):
        if is_value(t):
            continue
        if len(sample[p]) < 20:
            sample[p].append(t)
            name_rows.append({"p": {"type": "uri", "value": p}, "o": {"type": "uri", "value": t}})
    for p, _ in value_text.values():
        name_rows.append({"p": {"type": "uri", "value": p}, "o": {"type": "literal", "value": ""}})
    pred_names = await _predicate_names(client, graphs, registry_root, None, name_rows, predicates)

    def pname(p: str) -> str:
        return pred_names.get(p) or _fallback_label(p)

    class_iris: set[str] = set()
    class_iris |= {kind_of[e] for e in keep_entities if kind_of[e]}  # type: ignore[misc]
    class_iris |= {
        bundles[b]["class_iri"] for b in bundles if b in live and bundles[b]["class_iri"]
    }
    class_iris |= {sorted(hub_types[e])[0] for e in keep_entities if e in hub_ids}
    class_iris |= {class_top[c] for c in class_iris if c in class_top}  # 色の鍵の名前も引く
    class_label = await _class_labels(client, registry_root, class_iris)

    # --- 8. 返り値 --------------------------------------------------------------
    nodes: list[dict[str, Any]] = []
    kind_counts: dict[str, int] = defaultdict(int)
    kind_datasets: dict[str, set[str]] = defaultdict(set)
    # 値の点の持ち主: (種類, 件が実際に使った元の述語) ごとの数
    holders: dict[str, dict[tuple[str, str], int]] = defaultdict(lambda: defaultdict(int))
    for s, t, p0 in raw_edges:
        if is_value(t) and kind_of.get(s):
            holders[t][(kind_of[s], p0)] += 1  # type: ignore[index]

    for e in sorted(keep_entities):
        is_hub = e in hub_ids
        cls = sorted(hub_types[e])[0] if is_hub else kind_of[e]
        nodes.append(
            {
                "id": e,
                "kind": "hub" if is_hub else "entity",
                "label": labels.get(e) or _local_name(e),
                "class_iri": cls,
                "class_label": class_label.get(cls) if cls else None,
                # 色の鍵: 上位構造があれば最上位の種類（無ければ自分の種類）
                "group_iri": class_top.get(cls, cls) if cls else None,
                "dataset_id": None if is_hub else dataset_of.get(e),
                "count": 1,
                "degree": degree[e],
                "set_spec": None,
            }
        )
        if not is_hub and cls:
            kind_counts[class_top.get(cls, cls)] += 1
            if dataset_of.get(e):
                kind_datasets[class_top.get(cls, cls)].add(dataset_of[e])

    for vid in sorted(v for v in value_text if v in live):
        p, text = value_text[vid]
        spec = None
        if holders.get(vid):
            # 上位の述語で合流していても、一覧は件が実際に使う元の述語で引く
            (top, orig_p), _n = sorted(holders[vid].items(), key=lambda kv: (-kv[1], kv[0]))[0]
            # 一覧側の eq は完全一致。選んだ種類の件がその述語で持つ値（空白つき）で
            # 最も多いものを渡す（別の種類が空白違いの値を多く持っていても 0 件にしない）
            raw_counts: dict[str, int] = defaultdict(int)
            for s0, p1, o1 in value_raw[vid]:
                if p1 == orig_p and kind_of.get(s0) == top:
                    raw_counts[o1] += 1
            if not raw_counts:  # 念のため（持ち主の数え方と食い違ったとき）
                for _s0, p1, o1 in value_raw[vid]:
                    if p1 == orig_p:
                        raw_counts[o1] += 1
            raw = sorted(raw_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            spec = _spec(top, [{"property": orig_p, "op": "eq", "value": raw}])
        nodes.append(
            {
                "id": vid,
                "kind": "value",
                "label": f"{pname(p)}: {text}",
                "class_iri": None,
                "class_label": None,
                "dataset_id": None,
                "count": 1,
                "degree": degree[vid],
                "set_spec": spec,
            }
        )

    for bid in sorted(b for b in bundles if b in live):
        b = bundles[bid]
        cls = b["class_iri"]
        name = class_label.get(cls) if cls else None
        n = len(b["members"])
        outs = sorted(
            (nid, pred)
            for nid, pred, d in b["signature"]
            if d == "out" and not is_value(nid)
        )
        spec = None
        if cls and len(outs) == 1:
            spec = _spec(cls, [{"property": outs[0][1], "iri": outs[0][0]}])
        member_ds = {dataset_of[m] for m in b["members"] if m in dataset_of}
        nodes.append(
            {
                "id": bid,
                "kind": "bundle",
                # 件数の言い回し（「3,001 件」など）は画面が言語ごとに付ける
                "label": name or (_fallback_label(cls) if cls else ""),
                "class_iri": cls,
                "class_label": name,
                "group_iri": class_top.get(cls, cls) if cls else None,
                "dataset_id": next(iter(member_ds)) if len(member_ds) == 1 else None,
                "count": n,
                "degree": degree[bid],
                "set_spec": spec,
            }
        )
        if cls:
            kind_counts[class_top.get(cls, cls)] += n
            kind_datasets[class_top.get(cls, cls)] |= member_ds

    kinds = [
        {"class_iri": c, "class_label": class_label.get(c) or _fallback_label(c), "count": n,
         "dataset_ids": sorted(kind_datasets.get(c, ()))}
        for c, n in sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    edges = [
        {"source": s, "target": t, "label": pname(p)} for s, t, p in sorted(edge_set)
    ]
    n_values = len([n for n in nodes if n["kind"] == "value"])
    n_bundles = len([n for n in nodes if n["kind"] == "bundle"])
    used_ds = {n["dataset_id"] for n in nodes if n.get("dataset_id")}
    for k in kinds:
        used_ds |= set(k["dataset_ids"])
    datasets = [
        {"id": d, "label": resolve_dataset_label(registry_root, d)} for d in sorted(used_ds)
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "kinds": kinds,
        "datasets": datasets,
        "stats": {
            "entities": total_entities,
            "nodes": len(nodes),
            "edges": len(edges),
            "values": n_values,
            "bundles": n_bundles,
            "published_graphs": len(graphs),
        },
        "truncated": truncated,
    }


def _spec(class_iri: str, where: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "class": class_iri,
        "where": where,
        "order_by": None,
        "limit": 20,
        "source_scope": "all",
    }
