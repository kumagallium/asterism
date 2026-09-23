"""Tests for asterism.agent_doc (契約メモ §3 — D1-export).

Fixture data spans two unrelated fictional domains (library checkouts /
weather stations) — no materials-science noun anywhere (§0). No LLM, no
network: this module is pure string templating.
"""
from __future__ import annotations

from asterism.agent_doc import AgentDocCard, AgentDocSubject, render_agent_doc

_SUBJECT = AgentDocSubject(
    label="Checkout One",
    is_set=False,
    class_label="Checkout Record",
    dataset_names=["Library Checkouts (v1)"],
)
_CARDS = [
    AgentDocCard(title="Facts about this thing", output_kind="facts", tool="subject_facts"),
    AgentDocCard(
        title="Where the facts came from", output_kind="breakdown", tool="subject_sources"
    ),
]

_REQUIRED_JA = (
    "答えを作らず",
    "答えられないこと",
    "答えられること",
    "出典の出しかた",
    "asterism-agent serve",
)
_REQUIRED_EN = (
    "Do not make up answers",
    "What it cannot answer",
    "What it can answer",
    "How to cite",
    "asterism-agent serve",
)


def _render(
    lang: str, subject: AgentDocSubject = _SUBJECT, cards: list[AgentDocCard] = _CARDS
) -> str:
    return render_agent_doc(
        subject,
        cards,
        lang=lang,
        generated_at="2026-09-23T00:00:00Z",
        asterism_version="0.30.0",
        slug="checkout-one",
    )


def test_ja_has_all_seven_sections() -> None:
    doc = _render("ja")
    for needle in _REQUIRED_JA:
        assert needle in doc, needle
    assert "## これは何か" in doc
    assert "## 規律" in doc
    assert "## 起動" in doc


def test_en_has_all_seven_sections() -> None:
    doc = _render("en")
    for needle in _REQUIRED_EN:
        assert needle in doc, needle
    assert "## What this is" in doc
    assert "## Discipline" in doc
    assert "## How to start" in doc


def test_ja_lists_every_card_with_tool_name() -> None:
    doc = _render("ja")
    assert "subject_facts" in doc
    assert "subject_sources" in doc
    assert "Facts about this thing" in doc


def test_en_lists_every_card_with_tool_name() -> None:
    doc = _render("en")
    assert "subject_facts" in doc
    assert "subject_sources" in doc


def test_no_cards_still_renders_a_valid_section() -> None:
    doc = _render("ja", cards=[])
    assert "答えられること" in doc
    assert "カードがありません" in doc


def test_set_subject_says_filtered_not_one_thing() -> None:
    set_subject = AgentDocSubject(
        label="Weather Stations", is_set=True, class_label="Station", dataset_names=[]
    )
    doc = _render("ja", subject=set_subject)
    assert "絞り込み" in doc


def test_defaults_to_ja_for_unknown_lang() -> None:
    doc = _render("fr")
    assert "答えを作らず" in doc


def test_deterministic() -> None:
    assert _render("ja") == _render("ja")
