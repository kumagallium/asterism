"""語の名前を文に当てる（照合）。標準ライブラリだけの純関数。

論文の道（``asterism_api.chat_paper``）と**同じ規則**を ingest に置く。``fold``・``prep_text``・
``term_key``・``mentions`` は同じ入力に同じ返りを返す（api 側のテストで 2 つを固定する）。
そのうえで、当たりの位置（:func:`name_spans`）と、複数の語を一度に文へ当てて「長い当たりが勝つ」
:func:`match_terms` を足す。分野固有の名詞は持たない。
"""

# ruff: noqa: RUF001
# 全角の括弧・ハイフン類・引用符を文字列に持つのは意図（照合の規則そのもの）。
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache

__all__ = [
    "NAME_MAX",
    "fold",
    "match_terms",
    "mentions",
    "merge_key",
    "name_spans",
    "name_variants",
    "prep_text",
    "term_key",
    "usable_name",
]

_CJK_FROM = 0x2E80  # これ以上（日本語など）は語の境目を見ない
_LATIN_LIMIT = 0x250  # これ未満の文字だけの名前は「ラテン名」（短いものは照合に使わない）
#: 照合に使う名前の長さの上限（:func:`fold` した後・空白を除いた文字数）。これより長いものは
#: 語の名前ではなく文なので当てない（巨大な名前で正規表現を組ませない）。
NAME_MAX = 200
_QUOTES = "'’"
_HYPHENS = "-‐‑‒–­"


def fold(s: str) -> str:
    """大文字小文字を残して整える（NFKC・改行前のハイフンをつなぐ・ハイフンと空白を 1 つに）。"""
    s = re.sub(rf"[{_HYPHENS}]\s*\n\s*", "", s)
    s = unicodedata.normalize("NFKC", s)
    return " ".join(re.sub(rf"[{_HYPHENS}]+", " ", s).split())


def prep_text(s: str) -> str:
    """照合の前の整え方: 改行の前のハイフン（``thermo-\\nelectric``）はつなぎ、NFKC・大文字小文字・
    空白・ハイフンを 1 つの空白に揃える。"""
    return fold(s).casefold()


def _is_word(c: str) -> bool:
    return (c.isalnum() and ord(c) < _CJK_FROM) or c in _QUOTES


#: 単数と複数が同じ形の語・``s`` で終わるが単数の語（小さな表）
_INVARIANT = frozenset({
    "series", "species", "news", "bias", "gas", "lens", "chaos", "atlas", "canvas", "axis",
    "basis", "analysis", "synthesis", "hypothesis", "thesis", "diagnosis", "crisis", "physics",
    "mathematics", "statistics", "mechanics", "kinetics", "thermodynamics",
})  # fmt: skip
#: 不規則な複数形 → 単数形
_IRREGULAR = {
    "analyses": "analysis", "syntheses": "synthesis", "hypotheses": "hypothesis",
    "theses": "thesis", "diagnoses": "diagnosis", "crises": "crisis", "matrices": "matrix",
    "indices": "index", "vertices": "vertex", "appendices": "appendix", "gases": "gas",
    "axes": "axis", "biases": "bias",
}  # fmt: skip


def _singular(w: str) -> str:
    """小文字の 1 語を単数形に寄せる（規則は 1 つ — :func:`term_key` と :func:`mentions` が共有）。
    数字を含む語・3 文字以下の語は触らない。``ies→y``・``zes→ze``・``xes/ches/shes`` は語幹・
    それ以外の末尾 ``s`` は 1 文字落とす（``ss``/``us``/``is`` 終わりと表の語は落とさない）。"""
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    if len(w) <= 3 or not re.fullmatch(r"[a-z]+", w) or w in _INVARIANT:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("zes"):
        return w[:-1]
    if re.search(r"(xes|ches|shes|sses)$", w):
        return w[:-2]
    if w.endswith("s") and not re.search(r"(ss|us|is)$", w):
        return w[:-1]
    return w


def _plurals(w: str) -> set[str]:
    out = {w + "s"}
    if re.search(r"(s|x|z|ch|sh)$", w):
        out.add(w + "es")
    if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
        out.add(w[:-1] + "ies")
    if w.endswith("is"):
        out.add(w[:-2] + "es")
    out |= {k for k, v in _IRREGULAR.items() if v == w}
    return out


