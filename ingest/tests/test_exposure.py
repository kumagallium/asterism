"""Exposure-profile switch (ADR store-mcp-split)."""
from __future__ import annotations

from asterism.exposure import ENV_EXPOSE_RAW_SPARQL, raw_sparql_enabled


def test_default_is_closed_when_unset() -> None:
    # No var -> closed (safe-by-default for a sensitive store).
    assert raw_sparql_enabled({}) is False


def test_falsy_values_disable_the_escape() -> None:
    for v in ("0", "false", "FALSE", "no", "Off", " off "):
        assert raw_sparql_enabled({ENV_EXPOSE_RAW_SPARQL: v}) is False, v


def test_explicit_truthy_values_open_the_escape() -> None:
    for v in ("1", "true", "yes", "on", "anything"):
        assert raw_sparql_enabled({ENV_EXPOSE_RAW_SPARQL: v}) is True, v
