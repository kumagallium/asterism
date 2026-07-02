"""Operator-configured (server-side) LLM keys — the opt-in "configure once" path.

By default Asterism keeps provider keys out of the server entirely: the browser
holds them and sends one per request (never persisted server-side). That stays
the default here — :func:`server_key_for` returns ``None`` unless the operator
explicitly sets ``ASTERISM_LLM_KEY_<PROVIDER>`` in the environment (asterism.env).

When set, the api/demo-agent use it as a **fallback** only when the request sends
no key of its own, so a browser-supplied key still wins. This lets a Private,
single-tenant deployment (login-gated, one org) hold keys once so its users never
re-enter them — the same trust level as the box already holding the login secret.
It is a deliberate relaxation of the browser-only-key default; leave the env unset
on any multi-tenant / public instance to keep keys off the server.

Modeled on Graphium's server-side model config; the env indirection keeps it
testable (monkeypatch the environment) and provider-agnostic.
"""

from __future__ import annotations

import os

# The providers the settings UI can register (mirrors ui store PROVIDERS).
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "openai-compatible")


def _env_name(provider: str) -> str:
    """``openai-compatible`` -> ``ASTERISM_LLM_KEY_OPENAI_COMPATIBLE``."""
    slug = (provider or "anthropic").strip().lower().replace("-", "_")
    return f"ASTERISM_LLM_KEY_{slug.upper()}"


def server_key_for(provider: str | None) -> str | None:
    """Operator fallback key for ``provider``, or ``None`` when not configured.

    Off by default (returns ``None``): only an explicitly set, non-empty
    ``ASTERISM_LLM_KEY_<PROVIDER>`` yields a key."""
    val = os.environ.get(_env_name(provider or "anthropic"), "").strip()
    return val or None


def configured_providers() -> dict[str, bool]:
    """Which providers have an operator key set — booleans only, never the key.

    Powers ``GET /api/llm/server-keys`` so the UI can let a user proceed without
    typing a key when the server already has one for the active model's provider."""
    return {p: server_key_for(p) is not None for p in PROVIDERS}
