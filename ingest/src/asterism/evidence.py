"""論文の根拠の文をストアから引く 1 か所（ui-redesign PR 12c・design.md §20）。

確定した関係の来歴 ``prov:wasDerivedFrom <文 IRI>`` を、画面・回答の出典に出せる形
（文・節・論文名）にする。引用の文字列は必ずここでストアから引く（AI の出力は使わない）。

* 見る graph は registry の**公開済みの論文**（``meta.source_kind == "xml"``）だけ。未公開
  （下書きの版 graph）の文は引かない — 確定した関係の根拠は公開済みの論文にしかない。
* SPARQL は 1 回。IRI は :data:`asterism.themes._EVIDENCE_IRI` の形を通ったものだけを
  ``VALUES`` に入れる（外からの文字列を SPARQL に入れない）。
* 引けない IRI は返さない（呼び手は黙って省く）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from asterism import documents as doc
from asterism import substrate
from asterism.subjects import resolve_dataset_label, valid_dataset_id
from asterism.themes import _EVIDENCE_IRI

__all__ = ["TEXT_MAX", "cut", "order_by_paper", "paper_count", "resolve_evidence"]

#: 札に出す文の長さ（超えたら「…」で切る・全文は ``full``）
TEXT_MAX = 150
_UNPUBLISHED_STATUS = ("retracted", "deleted")


def _paper_graphs(registry_root: Path) -> dict[str, tuple[str, str]]:
    """公開済みの論文の ``{graph IRI: (dataset_id, 論文名)}``。graph の解決は
    ``live_graph`` → ``graph_iri`` → 鍵の graph。``chat_paper.read_sentences``（下書きを読む）は
    ``graph_iri`` が先だが、公開済みの論文に作り直し中の版 graph（``graph_iri``）があっても、
    確定した関係の根拠は公開中の版（``live_graph``）から引く。"""
    out: dict[str, tuple[str, str]] = {}
    if not registry_root.is_dir():
        return out
    for child in sorted(registry_root.iterdir()):
        did = valid_dataset_id(child.name) if child.is_dir() else None
        if did is None:
            continue
        try:
            meta = json.loads((child / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or not meta.get("promoted"):
            continue
        if meta.get("status") in _UNPUBLISHED_STATUS or meta.get("is_shared_vocab"):
            continue
        if str(meta.get("source_kind") or "csv") != "xml":
            continue
        graph = (
            meta.get("live_graph") or meta.get("graph_iri") or substrate.canonical_graph_iri(did)
        )
        if not _EVIDENCE_IRI.fullmatch(str(graph)):
            continue
        out[str(graph)] = (did, resolve_dataset_label(registry_root, did))
    return out


def cut(text: str) -> str:
    """150 字で切る（超えたら末尾に「…」）。"""
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX].rstrip() + "…"


def _paper_key(item: dict) -> str:
    return str(item.get("dataset_id") or item.get("dataset_label") or "")


def paper_count(items: list[dict]) -> int:
    """根拠の文がまたがる論文の数（論文が分からない文は数えない）。"""
    return len({k for k in (_paper_key(i) for i in items) if k})


def order_by_paper(items: list[dict], limit: int | None = None) -> list[dict]:
    """根拠の並び（13a）: 論文ごとに 1 件ずつ先に（論文名の順）、残りは元の順でそのあと。
    ``limit`` があれば先頭から切る — 上限があるときも、なるべく別の論文から選ばれる。
    論文の分からない文は 1 つの組として扱う。"""
    firsts: dict[str, dict] = {}
    for it in items:
        firsts.setdefault(_paper_key(it), it)
    head = sorted(
        firsts.values(), key=lambda it: (str(it.get("dataset_label") or ""), _paper_key(it))
    )
    rest = [it for it in items if not any(it is h for h in head)]
    out = [*head, *rest]
    return out if limit is None else out[:limit]


async def resolve_evidence(
    client: Any, iris: Iterable[str], *, registry_root: Path | str | None
) -> dict[str, dict]:
    """文 IRI → ``{iri, text, full, section, path, dataset_id, dataset_label}``。

    ``text`` は 150 字で切る（末尾「…」）・``full`` は全文・``section`` は節の題名（無ければ
    ``None``）・``path`` は ``lit:structuralPath``（無ければ ``None``）。引けない IRI・形の悪い
    IRI は返さない。"""
    wanted = list(
        dict.fromkeys(i for i in iris if isinstance(i, str) and _EVIDENCE_IRI.fullmatch(i))
    )
    if not wanted or registry_root is None:
        return {}
    graphs = _paper_graphs(Path(registry_root))
    if not graphs:
        return {}
    gv = " ".join(f"<{g}>" for g in graphs)
    ev = " ".join(f"<{i}>" for i in wanted)
    po, nif, lit = doc.PO, doc.NIF, doc.LIT
    res = await client.sparql_select(
        "SELECT ?e ?g ?t ?title ?path WHERE { "
        f"VALUES ?e {{ {ev} }} VALUES ?g {{ {gv} }} "
        f"GRAPH ?g {{ ?e <{nif}anchorOf> ?t . "
        f"OPTIONAL {{ ?p <{po}contains> ?e . ?sec <{po}contains> ?p . "
        f"?sec a <{doc.DOCO}Section> . "
        f"OPTIONAL {{ ?sec <{doc.DCTERMS}title> ?title }} "
        f"OPTIONAL {{ ?sec <{lit}structuralPath> ?path }} }} }} }}"
    )
    rows = (res.get("results") or {}).get("bindings", []) if isinstance(res, dict) else []

    def v(b: dict, k: str) -> str | None:
        return (b.get(k) or {}).get("value")

    out: dict[str, dict] = {}
    for b in sorted(rows, key=lambda b: (v(b, "e") or "", v(b, "title") or "", v(b, "path") or "")):
        iri, g, body = v(b, "e"), v(b, "g"), v(b, "t")
        if not iri or iri in out or g not in graphs or not body or not body.strip():
            continue
        full = " ".join(body.split())
        did, label = graphs[g]
        out[iri] = {
            "iri": iri,
            "text": cut(full),
            "full": full,
            "section": v(b, "title") or None,
            "path": v(b, "path") or None,
            "dataset_id": did,
            "dataset_label": label,
        }
    return out
