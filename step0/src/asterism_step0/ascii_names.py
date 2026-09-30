"""英字にできない名前から、決定論的に一意な識別子を作る（stdlib のみ）。

``_identifier``／``_class_name``（``staged_propose.py``）や ``_ascii_map_name``
（``skeleton_annotate.py``）は ASCII の部分だけを取り出す。日本語のような、
大文字小文字の区別が無い文字体系の名前は ASCII 部分が空になり、機械が作る
名前が ``value``／``Value`` のような固定の語に潰れ、違う名前どうしが区別
できなくなる（列の順番でしか見分けられない）。

ここに置く2つの道具は、その穴だけを埋める:

* :func:`name_tag` — 同じ入力からは、いつも同じ6桁の符号（乱数・時刻・列の
  順番を使わない）。
* :func:`loses_words` — ASCII 部分を意味のある単語として使ってよいかの目印
  （Unicode カテゴリ ``Lo``／``Lm`` の文字を含むかどうか）。
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["loses_words", "name_tag", "needs_tag"]

_FNV_OFFSET_BASIS = 0x811C9DC5
_FNV_PRIME = 0x01000193
_MASK_32 = 0xFFFFFFFF

# 大文字小文字の区別が無い文字体系（日本語の漢字・かな、中国語の漢字など）。
# Lo = other letter（かな・漢字・ハングル音節など）、Lm = modifier letter
# （長音記号「ー」など）。ASCII 部分を単語として使うと意味を失う合図。
_NO_CASE_CATEGORIES = frozenset({"Lo", "Lm"})

# 取り除く空白を明示する。Python の ``str.strip()`` と JS の ``trim()`` は
# 取り除く集合が違う（BOM・NEL・制御文字）ので、両言語で同じ集合を使う。
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

_STRIP_CHARS = " \t\n\r\v\f\u00a0\u3000\ufeff"


def name_tag(text: str) -> str:
    """``text`` から決定論的に導く6桁の16進符号。

        ``NFKC(text)`` の前後から明示の空白（U+0020・``\\t\\n\\r\\v\\f``・U+00A0・
    U+3000・U+FEFF）だけを除いた UTF-8 バイト列に FNV-1a 32bit
        （初期値 ``0x811C9DC5``・素数 ``0x01000193``）をかけ、8桁の16進表現の
        先頭6桁を返す。同じ入力は、いつ・どこで呼んでも同じ符号になる。
    """
    cleaned = unicodedata.normalize("NFKC", str(text)).strip(_STRIP_CHARS)
    # 対になっていない代用符号位置は UTF-8 にできない。TypeScript の TextEncoder と
    # 同じく U+FFFD に置き換える（落とさない・両方で同じ符号になる）。
    data = _LONE_SURROGATE.sub("\ufffd", cleaned).encode("utf-8")
    h = _FNV_OFFSET_BASIS
    for byte in data:
        h ^= byte
        h = (h * _FNV_PRIME) & _MASK_32
    return f"{h:08x}"[:6]


def loses_words(text: str) -> bool:
    """``text`` に、大文字小文字の区別が無い文字が1つでもあれば真。"""
    return any(unicodedata.category(ch) in _NO_CASE_CATEGORIES for ch in str(text))


def needs_tag(text: str, ascii_part: str) -> bool:
    """符号を付けて一意にすべき名前か。

    元の名前に ASCII 以外の文字があり、かつ（ASCII の部分が空、または大文字小文字の
    区別が無い文字を含む）とき真。英字・数字・記号だけの名前は、いつも偽
    （今までの識別子を 1 文字も変えない）。
    """
    s = str(text)
    return (not s.isascii()) and (not ascii_part or loses_words(s))