def _word(raw: str) -> tuple[str, set[str]]:
    """(元の大文字小文字のままの 1 語) → ``(単数形の鍵, 同じ語とみなす表面の形)``。数字を含む語は
    そのまま。全部大文字の語（化学式・略号）は単数化せず、末尾に小文字の ``s`` 1 つが付いた形
    （``ZTs``）だけを同じとみなす。"""
    low = raw.casefold()
    if any(c.isdigit() for c in raw) or not re.fullmatch(r"[A-Za-z]+", raw):
        return low, {low}
    if re.fullmatch(r"[A-Z]{2,}s?", raw):
        base = low[:-1] if raw.endswith("s") else low
        return base, {base, base + "s"}
    sing = _singular(low)
    return sing, {low, sing, *_plurals(sing)}


def term_key(name: str) -> str:
    """名前を 1 つの語にまとめるための鍵: 大文字小文字・全角半角・空白・ハイフンを揃え、英語の
    語は単数形に寄せる（:func:`_word` の規則）。"""
    return " ".join(_word(w)[0] for w in fold(name).split(" "))


def usable_name(name: str) -> bool:
    """照合に使える名前か。``fold`` して空白を除いた長さが、全部ラテン系の文字（U+0250 未満）なら
    3 以上、それ以外（日本語など）なら 2 以上で、:data:`NAME_MAX` 以下。**文字（letter）を 1 つも
    含まない名前は使わない** — 数字だけ（``100``）・記号だけ（``...``）の名前は、どの文の数字や
    省略記号にも当たってしまう。"""
    bare = fold(name).replace(" ", "")
    if not bare or len(bare) > NAME_MAX or not any(c.isalpha() for c in bare):
        return False
    if all(ord(c) < _LATIN_LIMIT for c in bare):
        return len(bare) >= 3
    return len(bare) >= 2


@lru_cache(maxsize=4096)
def _compile(name: str) -> tuple[re.Pattern[str] | None, str | None]:
    """名前 → ``(語の境目つきの正規表現, 空白を無視して比べるときの文字列)``。

    照合に使えない名前は ``(None, None)``。正規表現は名前ごとに 1 回だけ組む（使い回す）。
    後ろの文字列は、英数字（日本語を除く）の無い名前だけ非 None。"""
    n = fold(name)
    bare = n.replace(" ", "")
    if not bare or (all(ord(c) < _LATIN_LIMIT for c in bare) and len(bare) < 3):
        return None, None
    pat = " ".join(
        "(?:" + "|".join(re.escape(f) for f in sorted(_word(w)[1], key=len, reverse=True)) + ")"
        for w in n.split(" ")
    )
    loose = None if any(c.isalnum() and ord(c) < _CJK_FROM for c in n) else bare.casefold()
    return re.compile(pat), loose


def _squash(text_norm: str) -> tuple[str, list[int]]:
    """空白を除いた文字列と、その各文字の ``text_norm`` での添字。"""
    index = [i for i, c in enumerate(text_norm) if c != " "]
    return "".join(text_norm[i] for i in index), index


def _spans(
    text_norm: str,
    rx: re.Pattern[str],
    loose: str | None,
    squash: Callable[[], tuple[str, list[int]]] | None = None,
) -> list[tuple[int, int]]:
    """:func:`name_spans` の本体（コンパイル済みの正規表現を使う）。``squash`` は空白を除いた文と
    添字を返す関数 — 多くの名前を同じ文に当てるとき、呼び手が 1 回だけ作って使い回す。"""
    out: list[tuple[int, int]] = []
    for m in rx.finditer(text_norm):
        a, b = m.start(), m.end()
        if _is_word(text_norm[a]) and a > 0 and _is_word(text_norm[a - 1]):
            continue
        if _is_word(text_norm[b - 1]) and b < len(text_norm) and _is_word(text_norm[b]):
            continue
        out.append((a, b))
    if out or loose is None:
        return out
    # 英数字の無い名前: PDF の抽出で入る空白を無視して比べる。空白を除いた文字列の添字を
    # 元の ``text_norm`` の添字に戻す。
    squashed, index = squash() if squash is not None else _squash(text_norm)
    pos = squashed.find(loose)
    while pos != -1:
        out.append((index[pos], index[pos + len(loose) - 1] + 1))
        pos = squashed.find(loose, pos + 1)
    return out


