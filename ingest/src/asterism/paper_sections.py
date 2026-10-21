"""文書から「要旨と結論の文」だけを取り出す（決定論・ui-redesign PR 10b・design.md §18）。

「この論文から問いを提案してもらう」で AI に渡すのは、**利用者の文書の要旨と結論の文だけ**（本文
全体は渡さない）。ここはその文を、見出しの名前で機械的に選ぶ。AI も乱数も使わない。

入口は 2 つ。どちらも JATS（取り込みが文書を落とす形）に揃えて同じ規則で読む:

* :func:`extract_from_jats` — JATS の XML（Word は pandoc が、PDF は Docling が JATS にしたもの）。
* :func:`extract_from_docling` — Docling の構造化出力（``DoclingDocument`` の dict）。
  ``asterism.documents.docling_dict_to_jats`` で JATS にしてから同じ規則で読む。

規則（見出しは小文字・章番号（``2.`` ``IV.`` ``第 3 章``）・末尾の記号を外してから比べる）:

* 要旨 = JATS の ``<abstract>``、または見出しが ``Abstract`` / ``Summary`` / ``要旨`` / ``概要`` /
  ``アブストラクト`` の節。
* 結論 = 見出しが ``Conclusion(s)``（``Conclusions and outlook`` のような続きも可）/
  ``Concluding remarks`` / ``結論`` / ``結言`` / ``まとめ`` / ``おわりに`` の節（下の節も含む）。
* 要旨も結論も見つからないときだけ、本文の先頭 2 段落＋末尾 2 段落（短い行は数えない）。
* 合計（見出しの名前を含む）は :data:`MAX_CHARS` = 2,500 字まで。超えたら各節の**先頭から**残し、
  短い節の余りは長い節に回す。

何も保存しない・本文全体を返さない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import re
from dataclasses import dataclass, field

from asterism.documents import _safe_parse, docling_dict_to_jats

__all__ = [
    "MAX_CHARS",
    "PaperText",
    "extract_from_docling",
    "extract_from_jats",
]

#: AI に渡す文の合計の上限（見出しの名前を含む）。
MAX_CHARS = 2500
#: 見出しが見つからないときの先頭・末尾の段落の数。
_EDGE_PARAS = 2
#: 見出しが見つからないとき、これより短い行（著者名・日付など）は段落に数えない。
_MIN_PARA_CHARS = 30

_ABSTRACT_HEADINGS = {"abstract", "summary", "要旨", "概要", "アブストラクト", "要約"}
_CONCLUSION_RE = re.compile(
    r"^(conclusions?|concluding remarks?|結論|結言|まとめ|おわりに)(\b|$|[と・および及び])"
)
# 章番号: ``2.`` ``2.1`` ``IV.`` ``第 3 章`` ``(3)`` ``3)``
_NUMBER_RE = re.compile(
    r"^(第\s*[0-9０-９一二三四五六七八九十]+\s*[章節]|\(?[0-9ivx]+(\.[0-9]+)*[.)]?)\s+"
)


@dataclass(frozen=True)
class PaperText:
    """取り出した文。``sections`` = ``[(見出しの名前, 文)]``・``basis`` = ``headings``（見出しで
    取った）か ``edges``（見出しが無く先頭と末尾の段落）。"""

    sections: list[tuple[str, str]] = field(default_factory=list)
    basis: str = "headings"

    @property
    def text(self) -> str:
        """AI に渡す 1 つの文字列（1 節 1 行・``見出し: 文``・合計 :data:`MAX_CHARS` 字まで）。"""
        return "\n".join(f"{label}: {body}" for label, body in self.sections)

    def __bool__(self) -> bool:
        return bool(self.sections)


def _squash(s: str) -> str:
    return " ".join(s.split())


def _heading_key(title: str) -> str:
    t = _squash(title).lower().strip(" .:：。")
    t = _NUMBER_RE.sub("", t).strip(" .:：。")
    return t


def _is_abstract(title: str) -> bool:
    return _heading_key(title) in _ABSTRACT_HEADINGS


def _is_conclusion(title: str) -> bool:
    return bool(_CONCLUSION_RE.match(_heading_key(title)))


def _paragraphs(el) -> list[str]:
    """``el`` の下の段落（入れ子の節・リストも文書の順に）。段落が無ければ ``el`` 自身の文字。"""
    out = [_squash("".join(p.itertext())) for p in el.iter("p")]
    out = [p for p in out if p]
    if not out:
        own = _squash("".join(el.itertext()))
        title = el.find("title")
        if title is not None:
            own = _squash(own.replace(_squash("".join(title.itertext())), "", 1))
        out = [own] if own else []
    return out


def _cut(text: str, limit: int) -> str:
    """先頭から ``limit`` 字まで残す。文の切れ目（。．.!?）が後ろ半分にあればそこで切る。"""
    if len(text) <= limit:
        return text
    head = text[: max(limit, 0)]
    hits = [m.end() for m in re.finditer(r"[。．!?！？]|\.(?=\s)", head)]
    if hits and hits[-1] >= limit // 2:
        return head[: hits[-1]].strip()
    return head.strip()


def _fit(sections: list[tuple[str, str]], cap: int) -> list[tuple[str, str]]:
    """合計（``見出し: 文`` の形・改行つき）を ``cap`` 字に収める。各節の先頭から残し、短い節の
    余りは長い節へ回す（小さい順に等分を割り当てる）。"""
    overhead = [len(label) + 2 for label, _ in sections]
    budget = cap - sum(overhead) - max(len(sections) - 1, 0)
    if budget <= 0:
        return []
    lens = [len(body) for _, body in sections]
    if sum(lens) <= budget:
        return sections
    alloc = [0] * len(sections)
    left = budget
    order = sorted(range(len(sections)), key=lambda i: lens[i])
    for rank, i in enumerate(order):
        share = left // (len(order) - rank)
        alloc[i] = min(lens[i], share)
        left -= alloc[i]
    return [(label, _cut(body, alloc[i])) for i, (label, body) in enumerate(sections)]


def _body_paragraphs(root) -> list[str]:
    body = root.find(".//body")
    if body is None:
        return []
    skip: set[int] = set()
    for tag in ("fig", "table-wrap", "caption"):
        for el in body.iter(tag):
            skip.update(id(p) for p in el.iter("p"))
    out = []
    for p in body.iter("p"):
        if id(p) in skip:
            continue
        t = _squash("".join(p.itertext()))
        if len(t) >= _MIN_PARA_CHARS:
            out.append(t)
    return out


def extract_from_jats(xml_text: str, *, max_chars: int = MAX_CHARS) -> PaperText:
    """JATS の XML → 要旨と結論の文（規則はモジュールの説明）。文書が読めなければ空。"""
    try:
        root = _safe_parse(xml_text)
    except Exception:
        return PaperText()
    abstract: tuple[str, str] | None = None
    conclusion: tuple[str, str] | None = None

    for ab in root.iter("abstract"):
        if (ab.get("abstract-type") or "").lower() in ("graphical", "teaser", "toc", "highlights"):
            continue
        t = " ".join(_paragraphs(ab))
        if t:
            abstract = ("Abstract", t)
            break

    def walk(parent) -> None:
        nonlocal abstract, conclusion
        for sec in parent.findall("sec"):
            title_el = sec.find("title")
            title = _squash("".join(title_el.itertext())) if title_el is not None else ""
            if abstract is None and title and _is_abstract(title):
                abstract = ("Abstract", " ".join(_paragraphs(sec)))
            elif conclusion is None and title and _is_conclusion(title):
                conclusion = ("Conclusion", " ".join(_paragraphs(sec)))
            else:
                walk(sec)

    body = root.find(".//body")
    if body is not None:
        walk(body)

    found = [s for s in (abstract, conclusion) if s and s[1]]
    basis = "headings"
    if not found:
        paras = _body_paragraphs(root)
        if not paras:
            return PaperText()
        head = paras[:_EDGE_PARAS]
        tail = paras[max(_EDGE_PARAS, len(paras) - _EDGE_PARAS) :]
        found = [s for s in (("Beginning", " ".join(head)), ("Ending", " ".join(tail))) if s[1]]
        basis = "edges"
    return PaperText(sections=_fit(found, max_chars), basis=basis)


def extract_from_docling(doc: dict, *, max_chars: int = MAX_CHARS) -> PaperText:
    """Docling の構造化出力（``DoclingDocument`` の dict）→ 要旨と結論の文。"""
    if not isinstance(doc, dict):
        return PaperText()
    return extract_from_jats(docling_dict_to_jats(doc), max_chars=max_chars)
