"""Exposure-profile switch (ADR store-mcp-split)."""
from __future__ import annotations

from asterism.exposure import ENV_EXPOSE_RAW_SPARQL, raw_sparql_enabled


def test_default_is_open_when_unset() -> None:
    # No var -> open (backward compatible / topology A).
    assert raw_sparql_enabled({}) is True


def test_falsy_values_disable_the_escape() -> None:
    for v in ("0", "false", "FALSE", "no", "Off", " off "):
        assert raw_sparql_enabled({ENV_EXPOSE_RAW_SPARQL: v}) is False, v


def test_truthy_and_other_values_keep_it_open() -> None:
    for v in ("1", "true", "yes", "on", "anything"):
        assert raw_sparql_enabled({ENV_EXPOSE_RAW_SPARQL: v}) is True, v
