"""作業場の会話の差分を、テーマの下書きに当てる（ui-redesign PR 7a・design.md §16）。

AI は閉じた差分（``asterism_step0.graph_propose``: 7 操作）を**提案**するだけで、何も決めない。
人が「そう」と言ったとき、ここの決定論の関数が下書きに当てる。AI は呼ばない・ストアも読まない
（既にある用語は呼ぶ側が渡す）。

* :func:`apply_to_draft` — テーマの下書き（``themes.load_draft`` の形・v2）に差分を当てる。
  ``add_concept`` は :func:`asterism.themes.postprocess_draft` と同じ照合（完全一致が ``use`` に
  勝つ・2 文字以下の別名は標準に当てない・同じ名前の同じ種別は畳む）、``add_relation`` /
  ``retarget`` は 9 述語の種別表と循環（:func:`asterism.shared_vocab.cycle_rels`）で検査、
  ``remove`` は概念なら掛かる関係も外す、``rename`` は名前と別名、``add_question`` は問いと
  ``answers[]`` を 1 件足す。``bind_column`` はテーマでは拒む（データの当てはめ専用）。却下済み
  （``dismissed``）に当たる操作・検査に通らない操作は**落として**理由を ``report["dropped"]`` に
  残す（下書きは壊さない）。
* :func:`ops_to_sentences` — 提案カードの 1 行（``themes.json`` の ``rel.sentence`` と同じ言い方）。
* :func:`dismissal_keys` — 「✕」で保存する却下の鍵（``shared_vocab.dismiss_fit`` に渡す形）。

``report = {applied, dropped: [{index, op, reason, ref}], unlinked: [<key>]}``。``reason`` の
値: ``unknown_key`` / ``same`` / ``mixed`` / ``cycle`` / ``quantity_target`` /
``target_not_property`` / ``dismissed`` / ``duplicate`` / ``not_found`` / ``limit`` /
``not_renamable`` / ``dataset_only``。``unlinked`` = 問いの対象に関係でつながらない概念の key
（画面の印に使う。問いの対象がまだ無ければ空）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from asterism import shared_vocab as sv
from asterism import themes
from asterism.shared_vocab import SharedTerm, normalize_label

__all__ = [
    "KIND_KEY_PREFIX",
    "apply_to_draft",
    "dismissal_keys",
    "end_iri",
    "kind_key",
    "op_ref",
    "ops_to_sentences",
    "plan_term_relations",
    "proposal_id",
]

_USE_EXISTING = "existing:"
_USE_STANDARD = "standard:"
_PLACEHOLDER_SLUG = re.compile(r"^t\d+\Z")


def _claimed_use(
    op: dict, kind: str, by_slug: dict[str, SharedTerm]
) -> tuple[str, str | None, themes.StdRef | None] | None:
    """``add_concept`` が名乗る ``use``（``existing:<slug>`` / ``standard:<curie>``）を検査する →
    ``(use, slug, 標準の語)``。名乗りが閉じた一覧・同じ種別に当たらなければ ``None``。
    ``apply_to_draft`` と :func:`dismissal_keys` が同じ解決をするための 1 か所。"""
    claimed = str(op.get("use") or "new")
    if claimed.startswith(_USE_EXISTING):
        e2 = by_slug.get(claimed[len(_USE_EXISTING) :])
        if e2 is not None and e2.kind == kind:
            return claimed, e2.slug, None
    elif claimed.startswith(_USE_STANDARD):
        ref = themes.std_by_curie(claimed[len(_USE_STANDARD) :])
        if ref is not None and (
            ref.kind == kind or (ref.kind == "quantity" and kind == "property")
        ):
            return claimed, None, ref
    return None


def proposal_id(ops: list[dict]) -> str:
    """差分の ID — ``sha1(ops)[:12]``（キーを並べた JSON）。適用の冪等の鍵。"""
    text = json.dumps(ops, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 共通: 1 行の見出し（落とした操作の「どれか」を画面に返す）
# ---------------------------------------------------------------------------


def op_ref(op: dict) -> str:
    name = op.get("op")
    if name == "add_concept":
        return str(op.get("label_ja") or op.get("key") or "")
    if name in ("add_relation", "remove") and op.get("target") != "concept":
        return f"{op.get('from')} -{op.get('rel')}-> {op.get('to')}"
    if name == "retarget":
        return f"{op.get('from')} -{op.get('rel')}-> {op.get('new_to')}"
    if name in ("remove", "rename"):
        return str(op.get("key") or "")
    if name == "add_question":
        return str(op.get("question") or "")
    if name == "bind_column":
        return f"{op.get('column')} -{op.get('rel')}-> {op.get('to')}"
    return ""


# ---------------------------------------------------------------------------
# テーマの下書きに当てる
# ---------------------------------------------------------------------------


class _Applier:
    def __init__(
        self,
        draft: dict,
        *,
        existing: list[SharedTerm],
        dismissed: Iterable[dict],
        by: str,
    ) -> None:
        self.draft = copy.deepcopy(draft)
        self.rows: list[dict] = [r for r in self.draft.get("concepts") or [] if isinstance(r, dict)]
        self.rels: list[dict] = [
            r for r in self.draft.get("relations") or [] if isinstance(r, dict)
        ]
        self.answers: list[dict] = [
            a for a in self.draft.get("answers") or [] if isinstance(a, dict)
        ]
        self.questions: list[str] = list(self.draft.get("questions") or [])
        self.standards: dict[str, dict] = dict(self.draft.get("standards") or {})
        self.existing = existing
        self.by_slug = {e.slug: e for e in existing}
        self.dismissed = list(dismissed)
        self.by = by
        self.taken_keys = {str(r.get("key")) for r in self.rows}
        self.taken_slugs = set(self.by_slug) | {
            str(r.get("slug")) for r in self.rows if r.get("slug")
        }
        #: 操作の key → 下書きの key（同名の概念に畳んだ・無い = 却下済み）
        self.keymap: dict[str, str | None] = {}
        self.applied = 0
        self.dropped: list[dict] = []

    # -- 小道具 ------------------------------------------------------------------

    def drop(self, i: int, op: dict, reason: str) -> None:
        self.dropped.append(
            {"index": i, "op": op.get("op"), "reason": reason, "ref": op_ref(op)}
        )

    def row_by_key(self, key: str) -> dict | None:
        return next((r for r in self.rows if r.get("key") == key), None)

    def row(self, ref: str) -> tuple[dict | None, bool]:
        """操作が指す key → ``(行, 却下済みか)``。"""
        if ref in self.keymap:
            k = self.keymap[ref]
            return (self.row_by_key(k) if k is not None else None), k is None
        return self.row_by_key(ref), False

    def find_same(self, label_ja: str, kind: str, *, skip: dict | None = None) -> dict | None:
        n = normalize_label(label_ja)
        if not n:
            return None
        for r in self.rows:
            if r is skip or r.get("kind") != kind:  # 同名でも種類と項目は別の行
                continue
            if n in themes._norm_set(r.get("label_ja"), r.get("label_en")):
                return r
        return None

    def remember_std(self, ref: themes.StdRef) -> None:
        self.standards[ref.curie] = {"label": ref.label, "iri": ref.iri, "kind": ref.kind}

    def fresh_key(self) -> str:
        return themes._fresh_key(self.taken_keys)

    def end(self, ref: str) -> tuple[dict | None, themes.StdRef | None, bool]:
        """関係の端 → ``(行, 標準の語, 却下済み)``。標準の語は閉じた一覧にある curie だけ。"""
        row, gone = self.row(ref)
        if gone:
            return None, None, True
        if row is not None:
            return row, None, False
        if ":" in ref:
            std = themes.std_by_curie(ref)
            if std is not None:
                return None, std, False
        return None, None, False

    # -- 関係の検査 --------------------------------------------------------------

    def triples(self, *, without: dict | None = None) -> set[tuple[str, str, str]]:
        """下書きの関係（問題の無いもの）を ``(from, 述語, to)`` に。循環の検査の入力。"""
        out: set[tuple[str, str, str]] = set()
        for r in self.rels:
            if r is without or r.get("problem"):
                continue
            src = self.row_by_key(str(r.get("from")))
            if src is None:
                continue
            try:
                pred = themes.relation_predicate(str(r.get("rel")), str(src.get("kind")))
            except themes.ThemeError:
                continue
            out.add((str(r["from"]), pred, str(r["to"])))
        return out

    def check_relation(
        self,
        subj: dict,
        rel: str,
        obj: dict | None,
        std: themes.StdRef | None,
        *,
        without: dict | None = None,
    ) -> str | None:
        """種別表・同一・循環の検査（``postprocess_draft`` と同じ規則）。通れば ``None``。"""
        obj_key = std.curie if std is not None else str(obj["key"])  # type: ignore[index]
        obj_kind = std.kind if std is not None else str(obj["kind"])  # type: ignore[index]
        if obj_key == subj["key"]:
            return "same"
        if rel == "quantity":
            if subj["kind"] != "property" or std is None or std.kind != "quantity":
                return "quantity_target"
        elif rel in ("lowers", "raises", "affects"):
            if obj_kind != "property":
                return "target_not_property"
        elif obj_kind != subj["kind"]:  # sub / eq / part は同じ種別どうし
            return "mixed"
        pred = themes.relation_predicate(rel, str(subj["kind"]))
        cyc = sv.cycle_rels(pred)
        if cyc is not None and std is None and obj_key in sv._down_set(
            str(subj["key"]), self.triples(without=without), cyc
        ):
            return "cycle"
        return None

    def end_key_of(self, row: dict | None, std: themes.StdRef | None) -> str:
        return std.iri if std is not None else themes.concept_end_key(row)  # type: ignore[arg-type]

    def is_dismissed(
        self, subj: dict, rel: str, obj: dict | None, std: themes.StdRef | None
    ) -> bool:
        return sv.is_dismissed(
            self.dismissed,
            themes.concept_end_key(subj),
            self.end_key_of(obj, std),
            relation=themes.relation_predicate(rel, str(subj["kind"])),
        )

    # -- 操作 --------------------------------------------------------------------

    def add_concept(self, i: int, op: dict) -> None:
        label_ja, kind = op["label_ja"], op["kind"]
        dup = self.find_same(label_ja, kind)
        if dup is not None:
            self.keymap[op["key"]] = dup["key"]
            return self.drop(i, op, "duplicate")
        if len(self.rows) >= themes.MAX_CONCEPTS:
            return self.drop(i, op, "limit")
        aliases = list(sv.clean_aliases(op.get("aliases")))
        label_en = op.get("label_en") or ""
        match, use, slug, std = themes._machine_match(
            label_ja, label_en, aliases, kind, self.existing
        )
        if match is None and sv.is_dismissed(
            self.dismissed, sv.concept_subject(label_ja), sv.CONCEPT_DISMISS_TERM
        ):
            self.keymap[op["key"]] = None
            return self.drop(i, op, "dismissed")
        if std is not None:
            self.remember_std(std)
        if match is None:
            got = _claimed_use(op, kind, self.by_slug)
            if got is not None:
                use, slug_c, ref = got
                if slug_c:
                    slug = slug_c
                if ref is not None:
                    self.remember_std(ref)
        if not slug:
            slug = themes.derive_slug(label_en, aliases, self.taken_slugs, len(self.rows) + 1)
            self.taken_slugs.add(slug)
        key = op["key"] if op["key"] not in self.taken_keys else self.fresh_key()
        self.taken_keys.add(key)
        self.keymap[op["key"]] = key
        self.rows.append(
            {
                "key": key,
                "label_ja": label_ja,
                "label_en": label_en,
                "aliases": aliases,
                "kind": kind,
                "use": use,
                "slug": slug,
                "reason": op.get("reason") or "",
                "match": match,
                "by": self.by,
            }
        )
        self.applied += 1

    def _resolve_pair(
        self, i: int, op: dict, from_ref: str, to_ref: str
    ) -> tuple[dict, dict | None, themes.StdRef | None] | None:
        subj, gone_s = self.row(from_ref)
        obj, std, gone_o = self.end(to_ref)
        if gone_s or gone_o:
            self.drop(i, op, "dismissed")
            return None
        if subj is None or (obj is None and std is None):
            self.drop(i, op, "unknown_key")
            return None
        return subj, obj, std

    def has_relation(self, frm: str, rel: str, to: str) -> bool:
        """同じ関係が下書きにあるか（``eq`` は向きを問わない）。"""
        have = {(r.get("from"), r.get("to"), r.get("rel")) for r in self.rels}
        return (frm, to, rel) in have or (rel == "eq" and (to, frm, rel) in have)

    def add_relation(self, i: int, op: dict) -> None:
        got = self._resolve_pair(i, op, op["from"], op["to"])
        if got is None:
            return
        subj, obj, std = got
        if std is not None:
            self.remember_std(std)
        obj_key = std.curie if std is not None else str(obj["key"])  # type: ignore[index]
        if self.has_relation(str(subj["key"]), op["rel"], obj_key):
            return self.drop(i, op, "duplicate")
        problem = self.check_relation(subj, op["rel"], obj, std)
        if problem:
            return self.drop(i, op, problem)
        if self.is_dismissed(subj, op["rel"], obj, std):
            return self.drop(i, op, "dismissed")
        self.rels.append(
            {
                "from": subj["key"],
                "rel": op["rel"],
                "to": obj_key,
                "reason": op.get("reason") or "",
                "problem": None,
                "by": self.by,
            }
        )
        self.applied += 1

    def _find_relation(self, frm: str, rel: str, to: str) -> dict | None:
        row, _ = self.row(frm)
        end_row, std, _ = self.end(to)
        if row is None or (end_row is None and std is None):
            return None
        to_key = std.curie if std is not None else str(end_row["key"])  # type: ignore[index]
        return next(
            (
                r
                for r in self.rels
                if (r.get("from"), r.get("rel"), r.get("to")) == (row["key"], rel, to_key)
            ),
            None,
        )

    def retarget(self, i: int, op: dict) -> None:
        old = self._find_relation(op["from"], op["rel"], op["to"])
        if old is None:
            # 元の線が無い — 付け替えの先に同じ線が既にあれば、再送（もう当たっている）
            if self._find_relation(op["from"], op["rel"], op["new_to"]) is not None:
                return self.drop(i, op, "duplicate")
            return self.drop(i, op, "not_found")
        got = self._resolve_pair(i, op, op["from"], op["new_to"])
        if got is None:
            return
        subj, obj, std = got
        if std is not None:
            self.remember_std(std)
        new_key = std.curie if std is not None else str(obj["key"])  # type: ignore[index]
        if new_key == old["to"]:
            return self.drop(i, op, "duplicate")
        if self.has_relation(str(subj["key"]), op["rel"], new_key):
            return self.drop(i, op, "duplicate")
        problem = self.check_relation(subj, op["rel"], obj, std, without=old)
        if problem:
            return self.drop(i, op, problem)  # 元の関係は残る
        if self.is_dismissed(subj, op["rel"], obj, std):
            return self.drop(i, op, "dismissed")
        old["to"] = new_key
        old["reason"] = op.get("reason") or old.get("reason") or ""
        old["problem"] = None
        old["by"] = self.by
        self.applied += 1

    def remove(self, i: int, op: dict) -> None:
        if op["target"] == "relation":
            rel = self._find_relation(op["from"], op["rel"], op["to"])
            if rel is None:
                return self.drop(i, op, "not_found")
            self.rels.remove(rel)
            self.applied += 1
            return
        row, _gone = self.row(op["key"])
        if row is None:
            return self.drop(i, op, "not_found")
        key = row["key"]
        self.rows.remove(row)
        self.rels = [r for r in self.rels if key not in (r.get("from"), r.get("to"))]
        for a in self.answers:  # 概念を失った問いの答え方は、メモに戻す
            if key in (a.get("target"), a.get("kind"), a.get("property")):
                a.update(
                    mode="memo", memo="ai", target="", kind="", property="", direction="", op=""
                )
        self.applied += 1

    def rename(self, i: int, op: dict) -> None:
        row, _gone = self.row(op["key"])
        if row is None:
            return self.drop(i, op, "not_found")
        if row.get("use") != "new" or row.get("match"):
            return self.drop(i, op, "not_renamable")  # 既にある用語・標準の語に結んだ概念
        if self.find_same(op["label_ja"], str(row["kind"]), skip=row) is not None:
            return self.drop(i, op, "duplicate")
        row["label_ja"] = op["label_ja"]
        row["label_en"] = op.get("label_en") or ""
        row["aliases"] = list(sv.clean_aliases(op.get("aliases")))
        if op.get("reason"):
            row["reason"] = op["reason"]
        match, use, slug, std = themes._machine_match(
            row["label_ja"], row["label_en"], row["aliases"], str(row["kind"]), self.existing
        )
        if match is not None:  # 名前を直したら、既にある語に当たった
            row["match"], row["use"] = match, use
            if slug:
                row["slug"] = slug
            if std is not None:
                self.remember_std(std)
        elif _PLACEHOLDER_SLUG.match(str(row.get("slug") or "")) and row["label_en"]:
            self.taken_slugs.discard(str(row["slug"]))
            row["slug"] = themes.derive_slug(
                row["label_en"], row["aliases"], self.taken_slugs, len(self.rows)
            )
            self.taken_slugs.add(row["slug"])
        row["by"] = self.by
        self.applied += 1

    def add_question(self, i: int, op: dict) -> None:
        asked = {normalize_label(q) for q in self.questions}
        if normalize_label(op["question"]) in asked:
            return self.drop(i, op, "duplicate")
        if len(self.questions) >= themes._MAX_QUESTIONS:
            return self.drop(i, op, "limit")
        question = themes.clean_questions([op["question"]])
        if not question:
            return self.drop(i, op, "unknown_key")

        def pick(ref: str | None, kind: str | None = None) -> dict | None:
            if not ref:
                return None
            row, _ = self.row(ref)
            if row is not None and kind is not None and row["kind"] != kind:
                return None
            return row

        mode, op_kind = op["mode"], op["op_kind"]
        t_row, k_row = pick(op.get("target")), pick(op.get("kind"), "class")
        p_row = pick(op.get("property"), "property")
        direction = op.get("direction")
        if mode == "knowledge":
            good = op_kind in themes.KNOWLEDGE_OPS and t_row is not None
            if op_kind == "factors":
                good = good and direction in themes.KNOWLEDGE_DIRECTIONS
        else:
            good = (
                (op_kind == "count" and k_row is not None)
                or (op_kind == "range" and p_row is not None)
                or (op_kind == "top" and p_row is not None)
            )
        self.questions.append(question[0])
        self.answers.append(
            themes._answer_row(
                question[0],
                mode if good else "memo",
                op=op_kind,
                target=t_row["key"] if t_row and mode == "knowledge" else "",
                direction=direction if mode == "knowledge" and op_kind == "factors" else "",
                kind=k_row["key"] if k_row and mode == "data" else "",
                prop=p_row["key"] if p_row and mode == "data" else "",
                memo=None if good else "ai",
                wanted_data=op.get("wanted_data") or "",
                reason=op.get("reason") or "",
            )
        )
        self.answers[-1]["by"] = self.by
        self.applied += 1

    # -- 仕上げ ------------------------------------------------------------------

    def unlinked(self) -> list[str]:
        """問いの対象に、関係でつながらない概念の key（対象がまだ無ければ空）。"""
        targets = {
            str(a[f])
            for a in self.answers
            for f in ("target", "kind", "property")
            if a.get(f) and self.row_by_key(str(a[f])) is not None
        }
        if not targets:
            return []
        edges = [
            (str(r["from"]), str(r["to"]))
            for r in self.rels
            if r.get("from") is not None and r.get("to") is not None and not r.get("problem")
        ]
        reached = themes._connected(targets, edges)
        return [str(r["key"]) for r in self.rows if r["key"] not in reached]


def apply_to_draft(
    draft: dict,
    ops: list[dict],
    *,
    existing: list[SharedTerm],
    dismissed: Iterable[dict] = (),
    by: str = "ai",
) -> tuple[dict, dict]:
    """テーマの下書きに差分 ``ops`` を当てる → ``(新しい下書き, report)``。

    ``draft`` は :func:`asterism.themes.load_draft` の形（v2・書き換えない）。``existing`` は
    既にある共有の用語（照合の入力）、``dismissed`` は ``shared_vocab.load_dismissed`` の返り値。
    ``by`` は行の ``by``（``"ai"`` = AI の提案を人が受けた・``"human"`` = 人の直接の編集）。
    操作は先頭から順に当てる（前の操作で足した概念を、あとの操作が key で指せる）。検査に通らない
    操作は落として ``report["dropped"]`` に理由を残し、それ以外は当てる。保存はしない。"""
    ap = _Applier(draft, existing=existing, dismissed=dismissed, by=by)
    handlers = {
        "add_concept": ap.add_concept,
        "add_relation": ap.add_relation,
        "retarget": ap.retarget,
        "remove": ap.remove,
        "rename": ap.rename,
        "add_question": ap.add_question,
    }
    for i, op in enumerate(ops):
        handler = handlers.get(str(op.get("op")))
        if handler is None:  # bind_column（データの当てはめ専用）・知らない操作
            ap.drop(i, op, "dataset_only" if op.get("op") == "bind_column" else "unknown_key")
            continue
        handler(i, op)
    out = {
        **ap.draft,
        "concepts": ap.rows,
        "relations": ap.rels,
        "answers": ap.answers,
        "questions": ap.questions,
        "standards": ap.standards,
    }
    return out, {"applied": ap.applied, "dropped": ap.dropped, "unlinked": ap.unlinked()}


# ---------------------------------------------------------------------------
# ホーム（星図）: 公開済みの用語どうしの関係の下ごしらえ（store には書かない）
# ---------------------------------------------------------------------------


KIND_KEY_PREFIX = "kind:"


def kind_key(iri: str) -> str:
    """公開済みの種類（星）の鍵 ``kind:<sha1(IRI)[:10]>``。AI は IRI を書かない（書けない）ので、
    図の文脈には IRI の代わりにこの短い鍵を渡し、適用のときに公開済みの種類の集合へ引き戻す。
    同じ IRI は同じ鍵（決定論）。"""
    return KIND_KEY_PREFIX + hashlib.sha1(iri.encode("utf-8")).hexdigest()[:10]


def plan_term_relations(
    ops: list[dict],
    *,
    terms: list[SharedTerm],
    dismissed: Iterable[dict] = (),
    kinds: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """ホームの差分（``add_relation`` / ``remove`` の関係だけ）を、alignment graph に書く
    **計画**にする → ``(plans, dropped)``。key は公開済みの用語の slug（関係の先は標準の語の
    curie でもよい）。``kinds`` = 公開済みの種類 ``{kind_key(iri): {iri, label}}``（星）— 渡すと、
    ``kind:<…>`` の鍵を**関係の元（または先）**に使える。種類は「種類（class）」として検査し
    （種別表・循環は用語と同じ規則）、却下の鍵・線の端は種類の IRI そのもの。種別表・却下済みで
    落とすのはテーマと同じ規則（循環は store を読む ``assert_alignment`` が拒む）。``plans`` の
    1 件 = ``{index, action: "add"|"remove", source, target, relation, reason}``（``source`` /
    ``target`` は IRI・``relation`` は :data:`~asterism.crosswalk_runtime.ALIGN_RELATIONS` の鍵）。
    それ以外の操作は ``unsupported`` で落とす。何も書かない。"""
    kinds = kinds or {}
    rows = [
        {
            "key": t.slug, "label_ja": t.label, "label_en": t.label_en or "",
            "aliases": list(t.aliases), "kind": t.kind, "use": f"{_USE_EXISTING}{t.slug}",
            "slug": t.slug, "match": None, "by": "ai",
        }
        for t in terms
    ] + [
        {
            "key": key, "label_ja": str(k.get("label") or ""), "label_en": "", "aliases": [],
            "kind": "class", "use": "new", "slug": "", "match": None, "by": "ai",
        }
        for key, k in kinds.items()
    ]  # fmt: skip
    ap = _Applier({"concepts": rows, "relations": []}, existing=terms, dismissed=dismissed, by="ai")
    dismissed_rows = list(dismissed)
    plans: list[dict] = []
    for i, op in enumerate(ops):
        name = op.get("op")
        if name == "add_relation" or (name == "remove" and op.get("target") == "relation"):
            got = ap._resolve_pair(i, op, str(op["from"]), str(op["to"]))
            if got is None:
                continue
            subj, obj, std = got
            from_kind = kinds.get(str(op["from"]))
            to_kind = kinds.get(str(op["to"]))
            if name == "add_relation":
                problem = ap.check_relation(subj, op["rel"], obj, std)
                if problem:
                    ap.drop(i, op, problem)
                    continue
            source = str(from_kind["iri"]) if from_kind else themes.concept_end_key(subj)
            target = (
                str(to_kind["iri"]) if to_kind else ap.end_key_of(obj, std)  # type: ignore[arg-type]
            )
            relation = themes.relation_predicate(str(op["rel"]), str(subj["kind"]))
            if name == "add_relation":
                if from_kind or to_kind:  # 種類の端は IRI のまま比べる
                    gone = sv.is_dismissed(dismissed_rows, source, target, relation=relation)
                else:
                    gone = ap.is_dismissed(subj, op["rel"], obj, std)
                if gone:
                    ap.drop(i, op, "dismissed")
                    continue
            plans.append(
                {
                    "index": i,
                    "action": "add" if name == "add_relation" else "remove",
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "reason": op.get("reason") or "",
                }
            )
        else:
            ap.drop(i, op, "unsupported")
    return plans, ap.dropped


# ---------------------------------------------------------------------------
# 却下の鍵（「✕」）
# ---------------------------------------------------------------------------


def end_iri(ref: str, terms_by_slug: dict[str, SharedTerm]) -> str | None:
    """ホームで使う関係の端 → IRI。公開済みの用語の slug は ``sv:`` の IRI、標準の語は curie →
    閉じた一覧の IRI。どちらでもなければ ``None``。"""
    if ref in terms_by_slug:
        return terms_by_slug[ref].iri
    std = themes.std_by_curie(ref) if ":" in ref else None
    return std.iri if std is not None else None


def dismissal_keys(
    draft: dict, ops: list[dict], *, existing: list[SharedTerm]
) -> list[dict]:
    """テーマの差分の各操作を、却下の鍵 ``[{subject, term, relation?}]``
    （``shared_vocab.dismiss_fit`` に渡す形）にする。概念は ``concept_subject``
    （既にある語・標準の語に当たる概念は却下しない — ``apply_to_draft`` も当たった概念は
    却下で落とさない）、関係は両端の
    :func:`asterism.themes.concept_end_key` ＋述語（``retarget`` は付け替えの先の関係）。
    決定論・何も書かない。鍵にできない操作（``remove`` / ``rename`` / ``add_question``）は
    出さない。"""
    rows: dict[str, dict] = {
        str(r["key"]): r
        for r in draft.get("concepts") or []
        if isinstance(r, dict) and r.get("key")
    }
    by_slug = {e.slug: e for e in existing}
    out: list[dict] = []
    seen: set[tuple[str, str, str | None]] = set()

    def add(subject: str, term: str, relation: str | None = None) -> None:
        if (subject, term, relation) not in seen:
            seen.add((subject, term, relation))
            row: dict = {"subject": subject, "term": term}
            if relation is not None:
                row["relation"] = relation
            out.append(row)

    for op in ops:
        if op.get("op") != "add_concept":
            continue
        label_ja, kind = str(op["label_ja"]), str(op["kind"])
        aliases = list(sv.clean_aliases(op.get("aliases")))
        match, use, slug, _std = themes._machine_match(
            label_ja, str(op.get("label_en") or ""), aliases, kind, existing
        )
        if match is None:  # apply と同じ解決: 既存の語に結んだ概念は IRI で指す
            claimed = _claimed_use(op, kind, by_slug)
            if claimed is not None:
                use, slug = claimed[0], claimed[1] or slug
        rows[str(op["key"])] = {
            "key": op["key"], "label_ja": label_ja, "kind": kind, "use": use, "slug": slug,
            "match": match,
        }  # fmt: skip
        if match is None:
            add(sv.concept_subject(label_ja), sv.CONCEPT_DISMISS_TERM)

    def end_of(ref: str) -> str | None:
        if ref in rows:
            return themes.concept_end_key(rows[ref])
        std = themes.std_by_curie(ref) if ":" in ref else None
        return std.iri if std is not None else None

    for op in ops:
        name = op.get("op")
        if name not in ("add_relation", "retarget"):
            continue
        subj = rows.get(str(op["from"]))
        to_ref = str(op["new_to"] if name == "retarget" else op["to"])
        term = end_of(to_ref)
        if subj is None or term is None:
            continue
        add(
            themes.concept_end_key(subj),
            term,
            themes.relation_predicate(str(op["rel"]), str(subj["kind"])),
        )
    return out


# ---------------------------------------------------------------------------
# 提案カードの文
# ---------------------------------------------------------------------------

_REL_SENTENCE = {
    "ja": {
        "sub": "{s} は {o} の一種",
        "eq": "{s} と {o} は同じもの",
        "quantity": "{s} の量は {o}",
        "part": "{s} は {o} の一部",
        "lowers": "{s} は {o} を下げる",
        "raises": "{s} は {o} を上げる",
        "affects": "{s} は {o} を変える",
    },
    "en": {
        "sub": "{s} is a kind of {o}",
        "eq": "{s} is the same as {o}",
        "quantity": "The quantity of {s} is {o}",
        "part": "{s} is a part of {o}",
        "lowers": "{s} lowers {o}",
        "raises": "{s} raises {o}",
        "affects": "{s} changes {o}",
    },
}


def ops_to_sentences(ops: list[dict], graph: dict, lang: str = "ja") -> list[str]:
    """提案カードの文（``ops`` と同じ順・1 操作 1 文）。言い方は画面の ``rel.sentence`` と同じ
    （「粒界散乱 は 格子熱伝導率 を下げる」）。``graph`` = ``{concepts: [{key, label_ja,
    label_en}]}``（画面にある図）— 同じ差分で足す概念の名前も引く。key が引けなければ key のまま、
    標準の語の curie は標準の名前。決定論・LLM なし。"""
    lg = "en" if str(lang).startswith("en") else "ja"
    names: dict[str, str] = {}

    def name_of(row: dict) -> str:
        if lg == "en":
            return str(row.get("label_en") or row.get("label_ja") or row.get("key") or "")
        return str(row.get("label_ja") or row.get("label_en") or row.get("key") or "")

    for c in graph.get("concepts") or []:
        if isinstance(c, dict) and c.get("key") is not None:
            names[str(c["key"])] = name_of(c)
    for op in ops:
        if op.get("op") == "add_concept":
            names[str(op["key"])] = name_of(op)

    def label(ref: Any) -> str:
        ref = str(ref)
        if ref in names:
            return names[ref]
        std = themes.std_by_curie(ref) if ":" in ref else None
        if std is not None:
            return f"{std.label}（標準）" if lg == "ja" else f"{std.label} (standard)"
        return ref

    def rel(frm: Any, r: Any, to: Any) -> str:
        return _REL_SENTENCE[lg].get(str(r), "{s} - {o}").format(s=label(frm), o=label(to))

    out: list[str] = []
    for op in ops:
        name = op.get("op")
        if name == "add_concept":
            kind = op.get("kind")
            if lg == "en":
                what = "kind of thing" if kind == "class" else "property"
                out.append(f"Add {name_of(op)} ({what})")
            else:
                out.append(f"「{name_of(op)}」を{'種類' if kind == 'class' else '項目'}として足す")
        elif name == "add_relation":
            out.append(rel(op["from"], op["rel"], op["to"]))
        elif name == "retarget":
            line = rel(op["from"], op["rel"], op["new_to"])
            out.append(f"{line} に付け替え" if lg == "ja" else f"Move to: {line}")
        elif name == "remove":
            if op.get("target") == "concept":
                out.append(
                    f"「{label(op['key'])}」を外す" if lg == "ja" else f"Remove {label(op['key'])}"
                )
            else:
                line = rel(op["from"], op["rel"], op["to"])
                out.append(f"{line} を外す" if lg == "ja" else f"Remove: {line}")
        elif name == "rename":
            new = name_of(op)
            old = label(op["key"])
            out.append(
                f"「{old}」の名前を「{new}」に" if lg == "ja" else f"Rename {old} to {new}"
            )
        elif name == "add_question":
            q = op.get("question")
            out.append(f"問いを足す: {q}" if lg == "ja" else f"Add a question: {q}")
        elif name == "bind_column":
            col = op.get("column")
            subject = f"列「{col}」" if lg == "ja" else f"Column {col}"
            r = str(op.get("rel"))
            text = _REL_SENTENCE[lg].get(r, "{s} - {o}").format(s=subject, o=label(op["to"]))
            out.append(text)
        else:
            out.append("")
    return out
