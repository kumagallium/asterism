"""文の手番の振り分け（ui-redesign PR 11d・§19）— 純関数・決定論。AI を呼ばない。

``classify`` は人の 1 文を 4 つのどれかにする。順は **戻す → 図を直す語 → 見せて → 答えて → 図**:

* ``undo`` — 「さっきのを戻して」（11b の規則をここに置く。``chat_turns.is_undo`` は呼ぶだけ）。
* ``show`` — 「<名前>を見せて／の一覧／の表」で <名前> が公開済みの種類・用語の名前に当たる。
* ``ask`` — 「答えて」「教えて」で終わる／末尾が ？ で、下書きが開いていなくて、名前に
  1 つ以上当たる。
* ``graph`` — それ以外（11b の図の提案）。「図に」「足して」「外して」「付け替え」「直して」
  「広げて」を含む文は、ほかの形に見えても必ず ``graph``。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import re
from typing import Literal

from asterism.shared_vocab import normalize_label

__all__ = ["Intent", "classify", "is_undo", "show_target"]

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
    r"(?:(?:一覧|表)(?:を|で)?(?:見せて(?:ください|くれ|ほしい)?)?"
    r"|見せて(?:ください|くれ|ほしい)?)[\s。.!！]*"
)
_ANSWER_TAIL = re.compile(r"(?:答えて|教えて)(?:ください|くれ|ね|よ)?[\s。.!！]*\Z")
_QUESTION_MARK = re.compile(r"[?？][\s。.!！]*\Z")
_MIN_NAME = 2  # 質問に名前が「当たる」と見る最小の長さ（1 字の名前は何にでも当たる）


def is_undo(text: str) -> bool:
    """「さっきのを戻して」か（決定論・AI を呼ばない）。"""
    t = text.strip()
    return bool(_UNDO_JA.match(t) or _UNDO_EN.match(t))


def _folded(names: dict[str, str]) -> dict[str, str]:
    """正規化した名前 → 元の名前（空・重複は先のものを残す）。"""
    out: dict[str, str] = {}
    for label in names:
        key = normalize_label(label)
        if key:
            out.setdefault(key, label)
    return out


def show_target(text: str, names: dict[str, str]) -> str | None:
    """「<名前>を見せて」の <名前>（``names`` の元の名前）。形が違う・当たらなければ ``None``。
    長い名前から当てる（「価格」と「価格表」が両方あれば「価格表を見せて」は後者）。"""
    t = normalize_label(text)
    for key in sorted(_folded(names), key=len, reverse=True):
        if re.match(rf"{re.escape(key)}\s*(?:を|の)?\s*{_SHOW_TAIL}\Z", t):
            return _folded(names)[key]
    return None


def _names_hit(text: str, names: dict[str, str]) -> bool:
    t = normalize_label(text)
    return any(len(k) >= _MIN_NAME and k in t for k in _folded(names))


def may_need_names(text: str) -> bool:
    """名前の一覧（星図を読む）が要る文か — 「見せて／一覧／表」「答えて／教えて」「？」を
    含むときだけ。ほかの文（図の提案）は名前を読まずに ``graph`` へ進める。"""
    n = normalize_label(text)
    return bool(
        re.search(r"見せて|一覧|表", n) or _ANSWER_TAIL.search(n) or _QUESTION_MARK.search(n)
    )


def classify(text: str, *, names: dict[str, str], draft_open: bool) -> Intent:
    """文 → ``undo`` / ``show`` / ``ask`` / ``graph``。``names`` は公開済みの種類・用語の表示名
    （``{名前: IRI}``）、``draft_open`` は話がいま下書きを育てているか。"""
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
    if _QUESTION_MARK.search(n) and not draft_open and _names_hit(t, names):
        return "ask"
    return "graph"
