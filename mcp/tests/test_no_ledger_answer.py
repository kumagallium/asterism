"""MCP は確認済みの関係だけで答える（design §23.7）。台帳の走者（未確認も使う）は MCP に入れない。"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_FORBIDDEN = ("ledger_answer", "answer_ledger_question", "lq_")


def test_mcp_never_mentions_the_ledger_runner() -> None:
    hits = [
        f"{p.relative_to(_SRC)}: {word}"
        for p in sorted(_SRC.rglob("*.py"))
        for word in _FORBIDDEN
        if word in p.read_text(encoding="utf-8")
    ]
    assert hits == []


def test_answer_knowledge_question_does_not_read_the_ledger() -> None:
    text = (_SRC / "asterism_mcp" / "tools.py").read_text(encoding="utf-8")
    start = text.index("async def answer_knowledge_question")
    body = text[start : start + 6000]
    assert "load_ledger" not in body and "pair_relations" not in body
