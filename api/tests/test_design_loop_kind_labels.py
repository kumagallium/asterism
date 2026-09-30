"""種類の表示名（``subject.label``）を、設計を書き直すたびに言い直す
（``design_loop._overlay_kind_labels``）。

表示名は表示メタで、設計を丸ごと書き直す AI は書き戻さない。人が確かめた
骨格にある表示名を、同じ名前のマップへ決定論で戻す。
見本は架空の題材（貸し出しの記録）。
"""

from __future__ import annotations

import yaml

from asterism_api.design_loop import _overlay_kind_labels


def _schema(*, label: str | None = None) -> str:
    label_line = f"      label: {label}\n" if label else ""
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        "maps:\n"
        "  - name: record\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:record/{LoanId}"\n'
        "      classes: [ex:Record_1a2b3c]\n"
        f"{label_line}"
        "    properties:\n"
        "      - predicate: ex:loanDate\n"
        "        column: LoanDate\n"
        "```\n"
    )


def _skeleton(label: str | None) -> dict:
    subject: dict = {"template": "exr:record/{LoanId}", "classes": ["ex:Record_1a2b3c"]}
    if label:
        subject["label"] = label
    return {"version": 1, "maps": [{"name": "record", "source": "data.csv", "subject": subject}]}


def _subject(schema_md: str) -> dict:
    block = schema_md.split("```yaml", 1)[1].split("```", 1)[0]
    return yaml.safe_load(block)["maps"][0]["subject"]


def test_the_display_name_is_put_back_after_a_rewrite() -> None:
    out = _overlay_kind_labels(_schema(), _skeleton("貸し出しの記録"))
    assert _subject(out)["label"] == "貸し出しの記録"
    # 公開される名前と ID の作り方は触らない
    assert _subject(out)["classes"] == ["ex:Record_1a2b3c"]
    assert _subject(out)["template"] == "exr:record/{LoanId}"


def test_putting_it_back_twice_changes_nothing() -> None:
    once = _overlay_kind_labels(_schema(), _skeleton("貸し出しの記録"))
    assert _overlay_kind_labels(once, _skeleton("貸し出しの記録")) == once


def test_a_display_name_the_design_already_has_is_kept() -> None:
    schema_md = _schema(label="貸出")
    assert _overlay_kind_labels(schema_md, _skeleton("貸し出しの記録")) == schema_md


def test_nothing_changes_without_a_display_name_to_put_back() -> None:
    schema_md = _schema()
    assert _overlay_kind_labels(schema_md, _skeleton(None)) == schema_md
    assert _overlay_kind_labels(schema_md, None) == schema_md


def test_a_document_without_a_design_is_left_alone() -> None:
    assert _overlay_kind_labels("## Schema proposal\n", _skeleton("貸し出しの記録")) == (
        "## Schema proposal\n"
    )
