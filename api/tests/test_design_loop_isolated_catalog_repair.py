"""Deterministic repair inside the loop: link a ☑ value catalog the model left
unreferenced (K49, docs/architecture/meaning-before-identity.md).

Live case (2026-09-25): a Kantan S4 checkbox turns a column into its own "value
catalog" map (subject keyed on that one column, K33) at the skeleton stage, but
whether the record kind actually LINKS to it is left to the per-map LLM round.
A weak/stub model routinely skips it, the design compiles and validates, and
only the RML-level connectivity check ("DISCONNECTED groups") objects -- which
four automatic rounds could not clear, because nothing in the loop's repairs
looked at value catalogs. The skeleton already proves the missing edge (the
catalog's key column sits on the same source as some other map), so the
machine adds it without a model.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from asterism_api.design_loop import (
    Issue,
    _evaluate,
    _link_isolated_value_catalogs,
    _verdict,
    repair_design,
)

# A fictional library-loan CSV: one row per loan, a title that repeats.
_CSV = (
    b"LoanId,BookTitle,LoanDate,Count\n"
    b"L1,Origin of Species,2024-01-01,1\n"
    b"L2,Origin of Species,2024-01-02,2\n"
    b"L3,Brief History,2024-01-03,1\n"
)


def _spec(*, linked: bool = False) -> str:
    """The shape a weak model produces: a per-row `loan` map and a `book_title`
    value catalog (K33: keyed on BookTitle alone, its only property reads that
    SAME column back as a label) with no edge between them -- unless `linked`."""
    link = (
        "      - predicate: ex:hasBookTitle\n"
        '        object_template: "exr:bookTitle/{BookTitle}"\n'
        if linked
        else ""
    )
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        '  rdfs: "http://www.w3.org/2000/01/rdf-schema#"\n'
        "maps:\n"
        "  - name: loan\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:loan/{LoanId}"\n'
        "      classes: [ex:Loan]\n"
        "    properties:\n"
        f"{link}"
        "      - predicate: ex:loanDate\n"
        "        column: LoanDate\n"
        "      - predicate: ex:count\n"
        "        column: Count\n"
        "  - name: book_title\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:bookTitle/{BookTitle}"\n'
        "      classes: [ex:BookTitle]\n"
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: BookTitle\n"
        "```\n"
    )


def _maps(schema_md: str) -> dict[str, dict]:
    block = schema_md.split("```yaml", 1)[1].split("```", 1)[0]
    spec = yaml.safe_load(block)
    return {m["name"]: m for m in spec["maps"]}


def test_the_record_gets_a_link_to_the_isolated_catalog(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec(linked=False)
    _, before = _verdict(schema_md, tmp_path)
    assert any("DISCONNECTED groups" in i.message for i in before)

    repaired, _, after = _evaluate(schema_md, tmp_path)
    maps = _maps(repaired)
    added = [
        p
        for p in maps["loan"]["properties"]
        if p.get("object_template") == "exr:bookTitle/{BookTitle}"
    ]
    assert len(added) == 1
    assert added[0]["predicate"] == "ex:hasBookTitle"
    # The label the model wrote is untouched.
    assert any(p.get("predicate") == "rdfs:label" for p in maps["book_title"]["properties"])
    assert not any("DISCONNECTED groups" in i.message for i in after)


def test_the_link_carries_the_catalogs_display_name(tmp_path: Path) -> None:
    """つなぐ先の種類に表示名（subject.label）があれば、機械が足すつなぐ項目にも
    同じ言葉が付く。付かないと、公開用の名前（has＋種類の名前）を崩した語が、
    項目の名前として画面に出る。"""
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec(linked=False).replace(
        "      classes: [ex:BookTitle]\n",
        "      classes: [ex:BookTitle]\n      label: 本の題名\n",
    )
    repaired, _, _ = _evaluate(schema_md, tmp_path)
    added = [
        p
        for p in _maps(repaired)["loan"]["properties"]
        if p.get("object_template") == "exr:bookTitle/{BookTitle}"
    ]
    assert len(added) == 1
    assert added[0]["label"] == "本の題名"


def test_the_link_has_no_label_when_the_catalog_has_none(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    repaired, _, _ = _evaluate(_spec(linked=False), tmp_path)
    added = [
        p
        for p in _maps(repaired)["loan"]["properties"]
        if p.get("object_template") == "exr:bookTitle/{BookTitle}"
    ]
    assert len(added) == 1
    assert "label" not in added[0]


def test_an_already_linked_design_is_left_byte_identical(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec(linked=True)
    _, issues = _verdict(schema_md, tmp_path)
    assert _link_isolated_value_catalogs(schema_md, tmp_path, issues) is None


def test_no_edit_when_the_catalog_column_is_not_in_the_header(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    # `Title` is not a real column of data.csv.
    schema_md = _spec(linked=False).replace(
        '      template: "exr:bookTitle/{BookTitle}"\n      classes: [ex:BookTitle]',
        '      template: "exr:bookTitle/{Title}"\n      classes: [ex:BookTitle]',
    ).replace(
        "      - predicate: rdfs:label\n        column: BookTitle\n",
        "      - predicate: rdfs:label\n        column: Title\n",
    )
    issues = [
        Issue(
            category="connectivity",
            subject="loan + book_title",
            message="the mapping's entities split into 2 DISCONNECTED groups",
        )
    ]
    assert _link_isolated_value_catalogs(schema_md, tmp_path, issues) is None


def test_two_isolated_catalogs_are_both_linked(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec(linked=False).replace(
        "  - name: book_title\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:bookTitle/{BookTitle}"\n'
        "      classes: [ex:BookTitle]\n"
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: BookTitle\n",
        "  - name: book_title\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:bookTitle/{BookTitle}"\n'
        "      classes: [ex:BookTitle]\n"
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: BookTitle\n"
        "  - name: loan_date\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:loanDate/{LoanDate}"\n'
        "      classes: [ex:LoanDate]\n"
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: LoanDate\n",
    )
    repaired, _, after = _evaluate(schema_md, tmp_path)
    maps = _maps(repaired)
    predicates = {p.get("predicate"): p.get("object_template") for p in maps["loan"]["properties"]}
    assert predicates.get("ex:hasBookTitle") == "exr:bookTitle/{BookTitle}"
    assert predicates.get("ex:hasLoanDate") == "exr:loanDate/{LoanDate}"
    assert not any("DISCONNECTED groups" in i.message for i in after)


def test_repair_design_manual_path_reaches_the_same_result(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec(linked=False)
    repaired = repair_design(schema_md, tmp_path)
    maps = _maps(repaired)
    added = [
        p
        for p in maps["loan"]["properties"]
        if p.get("object_template") == "exr:bookTitle/{BookTitle}"
    ]
    assert len(added) == 1
    assert added[0]["predicate"] == "ex:hasBookTitle"


# --- K58: リンクは、その列を元々持っていた種類が持つ -----------------------------


def _home_spec(maps: list[dict]) -> str:
    """K58 用の spec。maps をそのまま §9 の YAML にする。"""
    doc = {
        "version": 1,
        "prefixes": {
            "ex": "https://ns.invalid/ns#",
            "exr": "https://ns.invalid/r/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "dcterms": "http://purl.org/dc/terms/",
        },
        "maps": maps,
    }
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return "## Schema proposal\n\n### 9. Declarative mapping spec\n\n```yaml\n" + body + "```\n"


def _links_to(maps: dict[str, dict], target: str) -> list[str]:
    return [
        name
        for name, m in maps.items()
        for p in m.get("properties") or []
        if p.get("object_template") == target
    ]


def test_the_home_of_a_preface_column_is_the_card_not_the_busiest_map(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(
        b"CardNo,Title,Category,Food,Amount\n"
        b"C1,Soup,Stew,carrot,1\nC1,Soup,Stew,potato,2\nC1,Soup,Stew,onion,1\n"
    )
    schema_md = _home_spec(
        [
            {
                "name": "card",
                "source": "data.csv",
                "subject": {"template": "exr:card/{CardNo}", "classes": ["ex:Card"]},
                "properties": [{"predicate": "ex:title", "column": "Title"}],
            },
            {
                "name": "record",
                "source": "data.csv",
                "subject": {"template": "exr:record/{CardNo}/{Food}", "classes": ["ex:Record"]},
                "properties": [
                    {"predicate": "ex:amount", "column": "Amount"},
                    {"predicate": "dcterms:isPartOf", "object_template": "exr:card/{CardNo}"},
                ],
            },
            {
                "name": "category",
                "source": "data.csv",
                "subject": {"template": "exr:category/{Category}", "classes": ["ex:Category"]},
                "properties": [{"predicate": "rdfs:label", "column": "Category"}],
            },
        ]
    )
    _, before = _verdict(schema_md, tmp_path)
    assert any("DISCONNECTED groups" in i.message for i in before)

    repaired, _, after = _evaluate(schema_md, tmp_path)
    maps = _maps(repaired)
    assert _links_to(maps, "exr:category/{Category}") == ["card"]
    added = [
        p
        for p in maps["card"]["properties"]
        if p.get("object_template") == "exr:category/{Category}"
    ]
    assert added[0]["predicate"] == "ex:hasCategory"
    assert not any("DISCONNECTED groups" in i.message for i in after)


def test_the_home_of_a_table_column_is_the_row_kind_even_when_the_card_is_busier(
    tmp_path: Path,
) -> None:
    (tmp_path / "data.csv").write_bytes(
        b"CardNo,Dish,Servings,Source,Food,Brand\n"
        b"C1,Curry,4,Book,carrot,A\nC1,Curry,4,Book,potato,B\nC1,Curry,4,Book,onion,A\n"
    )
    schema_md = _home_spec(
        [
            {
                "name": "card",
                "source": "data.csv",
                "subject": {"template": "exr:card/{CardNo}", "classes": ["ex:Card"]},
                "properties": [
                    {"predicate": "ex:dish", "column": "Dish"},
                    {"predicate": "ex:servings", "column": "Servings"},
                    {"predicate": "ex:source", "column": "Source"},
                ],
            },
            {
                "name": "record",
                "source": "data.csv",
                "subject": {"template": "exr:record/{CardNo}/{Food}", "classes": ["ex:Record"]},
                "properties": [
                    {"predicate": "ex:food", "column": "Food"},
                    {"predicate": "dcterms:isPartOf", "object_template": "exr:card/{CardNo}"},
                ],
            },
            {
                "name": "brand",
                "source": "data.csv",
                "subject": {"template": "exr:brand/{Brand}", "classes": ["ex:Brand"]},
                "properties": [{"predicate": "rdfs:label", "column": "Brand"}],
            },
        ]
    )
    repaired, _, _ = _evaluate(schema_md, tmp_path)
    maps = _maps(repaired)
    assert _links_to(maps, "exr:brand/{Brand}") == ["record"]
    added = [
        p
        for p in maps["record"]["properties"]
        if p.get("object_template") == "exr:brand/{Brand}"
    ]
    assert added[0]["predicate"] == "ex:hasBrand"


def test_catalog_home_among_falls_back_to_property_count_without_rows() -> None:
    from asterism_api.design_loop import _catalog_home_among

    a = {"name": "a", "subject": {"template": "x:a/{K}"}, "properties": [{}]}
    b = {"name": "b", "subject": {"template": "x:b/{K}/{J}"}, "properties": [{}, {}, {}]}
    c = {"name": "c", "subject": {"template": "x:c/{Z}"}, "properties": [{}, {}, {}]}
    assert _catalog_home_among([a, b, c], "X", None)["name"] == "b"  # 最多
    assert _catalog_home_among([a, b, c], "X", [])["name"] == "b"
    # 同点は先頭
    assert _catalog_home_among([c, b], "X", None)["name"] == "c"
    # rows があっても、どの鍵も列を決めない（X が鍵ごとにばらばら）ならプロパティ数に落ちる
    rows = [
        {"K": "1", "J": "1", "Z": "1", "X": "p"},
        {"K": "1", "J": "2", "Z": "1", "X": "q"},
        {"K": "2", "J": "1", "Z": "1", "X": "r"},
    ]
    assert _catalog_home_among([a, b, c], "X", rows)["name"] == "b"
    # 鍵が列を決めるなら、件数最少が勝つ（b: 3 種 / a: K で決まる → 2 種）
    rows2 = [
        {"K": "1", "J": "1", "Z": "1", "X": "p"},
        {"K": "1", "J": "2", "Z": "2", "X": "p"},
        {"K": "2", "J": "1", "Z": "3", "X": "q"},
    ]
    assert _catalog_home_among([b, a], "X", rows2)["name"] == "a"


def test_a_shared_kind_in_another_file_does_not_hide_this_files_island(tmp_path: Path) -> None:
    """K63: 別々のファイルの受け口が同じ種類（同じテンプレート）を共有しても、
    別のファイルからのリンクで「つながっている」と読まない。このファイルの行が
    孤島のまま残らないよう、K49 はこのファイルの種類から辺を足す。述語は先頭の
    受け口の名前でそろう（hasBookTitle2 に割れない）。"""
    (tmp_path / "data.csv").write_bytes(_CSV)
    (tmp_path / "shelf.csv").write_bytes(
        b"ShelfId,Floor,BookTitle\nS1,1,Origin of Species\nS2,2,Silent Spring\n"
    )
    schema_md = _spec(linked=True).replace(
        "```\n",
        "  - name: shelf\n"
        "    source: shelf.csv\n"
        "    subject:\n"
        '      template: "exr:shelf/{ShelfId}"\n'
        "      classes: [ex:Shelf]\n"
        "    properties:\n"
        "      - predicate: ex:floor\n"
        "        column: Floor\n"
        "  - name: book_title2\n"
        "    source: shelf.csv\n"
        "    subject:\n"
        '      template: "exr:bookTitle/{BookTitle}"\n'
        "      classes: [ex:BookTitle]\n"
        "    properties:\n"
        "      - predicate: rdfs:label\n"
        "        column: BookTitle\n"
        "```\n",
    )
    _, before = _verdict(schema_md, tmp_path)
    assert any("DISCONNECTED groups" in i.message for i in before)
    repaired, _, after = _evaluate(schema_md, tmp_path)
    added = [
        p
        for p in _maps(repaired)["shelf"].get("properties") or []
        if p.get("object_template") == "exr:bookTitle/{BookTitle}"
    ]
    assert len(added) == 1
    assert added[0]["predicate"] == "ex:hasBookTitle"
    assert not any("DISCONNECTED groups" in i.message for i in after)
