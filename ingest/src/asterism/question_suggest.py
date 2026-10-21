"""「問いを提案してもらう」の後処理（決定論・ui-redesign PR 10a・design.md §18）。

AI（``asterism_step0.question_suggest``）が返した閉じた JSON の問いを、**機械が検査して絞る**。
ここを通ったものだけが画面に出る。AI の文を信用せず、次の規則を全部当てる:

* 答え方が閉じた形に落ちる — データなら ``themes.FITTING_OPS`` の 7 つ（``count`` / ``range`` /
  ``top`` / ``filter`` / ``group`` / ``trend`` / ``join``）で、その形に要る部分が全部ある
  （``themes.data_answer_complete`` と同じ規則）。知識なら ``factors`` / ``parts`` / ``kinds`` で、
  対象が既にある用語・向きが揃っている。落ちないものは出さない（メモの問いを増やさない）。
* 名前が実在する — ``kind`` / ``kind2`` は表の「何の行か」（または既にある種類の用語）、
  ``property`` / ``property2`` は表の列（見出し・表示名）か既にある項目の用語、``target`` は既に
  ある用語の名前に、:func:`~asterism.shared_vocab.normalize_label` で完全一致するものだけ。
  当たらない名前を使う問いは落とす（``range`` / ``top`` の任意の ``kind`` も、言ってあって当たらない
  ときは落とす）。``trend`` の x と y が同じ列・``join`` の両端が同じ種類の問いも落とす。出すのは
  当たった**正式な表示名**。
* ``filter`` の ``value`` は**問いの文に書かれている語**だけ（データの値を言い当てても通さない）。
* 既存の問い・同じ提案の中で文が重なるものは出さない（``normalize_label``）。同じ答え方（実行文が
  同じ）も 1 本だけ。「何件ある？」だけの問い（``count``）は 1 本まで。
* 多くて 5 本。

返す行は ``{question, mode, op, shape, kind, property, property2, kind2, value, match, target,
direction}``。``shape`` = 機械が組む実行文
（:func:`~asterism.shared_vocab.describe_selection`）。使わない欄は ``""``。
何も保存しない（提案の文・却下は残さない）。AI の ``reason`` は返さない。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from asterism import shared_vocab as sv
from asterism import themes
from asterism.mapping_ir_read import MappingIRView
from asterism.shared_vocab import normalize_label

__all__ = [
    "MAX_SUGGESTIONS",
    "dataset_names",
    "name_index",
    "postprocess_questions",
]

MAX_SUGGESTIONS = 5

#: 名前の索引: ``normalize_label(別名) → 正式な表示名``。
NameIndex = dict[str, str]
#: ``[(表示名, [別名…])]``。
Entries = list[tuple[str, list[str]]]


def name_index(entries: Iterable[tuple[str, Iterable[str]]]) -> NameIndex:
    """``[(表示名, [別名…])]`` → 索引。表示名自身も別名に数える。同じ別名は先の表示名が勝つ。"""
    out: NameIndex = {}
    for display, aliases in entries:
        shown = " ".join(str(display or "").split())
        if not shown:
            continue
        for a in (shown, *aliases):
            key = normalize_label(str(a or ""))
            if key and key not in out:
                out[key] = shown
    return out


def dataset_names(view: MappingIRView) -> tuple[Entries, Entries]:
    """データセットの Mapping IR → ``(kinds, properties)``（どちらも ``[(表示名, [別名…])]``）。

    * 種類 = 各 map の主語の表示名（``subject.label``・無ければ map の名前）。map の名前は別名。
    * 項目 = 各 map の行。表示名は行の ``label``（無ければつなぐ先の種類の名前・列の見出し）、
      列の見出しは別名。主語の型を宣言するだけの行（見出しも名前も無い）は数えない。
    データの値は読まない（見出しと設計で付けた名前だけ）。"""
    kinds: Entries = []
    props: Entries = []
    for tm in view.maps:
        kinds.append((tm.subject_label or tm.name, [tm.name]))
        for p in tm.properties:
            cols = [c for c in (p.column, *p.columns) if c]
            display = p.label or p.link_label or (cols[0] if cols else "")
            if display:
                props.append((display, [*cols, *(x for x in (p.label, p.link_label) if x)]))
    return kinds, props


def _term_entries(
    terms: Iterable[sv.SharedTerm], kind: str | None, lang: str
) -> Entries:
    out: Entries = []
    for t in terms:
        if kind is not None and t.kind != kind:
            continue
        en = t.label_en or ""
        shown = (en or t.label) if lang.startswith("en") else t.label
        out.append((shown, [t.label, en, *t.aliases]))
    return out


def _pick(idx: NameIndex, name: Any) -> str | None:
    key = normalize_label(str(name or ""))
    return idx.get(key) if key else None


def postprocess_questions(
    ai: dict,
    *,
    kinds: Iterable[tuple[str, Iterable[str]]] = (),
    properties: Iterable[tuple[str, Iterable[str]]] = (),
    terms: Iterable[sv.SharedTerm] = (),
    existing_questions: Iterable[str] = (),
    lang: str = "ja",
) -> list[dict]:
    """検査済みの AI の返し（``{"questions": [...]}``）→ 出してよい問いの行（最大 5）。規則は
    モジュールの説明を見る。``kinds`` / ``properties`` = :func:`dataset_names` の返し、``terms`` =
    既にある用語（種類・項目・対象の名前の索引にもなる）。"""
    terms = list(terms)
    kind_idx = name_index(
        [*kinds, *_term_entries(terms, "class", lang)],
    )
    prop_idx = name_index(
        [*properties, *_term_entries(terms, "property", lang)],
    )
    target_idx = name_index(_term_entries(terms, None, lang))
    seen_q = {normalize_label(q) for q in existing_questions}
    seen_shape: set[str] = set()
    out: list[dict] = []
    has_count = False
    for raw in ai.get("questions") or []:
        if not isinstance(raw, dict) or len(out) >= MAX_SUGGESTIONS:
            continue
        question = " ".join(str(raw.get("question") or "").split())
        qkey = normalize_label(question)
        if not question or qkey in seen_q:
            continue
        mode, op = raw.get("mode"), raw.get("op")
        sel: dict[str, str] = {"op": str(op)}
        if mode == "data" and op in themes.FITTING_OPS:
            names = {
                "kind": _pick(kind_idx, raw.get("kind")),
                "property": _pick(prop_idx, raw.get("property")),
                "property2": _pick(prop_idx, raw.get("property2")),
                "kind2": _pick(kind_idx, raw.get("kind2")),
            }
            # 使う欄に名前が当たらない問いは落とす。使わない欄は黙って空にする
            used = {
                "count": ("kind",),
                "range": ("property",),
                "top": ("property",),
                "filter": ("kind", "property"),
                "group": ("kind", "property"),
                "trend": ("kind", "property", "property2"),
                "join": ("kind", "property", "kind2"),
            }[str(op)]
            optional = {"range": ("kind",), "top": ("kind",)}.get(str(op), ())
            if any(names[f] is None for f in used):
                continue
            # 任意の欄（range / top の kind）は、言ってあって当たらないなら問いごと落とす
            # （黙って空にすると、AI が言った範囲と実行文がずれる）
            if any(raw.get(o) and names[o] is None for o in optional):
                continue
            # 同じ列どうしの推移・同じ種類どうしのつなぎは問いにならない
            if op == "trend" and names["property"] == names["property2"]:
                continue
            if op == "join" and names["kind"] == names["kind2"]:
                continue
            sel.update({f: names[f] or "" for f in (*used, *(o for o in optional if names[o]))})
            if op == "filter":
                value = themes._clean_value(raw.get("value"))
                # 値は問いの文に書かれている語だけ（データの値を言い当てた語は通さない）
                if not value or normalize_label(value) not in qkey:
                    continue
                sel["value"] = value
                sel["match"] = themes._clean_match(raw.get("match"))
            if not themes.data_answer_complete(
                op,
                kind=sel.get("kind"),
                prop=sel.get("property"),
                prop2=sel.get("property2"),
                kind2=sel.get("kind2"),
                value=sel.get("value", ""),
            ):
                continue
        elif mode == "knowledge" and op in themes.KNOWLEDGE_OPS:
            target = _pick(target_idx, raw.get("target"))
            direction = raw.get("direction")
            if target is None or (op == "factors" and direction not in themes.KNOWLEDGE_DIRECTIONS):
                continue
            sel["target"] = target
            if op == "factors":
                sel["direction"] = str(direction)
        else:
            continue
        shape = sv.describe_selection(sel, lang)
        if not shape or shape in seen_shape:
            continue
        if op == "count":  # 「何件ある？」だけの問いは 1 本まで
            if has_count:
                continue
            has_count = True
        seen_q.add(qkey)
        seen_shape.add(shape)
        out.append(
            {
                "question": question,
                "mode": str(mode),
                "op": str(op),
                "shape": shape,
                "kind": sel.get("kind", ""),
                "property": sel.get("property", ""),
                "property2": sel.get("property2", ""),
                "kind2": sel.get("kind2", ""),
                "value": sel.get("value", ""),
                "match": sel.get("match", ""),
                "target": sel.get("target", ""),
                "direction": sel.get("direction", ""),
            }
        )
    return out
