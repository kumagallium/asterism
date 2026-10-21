"""文書の要旨と結論の文を取る（ui-redesign PR 10b・契約 A）— 見出しの揺れ・無いとき・字数の上限。

文はすべて、このテストのために自分で書いた短い要約（実在の論文の本文ではない）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.paper_sections import MAX_CHARS, extract_from_docling, extract_from_jats

ABS = "Bi2Te3 に微量の Sb を加えると、室温付近の熱伝導率が下がり ZT が上がることを調べた。"
CON = "粒界での散乱が格子熱伝導率を下げ、ZT の向上に効いていると結論した。"
METHODS = "試料は焼結で作り、レーザーフラッシュ法で熱拡散率を測った（この文は渡してはいけない）。"


def _jats(*secs: tuple[str, str], abstract: str | None = None) -> str:
    front = f"<front><article-meta><abstract><p>{abstract}</p></abstract></article-meta></front>"
    body = "".join(f"<sec><title>{t}</title><p>{p}</p></sec>" for t, p in secs)
    return f"<article>{front if abstract else ''}<body>{body}</body></article>"


def test_takes_the_abstract_element_and_the_conclusion_section_and_nothing_else() -> None:
    xml = _jats(("1. Introduction", "導入の文。" * 10), ("2. Methods", METHODS),
                ("5. Conclusions", CON), abstract=ABS)  # fmt: skip
    got = extract_from_jats(xml)
    assert got.basis == "headings"
    assert got.sections == [("Abstract", ABS), ("Conclusion", CON)]
    assert got.text == f"Abstract: {ABS}\nConclusion: {CON}"
    assert "焼結" not in got.text and "導入の文" not in got.text  # the body is never passed


@pytest.mark.parametrize(
    "abs_title, con_title",
    [
        ("Abstract", "Conclusion"),
        ("ABSTRACT", "CONCLUSIONS"),
        ("Summary", "6. Conclusions and Outlook"),
        ("要旨", "結論"),
        ("概要", "まとめ"),
        ("アブストラクト", "おわりに"),
        ("Abstract:", "IV. Conclusion"),
        ("Abstract", "Concluding remarks"),
    ],
)
def test_headings_are_found_whatever_the_case_number_or_language(
    abs_title: str, con_title: str
) -> None:
    xml = _jats((abs_title, ABS), ("Methods", METHODS), (con_title, CON))
    got = extract_from_jats(xml)
    assert got.sections == [("Abstract", ABS), ("Conclusion", CON)]


def test_a_conclusion_with_subsections_is_read_whole_and_a_nested_one_is_found() -> None:
    xml = (
        "<article><body>"
        "<sec><title>Discussion</title><p>議論の文。</p>"
        f"<sec><title>4.2 Conclusion</title><p>{CON}</p></sec></sec>"
        "<sec><title>Conclusions</title><p>一つ目の文。</p>"
        "<sec><title>Outlook</title><p>二つ目の文。</p></sec></sec>"
        "</body></article>"
    )
    got = extract_from_jats(xml)
    assert got.sections == [("Conclusion", f"{CON}")]  # the first conclusion in reading order

    only = _jats(("Conclusions", "一つ目の文。"))
    only = only.replace("</sec>", "<sec><title>Outlook</title><p>二つ目の文。</p></sec></sec>")
    assert extract_from_jats(only).sections == [("Conclusion", "一つ目の文。 二つ目の文。")]


def test_without_abstract_or_conclusion_headings_it_takes_two_paragraphs_from_each_end() -> None:
    paras = [
        "著者名",  # short furniture is not a paragraph
        "第 1 段落: 熱電材料の性能指数 ZT を上げる方法について、試料の作り方から順に整理する。",
        "第 2 段落: 格子熱伝導率を下げるには、粒界やナノ構造でのフォノン散乱が使われてきた。",
        "第 3 段落: 途中の文。" + "あ" * 30,
        "第 4 段落: 途中の文。" + "い" * 30,
        "第 5 段落: まとめにあたる文だが、見出しは付いていない。" + "う" * 20,
        "第 6 段落: 最後の文。ZT の向上は粒界の密度に支えられている。",
    ]
    xml = "<article><body>" + "".join(f"<p>{p}</p>" for p in paras) + "</body></article>"
    got = extract_from_jats(xml)
    assert got.basis == "edges"
    assert [label for label, _ in got.sections] == ["Beginning", "Ending"]
    beginning, ending = got.sections[0][1], got.sections[1][1]
    assert beginning == f"{paras[1]} {paras[2]}"
    assert ending == f"{paras[5]} {paras[6]}"
    assert "第 3 段落" not in got.text and "第 4 段落" not in got.text and "著者名" not in got.text


def test_a_short_document_does_not_repeat_a_paragraph_in_both_ends() -> None:
    p = ["一つ目の段落: " + "あ" * 40, "二つ目の段落: " + "い" * 40, "三つ目の段落: " + "う" * 40]
    xml = "<article><body>" + "".join(f"<p>{x}</p>" for x in p) + "</body></article>"
    got = extract_from_jats(xml)
    assert got.sections[0][1] == f"{p[0]} {p[1]}" and got.sections[1][1] == p[2]


def test_the_total_is_capped_each_section_keeps_its_head_and_a_short_one_is_kept_whole() -> None:
    long_abs = "要旨の文。" * 800  # 4,000 chars
    long_con = "結論の文。" * 800
    got = extract_from_jats(_jats(("Abstract", long_abs), ("Conclusion", long_con)))
    assert len(got.text) <= MAX_CHARS
    (_, a), (_, c) = got.sections
    assert long_abs.startswith(a) and long_con.startswith(c)  # the head of each, not the tail
    assert abs(len(a) - len(c)) <= 10 and len(a) > 1000

    short_con = "結論は短い。"
    got = extract_from_jats(_jats(("Abstract", long_abs), ("Conclusion", short_con)))
    assert len(got.text) <= MAX_CHARS
    assert got.sections[1][1] == short_con  # the spare goes to the long one
    assert len(got.sections[0][1]) > 2300


def test_unreadable_or_empty_documents_give_nothing() -> None:
    assert not extract_from_jats("これは XML ではない")
    assert not extract_from_jats("<article><body></body></article>")
    assert extract_from_jats("<article><body></body></article>").text == ""
    assert not extract_from_docling({})


def _docling(items: list[tuple[str, str]]) -> dict:
    """見出しつき段落だけの小さな Docling の dict（``texts`` と ``body.children``）。"""
    texts = []
    for i, (label, text) in enumerate(items):
        t = {"self_ref": f"#/texts/{i}", "label": label, "text": text}
        if label == "section_header":
            t["level"] = 1
        texts.append(t)
    return {
        "name": "sample",
        "texts": texts,
        "body": {"children": [{"$ref": t["self_ref"]} for t in texts]},
    }


def test_the_docling_structure_is_read_by_the_same_rules() -> None:
    doc = _docling([
        ("title", "熱電材料の粒界について"),
        ("section_header", "Abstract"), ("text", ABS),
        ("section_header", "1. Introduction"), ("text", "導入の文。" * 8),
        ("section_header", "2. Methods"), ("text", METHODS),
        ("section_header", "3. Conclusion"), ("text", CON),
    ])  # fmt: skip
    got = extract_from_docling(doc)
    assert got.sections == [("Abstract", ABS), ("Conclusion", CON)]
    # no section headers at all (a PDF the layout model could not segment) → the two ends
    flat = _docling([("text", f"段落 {i}: " + "あ" * 40) for i in range(6)])
    assert extract_from_docling(flat).basis == "edges"


def test_a_real_docling_fixture_gives_a_bounded_non_empty_text() -> None:
    path = Path(__file__).parent / "fixtures" / "docling_ma11040649.json"
    got = extract_from_docling(json.loads(path.read_text(encoding="utf-8")))
    assert got and len(got.text) <= MAX_CHARS
