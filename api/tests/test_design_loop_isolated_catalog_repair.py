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
