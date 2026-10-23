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

from asterism import graph_diff
from asterism.shared_vocab import normalize_label

__all__ = [
    "Intent",
    "classify",
    "entry_iri",
    "has_fix_words",
    "is_remove_form",
    "is_undo",
    "parse_remove",
    "show_target",
]

Intent = Literal["undo", "show", "ask", "graph"]

_UNDO_JA = re.compile(
    r"^(さっきの|前の|今の|それ|元に)[をは]?(戻して|取り消して|やめて)(ください|くれ)?[。！!]?$"
)
_UNDO_EN = re.compile(
    r"^(undo|revert)( that| it| this| the last( step| one)?)?[.!]?$", re.IGNORECASE
)

#: 含まれていれば必ず図の手番にする語（「ひとつ前の位置に戻して」のように名前を含んでも図）
_GRAPH_WORDS = ("図に", "足して", "外して", "付け替え", "直して", "広げて")

#: 指摘・訂正の言葉（11n）— 未承認の提案がある話で、疑問文でない文に含まれていれば提案（直し）へ。
#: 普通の文に多い語（伸びている・入れ替え）は入れない
_FIX_WORDS_JA = ("間違", "誤り", "おかしい", "違う", "ずれて", "付け替え", "直して")
_FIX_EN = re.compile(r"\b(?:wrong|incorrect|mistake|mistaken|should be)\b", re.IGNORECASE)
#: 疑問文と見る形（ja: 末尾・語 / en: 先頭の疑問詞）
_ASKING_END_JA = re.compile(r"(?:か|かな|ですか|のは|でしょうか)[\s。.!！]*\Z")
_ASKING_WORDS_JA = ("知りたい", "なぜ", "とは", "教えて", "答えて", "どう", "何")
_ASKING_START_EN = re.compile(
    r"^(?:what|why|how|which|where|when|can|could|should|tell|is|are|do|does)\b", re.IGNORECASE
)

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


def _is_asking(text: str) -> bool:
    """疑問文か（末尾が ？／か／ですか…、知りたい・なぜ・とは…を含む、英語の先頭が疑問詞）。"""
    t = text.strip()
    return bool(
        _QUESTION_MARK.search(t)
        or _ASKING_END_JA.search(t)
        or any(w in t for w in _ASKING_WORDS_JA)
        or _ASKING_START_EN.match(t)
    )


def has_fix_words(text: str) -> bool:
    """指摘・訂正の言葉（間違い・違う・おかしい…）を含む疑問文でない文か。呼ぶ側は
    **未承認の提案がある話**でだけ使う（「違う店の値段も知りたい」を直しにしない）。"""
    t = text.strip()
    if not t or _is_asking(t):
        return False
    return any(w in t for w in _FIX_WORDS_JA) or bool(_FIX_EN.search(t))


# ---- 外す指示の決定論（11n）-----------------------------------------------------------------

_QUOTES = "「」『』\"'“”"
_REMOVE_JA = re.compile(
    r"^(?P<body>.+?)\s*を\s*(?:外し|はずし|消し|削除し)(?:て|てください|て下さい|てくれ)?[\s。.!！]*$"
)
_REMOVE_EN = re.compile(r"^(?:please\s+)?(?:remove|drop|delete|take out)\s+(?P<body>.+?)[\s.!]*$")
_NOT_JA = re.compile(
    r"^(?P<a>.+?)\s*は\s*(?P<b>.+?)\s*の\s*(?P<k>一種|一部)\s*(?:では|じゃ)\s*(?:ない|ありません)"
    r"[\s。.!！]*$"
)
_NOT_EN = re.compile(
    r"^(?P<a>.+?)\s+is\s+not\s+an?\s+(?P<k>kind|part)\s+of\s+(?P<b>.+?)[\s.!]*$"
)
_NOT_REL = {"一種": "sub", "kind": "sub", "一部": "part", "part": "part"}


def _flat(s: str) -> str:
    """照合用: 引用符を外し、空白を詰めて正規化する。"""
    return re.sub(r"\s+", "", normalize_label(s.strip().strip(_QUOTES)))


def _names_of(concept: dict) -> set[str]:
    out = {_flat(str(concept.get(k) or "")) for k in ("label_ja", "label_en")}
    out |= {_flat(str(a)) for a in concept.get("aliases") or []}
    return {n for n in out if n}


def _find_key(draft: dict, name: str) -> str | None:
    """名前に当たる概念の鍵。2 件以上に当たる（label と aliases の衝突を含む）なら ``None``。"""
    f = _flat(name)
    hits = {
        str(c.get("key")) for c in draft.get("concepts") or [] if f and f in _names_of(c)
    }
    return next(iter(hits)) if len(hits) == 1 else None


def parse_remove(text: str, draft: dict | None) -> list[dict] | None:
    """「〈文〉を外して」「〈A〉は〈B〉の一種ではない」「〈名前〉を外して」→ ``remove`` の op の列。
    下書きの文（``ops_to_sentences`` と同じ言い方）か概念の名前に当たらなければ ``None``
    （今まで通り AI へ）。決定論・AI を呼ばない。"""
    if not draft:
        return None
    t = text.strip()
    rels = [r for r in draft.get("relations") or [] if r.get("from") and r.get("to")]
    concepts = {"concepts": draft.get("concepts") or []}
    m = _NOT_JA.match(t) or _NOT_EN.match(t.lower())
    if m:
        a, b = _find_key(draft, m["a"]), _find_key(draft, m["b"])
        want = _NOT_REL[m["k"]]
        hit = [
            r for r in rels
            if a and b and r["from"] == a and r["to"] == b and r.get("rel") == want
        ]  # fmt: skip
        return [_remove_rel(r) for r in hit[:1]] or None
    m = _REMOVE_JA.match(t) or _REMOVE_EN.match(t.lower())
    if not m:
        return None
    body = _flat(m["body"])
    if not body:
        return None
    for r in rels:  # 文の一致（ja・en どちらの言い方でも）
        op = {"op": "add_relation", "from": r["from"], "rel": r.get("rel"), "to": r["to"]}
        for lg in ("ja", "en"):
            if _flat(graph_diff.ops_to_sentences([op], concepts, lg)[0]) == body:
                return [_remove_rel(r)]
    key = _find_key(draft, m["body"])  # 名前の一致
    if key is not None:
        return [{"op": "remove", "target": "concept", "key": key, "reason": ""}]
    return None


def is_remove_form(text: str) -> bool:
    """「〜を外して」「remove 〜」の形か（下書きに当たったかは問わない）。"""
    t = text.strip()
    return bool(_REMOVE_JA.match(t) or _REMOVE_EN.match(t.lower()))


def _remove_rel(r: dict) -> dict:
    return {
        "op": "remove", "target": "relation", "from": r["from"], "rel": r.get("rel"),
        "to": r["to"], "reason": "",
    }  # fmt: skip
