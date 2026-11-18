"""取り込み元の「知識の問い」の置き場（PR 14e-1b・``pair-relations.md`` §7）。

確認済みの関係をストアに置くと、その関係を問いで引けるようになる（MCP の
``answer_knowledge_question``・星図の用語の「問い」）。問いの名前と選び方は、話の
``themes.json`` の ``selections`` と同じ形で、**別のファイル**
``<registry>/vocab-shared/knowledge_sources.json`` に取り込み元ごとに置く
（``themes.json`` に入れると話として画面に出る・読む所が 30 か所を超える・別のキーは
``load_themes_file`` が落とす、ため）。

形: ``{version: 1, sources: {<取り込み元>: {title, updated_at, minted: [slug], uses: [slug],
relations: [{from, relation, to}], selections: {<kq_ の名前>: {mode: "knowledge", op,
target_slug, target, title, direction?}}}}}``。

問い＝確認済みで**ストアに置けた**関係だけに :func:`asterism.pair_relations.questions` と同じ
数え方を当てた結果（対象の語は用語の slug に直す）。確認が変わるたびにその取り込み元の分を
作り直す（消えた問いは消す）。話に同じ名前の問いが在れば話を正とし、ここには作らない。
取り込み元を外しても残す（確認済みは残るため）。話の側の後始末はこのファイルを見ない。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from asterism import pair_relations as pr
from asterism import shared_vocab as sv
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.themes import knowledge_tool_name, load_themes_file

logger = logging.getLogger(__name__)

__all__ = [
    "FILE",
    "VERSION",
    "build_entry",
    "load_sources",
    "save_sources",
    "selections",
    "update_source",
]

FILE = "knowledge_sources.json"
VERSION = 1


def _path(root: Path | str) -> Path:
    return sv._dir(root) / FILE


def _empty() -> dict:
    return {"version": VERSION, "sources": {}}


def load_sources(root: Path | str) -> dict:
    """読む。無ければ空。壊れていれば ``.bad`` に退避して空として読む（上書きしない）。"""
    path = _path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except (OSError, UnicodeDecodeError):
        return _empty()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if (
        not isinstance(data, dict)
        or data.get("version") != VERSION
        or not isinstance(data.get("sources"), dict)
    ):
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            logger.warning("knowledge_sources: could not set the broken file aside")
        return _empty()
    data["sources"] = {k: v for k, v in data["sources"].items() if isinstance(v, dict)}
    return data


def save_sources(root: Path | str, data: dict) -> None:
    sv._scaffold(root)
    sv._atomic_write(
        _path(root), json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    )


def selections(root: Path | str) -> list[tuple[dict, str, dict]]:
    """読む所が使う形: ``[(取り込み元の札 {id: None, label, source}, 問いの名前, 選び方)]``。
    ``mode == "knowledge"`` で対象の slug がある物だけ。名前の順。"""
    out: list[tuple[dict, str, dict]] = []
    for source, rec in sorted(load_sources(root)["sources"].items()):
        sels = rec.get("selections")
        if not isinstance(sels, dict):
            continue
        label = str(rec.get("title") or source)
        for name in sorted(sels):
            sel = sels[name]
            if isinstance(sel, dict) and sel.get("mode") == "knowledge" and sel.get("target_slug"):
                out.append(({"id": None, "label": label, "source": source}, str(name), sel))
    return out


def build_entry(
    ledger: Mapping[str, Any],
    copy: KnowledgeCopy,
    *,
    mapping: Mapping[str, Mapping[str, Any]],
    title: str | None,
    taken_names: set[str],
    updated_at: str,
) -> dict | None:
    """台帳の「確認済みでストアに置けた件」から、取り込み元 1 つの記録を作る。
    置けた件が無ければ ``None``。``mapping`` は台帳の ``stored_terms``（語 id → slug）。"""
    placed: dict[str, dict] = {}
    relations: list[dict] = []
    for key, e in sorted((ledger.get("pairs") or {}).items()):
        c = e.get("confirmed") if isinstance(e, dict) else None
        st = e.get("store") if isinstance(e, dict) else None
        if not c or not isinstance(st, dict) or not st.get("ok"):
            continue
        a, b = mapping.get(str(c.get("from"))), mapping.get(str(c.get("to")))
        if a is None or b is None:
            continue
        placed[key] = {"rel": c.get("rel"), "from": c.get("from"), "to": c.get("to"),
                       "at": c.get("at"), "by": "user", "evidence": []}  # fmt: skip
        relations.append({"from": a["slug"], "relation": c.get("rel"), "to": b["slug"]})
    if not placed:
        return None
    pseudo = {
        "pairs": {
            k: {"key": k, "state": "relation", "rel": v["rel"], "from": v["from"], "to": v["to"],
                "by": "ai", "confirmed": v}
            for k, v in placed.items()
        },
        "origin": [],
    }  # fmt: skip
    items, _total = pr.questions(pseudo, copy, lang="ja", limit=10**9)
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    sels: dict[str, dict] = {}
    for it in items:
        tid = str(it["term"]["id"])
        slug = (mapping.get(tid) or {}).get("slug")
        if not slug:
            continue
        direction = it.get("direction")
        name = knowledge_tool_name(slug, it["op"], direction)
        if name in taken_names:  # 話に同じ問いが在れば話を正とする
            continue
        sels[name] = {
            "mode": "knowledge",
            "op": it["op"],
            "target_slug": slug,
            "target": str(terms[tid].get("name") or slug),
            "title": str(it["text"]),
            **({"direction": direction} if it["op"] == "factors" and direction else {}),
        }
    used = {r["from"] for r in relations} | {r["to"] for r in relations}
    minted = sorted({v["slug"] for v in mapping.values() if v.get("minted") and v["slug"] in used})
    uses = sorted(
        {v["slug"] for v in mapping.values() if not v.get("minted") and v["slug"] in used}
    )
    uniq = {(r["from"], r["relation"], r["to"]): r for r in relations}
    return {
        "title": title or str(ledger.get("source") or ""),
        "updated_at": updated_at,
        "minted": minted,
        "uses": uses,
        "relations": [uniq[k] for k in sorted(uniq, key=str)],
        "selections": dict(sorted(sels.items())),
    }


def update_source(
    root: Path | str,
    source: str,
    ledger: Mapping[str, Any],
    copy: KnowledgeCopy,
    *,
    mapping: Mapping[str, Mapping[str, Any]],
    title: str | None,
    now: str,
) -> bool:
    """この取り込み元の分を作り直して保存する（変わらなければ書かない）。返りは書いたか。"""
    data = load_sources(root)
    taken: set[str] = set()
    for theme in (load_themes_file(root) or {}).get("themes", []):
        sels = theme.get("selections")
        if isinstance(sels, dict):
            taken.update(str(n) for n in sels)
    prev = data["sources"].get(source)
    entry = build_entry(
        ledger, copy, mapping=mapping, title=title, taken_names=taken, updated_at=now
    )
    if entry is None:
        if prev is None:
            return False
        del data["sources"][source]
    else:
        if prev is not None and {**prev, "updated_at": None} == {**entry, "updated_at": None}:
            return False
        data["sources"][source] = entry
    save_sources(root, data)
    return True
