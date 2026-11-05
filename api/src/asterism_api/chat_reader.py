"""公開済みの論文の本文を、節・段落・文の順で引く（ui-redesign PR 13b・design.md §22）。

根拠の文を押したときに、会話の列を論文の本文に入れ替えるための読み出し。
``GET /api/chat/paper`` の本体。

* 公開済みの論文は公開中の版（:func:`asterism.evidence._paper_graphs` と同じ）。**未公開の論文も
  開ける**（承認を決めるときこそ前後の文脈が要る）— 削除・取り消しされていない論文のデータセット
  （``source_kind == "xml"``）なら、下書きの版 graph（``chat_paper.read_sentences`` と同じ解決:
  ``graph_iri`` → ``live_graph`` → canonical）を読む。返りの ``published`` で区別する。
  graph IRI は registry から引いて形を検査済み。外からの文字列は SPARQL に入れない。
* SPARQL は文書の読み出しで 2 回（文＋段落＋節の対応・節の題名と入れ子）。
  文書の順は ``nif:beginIndex``。
* 入れ子の節は平らにして ``depth``（0 始まり）。文を持たない節は出さない。
* ``evidence`` = この論文の文のうち、確定した関係の根拠になっているもの（公開済みのときだけ。
  来歴 ``prov:wasDerivedFrom`` を関係の graph から 1 回引いて突き合わせる）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from asterism import documents as doc
from asterism import evidence, substrate
from asterism.subjects import resolve_dataset_label
from asterism.themes import _EVIDENCE_IRI

__all__ = ["MAX_SENTENCES", "PaperNotFound", "build_paper", "flatten", "locate"]

#: 返す文の上限（超えたら先頭から・``truncated: true``）
MAX_SENTENCES = 5000


class PaperNotFound(Exception):
    """公開済みの論文ではない（未公開・論文でない・無い）。"""


_PROV = "http://www.w3.org/ns/prov#"


def locate(root: Path, dataset_id: str) -> tuple[str, str, bool]:
    """開く論文の ``(graph IRI, 論文名, 公開済みか)``。開けなければ :class:`PaperNotFound`。"""
    for g, (did, label) in evidence._paper_graphs(root).items():
        if did == dataset_id:
            return g, label, True
    try:
        meta = json.loads((root / dataset_id / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PaperNotFound(dataset_id) from None
    if (
        not isinstance(meta, dict)
        or meta.get("status") in evidence._UNPUBLISHED_STATUS
        or meta.get("is_shared_vocab")
        or str(meta.get("source_kind") or "csv") != "xml"
    ):
        raise PaperNotFound(dataset_id)
    graph = str(
        meta.get("graph_iri") or meta.get("live_graph") or substrate.canonical_graph_iri(dataset_id)
    )
    if not _EVIDENCE_IRI.fullmatch(graph):
        raise PaperNotFound(dataset_id)
    return graph, resolve_dataset_label(root, dataset_id), False


def _v(b: dict, k: str) -> str | None:
    return (b.get(k) or {}).get("value")


def _rows(res: Any) -> list[dict]:
    results = res.get("results") if isinstance(res, dict) else None
    return list(results.get("bindings", [])) if isinstance(results, dict) else []


def flatten(sentence_bindings: list[dict], section_bindings: list[dict]) -> list[dict]:
    """SPARQL の結果 → 平らな節の並び（文書の順）。純関数。

    節 = ``{iri, title, path, role, depth, paragraphs: [{iri, sentences: [{iri, text}]}]}``。
    節の順は「その節（子を含む）の最初の文の ``beginIndex``」・段落と文は ``beginIndex`` 順。"""
    secs: dict[str, dict] = {}

    def sec_of(iri: str) -> dict:
        return secs.setdefault(
            iri, {"title": "", "path": "", "role": None, "children": set(), "paras": {}}
        )

    for b in section_bindings:
        iri = _v(b, "sec")
        if not iri:
            continue
        s = sec_of(iri)
        if _v(b, "title"):
            s["title"] = _v(b, "title") or ""
        if _v(b, "path"):
            s["path"] = _v(b, "path") or ""
        if _v(b, "role") and s["role"] is None:
            s["role"] = (_v(b, "role") or "").rsplit("/", 1)[-1].lower() or None
        if _v(b, "child"):
            s["children"].add(_v(b, "child"))
    seen: set[str] = set()
    for b in sentence_bindings:
        iri, body, sec, para = _v(b, "s"), _v(b, "t"), _v(b, "sec"), _v(b, "p")
        if not iri or iri in seen or not body or not body.strip() or not sec or not para:
            continue
        seen.add(iri)
        try:
            begin = int(_v(b, "b") or 0)
        except ValueError:
            begin = 0
        p = sec_of(sec)["paras"].setdefault(para, {"iri": para, "begin": begin, "sentences": []})
        p["begin"] = min(p["begin"], begin)
        p["sentences"].append({"iri": iri, "text": " ".join(body.split()), "begin": begin})

    children = {c for s in secs.values() for c in s["children"]}
    inf = float("inf")
    memo: dict[str, float] = {}

    def first(iri: str, trail: frozenset[str] = frozenset()) -> float:
        if iri in memo:
            return memo[iri]
        if iri in trail or iri not in secs:
            return inf
        s = secs[iri]
        own = [p["begin"] for p in s["paras"].values()]
        sub = [first(c, trail | {iri}) for c in s["children"]]
        memo[iri] = min([*own, *sub], default=inf)
        return memo[iri]

    out: list[dict] = []
    emitted: set[str] = set()

    def walk(iri: str, depth: int) -> None:
        if iri in emitted or iri not in secs or first(iri) == inf:
            return
        emitted.add(iri)
        s = secs[iri]
        paras = sorted(s["paras"].values(), key=lambda p: (p["begin"], p["iri"]))
        out.append(
            {
                "iri": iri,
                "title": s["title"] or None,
                "path": s["path"] or None,
                "role": s["role"],
                "depth": depth,
                "paragraphs": [
                    {
                        "iri": p["iri"],
                        "sentences": [
                            {"iri": x["iri"], "text": x["text"]}
                            for x in sorted(p["sentences"], key=lambda q: (q["begin"], q["iri"]))
                        ],
                    }
                    for p in paras
                ],
            }
        )
        depth_next = depth + 1
        for c in sorted(s["children"], key=lambda c: (first(c), c)):
            walk(c, depth_next)

    roots = [i for i in secs if i not in children]
    for r in sorted(roots, key=lambda i: (first(i), i)):
        walk(r, 0)
    return out


async def build_paper(client: Any, registry_root: Path | str, dataset_id: str) -> dict[str, Any]:
    """``GET /api/chat/paper`` の返り。公開済みの論文でなければ :class:`PaperNotFound`。"""
    root = Path(registry_root)
    graph, label, published = locate(root, dataset_id)
    po, nif, lit = doc.PO, doc.NIF, doc.LIT
    section = f"<{doc.DOCO}Section>"
    sent = await client.sparql_select(
        f"SELECT ?s ?t ?b ?p ?sec WHERE {{ GRAPH <{graph}> {{ "
        f"?s a <{doc.DOCO}Sentence> ; <{nif}anchorOf> ?t ; <{nif}beginIndex> ?b . "
        f"?p <{po}contains> ?s . ?sec <{po}contains> ?p . ?sec a {section} }} }} "
        f"ORDER BY ?b LIMIT {MAX_SENTENCES + 1}"
    )
    secs = await client.sparql_select(
        f"SELECT ?sec ?title ?path ?role ?child WHERE {{ GRAPH <{graph}> {{ ?sec a {section} . "
        f"OPTIONAL {{ ?sec <{doc.DCTERMS}title> ?title }} "
        f"OPTIONAL {{ ?sec <{lit}structuralPath> ?path }} "
        f'OPTIONAL {{ ?sec a ?role . FILTER(STRSTARTS(STR(?role), "{doc.DEO}")) }} '
        f"OPTIONAL {{ ?sec <{po}contains> ?child . ?child a {section} }} }} }}"
    )
    sent_rows = _rows(sent)
    truncated = len(sent_rows) > MAX_SENTENCES
    sections = flatten(sent_rows[:MAX_SENTENCES], _rows(secs))
    ordered: list[str] = []
    if published:
        res = await client.sparql_select(
            f"SELECT DISTINCT ?e WHERE {{ GRAPH <{substrate.ALIGNMENT_GRAPH}> {{ "
            f"?a <{_PROV}wasDerivedFrom> ?e }} }}"
        )
        cited = {_v(x, "e") for x in _rows(res)}
        ordered = [
            x["iri"]
            for s in sections
            for p in s["paragraphs"]
            for x in p["sentences"]
            if x["iri"] in cited
        ]
    return {
        "dataset_id": dataset_id,
        "label": label,
        "sections": sections,
        "evidence": ordered,
        "published": published,
        "truncated": truncated,
    }
