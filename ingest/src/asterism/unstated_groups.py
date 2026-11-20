"""「言えなかった関係の種類」を束ねる（R2・design §23.13 ②）。

台帳の ``state == "none"`` の件に付いた ``unstated_kind``（R1）を、照合の規則
（:func:`asterism.term_match.merge_key` ＝ 表記ゆれ・空白・括弧・中黒・英語の単数化）で束ね、
種類ごとの組の数と例の組を返す純関数。AI を呼ばない・台帳を書き換えない・決定論。
``asterism_api`` に依らない。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from asterism import pair_relations as pr
from asterism import relation_catalog
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.term_match import merge_key

__all__ = ["EXAMPLES_MAX", "unstated_groups"]

#: 1 群に付ける例の組の既定の最大件数。
EXAMPLES_MAX = 5


def _catalog_keys() -> dict[str, str]:
    """束ねる鍵 → 目録の関係の名前（名前・日本語名・英語名のどれでも。先に載った関係が勝つ）。"""
    out: dict[str, str] = {}
    for rel in relation_catalog.RELATIONS:
        for text in (rel.name, rel.label.get("ja"), rel.label.get("en")):
            key = merge_key(text) if isinstance(text, str) else ""
            if key:
                out.setdefault(key, rel.name)
    return out


def unstated_groups(
    ledgers: Iterable[tuple[str, Mapping[str, Any], KnowledgeCopy]],
    *,
    lang: str = "ja",
    examples: int = EXAMPLES_MAX,
) -> list[dict]:
    """``[(取り込み元, 台帳, 写し)]`` → 群の一覧（``count`` の多い順 → ``label`` の文字の順）。

    入れる件: ``state == "none"``・``unstated_kind`` が規則
    （:func:`pair_relations._unstated_kind`）を通る・取り残し（``orphan``）でない・両端の語が写しにある。束ねる鍵が空の物は捨てる。
    群 ``{key, label, count, sources: {取り込み元: 組の数}, variants: [{label, count}],
    examples: [{source, pair, a: {id, name}, b: {id, name}, note}], in_catalog?}``。

    * ``label``: 群の中で最も多い元の書き方（同数なら文字の順で先）。
    * ``variants``: 群の全部の書き方と数（``label`` も含む。多い順 → 文字の順）。
    * ``examples``: 最大 ``examples`` 件。並びは 取り込み元の名前 → 組の鍵。
    * ``in_catalog``: 目録の関係の名前・日本語名・英語名と同じ鍵のとき、その関係の名前。
      （``lang`` は今は結果を変えない。画面の言語を口で受けるために置く。）
    """
    del lang
    catalog = _catalog_keys()
    limit = max(0, int(examples))
    spellings: dict[str, Counter[str]] = {}
    per_source: dict[str, Counter[str]] = {}
    rows: dict[str, list[tuple[str, str, dict]]] = {}
    for source, ledger, copy in ledgers:
        terms = {str(t["id"]): t for t in copy.terms if isinstance(t, Mapping) and "id" in t}
        for pair_key, entry in (ledger.get("pairs") or {}).items():
            if not isinstance(entry, Mapping) or entry.get("state") != "none":
                continue
            if entry.get("orphan"):
                continue
            kind = pr._unstated_kind(entry.get("unstated_kind"))
            key = merge_key(kind) if kind else ""
            if not kind or not key:
                continue
            a, b = terms.get(str(entry.get("a"))), terms.get(str(entry.get("b")))
            if a is None or b is None:
                continue
            spellings.setdefault(key, Counter())[kind] += 1
            per_source.setdefault(key, Counter())[source] += 1
            note = entry.get("note")
            rows.setdefault(key, []).append(
                (
                    source,
                    str(pair_key),
                    {
                        "source": source,
                        "pair": str(pair_key),
                        "a": {"id": str(a["id"]), "name": a.get("name")},
                        "b": {"id": str(b["id"]), "name": b.get("name")},
                        "note": note if isinstance(note, str) and note else None,
                    },
                )
            )
    groups: list[dict] = []
    for key, spell in spellings.items():
        ordered = sorted(spell.items(), key=lambda kv: (-kv[1], kv[0]))
        label = ordered[0][0]
        examples_all = [r[2] for r in sorted(rows[key], key=lambda r: (r[0], r[1]))]
        group: dict[str, Any] = {
            "key": key,
            "label": label,
            "count": sum(spell.values()),
            "sources": dict(sorted(per_source[key].items())),
            "variants": [{"label": k, "count": n} for k, n in ordered],
            "examples": examples_all[:limit],
        }
        if key in catalog:
            group["in_catalog"] = catalog[key]
        groups.append(group)
    groups.sort(key=lambda g: (-g["count"], g["label"]))
    return groups
