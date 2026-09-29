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

import unicodedata

__all__ = ["loses_words", "name_tag"]

_FNV_OFFSET_BASIS = 0x811C9DC5
_FNV_PRIME = 0x01000193
_MASK_32 = 0xFFFFFFFF

# 大文字小文字の区別が無い文字体系（日本語の漢字・かな、中国語の漢字など）。
# Lo = other letter（かな・漢字・ハングル音節など）、Lm = modifier letter
# （長音記号「ー」など）。ASCII 部分を単語として使うと意味を失う合図。
_NO_CASE_CATEGORIES = frozenset({"Lo", "Lm"})


def name_tag(text: str) -> str:
    """``text`` から決定論的に導く6桁の16進符号。

    ``NFKC(text).strip()`` の UTF-8 バイト列に FNV-1a 32bit
    （初期値 ``0x811C9DC5``・素数 ``0x01000193``）をかけ、8桁の16進表現の
    先頭6桁を返す。同じ入力は、いつ・どこで呼んでも同じ符号になる。
    """
    data = unicodedata.normalize("NFKC", str(text)).strip().encode("utf-8")
    h = _FNV_OFFSET_BASIS
    for byte in data:
        h ^= byte
        h = (h * _FNV_PRIME) & _MASK_32
    return f"{h:08x}"[:6]


def loses_words(text: str) -> bool:
    """``text`` に、大文字小文字の区別が無い文字が1つでもあれば真。"""
    return any(unicodedata.category(ch) in _NO_CASE_CATEGORIES for ch in str(text))
