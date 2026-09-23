"""Tests for asterism.mermaid_flow.to_mermaid (契約メモ §3 — D1-export).

Reads the SAME fixture file the ui side reads
(``ui/src/cards/fixtures/mermaid_cases.json``) so both languages are proven to
render byte-identical Mermaid text from the same ``GraphSpec`` (契約メモ §3 /
§7.4). Fixture data spans two unrelated fictional domains (§0).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.mermaid_flow import to_mermaid

_FIXTURES = (
    Path(__file__).resolve().parents[2] / "ui" / "src" / "cards" / "fixtures" / "mermaid_cases.json"
)


def _load_cases() -> list[dict]:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_to_mermaid_matches_fixture(case: dict) -> None:
    assert to_mermaid(case["graph"]) == case["expected"]


def test_to_mermaid_is_pure() -> None:
    graph = {
        "direction": "LR",
        "nodes": [{"id": "a", "label": "A", "kind": "entity"}],
        "edges": [],
    }
    before = json.loads(json.dumps(graph))
    to_mermaid(graph)
    assert graph == before
