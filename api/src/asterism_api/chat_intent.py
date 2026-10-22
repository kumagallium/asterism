"""文の手番の振り分け（ui-redesign PR 11d・§19）— 純関数・決定論。AI を呼ばない。

``classify`` は人の 1 文を 4 つのどれかにする。順は **戻す → 図を直す語 → 見せて → 答えて → 図**:

* ``undo`` — 「さっきのを戻して」（11b の規則をここに置く。``chat_turns.is_undo`` は呼ぶだけ）。
* ``show`` — 「<名前>を見せて／の一覧／の表」で <名前> が公開済みの種類・用語の名前に当たる。
* ``ask`` — 「答えて」「教えて」（en: answer / tell me）で終わる。または末尾が ？ で、下書きが
  開いていなくて、名前が**公開済みの種類**（``names`` の ``kind`` が ``dataset_kind``）に当たる
  （用語名だけの当たりは ``graph``）。名前の照合は語の境目（ASCII は単語・日本語は 2 文字以上）。
* ``graph`` — それ以外（11b の図の提案）。「図に」「足して」「外して」「付け替え」「直して」
  「広げて」を含む文は、ほかの形に見えても必ず ``graph``。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import re
from typing import Any, Literal

from asterism.shared_vocab import normalize_label

__all__ = ["Intent", "classify", "entry_iri", "is_undo", "show_target"]

Intent = Literal["undo", "show", "ask", "graph"]

_UNDO_JA = re.compile(
    r"^(さっきの|前の|今の|それ|元に)[をは]?(戻して|取り消して|やめて)(ください|くれ)?[。！!]?$"
)
_UNDO_EN = re.compile(
    r"^(undo|revert)( that| it| this| the last( step| one)?)?[.!]?$", re.IGNORECASE
)

#: 含まれていれば必ず図の手番にする語（「ひとつ前の位置に戻して」のように名前を含んでも図）
_GRAPH_WORDS = ("図に", "足して", "外して", "付け替え", "直して", "広げて")

_SHOW_TAIL = (
    r"(?:(?:一覧|表)(?:を|で)?"
    r"(?:見せて(?:ください|下さい|くれ|ほしい)?|にして(?:ください|下さい|くれ)?)?"
    r"|見せて(?:ください|下さい|くれ|ほしい)?)[\s。.!！]*"
)
_ANSWER_TAIL = re.compile(
    r"(?:答えて|教えて)(?:ください|くれ|ね|よ)?[\s。.!！]*\Z"
    r"|\b(?:please answer|answer it|answer|tell me)[\s.!]*\Z"
)
_QUESTION_MARK = re.compile(r"[?？][\s。.!！]*\Z")
_MIN_NAME = 2  # 質問に名前が「当たる」と見る最小の長さ（1 字の名前は何にでも当たる）

#: ``names`` の値が文字列（IRI だけ）のときの種別 — 公開済みの種類として扱う
_DEFAULT_KIND = "dataset_kind"


def entry_iri(names: dict, label: str) -> str:
    """``names[label]`` の IRI（値は IRI の文字列か ``{"iri", "kind"}``）。"""
    v = names[label]
    return str(v["iri"]) if isinstance(v, dict) else str(v)


def _entry_kind(v: Any) -> str:
    return str(v.get("kind") or _DEFAULT_KIND) if isinstance(v, dict) else _DEFAULT_KIND


def _within(key: str, text: str) -> bool:
    """名前 ``key`` が文 ``text`` に語として入っているか。ASCII の名前は単語の境目で
    （「te」は「temperature」に当たらない）、日本語の名前は 2 文字以上だけ部分一致。"""
    if key.isascii():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text))
    return len(key) >= _MIN_NAME and key in text


def is_undo(text: str) -> bool:
    """「さっきのを戻して」か（決定論・AI を呼ばない）。"""
    t = text.strip()
    return bool(_UNDO_JA.match(t) or _UNDO_EN.match(t))


def _folded(names: dict) -> dict[str, str]:
    """正規化した名前 → 元の名前（空・重複は先のものを残す）。"""
    out: dict[str, str] = {}
    for label in names:
        key = normalize_label(label)
        if key:
            out.setdefault(key, label)
    return out


def show_target(text: str, names: dict) -> str | None:
    """「<名前>を見せて」「show <name>」の <名前>（``names`` の元の名前）。形が違う・当たらなければ
    ``None``。長い名前から当てる（「価格」と「価格表」が両方あれば「価格表を見せて」は後者）。"""
    t = normalize_label(text)
    folded = _folded(names)
    for key in sorted(folded, key=len, reverse=True):
        k = re.escape(key)
        if (
            re.match(rf"{k}\s*(?:を|の)?\s*{_SHOW_TAIL}\Z", t)
            or re.match(rf"show\s+(?:me\s+)?(?:the\s+)?{k}(?:\s+(?:table|list))?[\s.!]*\Z", t)
            or re.match(rf"{k}\s+(?:table|list)[\s.!]*\Z", t)
        ):
            return folded[key]
    return None


def _kind_hit(text: str, names: dict) -> bool:
    """文が、公開済みの種類（星）の名前に当たるか。用語名だけの当たりは数えない。"""
    t = normalize_label(text)
    folded = _folded(names)
    return any(_entry_kind(names[label]) == _DEFAULT_KIND and _within(k, t)
               for k, label in folded.items())  # fmt: skip


def may_need_names(text: str) -> bool:
    """名前の一覧（星図を読む）が要る文か — 「見せて／一覧／表」「答えて／教えて」「？」を
    含むときだけ。ほかの文（図の提案）は名前を読まずに ``graph`` へ進める。"""
    n = normalize_label(text)
    return bool(
        re.search(r"見せて|一覧|表|\bshow\b|\btable\b|\blist\b", n)
        or _ANSWER_TAIL.search(n)
        or _QUESTION_MARK.search(n)
    )


def has_graph_words(text: str) -> bool:
    """図を直す言葉（図に・足して・外して…）を含むか — 含む文は AI の振り分けを飛ばして提案へ。"""
    return any(w in text for w in _GRAPH_WORDS)


def classify(text: str, *, names: dict, draft_open: bool) -> Intent:
    """文 → ``undo`` / ``show`` / ``ask`` / ``graph``。``names`` は公開済みの種類・用語の表示名
    （``{名前: {"iri", "kind": "class"|"property"|"dataset_kind"}}``。値が文字列なら
    IRI のみ＝公開済みの種類として扱う）、``draft_open`` は話がいま下書きを育てているか。"""
    t = text.strip()
    if is_undo(t):
        return "undo"
    if any(w in t for w in _GRAPH_WORDS):
        return "graph"
    if show_target(t, names) is not None:
        return "show"
    n = normalize_label(t)
    if _ANSWER_TAIL.search(n):
        return "ask"
    if _QUESTION_MARK.search(n) and not draft_open and _kind_hit(t, names):
        return "ask"
    return "graph"
