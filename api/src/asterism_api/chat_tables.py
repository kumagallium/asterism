"""「見せて」の表（ui-redesign PR 11d・§19）— 閉じたテンプレートだけ。AI は呼ばない。

種類 1 つの個体を 20 件、その項目の値と並べる。SPARQL は次の 4 本の**固定の形**で、利用者の文字列は
埋め込まない（種類の IRI は :func:`check_iri` を通ったものだけ、
個体の IRI はストアから読んだものだけ）。

1. 個体の一覧 — ``subjects/search`` の空 ``q`` + ``class_iri`` と同じ範囲（公開済みの版グラフ・
   ``?s a <種類>``）。
2. 個体の数（``total``）— 同じ範囲の ``COUNT(DISTINCT ?s)``。
3. 個体の名前 — ``subjects.LABEL_PREDICATES`` の優先順（``subjects.pick_label``）。
4. 値 — ``VALUES ?s { … }`` で ``?s ?p ?o``（``?p`` は IRI）。主語は先に（20 個まで）決め、その
   主語の値だけを引く。``LIMIT`` は 主語の数 × :data:`MAX_CELLS_PER_ROW`。値が公開済みのグラフの
   主語の IRI なら、その名前（:func:`resolve_cited` と同じ引き方）に置き換え、セルは
   ``{"text": 名前, "iri": IRI}``（文字列の値は ``{"text"}`` だけ）。

項目の名前は ``shared_vocab._display_labels``（ontology graph と共有の語の ``rdfs:label``・無ければ
IRI の末尾）、種類の名前は ``class_schema.class_labels``。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from asterism import shared_vocab, sky_view, subject_tools, subjects, substrate
from asterism.class_schema import class_labels

__all__ = [
    "MAX_CELLS",
    "MAX_CELLS_PER_ROW",
    "MAX_COLUMNS",
    "check_iri",
    "kind_for_term",
    "resolve_cited",
    "table_for_kind",
]

MAX_ROWS = 20
#: 1 個体あたりに引く値（行）の上限。値の問い合わせの ``LIMIT`` は 個体の数 × この数 — 全体の
#: 上限で打ち切って、途中の個体の値が欠けることがないように、打ち切りは個体の単位にする。
MAX_CELLS_PER_ROW = 100
MAX_CELLS = MAX_ROWS * MAX_CELLS_PER_ROW
MAX_COLUMNS = 12
_MAX_LINKED = 500  # 値が別の個体の IRI のとき、名前を引く IRI の上限
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
#: ``questions_routes._IRI_RE`` と同じ形（絶対 http(s) IRI・空白と SPARQL の記号を含まない）
_IRI_RE = re.compile(r'^https?://[^\s<>"{}|\\^`]+$', re.IGNORECASE)


def check_iri(iri: str) -> str:
    """検査済みの IRI を返す。形が違えば :class:`ValueError`。"""
    if not isinstance(iri, str) or not _IRI_RE.match(iri):
        raise ValueError(f"not an absolute IRI: {iri!r}")
    return iri


def _rows(raw: Any) -> list[dict]:
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return list(results.get("bindings", []) if isinstance(results, dict) else [])


def _v(row: dict, var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


async def _published_graphs(client: Any, root: Path) -> list[str]:
    """公開済みの版グラフ（``subjects/search`` と同じ ``canonical_graphs`` を、登録で公開済みの
    データセットに絞る）。"""
    ids = {d["id"] for d in sky_view._published_datasets(root)}
    graphs = await substrate.canonical_graphs(client)
    return [g for g in graphs if substrate.dataset_id_of_canonical_graph(g) in ids]


def kind_for_term(sky: dict, term_iri: str) -> str | None:
    """用語に入っている種類（``sky`` の ``terms[].members`` のうち、公開済みの種類であるもの）の
    最初の 1 つ。無ければ ``None``。"""
    kinds = {k["iri"] for d in sky.get("datasets") or [] for k in d.get("kinds") or []}
    term = next((t for t in sky.get("terms") or [] if t.get("iri") == term_iri), None)
    for m in (term or {}).get("members") or []:
        if m.get("iri") in kinds:
            return str(m["iri"])
    return None


async def table_for_kind(
    client: Any,
    root: Path | str,
    kind_iri: str,
    *,
    limit: int = 20,
    lang: str = "ja",
    graphs: list[str] | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """種類 1 つの表 → ``{kind, columns, rows, total, truncated}``（形は契約 A2）。

    ``graphs`` を渡すと、公開済みの版グラフの代わりにその graph だけを読む（中身の表が、
    1 つのデータセットや、公開前の版グラフを読むため）。既定（``None``）は公開済み全部。

    ``names``（IRI → 名前）は登録の設計が付けた名前で、種類と項目の名前をストアの名前より先に使う
    （公開前の graph にはまだ名前の triple が無いため）。既定は使わない。"""
    check_iri(kind_iri)
    clause = subjects.class_type_clause(kind_iri)
    if clause is None:
        raise ValueError(f"invalid class_iri: {kind_iri!r}")
    root = Path(root)
    labels = await class_labels(client, root, {kind_iri})
    out: dict[str, Any] = {
        "kind": {
            "iri": kind_iri,
            "label": (names or {}).get(kind_iri) or labels.get(kind_iri) or kind_iri,
        },
        "columns": [],
        "rows": [],
        "total": 0,
        "truncated": False,
    }
    if graphs is None:
        graphs = await _published_graphs(client, root)
    if not graphs:
        return out
    named = substrate.canonical_from_clauses(graphs, named=True)
    count = _rows(
        await client.sparql_select(
            f"SELECT (COUNT(DISTINCT ?s) AS ?__n)\n{named}"
            f"WHERE {{ GRAPH ?g {{ {clause}FILTER(isIRI(?s)) }} }}"
        )
    )
    total = int(_v(count[0], "__n") or 0) if count else 0
    out["total"] = total
    if not total:
        return out
    listed = _rows(
        await client.sparql_select(
            f"SELECT DISTINCT ?s\n{named}"
            f"WHERE {{ GRAPH ?g {{ {clause}FILTER(isIRI(?s)) }} }} "
            f"ORDER BY ?s LIMIT {min(int(limit), MAX_ROWS)}"
        )
    )
    subjects_iris = [s for s in (_v(r, "s") for r in listed) if s and _IRI_RE.match(s)]
    if not subjects_iris:
        return out
    values = " ".join(f"<{s}>" for s in subjects_iris)
    pairs = " ".join(f"(<{p}> {i})" for i, p in enumerate(subjects.LABEL_PREDICATES))
    name_rows = _rows(
        await client.sparql_select(
            f"SELECT ?s ?label ?__rank (LANG(?label) AS ?__lang)\n{named}"
            f"WHERE {{ GRAPH ?g {{ VALUES ?s {{ {values} }} "
            f"VALUES (?__lp ?__rank) {{ {pairs} }} ?s ?__lp ?label }} }}"
        )
    )
    cands: dict[str, list[tuple[str | None, int | None, str | None]]] = {}
    for r in name_rows:
        s = _v(r, "s")
        if s is None:
            continue
        rank = _v(r, "__rank")
        cands.setdefault(s, []).append(
            (_v(r, "label"), int(rank) if rank is not None else None, _v(r, "__lang"))
        )
    cell_rows = _rows(
        await client.sparql_select(
            f"SELECT ?s ?p ?o\n{named}"
            f"WHERE {{ GRAPH ?g {{ VALUES ?s {{ {values} }} ?s ?p ?o "
            f"FILTER(isIRI(?p) && ?p != <{_RDF_TYPE}>) }} }} ORDER BY ?s ?p ?o "
            f"LIMIT {len(subjects_iris) * MAX_CELLS_PER_ROW}"
        )
    )
    skip = set(subjects.LABEL_PREDICATES)
    #: 主語 → 項目 → [(値の文字列, 値が IRI ならその IRI)]
    cells: dict[str, dict[str, list[tuple[str, str | None]]]] = {s: {} for s in subjects_iris}
    used: dict[str, int] = {}
    for r in cell_rows:
        s, p, v = _v(r, "s"), _v(r, "p"), _v(r, "o")
        if s not in cells or not p or v is None or p in skip or not _IRI_RE.match(p):
            continue
        is_iri = (r.get("o") or {}).get("type") in ("uri", "iri") and bool(_IRI_RE.match(v))
        slot = cells[s].setdefault(p, [])
        if all(v != x for x, _ in slot):
            slot.append((v, v if is_iri else None))
        used[p] = used.get(p, 0) + 1
    props = sorted(used, key=lambda p: (-used[p], p))[:MAX_COLUMNS]
    linked = sorted(
        {i for s in cells for p in props for _, i in cells[s].get(p, []) if i is not None}
    )[:_MAX_LINKED]
    names_of: dict[str, str] = {}
    if linked:
        for i, info in (await resolve_cited(client, root, linked)).items():
            names_of[i] = info.get("label") or subject_tools._local_name(i)
    prop_names = await shared_vocab._display_labels(client, props)
    out["columns"] = [
        {"iri": p, "label": (names or {}).get(p) or prop_names.get(p) or p} for p in props
    ]

    def cell(items: list[tuple[str, str | None]]) -> dict[str, str]:
        texts = [names_of.get(i, v) if i is not None else v for v, i in items]
        only = items[0][1] if len(items) == 1 else None
        out_cell = {"text": " / ".join(texts)}
        if only is not None and only in names_of:
            out_cell["iri"] = only
        return out_cell

    out["rows"] = [
        {
            "iri": s,
            "label": subjects.pick_label(cands.get(s) or []) or subject_tools._local_name(s),
            "cells": {p: cell(cells[s][p]) for p in props if p in cells[s]},
        }
        for s in subjects_iris
    ]
    cap = len(subjects_iris) * MAX_CELLS_PER_ROW
    out["truncated"] = total > len(subjects_iris) or len(cell_rows) >= cap
    return out


async def resolve_cited(
    client: Any, root: Path | str, iris: list[str]
) -> dict[str, dict[str, str]]:
    """Ask の出典の個体 → ``{iri: {label, dataset_id, dataset_label}}``（引けたものだけ）。"""
    safe = sorted({i for i in iris if isinstance(i, str) and _IRI_RE.match(i)})
    if not safe:
        return {}
    graphs = await substrate.canonical_graphs(client)
    if not graphs:
        return {}
    named = substrate.canonical_from_clauses(graphs, named=True)
    values = " ".join(f"<{i}>" for i in safe)
    pairs = " ".join(f"(<{p}> {i})" for i, p in enumerate(subjects.LABEL_PREDICATES))
    rows = _rows(
        await client.sparql_select(
            f"SELECT ?s ?g ?label ?__rank (LANG(?label) AS ?__lang)\n{named}"
            f"WHERE {{ GRAPH ?g {{ VALUES ?s {{ {values} }} ?s ?__p ?__o "
            f"OPTIONAL {{ VALUES (?__lp ?__rank) {{ {pairs} }} ?s ?__lp ?label }} }} }} LIMIT 2000"
        )
    )
    cands: dict[str, list[tuple[str | None, int | None, str | None]]] = {}
    graph_of: dict[str, str] = {}
    for r in rows:
        s = _v(r, "s")
        if s is None:
            continue
        graph_of.setdefault(s, _v(r, "g") or "")
        rank = _v(r, "__rank")
        cands.setdefault(s, []).append(
            (_v(r, "label"), int(rank) if rank is not None else None, _v(r, "__lang"))
        )
    out: dict[str, dict[str, str]] = {}
    for s, g in graph_of.items():
        ds = substrate.dataset_id_of_canonical_graph(g) if g else None
        out[s] = {
            "label": subjects.pick_label(cands.get(s) or []) or "",
            "dataset_id": ds or "",
            "dataset_label": subjects.resolve_dataset_label(Path(root), ds) if ds else "",
        }
    return out
