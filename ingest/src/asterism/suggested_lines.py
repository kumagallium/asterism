"""公開のあとに「結べそうな線」を機械が計算して置く（ui-redesign PR 7c・§15.2「育てる」）。

データセットを公開（``promote`` と名前だけの公開）したら、そのデータセットの**種類・項目**の名前と、
共有の用語（全テーマの概念）の名前の**完全一致**を機械が数え、``registry/vocab-shared/
suggested_lines.json`` に置く。AI は呼ばない・値は読まない・公開を止めない（失敗は警告だけ）。

* 規則は :func:`asterism.shared_vocab.fit_candidates` と同じ — 正規化した名前の完全一致だけ
  （部分一致なし）。2 文字以下の**別名**は使わない（短い別名がなんにでも当たるのを防ぐ。名前
  そのものは 2 文字でも使える）。種類（class）は class の用語に ``equivalentClass``、項目
  （property）は property の用語に ``equivalentProperty``。
* 置くのは ``[{subject, term, relation, dataset_id, version, at}]``。**冪等**: 同じ
  データセット × 版 × subject × term は 1 件（公開をやり直しても増えない）。
* 読むとき（:func:`pending`）に、いまの store・却下と照らして**もう要らないもの**（すでに線がある・
  人が外した・用語や公開が無くなった）を除く。置いた時点の判断を信じない。
* 線にするのは人の「まとめて確定」だけ（api の ``/api/sky/suggestions/apply``）。来歴は
  ``drafted_by="machine"``。

``subject`` は種類・項目の IRI。標準の語・共有の語（``sv:``）そのものは対象にしない。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from asterism import shared_vocab, substrate
from asterism.class_schema import class_labels

__all__ = [
    "FILE",
    "compute_for_dataset",
    "load_suggestions",
    "pending",
    "published_subjects",
    "record_for_dataset",
    "remove_suggestions",
    "version_of",
]

FILE = "suggested_lines.json"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
#: 別名がこの長さ（正規化後）以下なら、完全一致の根拠にしない
_MIN_ALIAS_LEN = 3
#: 1 データセットあたりに置く上限（暴走した公開で一覧が膨らまない）
_MAX_PER_DATASET = 200
#: 読み→書きを守る（公開の後処理・まとめて確定・外すが別スレッドで同時に走っても行を失わない）
_LOCK = threading.RLock()
_REL_OF = {"class": "equivalentClass", "property": "equivalentProperty"}


def _path(root: Path | str) -> Path:
    return shared_vocab._dir(root) / FILE


def _clean(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for d in raw:
        if not (
            isinstance(d, dict)
            and all(isinstance(d.get(k), str) and d[k] for k in ("subject", "term", "relation"))
        ):
            continue
        out.append(
            {
                "subject": d["subject"],
                "term": d["term"],
                "relation": d["relation"],
                "dataset_id": str(d.get("dataset_id") or ""),
                "version": str(d.get("version") or ""),
                "at": str(d.get("at") or ""),
            }
        )
    return out


def load_suggestions(root: Path | str) -> list[dict]:
    """置いた全件（無い・壊れていれば ``[]``）。読む側は :func:`pending` で絞る。"""
    try:
        return _clean(json.loads(_path(root).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def _save(root: Path | str, rows: list[dict]) -> None:
    shared_vocab._atomic_write(_path(root), json.dumps(rows, indent=2, ensure_ascii=False))


def version_of(meta: dict) -> str:
    """公開した版の印 — 生きている版 graph の IRI の最後の区切り（無ければ ``promoted_at``）。"""
    live = str(meta.get("live_graph") or "").rstrip("/")
    tail = live.rsplit("/", 1)[-1] if live else ""
    return tail or str(meta.get("promoted_at") or "")


async def _subjects(client: Any, dataset_id: str) -> dict[str, tuple[str, list[str]]]:
    """データセットの ontology graph の種類・項目 → ``(class|property, [名前…])``。名前は
    ``rdfs:label`` の全言語（日本語・英語どちらの名前でも用語に当てられる）。"""
    graph = substrate.ontology_graph_iri(dataset_id)
    rows = await shared_vocab._bindings(
        client,
        f"SELECT ?s ?t ?l WHERE {{ GRAPH <{graph}> {{ ?s a ?t . "
        f"OPTIONAL {{ ?s <{_RDFS}label> ?l }} }} "
        f"VALUES ?t {{ <{_RDFS}Class> <{_RDF}Property> }} FILTER(isIRI(?s)) }} ORDER BY ?s ?l",
    )
    out: dict[str, tuple[str, list[str]]] = {}
    for b in rows:
        iri = b["s"]["value"]
        kind = "class" if b["t"]["value"] == f"{_RDFS}Class" else "property"
        cur = out.setdefault(iri, (kind, []))
        label = (b.get("l") or {}).get("value")
        if label and label not in cur[1]:
            cur[1].append(label)
    return out


def _skip_subject(iri: str) -> bool:
    """共有の語（``sv:``）・標準の名前空間の語は、データセットの種類・項目ではない。"""
    return shared_vocab.is_shared_iri(iri) or any(
        iri.startswith(ns) for ns in shared_vocab.known_namespaces()
    )


async def compute_for_dataset(
    client: Any,
    dataset_id: str,
    *,
    version: str,
    at: str,
    terms: list[shared_vocab.SharedTerm] | None = None,
    registry_root: Path | str | None = None,
) -> list[dict]:
    """このデータセットの種類・項目と、共有の用語の完全一致 → 行（置く前の候補）。決定論。

    1 つの subject は 1 つの term と一致したときだけでなく、複数に一致すれば全部返す（人が選ぶ）。
    同じ（subject, term）は 1 回だけ。上限 ``_MAX_PER_DATASET``。``registry_root`` を渡すと、
    星図に出る種類の表示名（設計で付けた名前）も種類の名前に加える。"""
    terms = terms if terms is not None else await shared_vocab._read_terms(client)
    if not terms:
        return []
    subjects = await _subjects(client, dataset_id)
    classes = {i for i, (k, _l) in subjects.items() if k == "class" and not _skip_subject(i)}
    if classes and registry_root is not None:
        try:
            shown = await class_labels(client, Path(registry_root), classes)
        except Exception:  # 表示名が引けなくても、ontology の名前で続ける
            shown = {}
        for iri, name in shown.items():
            if name and name not in subjects[iri][1]:
                subjects[iri][1].append(name)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for iri in sorted(subjects):
        kind, labels = subjects[iri]
        if _skip_subject(iri) or not labels:
            continue
        for label in labels:
            cands = shared_vocab.fit_candidates(
                label, "", shared_terms=terms, dataset_terms=[], standard_hits=[]
            )
            for c in cands:
                if c["kind"] != "shared" or c["term_kind"] != kind or (iri, c["term"]) in seen:
                    continue
                if c["matched_by"] == "alias":
                    alias = shared_vocab.normalize_label(str(c.get("alias") or ""))
                    if len(alias) < _MIN_ALIAS_LEN:
                        continue
                seen.add((iri, c["term"]))
                rows.append(
                    {
                        "subject": iri,
                        "term": c["term"],
                        "relation": _REL_OF[kind],
                        "dataset_id": dataset_id,
                        "version": version,
                        "at": at,
                    }
                )
                if len(rows) >= _MAX_PER_DATASET:
                    return rows
    return rows


async def published_subjects(client: Any, dataset_ids: Iterable[str]) -> set[tuple[str, str]]:
    """公開済みのデータセットの ontology graph にいま在る ``(dataset_id, 種類・項目の IRI)``。
    :func:`pending` の ``live_subjects`` に渡す（設計を直して消えた種類・項目の候補を出さない）。"""
    out: set[tuple[str, str]] = set()
    for ds in sorted(set(dataset_ids)):
        for iri in await _subjects(client, ds):
            out.add((ds, iri))
    return out


def record_for_dataset(
    root: Path | str, rows: Iterable[dict], *, dataset_id: str | None = None
) -> int:
    """候補を置く。**そのデータセットの旧版の行は置き換える**（``dataset_id`` が同じ行を消して
    から足す。``dataset_id`` を渡すと、rows が空でも旧い行を消す）。冪等: データセット × 版 ×
    subject × term がすでに在る行は数えない。足した（新しい）件数。"""
    new = _clean(list(rows))
    datasets = {r["dataset_id"] for r in new}
    if dataset_id:
        datasets.add(dataset_id)
    with _LOCK:
        have = load_suggestions(root)
        old_keys = {(d["dataset_id"], d["version"], d["subject"], d["term"]) for d in have}
        kept = [d for d in have if d["dataset_id"] not in datasets]
        seen: set[tuple[str, str, str, str]] = set()
        added = 0
        for r in new:
            k = (r["dataset_id"], r["version"], r["subject"], r["term"])
            if k in seen:
                continue
            seen.add(k)
            kept.append(r)
            if k not in old_keys:
                added += 1
        if kept != have:
            _save(root, kept)
        return added


def remove_suggestions(root: Path | str, items: Iterable[dict]) -> int:
    """（subject, term, relation）が同じ行を全版ぶん消す（線にした・外したあと）。消した件数。"""
    drop = {(i["subject"], i["term"], i["relation"]) for i in items}
    with _LOCK:
        have = load_suggestions(root)
        kept = [d for d in have if (d["subject"], d["term"], d["relation"]) not in drop]
        if len(kept) != len(have):
            _save(root, kept)
        return len(have) - len(kept)


def pending(
    rows: Iterable[dict],
    *,
    lines: Iterable[dict],
    dismissed: Iterable[dict],
    terms: Iterable[shared_vocab.SharedTerm],
    live_datasets: set[str],
    live_subjects: set[tuple[str, str]] | None = None,
) -> list[dict]:
    """置いた候補のうち、いま出してよいもの。除くのは — 公開が無くなった・用語が無くなった・
    すでに同じ向きか逆向きの線がある（``≡`` は向きを問わない）・人が外した（関係つきも関係なしも）。
    ``live_subjects``（:func:`published_subjects`）を渡すと、種類・項目がいまの公開済みの
    ontology に無い候補も除く（設計を直して消えた種類を出さない）。
    同じ（subject, term, relation）は 1 件（新しい版・新しい ``at`` を残す）。並びは決定論。"""
    term_iris = {t.iri for t in terms}
    have: set[tuple[str, str, str]] = set()
    for ln in lines:
        have.add((ln["source"], ln["relation"], ln["target"]))
        if str(ln["relation"]).startswith("equivalent"):
            have.add((ln["target"], ln["relation"], ln["source"]))
    dismissed = list(dismissed)
    best: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        key = (r["subject"], r["term"], r["relation"])
        has_line = (r["subject"], r["relation"], r["term"]) in have
        if r["dataset_id"] not in live_datasets or r["term"] not in term_iris or has_line:
            continue
        if live_subjects is not None and (r["dataset_id"], r["subject"]) not in live_subjects:
            continue
        subj, term, rel = r["subject"], r["term"], r["relation"]
        if shared_vocab.is_dismissed(dismissed, subj, term):
            continue
        if shared_vocab.is_dismissed(dismissed, subj, term, rel):
            continue
        cur = best.get(key)
        if cur is None or (r["at"], r["version"]) > (cur["at"], cur["version"]):
            best[key] = r
    return sorted(best.values(), key=lambda r: (r["dataset_id"], r["subject"], r["term"]))
