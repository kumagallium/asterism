"""Tests for asterism.default_view (契約メモ §3 — D1-export).

Reads the SAME fixture file the ui side reads
(``ui/src/cards/fixtures/default_view_cases.json``) so both languages are
proven to produce byte-identical ``ViewSpec`` JSON from the same
``ToolContract``/``rows`` input (契約メモ §3 / §7.4). Fixture data spans two
unrelated fictional domains (library checkouts / weather stations) — no
materials-science noun anywhere (§0).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.default_view import default_view_for, unit_label

_FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "ui" / "src" / "cards" / "fixtures" / "default_view_cases.json"
)


def _load_cases() -> list[dict]:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_default_view_matches_fixture(case: dict) -> None:
    view = default_view_for(case["tool"], case["rows"])
    assert view == case["expected"]


def test_default_view_is_deterministic_and_does_not_mutate_rows() -> None:
    tool = {
        "name": "t",
        "title": "test",
        "output_kind": "series",
        "item": {
            "observed_at": {"var": "observed_at", "role": "x", "number": False},
            "humidity_pct": {"var": "humidity_pct", "role": "y", "number": True},
        },
    }
    rows = [{"observed_at": "2020-01-01", "humidity_pct": 40}]
    before = json.loads(json.dumps(rows))
    a = default_view_for(tool, rows)
    b = default_view_for(tool, rows)
    assert a == b
    assert rows == before


def test_default_view_ranked_uses_item_key_not_var() -> None:
    """``item`` の**キー**が var と異なる宣言ツールでも、``field``/``subject_field``/
    ``sort.field`` は行のキー（＝item のキー）を指すこと（var を指すとバグ）。"""
    tool = {
        "name": "t",
        "title": "test",
        "output_kind": "ranked",
        "item": {
            "checkout_count": {"var": "checkoutCount", "role": "value", "number": True},
            "branch_name": {"var": "branchName", "role": "label"},
            "branch_page": {"var": "branchPage", "role": "subject"},
        },
    }
    view = default_view_for(tool, [])
    assert view["spec"]["columns"][0]["field"] == "branch_name"
    assert view["spec"]["columns"][1]["field"] == "checkout_count"
    assert view["spec"]["subject_field"] == "branch_page"
    assert view["spec"]["sort"] == {"field": "checkout_count", "dir": "desc"}


def test_default_view_no_custom_key() -> None:
    """The default view never carries a ``custom`` key (that marker is only
    for LLM-written view specs — Phase 2, out of scope here)."""
    tool = {"name": "t", "title": "test", "output_kind": "flow", "item": {}}
    view = default_view_for(tool, [])
    assert "custom" not in view


def test_unit_label() -> None:
    assert unit_label(None) == ""
    assert unit_label("") == ""
    assert unit_label("unit:K") == "K"
    assert unit_label("unit:W-PER-M-K") == "W-PER-M-K"
    assert unit_label("http://qudt.org/vocab/unit/KiloGM") == "KiloGM"


def test_default_view_unknown_output_kind_raises() -> None:
    tool = {"name": "t", "title": "test", "output_kind": "bogus", "item": {}}
    with pytest.raises(ValueError):
        default_view_for(tool, [])