def name_spans(text_norm: str, name: str) -> list[tuple[int, int]]:
    """整えた文（:func:`prep_text`）に名前が当たる位置 ``(開始, 終了)``（``text_norm`` の添字・
    終了は含まない）。語の境目は Unicode の文字・数字（アポストロフィも語の一部。日本語などは境目を
    見ず部分一致）。英語は語ごとに単数・複数を同じとみなす。ラテン文字だけで 3 文字未満の名前は
    照合に使わない（空を返す）。"""
    rx, loose = _compile(name)
    if rx is None:
        return []
    return _spans(text_norm, rx, loose)


def mentions(text_norm: str, name: str) -> bool:
    """整えた文に、語の名前が現れるか（``bool(name_spans(...))`` と同じ）。"""
    return bool(name_spans(text_norm, name))


_PAREN = re.compile(r"[(（]([^()（）]*)[)）]")


def _variant_ok(part: str) -> bool:
    """括弧の外・中を別名として使ってよいか: 文字（letter）を含み、空白を除いて 3 文字以上。
    ``Si(100)`` の ``100``・``Si``、``熱電材料 (n型)`` の ``n型`` のような短い修飾は別名にしない
    （どの文の「100」「n型」にも当たるため）。"""
    bare = fold(part).replace(" ", "")
    return len(bare) >= 3 and any(c.isalpha() for c in bare)


def name_variants(name: str) -> list[str]:
    """名前そのもの・括弧（半角・全角の丸括弧）の外・括弧の中。空と重複（:func:`term_key` が
    同じもの）は除く。順は 名前 → 外 → 中。外と中は、文字を含む 3 文字以上のものだけ
    （:func:`_variant_ok`）。"""
    cands = [name]
    parens = _PAREN.findall(name)
    if parens:
        cands.extend(p for p in [_PAREN.sub(" ", name), *parens] if _variant_ok(p))
    out: list[str] = []
    seen: set[str] = set()
    for c in cands:
        c = " ".join(c.split())
        if not c:
            continue
        k = term_key(c)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


_DROP_IN_KEY = re.compile(r"[\s()（）\[\]［］・]")


def merge_key(name: str) -> str:
    """語を束ねる鍵: :func:`term_key` から空白・括弧類・中黒を除いたもの。"""
    return _DROP_IN_KEY.sub("", term_key(name))


def match_terms(
    names_by_term: Mapping[str, Sequence[str]], texts: Mapping[str, str]
) -> dict[str, list[str]]:
    """文の id → 当たった語の id（語の id の順・重複なし）。当たりの無い文はキーを出さない。

    語ごとに、渡された名前のうち :func:`usable_name` のものを :func:`prep_text` した文に当てる。
    **長い当たりが勝つ**: 全部の語の当たり（語, 開始, 終了）を集め、長い順（同じ長さなら開始の
    小さい順）に採り、既に採った当たりと重なるものは捨てる。ただし同じ開始・同じ終了の当たりは別の語でも
    両方採る。採られた当たりが 1 つでもある語が「当たった」語。"""
    compiled: list[tuple[str, list[tuple[re.Pattern[str], str | None]]]] = []
    for term_id in sorted(names_by_term):
        pats: list[tuple[re.Pattern[str], str | None]] = []
        seen: set[str] = set()
        for name in names_by_term[term_id]:
            if not isinstance(name, str) or name in seen or not usable_name(name):
                continue
            seen.add(name)
            rx, loose = _compile(name)
            if rx is not None:
                pats.append((rx, loose))
        if pats:
            compiled.append((term_id, pats))

    result: dict[str, list[str]] = {}
    for text_id, raw in texts.items():
        text = prep_text(raw)
        cache: list[tuple[str, list[int]]] = []

        def squash(text: str = text, cache: list = cache) -> tuple[str, list[int]]:
            # 空白を除いた文は、この文で最初に要るときに 1 回だけ作る
            if not cache:
                cache.append(_squash(text))
            return cache[0]

        hits: list[tuple[int, int, str]] = []
        for term_id, pats in compiled:
            for rx, loose in pats:
                hits.extend((a, b, term_id) for a, b in _spans(text, rx, loose, squash))
        if not hits:
            continue
        hits.sort(key=lambda h: (-(h[1] - h[0]), h[0], h[2]))
        taken: list[tuple[int, int]] = []
        won: set[str] = set()
        for a, b, term_id in hits:
            if (a, b) in taken or not any(a < tb and ta < b for ta, tb in taken):
                if (a, b) not in taken:
                    taken.append((a, b))
                won.add(term_id)
        if won:
            result[text_id] = sorted(won)
    return result
