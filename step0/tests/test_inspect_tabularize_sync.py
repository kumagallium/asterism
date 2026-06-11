"""Pin step0's lightweight flatten/reserved-column mirror to the canonical
definition in ``asterism.tabularize``.

``asterism_step0.inspect`` deliberately reimplements ``flatten_record`` /
``safe_col`` / the reserved column set instead of importing them, so the step0
design tool stays installable without the ingest runtime (rdflib/httpx/watchfiles
— see ``inspect.py`` and ``asterism.tabularize``'s ``RESERVED_COLUMNS`` comment).
The cost of that duplication is drift; this test removes it by asserting the two
implementations agree, whenever the ingest package is importable. It SKIPS in the
bare step0 environment (where ``asterism`` is absent) and runs in dev / the
ingest-augmented CI job — so a divergence fails the build without coupling the
packages.
"""
from __future__ import annotations

import pytest

from asterism_step0.inspect import (
    _RESERVED_SOURCE_COLUMNS,
    _flatten_record,
    _safe_column,
)

asterism_tabularize = pytest.importorskip(
    "asterism.tabularize",
    reason="ingest package not installed; the cross-package sync check needs it",
)


def test_reserved_column_set_matches_canonical() -> None:
    assert _RESERVED_SOURCE_COLUMNS == asterism_tabularize.RESERVED_COLUMNS


def test_safe_column_matches_canonical() -> None:
    for name in ["subject", "predicate", "object", "graph", "SUBJECT", "subj", "id", ""]:
        assert _safe_column(name) == asterism_tabularize.safe_col(name)


def test_flatten_record_matches_canonical() -> None:
    records = [
        {"id": "r1", "owner": {"login": "octocat", "type": "User"}},  # dotted object
        {"id": "r1", "topics": ["ai", "ml"]},  # scalar array → JSON-string cell
        {"author": [{"family": "Adams"}, {"family": "Brown"}]},  # object array
        {"archived": True, "forked": False, "lang": None},  # bool / null
        {"subject": ["x"], "predicate": "p", "nested": {"subject": 1}},  # reserved names
        "solo",  # non-object record → synthetic "value"
        ["a", "b"],  # non-object array record
    ]
    for record in records:
        assert _flatten_record(record) == asterism_tabularize.flatten_record(record)
